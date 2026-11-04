// 中途要人处理的那一张（方案 14.10，工具 operator_choose，input {task, company, doc_id, ref, party, reason, level, result_unknown}）：
// 每张没做成、要人处理的单一张。样子同「允许」卡片（operator-card.tsx）：问话是这张单（单号 · 对方），下面一行原因。
//   - 结果不明：〔已入账〕〔没有入账〕→ 站点 resolve，再 task_settle（以站点为准收尾）；
//   - 其余：能点时预检这一张（precheck），要人选的按类下拉 +「确认」→ task_answer（这张交回原任务接着做）；没得选的只有「重试」
//     （task_answer 空回答）。
// 本页先定下卡片；站点交回队列后出 user.custom_tool_result {answered: true}。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { useEffect, useState } from "react";
import { Marquee } from "@/components/ui/marquee";
import { co, op } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught } from "@/lib/caught";
import { type CardButton, OperatorCard, useCardAction } from "./operator-card";
import {
  answersFor,
  cardPhase,
  type ChooseInput,
  docTitle,
  kindTitle,
  operatorActions,
  optionLabel,
  type Question,
  questionKey,
  questionsOf,
  reasonText,
  RESULT_UNKNOWN,
} from "./operator-shared";

export const OperatorChooseCard: ToolCallMessagePartComponent = (props) => {
  const input = (props.args ?? {}) as unknown as ChooseInput;
  const part = props as unknown as CardPart;
  const asking = cardPhase(part) === "ask";
  const action = useCardAction(props.toolCallId);
  const unknown = !!input.result_unknown || input.reason === RESULT_UNKNOWN;
  const [questions, setQuestions] = useState<Question[]>([]);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!asking || unknown || !input.company || !input.doc_id) return;
    let alive = true;
    void operatorActions.precheck(input.company, [input.doc_id]).then(
      (reply) => alive && setQuestions(questionsOf(reply.documents)),
      (caughtError: unknown) => reportCaught(caughtError, "operator-choose"),
    );
    return () => {
      alive = false;
    };
  }, [asking, unknown, input.company, input.doc_id]);
  const left = questions.filter((q) => !picked[questionKey(q)]).length;
  const chosen = questions
    .map((q) => {
      const option = q.options.find((o) => o.code === picked[questionKey(q)]);
      return option ? optionLabel(q.kind, option) : "";
    })
    .filter(Boolean)
    .join(" · ");

  const answer = () => {
    setNotice(left > 0 ? op.confirmLeft(left) : "");
    if (left > 0) return;
    void action.run(() => operatorActions.answer(input.task, input.doc_id, answersFor(questions, picked)));
  };
  const resolve = (entered: boolean) =>
    void action.run(async () => {
      await operatorActions.resolve(input.company, input.doc_id, entered);
      await operatorActions.settle(input.task);
    });

  const buttons: CardButton[] = unknown
    ? [
        { slot: "cc-op-not-entered", label: op.notEntered, onClick: () => resolve(false) },
        { slot: "cc-op-entered", label: op.doc.saved!, primary: true, onClick: () => resolve(true) },
      ]
    : questions.length
      ? [{ slot: "cc-op-confirm", label: co.confirm, primary: true, onClick: answer }]
      : [{ slot: "cc-op-retry", label: op.retry, primary: true, onClick: answer }];

  const body = (
    <div className="cc-op-before-body">
      <span className="text-ink-3 text-caption" data-slot="cc-op-reason">{reasonText(input.reason)}</span>
      {questions.map((q) => (
        <label key={questionKey(q)} className="cc-op-ask" data-kind={q.kind} data-slot="cc-op-ask">
          <Marquee className="min-w-0 flex-1 truncate">{`${kindTitle(q.kind, q.groups ?? [])} · ${q.label || q.subject}`}</Marquee>
          <select
            className="cc-input cc-op-ask-select"
            aria-label={`${kindTitle(q.kind)} · ${q.label || q.subject}`}
            value={picked[questionKey(q)] ?? ""}
            onChange={(e) => setPicked((p) => ({ ...p, [questionKey(q)]: e.target.value }))}
          >
            <option value="" disabled>—</option>
            {q.options.map((option) => (
              <option key={option.code} value={option.code}>
                {optionLabel(q.kind, option)}
              </option>
            ))}
          </select>
        </label>
      ))}
    </div>
  );

  return (
    <OperatorCard
      part={part}
      slot="cc-op-choose"
      question={docTitle(input, input.doc_id)}
      answer={chosen || buttons[buttons.length - 1]!.label}
      body={body}
      buttons={buttons}
      busy={action.busy}
      failure={notice || action.failure}
    />
  );
};
