// 对话页用到的全部接口（`pearnly_core.assistant.api`）。会话归属、工具执行都在服务端判定。

import { get, post } from "@/lib/frappe";
import type { SessionSummary, StreamEvent } from "./events";

export type EventsPage = { events: StreamEvent[]; full: boolean };

export function listSessions(): Promise<{ sessions: SessionSummary[]; nextCursor: string | null }> {
  return get("listing");
}

export function createSession(): Promise<SessionSummary> {
  return post("create");
}

export function getSession(id: string): Promise<SessionSummary> {
  return get("get", { session_id: id });
}

export async function renameSession(id: string, title: string): Promise<void> {
  await post("rename", { session_id: id, title });
}

export async function archiveSession(id: string): Promise<void> {
  await post("archive", { session_id: id });
}

export async function deleteSession(id: string): Promise<void> {
  await post("delete", { session_id: id });
}

/** 不带 after：整段历史；带 after：只要它之后的新事件（锚点丢了服务端会退回整段，full=true）。 */
export function listEvents(id: string, after?: string): Promise<EventsPage> {
  return get("events", { session_id: id, after });
}

export async function sendMessage(id: string, text: string): Promise<void> {
  await post("send", { session_id: id, text });
}

export async function confirmTools(
  id: string,
  confirmations: Array<{ tool_use_id: string; result: "allow" | "deny"; deny_message?: string }>,
): Promise<void> {
  await post("confirm", { session_id: id, confirmations: JSON.stringify(confirmations) });
}

export async function interruptSession(id: string): Promise<void> {
  await post("interrupt", { session_id: id });
}

/** 提醒服务端执行一次自定义工具调用；工具名和参数服务端自己从会话里读，不由浏览器给。 */
export async function runTool(id: string, toolUseId: string): Promise<void> {
  await post("run_tool", { session_id: id, tool_use_id: toolUseId });
}
