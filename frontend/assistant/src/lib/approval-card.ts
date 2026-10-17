// 允许 / 拒绝卡（components/tool-uis/approval-bar.tsx）此刻是哪个样子（用户 10-17，照 Claude 的问题卡片）：
//   - 等回答：问话 + 改动 + 底部靠右「拒绝」「允许」；
//   - 点完：卡片留在对话流原处（不并进折叠的步骤组），按钮行换成淡色的所选那一项；这一步工具没做成时加危险色「失败」。
// 当场点的、切到别的对话再切回、重开对话、别的设备上答过，都是同一个判断：本页点过（approval.approved），结果里写明
// 被拒绝（lib/step-summary.ts 的 stepDenied），或要「允许」的本站工具已有结果（重开对话时历史里没有点击记录）。
// 纯函数：不碰 React。

import { stepDenied, stepFailed } from "./step-summary";

type Approval = { id: string; approved?: boolean };

/** 一步（工具调用部件）里用得到的几项。 */
export type CardPart = {
  toolName: string;
  args?: Readonly<Record<string, unknown>> | undefined;
  result?: unknown;
  isError?: boolean | undefined;
  status?: { type: string } | undefined;
  approval?: Approval | undefined;
};

/** 要「允许」的本站工具（lib/tool-labels.ts）：ask 里有的都要；gate 里列了参数的，给了其中一个才要（同服务端 needs_approval）。 */
export type Gates = { ask: Readonly<Record<string, string>>; gate?: Readonly<Record<string, readonly string[]>> };

export type CardState = { phase: "ask" } | { phase: "answered"; approved: boolean; failed: boolean };

/** 这次调用给了这个参数（False / 0 也算给了；空串、空表、空对象不算），同 pearnly_core tools._given。 */
function given(value: unknown): boolean {
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

/** 结果标了出错（出卡片前就核出要拒绝的、执行时出错的都是）。 */
const resultError = (result: unknown) =>
  typeof result === "object" && result !== null && (result as { isError?: unknown }).isError === true;

/** 这一步是不是允许 / 拒绝卡，此刻什么样子；不是卡回 null（照常是步骤组里的一行）。 */
export function approvalCard(part: CardPart, gates: Gates): CardState | null {
  const approval = part.approval;
  if (approval && approval.approved === undefined) return { phase: "ask" };
  if (approval) {
    const approved = approval.approved === true;
    return { phase: "answered", approved, failed: approved && stepFailed(part) };
  }
  if (stepDenied(part)) return { phase: "answered", approved: false, failed: false };
  // 重开对话：要「允许」的本站工具已有结果就是点过「允许」。结果标了出错的不算：出卡片前就核出要拒绝的也是这样交回的
  // （pearnly_core toolrun.execute 的 refused），那一步没出过卡片
  if (part.result !== undefined && !resultError(part.result) && gatedCall(part.toolName, part.args, gates)) {
    return { phase: "answered", approved: true, failed: stepFailed(part) };
  }
  return null;
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
