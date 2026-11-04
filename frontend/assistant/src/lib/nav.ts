// 页面内的「后退 / 前进」（桌面端标题栏的两个箭头，同 Claude 桌面端）：记下看过的视图与对话，箭头在它们之间切换。
// 只在本页内存里，不进浏览器历史（对话页只有一个地址，浏览器后退会离开 CC）。
//
// 深链接（提醒的推送通知、桌面端托盘通知点开，2026-10-12）：
//   /assistant?c=<对话>    → 打开那段对话；不是本人的、不存在或已删 → 有定时任务的产品开定时任务页，否则新对话
//   /assistant?view=tasks  → 定时任务页（带 &company=<公司> 只看这家公司的任务）
//   /assistant?view=companies → 公司页；/assistant?company=<公司> → 这家公司的主档页
//   /assistant?view=pending → 待处理（带 &company=<公司> 只看这家）
//   公司只在有公司分组的产品（共用层 projects() 的 kind，Cowork、ERP）里有，待处理还要产品声明了 pending；别的产品同「找不到」处理
// 来源三处：打开页面时的地址参数（处理完 replaceState 回 /assistant，刷新不再重开，桌面端记住的地址也是干净的）、
// 后台脚本点通知发来的 pearnly-open（pearnly_core/assistant/service_worker.js）、桌面端的 tomo-open（window.tomo.open）。
//
// 两种模式（评估 docs/work/2026-10-26-express-operator-product-评估.md §10 ①，左上角「对话 | 代录」，Ctrl / ⌘ + Shift + 1 / 2）：
//   对话自己的视图 chat / tasks；代录自己的视图 operator；两种模式共有的 companies / company / pending（侧栏共有的「公司」
//   「待处理」，主会话 11-03：代录模式下点了留在代录，主栏换成那一页）。模式单独记（useModePlace.mode）：进了模式自己的视图
//   就是那个模式，进共有的视图不换模式。两种模式各记各的最后位置：切到代录时记下当时的对话视图（对话本身组件库记着），切回来回到它；
//   代录记着最后的视图（任务页或共有的那几页）与看的是哪一批（空 = 新建批次）。后退 / 前进也记模式与代录看的是哪一批。

import { useAui, useAuiState } from "@assistant-ui/react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { useCompanyNav } from "@/lib/companies";
import { getSession } from "@/lib/managed-agents/api";
import { hasSection, projectsReady } from "@/lib/managed-agents/projects";
import { reportCaught, withReport } from "./caught";

export type View = "chat" | "tasks" | "companies" | "company" | "pending" | "operator";
type Entry = { view: View; mode: Mode; thread: string; company: string; batch: string };

export type Mode = "chat" | "operator";

/** 模式自己的视图属于哪个模式；共有的视图（公司、待处理）为 null。 */
export const ownMode = (view: View): Mode | null => (view === "operator" ? "operator" : view === "chat" || view === "tasks" ? "chat" : null);

/** 当前模式与两种模式各自的最后位置：对话模式最后看的视图、代录模式最后看的视图与批次（空 = 新建批次）。 */
export const useModePlace = create<{ mode: Mode; chatView: View; operatorView: View; batch: string }>(() => ({
  mode: "chat",
  chatView: "chat",
  operatorView: "operator",
  batch: "",
}));

/** 换视图时跟着定模式：模式自己的视图就是那个模式，共有的不换（ViewContext 的 setView 都经这里）。 */
export function noteViewMode(view: View) {
  const mode = ownMode(view);
  if (mode && mode !== useModePlace.getState().mode) useModePlace.setState({ mode });
}

/** 切模式：记下这个模式此刻的视图，回到另一个模式上次的视图。 */
export function switchMode(mode: Mode, view: View, setView: (v: View) => void) {
  const place = useModePlace.getState();
  if (mode === place.mode) return;
  if (mode === "operator") {
    useModePlace.setState({ chatView: view, mode });
    setView(place.operatorView);
  } else {
    useModePlace.setState({ operatorView: view, mode });
    setView(place.chatView);
  }
}

/** 打开代录的任务页（batch：任务号，空 = 新建任务）：从对话模式来的（待处理页的「任务」一组）先记下对话视图。 */
export function openOperator(batch: string, view: View, setView: (v: View) => void) {
  const place = useModePlace.getState();
  useModePlace.setState(place.mode === "operator" ? { batch } : { batch, chatView: view });
  setView("operator");
}

/** 当前模式。 */
export const useCurrentMode = () => useModePlace((s) => s.mode);

/** Ctrl / ⌘ + Shift + 1 / 2 对应的模式（event.code：Shift 按住时 event.key 是符号，按键位认）。 */
export function modeForKey(event: Pick<KeyboardEvent, "code" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">): Mode | null {
  if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey) return null;
  return event.code === "Digit1" ? "chat" : event.code === "Digit2" ? "operator" : null;
}

export const ViewContext = createContext<{ view: View; setView: (v: View) => void }>({ view: "chat", setView: () => {} });
export const useView = () => useContext(ViewContext);

export function useNavHistory(view: View, setView: (v: View) => void) {
  const aui = useAui();
  const thread = useAuiState((s) => s.threads.mainThreadId) ?? "";
  // 主档页看的是哪家也记进去：在两家公司之间来回也能后退
  const company = useCompanyNav((s) => (view === "company" ? s.company : ""));
  // 代录看的是哪一批也记进去：在两批之间来回也能后退；模式也记（共有的公司、待处理页两种模式都能看）
  const batch = useModePlace((s) => (view === "operator" ? s.batch : ""));
  const mode = useCurrentMode();
  const stack = useRef<Entry[]>([]);
  const index = useRef(-1);
  const replaying = useRef(false);
  const [, bump] = useState(0);

  useEffect(() => {
    const current = stack.current[index.current];
    if (replaying.current) {
      replaying.current = false;
      return;
    }
    if (current && current.view === view && current.mode === mode && current.thread === thread && current.company === company && current.batch === batch) return;
    stack.current = [...stack.current.slice(0, index.current + 1), { view, mode, thread, company, batch }].slice(-50);
    index.current = stack.current.length - 1;
    bump((n) => n + 1);
  }, [view, mode, thread, company, batch]);

  const go = useCallback(
    (step: number) => {
      const target = stack.current[index.current + step];
      if (!target) return;
      index.current += step;
      replaying.current = true;
      if (target.company) useCompanyNav.setState({ company: target.company });
      // 退回 / 前进换了模式：记下离开的那个模式此刻的视图（切回来回到这里）；退回到代录的任务页：回到那一批
      const place = useModePlace.getState();
      if (target.mode !== place.mode) useModePlace.setState(target.mode === "operator" ? { chatView: view, mode: target.mode } : { operatorView: view, mode: target.mode });
      if (target.view === "operator") useModePlace.setState({ batch: target.batch });
      setView(target.view);
      if (target.thread && target.thread !== thread) void aui.threads().switchToThread(target.thread);
      bump((n) => n + 1);
    },
    [aui, setView, thread, view],
  );

  return {
    canBack: index.current > 0,
    canForward: index.current < stack.current.length - 1,
    back: () => go(-1),
    forward: () => go(1),
  };
}

// ── 深链接 ──────────────────────────────────────────────────────────────────────────────────────

/** 要打开的东西：c 是对话编号；view 是 tasks / companies / pending 时开那一页；company：主档页或只看这家。 */
export type Target = { c: string; view: string; company?: string };

// 同服务端 assistant/contracts.py 的 OPAQUE_ID（公司编号也是这个形状）
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$/;

/** 从地址里读 ?c= / ?view=（只认本站 /assistant 下的地址）。 */
export function targetOf(url: string): Target | null {
  let parsed: URL;
  try {
    parsed = new URL(url, location.origin);
  } catch (caughtError) { reportCaught(caughtError, "nav");
    return null;
  }
  if (parsed.origin !== location.origin || !parsed.pathname.startsWith("/assistant")) return null;
  const c = parsed.searchParams.get("c") ?? "";
  const view = parsed.searchParams.get("view") ?? "";
  const raw = parsed.searchParams.get("company") ?? "";
  const company = SESSION_ID.test(raw) ? raw : "";
  if (!c && !view && !company) return null;
  return company ? { c, view, company } : { c, view };
}

type Aui = ReturnType<typeof useAui>;

/** 切到这段对话；不是本人的、不存在、已删或已归档都回 false。 */
async function openConversation(aui: Aui, id: string): Promise<boolean> {
  if (!SESSION_ID.test(id)) return false;
  try {
    // 先等侧栏第一页：列表里有的直接切过去，不再多问一次
    await aui.threads().getLoadThreadsPromise().catch(withReport(() => undefined, "nav"));
    const listed = aui.threads().getState().threadItems.some((item) => item.remoteId === id && item.status === "regular");
    if (!listed) {
      // 不是本人的、不存在服务端回 404；已归档的组件库会去「恢复」，这里不恢复，直接当找不到
      const session = await getSession(id);
      if (session.archived_at) return false;
    }
    await aui.threads().switchToThread(id);
    return true;
  } catch (caughtError) { reportCaught(caughtError, "nav");
    return false;
  }
}

/**
 * 接三处来源的「打开」（见文件头）。hasTasks：这个产品有没有定时任务页（取不到时按没有）。
 */
export function useDeepLinks(setView: (v: View) => void, hasTasks: () => Promise<boolean>) {
  const aui = useAui();
  const latest = useRef({ setView, hasTasks });
  latest.current = { setView, hasTasks };

  useEffect(() => {
    let alive = true;
    const fallback = async () => {
      const tasks = await latest.current.hasTasks().catch(withReport(() => false, "nav"));
      if (!alive) return;
      if (tasks) latest.current.setView("tasks");
      else {
        latest.current.setView("chat");
        void aui.threads().switchToNewThread();
      }
    };
    const open = async (target: Target) => {
      if (target.c) {
        latest.current.setView("chat");
        if (await openConversation(aui, target.c)) return;
        if (alive) await fallback();
        return;
      }
      const company = target.company ?? "";
      if (target.view === "tasks") {
        useCompanyNav.setState({ taskCompany: company });
        await fallback();
        return;
      }
      if (target.view === "companies" || target.view === "pending" || company) {
        // 公司只在有公司分组的产品，待处理还要声明了 pending（共用层 projects()）
        const kind = await projectsReady().catch(withReport(() => null, "nav"));
        if (!alive) return;
        if (!kind || (target.view === "pending" && !hasSection("pending"))) {
          latest.current.setView("chat");
          void aui.threads().switchToNewThread();
          return;
        }
        if (target.view === "pending") {
          useCompanyNav.setState({ pendingCompany: company });
          latest.current.setView("pending");
        } else if (target.view === "companies" || !company) latest.current.setView("companies");
        else {
          // 公司主档页只在对话模式（代录模式下进公司是新建任务页，用户 11-03）：提醒点开时在代录就先回对话模式，
          // 代录停在任务页，切回来还是那一批
          if (useModePlace.getState().mode === "operator") useModePlace.setState({ mode: "chat", operatorView: "operator" });
          useCompanyNav.setState({ company });
          latest.current.setView("company");
        }
      }
    };

    // 1) 打开页面时的地址参数：处理完把 c、view 从地址里去掉
    const initial = targetOf(location.href);
    if (initial) {
      const url = new URL(location.href);
      url.searchParams.delete("c");
      url.searchParams.delete("view");
      url.searchParams.delete("company");
      history.replaceState(history.state, "", url.pathname + url.search + url.hash);
      void open(initial);
    }

    // 2) 后台脚本点通知：已有 CC 窗口时聚焦它并发来要打开的地址
    const fromWorker = (event: MessageEvent) => {
      const data = event.data as { type?: string; url?: string } | null;
      if (data?.type !== "pearnly-open" || typeof data.url !== "string") return;
      const target = targetOf(data.url);
      if (target) void open(target);
    };
    // 3) 桌面端托盘 / 页面自己的通知点开：detail 是对话编号，空串 = 定时任务页；「/assistant?…」是深链接（提醒属于某家公司时
    //    点开主档页，lib/notify.ts 的 deepLink）
    const fromDesktop = (event: Event) => {
      const id = String((event as CustomEvent).detail ?? "").trim();
      const link = id.startsWith("/assistant") ? targetOf(id) : null;
      void open(link ?? (id && !id.startsWith("/") ? { c: id, view: "" } : { c: "", view: "tasks" }));
    };
    navigator.serviceWorker?.addEventListener("message", fromWorker);
    navigator.serviceWorker?.startMessages();
    window.addEventListener("tomo-open", fromDesktop);
    return () => {
      alive = false;
      navigator.serviceWorker?.removeEventListener("message", fromWorker);
      window.removeEventListener("tomo-open", fromDesktop);
    };
  }, [aui]);
}
