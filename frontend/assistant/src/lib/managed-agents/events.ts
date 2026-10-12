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
};

/** 对话页收到的全部事件：引擎事件 + 本站事件（类型以 pearnly. 开头）。 */
export type FeedEvent = StreamEvent | NoticeEvent;

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

/** Row shape the sidebar and history endpoints hand the browser. */
export type SessionSummary = Pick<
  ManagedAgentsSession,
  "id" | "title" | "status" | "created_at" | "updated_at" | "archived_at"
> & { starred?: boolean; /** 有没看过的提醒（侧栏圆点） */ unread?: boolean };

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
