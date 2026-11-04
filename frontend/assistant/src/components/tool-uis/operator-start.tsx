// 开始前（方案 14.10，工具 operator_start，input {task, company, company_title, period}）：样子同「允许」卡片（operator-card.tsx）。
// 能点（pearnly.tool_run awaiting_approval、还没有结果）时卡片自己调代录接口：资料第一页（documents，服务端分页，「显示更多」再取）→
// 预检（precheck：每张 ready / choose / waiting / blocked 与写入目标 B4）。问话一行「写入 {公司} · {账套} 的 Express」；下面依次是
// Express 登录没设置的一行〔设置〕（本页就在那台电脑上时）、上次推送结果不明的单〔已入账〕〔没有入账〕（resolve 后再预检）、停下的单与原因、
// 要人定的按类一次问完（下拉）；底部「开始」一直能点，还差几项写在它上面。点「开始」照旧调 start（带回答与任务号），本页先定下卡片；
// 交单结果（user.custom_tool_result {sent, not_sent[]}）到了，点完那一行下面列没收的单与原因。不调对话的 confirm / run_tool。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { type FC, type ReactNode, useCallback, useEffect, useState } from "react";
import { Marquee } from "@/components/ui/marquee";
import { co, lang, op } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught } from "@/lib/caught";
import { OperatorCard, useCardAction } from "./operator-card";
import {
  answersFor,
  authorizeHere,
  cardPhase,
  type DocGroup,
  type DocRef,
  docTitle,
  groupOf,
  kindTitle,
  loginHere,
  operatorActions,
  optionLabel,
  type PrecheckDoc,
  questionGroups,
  questionKey,
  questionsOf,
  reasonText,
  RESULT_UNKNOWN,
  resultOf,
  type StartInput,
  type StartResult,
  type WriteTarget,
} from "./operator-shared";

/** 问话：写入 {公司} · {账套} 的 Express（不知道的那一项不写）；一批写进几份账套时列出每一份（服务端 targets，张数多的在前）。 */
export function writeLine(input: Pick<StartInput, "company" | "company_title">, targets: readonly WriteTarget[]): string {
  const sets = targets.map((t) => t.account_set_title || t.account_set || "").filter(Boolean);
  const named = sets.length > 1 ? new Intl.ListFormat(lang, { type: "conjunction" }).format(sets) : sets[0];
  return op.writeTo([input.company_title || input.company, named].filter(Boolean).join(" · "));
}

/** 交给 start 的分组：交出的每张单此刻在哪一组（拖动改过的以站点记住的为准，documents 已带回）。 */
export function groupsToStart(docs: readonly DocRef[], docIds: readonly string[]): Record<string, DocGroup> {
  const wanted = new Set(docIds);
  return Object.fromEntries(docs.filter((d) => wanted.has(d.doc_id)).map((d) => [d.doc_id, groupOf(d)]));
}

/** 交给 start 的单：不入账的不交，结果不明还没标的不交。 */
export function docsToStart(docs: readonly DocRef[], prechecks: readonly PrecheckDoc[]): string[] {
  const unknown = new Set(prechecks.filter((p) => p.state === "blocked" && p.reason === RESULT_UNKNOWN).map((p) => p.doc_id));
  return docs.filter((d) => groupOf(d) !== "skip" && !unknown.has(d.doc_id)).map((d) => d.doc_id);
}

/** 一行单据与它能做的。 */
const DocLine: FC<{ title: string; detail?: string; slot: string; children?: ReactNode }> = ({ title, detail, slot, children }) => (
  <div className="cc-op-handle" data-slot={slot}>
    <Marquee className="block min-w-0 truncate text-ink">{title}</Marquee>
    {detail && <span className="text-ink-3 text-caption">{detail}</span>}
    {children}
  </div>
);

type Prep = { docs: DocRef[]; next: number | null; prechecks: PrecheckDoc[]; targets: WriteTarget[]; login: boolean | null };

/** 准备中的资料与预检（只在卡片能点时取）。 */
function usePrep(input: StartInput, on: boolean) {
  const [prep, setPrep] = useState<Prep | null>(null);
  const load = useCallback(async (offset: number, before: Prep | null) => {
    const page = await operatorActions.documents(input.company, input.period, offset);
    const docs = [...(before?.docs ?? []), ...page.documents];
    const [checked, login] = await Promise.all([operatorActions.precheck(input.company, docs.map((d) => d.doc_id)), loginHere()]);
    setPrep({ docs, next: page.next, prechecks: checked.documents, targets: checked.targets, login });
  }, [input.company, input.period]);
  useEffect(() => {
    if (!on || !input.company) return;
    void load(0, null).catch((caughtError: unknown) => reportCaught(caughtError, "operator-start"));
  }, [on, input.company, load]);
  return { prep, more: () => (prep && prep.next !== null ? load(prep.next, prep) : Promise.resolve()), reload: () => load(0, null) };
}

export const OperatorStartCard: ToolCallMessagePartComponent = (props) => {
  const input = (props.args ?? {}) as unknown as StartInput;
  const part = props as unknown as CardPart;
  const asking = cardPhase(part) === "ask";
  const action = useCardAction(props.toolCallId);
  const { prep, more, reload } = usePrep(input, asking);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const prechecks = prep?.prechecks ?? [];
  const groupOfDoc = (id: string) => groupOf(prep?.docs.find((d) => d.doc_id === id) ?? {});
  const questions = questionsOf(prechecks, groupOfDoc);
  const left = questions.filter((q) => !picked[questionKey(q)]).length;
  const titleOf = (id: string) => docTitle(prep?.docs.find((d) => d.doc_id === id) ?? {}, id);
  const unknown = prechecks.filter((p) => p.state === "blocked" && p.reason === RESULT_UNKNOWN);
  const stopped = prechecks.filter((p) => (p.state === "blocked" || p.state === "waiting") && p.reason !== RESULT_UNKNOWN);
  const result = resultOf<StartResult>(props.result);

  const start = () => {
    const docIds = docsToStart(prep?.docs ?? [], prechecks);
    const missing = !docIds.length ? op.noDocs : left > 0 ? op.confirmLeft(left) : "";
    setNotice(missing);
    if (missing) return;
    void action.run(() => operatorActions.start(input, docIds, answersFor(questions, picked), groupsToStart(prep?.docs ?? [], docIds)));
  };
  const resolve = (docId: string, entered: boolean) =>
    void action.run(async () => {
      await operatorActions.resolve(input.company, docId, entered);
      await reload();
    }, false);

  const body = prep && (
    <div className="cc-op-before-body">
      {prep.login === false && (
        <div className="cc-op-line" data-slot="cc-op-login">
          <Marquee className="min-w-0 flex-1 truncate">{op.reason.login_missing}</Marquee>
          <button type="button" className="cc-card-btn" data-slot="cc-op-authorize" disabled={action.busy}
            onClick={() => void action.run(async () => (await authorizeHere()) && reload(), false)}>
            {op.setUp}
          </button>
        </div>
      )}
      {unknown.map((d) => (
        <DocLine key={d.doc_id} slot="cc-op-unknown" title={titleOf(d.doc_id)} detail={reasonText(d.reason, d.message)}>
          <div className="cc-card-actions">
            <button type="button" className="cc-card-btn" data-slot="cc-op-entered" disabled={action.busy} onClick={() => resolve(d.doc_id, true)}>
              {op.doc.saved}
            </button>
            <button type="button" className="cc-card-btn" data-slot="cc-op-not-entered" disabled={action.busy} onClick={() => resolve(d.doc_id, false)}>
              {op.notEntered}
            </button>
          </div>
        </DocLine>
      ))}
      {stopped.map((d) => (
        <DocLine key={d.doc_id} slot="cc-op-handle" title={titleOf(d.doc_id)} detail={reasonText(d.reason, d.message)} />
      ))}
      {questionGroups(questions).map(([kind, list]) => (
        <div key={kind} className="cc-op-ask-group" data-kind={kind} data-slot="cc-op-ask-group">
          <div className="cc-op-ask-kind">{kindTitle(kind, list.flatMap((q) => q.groups ?? []))}</div>
          {list.map((q) => (
            <label key={questionKey(q)} className="cc-op-ask" data-slot="cc-op-ask">
              <Marquee className="min-w-0 flex-1 truncate">{q.label || q.subject}</Marquee>
              <select
                className="cc-input cc-op-ask-select"
                aria-label={`${kindTitle(kind)} · ${q.label || q.subject}`}
                value={picked[questionKey(q)] ?? ""}
                onChange={(e) => setPicked((p) => ({ ...p, [questionKey(q)]: e.target.value }))}
              >
                <option value="" disabled>—</option>
                {q.options.map((option) => (
                  <option key={option.code} value={option.code}>
                    {optionLabel(kind, option)}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      ))}
      {prep.next !== null && (
        <button type="button" className="cc-show-more" disabled={action.busy} onClick={() => void action.run(more, false)}>
          {co.showMore}
        </button>
      )}
    </div>
  );
  // 交单结果：没收的单与原因（点完那一行下面）
  const refused = result?.not_sent ?? [];
  const after = refused.length ? (
    <div className="cc-op-before-body" data-slot="cc-op-refused">
      {refused.map((d) => (
        <DocLine key={d.doc_id} slot="cc-op-row" title={docTitle(d, d.doc_id)} detail={reasonText(d.reason)} />
      ))}
    </div>
  ) : null;

  return (
    <OperatorCard
      part={part}
      slot="cc-op-before"
      question={<span data-slot="cc-op-target">{writeLine(input, prep?.targets ?? [])}</span>}
      answer={op.start}
      body={body}
      after={after}
      buttons={[{ slot: "cc-op-start", label: op.start, primary: true, onClick: start }]}
      busy={action.busy || (asking && !prep)}
      failure={notice || action.failure}
    />
  );
};
