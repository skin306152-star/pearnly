// 侧栏每个对话的状态点（同 Claude）：正在跑的转圈，跑完而你没在看的留一个小圆点，点开就消失。
// 不为后台对话另开连接：服务端转发线程把每个会话的事件都推到用户自己的房间（realtime.ts），这里只看状态事件。
// 只记本页打开以后发生的；刷新后没有历史状态。

import { useSyncExternalStore } from "react";
import type { StreamEvent } from "./events";

export type Activity = "running" | "unread";

const state = new Map<string, Activity>();
const listeners = new Set<() => void>();
let active: string | null = null;

function set(sessionId: string, next: Activity | null) {
  if ((state.get(sessionId) ?? null) === next) return;
  if (next) state.set(sessionId, next);
  else state.delete(sessionId);
  listeners.forEach((listener) => listener());
}

/** 收到某个会话的推送事件。等工具结果、等批准时引擎也是 idle(requires_action)，这一轮还没结束，照旧算在跑。 */
export function noteEvent(sessionId: string, event: StreamEvent) {
  const type = (event as { type?: string }).type;
  if (type === "session.status_running") set(sessionId, "running");
  else if (type === "session.status_idle") {
    const reason = (event as { stop_reason?: { type?: string } }).stop_reason?.type;
    if (reason === "requires_action") set(sessionId, "running");
    else set(sessionId, sessionId === active ? null : "unread");
  } else if (type === "session.status_terminated" || type === "session.deleted") set(sessionId, null);
}

/** 正在看的对话：它的圆点立即消失，之后跑完也不再留点。 */
export function setActiveSession(sessionId: string | null) {
  active = sessionId;
  if (sessionId && state.get(sessionId) === "unread") set(sessionId, null);
}

export function useActivity(sessionId: string | undefined): Activity | undefined {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => (sessionId ? state.get(sessionId) : undefined),
  );
}
