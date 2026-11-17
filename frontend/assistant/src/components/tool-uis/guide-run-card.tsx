// 按表单的引导卡（引导式分支 3 接线 F2 / F4 / F5；服务端 assistant/guide/run.py · view()）：一次引导在对话里是一张卡，按服务端此刻的状态画——
//   在问：这一轮要答的题，版式同提问卡（卡头题目 + 翻页器、通栏选项、右下「跳过」「下一步 / 提交」，⌘ / Ctrl + 回车同主按钮）；
//        每题按 input 画（十一种：choice / multi 可服务端搜索、amounts〔可搜索加行、可为 0〕、split、file、month、date、time、number、text、questions 子题）；
//        题下灰字是服务端给的事实行（facts）；还在读（wait）出一行「正在加载」并过一会儿再取（退避，有上限）；被规则退回的题停在那一题、说明原因；
//        已定的项收在「已定 n 项」里。
//   复核 / 办完：见 guide-review.tsx（G2）。
//   取消 / 失效（F5）：只剩一行（业务名 · 已取消 / 已失效），不能点。
// 卡上交回的值都是服务端给的选项码、对话文件 id 或用户在卡上填的数，按键交回（answerGuide），页面不拼、不改、不经模型。
// 标题、选项名、事实行由服务端按本页语言翻好。只用标准组件（问卷部件、按钮、徽章、折叠）与 globals.css 的令牌。

import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { type FC, type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Shortcut } from "@/components/shortcut";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import {
  Questionnaire, QuestionnaireActions, QuestionnaireDescription, QuestionnaireItem, QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { Skeleton } from "@/components/ui/skeleton";
import { ask, guide, steps, t } from "@/i18n";
import { businessName, noteRunView } from "@/lib/business";
import { reportCaught } from "@/lib/caught";
import { shownError } from "@/lib/frappe";
import type { AmountRow } from "@/lib/guide-inputs";
import {
  answerGuide, answeredOk, cancelGuide, refusedText, firstPosition, type GuideDecided, type GuideOption, type GuideQuestion, type GuideView, initialValue, isClosed, isEmpty,
  isWaiting, searchGuide, splitTotal, toSend, viewGuide, WAIT_TRIES, waitDelay,
} from "@/lib/guide-run";
import type { GuideRun } from "@/lib/managed-agents/reducer";
import {
  AmountsField, ChoiceField, FileField, SearchAmountsField, SplitField, SubQuestionsField, ValueField,
} from "./guide-inputs";
import { CardFoot, CardFrame, CardLine, QuestionFrame } from "./card-frame";
import { GuideCardBody, guideCardOf } from "./guide-card";
import { GuideOperatorHead, operatorTitle } from "./guide-operator";
import { GuideResultCard, GuideReview, GuideUploadsLine, runAttrs } from "./guide-review";

type Values = Record<string, unknown>;

/** 卡里加载占位（ccs：卡里加载用骨架，不写字、不转圈）：三条字高的条。 */
const GuideSkeleton: FC = () => (
  <div className="cc-guide-body flex min-w-0 flex-col gap-(--cc-space-8)" data-slot="cc-guide-wait" aria-busy>
    <Skeleton /><Skeleton /><Skeleton className="w-1/2" />
  </div>
);
type Act = (call: () => Promise<GuideView>) => void;

/** 一题的输入（按 input 选组件）。 */
const QuestionBody: FC<{
  run: string; sessionId: string | null; question: GuideQuestion; all: readonly GuideQuestion[]; values: Values; disabled: boolean;
  onChange: (value: unknown) => void;
}> = ({ run, sessionId, question: q, all, values, disabled, onChange }) => {
  const value = values[q.key];
  const search = q.search ? (txt: string, start: number) => searchGuide(run, q.key, txt, start) : undefined;
  switch (q.input) {
    case "choice":
    case "multi":
      return (
        <ChoiceField options={q.options ?? []} more={q.more} search={search} multi={q.input === "multi"} label={q.title} disabled={disabled}
          value={value as string | string[] | undefined} recommended={q.tier === "how" && typeof q.preselected === "string" ? q.preselected : undefined}
          onChange={onChange} />
      );
    case "amounts":
      return search || q.more ? (
        <SearchAmountsField options={q.options ?? []} more={q.more} search={search} rows={(value as AmountRow[]) ?? []} zero={q.zero}
          label={q.title} disabled={disabled} onChange={onChange} />
      ) : (
        <AmountsField options={q.options ?? []} rows={(value as AmountRow[]) ?? []} zero={q.zero} disabled={disabled} onChange={onChange} />
      );
    case "split":
      return <SplitField options={q.options ?? []} rows={(value as AmountRow[]) ?? []} total={splitTotal(q, all, values)} disabled={disabled} onChange={onChange} />;
    case "file":
      return <FileField sessionId={sessionId} value={(value as string[]) ?? []} label={q.title} disabled={disabled} onChange={onChange} />;
    case "questions":
      return <SubQuestionsField questions={q.questions ?? []} value={(value as Values) ?? {}} disabled={disabled} onChange={onChange} />;
    default:
      return (
        <div className="cc-guide-body">
          <ValueField kind={q.input as "month" | "date" | "time" | "number" | "text"} label={q.title} disabled={disabled}
            value={typeof value === "string" ? value : ""} onChange={onChange} />
        </div>
      );
  }
};

/** 已定项改值（11-16 引擎定：选项走 guide_search 取第一页，decided 不带选项）：选项类用单选，其余按输入方式一格。 */
const DecidedEdit: FC<{ run: string; item: GuideDecided; value: unknown; disabled: boolean; onChange: (value: unknown) => void }> = ({
  run, item, value, disabled, onChange,
}) => {
  const [page, setPage] = useState<{ options: GuideOption[]; more: boolean } | null>(null);
  const choice = !item.input || item.input === "choice";
  useEffect(() => {
    if (!choice) return;
    let alive = true;
    void searchGuide(run, item.key, "", 0).then((found) => alive && setPage(found), (err) => reportCaught(err, "guide-decided"));
    return () => { alive = false; };
  }, [run, item.key, choice]);
  if (!choice) {
    return (
      <ValueField kind={item.input as "month" | "date" | "time" | "number" | "text"} label={item.title} disabled={disabled}
        value={typeof value === "string" ? value : typeof item.value === "string" ? item.value : ""} onChange={onChange} />
    );
  }
  if (!page) return <GuideSkeleton />;
  return (
    <ChoiceField options={page.options} more={page.more} search={(txt, start) => searchGuide(run, item.key, txt, start)} multi={false}
      label={item.title} disabled={disabled} value={(value as string | undefined) ?? (typeof item.value === "string" ? item.value : undefined)}
      onChange={onChange} />
  );
};

/** 「已定 n 项」：后台能定的、不用问的（照服务端给的显示名），收在折叠里；每项可点「编辑」改，改了随这一轮一起交。 */
const Decided: FC<{
  run: string; items: readonly GuideDecided[]; open?: boolean; values: Values; disabled: boolean; onChange: (key: string, value: unknown) => void;
}> = ({ run, items, open, values, disabled, onChange }) => {
  const [shown, setShown] = useState(!!open);
  const [editing, setEditing] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <Collapsible open={shown} onOpenChange={setShown} data-slot="cc-guide-decided">
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" type="button">
          {shown ? <ChevronDownIcon /> : <ChevronRightIcon />}
          {guide.decided(items.length)}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        {items.map((item) => (
          <div key={item.key} data-key={item.key} className="flex min-w-0 flex-col">
            {/* 整行是按钮（同复核卡能回到某一步的那一行），点了展开改值 */}
            <Button type="button" variant="ghost" size="sm" className="w-full justify-start" aria-label={`${t("edit")} ${item.title}`}
              aria-expanded={editing === item.key} disabled={disabled} data-slot="cc-guide-decided-edit"
              onClick={() => setEditing(editing === item.key ? null : item.key)}>
              <span className="min-w-0 flex-1 text-start text-(--cc-ink-3)">{item.title}</span>
              <span className="text-end">{item.value_label ?? (typeof item.value === "string" ? item.value : "")}</span>
              {editing === item.key ? <ChevronDownIcon /> : <ChevronRightIcon />}
            </Button>
            {editing === item.key && (
              <DecidedEdit run={run} item={item} value={values[item.key]} disabled={disabled} onChange={(value) => onChange(item.key, value)} />
            )}
          </div>
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
};

/** 在问的这一轮。 */
const Asking: FC<{ view: GuideView; sessionId: string | null; busy: boolean; failure: string; act: Act; decidedOpen?: boolean }> = ({
  view, sessionId, busy, failure, act, decidedOpen,
}) => {
  const card = view.card!;
  const questions = card.questions;
  const [pos, setPos] = useState(() => firstPosition(questions, card.returned));
  const [values, setValues] = useState<Values>(() => Object.fromEntries(questions.map((q) => [q.key, initialValue(q)])));
  const place = Math.min(pos, Math.max(questions.length - 1, 0));
  const q = questions[place];
  const all: Values = { ...(card.answers ?? {}), ...values };
  const ok = (question: GuideQuestion) => answeredOk(question, values[question.key], question.input === "split" ? splitTotal(question, questions, all) : "");
  const skippable = (question: GuideQuestion) => !question.required && isEmpty(values[question.key]);
  const last = place >= questions.length - 1;
  const ready = questions.every((question) => ok(question) || skippable(question));
  const [decidedValues, setDecided] = useState<Values>({});
  const submit = () => {
    const given: Values = {};
    for (const question of questions) if (ok(question)) given[question.key] = toSend(question, values[question.key]);
    // 已定项只交改过的（同第 1 期卡）
    for (const item of card.decided) {
      const changed = decidedValues[item.key];
      if (!isEmpty(changed) && changed !== item.value) given[item.key] = changed;
    }
    act(() => answerGuide(view.run, given, view.version));
  };
  const primary = () => (last ? ready && submit() : setPos(place + 1));
  const skip = () => {
    setValues((s) => ({ ...s, [q.key]: undefined }));
    if (last) submit(); else setPos(place + 1);
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !busy) {
      e.preventDefault();
      primary();
    }
  };
  const refused = refusedText(view, q?.key ?? "");
  const returned = card.returned?.[q?.key ?? ""];
  if (!q) {
    // 没有要问的（全都定了、等服务端生成草稿）：只留已定项
    return (
      <Decided run={view.run} items={card.decided} open={decidedOpen} values={decidedValues} disabled={busy}
        onChange={(key, value) => setDecided((s) => ({ ...s, [key]: value }))} />
    );
  }
  const multi = q.input === "multi" || q.input === "file";
  return (
    <div className="cc-askq-wrap" onKeyDown={onKey}>
      <Questionnaire noValidate onSubmit={(e) => { e.preventDefault(); primary(); }} data-phase="ask">
        <QuestionnaireItem key={q.key} name={q.key} multiple={multi} data-slot="cc-guide-question" data-key={q.key} data-input={q.input}>
          <QuestionnaireTitle data-slot="cc-askq-question">{q.title || businessName(view.business)}</QuestionnaireTitle>
          {questions.length > 1 && (
            <div className="cc-ui-questionnaire-pager" data-slot="cc-askq-pager">
              <Button type="button" variant="ghost" size="icon-xs" aria-label={ask.prev} disabled={place === 0} onClick={() => setPos(place - 1)}>
                <ChevronLeftIcon />
              </Button>
              <span className="cc-askq-nth cc-ui-questionnaire-progress">{ask.nth(place + 1, questions.length)}</span>
              <Button type="button" variant="ghost" size="icon-xs" aria-label={ask.after} disabled={last} onClick={() => setPos(place + 1)}>
                <ChevronRightIcon />
              </Button>
            </div>
          )}
          {(q.facts ?? []).map((fact) => <QuestionnaireDescription key={fact} data-slot="cc-guide-fact">{fact}</QuestionnaireDescription>)}

          {(returned || refused) && (
            <Alert variant="destructive" data-slot="cc-guide-returned">
              <AlertDescription>{returned?.text || refused}</AlertDescription>
            </Alert>
          )}
          {/* 还在读（服务端 wait）：题下那行事实就是服务端的说法，选项处只放骨架占位，不再写「正在加载」（验收 B8：同一件事说两遍） */}
          {q.wait && !(q.options ?? []).length ? (
            <GuideSkeleton />
          ) : (
            <QuestionBody run={view.run} sessionId={sessionId} question={q} all={questions} values={all} disabled={busy}
              onChange={(value) => setValues((s) => ({ ...s, [q.key]: value }))} />
          )}
        </QuestionnaireItem>
        <Decided run={view.run} items={card.decided} open={decidedOpen} values={decidedValues} disabled={busy}
          onChange={(key, value) => setDecided((s) => ({ ...s, [key]: value }))} />
        {failure && (
          <Alert variant="destructive" data-slot="cc-ask-failure">
            <AlertDescription className="line-clamp-(--cc-alert-lines)">{failure}</AlertDescription>
          </Alert>
        )}
        <QuestionnaireActions>
          {skippable(q) && (
            <Button type="button" variant="secondary" size="xs" className="col-start-2" data-slot="cc-askq-skip" disabled={busy} onClick={skip}>
              {ask.skip}
            </Button>
          )}
          {/* 不用 type=submit：问卷部件在提交时会先把焦点跳到空着的输入格（分摊里没用的方式本来就空着），不交 */}
          <Button type="button" size="xs" className="col-start-3" data-slot="cc-askq-next" onClick={primary} disabled={busy || (last ? !ready : !(ok(q) || skippable(q)))}>
            {last ? ask.submit : ask.next}
            <Shortcut keys="Ctrl↵" />
          </Button>
        </QuestionnaireActions>
      </Questionnaire>
    </div>
  );
};

/** 卡的外框（同提问卡，共用骨架 card-frame.tsx 的 QuestionFrame）。 */
const Frame: FC<{ view: GuideView; children: React.ReactNode; head?: React.ReactNode; headBody?: React.ReactNode }> = ({ view, children, head, headBody }) => (
  <QuestionFrame slot="cc-guide-run" attrs={runAttrs(view)} head={head} headBody={headBody}>
    {children}
  </QuestionFrame>
);

/** 取消 / 失效（F5）：一行，不能点（共用骨架的一行态）。 */
export const GuideClosed: FC<{ view: GuideView }> = ({ view }) => (
  <CardLine slot="cc-guide-run" phase="cancelled"
    attrs={{ "data-run": view.run, "data-status": view.stale ? "expired" : view.status, "data-ended": "", "aria-disabled": "true" }}
    title={businessName(view.business)} answer={view.status === "cancelled" ? steps.cancelled : guide.expired} />
);

/** 按单据的题卡（documents 源的题卡段）：就是第 1 期那张卡（同一组件、同一种答案 [{key, value}]），交给这次引导。代录时卡头
 *  （写入 公司 · 账套 · 电脑）与登录 / 事实行在同一张卡的上半截，题目接在下面（一张卡，不另起一张）。 */
const DocumentsCard: FC<{ view: GuideView; busy: boolean; failure: string; act: Act }> = ({ view, busy, failure, act }) => {
  const title = operatorTitle(view);
  const body = (
    <GuideCardBody card={guideCardOf({ card: view.card })} company={view.company} phase="ask" busy={busy} framed={!title}
      onSubmit={(answers) => act(() => answerGuide(view.run, { answers }, view.version))} />
  );
  if (title) {
    return (
      <QuestionFrame slot="cc-guide-run" attrs={{ ...runAttrs(view), "data-stage": "asking" }} head={title}
        headBody={<GuideOperatorHead view={view} busy={busy} act={act} />}>
        {body}
        <CardFoot buttons={[]} failure={failure} />
      </QuestionFrame>
    );
  }
  return (
    <div data-slot="cc-guide-run" data-run={view.run} data-status={view.status} data-business={view.business} data-stage="asking"
      className="flex min-w-0 flex-col gap-(--cc-turn-gap)">
      {view.card?.facts ? <CardFrame title={businessName(view.business)}><GuideOperatorHead view={view} busy={busy} act={act} /></CardFrame> : null}
      {body}
      <CardFoot buttons={[]} failure={failure} />
    </div>
  );
};

/** 一张卡此刻的样子（只画，不取）：展示页与对话页共用。卡片骨架一套（card-frame.tsx）：问答是问卷版式，复核 / 结果 / 识别中是头体脚。 */
export const GuideRunView: FC<{
  view: GuideView; sessionId: string | null; busy: boolean; failure: string; act: Act; decidedOpen?: boolean;
}> = ({ view, sessionId, busy, failure, act, decidedOpen }) => {
  if (isClosed(view)) return <GuideClosed view={view} />;
  const props = { view, sessionId, busy, failure, act };
  const documents = view.source === "documents";
  const head = documents ? (
    <>
      <GuideOperatorHead view={view} busy={busy} act={act} />
      {view.uploads && view.stage !== "reading" ? <GuideUploadsLine uploads={view.uploads} /> : null}
    </>
  ) : null;
  if (view.result) return <GuideResultCard {...props} head={documents ? <GuideOperatorHead view={view} busy={busy} act={act} /> : null} />;
  if (documents && view.stage === "asking" && view.card) return <DocumentsCard view={view} busy={busy} failure={failure} act={act} />;
  if (documents && view.stage === "reading" && view.uploads) {
    return (
      <CardFrame title={operatorTitle(view) ?? businessName(view.business)} slot="cc-guide-run" attrs={runAttrs(view)} failure={failure}>
        <GuideOperatorHead view={view} busy={busy} act={act} />
        <GuideUploadsLine uploads={view.uploads} />
      </CardFrame>
    );
  }
  if (view.review && (documents ? view.stage === "review" : view.status === "reviewing")) return <GuideReview {...props} head={head} />;
  if (view.card && (view.status === "asking" || (documents && view.stage === "files"))) {
    const title = documents ? operatorTitle(view) : null;
    return (
      <Frame view={view} head={title} headBody={title ? <GuideOperatorHead view={view} busy={busy} act={act} /> : null}>
        <Asking key={`${view.run}:${view.version}`} view={view} sessionId={sessionId} busy={busy} failure={failure} act={act} decidedOpen={decidedOpen} />
      </Frame>
    );
  }
  return null;
};

/** 对话里的一次引导：按事件的版本取 view；在等读取时退避再取；作答 / 确定 / 取消后用服务端回的那份。 */
export const GuideRunCard: FC<{ run: GuideRun; sessionId: string | null }> = ({ run, sessionId }) => {
  const [view, setView] = useState<GuideView | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const tries = useRef(0);
  const load = useCallback(() => {
    void viewGuide(run.run).then(setView, (err) => reportCaught(err, "guide-view"));
  }, [run.run]);
  useEffect(load, [load, run.version]);
  useEffect(() => {
    if (!isWaiting(view) || tries.current >= WAIT_TRIES) return;
    const timer = window.setTimeout(() => { tries.current += 1; load(); }, waitDelay(tries.current));
    return () => window.clearTimeout(timer);
  }, [view, load]);
  useEffect(() => { tries.current = 0; }, [view?.version, view?.status]);
  // 本页记的运行状态跟上（菜单在引导开着时不出，推送断开时也按卡上这一版判）
  useEffect(() => { if (view && sessionId) noteRunView(sessionId, view); }, [view, sessionId]);
  const act: Act = useMemo(() => (call) => {
    setBusy(true);
    setFailure("");
    void call()
      .then(setView, (err) => { reportCaught(err, "guide-act"); setFailure(shownError(err)); })
      .finally(() => setBusy(false));
  }, []);
  if (!view) return null;
  return <GuideRunView view={view} sessionId={sessionId} busy={busy} failure={failure} act={act} />;
};
