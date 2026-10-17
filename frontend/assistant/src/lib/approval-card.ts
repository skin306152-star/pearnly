// 允许 / 拒绝卡（components/tool-uis/approval-bar.tsx）此刻是哪个样子（用户 10-17，照 Claude 的问题卡片）：
//   - 等回答：问话 + 改动 + 底部靠右「拒绝」「允许」；
//   - 点完：卡片留在对话流原处（不并进折叠的步骤组），按钮行换成淡色的所选那一项；这一步工具没做成时加危险色「失败」；
//     刚点、确认还没送到时那一行先淡一档（sending）；
//   - 没答就没了（中断、回合结束、终止，reducer 的 stripUnresolvedApprovals）：问话 + 淡色「已取消」，不写成做完的说法。
//     没答就点了停止的（reducer 标 cancelled）之后补来结果（中断时引擎交回的出错结果）也照旧「已取消」，不当成「允许 · 失败」
//     （第 10 轮 C20）；只有结果写明拒绝的写「拒绝」。
// 当场点的、切到别的对话再切回、重开对话、别的设备上答过，都是同一个判断。答案以服务端结果为准：结果写明被拒绝（lib/step-summary.ts
// 的 stepDenied）就是「拒绝」，有结果、没写拒绝、也不是出错交回的就是「允许」（两台设备同时点、确认超时后改点，本页点的那一项
// 可能不是服务端定下的）；还没结果才按本页点的。重开对话时历史里没有点击记录：要「允许」的本站工具有结果就是出过卡片，
// 除非结果带着「没问过用户」的标记（出卡片前就核出要拒绝的，pearnly_core toolrun.execute，那一步照常是步骤组里的一行）。
// 纯函数：不碰 React。

import { stepDenied, stepFailed, stepNotAsked } from "./step-summary";

type Approval = { id: string; approved?: boolean; sending?: boolean };

/** 一步（工具调用部件）里用得到的几项。 */
export type CardPart = {
  toolName: string;
  args?: Readonly<Record<string, unknown>> | undefined;
  result?: unknown;
  isError?: boolean | undefined;
  status?: { type: string } | undefined;
  approval?: Approval | undefined;
  /** 没答就点了停止（reducer 的 cancelOpenGates）。 */
  cancelled?: boolean | undefined;
};

/** 要「允许」的本站工具（lib/tool-labels.ts）：ask 里有的都要；gate 里列了参数的，给了其中一个才要（同服务端 needs_approval）；
 *  ask_when 是问法随参数变的工具。 */
export type Gates = {
  ask: Readonly<Record<string, string>>;
  gate?: Readonly<Record<string, readonly string[]>>;
  ask_when?: Readonly<Record<string, Readonly<Record<string, string>>>>;
};

export type CardState =
  | { phase: "ask" }
  | { phase: "answered"; approved: boolean; failed: boolean; sending?: boolean }
  | { phase: "cancelled" };

/** 这次调用给了这个参数（False / 0 也算给了；空串、空表、空对象不算），同 pearnly_core tools._given。 */
export function given(value: unknown): boolean {
  if (value === undefined || value === null || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value as object).length > 0;
  return true;
}

/** 这次调用要不要用户点「允许」（本站工具）。 */
export function gatedCall(toolName: string, args: CardPart["args"], gates: Gates): boolean {
  if (!Object.hasOwn(gates.ask, toolName)) return false;
  const keys = gates.gate && Object.hasOwn(gates.gate, toolName) ? gates.gate[toolName] : undefined;
  return !keys || keys.some((key) => given(args?.[key]));
}

/** 卡片上那句问话（声明的）：ask_when 里给了参数的那句（按声明顺序取第一个），否则 ask；都没有回 undefined。同服务端 approval_view。 */
export function declaredAsk(toolName: string, args: CardPart["args"], gates: Gates): string | undefined {
  const when = gates.ask_when && Object.hasOwn(gates.ask_when, toolName) ? gates.ask_when[toolName] : undefined;
  for (const [key, text] of Object.entries(when ?? {})) if (given(args?.[key])) return text;
  return Object.hasOwn(gates.ask, toolName) ? gates.ask[toolName] : undefined;
}

/** 结果标了出错。 */
const resultError = (result: unknown) =>
  typeof result === "object" && result !== null && (result as { isError?: unknown }).isError === true;

/** 这一轮已不在跑、也不在等（工具调用没有结果时部件状态同消息状态）：没答的门已撤掉。 */
const over = (status: CardPart["status"]) => status?.type === "complete" || status?.type === "incomplete";

/** 这一步是不是允许 / 拒绝卡，此刻什么样子；不是卡回 null（照常是步骤组里的一行）。 */
export function approvalCard(part: CardPart, gates: Gates): CardState | null {
  const { approval, result } = part;
  if (approval && approval.approved === undefined) return { phase: "ask" };
  const hasResult = result !== undefined;
  if (approval) {
    // 服务端结果优先于本页点的：写明拒绝就是拒绝；有结果、没写拒绝、不是出错交回的就是允许了（工具照常执行过）
    const approved = hasResult ? !stepDenied({ result }) && (approval.approved === true || !resultError(result)) : approval.approved === true;
    const sending = !hasResult && approval.sending === true;
    return { phase: "answered", approved, failed: approved && stepFailed(part), ...(sending ? { sending } : {}) };
  }
  if (hasResult && stepDenied({ result })) return { phase: "answered", approved: false, failed: false };
  if (!gatedCall(part.toolName, part.args, gates)) return null;
  if (part.cancelled) return { phase: "cancelled" };
  // 重开对话：要「允许」的本站工具已有结果就是点过「允许」（做没做成看结果）；出卡片前就核出要拒绝的不是卡
  if (hasResult) return stepNotAsked({ result }) ? null : { phase: "answered", approved: true, failed: stepFailed(part) };
  // 没有结果、也没有门：这一轮已结束（中断、终止、回合结束时撤掉的门；重开对话时同样）
  return over(part.status) ? { phase: "cancelled" } : null;
}

/** 手机上浮在输入框上方的那一张：最早一张还在等回答的（多张时答完一张再浮下一张）。 */
export function firstPendingApproval<P extends { type: string; approval?: Approval }>(
  messages: readonly { role: string; content: readonly P[] }[],
): P | undefined {
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    for (const part of message.content) {
      if (part.type === "tool-call" && part.approval && part.approval.approved === undefined) return part;
    }
  }
  return undefined;
}
