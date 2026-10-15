// 侧栏每个对话的状态点（同 Claude）：正在跑的转圈，跑完而你没在看的留一个小圆点，点开就消失。
// 不为后台对话另开连接：服务端转发线程把每个会话的事件都推到用户自己的房间（realtime.ts），这里只看状态事件。
// 只记本页打开以后发生的；刷新后没有历史状态。
//
// 本站提醒（pearnly.notice）另记一个圆点：服务端记着有没有看过（列表带回 unread，刷新后仍在；别处看过了列表带回
// unread 为假，圆点去掉）。正看着的对话里显示出了新的提醒（推送、补拉、整段回放带回来的都算，runtime-provider 报
// noticesShown），就告诉服务端看过了（notices_seen）。「正看着」= 当前对话、对话页（不是定时任务页）、页面在前台。
// 提醒到的对话浮到侧栏最上面（useBumps）。
// 所有推来的提醒（包括没挂在对话上的）都交给 onNotice 的监听者：桌面窗口弹系统通知、侧栏没加载到的对话重新拉列表。
// 每个监听者单独兜住异常，一个出错不挡别的。
// 「在跑」也按服务端读回的运行状态校正（noteRun）：开着的对话由它的会话控制器报；别的对话在回到前台、恢复、联网、重连时
// 由侧栏列表核对（list-sync.ts 的 watchList）。在后台漏了「结束」的推送，转圈不会一直挂着。

import { useSyncExternalStore } from "react";
import { markNoticesSeen } from "./api";
import { type FeedEvent, isNotice, type NoticeEvent, type RunStatus } from "./events";

export type Activity = "running" | "unread" | "notice";

const state = new Map<string, "running" | "unread">();
// 有没看过的提醒的对话 → 圆点出现的时间（毫秒）
const noticed = new Map<string, number>();
// 对话里显示出来的最新一条提醒、已告诉服务端看过的最新一条：同一条只报一次
const latestNotice = new Map<string, string>();
const reported = new Map<string, string>();
// 提醒把对话浮到最上面：对话编号 → 提醒时间（毫秒）；侧栏按它与最后活动时间里较晚的一个排序
let bumps: ReadonlyMap<string, number> = new Map();
const listeners = new Set<() => void>();
const noticeListeners = new Set<(notice: NoticeEvent) => void>();
let active: string | null = null;
// 对话页开着（不是定时任务页）：assistant.tsx 按当前视图设
let chatView = true;

const changed = () => listeners.forEach((listener) => listener());

function set(sessionId: string, next: "running" | "unread" | null) {
  if ((state.get(sessionId) ?? null) === next) return;
  if (next) state.set(sessionId, next);
  else state.delete(sessionId);
  changed();
}

/** 正看着这段对话（当前对话、对话页开着、页面在前台）。 */
function viewing(sessionId: string): boolean {
  return sessionId === active && chatView && document.visibilityState === "visible";
}

function mark(sessionId: string) {
  if (noticed.has(sessionId)) return;
  noticed.set(sessionId, Date.now());
  changed();
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
    else mark(sessionId);
    const at = Date.parse(notice.processed_at) || Date.now();
    if ((bumps.get(sessionId) ?? 0) < at) {
      bumps = new Map(bumps).set(sessionId, at);
      changed();
    }
  }
  noticeListeners.forEach((listener) => {
    try {
      listener(notice);
    } catch (error) {
      console.error("[cc] notice listener failed", error);
    }
  });
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

/** 服务端读回的此刻运行状态：在跑、等处理算在跑；侧栏还标着在跑而服务端说结束了，按收到「结束」处理（正看着的不留点，
 *  别的留「跑完没看」的点）；本来没标在跑的不因此多出圆点。 */
export function noteRun(sessionId: string, run: RunStatus) {
  if (run.status === "running" || run.status === "requires_action") set(sessionId, "running");
  else if (state.get(sessionId) === "running") set(sessionId, run.status === "idle" && sessionId !== active ? "unread" : null);
}

/** 侧栏标着在跑、但不是正开着的那个对话（开着的由它自己的会话控制器核对）：要向服务端核对的。 */
export function runningSessions(): string[] {
  return [...state].filter(([id, value]) => value === "running" && id !== active).map(([id]) => id);
}

/** 侧栏列表带回的「有没看过的提醒」（服务端 sessions.listing 的 unread）。 */
export function seedUnread(sessionId: string) {
  if (viewing(sessionId)) flush(sessionId);
  else mark(sessionId);
}

/** 侧栏列表说这段对话的提醒都看过了（unread 为假，例如在手机上看过）：去掉提醒圆点。since：这次列表请求发出的时间，
 *  之后才推来的提醒照样留点（列表那一刻服务端还没有它）。正看着的对话由 flush 管。 */
export function clearUnread(sessionId: string, since = Number.POSITIVE_INFINITY) {
  if (viewing(sessionId)) return;
  const at = noticed.get(sessionId);
  if (at === undefined || at >= since) return;
  noticed.delete(sessionId);
  changed();
}

/** 正在看的对话：它的圆点立即消失，之后跑完也不再留点；对话里的提醒算看过了。 */
export function setActiveSession(sessionId: string | null) {
  active = sessionId;
  if (!sessionId) return;
  if (state.get(sessionId) === "unread") set(sessionId, null);
  flush(sessionId);
}

/** 对话页开着没有（切到定时任务页时为假）：不在对话页时推来的提醒不算看过，回到对话页才算。 */
export function setChatView(open: boolean) {
  chatView = open;
  if (open && active) flush(active);
}

// 页面回到前台：正看着的对话在后台时到的提醒算看过了
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (active) flush(active);
  });
}

/** 这段对话现在的行尾状态（useActivity 读的就是它）。 */
export function activityOf(sessionId: string | undefined): Activity | undefined {
  return sessionId ? (state.get(sessionId) ?? (noticed.has(sessionId) ? "notice" : undefined)) : undefined;
}

export function useActivity(sessionId: string | undefined): Activity | undefined {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => activityOf(sessionId),
  );
}

/** 这几段对话里有没有在跑、没看过的（侧栏收起的公司组在组名旁显示一个点）。 */
export function useAnyActivity(sessionIds: readonly string[]): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => sessionIds.some((id) => activityOf(id) !== undefined),
  );
}

/** 这几段对话各自的状态（公司页「对话」按进行中 / 等你处理 / 已完成分组）：任何一段变了才重画。 */
export function useActivities(sessionIds: readonly string[]): Record<string, Activity | undefined> {
  const snapshot = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => sessionIds.map((id) => activityOf(id) ?? "").join("\n"),
  );
  const states = snapshot.split("\n");
  return Object.fromEntries(sessionIds.map((id, i) => [id, (states[i] || undefined) as Activity | undefined]));
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
