// 站点实时通道（Frappe 自带的 socket.io）：服务端转发线程把会话的新事件推到当前用户自己的房间
// （pearnly_core.assistant.relay，事件名 pearnly_assistant_event）。整页只开一条连接，各会话按编号订阅。
// 命名空间是站点名，Frappe 按请求的 Host 认站点，所以用当前页面的主机名；登录凭的是本站会话 cookie。

import { io, type Socket } from "socket.io-client";
import type { StreamEvent } from "./events";

const EVENT = "pearnly_assistant_event";

type Handler = (event: StreamEvent) => void;
type Push = { session_id: string; event: StreamEvent };

let socket: Socket | null = null;
const handlers = new Map<string, Set<Handler>>();
const statusListeners = new Set<(connected: boolean) => void>();

function connect(): Socket {
  if (socket) return socket;
  socket = io(`${window.location.origin}/${window.location.hostname}`, { withCredentials: true });
  socket.on(EVENT, (push: Push) => {
    handlers.get(push?.session_id)?.forEach((handler) => handler(push.event));
  });
  socket.on("connect", () => statusListeners.forEach((listener) => listener(true)));
  socket.on("disconnect", () => statusListeners.forEach((listener) => listener(false)));
  return socket;
}

export function isConnected(): boolean {
  return Boolean(socket?.connected);
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
