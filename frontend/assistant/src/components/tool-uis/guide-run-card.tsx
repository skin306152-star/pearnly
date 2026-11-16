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
import { Item, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import {
  Questionnaire, QuestionnaireActions, QuestionnaireDescription, QuestionnaireItem, QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { Spinner } from "@/components/ui/spinner";
import { ask, guide, steps, t, ui } from "@/i18n";
import { businessName } from "@/lib/business";
import { reportCaught } from "@/lib/caught";
import { shownError } from "@/lib/frappe";
import type { AmountRow } from "@/lib/guide-inputs";
import {
  answerGuide, answeredOk, cancelGuide, firstPosition, type GuideDecided, type GuideQuestion, type GuideView, initialValue, isClosed, isEmpty,
  isWaiting, searchGuide, splitTotal, toSend, viewGuide, WAIT_TRIES, waitDelay,
} from "@/lib/guide-run";
import type { GuideRun } from "@/lib/managed-agents/reducer";
import {
  AmountsField, ChoiceField, FileField, SearchAmountsField, SplitField, SubQuestionsField, ValueField,
} from "./guide-inputs";
import { GuideCardBody, guideCardOf } from "./guide-card";
import { GuideResultCard, GuideReview, GuideUploadsLine } from "./guide-review";

type Values = Record<string, unknown>;
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

/** 「已定 n 项」：后台能定的、不用问的（照服务端给的显示名），收在折叠里。 */
const Decided: FC<{ items: readonly GuideDecided[]; open?: boolean }> = ({ items, open }) => {
  const [shown, setShown] = useState(!!open);
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
          <Item key={item.key} size="sm" data-key={item.key}>
            <ItemContent>
              <ItemTitle>{item.title}</ItemTitle>
              <ItemDescription>{item.value_label ?? (typeof item.value === "string" ? item.value : "")}</ItemDescription>
            </ItemContent>
          </Item>
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
  const submit = () => {
    const given: Values = {};
    for (const question of questions) if (ok(question)) given[question.key] = toSend(question, values[question.key]);
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
  const refused = view.refused?.[q?.key ?? ""];
  const returned = card.returned?.[q?.key ?? ""];
  if (!q) {
    // 没有要问的（全都定了、等服务端生成草稿）：只留已定项
    return <Decided items={card.decided} open={decidedOpen} />;
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
          {q.wait && (
            <QuestionnaireDescription data-slot="cc-guide-wait" className="flex items-center gap-(--cc-space-8)">
              <Spinner />
              {ui.loading}
            </QuestionnaireDescription>
          )}
          {(returned || refused) && (
            <Alert variant="destructive" data-slot="cc-guide-returned">
              <AlertDescription>{returned?.text || refused}</AlertDescription>
            </Alert>
          )}
          <QuestionBody run={view.run} sessionId={sessionId} question={q} all={questions} values={all} disabled={busy}
            onChange={(value) => setValues((s) => ({ ...s, [q.key]: value }))} />
        </QuestionnaireItem>
        <Decided items={card.decided} open={decidedOpen} />
        {failure && (
          <Alert variant="destructive" data-slot="cc-ask-failure">
            <AlertDescription className="line-clamp-(--cc-alert-lines)">{failure}</AlertDescription>
          </Alert>
        )}
        <QuestionnaireActions>
          <Button type="button" variant="ghost" size="xs" className="col-start-1" data-slot="cc-guide-cancel" disabled={busy}
            onClick={() => act(() => cancelGuide(view.run))}>
            {t("cancel")}
          </Button>
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

/** 卡的外框（同提问卡）。 */
const Frame: FC<{ view: GuideView; children: React.ReactNode }> = ({ view, children }) => (
  <div data-slot="cc-guide-run" data-run={view.run} data-status={view.status} data-business={view.business}
    className="cc-steps-card cc-ui-questionnaire-card">
    {children}
  </div>
);

/** 取消 / 失效（F5）：一行，不能点。 */
export const GuideClosed: FC<{ view: GuideView }> = ({ view }) => (
  <div data-slot="cc-guide-run" data-run={view.run} data-status={view.stale ? "expired" : view.status} data-ended
    className="cc-steps-card cc-ui-questionnaire-card" aria-disabled>
    <div className="aui-step cc-ask" data-phase="cancelled">
      <div className="cc-ask-q">{businessName(view.business)}</div>
      <div data-slot="cc-ask-answer" className="cc-ask-answer">{view.status === "cancelled" ? steps.cancelled : guide.expired}</div>
    </div>
  </div>
);

/** 按单据的题卡（documents 源的题卡段）：就是第 1 期那张卡（同一组件、同一种答案 [{key, value}]），交给这次引导。 */
const DocumentsCard: FC<{ view: GuideView; busy: boolean; failure: string; act: Act }> = ({ view, busy, failure, act }) => (
  <div data-slot="cc-guide-run" data-run={view.run} data-status={view.status} data-business={view.business} data-stage="asking">
    <GuideCardBody card={guideCardOf({ card: view.card })} company={view.company} phase="ask" busy={busy}
      onSubmit={(answers) => act(() => answerGuide(view.run, { answers }, view.version))} />
    {failure && (
      <Alert variant="destructive" data-slot="cc-ask-failure">
        <AlertDescription className="line-clamp-(--cc-alert-lines)">{failure}</AlertDescription>
      </Alert>
    )}
  </div>
);

/** 一张卡此刻的样子（只画，不取）：展示页与对话页共用。 */
export const GuideRunView: FC<{
  view: GuideView; sessionId: string | null; busy: boolean; failure: string; act: Act; decidedOpen?: boolean;
}> = ({ view, sessionId, busy, failure, act, decidedOpen }) => {
  if (isClosed(view)) return <GuideClosed view={view} />;
  const props = { view, sessionId, busy, failure, act };
  if (view.result) return <Frame view={view}><GuideResultCard {...props} /></Frame>;
  if (view.source === "documents") {
    if (view.stage === "asking" && view.card) return <DocumentsCard view={view} busy={busy} failure={failure} act={act} />;
    if (view.stage === "reading" && view.uploads) return <Frame view={view}><GuideUploadsLine uploads={view.uploads} /></Frame>;
    if (view.stage === "review" && view.review) {
      return (
        <Frame view={view}>
          {view.uploads ? <GuideUploadsLine uploads={view.uploads} /> : null}
          <GuideReview {...props} />
        </Frame>
      );
    }
  }
  if (view.status === "asking" && view.card) {
    return (
      <Frame view={view}>
        <Asking key={`${view.run}:${view.version}`} view={view} sessionId={sessionId} busy={busy} failure={failure} act={act} decidedOpen={decidedOpen} />
      </Frame>
    );
  }
  if (view.status === "reviewing" && view.review) return <Frame view={view}><GuideReview {...props} /></Frame>;
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
