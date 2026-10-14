// 站点实时通道（Frappe 自带的 socket.io）：服务端转发线程把会话的新事件推到当前用户自己的房间
// （pearnly_core.assistant.relay，事件名 pearnly_assistant_event）。整页只开一条连接，各会话按编号订阅。
// 命名空间是站点名，Frappe 按请求的 Host 认站点，所以用当前页面的主机名；登录凭的是本站会话 cookie。
//
// 本站提醒（pearnly.notice，pearnly_core.assistant.notices）走同一个事件名；没挂在对话上的 session_id 为空，
// 照样交给 activity.ts（桌面窗口靠它弹系统通知）。产品自己的站点事件用 onSiteEvent 按名字订阅：
// 事件名由调用方给，这里不认识任何产品。
// 侧栏同步（pearnly.session，pearnly_core.assistant.sidebar）也走同一个事件名、session_id 为空：同一账号在别的设备上
// 新建、改名（含自动标题）、加星标、归档、删除对话时推来，交给 onSessionChange 的监听者（list-sync.ts）；
// 推来的标题顺手记下（latestTitle），侧栏给新对话取标题时直接用。
// 一条事件先交给这个会话的页面（卡片、回复），再记侧栏状态与提醒；每一路单独兜住异常，哪一路出错都不挡别的。

import { io, type Socket } from "socket.io-client";
import { noteEvent } from "./activity";
import { type FeedEvent, isNotice } from "./events";

const EVENT = "pearnly_assistant_event";

/** 侧栏同步的一条事件（服务端 contracts.session_change）：哪段对话、变了什么；标题、星标变了带新值。 */
export type SessionChange = {
  type: "pearnly.session";
  change: "created" | "titled" | "starred" | "archived" | "deleted";
  session: string;
  title?: string;
  starred?: boolean;
};

type Handler = (event: FeedEvent) => void;
type Push = { session_id: string; event: FeedEvent };

let socket: Socket | null = null;
const handlers = new Map<string, Set<Handler>>();
const statusListeners = new Set<(connected: boolean) => void>();
const sessionListeners = new Set<(change: SessionChange) => void>();
const titles = new Map<string, string>();

function safely(run: () => void) {
  try {
    run();
  } catch (error) {
    console.error("[cc] realtime listener failed", error);
  }
}

function sessionChange(event: unknown): SessionChange | null {
  const change = event as SessionChange;
  return change?.type === "pearnly.session" && typeof change.session === "string" ? change : null;
}

function noteSessionChange(change: SessionChange) {
  if (change.change === "titled" && change.title) titles.set(change.session, change.title);
  sessionListeners.forEach((listener) => safely(() => listener(change)));
}

/** 一条推来的事件：先给会话的页面，再给侧栏状态与提醒的监听者（activity.ts）；侧栏同步的只给 list-sync。 */
export function dispatch(push: Push) {
  if (!push?.event) return;
  const change = sessionChange(push.event);
  if (change) return noteSessionChange(change);
  if (push.session_id) handlers.get(push.session_id)?.forEach((handler) => safely(() => handler(push.event)));
  if (push.session_id || isNotice(push.event)) safely(() => noteEvent(push.session_id || "", push.event));
}

function connect(): Socket {
  if (socket) return socket;
  socket = io(`${window.location.origin}/${window.location.hostname}`, { withCredentials: true });
  socket.on(EVENT, dispatch);
  socket.on("connect", () => statusListeners.forEach((listener) => listener(true)));
  socket.on("disconnect", () => statusListeners.forEach((listener) => listener(false)));
  return socket;
}

export function isConnected(): boolean {
  return Boolean(socket?.connected);
}

/** 实时通道收到服务端的心跳（socket.io 的 ping，默认约 25 秒一次）；返回取消订阅。会话据此分辨「慢」和「断」
 *  （session-controller 的 STALL_MS）。 */
export function onHeartbeat(listener: () => void): () => void {
  const manager: Socket["io"] | undefined = connect().io;
  manager?.on("ping", listener);
  return () => {
    manager?.off("ping", listener);
  };
}

/** 订阅一个会话的推送；返回取消订阅。`onStatus` 在实时通道连上 / 断开时调用。 */
export function subscribe(sessionId: string, handler: Handler, onStatus: (connected: boolean) => void): () => void {
  connect();
  const set = handlers.get(sessionId) ?? new Set<Handler>();
  set.add(handler);
  handlers.set(sessionId, set);
  statusListeners.add(onStatus);
  return () => {
    set.delete(handler);
    if (!set.size) handlers.delete(sessionId);
    statusListeners.delete(onStatus);
  };
}

/** 订阅一个站点实时事件（服务端 frappe.publish_realtime 推给本人的）；返回取消订阅。 */
export function onSiteEvent(event: string, handler: (data: unknown) => void): () => void {
  const s = connect();
  s.on(event, handler);
  return () => {
    s.off(event, handler);
  };
}

/** 实时通道连上 / 断开时调用（重连后各页面补刷一次，补上断线期间漏掉的事件）；返回取消订阅。 */
export function onConnectionChange(listener: (connected: boolean) => void): () => void {
  connect();
  statusListeners.add(listener);
  return () => {
    statusListeners.delete(listener);
  };
}

/** 先接上实时通道：新对话发出第一条消息前调用，免得标题推来时还没连上。 */
export function listen(): void {
  connect();
}

/** 侧栏同步事件（别的设备、或服务端起好标题时推来的）；返回取消订阅。 */
export function onSessionChange(listener: (change: SessionChange) => void): () => void {
  connect();
  sessionListeners.add(listener);
  return () => {
    sessionListeners.delete(listener);
  };
}

/** 这段对话最近推来的标题（临时标题或概括出的），没推来过回 undefined。 */
export function latestTitle(sessionId: string): string | undefined {
  return titles.get(sessionId);
}
