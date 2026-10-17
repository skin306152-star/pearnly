// The human-in-the-loop gate for an always_ask tool.
//
// Managed Agents parked the turn on this tool call and is waiting for a
// user.tool_confirmation. Allow lets it run; Deny sends the agent a message
// it sees as the tool's result ("Denied: not on the production box"), so it
// can adjust instead of retrying blindly. Rendered by any tool card whose
// part carries an unresolved `approval` (see reducer.ts).
//
// 卡片规矩（用户 2026-10-13，照 Claude 对话里卡片的做法）：按钮最多两个、放在底部；改动最多显示 CARD_ROWS 行，
// 再多就列前几行 + 一行「还有 N 项」（卡内不套滚动；全部参数在步骤行点开可看）；颜色、圆角只用 globals.css 的变量。
// 按钮与点完的样子照 Claude 的问题卡片（用户 10-17）：底部靠右，「拒绝」在左（次要：浅灰实心）、「允许」在右（主要：反色实心），
// 都不带图标，统一用 .cc-card-btn（电脑 28 高、小一号字；手机大号胶囊，不低于 44pt）；改动清单不另套框，一条细线隔开。
// 点完按钮行换成一行淡色字：所选那一项（「允许」「拒绝」），这一步没做成时加危险色「失败」；没有绿勾红叉。
// 确认请求没送到（restoreGates 带回 failure）：按钮还在，上方一行原因，再点重试。

import { cn } from "@/lib/utils";
import type { CardState } from "@/lib/approval-card";
import { steps, t } from "@/i18n";

type Change = { field: string; before: string; after: string };
type Approval = {
  id: string;
  approved?: boolean;
  reason?: string;
  preview?: Change[];
  failure?: string;
};

/** 卡片上最多几行改动（含「还有 N 项」那一行）。 */
export const CARD_ROWS = 5;

/** 要显示的改动行与收起的行数：不超过 CARD_ROWS 行全显示；多了显示前 CARD_ROWS - 1 行，其余合成「还有 N 项」。 */
export function cardRows(preview: readonly Change[] = []): { shown: readonly Change[]; more: number } {
  if (preview.length <= CARD_ROWS) return { shown: preview, more: 0 };
  return { shown: preview.slice(0, CARD_ROWS - 1), more: preview.length - (CARD_ROWS - 1) };
}

// 对话里出错那一行（同 thread.tsx 的 ERROR_BOX：回复出错、发送没成功）：淡红底、红字、最多两行
const FAILURE_BOX = "border-danger-line bg-danger-soft text-danger rounded-md border p-3 text-sm";

/** 改动清单：前几行 + 「还有 N 项」；没有改动不画。 */
export function ChangeList({ preview, className }: { preview?: readonly Change[] | undefined; className?: string }) {
  const { shown, more } = cardRows(preview);
  if (!shown.length) return null;
  return (
    <ul className={cn("cc-ask-changes", className)}>
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
  );
}

/** 确认没送到的原因（按钮上方一行）。 */
export function ApprovalFailure({ message }: { message?: string | undefined }) {
  if (!message) return null;
  return (
    <div role="alert" data-slot="cc-ask-failure" className={FAILURE_BOX}>
      <p className="line-clamp-2">{message}</p>
    </div>
  );
}

/** 点完那一行：所选那一项，没做成时加「失败」。 */
export function ApprovalAnswer({ approved, failed }: { approved: boolean; failed: boolean }) {
  return (
    <div data-slot="cc-ask-answer" className="cc-ask-answer">
      {approved ? t("allow") : t("deny")}
      {failed && (
        <>
          {" · "}
          <span className="cc-step-failed">{steps.failed}</span>
        </>
      )}
    </div>
  );
}

/** 问话下面那一段：等回答时是改动清单 + 底部靠右两个按钮（buttons 为 false 时只列改动：手机上按钮在浮层里），
 *  点完是所选那一项。 */
export function ApprovalBar({
  approval,
  card,
  respondToApproval,
  buttons = true,
  className,
}: {
  approval?: Approval | undefined;
  /** 卡片此刻的样子（lib/approval-card.ts）；不给时按 approval 定。 */
  card?: CardState | null | undefined;
  respondToApproval?: ((response: { approved: boolean; reason?: string }) => void) | undefined;
  buttons?: boolean;
  className?: string;
}) {
  const state: CardState | null =
    card ?? (approval ? (approval.approved === undefined ? { phase: "ask" } : { phase: "answered", approved: approval.approved, failed: false }) : null);
  if (!state) return null;
  if (state.phase === "answered") return <ApprovalAnswer approved={state.approved} failed={state.failed} />;
  // No way to answer on this call.
  if (!respondToApproval) return null;

  return (
    <div className={cn("flex flex-col", className)}>
      <ChangeList preview={approval?.preview} />
      {buttons && (
        <div className="cc-ask-foot">
          <ApprovalFailure message={approval?.failure} />
          <div className="cc-card-actions">
            <button type="button" className="cc-card-btn" onClick={() => respondToApproval({ approved: false })}>
              {t("deny")}
            </button>
            <button type="button" className="cc-card-btn" data-primary="" onClick={() => respondToApproval({ approved: true })}>
              {t("allow")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
