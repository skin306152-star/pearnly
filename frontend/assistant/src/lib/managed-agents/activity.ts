// 侧栏每个对话的状态点（同 Claude）：正在跑的转圈，跑完而你没在看的留一个小圆点，点开就消失。
// 不为后台对话另开连接：服务端转发线程把每个会话的事件都推到用户自己的房间（realtime.ts），这里只看状态事件。
// 只记本页打开以后发生的；刷新后没有历史状态。
//
// 本站提醒（pearnly.notice）另记一个圆点：服务端记着有没有看过（列表带回 unread，刷新后仍在）。正看着的对话里
// 显示出了新的提醒（推送、补拉、整段回放带回来的都算，runtime-provider 报 noticesShown），就告诉服务端看过了
// （notices_seen）。提醒到的对话浮到侧栏最上面（useBumps）。
// 所有推来的提醒（包括没挂在对话上的）都交给 onNotice 的监听者：桌面窗口弹系统通知、侧栏没加载到的对话重新拉列表。

import { useSyncExternalStore } from "react";
import { markNoticesSeen } from "./api";
import { type FeedEvent, isNotice, type NoticeEvent } from "./events";

export type Activity = "running" | "unread" | "notice";

const state = new Map<string, "running" | "unread">();
// 有没看过的提醒的对话
const noticed = new Set<string>();
// 对话里显示出来的最新一条提醒、已告诉服务端看过的最新一条：同一条只报一次
const latestNotice = new Map<string, string>();
const reported = new Map<string, string>();
// 提醒把对话浮到最上面：对话编号 → 提醒时间（毫秒）；侧栏按它与最后活动时间里较晚的一个排序
let bumps: ReadonlyMap<string, number> = new Map();
const listeners = new Set<() => void>();
const noticeListeners = new Set<(notice: NoticeEvent) => void>();
let active: string | null = null;

const changed = () => listeners.forEach((listener) => listener());

function set(sessionId: string, next: "running" | "unread" | null) {
  if ((state.get(sessionId) ?? null) === next) return;
  if (next) state.set(sessionId, next);
  else state.delete(sessionId);
  changed();
}

/** 正看着这段对话（当前对话、页面在前台）。 */
function viewing(sessionId: string): boolean {
  return sessionId === active && document.visibilityState === "visible";
}

/** 正看着：圆点消失；对话里有还没报过的提醒就告诉服务端看过了。 */
function flush(sessionId: string) {
  if (!viewing(sessionId)) return;
  if (noticed.delete(sessionId)) changed();
  const last = latestNotice.get(sessionId);
  if (!last || reported.get(sessionId) === last) return;
  reported.set(sessionId, last);
  void markNoticesSeen(sessionId).catch(() => reported.delete(sessionId));
}

/** 这段对话里显示出来的最新一条提醒（runtime-provider：当前对话的提醒有变化时调）。 */
export function noticesShown(sessionId: string, lastNoticeId: string) {
  latestNotice.set(sessionId, lastNoticeId);
  flush(sessionId);
}

function noteNotice(sessionId: string, notice: NoticeEvent) {
  if (sessionId) {
    if (viewing(sessionId)) flush(sessionId);
    else if (!noticed.has(sessionId)) {
      noticed.add(sessionId);
      changed();
    }
    const at = Date.parse(notice.processed_at) || Date.now();
    if ((bumps.get(sessionId) ?? 0) < at) {
      bumps = new Map(bumps).set(sessionId, at);
      changed();
    }
  }
  noticeListeners.forEach((listener) => listener(notice));
}

/** 收到某个会话的推送事件。等工具结果、等批准时引擎也是 idle(requires_action)，这一轮还没结束，照旧算在跑。
 *  提醒（sessionId 可能为空：没挂在对话上）另记，见文件头。 */
export function noteEvent(sessionId: string, event: FeedEvent) {
  if (isNotice(event)) return noteNotice(sessionId, event);
  if (!sessionId) return;
  const type = (event as { type?: string }).type;
  if (type === "session.status_running") set(sessionId, "running");
  else if (type === "session.status_idle") {
    const reason = (event as { stop_reason?: { type?: string } }).stop_reason?.type;
    if (reason === "requires_action") set(sessionId, "running");
    else set(sessionId, sessionId === active ? null : "unread");
  } else if (type === "session.status_terminated" || type === "session.deleted") set(sessionId, null);
}

/** 侧栏列表带回的「有没看过的提醒」（服务端 sessions.listing 的 unread）。 */
export function seedUnread(sessionId: string) {
  if (viewing(sessionId)) flush(sessionId);
  else if (!noticed.has(sessionId)) {
    noticed.add(sessionId);
    changed();
  }
}

/** 正在看的对话：它的圆点立即消失，之后跑完也不再留点；对话里的提醒算看过了。 */
export function setActiveSession(sessionId: string | null) {
  active = sessionId;
  if (!sessionId) return;
  if (state.get(sessionId) === "unread") set(sessionId, null);
  flush(sessionId);
}

// 页面回到前台：正看着的对话在后台时到的提醒算看过了
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (active) flush(active);
  });
}

export function useActivity(sessionId: string | undefined): Activity | undefined {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => (sessionId ? (state.get(sessionId) ?? (noticed.has(sessionId) ? "notice" : undefined)) : undefined),
  );
}

/** 提醒浮顶的时间（对话编号 → 毫秒）。 */
export function useBumps(): ReadonlyMap<string, number> {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => bumps,
  );
}

/** 每条推来的提醒（含没挂对话的）；返回取消订阅。 */
export function onNotice(listener: (notice: NoticeEvent) => void): () => void {
  noticeListeners.add(listener);
  return () => {
    noticeListeners.delete(listener);
  };
}
