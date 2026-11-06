// 中途要人处理的那一张（方案 14.10，工具 operator_choose，input {task, company, doc_id, ref, party, reason, level, result_unknown}）：
// 每张没做成、要人处理的单一张。
//   - 结果不明：样子同「允许」卡片（operator-card.tsx），问话是这张单（单号 · 对方），下面一行原因，〔没有入账〕〔已入账〕→ 站点 resolve，
//     再 task_settle（以站点为准收尾）；
//   - 有要选的（能点时预检这一张，precheck 的 needed）：通用提问卡片（ask-card.tsx，用户 11-05 照 Claude Code 的提问卡片），一项一题，
//     「提交」→ task_answer（这张交回原任务接着做；跳过的项不带，站点照常判）；
//   - 没得选的：同「允许」卡片，只有「重试」（task_answer 空回答）。
// 本页先定下卡片；站点交回队列后出 user.custom_tool_result {answered: true}。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { useEffect, useMemo, useState } from "react";
import { op } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught } from "@/lib/caught";
import { AskCard } from "./ask-card";
import { type AskItem, type AskResult, docAnswers, itemQuestion, itemsOfNeeded } from "./ask-shared";
import { type CardButton, OperatorCard, useCardAction } from "./operator-card";
import { cardPhase, type ChooseInput, docTitle, operatorActions, reasonText, RESULT_UNKNOWN } from "./operator-shared";

export const OperatorChooseCard: ToolCallMessagePartComponent = (props) => {
  const input = (props.args ?? {}) as unknown as ChooseInput;
  const part = props as unknown as CardPart;
  const phase = cardPhase(part);
  const asking = phase === "ask";
  const action = useCardAction(props.toolCallId);
  const unknown = !!input.result_unknown || input.reason === RESULT_UNKNOWN;
  const [items, setItems] = useState<AskItem[] | null>(null);
  const [sent, setSent] = useState<AskResult | null>(null);
  useEffect(() => {
    if (!asking || unknown || !input.company || !input.doc_id) return;
    let alive = true;
    void operatorActions.precheck(input.company, [input.doc_id]).then(
      (reply) => alive && setItems(itemsOfNeeded(reply.documents)),
      (caughtError: unknown) => reportCaught(caughtError, "operator-choose"),
    );
    return () => {
      alive = false;
    };
  }, [asking, unknown, input.company, input.doc_id]);
  // 中途这一张：问题前面带上是哪张单
  const title = docTitle(input, "");
  const questions = useMemo(
    () => (items ?? []).map((item) => itemQuestion(item, input.company)).map((q) => (title ? { ...q, question: `${title} · ${q.question}` } : q)),
    [items, input.company, title],
  );

  if (!unknown && questions.length) {
    const submit = (result: AskResult) =>
      void action.run(async () => {
        await operatorActions.answer(input.task, input.doc_id, docAnswers(items ?? [], questions, result));
        setSent(result);
      });
    return <AskCard questions={questions} phase={sent ? "answered" : phase} result={sent} busy={action.busy} failure={action.failure} onSubmit={submit} />;
  }

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
    : [{ slot: "cc-op-retry", label: op.retry, primary: true, onClick: () => void action.run(() => operatorActions.answer(input.task, input.doc_id, {})) }];
  return (
    <OperatorCard
      part={part}
      slot="cc-op-choose"
      question={docTitle(input, input.doc_id)}
      answer={buttons[buttons.length - 1]!.label}
      body={
        <div className="cc-op-before-body">
          <span className="text-ink-3 text-caption" data-slot="cc-op-reason">{reasonText(input.reason)}</span>
          {input.detail ? <span className="cc-op-detail" data-slot="cc-op-detail">{input.detail}</span> : null}
        </div>
      }
      buttons={buttons}
      busy={action.busy}
      failure={action.failure}
    />
  );
};
