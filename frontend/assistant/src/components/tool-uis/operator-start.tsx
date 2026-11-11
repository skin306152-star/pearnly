// 开始前（方案 14.10，工具 operator_start，input {task, company, company_title, period}）：样子同「允许」卡片（operator-card.tsx）。
// 用户 11-05：要人定的事（账套、对方档、科目……）都由 CC 先判定（write_precheck check）再用通用提问卡片 ask_user 一题一题问（ask-card.tsx）。
// 用户 11-10（一句话下令，A 线 11-11）：卡片只做预选、看清、就地改，没有「开始」：写之前 CC 按服务端的写入计划一句话复述，用户在对话里
// 同意后才开始（pearnly_core.assistant.write_plan）；「再录一次」也在对话里说。卡片没有结果时（任务还在准备）自己调代录接口取数：
// 资料第一页（documents，服务端分页，「显示更多」再取）→ 预检（precheck：写入目标 B4，几份账套就列几份；结果不明的单）。
// 主体只有不是选择题的两样：Express 登录没设置的一行〔设置〕（本页就在那台电脑上时）、上次推送结果不明的单〔已入账〕〔没有入账〕
// （resolve 后再预检，防重复入账的流程锁）；卡里答的题只记下（remember）。交单结果（user.custom_tool_result {sent, not_sent[]}）到了，
// 那一行下面列没收的单与原因。不调对话的 confirm / run_tool。

import { type ToolCallMessagePartComponent, useAuiState } from "@assistant-ui/react";
import { type FC, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Marquee } from "@/components/ui/marquee";
import { co, lang, op } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught } from "@/lib/caught";
import { AskCard } from "./ask-card";
import { type AskResult, docAnswers, itemQuestion, itemsOfNeeded } from "./ask-shared";
import { OperatorCard, useCardAction } from "./operator-card";
import { uploadStateOf } from "./operator-steps";
import {
  authorizeHere,
  type DocGroup,
  type DocRef,
  docTitle,
  groupOf,
  loginHere,
  operatorActions,
  type PrecheckDoc,
  reasonText,
  RESULT_UNKNOWN,
  resultOf,
  type StartInput,
  type StartResult,
  type WriteTarget,
} from "./operator-shared";

/** 问话：写入 {公司} · {账套} · {电脑} 的 Express（不知道的那一项不写）；一批写进几份账套时列出每一份（服务端 targets，张数多的在前）。 */
export function writeLine(input: Pick<StartInput, "company" | "company_title">, targets: readonly WriteTarget[]): string {
  const sets = targets.map((t) => t.account_set_title || t.account_set || "").filter(Boolean);
  const named = sets.length > 1 ? new Intl.ListFormat(lang, { type: "conjunction" }).format(sets) : sets[0];
  const computers = [...new Set(targets.map((t) => t.computer ?? "").filter(Boolean))];
  return op.writeTo([input.company_title || input.company, named, computers.join("、")].filter(Boolean).join(" · "));
}

/** 交给 start 的分组：交出的每张单此刻在哪一组（拖动改过的以站点记住的为准，documents 已带回）。 */
export function groupsToStart(docs: readonly DocRef[], docIds: readonly string[]): Record<string, DocGroup> {
  const wanted = new Set(docIds);
  return Object.fromEntries(docs.filter((d) => wanted.has(d.doc_id)).map((d) => [d.doc_id, groupOf(d)]));
}

/** 交给 start 的单：不入账的不交，结果不明还没标的不交，同一张票已写进 Express 的不交——除非人点了「再录一次」（again）。 */
export function docsToStart(docs: readonly DocRef[], prechecks: readonly PrecheckDoc[], again: ReadonlySet<string> = new Set()): string[] {
  const unknown = new Set(prechecks.filter((p) => p.state === "blocked" && p.reason === RESULT_UNKNOWN).map((p) => p.doc_id));
  const written = new Set(prechecks.filter((p) => p.state === "written" && !again.has(p.doc_id)).map((p) => p.doc_id));
  return docs.filter((d) => groupOf(d) !== "skip" && !unknown.has(d.doc_id) && !written.has(d.doc_id)).map((d) => d.doc_id);
}

/** 一行单据与它能做的。 */
// 卡片里逐张列的单（结果不明、要人处理）先列这么多行，其余收进「显示更多」（11-09 发布前流程：45 张逐行列出卡片高 2300px，
// 把用户的话和回复挤出视口；全部的单在「资料」那一步）
export const ROWS_SHOWN = 5;

/** 先列的几行：几组按先后占这几行（结果不明的在前、各带按钮，再是要人处理的、已写进 Express 的）；`all` 时全列。 */
export function rowsShown<T>(groups: readonly (readonly T[])[], all: boolean): { groups: T[][]; hidden: number } {
  let left = all ? Infinity : ROWS_SHOWN;
  const shown = groups.map((group) => {
    const taken = group.slice(0, Math.max(0, left));
    left -= taken.length;
    return taken;
  });
  return { groups: shown, hidden: groups.reduce((n, group, i) => n + group.length - shown[i]!.length, 0) };
}

const DocLine: FC<{ title: string; detail?: string; slot: string; children?: ReactNode }> = ({ title, detail, slot, children }) => (
  <div className="cc-op-handle" data-slot={slot}>
    <Marquee className="block min-w-0 truncate text-ink">{title}</Marquee>
    {detail && <span className="text-ink-3 text-caption">{detail}</span>}
    {children}
  </div>
);

type Prep = { docs: DocRef[]; next: number | null; prechecks: PrecheckDoc[]; targets: WriteTarget[]; login: { set: boolean; user: string } | null };

/** 第一页重取后合上已取过的「显示更多」那几页：第一页在前（新上传的落在这里），别的页里已有的照留，同一张只留一次。 */
export function mergeDocs(first: readonly DocRef[], before: readonly DocRef[]): DocRef[] {
  const seen = new Set(first.map((d) => d.doc_id));
  return [...first, ...before.filter((d) => !seen.has(d.doc_id))];
}

/** 这段对话里识别完的上传（operator_upload 有结果、已识别）：编号连成一串，变了卡片就重取资料。 */
export function uploadsDoneKey(messages: ReadonlyArray<{ content: ReadonlyArray<unknown> }>): string {
  const done: string[] = [];
  for (const message of messages) {
    for (const part of message.content as ReadonlyArray<{ type?: string; toolName?: string; toolCallId?: string; result?: unknown; isError?: boolean }>) {
      if (part.type !== "tool-call" || part.toolName !== "operator_upload" || part.result === undefined) continue;
      if (uploadStateOf(part.result, !!part.isError, null).kind === "done") done.push(String(part.toolCallId));
    }
  }
  return done.join("|");
}

/** 准备中的资料与预检（只在卡片能点时取）。上传识别完了（uploads 变了）重取；点「开始」前也重取（测试机 11-05：卡片出现后再上传的单，
 *  第一次点「开始」报「没有要处理的单据」、刷新才列出来）。重取保留已用「显示更多」取过的页。 */
function usePrep(input: StartInput, on: boolean, uploads: string) {
  const [prep, setPrep] = useState<Prep | null>(null);
  const load = useCallback(async (offset: number, before: Prep | null, refresh = false): Promise<Prep> => {
    const page = await operatorActions.documents(input.company, input.period, offset);
    const docs = refresh ? mergeDocs(page.documents, before?.docs ?? []) : [...(before?.docs ?? []), ...page.documents];
    const next = refresh && before ? before.next : page.next;
    const [checked, login] = await Promise.all([operatorActions.precheck(input.company, docs.map((d) => d.doc_id)), loginHere()]);
    const fresh = { docs, next, prechecks: checked.documents, targets: checked.targets, login };
    setPrep(fresh);
    return fresh;
  }, [input.company, input.period]);
  const latest = useRef<Prep | null>(null);
  latest.current = prep;
  useEffect(() => {
    if (!on || !input.company) return;
    void load(0, latest.current, true).catch((caughtError: unknown) => reportCaught(caughtError, "operator-start"));
  }, [on, input.company, load, uploads]);
  return {
    prep,
    more: () => (prep && prep.next !== null ? load(prep.next, prep) : Promise.resolve()),
    reload: () => load(0, null),
    refresh: () => load(0, latest.current, true),
  };
}

export const OperatorStartCard: ToolCallMessagePartComponent = (props) => {
  const input = (props.args ?? {}) as unknown as StartInput;
  const part = props as unknown as CardPart;
  // 开始由用户在对话里一句话下令（写入计划，用户 11-10）：卡片没有结果就是还在准备，只做预选、看清、就地改，不再停着等人点
  const asking = part.result === undefined;
  const action = useCardAction(props.toolCallId);
  const uploads = useAuiState((s) => uploadsDoneKey(s.thread.messages));
  const { prep, more, reload, refresh } = usePrep(input, asking, uploads);
  const [notice, setNotice] = useState("");
  const [allRows, setAllRows] = useState(false);
  const prechecks = prep?.prechecks ?? [];
  const titleOf = (id: string) => docTitle(prep?.docs.find((d) => d.doc_id === id) ?? {}, id);
  const unknown = prechecks.filter((p) => p.state === "blocked" && p.reason === RESULT_UNKNOWN);
  const stopped = prechecks.filter((p) => (p.state === "blocked" || p.state === "waiting") && p.reason !== RESULT_UNKNOWN);
  const result = resultOf<StartResult>(props.result);
  // 还没定的项（11-08 生产事故：CC 没先问就点了开始，同一客户在 24 张上各停一次、问 24 遍）：开始前在这张卡里一次问完，
  // 几张单的同一题（同一类、同一对象）只问一次（`itemsOfNeeded`），答案随「开始」交给站点记住，后面的单不再问
  const items = useMemo(() => itemsOfNeeded(prechecks), [prechecks]);
  const questions = useMemo(() => items.map((item) => itemQuestion(item, input.company)), [items, input.company]);
  const [sent, setSent] = useState<AskResult | null>(null);
  // 同一张票已写进 Express 的（默认跳过）：人点了「再录一次」的放进 again
  const written = prechecks.filter((p) => p.state === "written");
  const shown = rowsShown([unknown, stopped, written], allRows);
  const [shownUnknown, shownStopped, shownWritten] = shown.groups as [PrecheckDoc[], PrecheckDoc[], PrecheckDoc[]];
  // 卡里答的题只记下（同写账前置判定的记法），不开始写；写不写、写哪几张由写入计划定（再录一次也在对话里说）
  const remember = (answers: Parameters<typeof operatorActions.remember>[1], done?: () => void) =>
    void action.run(async () => {
      await operatorActions.remember(input.company, answers);
      done?.();
      await refresh();
    }, false);
  const resolve = (docId: string, entered: boolean) =>
    void action.run(async () => {
      await operatorActions.resolve(input.company, docId, entered);
      await reload();
    }, false);

  const body = prep && (
    <div className="cc-op-before-body">
      {prep.login?.set && prep.login.user && (
        <div className="cc-op-line" data-slot="cc-op-login-user">
          <Marquee className="min-w-0 flex-1 truncate">{op.expressLogin}</Marquee>
          <span>{prep.login.user}</span>
        </div>
      )}
      {prep.login?.set === false && (
        <div className="cc-op-line" data-slot="cc-op-login">
          <Marquee className="min-w-0 flex-1 truncate">{op.reason.login_missing}</Marquee>
          <button type="button" className="cc-card-btn" data-slot="cc-op-authorize" disabled={action.busy}
            onClick={() => void action.run(async () => (await authorizeHere()) && reload(), false)}>
            {op.setUp}
          </button>
        </div>
      )}
      {shownUnknown.map((d) => (
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
      {shownStopped.map((d) => (
        <DocLine key={d.doc_id} slot="cc-op-handle" title={titleOf(d.doc_id)} detail={reasonText(d.reason, d.message)} />
      ))}
      {shownWritten.map((d) => (
        <DocLine key={d.doc_id} slot="cc-op-written" title={titleOf(d.doc_id)} detail={[op.doc.saved, d.docnum].filter(Boolean).join(" · ")} />
      ))}
      {(shown.hidden > 0 || prep.next !== null) && (
        <button type="button" className="cc-show-more" disabled={action.busy}
          onClick={() => (shown.hidden > 0 ? setAllRows(true) : void action.run(more, false))}>
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

  if (asking && questions.length && !result) {
    const submit = (answered: AskResult) => remember(docAnswers(items, questions, answered), () => setSent(answered));
    return (
      <div data-slot="cc-op-before-ask">
        <AskCard questions={questions} phase={sent ? "answered" : "ask"} result={sent} busy={action.busy || !prep}
          failure={notice || action.failure} onSubmit={submit} />
      </div>
    );
  }

  return (
    <OperatorCard
      part={part}
      phase={asking ? "ask" : "answered"}
      slot="cc-op-before"
      question={<span data-slot="cc-op-target">{writeLine(input, prep?.targets ?? [])}</span>}
      answer={op.start}
      body={body}
      after={after}
      buttons={[]}
      busy={action.busy || (asking && !prep)}
      failure={notice || action.failure}
    />
  );
};
