// 执行电脑上的桥（方案 cc-unified-plan 14.1 E10、E13；CC 桌面版本批不改）：只有执行那台电脑的 CC 本机知道「第几张、为什么暂停」，
// 也只有它能停下、跳过、重做。代录会话本身由服务端的代录事件源投影（同一段对话、同一个 thread / runtime），这里只做执行电脑上页面的事：
//   - 读桥（operator_state，页面看得见时每 POLL_MS 一次）：在跑 / 暂停的任务，把桥的进度合成 pearnly.progress（第 n/N 张 · 暂停原因码）
//     并进这个任务那段对话（只在本页，不落库、不推送，session-controller 的 applyLocal）；
//   - 桥上开始做了：站点转「进行中」（task_running，每个任务一次）；做完了，或 CC 重启后留下、没在跑的：以站点为准收尾一次（task_settle），
//     交回桥上收尾（operator_control settle）；
//   - 记在任务上的控制（task_controls，取走即清空）：stop（别的设备、或这台电脑上点了 ■ / Esc，服务端把 user.interrupt 记成它）、
//     skip、retry，照桥上控制做。
// 网页、手机、旧版桌面版没有支持实操的桥：什么都不做（进度只看服务端事件）。

import { useEffect } from "react";
import { type Desktop, desktop, OPERATOR } from "@/lib/desktop";
import { getMethod, postMethod } from "@/lib/frappe";
import type { ProgressEvent } from "@/lib/managed-agents/events";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { reportCaught, withReport } from "@/lib/caught";

const API = "pearnly_core.express_operator.api.";
/** 桥多久读一次（毫秒）。 */
export const POLL_MS = 3000;
/** 同一个任务多久向站点取一次控制 / 收尾一次（秒）。 */
export const CONTROLS_S = 6;
export const SETTLE_S = 60;

/** 桥上一张单（CC 桌面版 Runner.snapshot 的一行，只取用得到的）。 */
type BridgeDoc = { doc_id: string; status: string; reason?: string };
/** 桥上一个任务。live = false：CC 重启前留下、这台电脑现在没在跑。site：站点上的公司编号。 */
export type BridgeBatch = { id: string; status: string; reason?: string; docs: BridgeDoc[]; live?: boolean; site?: string };

const FINAL = new Set(["finished", "stopped"]);
const DONE_DOCS = new Set(["saved", "voided", "skipped"]);
/** CC 重启后还没定的单（以站点为准收尾时要问的）。 */
const OPEN_DOCS = new Set(["pending", "entering"]);

/** 桥的进度 → 一条 pearnly.progress（整段对话那一条，同服务端的形状，方案 14.10）：done = 已有结论的张数、total = 共几张；
 *  暂停时 label 是暂停原因码，否则 entering。编号 local- 开头（本站事件，不当补拉锚点）。 */
export function progressOf(batch: BridgeBatch, at = new Date().toISOString()): ProgressEvent {
  const total = batch.docs.length;
  const done = batch.docs.filter((d) => DONE_DOCS.has(d.status) || d.status === "waiting").length;
  const label = batch.status === "paused" && batch.reason ? batch.reason : "entering";
  return { type: "pearnly.progress", id: `local-${batch.id}_${done}_${label}`, processed_at: at, tool_use_id: "", done, total, label, detail: "" };
}

type Deps = {
  bridge?: () => Promise<Desktop | null>;
  get?: <T>(method: string, args?: Record<string, unknown>) => Promise<T>;
  post?: <T>(method: string, args?: Record<string, unknown>) => Promise<T>;
  /** 把一条本页事件并进那段对话。 */
  apply?: (session: string, event: ProgressEvent) => void;
  now?: () => number;
};

/** 一个执行电脑上的读桥循环（测试直接调 tick）。 */
export function createBridgeWatch(deps: Deps = {}) {
  const bridge = deps.bridge ?? desktop;
  const get = deps.get ?? (getMethod as NonNullable<Deps["get"]>);
  const post = deps.post ?? (postMethod as NonNullable<Deps["post"]>);
  const apply = deps.apply ?? ((session, event) => getSessionController(session).applyLocal(event));
  const now = deps.now ?? (() => Math.floor(Date.now() / 1000));
  let api: Promise<Desktop | null> | null = null;
  const found = () =>
    (api ??= bridge()
      .then(async (b) => (b && (await b.state()).features?.includes(OPERATOR) ? b : null))
      .catch(withReport(null, "operator-bridge")));
  const sessions = new Map<string, string>();
  const marked = new Set<string>();
  const closed = new Set<string>();
  const asked = new Map<string, number>();
  const controlsAt = new Map<string, number>();

  /** 任务那段对话（task_get 的 session_id，记住）。 */
  async function sessionOf(task: string): Promise<string> {
    if (!sessions.has(task)) {
      const row = await get<{ session_id?: string }>(API + "task_get", { name: task });
      sessions.set(task, String(row?.session_id ?? ""));
    }
    return sessions.get(task) ?? "";
  }

  async function settle(b: Desktop, batch: BridgeBatch) {
    if (now() - (asked.get(batch.id) ?? -Infinity) < SETTLE_S) return;
    asked.set(batch.id, now());
    const task = await post<{ documents?: Array<Record<string, unknown>> }>(API + "task_settle", { name: batch.id });
    const results = Object.fromEntries((task?.documents ?? []).map((d) => [String(d.doc_id), d]));
    await b.call("operator_control", { batch: batch.id, action: "settle", results });
  }

  async function controls(b: Desktop, batch: BridgeBatch) {
    if (now() - (controlsAt.get(batch.id) ?? -Infinity) < CONTROLS_S) return;
    controlsAt.set(batch.id, now());
    const reply = await post<{ controls?: Array<{ action?: string; doc_id?: string }> }>(API + "task_controls", { name: batch.id });
    for (const c of reply?.controls ?? []) {
      if (c.action === "stop") await b.call("operator_control", { batch: batch.id, action: "stop", doc: "" });
      else if (c.action === "skip" && c.doc_id) await b.call("operator_control", { batch: batch.id, action: "skip", doc: String(c.doc_id) });
      // 重做走站点（CC 本机重录不了已经回执给站点的单），同 task_answer 的空回答
      else if (c.action === "retry" && c.doc_id) await post(API + "task_answer", { name: batch.id, doc_id: String(c.doc_id), answers: "[]" });
    }
  }

  async function tick() {
    const b = await found();
    if (!b) return;
    const reply = (await b.call("operator_state")) as unknown as { ok?: boolean; batches?: BridgeBatch[] };
    if (!reply?.ok) return;
    for (const batch of Array.isArray(reply.batches) ? reply.batches : []) {
      try {
        if (batch.live === false) {
          // CC 重启前留下、没在跑：还有没定的单就以站点为准收尾
          if (batch.site && batch.docs.some((d) => OPEN_DOCS.has(d.status) || (d.status === "waiting" && d.reason === "cc_restarted"))) await settle(b, batch);
          continue;
        }
        if (FINAL.has(batch.status)) {
          if (!closed.has(batch.id)) {
            closed.add(batch.id);
            asked.delete(batch.id);
            await settle(b, batch);
          }
          continue;
        }
        if ((batch.status === "running" || batch.status === "paused") && !marked.has(batch.id)) {
          marked.add(batch.id);
          await post(API + "task_running", { name: batch.id });
        }
        await controls(b, batch);
        if (batch.status === "running" || batch.status === "paused") {
          const session = await sessionOf(batch.id);
          if (session) apply(session, progressOf(batch));
        }
      } catch (caughtError) {
        reportCaught(caughtError, "operator-bridge");
        // 断网、站点不认（任务记录出来前的旧批次）：下一次再说
      }
    }
  }

  return { tick };
}

/** 外壳挂一次（有代录的产品）：页面看得见时每 POLL_MS 读一次桥，切到后台停，回到前台先读一次。 */
export function useOperatorBridge(enabled: boolean) {
  useEffect(() => {
    if (!enabled || typeof document === "undefined") return;
    const watch = createBridgeWatch();
    let timer: ReturnType<typeof setInterval> | null = null;
    let running = false;
    const tick = () => {
      if (running) return;
      running = true;
      void watch.tick().catch(withReport(undefined, "operator-bridge")).finally(() => {
        running = false;
      });
    };
    const start = () => {
      if (document.visibilityState === "hidden" || timer !== null) return;
      tick();
      timer = setInterval(tick, POLL_MS);
    };
    const stop = () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => (document.visibilityState === "hidden" ? stop() : start());
    document.addEventListener("visibilitychange", onVisibility);
    start();
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled]);
}
