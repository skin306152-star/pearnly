// 代录卡片的外框（开始前 operator-start.tsx、中途选 operator-choose.tsx、继续 operator-resume.tsx）：样子同对话里的「允许」卡片
// （steps.tsx 的 AskStep 套 LoneStep：.cc-steps-card 一层框，问话 .cc-ask-q，底部靠右 .cc-card-btn；点完按钮行换成一行淡色所选那一项，
// 没做成加危险色「失败」，没答就停了写「已取消」）。用户 11-04：不新造结束框、任务清单，卡片样子同「允许」卡片。
// 卡片不进折叠的步骤组（thread.tsx 的 groupBy 按工具名放行），手机上也留在对话流里（不是允许卡，不走输入框上方的浮层）。

import { useAuiState } from "@assistant-ui/react";
import { type FC, type ReactNode, useState } from "react";
import { steps } from "@/i18n";
import { shownError } from "@/lib/frappe";
import { reportCaught } from "@/lib/caught";
import type { CardPart } from "@/lib/approval-card";
import { cardFailed, cardPhase, settleCard } from "./operator-shared";

/** 这段对话的编号（点完卡片本页先定下用）。 */
export const useSessionId = (): string | null => useAuiState((s) => s.threadListItem.externalId ?? s.threadListItem.remoteId ?? null);

/** 卡片上的一个按钮的动作：在跑时按钮都不能点；出错写在按钮上方（同允许卡片确认没送到那一行）。done：做成后定下卡片。 */
export function useCardAction(toolUseId: string) {
  const sessionId = useSessionId();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const run = async (action: () => Promise<unknown>, settle = true) => {
    setBusy(true);
    setFailure("");
    try {
      await action();
      if (settle) settleCard(sessionId, toolUseId);
    } catch (caughtError) {
      reportCaught(caughtError, "operator-card");
      setFailure(shownError(caughtError));
    } finally {
      setBusy(false);
    }
  };
  return { busy, failure, run };
}

export type CardButton = { slot: string; label: string; primary?: boolean; onClick: () => void };

/** 卡片外框。answer：点完那一行（所选那一项）；body 只在问的时候显示（点完只剩问话与所选那一项，同允许卡片）。 */
export const OperatorCard: FC<{
  part: CardPart;
  slot: string;
  question: ReactNode;
  answer: string;
  body?: ReactNode;
  after?: ReactNode;
  buttons: readonly CardButton[];
  busy: boolean;
  failure: string;
}> = ({ part, slot, question, answer, body, after, buttons, busy, failure }) => {
  const phase = cardPhase(part);
  return (
    <div className="cc-steps-card" data-slot={slot}>
      <div className="aui-step cc-ask" data-phase={phase}>
        <div className="cc-ask-q">{question}</div>
        {phase === "ask" && body}
        {phase === "answered" && (
          <div data-slot="cc-ask-answer" className="cc-ask-answer">
            {answer}
            {cardFailed(part) && (
              <>
                {" · "}
                <span className="cc-step-failed">{steps.failed}</span>
              </>
            )}
          </div>
        )}
        {phase === "cancelled" && (
          <div data-slot="cc-ask-answer" className="cc-ask-answer">
            {steps.cancelled}
          </div>
        )}
        {after}
        {phase === "ask" && buttons.length > 0 && (
          <div className="cc-ask-foot">
            {failure && (
              <div role="alert" data-slot="cc-ask-failure" className="cc-alert">
                <p className="line-clamp-(--cc-alert-lines)">{failure}</p>
              </div>
            )}
            <div className="cc-card-actions">
              {buttons.map((button) => (
                <button
                  key={button.slot}
                  type="button"
                  className="cc-card-btn"
                  data-slot={button.slot}
                  data-primary={button.primary ? "" : undefined}
                  disabled={busy}
                  onClick={button.onClick}
                >
                  {button.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
