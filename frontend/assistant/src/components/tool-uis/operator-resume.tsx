// 继续（方案 14.10，工具 operator_resume，input {task, company, reason, doc_id, ref, party, doc_ids}）：任务因整批停（登录、选公司、授权……）
// 或停止（reason = stopped）停下时一张。样子同「允许」卡片（operator-card.tsx）：问话是停下的原因（代码 → 文案；卡住的那张写在后面），
// 底部靠右〔设置〕（要设置 Express 登录、且本页就在能设置的电脑上）与〔继续〕。继续照旧调站点 task_resume，本页先定下卡片；
// 交回后出 user.custom_tool_result {resumed: true}。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { useEffect, useState } from "react";
import { op } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { desktop, OPERATOR } from "@/lib/desktop";
import { type CardButton, OperatorCard, useCardAction } from "./operator-card";
import { authorizeHere, docTitle, LOGIN_REASONS, operatorActions, reasonText, type ResumeInput } from "./operator-shared";

/** 本页在支持实操的 CC 桌面版窗口里（能就地设置 Express 登录）。 */
function useCanAuthorize(): boolean {
  const [can, setCan] = useState(false);
  useEffect(() => {
    let alive = true;
    void desktop()
      .then(async (found) => !!found && !!(await found.state()).features?.includes(OPERATOR))
      .then((ok) => alive && setCan(ok), () => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return can;
}

/** 问话：停下的原因（停止写「已停止」），卡住的那张写在后面。 */
export function resumeQuestion(input: Pick<ResumeInput, "reason" | "doc_id" | "ref" | "party">): string {
  const reason = input.reason === "stopped" ? op.stopped : reasonText(input.reason);
  const doc = input.reason === "stopped" ? "" : docTitle(input, "");
  return [reason, doc].filter(Boolean).join(" · ");
}

export const OperatorResumeCard: ToolCallMessagePartComponent = (props) => {
  const input = (props.args ?? {}) as unknown as ResumeInput;
  const part = props as unknown as CardPart;
  const action = useCardAction(props.toolCallId);
  const canAuthorize = useCanAuthorize();
  const buttons: CardButton[] = [];
  if (LOGIN_REASONS.has(input.reason) && canAuthorize) {
    buttons.push({ slot: "cc-op-authorize", label: op.setUp, onClick: () => void action.run(authorizeHere, false) });
  }
  buttons.push({ slot: "cc-op-resume", label: op.resume, primary: true, onClick: () => void action.run(() => operatorActions.resume(input.task)) });
  return (
    <OperatorCard
      part={part}
      slot="cc-op-resume"
      question={resumeQuestion(input)}
      answer={op.resume}
      body={
        input.detail ? (
          <div className="cc-op-before-body">
            <span className="cc-op-detail" data-slot="cc-op-detail">{input.detail}</span>
          </div>
        ) : undefined
      }
      buttons={buttons}
      busy={action.busy}
      failure={action.failure}
    />
  );
};
