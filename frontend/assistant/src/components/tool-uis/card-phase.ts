// 代录卡片此刻的样子与点完先定下（纯判断 + 本页的会话控制器；不碰桌面桥与站点请求，对话末尾的待处理框、直写门也引它）。
// operator-shared.ts 照旧转出这些名字。

import type { CardPart } from "@/lib/approval-card";
import { getSessionController } from "@/lib/managed-agents/session-controller";

// ── 卡片此刻的样子 ───────────────────────────────────────────────────────────
/** expired：代录任务失效时还在等的卡（执行记录 status expired，B04 11-17）：同已取消一行，字写「已失效」。 */
export type CardPhase = "ask" | "answered" | "cancelled" | "expired" | "ended";

/** 方案 14.10：能点 = 有 pearnly.tool_run awaiting_approval、还没有结果（user.custom_tool_result）。有结果、执行记录往前走了、本页刚点完
 *  → 点完；作废、或没在等也没结果（任务已往前走）→ 已取消（同「允许」卡片没答就没了）。 */
export function cardPhase(part: CardPart): CardPhase {
  if (part.result !== undefined || part.approval?.approved !== undefined) return "answered";
  if (part.run === "expired") return "expired";
  if (part.run === "awaiting_approval") return "ask";
  // 只有认识的「往前走了」才算已答；不认识的状态是已结束、不说结果（ended，主会话 11-17）
  if (part.run === "running" || part.run === "done" || part.run === "error" || part.run === "denied") return "answered";
  if (part.run && part.run !== "cancelled") return "ended";
  return "cancelled";
}

/** 结果写明没做成。 */
export const cardFailed = (part: CardPart) => part.run === "error" || (part.result !== undefined && !!part.isError);

/** 点完卡片：本页先定下（只在本页，结果到了以它为准；不发给服务端）。 */
export function settleCard(sessionId: string | null | undefined, toolUseId: string) {
  if (!sessionId) return;
  getSessionController(sessionId).applyLocal({
    type: "user.tool_confirmation",
    id: `optimistic-confirm-${toolUseId}`,
    tool_use_id: toolUseId,
    result: "allow",
    processed_at: new Date().toISOString(),
  } as never);
}

