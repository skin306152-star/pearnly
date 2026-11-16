// 中途要人处理的那一张（方案 14.10，工具 operator_choose，input {task, company, doc_id, ref, party, reason, level, result_unknown}）：
// 每张没做成、要人处理的单一张。
//   - 结果不明：样子同「允许」卡片（operator-card.tsx），问话是这张单（单号 · 对方），下面一行原因，〔没有入账〕〔已入账〕→ 站点 resolve，
//     再 task_settle（以站点为准收尾）；
//   - 有要选的（能点时预检这一张，precheck 的 needed）：通用提问卡片（ask-card.tsx，用户 11-05 照 Claude Code 的提问卡片），一项一题，
//     「提交」→ task_answer（这张交回原任务接着做；跳过的项不带，站点照常判）；
//   - 没得选的：同「允许」卡片，只有「重试」（task_answer 空回答）。
// 本页先定下卡片；站点交回队列后出 user.custom_tool_result {answered: true}。

import { type ToolCallMessagePartComponent, useAuiState } from "@assistant-ui/react";
import { useEffect, useMemo, useState } from "react";
import { op } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught } from "@/lib/caught";
import { AskCard } from "./ask-card";
import { type AskItem, type AskResult, docAnswers, docItemQuestion, itemsOfNeeded } from "./ask-shared";
import { type CardButton, OperatorCard, useCardAction } from "./operator-card";
import { cardPhase, type ChooseInput, docTitle, operatorActions, reasonText, RESULT_UNKNOWN } from "./operator-shared";

/** 这段对话里同一任务、还在等人、停在「要人选」的几张卡（按出现先后）：``卡片编号\t单据`` 一行一张（字符串，选择器结果才稳定）。 */
export function choiceRun(messages: ReadonlyArray<{ content: ReadonlyArray<unknown> }>, task: string): string {
  const rows: string[] = [];
  for (const message of messages) {
    for (const part of message.content as ReadonlyArray<{ type?: string; toolName?: string; toolCallId?: string; args?: Record<string, unknown>; result?: unknown }>) {
      if (part.type !== "tool-call" || part.toolName !== "operator_choose" || part.result !== undefined) continue;
      const args = part.args ?? {};
      if (String(args.task ?? "") !== task || !isChoice(args.reason)) continue;
      rows.push(`${part.toolCallId}\t${String(args.doc_id ?? "")}`);
    }
  }
  return rows.join("\n");
}

const isChoice = (reason: unknown) => String(reason ?? "").startsWith("choice_needed");

export const OperatorChooseCard: ToolCallMessagePartComponent = (props) => {
  const input = (props.args ?? {}) as unknown as ChooseInput;
  const part = props as unknown as CardPart;
  const phase = cardPhase(part);
  const asking = phase === "ask";
  const action = useCardAction(props.toolCallId);
  const unknown = !!input.result_unknown || input.reason === RESULT_UNKNOWN;
  // 同一任务里几张都停在「要人选」：合成一张卡（排第一的那张画、其余不画），一次预检、同一题只问一次、一次交回
  // （11-08 生产：24 张同一客户的卡同一秒各发一次预检，每个 12–28 秒，同一题问 24 遍）
  const run = useAuiState((s) => (asking && isChoice(input.reason) ? choiceRun(s.thread.messages, input.task) : ""));
  const merged = useMemo(() => run.split("\n").filter(Boolean).map((row) => row.split("\t") as [string, string]), [run]);
  const lead = merged.length > 1 ? merged[0]![0] : "";
  const docIds = useMemo(() => (merged.length > 1 ? merged.map(([, doc]) => doc) : [input.doc_id]), [merged, input.doc_id]);
  const [items, setItems] = useState<AskItem[] | null>(null);
  const [sent, setSent] = useState<AskResult | null>(null);
  const following = !!lead && lead !== props.toolCallId;
  const docsKey = docIds.join(",");
  useEffect(() => {
    if (!asking || unknown || following || !input.company || !docsKey) return;
    let alive = true;
    void operatorActions.precheck(input.company, docsKey.split(",")).then(
      (reply) => alive && setItems(itemsOfNeeded(reply.documents)),
      (caughtError: unknown) => reportCaught(caughtError, "operator-choose"),
    );
    return () => {
      alive = false;
    };
  }, [asking, unknown, following, input.company, docsKey]);
  // 中途这一张：问题前面带上是哪张单（几张合成一张时写张数）
  const title = docIds.length > 1 ? op.docCount(docIds.length) : docTitle(input, "");
  const questions = useMemo(
    () => (items ?? []).map((item) => docItemQuestion(item, input.company, title)),
    [items, input.company, title],
  );
  if (following) return null;  // 合进排第一的那张卡（所有 hook 之后再返回）

  if (!unknown && questions.length) {
    const submit = (result: AskResult) =>
      void action.run(async () => {
        const answers = docAnswers(items ?? [], questions, result);
        if (docIds.length > 1) await operatorActions.answerMany(input.task, answers);
        else await operatorActions.answer(input.task, input.doc_id, answers);
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
