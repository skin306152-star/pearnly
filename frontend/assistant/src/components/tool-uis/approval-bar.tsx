
// The human-in-the-loop gate for an always_ask tool.
//
// Managed Agents parked the turn on this tool call and is waiting for a
// user.tool_confirmation. Allow lets it run; Deny sends the agent a message
// it sees as the tool's result ("Denied: not on the production box"), so it
// can adjust instead of retrying blindly. Rendered by any tool card whose
// part carries an unresolved `approval` (see reducer.ts).
//
// 卡片规矩（用户 2026-10-13，照 Claude 对话里卡片的做法）：按钮最多两个、放在底部；改动最多显示 CARD_ROWS 行，
// 再多就列前几行 + 一行「还有 N 项」（卡内不套滚动；全部参数在步骤行点开可看）；手机上按钮点按区不小于 44pt
// （--cc-mbtn-size）；颜色、圆角只用 globals.css 的变量。

import { CheckIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t } from "@/i18n";

type Change = { field: string; before: string; after: string };
type Approval = {
  id: string;
  approved?: boolean;
  reason?: string;
  preview?: Change[];
};

/** 卡片上最多几行改动（含「还有 N 项」那一行）。 */
export const CARD_ROWS = 5;

/** 要显示的改动行与收起的行数：不超过 CARD_ROWS 行全显示；多了显示前 CARD_ROWS - 1 行，其余合成「还有 N 项」。 */
export function cardRows(preview: readonly Change[] = []): { shown: readonly Change[]; more: number } {
  if (preview.length <= CARD_ROWS) return { shown: preview, more: 0 };
  return { shown: preview.slice(0, CARD_ROWS - 1), more: preview.length - (CARD_ROWS - 1) };
}

// 手机（粗指针）上按钮高不低于 44pt，与页面上其它圆按钮同一个尺寸变量
const TAP = "pointer-coarse:h-(--cc-mbtn-size) pointer-coarse:min-w-(--cc-mbtn-size)";

export function ApprovalBar({
  approval,
  respondToApproval,
  className,
}: {
  approval?: Approval;
  respondToApproval?: (response: { approved: boolean; reason?: string }) => void;
  what?: string;
  className?: string;
}) {
  // Already answered (in this tab, or replayed from history): show the verdict.
  if (approval && approval.approved !== undefined) {
    return (
      <div
        className={cn(
          "flex items-center gap-1.5 text-xs",
          approval.approved ? "text-success" : "text-danger",
          className,
        )}
      >
        {approval.approved ? <CheckIcon className="size-3.5" /> : <XIcon className="size-3.5" />}
        <span className="font-medium">{approval.approved ? t("allowed") : t("denied")}</span>
      </div>
    );
  }

  // No pending gate on this call.
  if (!approval || !respondToApproval) return null;

  const { shown, more } = cardRows(approval.preview);
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {shown.length > 0 && (
        <ul className="rounded-md border border-line bg-app px-3 py-2 text-(length:--cc-text-sm) text-ink-2 leading-6">
          {shown.map((change, i) => (
            <li key={i} className="break-words">
              <span className="me-2 text-ink-3">{change.field}</span>
              {change.before ? (
                <>
                  <span className="line-through decoration-ink-4">{change.before}</span>
                  <span className="px-1.5 text-ink-3">→</span>
                </>
              ) : null}
              <span className="font-medium">{change.after}</span>
            </li>
          ))}
          {more > 0 && <li className="text-ink-3">{t("moreItems").replace("{0}", String(more))}</li>}
        </ul>
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" className={TAP} onClick={() => respondToApproval({ approved: true })}>
          <CheckIcon className="size-3.5" />
          {t("allow")}
        </Button>
        <Button size="sm" variant="outline" className={TAP} onClick={() => respondToApproval({ approved: false })}>
          <XIcon className="size-3.5" />
          {t("deny")}
        </Button>
      </div>
    </div>
  );
}
