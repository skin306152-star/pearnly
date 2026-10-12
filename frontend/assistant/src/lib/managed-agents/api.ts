// 对话页用到的全部接口（`pearnly_core.assistant.api`）。会话归属、工具执行都在服务端判定。

import { get, post, upload } from "@/lib/frappe";
import type { SessionSummary, StreamEvent } from "./events";

export type EventsPage = { events: StreamEvent[]; full: boolean };

/** 侧栏一页（服务端 sessions.listing）：after 是上一页给的 nextCursor，空表示第一页（含全部加星标的）。 */
export function listSessions(after?: string): Promise<{ sessions: SessionSummary[]; nextCursor: string | null }> {
  return get("listing", { after });
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

export async function starSession(id: string, starred: boolean): Promise<void> {
  await post("star", { session_id: id, starred: starred ? 1 : 0 });
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

/** 提醒服务端执行一次自定义工具调用；工具名和参数服务端自己从会话里读，不由浏览器给。
 *  回 awaiting_approval：改共用设置的工具要用户先点「允许」（服务端拦下，答复走 confirm）。 */
export type ToolRunReply = {
  status: string;
  ask?: string;
  preview?: Array<{ field: string; before: string; after: string }>;
};

export async function runTool(id: string, toolUseId: string): Promise<ToolRunReply> {
  const out = await post<Partial<ToolRunReply>>("run_tool", { session_id: id, tool_use_id: toolUseId });
  return { status: out?.status ?? "", ask: out?.ask, preview: out?.preview };
}

export type UploadedFile = { fileId: string; filename: string; mountPath: string; size_bytes: number };

export type SessionFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  downloadable: boolean;
  created_at: string;
  kind: "upload" | "output";
};

/** 附件：传到本站，服务端校验后存进会话（HEIC 照片会转成 JPEG）。 */
export function uploadFile(id: string, file: File): Promise<UploadedFile> {
  return upload("upload", { session_id: id }, file);
}

export function listSessionFiles(id: string): Promise<SessionFile[]> {
  return get("session_files", { session_id: id });
}

export type SheetPreview = { type: "sheets"; sheets: Array<{ name: string; rows: string[][] }> } | { type: "none" };

/** 结果文件的结构化预览（目前是 Excel 表格）。 */
export function previewFile(id: string, fileId: string): Promise<SheetPreview> {
  return get("preview", { session_id: id, file_id: fileId });
}

/** 结果文件的地址。inline 只对图片、PDF 生效，其余类型服务端一律按下载给出。 */
export function fileUrl(id: string, fileId: string, inline = false): string {
  const query = new URLSearchParams({ session_id: id, file_id: fileId, inline: inline ? "1" : "0" });
  return `/api/method/pearnly_core.assistant.api.download?${query}`;
}
