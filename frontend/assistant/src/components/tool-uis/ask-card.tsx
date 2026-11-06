// 通用提问卡片（工具 ask_user，用户 11-05：1:1 照 Claude Code 的原生提问卡片；组件固定，问什么、几道题、什么选项由 CC 决定）。
// 一张卡一次只问一题，几题在同一张卡里翻页：
//   - 头部一行：左边问题，右边「‹ 第 1 项，共 4 项 ›」（不止一题时），左右箭头来回翻；
//   - 选项一行一个：左边单选圆点（多选题是复选框），右边两行字（选项名；浅色小字说明依据），行间分隔线；推荐项排第一、名字带「(推荐)」、
//     默认选中；最后一项固定「其他」：选了可以自己输入（题目带 search 时是服务端可搜索的选择框，带 choices 时在给定的值里筛）；
//   - 底部靠右「跳过」（次要）与「下一步 ⌘↵」（主要），最后一题主按钮是「提交 ⌘↵」（快捷键提示经共用的 Shortcut：Windows 写 Ctrl↵、触屏不写），⌘ / Ctrl + 回车同主按钮；
//   - 卡片下方橙色小点 +「需要您的输入」（等人的时候）。
// 点完（交回了答案）：同「允许」卡片点完只剩问话与一行淡色所选；没答就没了写「已取消」。
// 「跳过」= 这一题不定（交回时标 skipped），依赖它的单这次不写、留在对话里，其余照写（由 CC / 服务端处理）。
// 三处复用：ask_user 工具卡片（AskUserCard，答案交回这段会话）、代录中途那一张（operator-choose.tsx）、对话末尾的待处理框（pending.tsx），
// 后两处页面自己出题（ask-shared.ts 的 itemQuestion）。样式只用 globals.css 的 .cc-askq-* 与变量。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { type FC, type KeyboardEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { Shortcut } from "@/components/shortcut";
import { Marquee } from "@/components/ui/marquee";
import { ask, co, op, steps, t } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught, withReport } from "@/lib/caught";
import { useIsMobile } from "@/hooks/use-mobile";
import { approvalCard, type Gates } from "@/lib/approval-card";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { useToolLabels } from "@/lib/tool-labels";
import {
  answered,
  answeredLine,
  askActions,
  type AskQuestion,
  confirmAnswers,
  type AskResult,
  type AskState,
  initialStates,
  questionsOf,
  resultFrom,
  toggle,
  toggleOther,
} from "./ask-shared";
import type { CardPhase } from "./card-phase";
import { useSessionId } from "./operator-card";
import { type ChoiceOption, EXPENSE_CODE, NEW_CODE } from "./operator-words";

/** 「其他」展开后先列几条（其余「显示更多」：本页已取到的先放出，不够再向服务端取下一页）。 */
const OTHER_ROWS = 5;

/** 「其他」：打字；题目给了可选值（choices）就在里面筛，带 search 就去服务端搜（一页一页取），点一条就是它。 */
const OtherInput: FC<{ question: AskQuestion; state: AskState; onChange: (other: string, code?: string) => void }> = ({ question, state, onChange }) => {
  const [txt, setTxt] = useState(state.other ?? "");
  const [found, setFound] = useState<{ options: ChoiceOption[]; more: boolean } | null>(null);
  const [shown, setShown] = useState(OTHER_ROWS);
  const search = question.search;
  // 题目每次重画都是新对象：按内容认，不然打字后的计时会被反复重来、一直搜不出来
  const searchKey = search ? JSON.stringify(search) : "";
  useEffect(() => {
    if (!search) return;
    let alive = true;
    const timer = window.setTimeout(
      () => void askActions.search(search, txt.trim(), 0).then((page) => alive && setFound(page), withReport(undefined, "ask-search")),
      txt ? 250 : 0,
    );
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [searchKey, txt]);
  const more = () =>
    void askActions.search(search!, txt.trim(), found?.options.length ?? 0).then(
      (page) => setFound((before) => ({ options: [...(before?.options ?? []), ...page.options], more: page.more })),
      withReport(undefined, "ask-search"),
    );
  const needle = txt.trim().toLocaleLowerCase();
  const local = (question.choices ?? []).filter((c) => !needle || `${c.code} ${c.name}`.toLocaleLowerCase().includes(needle));
  const rows = search ? (found?.options ?? []) : local;
  const listed = rows.slice(0, shown);
  const hasMore = rows.length > shown || (!!search && !!found?.more);
  const showMore = () => {
    if (search && rows.length <= shown + OTHER_ROWS && found?.more) more();
    setShown((n) => n + OTHER_ROWS);
  };
  const listing = !!search || !!question.choices?.length;
  return (
    <div className="cc-askq-other" data-slot="cc-askq-other">
      <input
        className="cc-input w-full"
        autoFocus
        value={txt}
        aria-label={ask.other}
        placeholder={listing ? t("search") : undefined}
        onChange={(e) => {
          setTxt(e.target.value);
          setShown(OTHER_ROWS);
          onChange(e.target.value);
        }}
      />
      {listing && (
        <div className="cc-askq-other-list" role="listbox" aria-label={ask.other}>
          {listed.map((c) => {
            const label = c.code === NEW_CODE ? op.option.new! : c.code === EXPENSE_CODE ? op.option.expense! : [c.code, c.name].filter(Boolean).join(" ");
            const on = state.otherCode === c.code;
            return (
              <button key={c.code} type="button" role="option" aria-selected={on} className="cc-menu-item" data-slot="cc-askq-choice" onClick={() => onChange(label, c.code)}>
                <Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>
                {on && <CheckIcon />}
              </button>
            );
          })}
          {search && found !== null && !rows.length && <div className="cc-menu-label">{t("noResults")}</div>}
          {hasMore && (
            <button type="button" className="cc-show-more" data-slot="cc-askq-more" onClick={showMore}>
              {co.showMore}
            </button>
          )}
        </div>
      )}
    </div>
  );
};

/** 一个选项一行：圆点 / 复选框 + 两行字。 */
const OptionRow: FC<{ multi: boolean; on: boolean; label: string; description?: string | undefined; slot: string; onClick: () => void }> = ({
  multi,
  on,
  label,
  description,
  slot,
  onClick,
}) => (
  <button type="button" role={multi ? "checkbox" : "radio"} aria-checked={on} className="cc-askq-opt" data-slot={slot} onClick={onClick}>
    <span className="cc-askq-mark" data-multi={multi ? "" : undefined} data-on={on ? "" : undefined} aria-hidden="true">
      {multi && on && <CheckIcon />}
    </span>
    <span className="cc-askq-text">
      <span className="cc-askq-label">{label}</span>
      {description && <span className="cc-askq-desc">{description}</span>}
    </span>
  </button>
);

/** 卡片本体。phase：ask 能答；answered 只剩问话与一行所选（result）；cancelled 写「已取消」。framed：自己带一层框与「需要您的输入」
 *  （待处理列表里嵌着用时不带）。 */
export const AskCard: FC<{
  questions: readonly AskQuestion[];
  phase: CardPhase;
  result?: AskResult | null | undefined;
  busy: boolean;
  failure: string;
  onSubmit: (result: AskResult) => void;
  framed?: boolean;
  /** 卡片下方「需要您的输入」：不给时跟着 framed（嵌在列表里不画）；ask_user 在电脑上由对话的工作状态行画，这里不画。 */
  status?: boolean;
  slot?: string;
}> = ({ questions, phase, result, busy, failure, onSubmit, framed = true, status = framed, slot = "cc-askq" }) => {
  const [at, setAt] = useState(0);
  const [states, setStates] = useState<AskState[]>(() => initialStates(questions));
  useEffect(() => setStates((before) => (before.length === questions.length ? before : initialStates(questions))), [questions]);
  const q = questions[Math.min(at, questions.length - 1)];
  const state = states[at] ?? { labels: [], other: null };
  const last = at >= questions.length - 1;
  const set = (next: AskState) => setStates((all) => all.map((s, i) => (i === at ? next : s)));
  const forward = (next: AskState[]) => {
    if (last) onSubmit(resultFrom(questions, next));
    else setAt(at + 1);
  };
  const primary = () => forward(states);
  const skip = () => {
    const next = states.map((s, i) => (i === at ? { labels: [], other: null, skipped: true } : s));
    setStates(next);
    forward(next);
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !busy && phase === "ask") {
      e.preventDefault();
      primary();
    }
  };
  const frame = (body: ReactNode) =>
    framed ? <div className="cc-steps-card" data-slot={slot}>{body}</div> : <div data-slot={slot}>{body}</div>;

  if (!q) return null;
  if (phase !== "ask") {
    return frame(
      <div className="aui-step cc-ask" data-phase={phase}>
        <div className="cc-ask-q">{questions.length === 1 ? q.question : questions.map((x) => x.header || x.question).join(" · ")}</div>
        <div data-slot="cc-ask-answer" className="cc-ask-answer">
          {phase === "answered" ? answeredLine(result) : steps.cancelled}
        </div>
      </div>,
    );
  }
  const multi = !!q.multiSelect;
  return (
    <div className="cc-askq-wrap" onKeyDown={onKey}>
      {frame(
        <div className="cc-askq" data-phase="ask">
          <div className="cc-askq-head">
            <div className="cc-askq-q" data-slot="cc-askq-question">{q.question}</div>
            {questions.length > 1 && (
              <div className="cc-askq-pager" data-slot="cc-askq-pager">
                <button type="button" className="cc-row-btn cc-tap" aria-label={ask.prev} disabled={at === 0} onClick={() => setAt(at - 1)}>
                  <ChevronLeftIcon />
                </button>
                <span className="cc-askq-nth">{ask.nth(at + 1, questions.length)}</span>
                <button type="button" className="cc-row-btn cc-tap" aria-label={ask.after} disabled={last} onClick={() => setAt(at + 1)}>
                  <ChevronRightIcon />
                </button>
              </div>
            )}
          </div>
          <div className="cc-askq-options" role={multi ? "group" : "radiogroup"} aria-label={q.question}>
            {q.options.map((o) => (
              <OptionRow key={o.label} slot="cc-askq-option" multi={multi} on={state.labels.includes(o.label)} label={o.label} description={o.description}
                onClick={() => set(toggle(q, state, o.label))} />
            ))}
            <OptionRow slot="cc-askq-other-option" multi={multi} on={state.other !== null} label={ask.other} onClick={() => set(toggleOther(q, state))} />
            {state.other !== null && (
              <OtherInput key={at} question={q} state={state} onChange={(other, code) => set({ ...state, other, otherCode: code, skipped: false })} />
            )}
          </div>
          {failure && (
            <div role="alert" data-slot="cc-ask-failure" className="cc-alert">
              <p className="line-clamp-(--cc-alert-lines)">{failure}</p>
            </div>
          )}
          <div className="cc-askq-foot">
            <button type="button" className="cc-card-btn" data-slot="cc-askq-skip" disabled={busy} onClick={skip}>
              {ask.skip}
            </button>
            <button type="button" className="cc-card-btn" data-primary="" data-slot="cc-askq-next" disabled={busy || !answered(state)} onClick={primary}>
              {last ? ask.submit : ask.next}
              <Shortcut keys="Ctrl↵" />
            </button>
          </div>
        </div>,
      )}
      {status && (
        <div className="cc-askq-status" data-slot="cc-askq-status">
          <span className="cc-needs-input-dot" aria-hidden="true" />
          <span className="cc-needs-input">{t("needsInput")}</span>
        </div>
      )}
    </div>
  );
};

/** ask_user 工具卡片此刻的样子（同「允许」卡片的判定，lib/approval-card.ts 的 approvalCard：服务端声明它要等人作答）：等作答 = ask；
 *  有结果 / 执行记录做完 = 点完；没答就没了 = 已取消。还没登记成等人作答（页面刚提醒服务端执行、门还没回来）回 null：卡片照样画、不能点。 */
export function askPhase(part: CardPart, gates: Gates): CardPhase | null {
  const card = approvalCard(part, gates);
  if (!card) return null;
  return card.phase === "answered" ? "answered" : card.phase;
}

/** 工具结果的正文 → 交回的答（读不出回 null）。 */
export function askResultOf(result: unknown): AskResult | null {
  if (result === undefined || result === null) return null;
  const text = typeof result === "object" && "text" in (result as object) ? String((result as { text: unknown }).text) : String(result);
  if (!/^\s*\{/.test(text)) return null;
  try {
    const parsed = JSON.parse(text) as AskResult;
    return Array.isArray(parsed?.answers) ? parsed : null;
  } catch (caughtError) {
    reportCaught(caughtError, "ask-result");
    return null;
  }
}

/** 工具 ask_user 的卡片（W2：工具声明要等人作答，调用登记成 awaiting_approval，同「允许」卡片）：「提交」= 对话的「允许」带上作答
 *  （session-controller 的 respondToApproval → confirm 的 answers），本页先定下；服务端核作答对不上题目会回「请求无效」，门放回来、
 *  卡片上方写原因，可以重答。电脑上「需要您的输入」由对话的工作状态行画（thread.tsx 的 RunIndicator，在卡片下方）；手机上那一行
 *  不画（等回答时它让给允许卡的浮层，ask_user 不浮），卡片自己带。 */
export const AskUserCard: ToolCallMessagePartComponent = (props) => {
  const part = props as unknown as CardPart & { approval?: { id: string; failure?: string; sending?: boolean } };
  // 入参按内容认（每次投影都是新对象，不然卡片里「其他」的搜索会被反复重来）
  const argsKey = JSON.stringify(props.args ?? null);
  const questions = useMemo(() => questionsOf(JSON.parse(argsKey)), [argsKey]);
  const labels = useToolLabels();
  const phone = useIsMobile();
  const sessionId = useSessionId();
  const [sent, setSent] = useState<AskResult | null>(null);
  const phase = askPhase(part, labels);
  const submit = (result: AskResult) => {
    if (!sessionId || !part.approval) return;
    setSent(result);
    getSessionController(sessionId).respondToApproval(part.approval.id, true, undefined, confirmAnswers(result));
  };
  // 门放回来了（作答没送到 / 对不上题目）：本页刚交的不算，卡片回到能答
  const failure = part.approval?.failure ?? "";
  const shown: CardPhase = phase ?? "ask";
  return (
    <AskCard
      questions={questions}
      phase={shown}
      result={askResultOf(props.result) ?? (shown === "answered" ? sent : null)}
      busy={phase === null || !!part.approval?.sending}
      failure={failure}
      onSubmit={submit}
      status={phone}
    />
  );
};
