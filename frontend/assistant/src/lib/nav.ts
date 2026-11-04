// 页面内的「后退 / 前进」（桌面端标题栏的两个箭头，同 Claude 桌面端）：记下看过的视图与对话，箭头在它们之间切换。
// 只在本页内存里，不进浏览器历史（对话页只有一个地址，浏览器后退会离开 CC）。
//
// 深链接（提醒的推送通知、桌面端托盘通知点开，2026-10-12）：
//   /assistant?c=<对话>    → 打开那段对话；不是本人的、不存在或已删 → 有定时任务的产品开定时任务页，否则新对话
//   /assistant?view=tasks  → 定时任务页（带 &company=<公司> 只看这家公司的任务）
//   /assistant?view=companies → 公司页；/assistant?company=<公司> → 这家公司的主档页
//   /assistant?task=<代录任务号>（旧的代录任务链接，兼容入口；?view=tasks&task=… 同）→ 按任务号查到它那段会话（task_get 的 session_id），切到代录模式打开；
//                             代录任务 11-04 起就是一段会话，统一用 ?c=<会话>
//   /assistant?view=pending → 兼容入口（早上的待处理汇总提醒、CC 桌面版托盘通知还在发；待处理 11-04 起并进对话，不再是视图）：
//                             带 &company=<公司> 打开这家待处理最多那个月的对话（服务端指定的那段；还没有就在这家这个月开新对话，
//                             末尾是「需要处理」框）；不带时只有一家有待处理就开那家，多家开公司页（列表上有数）
//   公司只在有公司分组的产品（共用层 projects() 的 kind，Cowork、ERP）里有，待处理还要产品声明了 pending；别的产品同「找不到」处理
// 来源三处：打开页面时的地址参数（处理完 replaceState 回 /assistant，刷新不再重开，桌面端记住的地址也是干净的）、
// 后台脚本点通知发来的 pearnly-open（pearnly_core/assistant/service_worker.js）、桌面端的 tomo-open（window.tomo.open）。
//
// 两种模式（评估 docs/work/2026-10-26-express-operator-product-评估.md §10 ①，左上角「对话 | 代录」，Ctrl / ⌘ + Shift + 1 / 2）：
//   对话自己的视图 chat / tasks；代录自己的视图 operator（就是对话页：代录任务是一段会话，同一个 thread / runtime，方案 cc-unified-plan 14）；
//   两种模式共有的 companies / company。模式单独记（useModePlace.mode）：进了模式自己的视图就是那个模式，进共有的视图不换模式；
//   侧栏按模式筛（lib/managed-agents/list-mode.ts，跟着这里改）。两种模式各记各的最后位置：切到代录时记下当时的对话视图，切回来回到它。
//   后退 / 前进也记模式。

import { useAui, useAuiState } from "@assistant-ui/react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { type PendingMonth, refreshPendingCounts, useCompanyNav, usePendingCounts } from "@/lib/companies";
import { getSession } from "@/lib/managed-agents/api";
import { useListMode } from "@/lib/managed-agents/list-mode";
import { getMethod } from "@/lib/frappe";
import { hasSection, projectsReady, setDraft, useProjects } from "@/lib/managed-agents/projects";
import { reportCaught, withReport } from "./caught";

export type View = "chat" | "tasks" | "companies" | "company" | "operator";
type Entry = { view: View; mode: Mode; thread: string; company: string };

export type Mode = "chat" | "operator";

/** 模式自己的视图属于哪个模式；共有的视图（公司列表、公司页）为 null。 */
export const ownMode = (view: View): Mode | null => (view === "operator" ? "operator" : view === "chat" || view === "tasks" ? "chat" : null);

/** 当前模式与两种模式各自的最后位置：对话模式最后看的视图、代录模式最后看的视图。 */
// map: sidebar-modes
export const useModePlace = create<{ mode: Mode; chatView: View; operatorView: View }>(() => ({
  mode: "chat",
  chatView: "chat",
  operatorView: "operator",
}));

// 侧栏按模式筛、新建时带的 mode 跟着当前模式（list-mode.ts，接口层单放的那一份）
useModePlace.subscribe((now) => {
  if (useListMode.getState().mode !== now.mode) useListMode.setState({ mode: now.mode });
});

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

/** 打开代录（对话页本身，侧栏只列代录会话）：从对话模式来的先记下对话视图。 */
export function openOperator(view: View, setView: (v: View) => void) {
  const place = useModePlace.getState();
  if (place.mode !== "operator") useModePlace.setState({ chatView: view });
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
  // 模式也记（共有的公司页两种模式都能看）
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
    if (current && current.view === view && current.mode === mode && current.thread === thread && current.company === company) return;
    stack.current = [...stack.current.slice(0, index.current + 1), { view, mode, thread, company }].slice(-50);
    index.current = stack.current.length - 1;
    bump((n) => n + 1);
  }, [view, mode, thread, company]);

  const go = useCallback(
    (step: number) => {
      const target = stack.current[index.current + step];
      if (!target) return;
      index.current += step;
      replaying.current = true;
      if (target.company) useCompanyNav.setState({ company: target.company });
      // 退回 / 前进换了模式：记下离开的那个模式此刻的视图（切回来回到这里）
      const place = useModePlace.getState();
      if (target.mode !== place.mode) useModePlace.setState(target.mode === "operator" ? { chatView: view, mode: target.mode } : { operatorView: view, mode: target.mode });
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

/** 要打开的东西：c 是对话编号；view 是 tasks / companies 时开那一页，pending 是兼容入口；company：主档页或只看这家；
 *  task：旧的代录任务链接（兼容入口，按任务号查到会话）。 */
export type Target = { c: string; view: string; company?: string; task?: string };

// 同服务端 assistant/contracts.py 的 OPAQUE_ID（公司编号也是这个形状）
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$/;

/** 从地址里读 ?c= / ?view=（只认本站 /assistant 下的地址）。 */
// map: deep-link
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
  const rawTask = parsed.searchParams.get("task") ?? "";
  const task = SESSION_ID.test(rawTask) ? rawTask : "";
  if (!c && !view && !company && !task) return null;
  return { c, view, ...(company ? { company } : {}), ...(task ? { task } : {}) };
}

/** 旧的代录任务链接：任务号 → 它那段会话（站点 task_get 的 session_id，只认本人的任务）；查不到回空。 */
export async function taskConversation(task: string): Promise<string> {
  try {
    const reply = await getMethod<{ session_id?: string }>("pearnly_core.express_operator.api.task_get", { name: task });
    return String(reply?.session_id ?? "");
  } catch (caughtError) { reportCaught(caughtError, "nav");
    return "";
  }
}

type Aui = ReturnType<typeof useAui>;

/** ?view=pending 落到哪：company 给了就是这家，没给时只有一家有待处理就是那家；那家待处理最多的月份与服务端指定的对话。
 *  多家（或一家都没有）回空公司（开公司页）；旧服务端没给按月份的数时只回公司（开公司页）。 */
export async function pendingSpot(company: string): Promise<{ company: string; month: string; conversation: string }> {
  await refreshPendingCounts();
  return spotOf(usePendingCounts.getState(), company);
}

/** pendingSpot 的取法（按已取到的计数）。 */
export function spotOf(
  { months, companies }: { months: readonly PendingMonth[] | null; companies: Readonly<Record<string, number>> },
  company: string,
): { company: string; month: string; conversation: string } {
  const having = Object.entries(companies).filter(([, n]) => n > 0).map(([id]) => id);
  const pick = company || (having.length === 1 ? having[0]! : "");
  if (!pick) return { company: "", month: "", conversation: "" };
  const row = (months ?? []).filter((m) => m.company === pick).sort((a, b) => b.count - a.count || b.month.localeCompare(a.month))[0];
  return { company: pick, month: row?.month ?? "", conversation: row?.conversation ?? "" };
}

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
      if (target.task && !target.c) {
        // 旧的代录任务链接：查到会话就在代录模式打开（侧栏列代录会话），查不到同「找不到」
        const session = await taskConversation(target.task);
        if (!alive) return;
        if (session) {
          if (useModePlace.getState().mode !== "operator") useModePlace.setState({ mode: "operator", chatView: "chat" });
          latest.current.setView("operator");
          if (await openConversation(aui, session)) return;
        }
        if (alive) await fallback();
        return;
      }
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
          const spot = await pendingSpot(company);
          if (!alive) return;
          // 待处理在对话里：提醒点开时在代录就先回对话模式
          if (useModePlace.getState().mode === "operator") useModePlace.setState({ mode: "chat", operatorView: "operator" });
          if (spot.conversation && (await openConversation(aui, spot.conversation))) {
            latest.current.setView("chat");
            return;
          }
          if (spot.company && spot.month) {
            // 还没有这家这个月的对话：开新对话（不在后台建），末尾就是「需要处理」框
            setDraft({ project: spot.company, period: useProjects.getState().period ? spot.month : "", scope: "" });
            latest.current.setView("chat");
            void aui.threads().switchToNewThread();
          } else if (spot.company) {
            useCompanyNav.setState({ company: spot.company });
            latest.current.setView("company");
          } else latest.current.setView("companies");
        } else if (target.view === "companies" || !company) latest.current.setView("companies");
        else {
          // 公司主档页只在对话模式（代录模式下进公司是新建页，用户 11-03）：提醒点开时在代录就先回对话模式
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
      url.searchParams.delete("task");
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
