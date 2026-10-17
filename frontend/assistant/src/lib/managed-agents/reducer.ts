// The one place that turns a Managed Agents event log into an assistant-ui
// conversation. It's a pure fold: the same function replays a stored session
// (GET /api/sessions/[id]/events) and folds live events off the tail, so an
// old chat and a live one can never diverge. No React, no fetching, no
// clocks: everything a test needs is an array of events.
//
// The mapping, in one table:
//   user.message                        → a user bubble
//   agent.message                       → assistant text (buffered, authoritative)
//   event_start / event_delta           → the same text, streamed early (token previews)
//   agent.thinking                      → an empty reasoning part (progress signal only)
//   agent.tool_use / mcp / custom       → a tool-call part; toolCallId is the *event* id
//   agent.tool_result / mcp（自带、MCP） → that part's result（结果到了就是放行过）
//   agent.tool_result（自定义工具）       → 平台代发的中断结果：自定义工具的结果正常由本站以 user.custom_tool_result 交回，
//                                          这一种只在点了停止时由引擎代发（第 11 轮 A 段实测：比 user.interrupt 早几十毫秒到）。
//                                          记成 interrupted 的结果，不算点了「允许」：还没答的门撤掉、标 cancelled（「已取消」）；
//                                          已点「允许」的照旧「允许」，等本站执行记录
//   user.custom_tool_result             → 本站交回的结果（我们的回答）
//   user.tool_confirmation              → that part's approval, settled
//   pearnly.answer                      → the same, pushed by this site the moment a held Pearnly tool's
//                                          answer is stored (另一台设备上的卡片立即点完，不等工具结果)
//   pearnly.tool_run                    → 本站执行记录的状态（part.run，按 RUN_RANK 只往前走）：本站拦下的卡片最后的样子以它为准
//                                          （lib/approval-card.ts）。done →「允许」、error →「允许 · 失败」、denied →「拒绝」、
//                                          cancelled →「已取消」、running →「允许」（淡）、awaiting_approval → 还在问。推来、读事件时
//                                          并在这页最后（整段：这段出现过的调用；增量：这页出现过的加锚点之后改过的，引擎收尾后才定下
//                                          的也补得到）、点「允许 / 拒绝」时 confirm 回的状态都折成这一种；先于调用本身到的记在 toolRuns
//   user.interrupt                      → 还没答的门撤掉、标 cancelled（之后补来的出错结果不算点了「允许」）
//   session.status_idle{requires_action} → approval:{id} on each blocked tool part
//   session.status_running / idle       → whether the turn is live
//   session.error                       → an error message + retry chip
//   pearnly.notice                      → a notice card (本站提醒，记在 notices，不进 messages；
//                                          页面经 withNotices 并进对话，见文件末尾)
//
// 运行状态（在回复 / 等允许 / 已结束）按服务端时间定，不按到达先后：推送与补拉交错时，晚到的旧状态事件不再盖过已采用的
// 新状态（statusAt）。服务端读回的此刻状态（RunStatus，applyRunStatus）在重放、重进、回到前台、重连时校正它。
//
// 「这一轮结束」只认真正的结束（空闲且不是停下来等处理、终止）。停下来等的调用都已有了答复（工具结果、允许 / 拒绝）而引擎
// 还没重新开跑的那段空档，同服务端读回的状态一样算在跑（2026-10-13 iPhone：两段工具之间约 10 秒显示成已结束、出现复制按钮、
// 发送键变回空闲）。

import type { FeedEvent, NoticeEvent, RunStatus, ToolRunStatus, ToolUseEvent } from "./events";
import { isPersisted, splitAttachmentNote } from "./events";
import { stepDenied } from "@/lib/step-summary";

// ---------------------------------------------------------------------------
// Message model (a structural subset of assistant-ui's ThreadMessageLike;
// the runtime casts across the boundary once, in the thread runtime hook).
// ---------------------------------------------------------------------------

/** What a tool call produced, normalized for the tool cards. */
export type ToolResultPayload = {
  text: string;
  isError: boolean;
  searchResults?: Array<{ title: string; url: string; snippet: string }>;
};

/** One line of what a held Pearnly tool call would change (server-computed). */
export type ApprovalChange = { field: string; before: string; after: string };

export type ToolApproval = {
  id: string;
  approved?: boolean;
  reason?: string;
  /** Held Pearnly tool calls: what the step asks ("Save this folder task?") and what it changes. */
  ask?: string;
  preview?: ApprovalChange[];
  /** 本页点了却没送到（确认请求出错，restoreGates）：按钮上方那一行原因，再点就清掉。 */
  failure?: string;
  /** 本页刚点、确认还没送到（乐观定下的）：点完那一行先淡一档，送到（markConfirmed）、回声或结果到了再定下。 */
  sending?: boolean;
};

export type TextPart = { type: "text"; text: string };
export type ReasoningPart = { type: "reasoning"; text: string };
export type ToolPart = {
  type: "tool-call";
  toolCallId: string;
  toolName: string;
  /** Managed Agents flavor of this tool, for renderers that care. */
  toolKind: "builtin" | "mcp" | "custom";
  mcpServer?: string;
  args: Record<string, unknown>;
  argsText: string;
  result?: ToolResultPayload;
  isError?: boolean;
  approval?: ToolApproval;
  /** 卡片没答就点了停止（user.interrupt 时门还开着、也没有结果；或平台代发的中断结果先到）：卡片写「已取消」
   *  （第 10 轮 C20、第 11 轮 A1，lib/approval-card.ts）。本页与重开对话都按事件记录认；本站执行记录（run）到了以它为准。 */
  cancelled?: boolean;
  /** result 是平台代发的中断结果（自定义工具的 agent.tool_result），不是本站交回的：卡片不按它定「允许 · 失败」。 */
  interrupted?: boolean;
  /** 本站执行记录的状态（pearnly.tool_run）：本站拦下的卡片最后的样子以它为准。 */
  run?: ToolRunStatus;
};
export type ContentPart = TextPart | ReasoningPart | ToolPart;

export type MessageStatus =
  | { type: "running" }
  | { type: "requires-action"; reason: "tool-calls" }
  | { type: "complete"; reason: "stop" | "unknown" }
  | { type: "incomplete"; reason: "cancelled" | "error"; error?: string };

export type MessageAttachment = {
  id: string;
  type: "image" | "document";
  name: string;
  contentType: string;
  status: { type: "complete" };
  content: [];
};

export type ThreadMessage = {
  id: string;
  role: "assistant" | "user";
  content: ContentPart[];
  createdAt: Date;
  status?: MessageStatus;
  /** Files the user attached, shown as chips in the bubble (parsed back out of the message text). */
  attachments?: MessageAttachment[];
  metadata?: { custom?: Record<string, unknown> };
};

/** 本站提醒（定时任务提醒、完成简报等），按时间先后。 */
export type NoticeItem = { id: string; createdAt: Date; kind: string; title: string; text: string };

/** 提醒卡片并进对话的那条（withNotices 生成）：组件库的 system 消息（只能有一段文字、不带状态），custom.notice = { kind, title }。
 *  不用 assistant：最后一条是 assistant 时组件库认为回复已经来了，不再补「思考中」占位，卡片也会被当成回复。 */
export type NoticeMessage = {
  id: string;
  role: "system";
  content: [TextPart];
  createdAt: Date;
  metadata: { custom: { notice: { kind: string; title: string } } };
};

const IMAGE_EXT = /\.(png|jpe?g|gif|webp)$/i;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** In-flight token preview: which text part it's writing into. */
type Preview = { messageIndex: number; partIndex: number };

export type ProjectionError = {
  message: string;
  retryStatus: "retrying" | "exhausted" | "terminal";
};

export type Projection = {
  messages: readonly ThreadMessage[];
  /** The agent is actively working (session status running). */
  isRunning: boolean;
  /** The session is unrecoverable (terminated/deleted): freeze the composer. */
  isDead: boolean;
  /** Event ids of built-in/MCP tool uses waiting on a human Allow/Deny. */
  pendingApprovals: string[];
  /** Every event id the session is CURRENTLY parked on (from the latest
   *  requires_action, cleared the moment the session runs or ends). Unlike
   *  pendingApprovals it is not affected by our optimistic clicks, so it's
   *  the ground truth for "is the server still waiting on this id?". */
  parkedIds: string[];
  /** Event ids of custom tool uses the client still owes a result for. */
  pendingCustomTools: string[];
  /** Custom tool uses already answered (from history or by us). */
  answeredCustomTools: ReadonlySet<string>;
  /** Tool-use ids settled by a REAL (persisted) confirmation, as opposed
   *  to our optimistic click. A restore must never reopen these. */
  confirmedIds: ReadonlySet<string>;
  /** Optimistic user bubbles whose real echo has landed (delivered). */
  echoedOptimistic: ReadonlySet<string>;
  /** Persisted event ids already folded (dedupe across replay + tail). */
  seen: ReadonlySet<string>;
  /** Live token previews keyed by the event id they'll reconcile into. */
  previews: ReadonlyMap<string, Preview>;
  /** 出错的模型请求收口时补了「 …」的预览：随后的重试（status_rescheduled）把它们清空，不留半句；本轮结束、会话终止时照旧留着。
   *  span 结束与 rescheduled 谁先到都一样（第 8 轮审查）。 */
  cutPreviews: readonly Preview[];
  /** All tool-use events seen, keyed by event id (drives requires_action). */
  toolUses: ReadonlyMap<string, ToolUseEvent>;
  /** Most recent session.error, cleared by the next running/message. */
  lastError: ProjectionError | null;
  /** 本站提醒，按时间先后（不在 messages 里：接续、乐观气泡、批准都不受影响）。 */
  notices: readonly NoticeItem[];
  /** 已采用的运行状态有多新（服务端时间，毫秒）：比它旧的状态事件只记已见、不再改运行状态。本页合成的不算。 */
  statusAt: number;
  /** 本站执行记录的状态，按调用编号（pearnly.tool_run 先于调用本身到时，建这一步时补上）。 */
  toolRuns: ReadonlyMap<string, ToolRunStatus>;
};

export const initialProjection = (): Projection => ({
  messages: [],
  isRunning: false,
  isDead: false,
  pendingApprovals: [],
  parkedIds: [],
  pendingCustomTools: [],
  answeredCustomTools: new Set(),
  confirmedIds: new Set(),
  echoedOptimistic: new Set(),
  seen: new Set(),
  previews: new Map(),
  cutPreviews: [],
  toolUses: new Map(),
  lastError: null,
  notices: [],
  statusAt: 0,
  toolRuns: new Map(),
});

// ---------------------------------------------------------------------------
// Content helpers
// ---------------------------------------------------------------------------

type LooseBlock = { type?: string; text?: string; title?: string; source?: string; content?: unknown };

function asBlocks(blocks: unknown): LooseBlock[] {
  return Array.isArray(blocks) ? (blocks as LooseBlock[]) : [];
}

function textOfBlocks(blocks: unknown): string {
  const parts: string[] = [];
  for (const block of asBlocks(blocks)) {
    if (block.type === "text" && typeof block.text === "string") parts.push(block.text);
  }
  return parts.join("\n\n");
}

/** Fold a tool_result content array into text + structured search results. */
export function toToolResult(content: unknown, isError: boolean): ToolResultPayload {
  const textParts: string[] = [];
  const searchResults: NonNullable<ToolResultPayload["searchResults"]> = [];
  for (const block of asBlocks(content)) {
    if (block.type === "text" && typeof block.text === "string") {
      textParts.push(block.text);
    } else if (block.type === "search_result") {
      searchResults.push({
        title: typeof block.title === "string" ? block.title : "",
        url: typeof block.source === "string" ? block.source : "",
        snippet: textOfBlocks(block.content).slice(0, 400),
      });
    } else if (block.type === "image") {
      textParts.push("[image]");
    } else if (block.type === "document") {
      textParts.push("[document]");
    }
  }
  return {
    text: textParts.join("\n"),
    isError,
    ...(searchResults.length ? { searchResults } : {}),
  };
}

// ---------------------------------------------------------------------------
// Immutable message helpers
// ---------------------------------------------------------------------------

const draft = (m: readonly ThreadMessage[]): ThreadMessage[] => [...m];

/** A "retrying" error is a live progress signal, not history: the moment the
 *  turn proves it recovered (produced output, or ended), the banner must go.
 *  A session that recovers in place never re-emits status_running (it was
 *  never idle), so waiting for that event would leave the banner up forever.
 *  Terminal/exhausted errors stay — they ARE the outcome. */
const clearRetrying = (err: ProjectionError | null): ProjectionError | null =>
  err?.retryStatus === "retrying" ? null : err;

// The reducer keeps no clock (a clock would make replay differ from live).
// Persisted events carry processed_at; the controller stamps its synthetic
// optimistic events too. Anything still missing a timestamp gets a fixed one.
const UNKNOWN_TIME = new Date(0);
const timeOf = (iso?: string | null) => (iso ? new Date(iso) : UNKNOWN_TIME);

// What a reasoning part says. The API exposes no thinking text, but the part
// must carry some: assistant-ui drops blank text/reasoning parts during
// conversion, and an invisible part can't signal anything.
export const THINKING_LABEL = "Thinking…";

/** Index of the assistant message that should absorb the current turn's
 *  activity, creating one if the last message is a user turn or nothing. */
function currentAssistantIndex(messages: ThreadMessage[], createdAt?: string | null): number {
  const last = messages[messages.length - 1];
  if (last && last.role === "assistant") {
    const li = messages.length - 1;
    // A message opened by a timestamp-less preview (event_start carries no
    // processed_at) gets the real time from the first persisted event that
    // lands on it, so live and replayed projections agree.
    if (createdAt && last.createdAt.getTime() === UNKNOWN_TIME.getTime()) {
      messages[li] = { ...last, createdAt: timeOf(createdAt) };
    }
    return li;
  }
  messages.push({
    id: `asst-${messages.length}`,
    role: "assistant",
    content: [],
    createdAt: timeOf(createdAt),
    status: { type: "running" },
  });
  return messages.length - 1;
}

/** A gate the server is no longer waiting on (interrupt, end of turn,
 *  termination) must not keep live Allow/Deny buttons. Strip approvals that
 *  never got an answer; leave answered ones as the record they are, and the
 *  ones in `keep` (the server is still parked on them). 已有结果的是别处答过的（别的设备点了、结果先到）：按结果定下
 *  所选那一项（settleApproval），卡片留着点完的样子，不当没答过撤掉。 */
function stripUnresolvedApprovals(
  messages: readonly ThreadMessage[],
  keep: ReadonlySet<string> = new Set(),
): readonly ThreadMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (msg.role !== "assistant") return msg;
    let msgChanged = false;
    const content = msg.content.map((part) => {
      if (part.type !== "tool-call" || !part.approval) return part;
      if (part.approval.approved !== undefined || keep.has(part.approval.id)) return part;
      msgChanged = changed = true;
      if (part.result !== undefined) return { ...part, approval: settleApproval(part.approval, !stepDenied(part)) };
      const rest = { ...part };
      delete rest.approval;
      return rest;
    });
    return msgChanged ? { ...msg, content } : msg;
  });
  return changed ? next : messages;
}

/** 定下一张卡的回答：问话与改动留着（点完的卡片照写问话），上次没送到的原因清掉；sending 只在本页乐观点下时带上。 */
function settleApproval(
  approval: ToolApproval | undefined,
  approved: boolean,
  id = approval?.id ?? "",
  reason?: string,
  sending = false,
): ToolApproval {
  const { failure: _failure, reason: _reason, approved: _approved, sending: _sending, ...kept } = approval ?? { id };
  return { ...kept, id, approved, ...(reason ? { reason } : {}), ...(sending ? { sending } : {}) };
}

/** 结果到了以后这张卡的回答：以服务端结果为准（写明拒绝就是拒绝；有结果、没写拒绝、不是出错交回的就是允许了，工具照常执行过）。
 *  本页乐观点的那一项与它不同时改过来（两台设备同时点、确认超时后改点），卡片不再一直写本页点的那一项（第 10 轮审查）。
 *  还没答的照结果定下；已答且一致的只去掉 sending。没有门回 undefined。 */
function approvalFromResult(approval: ToolApproval | undefined, result: ToolResultPayload): ToolApproval | undefined {
  if (!approval) return approval;
  const denied = stepDenied({ result });
  if (approval.approved === undefined) return settleApproval(approval, !denied);
  const approved = !denied && (approval.approved || !result.isError);
  if (approved !== approval.approved || approval.sending) return settleApproval(approval, approved, approval.id, approval.reason);
  return approval;
}

function withContent(message: ThreadMessage, content: ContentPart[]): ThreadMessage {
  return { ...message, content };
}

/** Is `b` the echo of our optimistic bubble `a`? The client and server
 *  build the message text with the same messageText(), so a real echo is
 *  identical. A prefix match is kept as slack for a server that appends. */
function sameOpening(a: readonly ContentPart[], b: readonly ContentPart[]): boolean {
  const at = a.find((p) => p.type === "text");
  const bt = b.find((p) => p.type === "text");
  if (!at || !bt || at.type !== "text" || bt.type !== "text") return false;
  return at.text === bt.text || bt.text.startsWith(at.text) || at.text.startsWith(bt.text);
}

/** A files-only message has no text to compare: match the echo by its attachments. */
function sameAttachments(a: ThreadMessage, b: ThreadMessage): boolean {
  const ids = (m: ThreadMessage) => (m.attachments ?? []).map((x) => x.id).join("|");
  return !a.content.length && !b.content.length && ids(a) !== "" && ids(a) === ids(b);
}

/** Find a tool-call part by its toolCallId (= the tool-use event id). */
function findToolPart(messages: readonly ThreadMessage[], toolCallId: string) {
  for (let mi = messages.length - 1; mi >= 0; mi--) {
    const msg = messages[mi]!;
    if (msg.role !== "assistant") continue;
    const pi = msg.content.findIndex((p) => p.type === "tool-call" && p.toolCallId === toolCallId);
    if (pi !== -1) return { mi, pi } as const;
  }
  return null;
}

/** Replace one part inside one message, immutably. */
function updatePart(
  messages: ThreadMessage[],
  mi: number,
  pi: number,
  update: (part: ContentPart) => ContentPart,
) {
  const msg = messages[mi]!;
  const content = [...msg.content];
  content[pi] = update(content[pi]!);
  messages[mi] = withContent(msg, content);
}

const updateTool =
  (patch: Partial<ToolPart> | ((p: ToolPart) => Partial<ToolPart>)) =>
  (part: ContentPart): ContentPart => {
    if (part.type !== "tool-call") return part;
    const change = typeof patch === "function" ? patch(part) : patch;
    return { ...part, ...change };
  };

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

/** The last assistant message carries the turn's live status; earlier ones
 *  are settled history. Explicit status wins over assistant-ui's auto-status,
 *  which is what lets the approval buttons appear while the composer stays
 *  in "the agent is busy" mode. */
function withDerivedStatuses(state: Projection): Projection {
  let li = -1;
  for (let i = state.messages.length - 1; i >= 0; i--) {
    if (state.messages[i]!.role === "assistant") {
      li = i;
      break;
    }
  }
  if (li === -1) return state;
  const last = state.messages[li]!;

  // The live status belongs to the current turn's assistant message only.
  // When a newer user bubble follows it, a new turn has begun and this
  // message is settled history: it must never read as running again,
  // whatever the session is doing now (the new turn's own assistant message
  // will carry the live status once it exists).
  const isCurrentTurn = li === state.messages.length - 1;

  let status: MessageStatus;
  if (!isCurrentTurn) {
    status = { type: "complete", reason: "unknown" };
  } else if (state.pendingApprovals.length > 0 || state.pendingCustomTools.length > 0) {
    status = { type: "requires-action", reason: "tool-calls" };
  } else if (state.isRunning) {
    status = { type: "running" };
  } else if (state.lastError && state.lastError.retryStatus !== "retrying") {
    status = { type: "incomplete", reason: "error", error: state.lastError.message };
  } else {
    status = { type: "complete", reason: "unknown" };
  }

  if (JSON.stringify(last.status ?? null) === JSON.stringify(status)) return state;
  const messages = draft(state.messages);
  messages[li] = { ...last, status };
  return { ...state, messages };
}

// ---------------------------------------------------------------------------
// The fold
// ---------------------------------------------------------------------------

export function applyEvent(state: Projection, event: FeedEvent): Projection {
  // Replay and tail overlap by design (the stream opens before history is
  // fetched, so no gap can appear); dedupe on the persisted event id.
  // Previews carry no id and are handled by their event_id instead.
  if (isPersisted(event)) {
    if (state.seen.has(event.id)) return state;
    state = { ...state, seen: new Set(state.seen).add(event.id) };
  }
  // 运行状态按服务端时间定：推送先到了新的「结束」，补拉晚到的旧「在跑」只记已见，不再把对话标回在回复
  // （去重会把那一步永远定住，重放也改不回来）。终止不论先后都算数。
  if (event.type === "session.status_running" || event.type === "session.status_rescheduled" ||
      event.type === "session.status_idle" || event.type === "session.status_terminated") {
    const at = statusTime(event);
    if (at !== null && at < state.statusAt && event.type !== "session.status_terminated") return state;
    if (at !== null && at > state.statusAt) state = { ...state, statusAt: at };
  }
  return withDerivedStatuses(applyEventInner(state, event));
}

/** 服务端时间（RFC 3339）→ 毫秒；解析不了回 null。小数秒只留三位：引擎给到微秒，有的浏览器多于三位就解析不了。 */
function serverTime(iso: string | null | undefined): number | null {
  const at = Date.parse(String(iso ?? "").replace(/(\.\d{3})\d+/, "$1"));
  return Number.isFinite(at) ? at : null;
}

/** 状态事件的服务端时间（毫秒）；本页合成的（optimistic-）与没有时间的回 null，按到达先后算。 */
function statusTime(event: { id: string; processed_at?: string | null }): number | null {
  return event.id.startsWith("optimistic-") ? null : serverTime(event.processed_at);
}

function applyEventInner(state: Projection, event: FeedEvent): Projection {
  switch (event.type) {
    // -------------------------------------------------------------- notices
    case "pearnly.notice":
      return { ...state, notices: insertNotice(state.notices, event) };

    // ---------------------------------------------------------------- turns
    case "user.message": {
      const messages = draft(state.messages);
      const { text, files } = splitAttachmentNote(textOfBlocks(event.content));
      const bubble: ThreadMessage = {
        id: event.id,
        role: "user",
        content: text ? [{ type: "text", text }] : [],
        createdAt: timeOf(event.processed_at),
        ...(files.length
          ? {
              attachments: files.map((f) => ({
                id: f.mountPath,
                type: IMAGE_EXT.test(f.filename) ? ("image" as const) : ("document" as const),
                name: f.filename,
                contentType: "",
                status: { type: "complete" as const },
                content: [] as [],
              })),
            }
          : {}),
      };
      // The controller shows a sent message immediately as an "optimistic-"
      // bubble; the real echoed event replaces its content but KEEPS the id.
      // assistant-ui keys messages by id: a new id at the same slot reads as
      // a second branch of an edited message (a phantom "2 / 2" picker).
      const last = messages[messages.length - 1];
      let echoedOptimistic = state.echoedOptimistic;
      // Reconcile against the trailing optimistic bubble only when this is its
      // echo: it sits at the end and opens with the same text (the server and
      // client build the text with the same messageText(), so a genuine echo
      // matches exactly). No clock comparison, since client and server
      // clocks disagree; the controller guarantees stored history is fully
      // folded before any optimistic bubble exists, so a historic message
      // can't land here after the bubble.
      const isEcho =
        !!last &&
        last.role === "user" &&
        last.id.startsWith("optimistic-") &&
        (sameOpening(last.content, bubble.content) || sameAttachments(last, bubble));
      if (isEcho) {
        messages[messages.length - 1] = { ...bubble, id: last.id };
        // The echo landed: this bubble is delivered, so a late send-failure
        // rollback must never evict it.
        echoedOptimistic = new Set(state.echoedOptimistic).add(last.id);
      } else {
        messages.push(bubble);
      }
      // A fresh user turn makes any previous error history.
      return { ...state, messages, echoedOptimistic, lastError: null };
    }

    case "session.status_running":
      // The session left the gate (or a new turn began): nothing is parked,
      // so no Allow/Deny can still be live. This also sweeps a gate a failed
      // /confirm may have reopened after the server had in fact resumed.
      // Safe with several gates parked at once: answering only a subset
      // re-emits status_idle{requires_action} with the remainder (which
      // re-stamps those gates) rather than status_running; running fires
      // only after every parked id is answered.
      return {
        ...state,
        messages: stripUnresolvedApprovals(state.messages),
        isRunning: true,
        parkedIds: [],
        pendingApprovals: [],
        lastError: null,
      };

    case "session.status_rescheduled": {
      // Transient error; the session retries on its own. Keep showing "busy".
      // 出错的那次模型请求还没写完的预览不会再有正式事件来收口（重试是新的一条）：清掉，不留半句在回复里。span 先结束时预览已
      // 补「 …」收口、记在 cutPreviews；rescheduled 先到时还在 previews——两处一起清
      if (state.previews.size === 0 && state.cutPreviews.length === 0) return { ...state, isRunning: true };
      const messages = draft(state.messages);
      for (const preview of [...state.previews.values(), ...state.cutPreviews]) {
        updatePart(messages, preview.messageIndex, preview.partIndex, (part) => (part.type === "text" ? { ...part, text: "" } : part));
      }
      return { ...state, messages, previews: new Map(), cutPreviews: [], isRunning: true };
    }

    case "session.status_idle": {
      const stop = event.stop_reason;
      if (stop.type !== "requires_action") {
        // The turn ended (or gave up) with no gate outstanding: any Allow/Deny
        // still on screen was abandoned (interrupted while parked).
        return {
          ...state,
          messages: stripUnresolvedApprovals(state.messages),
          isRunning: false,
          pendingApprovals: [],
          parkedIds: [],
          pendingCustomTools: [],
          cutPreviews: [],
          lastError: clearRetrying(state.lastError),
        };
      }
      // The turn is parked on human input. Each blocked event id is either a
      // tool the user must Allow/Deny, or a custom tool we must answer.
      // 停着等的调用里已经有了答复的（结果先到、已点过允许 / 拒绝）和还没收到的，都说明这一轮没完：算在跑（同 applyRunStatus）。
      const messages = draft(state.messages);
      const pendingApprovals: string[] = [];
      const pendingCustomTools: string[] = [];
      const parkedIds = [...stop.event_ids];
      let working = false;
      for (const id of stop.event_ids) {
        const toolUse = state.toolUses.get(id);
        if (!toolUse || answered(state, id)) {
          working = true;
          continue;
        }
        if (toolUse.type === "agent.custom_tool_use") {
          pendingCustomTools.push(id);
          continue;
        }
        pendingApprovals.push(id);
        // Stamp the gate onto the tool part: assistant-ui renders Allow/Deny
        // from `approval.id`.
        const found = findToolPart(messages, id);
        if (found) updatePart(messages, found.mi, found.pi, updateTool({ approval: { id } }));
      }
      return {
        ...state,
        messages,
        isRunning: working,
        pendingApprovals,
        parkedIds,
        pendingCustomTools,
        // Parking on a gate proves a mid-turn "retrying" recovered too.
        lastError: clearRetrying(state.lastError),
      };
    }

    case "session.status_terminated":
    case "session.deleted":
      return {
        ...state,
        messages: stripUnresolvedApprovals(state.messages),
        isRunning: false,
        isDead: true,
        pendingApprovals: [],
        parkedIds: [],
        pendingCustomTools: [],
        cutPreviews: [],
      };

    // ------------------------------------------------------------- thinking
    // agent.thinking carries no reasoning text in the current API: it is a
    // "started working" signal. The part needs SOME text — assistant-ui's
    // fromThreadMessageLike drops text/reasoning parts whose text is blank —
    // so it carries a fixed placeholder label rather than invented content.
    case "event_start": {
      // A preview for an event whose PERSISTED form already folded (replay
      // finished after the event completed) must not open a second part —
      // the persisted copy dedupes on `seen`, so nothing would ever
      // reconcile the duplicate away.
      if (state.seen.has(event.event.id)) return state;
      // 同一条的预览又从头来了一遍（推送线程断了重接，事件流把还没写完的这一条从头再推）：接着用原来那一段、清空重写，
      // 不另开一段——否则先到的几个字留在回复里，没有正式事件来收口（第 8 轮测试机：拒绝后的回复里多出一段只有「การ」）
      const open = state.previews.get(event.event.id);
      if (open) {
        if (event.event.type === "agent.thinking") return state;
        const restarted = draft(state.messages);
        updatePart(restarted, open.messageIndex, open.partIndex, (part) => (part.type === "text" ? { ...part, text: "" } : part));
        return { ...state, messages: restarted };
      }
      const messages = draft(state.messages);
      const mi = currentAssistantIndex(messages);
      const msg = messages[mi]!;
      if (event.event.type === "agent.thinking") {
        // Register the part as a preview: the buffered agent.thinking arrives
        // with the same id and reconciles into it (no second part, and it
        // backfills the timestamp this start event doesn't carry).
        const content: ContentPart[] = [...msg.content, { type: "reasoning", text: THINKING_LABEL }];
        messages[mi] = withContent(msg, content);
        const previews = new Map(state.previews).set(event.event.id, {
          messageIndex: mi,
          partIndex: content.length - 1,
        });
        return { ...state, messages, previews };
      }
      // agent.message preview: open an empty text part the deltas fill.
      const content: ContentPart[] = [...msg.content, { type: "text", text: "" }];
      messages[mi] = withContent(msg, content);
      const previews = new Map(state.previews).set(event.event.id, {
        messageIndex: mi,
        partIndex: content.length - 1,
      });
      return { ...state, messages, previews };
    }

    case "agent.thinking": {
      // Previewed by event_start: the part exists; just settle the preview
      // and backfill the real timestamp the start event couldn't know.
      const preview = state.previews.get(event.id);
      if (preview) {
        const previews = new Map(state.previews);
        previews.delete(event.id);
        const messages = draft(state.messages);
        const msg = messages[preview.messageIndex]!;
        if (msg.createdAt.getTime() === UNKNOWN_TIME.getTime()) {
          messages[preview.messageIndex] = { ...msg, createdAt: timeOf(event.processed_at) };
        }
        return { ...state, messages, previews };
      }
      const messages = draft(state.messages);
      const mi = currentAssistantIndex(messages, event.processed_at);
      const msg = messages[mi]!;
      messages[mi] = withContent(msg, [...msg.content, { type: "reasoning", text: THINKING_LABEL }]);
      return { ...state, messages };
    }

    // -------------------------------------------------------- token stream
    case "event_delta": {
      const preview = state.previews.get(event.event_id);
      if (!preview) return state; // a delta whose start we never saw
      const delta = event.delta;
      if (delta.type !== "content_delta" || delta.content.type !== "text") return state;
      const messages = draft(state.messages);
      updatePart(messages, preview.messageIndex, preview.partIndex, (part) =>
        part.type === "text" ? { ...part, text: part.text + delta.content.text } : part,
      );
      return { ...state, messages };
    }

    case "agent.message": {
      // The buffered message is authoritative. If a preview streamed a prefix
      // of it, reconcile in place (a preview is always a verbatim prefix);
      // otherwise append it whole (no streaming gate, or a missed start).
      const messages = draft(state.messages);
      const text = textOfBlocks(event.content);
      const preview = state.previews.get(event.id);
      const previews = new Map(state.previews);
      if (preview) {
        previews.delete(event.id);
        updatePart(messages, preview.messageIndex, preview.partIndex, (part) =>
          part.type === "text" ? { ...part, text } : part,
        );
        // The preview opened the message without a timestamp; the buffered
        // event has the real one, and replay will use it too.
        const msg = messages[preview.messageIndex]!;
        if (msg.createdAt.getTime() === UNKNOWN_TIME.getTime()) {
          messages[preview.messageIndex] = { ...msg, createdAt: timeOf(event.processed_at) };
        }
        // Output proves a mid-turn "retrying" recovered (no status_running
        // re-fires for an in-place recovery).
        return { ...state, messages, previews, lastError: clearRetrying(state.lastError) };
      }
      const mi = currentAssistantIndex(messages, event.processed_at);
      const msg = messages[mi]!;
      messages[mi] = withContent(msg, [...msg.content, { type: "text", text }]);
      return { ...state, messages, previews, lastError: clearRetrying(state.lastError) };
    }

    case "span.model_request_end": {
      // An errored model request never emits its buffered agent.message.
      // Close any preview it left open so it doesn't hang half-written.
      if (!event.is_error || state.previews.size === 0) return state;
      const messages = draft(state.messages);
      for (const [, preview] of state.previews) {
        updatePart(messages, preview.messageIndex, preview.partIndex, (part) =>
          part.type === "text" ? { ...part, text: `${part.text} …` } : part,
        );
      }
      return { ...state, messages, previews: new Map(), cutPreviews: [...state.cutPreviews, ...state.previews.values()] };
    }

    // ------------------------------------------------------------- tools
    case "agent.tool_use":
    case "agent.mcp_tool_use":
    case "agent.custom_tool_use": {
      const messages = draft(state.messages);
      const mi = currentAssistantIndex(messages, event.processed_at);
      const msg = messages[mi]!;
      const args = (event.input && typeof event.input === "object" ? event.input : {}) as Record<
        string,
        unknown
      >;
      const part: ToolPart = {
        type: "tool-call",
        toolCallId: event.id,
        toolName: event.name,
        toolKind:
          event.type === "agent.tool_use" ? "builtin" : event.type === "agent.mcp_tool_use" ? "mcp" : "custom",
        ...(event.type === "agent.mcp_tool_use" ? { mcpServer: event.mcp_server_name } : {}),
        args,
        argsText: JSON.stringify(args, null, 2),
        ...(state.toolRuns.has(event.id) ? { run: state.toolRuns.get(event.id)! } : {}),
      };
      messages[mi] = withContent(msg, [...msg.content, part]);
      const toolUses = new Map(state.toolUses).set(event.id, event);
      return { ...state, messages, toolUses };
    }

    case "agent.tool_result":
    case "agent.mcp_tool_result": {
      const toolCallId = event.type === "agent.tool_result" ? event.tool_use_id : event.mcp_tool_use_id;
      const found = findToolPart(state.messages, toolCallId);
      if (!found) return state;
      const result = toToolResult(event.content, !!event.is_error);
      const target = state.messages[found.mi]!.content[found.pi]!;
      if (event.type === "agent.tool_result" && target.type === "tool-call" && target.toolKind === "custom") {
        return interruptedCustomTool(state, toolCallId, result);
      }
      const messages = draft(state.messages);
      updatePart(messages, found.mi, found.pi, updateTool((p) => ({
        result,
        isError: result.isError,
        // A result arriving proves the gate was allowed, even if the echoed
        // confirmation raced past us. 已答的只去掉「发送中」（自带工具被拒时没有结果）。
        approval: p.approval && p.approval.approved === undefined ? settleApproval(p.approval, true)
          : p.approval?.sending ? settleApproval(p.approval, p.approval.approved === true, p.approval.id, p.approval.reason) : p.approval,
      })));
      return { ...state, messages };
    }

    case "user.custom_tool_result": {
      // Our own answer to a custom tool, echoed back (or replayed). Mark it
      // done so a reattach never answers the same call twice.
      const id = event.custom_tool_use_id;
      const answeredCustomTools = new Set(state.answeredCustomTools).add(id);
      let messages: readonly ThreadMessage[] = state.messages;
      const found = findToolPart(messages, id);
      if (found) {
        const next = draft(messages);
        const result = toToolResult(event.content, !!event.is_error);
        // 还挂着「允许 / 拒绝」却已有结果：别处答过了（别的设备点的，服务端执行完交回）。按结果定下所选那一项，这边按钮立即消失。
        // 本页已点过的也以结果为准（approvalFromResult）
        updatePart(next, found.mi, found.pi, updateTool((p) => ({
          result,
          // 本站的结果取代平台代发的中断结果
          ...(p.interrupted ? { isError: result.isError, interrupted: false } : {}),
          ...(p.approval && !runSettled(p.run) ? { approval: approvalFromResult(p.approval, result) } : {}),
        })));
        messages = next;
      }
      const pendingCustomTools = state.pendingCustomTools.filter((x) => x !== id);
      // 别处答过的卡片也不再等这边点
      const pendingApprovals = state.pendingApprovals.filter((x) => x !== id);
      return {
        ...state,
        messages,
        answeredCustomTools,
        pendingCustomTools,
        pendingApprovals,
        isRunning: state.isRunning || resumes(state, id, pendingApprovals, pendingCustomTools),
      };
    }

    case "user.tool_confirmation":
      // Our Allow/Deny, echoed (live) or replayed (history). Settle the gate.
      // A persisted (non-optimistic) confirmation is the server's own
      // record of the answer: from here on it can never be reopened.
      return settleConfirmation(state, event.tool_use_id, event.result === "allow", event.deny_message ?? undefined,
        event.id.startsWith("optimistic-confirm-"));

    case "pearnly.answer":
      // 本站拦下的调用，回答已落库（toolrun.decide 推来）：同一条已送到的确认，别的设备上点的也立即定下
      return settleConfirmation(state, event.tool_use_id, event.result === "allow", undefined, false);

    case "pearnly.tool_run":
      return applyToolRun(state, event.tool_use_id, event.status);

    case "user.interrupt":
      return cancelOpenGates(state);

    // ------------------------------------------------------------- errors
    case "session.error": {
      const err = event.error as { message?: string; retry_status?: { type?: string } };
      const retryStatus = (err.retry_status?.type ?? "terminal") as ProjectionError["retryStatus"];
      const lastError = { message: err.message ?? "The session hit an error.", retryStatus };
      // A terminal error can land before the agent said anything this turn.
      // Make sure there's an assistant message for the error to sit on.
      if (retryStatus === "retrying") return { ...state, lastError };
      const messages = draft(state.messages);
      currentAssistantIndex(messages, event.processed_at);
      return { ...state, messages, lastError };
    }

    default:
      // Span, thread (multi-agent), outcome, compaction and update events
      // are progress signals this UI doesn't render. Ignore, don't fail.
      return state;
  }
}

/** 停着等的这次调用已经有了答复：结果已到（我们的或服务端的）、已点过允许 / 拒绝。 */
function answered(state: Projection, id: string): boolean {
  if (state.answeredCustomTools.has(id) || state.confirmedIds.has(id)) return true;
  const found = findToolPart(state.messages, id);
  const part = found ? state.messages[found.mi]!.content[found.pi] : undefined;
  // 本站已在执行 / 已定下的（执行记录先于引擎事件到）也算答过：不再提醒服务端执行一次
  return part?.type === "tool-call" &&
    (part.result !== undefined || part.approval?.approved !== undefined || (part.run !== undefined && RUN_RANK[part.run] > 0));
}

/** 一张卡有了回答（确认的回声或重放、本站推来的回答、本页乐观点下的）：定下所选那一项，不再等这边点。 */
function settleConfirmation(state: Projection, id: string, allow: boolean, denyMessage: string | undefined, optimistic: boolean): Projection {
  const confirmedIds = optimistic ? state.confirmedIds : new Set(state.confirmedIds).add(id);
  let messages: readonly ThreadMessage[] = state.messages;
  const found = findToolPart(messages, id);
  if (found) {
    const next = draft(messages);
    // 已有结果的（结果先到）以结果为准，回声不再改它；本页乐观点下的先标「发送中」
    // 本站执行记录已定下的（执行完、拒绝、作废）也不再改
    updatePart(next, found.mi, found.pi, updateTool((p) => ({
      approval: (p.result !== undefined && p.approval?.approved !== undefined) || runSettled(p.run)
        ? p.approval
        : settleApproval(p.approval, allow, id, denyMessage, optimistic && p.result === undefined),
    })));
    messages = next;
  }
  const pendingApprovals = state.pendingApprovals.filter((x) => x !== id);
  return {
    ...state,
    messages,
    confirmedIds,
    pendingApprovals,
    isRunning: state.isRunning || resumes(state, id, pendingApprovals, state.pendingCustomTools),
  };
}

/** 点了停止（user.interrupt）：还开着、没有结果的门撤掉并标 cancelled，卡片写「已取消」；中断后引擎补来的结果不再把它
 *  定成「允许」（第 10 轮 C20：没点就停止，卡片写成「允许 · 失败」，重开一样）。重开对话时本站拦下的那一步还没有门
 *  （问句要向服务端要，gateCustomTool），按服务端此刻停着等的（parkedIds）认。已答的、已有结果的不动；不是卡片的那一步
 *  标了也照常是一行（lib/approval-card.ts 先看要不要「允许」）。 */
function cancelOpenGates(state: Projection): Projection {
  let changed = false;
  const cancelled = new Set<string>();
  const parked = new Set(state.parkedIds);
  const messages = state.messages.map((msg) => {
    if (msg.role !== "assistant") return msg;
    let msgChanged = false;
    const content = msg.content.map((part) => {
      if (part.type !== "tool-call" || part.result !== undefined || part.approval?.approved !== undefined) return part;
      if (part.run && RUN_RANK[part.run] > 0) return part; // 本站已在执行 / 已定下：照执行记录
      if (!part.approval && !parked.has(part.toolCallId)) return part;
      msgChanged = changed = true;
      cancelled.add(part.toolCallId);
      const { approval: _approval, ...rest } = part;
      return { ...rest, cancelled: true };
    });
    return msgChanged ? { ...msg, content } : msg;
  });
  if (!changed) return state;
  return {
    ...state,
    messages,
    pendingApprovals: state.pendingApprovals.filter((id) => !cancelled.has(id)),
    pendingCustomTools: state.pendingCustomTools.filter((id) => !cancelled.has(id)),
    // 服务端不再停在这几步：随后补来的结果不算「答完接着跑」（resumes），确认没送到也不放回（restoreGates）
    parkedIds: state.parkedIds.filter((id) => !cancelled.has(id)),
  };
}

/** 本站执行记录各状态的先后：只往前走（推来的新状态之后，晚到的、读得早的旧状态不再把它改回去）。 */
const RUN_RANK: Readonly<Record<ToolRunStatus, number>> = {
  awaiting_approval: 0, running: 1, done: 2, error: 2, denied: 2, cancelled: 2,
};

/** 本站已定下这次调用（执行完、出错、拒绝、作废）。 */
const runSettled = (run: ToolRunStatus | undefined): boolean => run !== undefined && RUN_RANK[run] === 2;

/** 一张卡（本站工具）换成别的样子时不再算等这边点、也不再欠结果。 */
function dropPending(state: Projection, id: string): Pick<Projection, "pendingApprovals" | "pendingCustomTools"> {
  return {
    pendingApprovals: state.pendingApprovals.filter((x) => x !== id),
    pendingCustomTools: state.pendingCustomTools.filter((x) => x !== id),
  };
}

/** 本站执行记录到了（pearnly.tool_run，或点「允许 / 拒绝」时 confirm 回的状态）：卡片照它定下。允许过（running / done / error）
 *  定成「允许」，拒绝定成「拒绝」，作废撤掉门、标 cancelled；还在等的只记下。状态只往前走（RUN_RANK）。还没有这一步时先记在 toolRuns，
 *  建这一步时补上。 */
function applyToolRun(state: Projection, id: string, status: ToolRunStatus): Projection {
  const known = state.toolRuns.get(id);
  if (known && RUN_RANK[known] > RUN_RANK[status]) return state;
  const toolRuns = new Map(state.toolRuns).set(id, status);
  const found = findToolPart(state.messages, id);
  const part = found ? state.messages[found.mi]!.content[found.pi] : undefined;
  if (!found || part?.type !== "tool-call") return { ...state, toolRuns };
  if (part.run && RUN_RANK[part.run] > RUN_RANK[status]) return { ...state, toolRuns };
  const messages = draft(state.messages);
  updatePart(messages, found.mi, found.pi, () => {
    const next: ToolPart = { ...part, run: status };
    if (status === "cancelled") {
      delete next.approval;
      next.cancelled = true;
    } else if (status === "denied") {
      if (part.approval) next.approval = settleApproval(part.approval, false, id, part.approval.reason);
    } else if (status !== "awaiting_approval" && part.approval &&
               (part.approval.approved !== true || part.approval.sending)) {
      next.approval = settleApproval(part.approval, true);
    }
    return next;
  });
  if (status === "awaiting_approval") return { ...state, messages, toolRuns };
  const pending = dropPending(state, id);
  if (status === "cancelled") {
    // 引擎不再停在这一步（这一轮已被停止）：随后补来的结果不算「答完接着跑」，确认没送到也不放回
    return { ...state, messages, toolRuns, ...pending, parkedIds: state.parkedIds.filter((x) => x !== id) };
  }
  // 允许 / 拒绝过：本站交回结果后引擎接着跑（只在服务端还停在这一步、其余也都答完时，同 user.custom_tool_result）
  return {
    ...state, messages, toolRuns, ...pending,
    isRunning: state.isRunning || resumes(state, id, pending.pendingApprovals, pending.pendingCustomTools),
  };
}

/** 自定义工具收到 agent.tool_result：只能是点了停止时引擎代发的中断结果（本站的结果走 user.custom_tool_result）。结果留着给
 *  步骤行看（标 interrupted），但不算点了「允许」：还没答的门撤掉、标 cancelled（「已取消」）；已点过的、本站已在执行 / 已定下的
 *  照旧，卡片等执行记录。本站的结果已先到的不覆盖。引擎不再停在这一步。 */
function interruptedCustomTool(state: Projection, id: string, result: ToolResultPayload): Projection {
  const found = findToolPart(state.messages, id)!;
  const part = state.messages[found.mi]!.content[found.pi] as ToolPart;
  if (part.result !== undefined && !part.interrupted) return state;
  const messages = draft(state.messages);
  updatePart(messages, found.mi, found.pi, () => {
    const next: ToolPart = { ...part, result, isError: result.isError, interrupted: true };
    const answered = part.approval?.approved !== undefined || (part.run !== undefined && RUN_RANK[part.run] > 0);
    if (!answered) {
      delete next.approval;
      next.cancelled = true;
    }
    return next;
  });
  return { ...state, messages, ...dropPending(state, id), parkedIds: state.parkedIds.filter((x) => x !== id) };
}

/** 答完 `id` 之后引擎会接着跑：服务端此刻正停在它上面，而停着等的其余调用也都答完了（没有还要用户点的门、还欠着的结果）。
 *  只看 parkedIds：这一轮已经往下走了或已结束时晚到的答复不会把它标回在跑（2026-10-13 iPhone）。 */
function resumes(
  state: Projection,
  id: string,
  pendingApprovals: readonly string[],
  pendingCustomTools: readonly string[],
): boolean {
  return state.parkedIds.includes(id) && pendingApprovals.length === 0 && pendingCustomTools.length === 0;
}

/** Fold a whole ordered event list. */
export function project(events: Iterable<FeedEvent>, from = initialProjection()): Projection {
  let state = from;
  for (const event of events) state = applyEvent(state, event);
  return state;
}

/** The turn is not something the user can type over: agent working, or a
 *  gate waiting for a click, or a custom tool we still owe. */
export const isBusy = (state: Pick<Projection, "isRunning" | "pendingApprovals" | "pendingCustomTools">): boolean =>
  state.isRunning || state.pendingApprovals.length > 0 || state.pendingCustomTools.length > 0;

/** The user just answered the last open gate: the session is about to
 *  resume server-side. Mark it running now so the composer doesn't briefly
 *  reopen (and accept a message mid-resume) before the real status_running
 *  arrives. Deliberately NOT a folded status_running event: that would clear
 *  parkedIds, which restoreGates needs intact if the confirmation POST
 *  fails. Only while the session is still parked on `id`: once a later status
 *  folded (the turn moved on, or already ended), nothing would ever clear the
 *  flag again (2026-10-13 iPhone: a late tool reply re-marked a finished turn). */
export function markResuming(state: Projection, id: string): Projection {
  if (!state.parkedIds.includes(id)) return state; // the server already left this gate
  if (isBusy(state)) return state; // gates still open, or already running
  return withDerivedStatuses({ ...state, isRunning: true });
}

/** 服务端读回的此刻运行状态（RunStatus）：在回复 / 等允许 / 已结束都以它为准，清掉本页乐观标的「在回复」、补不回的旧
 *  门（漏了事件、去重定住的那些）。as_of 比已采用的状态事件旧就不用（这期间到的状态事件更新）。
 *  requires_action：停着等的调用里，自带工具与本站拦下等「允许」的显示批准按钮；结果还欠着的照旧由页面提醒服务端执行；
 *  本站正在执行的、我们这边已答过的、还没收到的都算在忙。 */
export function applyRunStatus(state: Projection, run: RunStatus): Projection {
  const at = serverTime(run.as_of) ?? 0;
  if (state.statusAt > at) return state;
  const statusAt = Math.max(state.statusAt, at);
  // 不停在任何一步：没有还能点的门、没有欠着的工具结果
  const settled = {
    messages: stripUnresolvedApprovals(state.messages),
    pendingApprovals: [] as string[],
    parkedIds: [] as string[],
    pendingCustomTools: [] as string[],
    statusAt,
  };
  switch (run.status) {
    case "terminated":
      return withDerivedStatuses({ ...state, ...settled, isRunning: false, isDead: true });
    case "running":
      return withDerivedStatuses({ ...state, ...settled, isRunning: true });
    case "idle":
      return withDerivedStatuses({ ...state, ...settled, isRunning: false, lastError: clearRetrying(state.lastError) });
    case "requires_action":
      break;
    default:
      return state;
  }
  const parked = [...(run.parked ?? [])];
  const awaiting = new Set(run.awaiting_approval ?? []);
  const executing = new Set(run.executing ?? []);
  const isCustom = (id: string) => state.toolUses.get(id)?.type === "agent.custom_tool_use";
  // 还能点的门只留服务端真在等用户的：自带工具的确认、本站拦下等「允许」的 Pearnly 工具
  const gates = new Set(parked.filter((id) => !isCustom(id) || awaiting.has(id)));
  // 卡片还在等、本站却已在执行：别的设备上点了「允许」（结果还没回）。定下「允许」，这边按钮立即消失、卡片留着点完的样子
  const answeredElsewhere = draft(state.messages);
  for (const id of parked) {
    const found = isCustom(id) && executing.has(id) ? findToolPart(answeredElsewhere, id) : null;
    const part = found ? answeredElsewhere[found.mi]!.content[found.pi] : undefined;
    if (!found || part?.type !== "tool-call" || !part.approval || part.approval.approved !== undefined) continue;
    updatePart(answeredElsewhere, found.mi, found.pi, updateTool((p) => ({ approval: settleApproval(p.approval, true) })));
  }
  const messages = draft(stripUnresolvedApprovals(answeredElsewhere, gates));
  const pendingApprovals: string[] = [];
  const pendingCustomTools: string[] = [];
  let working = false;
  for (const id of parked) {
    const found = findToolPart(messages, id);
    const part = found ? messages[found.mi]!.content[found.pi] : undefined;
    if (!found || part?.type !== "tool-call" || !state.toolUses.has(id)) {
      working = true; // 服务端停在一次我们还没收到的调用上：这一轮没完
      continue;
    }
    if (part.result !== undefined || part.approval?.approved !== undefined || state.answeredCustomTools.has(id) ||
        executing.has(id)) {
      working = true; // 已答过正在交回，或本站正在执行
      continue;
    }
    if (isCustom(id)) {
      // 等「允许」且卡片已有问句的照旧显示；结果还欠着、或等「允许」但卡片还没拿到问句与改动的，由页面提醒服务端一次
      // （等「允许」的回问句与改动，见 gateCustomTool）
      if (awaiting.has(id) && part.approval) pendingApprovals.push(id);
      else pendingCustomTools.push(id);
      continue;
    }
    pendingApprovals.push(id);
    updatePart(messages, found.mi, found.pi, updateTool((p) => (p.approval ? {} : { approval: { id } })));
  }
  return withDerivedStatuses({
    ...state,
    messages,
    isRunning: working,
    pendingApprovals,
    parkedIds: parked,
    pendingCustomTools,
    lastError: clearRetrying(state.lastError),
    statusAt,
  });
}

// ---------------------------------------------------------------------------
// Recovery operations (used by the controller when an optimistic action
// fails). These are not events: they undo local optimism so the UI converges
// on what the server actually did, which a dedupe-by-id replay cannot.
// ---------------------------------------------------------------------------

/** A confirmation POST failed. Put back only the gates the server is still
 *  actually parked on: `parkedIds` is untouched by our optimistic click, so
 *  it tells us whether the session left the gate meanwhile (an interrupt,
 *  or an answer that landed via the tail even though our fetch errored). A
 *  part that already has a verdict or a result is left alone. If nothing
 *  qualifies, this is a no-op and the settled state stands. 卡片照原样回来（问话、改动不丢，第 9 轮测试：拦住确认请求后
 *  卡片变成做完的说法「记下了公司选择」、按钮还挂着），带上 `failure`：按钮上方写一行原因，再点重试。 */
export function restoreGates(state: Projection, ids: readonly string[], failure?: string): Projection {
  const restorable = ids.filter((id) => state.parkedIds.includes(id));
  if (restorable.length === 0) return state;
  const messages = draft(state.messages);
  const pendingApprovals = [...state.pendingApprovals];
  let changed = false;
  for (const id of restorable) {
    // A REAL confirmation already settled this on the server (its echo folded
    // before our fetch errored): the answer landed, so don't reopen it.
    if (state.confirmedIds.has(id)) continue;
    const found = findToolPart(messages, id);
    if (!found) continue;
    const part = messages[found.mi]!.content[found.pi]!;
    // Likewise if the tool already ran (result present), or this site's record moved past waiting / the gate was cancelled.
    if (part.type !== "tool-call" || part.result !== undefined || part.cancelled || (part.run && RUN_RANK[part.run] > 0)) continue;
    updatePart(messages, found.mi, found.pi, updateTool((p) => {
      const { approved: _approved, reason: _reason, failure: _failure, sending: _sending, ...kept } = p.approval ?? { id };
      return { approval: { ...kept, id, ...(failure ? { failure } : {}) } };
    }));
    if (!pendingApprovals.includes(id)) pendingApprovals.push(id);
    changed = true;
  }
  if (!changed) return state;
  // Gates restored means the server is still parked: undo any optimistic
  // "resuming" flag markResuming set when the click looked final.
  return withDerivedStatuses({ ...state, messages, pendingApprovals, isRunning: false });
}

/** 确认请求有了下文（送到了；或没送到、门已照原样放回 / 服务端早已不在等）：这几张卡不再是「发送中」，点完那一行照常显示。 */
export function clearSending(state: Projection, ids: readonly string[]): Projection {
  let messages: ThreadMessage[] | null = null;
  for (const id of ids) {
    const found = findToolPart(messages ?? state.messages, id);
    const part = found ? (messages ?? state.messages)[found.mi]!.content[found.pi] : undefined;
    if (!found || part?.type !== "tool-call" || !part.approval?.sending) continue;
    messages ??= draft(state.messages);
    updatePart(messages, found.mi, found.pi, updateTool((p) => {
      const { sending: _sending, ...kept } = p.approval!;
      return { approval: kept };
    }));
  }
  return messages ? { ...state, messages } : state;
}

/** The server held a Pearnly tool call for the user's Allow/Deny (a tool
 *  that changes company-wide settings; pearnly_core.assistant.toolrun). Turn
 *  the owed custom tool into an approval gate on its part: the click goes to
 *  /confirm, which the server answers by running or refusing the call. */
export function gateCustomTool(
  state: Projection,
  id: string,
  view: { ask?: string; preview?: ApprovalChange[] } = {},
): Projection {
  const found = findToolPart(state.messages, id);
  if (!found) return state;
  const part = state.messages[found.mi]!.content[found.pi]!;
  // 已点了停止（cancelOpenGates）：晚到的问句不再把按钮放回来
  if (part.type !== "tool-call" || part.result !== undefined || part.cancelled || (part.run && RUN_RANK[part.run] > 0)) return state;
  if (part.approval && part.approval.approved !== undefined) return state;
  const messages = draft(state.messages);
  updatePart(messages, found.mi, found.pi, updateTool({ approval: { id, ask: view.ask, preview: view.preview } }));
  return withDerivedStatuses({
    ...state,
    messages,
    pendingApprovals: state.pendingApprovals.includes(id) ? state.pendingApprovals : [...state.pendingApprovals, id],
    pendingCustomTools: state.pendingCustomTools.filter((x) => x !== id),
    isRunning: false,
  });
}

/** A message POST failed. If the bubble was never echoed, it wasn't sent and
 *  must not sit in the transcript reading as delivered: evict it (and the
 *  optimistic "running" it started). If the echo already landed, the message
 *  WAS delivered (the failure was only on our side of the response), so
 *  keep it. */
export function evictOptimisticMessage(state: Projection, optimisticId: string): Projection {
  if (state.echoedOptimistic.has(optimisticId)) return state;
  const idx = state.messages.findIndex((m) => m.id === optimisticId);
  if (idx === -1) return { ...state, isRunning: false };
  const messages = draft(state.messages);
  messages.splice(idx, 1);
  return withDerivedStatuses({ ...state, messages, isRunning: false });
}

// ---------------------------------------------------------------------------
// Notices（本站提醒）：记在 Projection.notices，页面显示前才并进对话。
// ---------------------------------------------------------------------------

/** 按时间插入（同一时刻排在后面）。 */
function insertNotice(notices: readonly NoticeItem[], event: NoticeEvent): readonly NoticeItem[] {
  const item: NoticeItem = {
    id: event.id,
    createdAt: timeOf(event.processed_at),
    kind: event.kind ?? "",
    title: event.title ?? "",
    text: event.text ?? "",
  };
  const at = item.createdAt.getTime();
  const index = notices.findIndex((n) => n.createdAt.getTime() > at);
  if (index < 0) return [...notices, item];
  return [...notices.slice(0, index), item, ...notices.slice(index)];
}

// 同一条提醒每次给出同一个消息对象：组件库按对象缓存转换结果，换了对象就要重算、重画。
const noticeMessages = new WeakMap<NoticeItem, NoticeMessage>();

function noticeMessage(notice: NoticeItem): NoticeMessage {
  let message = noticeMessages.get(notice);
  if (!message) {
    message = {
      id: notice.id,
      role: "system",
      content: [{ type: "text", text: notice.text }],
      createdAt: notice.createdAt,
      metadata: { custom: { notice: { kind: notice.kind, title: notice.title } } },
    };
    noticeMessages.set(notice, message);
  }
  return message;
}

/** 回复正在进行（最后一条是本轮的助手消息，状态是进行中或等批准）。 */
function replyInProgress(message: ThreadMessage | undefined): boolean {
  const type = message?.role === "assistant" ? message.status?.type : undefined;
  return type === "running" || type === "requires-action";
}

/** 提醒卡片并进对话：每条插在第一条比它晚的用户消息之前，没有就放最后。只插在用户消息前，不切开一轮回复；
 *  刷新后位置不变（同一规则）。卡片是带 metadata.custom.notice 的 system 消息（NoticeMessage）。
 *  回复正在进行时，本该放最后的提醒先放在这条回复前面：进行中的回复要一直是最后一条，否则组件库在卡片后面再补一个
 *  「思考中」占位（回复里已经有一个）；跑完回到最后。回复还没开始时卡片照样放最后，组件库在它后面补「思考中」占位。 */
export function withNotices(
  messages: readonly ThreadMessage[],
  notices: readonly NoticeItem[],
): readonly (ThreadMessage | NoticeMessage)[] {
  if (!notices.length) return messages;
  const out: (ThreadMessage | NoticeMessage)[] = [];
  const live = replyInProgress(messages.at(-1)) ? messages.length - 1 : -1;
  let next = 0;
  messages.forEach((message, index) => {
    if (message.role === "user") {
      const at = message.createdAt.getTime();
      while (next < notices.length && notices[next]!.createdAt.getTime() < at) out.push(noticeMessage(notices[next++]!));
    }
    if (index === live) while (next < notices.length) out.push(noticeMessage(notices[next++]!));
    out.push(message);
  });
  while (next < notices.length) out.push(noticeMessage(notices[next++]!));
  return out;
}
