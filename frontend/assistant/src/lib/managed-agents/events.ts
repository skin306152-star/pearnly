// Types shared by the server routes and the browser reducer. These are pure
// type re-exports (erased at build time), so importing them client-side pulls
// none of the SDK into the bundle.
//
// 这就是站点交给对话页的统一对话事件（沿用 Managed Agents 事件格式，见 pearnly_core/assistant/engines/base.py）：
// 换引擎时由服务端适配器翻译成这一种，对话页不改。
import type {
  BetaManagedAgentsSessionEvent,
  BetaManagedAgentsStreamSessionEvents,
} from "@anthropic-ai/sdk/resources/beta/sessions/events";
import type { BetaManagedAgentsSession } from "@anthropic-ai/sdk/resources/beta/sessions/sessions";

/** Any event that can be persisted in a session's history (has an id). */
export type SessionEvent = BetaManagedAgentsSessionEvent;

/** Anything that can arrive on the live stream: persisted events plus the
 *  token-preview pair (event_start / event_delta), which are never persisted. */
export type StreamEvent = BetaManagedAgentsStreamSessionEvents;

export type ManagedAgentsSession = BetaManagedAgentsSession;

/** 本站自己的提醒（pearnly_core.assistant.notices）：定时任务提醒、完成简报等。只在站点与对话页之间，不进引擎会话；
 *  整段回放时按时间并在引擎事件里，推送时 session 为空表示没挂在对话上（只弹系统通知）。编号带 pnotice_ 前缀。 */
export type NoticeEvent = {
  type: "pearnly.notice";
  id: string;
  processed_at: string;
  kind: string;
  title: string;
  text: string;
  session: string;
  /** 没挂对话时点开去哪（服务端 contracts.NOTICE_VIEWS 或 company:<公司>）；没有 = 定时任务页 */
  view?: string;
};

/** 卡片的回答（pearnly_core.assistant.toolrun.decide，contracts.answer_event）：本站拦下等「允许」的调用被点了「允许 / 拒绝」，
 *  回答落库就推来（不等工具执行完），同一段对话开在别的设备上的卡片立即换成点完的样子（第 10 轮 C22）。只走推送、不在事件记录里；
 *  编号带 panswer_ 前缀。result 同引擎 user.tool_confirmation。 */
export type AnswerEvent = {
  type: "pearnly.answer";
  id: string;
  processed_at: string;
  tool_use_id: string;
  result: "allow" | "deny";
};

/** 本站执行记录（Pearnly Assistant Tool Run）的状态，同服务端 contracts.TOOL_RUN_STATES。 */
export type ToolRunStatus = "awaiting_approval" | "running" | "done" | "error" | "denied" | "cancelled";

/** 一次本站工具调用在本站的执行状态（pearnly_core.assistant.toolrun，contracts.tool_run_event）：本站拦下等「允许」的卡片
 *  最后什么样子以它为准（第 11 轮 A 段）。状态变了（执行完 / 出错、拒绝、作废）推来；读事件时按这页出现过的调用编号并在
 *  这页最后；点「允许 / 拒绝」时服务端回的此刻状态（confirm 的 runs）也照它折进来。编号 ptoolrun_<调用编号>_<状态>。 */
export type ToolRunEvent = {
  type: "pearnly.tool_run";
  id: string;
  processed_at: string | null;
  tool_use_id: string;
  status: ToolRunStatus;
};

/** 对话页收到的全部事件：引擎事件 + 本站事件（类型以 pearnly. 开头）。 */
export type FeedEvent = StreamEvent | NoticeEvent | AnswerEvent | ToolRunEvent;

/** 一条执行记录事件（服务端 contracts.tool_run_event 的同一种写法；confirm 回的状态在页面这边照它合成）。 */
export function toolRunEvent(toolUseId: string, status: ToolRunStatus, processedAt: string | null = null): ToolRunEvent {
  return { type: "pearnly.tool_run", id: `ptoolrun_${toolUseId}_${status}`, processed_at: processedAt, tool_use_id: toolUseId, status };
}

/** 本站事件不在引擎的事件记录里：补拉不能拿它的编号当锚点（服务端找不到，会退回整段重放）。 */
export function isLocalEvent(event: FeedEvent): boolean {
  return String(event.type).startsWith("pearnly.");
}

export function isNotice(event: FeedEvent): event is NoticeEvent {
  return event.type === "pearnly.notice";
}

export type EventOf<T extends StreamEvent["type"]> = Extract<StreamEvent, { type: T }>;

/** Persisted events carry an id; the preview pair does not. */
export function isPersisted(event: FeedEvent): event is Extract<FeedEvent, { id: string }> {
  return event.type !== "event_start" && event.type !== "event_delta";
}

/** Events that carry a tool the agent invoked. All of them can be the target
 *  of a `requires_action` stop, and all render as tool-call parts. */
export type ToolUseEvent = EventOf<
  "agent.tool_use" | "agent.mcp_tool_use" | "agent.custom_tool_use"
>;

export function isToolUse(event: StreamEvent): event is ToolUseEvent {
  return (
    event.type === "agent.tool_use" ||
    event.type === "agent.mcp_tool_use" ||
    event.type === "agent.custom_tool_use"
  );
}

// ---------------------------------------------------------------------------
// The wire format between our server and the browser.
//
// The tail route (GET /api/sessions/[id]/stream) relays each Managed Agents
// event as one SSE `data:` line of {"event": <StreamEvent>}. Two envelope
// kinds exist only so the browser can tell "the session's stream said this"
// apart from "our relay is telling you something about itself".
// ---------------------------------------------------------------------------

export type RelayEnvelope =
  /** A Managed Agents event (or one of this site's own notices), passed through untouched. */
  | { kind: "event"; event: FeedEvent }
  /** The relay lost the upstream stream. The session keeps running
   *  server-side; the browser should replay history and reattach rather
   *  than treat this as a failed turn. */
  | { kind: "relay_error"; message: string };

/** 服务端读回的对话此刻的运行状态（pearnly_core.assistant.contracts.run_state）：以引擎的会话状态为准，停着等的调用
 *  再按本站执行记录分出等用户点「允许」的与本站正在执行的。对话页与侧栏在重放、重进、回到前台、联网、实时通道重连这些
 *  时刻按它改，不从「没收到某条事件」推断。as_of：服务端读状态前读到的最新一条事件的时间（不让它盖过比它新的状态事件）。 */
export type RunStatus = {
  status: "idle" | "running" | "requires_action" | "terminated";
  parked: string[];
  awaiting_approval: string[];
  executing: string[];
  as_of: string | null;
};

/** Row shape the sidebar and history endpoints hand the browser. status 只在真读过时有（建会话、取会话、侧栏核对过的）。 */
export type SessionSummary = Pick<ManagedAgentsSession, "id" | "title" | "created_at" | "updated_at" | "archived_at"> & {
  status?: ManagedAgentsSession["status"] | RunStatus["status"] | null;
  starred?: boolean;
  /** 有没看过的提醒（侧栏圆点） */
  unread?: boolean;
};

export const CUSTOM_TOOL_ACK_TEXT = "Rendered to the user in the chat.";

export type AttachedFileRef = { filename: string; mountPath: string };

/** The note appended to a user message telling the agent where uploads
 *  landed in the sandbox. Shared by the messages route and the browser's
 *  optimistic bubble so both render byte-identical text (the reducer
 *  reconciles the echo against the bubble by its opening). */
export function attachmentNote(files: readonly AttachedFileRef[]): string {
  if (files.length === 0) return "";
  const noun =
    files.length === 1
      ? "file, mounted read-only in the sandbox"
      : "files, mounted read-only in the sandbox";
  const lines = files.map((f) => `- ${f.filename} → ${f.mountPath}`);
  return `\n\n[Attached ${noun}:\n${lines.join("\n")}]`;
}

/** Display side of attachmentNote(): the bubble shows the user's own words
 *  plus file chips, not the sandbox note the agent reads. Inverse of the
 *  format above; anything that doesn't match stays as plain text. */
export function splitAttachmentNote(text: string): { text: string; files: AttachedFileRef[] } {
  const match = /(?:^|\n\n)\[Attached files?, mounted read-only in the sandbox:\n((?:- .+ → .+\n?)+)\]$/.exec(text);
  if (!match) return { text, files: [] };
  const files = match[1]
    .trim()
    .split("\n")
    .map((line) => /^- (.+) → (.+)$/.exec(line))
    .filter((m): m is RegExpExecArray => !!m)
    .map((m) => ({ filename: m[1], mountPath: m[2] }));
  return { text: text.slice(0, match.index), files };
}

/** The full user-message text as it will be sent (and echoed back). */
export function messageText(text: string, files: readonly AttachedFileRef[]): string {
  return text + attachmentNote(files);
}
