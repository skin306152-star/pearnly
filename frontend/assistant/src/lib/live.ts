// 列表自动刷新（同 Desk 的 p.live，pearnly_core/public/desk/product.js）：推送为主，定时兜底。
//
//   收到站点实时事件（events）：同一阵子来的合成一次（settleMs 800 毫秒），两次刷新至少隔 minMs（3 秒）——
//   小助手逐份推几十份时事件间隔都过了去抖时间，只去抖会整表重拉几十次。
//   兜底定时：busy（有正在处理的）时每 busyMs（10 秒），否则 idleMs（60 秒）；busy 一变就按新的间隔重排。
//   页面看不见时不刷；回到前台、窗口拿回焦点、实时通道重连时马上刷一次（补上断线期间漏掉的事件）。
//   刷新进行中又来事件：记一次，刷完再刷。refresh 抛错不影响下一次。卸载时摘掉全部监听与定时器。

import { useEffect, useRef } from "react";
import { onConnectionChange, onSiteEvent } from "@/lib/managed-agents/realtime";

export type LiveOptions = {
  events: readonly string[];
  busy?: boolean;
  busyMs?: number;
  idleMs?: number;
  settleMs?: number;
  minMs?: number;
};

export function useLiveRefresh(refresh: () => Promise<void> | void, options: LiveOptions) {
  const { events, busy = false, busyMs = 10_000, idleMs = 60_000, settleMs = 800, minMs = 3_000 } = options;
  // refresh 每次渲染都可能是新的：用最新的，不为它重挂监听
  const latest = useRef({ refresh, busy });
  latest.current = { refresh, busy };
  const reschedule = useRef<() => void>(() => undefined);
  const key = events.join("\n");

  useEffect(() => {
    let timer = 0;
    let soon = 0;
    let running = false;
    let again = false;
    let stopped = false;
    let last = Date.now(); // 挂上时调用方自己已经取过一次

    const schedule = () => {
      window.clearTimeout(timer);
      if (!stopped && !document.hidden) timer = window.setTimeout(run, latest.current.busy ? busyMs : idleMs);
    };
    async function run() {
      window.clearTimeout(timer);
      window.clearTimeout(soon);
      if (stopped || document.hidden) return; // 回到前台时再刷
      if (running) {
        again = true;
        return;
      }
      const wait = last + minMs - Date.now();
      if (wait > 0) {
        soon = window.setTimeout(run, wait);
        return;
      }
      running = true;
      last = Date.now();
      try {
        await latest.current.refresh();
      } catch {
        // 下一次再刷
      }
      running = false;
      if (stopped) return;
      if (again) {
        again = false;
        return run();
      }
      schedule();
    }
    const later = () => {
      window.clearTimeout(soon);
      soon = window.setTimeout(run, settleMs);
    };
    const onVisible = () => {
      if (!document.hidden) later();
    };

    const offs = key ? key.split("\n").map((event) => onSiteEvent(event, () => !stopped && later())) : [];
    offs.push(onConnectionChange((connected) => connected && onVisible()));
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    reschedule.current = () => {
      if (!running) schedule();
    };
    schedule();
    return () => {
      stopped = true;
      reschedule.current = () => undefined;
      window.clearTimeout(timer);
      window.clearTimeout(soon);
      offs.forEach((off) => off());
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [key, busyMs, idleMs, settleMs, minMs]);

  // 有了 / 没了正在处理的：兜底定时按新的间隔重排
  useEffect(() => {
    reschedule.current();
  }, [busy]);
}
