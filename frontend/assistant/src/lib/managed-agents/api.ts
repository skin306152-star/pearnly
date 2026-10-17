// 对话页用到的全部接口（`pearnly_core.assistant.api`）。会话归属、工具执行都在服务端判定。

import { get, post, upload } from "@/lib/frappe";
import type { FeedEvent, RunStatus, SessionSummary } from "./events";

/** 引擎事件，整段回放与增量补拉都按时间并入本站的提醒（pearnly.notice）。要了状态（status）才带 run（读不到也没有）。 */
export type EventsPage = { events: FeedEvent[]; full: boolean; run?: RunStatus };

/** 侧栏一行。产品有公司分组（Cowork，ADR-020）时多三项：project 所属公司（"" = 最近）、period 处理月份（YYYY-MM，"" = 不指定）、
 *  scope 分店号（5 位，"" = 不指定）；旧服务端、没有公司分组的产品没有这三项。 */
export type ListedSession = SessionSummary & { project?: string; period?: string; scope?: string };

/** groups：按公司分组的第一页才有，还有更多的公司 → 它的游标（"" = 这家从头翻）。ordered：同在第一页，组里手排过、按手排一并
 *  给的对话编号（不是各组最新的那几条，list-sync.ts 核对时不按它们算各组翻到哪儿）。 */
export type SessionPage = {
  sessions: ListedSession[];
  nextCursor: string | null;
  runs?: Record<string, RunStatus>;
  groups?: Record<string, string>;
  ordered?: string[];
};

/** 侧栏一页（服务端 sessions.listing）：after 是上一页给的 nextCursor，空表示第一页（含全部置顶的）。
 *  check：侧栏以为还在回复的对话，服务端各读一次此刻的运行状态放进 runs（不给就不读）。 */
export function listSessions(after?: string, check: readonly string[] = []): Promise<SessionPage> {
  return get("listing", { after, check: check.length ? check.join(",") : undefined });
}

/** 按公司分组的侧栏（grouped=1）。不给 group：第一页（全部置顶 + 最近一页 + 每家公司最新几条，groups 给还有更多的公司）；
 *  给 group（"" = 最近，或公司编号）：只翻那一组，after 是那一组的游标。 */
export function listGrouped(page: { group?: string; after?: string; check?: readonly string[] } = {}): Promise<SessionPage> {
  const check = page.check ?? [];
  return get("listing", {
    grouped: 1,
    group: page.group,
    after: page.group === undefined ? undefined : (page.after ?? ""),
    check: check.length ? check.join(",") : undefined,
  });
}

/** 一段对话挂在哪：公司、处理月份、分店（都可以是 ""）。 */
export type Filing = { project: string; period: string; scope: string };

/** 新对话；filing：开在哪家公司、处理哪个月、哪个分店（公司已被删时服务端照常建在最近，月份分店随之丢弃）。 */
export function createSession(filing?: Partial<Filing>): Promise<ListedSession> {
  if (!filing?.project) return post("create");
  return post("create", {
    project: filing.project,
    ...(filing.period ? { period: filing.period } : {}),
    ...(filing.scope ? { scope: filing.scope } : {}),
  });
}

/** 改对话的处理月份、分店（project 是它现在的公司，服务端核对：不同即拒绝——对话不能换公司）。不改置顶、不改最后活动时间。 */
export function moveSession(sessionId: string, filing: Filing): Promise<ListedSession & { project?: string }> {
  return post("move", { session_id: sessionId, project: filing.project, period: filing.period, scope: filing.scope });
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

/** 这段对话的提醒看过了（侧栏圆点消失；服务端记 seen）。 */
export async function markNoticesSeen(id: string): Promise<void> {
  await post("notices_seen", { session_id: id });
}

/** 不带 after：整段历史；带 after：只要它之后的新事件（锚点丢了服务端会退回整段，full=true）。
 *  status：顺带要一份此刻的运行状态（多读一次引擎状态，只在重放、回到前台、重连这些时刻要）。 */
export function listEvents(id: string, after?: string, status = false): Promise<EventsPage> {
  return get("events", { session_id: id, after, status: status ? 1 : undefined });
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

/** 上传结果；PDF 读得出页数时带 pages（对话里附件卡片的「PDF · N 页」）。 */
export type UploadedFile = { fileId: string; filename: string; mountPath: string; size_bytes: number; pages?: number };

export type SessionFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  downloadable: boolean;
  created_at: string;
  kind: "upload" | "output";
  /** 附件是 PDF 且读得出页数时有（服务端按本站留存的那份读） */
  pages?: number;
};

/** 附件：传到本站，服务端校验后存进会话（HEIC 照片会转成 JPEG）。 */
export function uploadFile(id: string, file: File): Promise<UploadedFile> {
  return upload("upload", { session_id: id }, file);
}

export function listSessionFiles(id: string): Promise<SessionFile[]> {
  return get("session_files", { session_id: id });
}

/** Word 文档预览里的一段文字或一张图（服务端 `docx_preview` 读出的纯数据：没有链接地址，图片只有 data: 地址）。 */
export type DocRun =
  | { text: string; bold?: boolean; italic?: boolean; underline?: boolean; strike?: boolean; sup?: boolean; sub?: boolean; link?: boolean }
  | { image: string; width?: number; height?: number };

export type DocCell = { blocks: DocBlock[]; colspan?: number; rowspan?: number };

export type DocBlock =
  | {
      type: "paragraph";
      runs: DocRun[];
      style?: "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "title" | "subtitle";
      align?: "center" | "right" | "justify";
      list?: { level: number; marker: string };
    }
  | { type: "table"; rows: Array<{ cells: DocCell[]; header?: boolean }> };

export type FilePreview =
  | { type: "sheets"; sheets: Array<{ name: string; rows: string[][] }> }
  | { type: "document"; blocks: DocBlock[] }
  | { type: "pdf"; pages: number }
  | { type: "none" };

/** 结果文件的结构化预览：Excel 表格、Word 文档；其余类型或读不出是 none（页面给下载）。 */
export function previewFile(id: string, fileId: string): Promise<FilePreview> {
  return get("preview", { session_id: id, file_id: fileId });
}

/** PDF 的一页画成的图（服务端 files.page_image）：thumb = 附件卡片缩略图（第一页），page = 手机上的整页预览。
 *  同一个文件编号的内容不变，服务端给私有缓存头，浏览器同一张只取一次。 */
export function pageUrl(id: string, fileId: string, page: number, size: "thumb" | "page"): string {
  const query = new URLSearchParams({ session_id: id, file_id: fileId, page: String(page), size });
  return `/api/method/pearnly_core.assistant.api.page_image?${query}`;
}

/** 结果文件（与用户发的附件）的地址。inline 只对图片、PDF 生效，其余类型服务端一律按下载给出。 */
export function fileUrl(id: string, fileId: string, inline = false): string {
  const query = new URLSearchParams({ session_id: id, file_id: fileId, inline: inline ? "1" : "0" });
  return `/api/method/pearnly_core.assistant.api.download?${query}`;
}
