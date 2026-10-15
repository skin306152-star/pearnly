// 工作状态行（同 Claude 的工作指示，教程第 6、8 条）：用户一发出，对话底部就出现一行「橘色动画点 + 计时 · 在做什么」，
// 一直在，直到这一轮真正结束；等用户点「允许」时轮到用户，这一行收起（卡片照常显示），点完再出来。
//
// 这里只按会话状态算「此刻在做什么」，不碰 React、不读时钟（计时与「思考了几秒」在组件里按本页时钟算，同一份状态重放出来
// 的结果不变）：
//   - 还没有回复、刚交回一步工具的结果、只有思考 → 思考中
//   - 一步工具在执行（还没有结果、不是在等「允许」）→ 这一步的进行时名称
//   - 正文在出 → 「思考了 M 秒」
//   - 运行中却很久收不到服务端任何消息（session-controller 的 stalled）→ 连接中断，正在重连
//
// 「这一轮没结束」与输入框、复制按钮用的是同一个判断（reducer.isBusy）：引擎空闲但停着等的调用还没答完、本站正在执行、
// 两段工具之间的交接空档都算在跑（见 reducer 文件头）。

import { steps } from "@/i18n";
import { isBusy, type Projection, type ThreadMessage } from "./reducer";

export type TurnActivity =
  | { kind: "hidden" }
  | { kind: "thinking" }
  | { kind: "writing" }
  | { kind: "tool"; toolName: string; args: Record<string, unknown> }
  | { kind: "reconnecting" };

type TurnState = Pick<Projection, "messages" | "isRunning" | "pendingApprovals" | "pendingCustomTools"> & {
  /** 运行中很久收不到服务端任何消息（session-controller）。 */
  stalled?: boolean;
};

// 乐观气泡的时间与按下发送相差不超过这么多才算同一次发送（新对话先建会话约 1 秒，回显后是服务端时间、两边时钟有偏差）
const SAME_SEND_MS = 60_000;

const HIDDEN: TurnActivity = { kind: "hidden" };
const THINKING: TurnActivity = { kind: "thinking" };

/** 此刻状态行显示什么。sending：按下发送、正式气泡还没画出来（新对话建会话、附件还在上传，pending-send.ts）。 */
export function turnActivity(state: TurnState, sending = false): TurnActivity {
  if (!sending && !isBusy(state)) return HIDDEN;
  // 轮到用户：卡片在对话里等点，状态行收起
  if (!sending && state.pendingApprovals.length > 0) return HIDDEN;
  if (state.stalled) return { kind: "reconnecting" };
  const last = state.messages.at(-1);
  if (sending || !last || last.role !== "assistant") return THINKING;
  return partActivity(last);
}

function partActivity(message: ThreadMessage): TurnActivity {
  for (let i = message.content.length - 1; i >= 0; i--) {
    const part = message.content[i]!;
    if (part.type === "text") {
      if (!part.text.trim()) continue; // 逐字预览刚开了个空段：字还没到
      return { kind: "writing" };
    }
    if (part.type === "reasoning") return THINKING;
    // 有结果、或被拒绝了的一步：引擎在看结果，接着想
    if (part.result !== undefined || part.approval?.approved === false) return THINKING;
    return { kind: "tool", toolName: part.toolName, args: part.args };
  }
  return THINKING;
}

/** 这一轮的计时起点（毫秒）。本页发出的（乐观气泡、还在发送）从按下发送那一刻算；别处发出的、刷新后接着看的从服务端记下
 *  这条消息的时间算（不晚于此刻）。没有用户消息时回 null。 */
export function turnStartedAt(
  messages: readonly ThreadMessage[],
  sentAt: number,
  sending: boolean,
  now: number,
): number | null {
  if (sending) return sentAt || now;
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i]!;
    if (message.role !== "user") continue;
    const at = message.createdAt.getTime();
    // 本页发出的这条（乐观气泡的编号在回显后也保留）：用按下发送的那一刻，回显换成服务端时间后计时也不跳。
    // 隔得远的不是这一次发送（例如切到另一段还在跑的对话）。
    if (message.id.startsWith("optimistic-") && sentAt && Math.abs(at - sentAt) < SAME_SEND_MS) return sentAt;
    return Math.min(at, now);
  }
  return null;
}

/** 状态行的字：「计时 · 在做什么」。seconds：这一轮已用几秒；thought：最近一段想了几秒（0 = 没看到它想，例如刷新后正文已在出，
 *  就只走计时）；step：执行中那一步的进行时名称。断了不带计时：计时还在走会让人以为在跑。 */
export function statusText(activity: TurnActivity, seconds: number, thought: number, step: string): string {
  const elapsed = steps.elapsed(Math.max(0, seconds));
  switch (activity.kind) {
    case "hidden":
      return "";
    case "reconnecting":
      return steps.connectionLost;
    case "tool":
      return `${elapsed} · ${step}`;
    case "writing":
      return thought > 0 ? `${elapsed} · ${steps.thoughtFor(steps.elapsed(thought))}` : elapsed;
    default:
      return `${elapsed} · ${steps.thinkingNow}…`;
  }
}

/** 最近一段「思考中」用了几秒（本页时钟）：这一轮开始、或一步工具交回结果后开始想，到开始出正文（或下一步工具）为止。
 *  只在状态变的那一刻记时间（同一份状态重画不改），不开定时；换了一轮（计时起点变了）从头记。 */
export class ThinkingClock {
  private start = Number.NaN;
  private since = 0;
  private seconds = 0;
  private was: TurnActivity["kind"] = "hidden";

  note(kind: TurnActivity["kind"], start: number, now: number): number {
    if (start !== this.start) {
      this.start = start;
      this.since = start;
      this.seconds = 0;
      this.was = "hidden";
    }
    if (kind === "thinking" && this.was !== "thinking" && this.was !== "hidden") this.since = now;
    if (kind !== "thinking" && kind !== "hidden" && kind !== "reconnecting" && this.was === "thinking") {
      this.seconds = Math.max(1, Math.round((now - this.since) / 1000));
    }
    if (kind !== "hidden" && kind !== "reconnecting") this.was = kind;
    return this.seconds;
  }
}

type ConnectionState = Pick<Projection, "lastError"> & {
  isLoaded: boolean;
  isTailing: boolean;
  transportError: string | null;
  stalled: boolean;
};

/** 对话顶部那一行（assistant.tsx 的 SessionStatusBar）此刻要不要说「正在重新连接…」：读写出错、引擎在重试、加载完了尾巴却
 *  没连上时说。只说这一句本地化的话，不显示原始报错（fetch 的「Failed to fetch」、引擎原文，多是英文：2026-10-15 安卓 F5），
 *  原文由页面写进控制台排查。对话内状态行已经在说「连接中断，正在重连…」（stalled）时不说，免得两处说同一件事。 */
export function showsReconnecting(state: ConnectionState): boolean {
  if (state.stalled) return false;
  return !!state.transportError || state.lastError?.retryStatus === "retrying" || (state.isLoaded && !state.isTailing);
}

/** 顶部那一行背后的原始报错（只给控制台）。 */
export function connectionDetail(state: Pick<ConnectionState, "lastError" | "transportError">): string {
  return state.transportError || (state.lastError?.retryStatus === "retrying" ? state.lastError.message : "");
}
