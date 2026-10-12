// 页面内的「后退 / 前进」（桌面端标题栏的两个箭头，同 Claude 桌面端）：记下看过的视图与对话，箭头在它们之间切换。
// 只在本页内存里，不进浏览器历史（对话页只有一个地址，浏览器后退会离开 CC）。
//
// 深链接（提醒的推送通知、桌面端托盘通知点开，2026-10-12）：
//   /assistant?c=<对话>    → 打开那段对话；不是本人的、不存在或已删 → 有定时任务的产品开定时任务页，否则新对话
//   /assistant?view=tasks  → 定时任务页
// 来源三处：打开页面时的地址参数（处理完 replaceState 回 /assistant，刷新不再重开，桌面端记住的地址也是干净的）、
// 后台脚本点通知发来的 pearnly-open（pearnly_core/assistant/service_worker.js）、桌面端的 tomo-open（window.tomo.open）。

import { useAui, useAuiState } from "@assistant-ui/react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { getSession } from "@/lib/managed-agents/api";

export type View = "chat" | "tasks";
type Entry = { view: View; thread: string };

export const ViewContext = createContext<{ view: View; setView: (v: View) => void }>({ view: "chat", setView: () => {} });
export const useView = () => useContext(ViewContext);

export function useNavHistory(view: View, setView: (v: View) => void) {
  const aui = useAui();
  const thread = useAuiState((s) => s.threads.mainThreadId) ?? "";
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
    if (current && current.view === view && current.thread === thread) return;
    stack.current = [...stack.current.slice(0, index.current + 1), { view, thread }].slice(-50);
    index.current = stack.current.length - 1;
    bump((n) => n + 1);
  }, [view, thread]);

  const go = useCallback(
    (step: number) => {
      const target = stack.current[index.current + step];
      if (!target) return;
      index.current += step;
      replaying.current = true;
      setView(target.view);
      if (target.thread && target.thread !== thread) void aui.threads().switchToThread(target.thread);
      bump((n) => n + 1);
    },
    [aui, setView, thread],
  );

  return {
    canBack: index.current > 0,
    canForward: index.current < stack.current.length - 1,
    back: () => go(-1),
    forward: () => go(1),
  };
}

// ── 深链接 ──────────────────────────────────────────────────────────────────────────────────────

/** 要打开的东西：c 是对话编号；view 是 "tasks" 时开定时任务页。 */
export type Target = { c: string; view: string };

// 同服务端 assistant/contracts.py 的 OPAQUE_ID
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{3,127}$/;

/** 从地址里读 ?c= / ?view=（只认本站 /assistant 下的地址）。 */
export function targetOf(url: string): Target | null {
  let parsed: URL;
  try {
    parsed = new URL(url, location.origin);
  } catch {
    return null;
  }
  if (parsed.origin !== location.origin || !parsed.pathname.startsWith("/assistant")) return null;
  const c = parsed.searchParams.get("c") ?? "";
  const view = parsed.searchParams.get("view") ?? "";
  if (!c && !view) return null;
  return { c, view };
}

type Aui = ReturnType<typeof useAui>;

/** 切到这段对话；不是本人的、不存在、已删或已归档都回 false。 */
async function openConversation(aui: Aui, id: string): Promise<boolean> {
  if (!SESSION_ID.test(id)) return false;
  try {
    // 先等侧栏第一页：列表里有的直接切过去，不再多问一次
    await aui.threads().getLoadThreadsPromise().catch(() => undefined);
    const listed = aui.threads().getState().threadItems.some((item) => item.remoteId === id && item.status === "regular");
    if (!listed) {
      // 不是本人的、不存在服务端回 404；已归档的组件库会去「恢复」，这里不恢复，直接当找不到
      const session = await getSession(id);
      if (session.archived_at) return false;
    }
    await aui.threads().switchToThread(id);
    return true;
  } catch {
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
      const tasks = await latest.current.hasTasks().catch(() => false);
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
      } else if (target.view === "tasks") await fallback();
    };

    // 1) 打开页面时的地址参数：处理完把 c、view 从地址里去掉
    const initial = targetOf(location.href);
    if (initial) {
      const url = new URL(location.href);
      url.searchParams.delete("c");
      url.searchParams.delete("view");
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
    // 3) 桌面端托盘 / 页面自己的通知点开：detail 是对话编号，空串 = 定时任务页
    const fromDesktop = (event: Event) => {
      const id = String((event as CustomEvent).detail ?? "").trim();
      void open(id ? { c: id, view: "" } : { c: "", view: "tasks" });
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
