// 引导卡（工具 pearnly_guide，CC 引导式录入第 1 期；评估 docs/work/2026-11-12-cc-guided-entry.md 6.2、10.1）：写入前要人定的事由服务端出题，
// 这里只照卡渲染、交回答案，不出题、不拼选项、不翻译（题目、选项、来源的文字服务端按本轮语言出好，放在调用的输入 card 里）。
// 标准组件库：Card + Questionnaire（一题一页、第 n 项共 m 项、跳过 / 下一步 / 提交）+ Badge（推荐）+ Collapsible（已定 n 项，展开可改）。
// 三档（服务端定）：怎么录的题推荐项预选（defaultChecked），点「提交」才算；做不做的题不预选；已定的不出题、收进「已定 n 项」。
// 交回：[{key, value}]（跳过的题不交；已定项只交改过的），随「允许」交给服务端（sessions.confirm → toolrun.decide → guide_tool）。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { type FC, type FormEvent, type KeyboardEvent, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Shortcut } from "@/components/shortcut";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Item, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireDescription,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireSkip,
  QuestionnaireSubmit,
  QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { ask, guide, steps } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught } from "@/lib/caught";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { useToolLabels } from "@/lib/tool-labels";
import { askPhase } from "./ask-card";
import { useSessionId } from "./operator-card";

export { GUIDE_TOOL } from "./guide-shared";

export type GuideOption = { code: string; label?: string; name?: string; note?: string; recommended?: boolean };
export type GuideQuestion = {
  key: string;
  kind: string;
  tier: "how" | "whether";
  title?: string;
  title_params?: { label?: string; count?: number };
  count?: number;
  options?: GuideOption[];
  preselected?: string;
  /** 允许自己填（卡上这一题末尾出「其他」，服务端定） */
  free?: boolean;
};
export type GuideDecided = { key: string; kind: string; title?: string; label?: string; value: string; value_label?: string; source_label?: string; options?: GuideOption[] };
export type GuideCardData = { questions: GuideQuestion[]; decided: GuideDecided[] };
export type GuideAnswer = { key: string; value: string };

/** 调用的输入 → 卡（读不出回空卡）。 */
export function guideCardOf(args: unknown): GuideCardData {
  const card = (args as { card?: Partial<GuideCardData> } | null)?.card;
  return { questions: Array.isArray(card?.questions) ? card.questions : [], decided: Array.isArray(card?.decided) ? card.decided : [] };
}

/** 选项上显示的字：服务端翻好的 label，主档选项是名称，都没有用编码。 */
export const optionText = (option: GuideOption) => option.label || option.name || option.code;

/** 默认勾选哪个选项：只有「怎么录」的题、且是服务端给的预选值；「做不做」的题一个都不选。 */
export const preselectOf = (question: GuideQuestion) =>
  question.tier === "how" && (question.options ?? []).some((o) => o.code === question.preselected) ? question.preselected ?? "" : "";

/** 表单值 → 交回的答案：没跳过、有值的题；已定项只交改过的。 */
export function answersFrom(card: GuideCardData, values: Record<string, string>, skipped: ReadonlySet<string>): GuideAnswer[] {
  const asked = card.questions
    .filter((q) => !skipped.has(q.key))
    .map((q) => ({ key: q.key, value: values[q.key] || "" }))
    .filter((a) => a.value);
  const changed = card.decided
    .filter((d) => values[`decided:${d.key}`] && values[`decided:${d.key}`] !== d.value)
    .map((d) => ({ key: d.key, value: values[`decided:${d.key}`] }));
  return [...asked, ...changed];
}

/** 工具结果正文里答了几项（读不出回 null）。 */
function answeredCount(result: unknown): { status: string; answered: number } | null {
  if (result === undefined || result === null) return null;
  const text = typeof result === "object" && "text" in (result as object) ? String((result as { text: unknown }).text) : String(result);
  try {
    const parsed = JSON.parse(text) as { status?: string; answered?: number };
    return { status: String(parsed.status ?? ""), answered: Number(parsed.answered ?? 0) };
  } catch (caughtError) {
    reportCaught(caughtError, "guide-result");
    return null;
  }
}

const Decided: FC<{ items: GuideDecided[]; disabled: boolean; open?: boolean }> = ({ items, disabled, open }) => {
  const [shown, setShown] = useState(!!open);
  if (!items.length) return null;
  return (
    <Collapsible open={shown} onOpenChange={setShown}>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" type="button">
          {shown ? <ChevronDownIcon /> : <ChevronRightIcon />}
          {guide.decided(items.length)}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        {items.map((item) => (
          <Item key={item.key} size="sm">
            <ItemContent>
              <ItemTitle>{item.title || item.label}</ItemTitle>
              {item.options?.length ? (
                <NativeSelect name={`decided:${item.key}`} defaultValue={item.value} disabled={disabled} aria-label={item.title || item.label}>
                  {item.options.map((option) => (
                    <NativeSelectOption key={option.code} value={option.code}>
                      {optionText(option)}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              ) : (
                <ItemDescription>{item.value_label || item.value}</ItemDescription>
              )}
              {item.source_label ? <ItemDescription>{item.source_label}</ItemDescription> : null}
            </ItemContent>
          </Item>
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
};

/** 卡片本体（页面与展示页共用），版式照 Claude 的提问卡（用户 11-13 定）：每题卡头左边题目、右边翻页器「‹ 第 n 项，共 m 项 ›」（首末项箭头变淡）；
 *  选项是通栏的行、行间细分隔线，左边单选圆点，右边选项名（推荐项后面「推荐」标记）+ 一行灰色说明（推荐来由 / 选项是什么，服务端给）；
 *  允许自己填的题末尾一行「其他」；「已定 n 项」在选项下、按钮上；右下「跳过」+「下一步」（最后一项「提交」），电脑带快捷键。
 *  全用 components/ui/questionnaire 的部件，外观是它的卡片外观（globals.css 标准组件库块 .cc-ui-questionnaire-card）。
 *  phase answered 只剩一行「已答 n 项」；cancelled 写「已取消」。 */
export const GuideCardBody: FC<{
  card: GuideCardData;
  phase: "ask" | "answered" | "cancelled";
  answered?: number | null;
  busy?: boolean;
  /** 「已定 n 项」一开始就展开（展示页截图用；对话里默认收起）。 */
  decidedOpen?: boolean;
  /** 一开始停在第几题（展示页截图用）。 */
  startAt?: number;
  onSubmit: (answers: GuideAnswer[]) => void;
}> = ({ card, phase, answered, busy, decidedOpen, startAt, onSubmit }) => {
  const skipped = useRef(new Set<string>());
  const [, rerender] = useState(0);
  if (phase !== "ask") {
    return (
      <div data-slot="cc-guide-card" data-phase={phase} className="cc-steps-card cc-ui-questionnaire-card">
        <div className="cc-ui-questionnaire-done">{phase === "answered" ? guide.answered(answered ?? 0) : steps.cancelled}</div>
      </div>
    );
  }
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values: Record<string, string> = {};
    const form = new FormData(event.currentTarget);
    for (const question of card.questions) {
      const all = form.getAll(question.key).map(String);
      const codes = new Set((question.options ?? []).map((o) => o.code));
      // 允许自己填的题：「其他」里填了字就用填的，没填用选中的选项
      const typed = question.free ? all.find((v) => !codes.has(v) && v.trim()) : undefined;
      values[question.key] = typed?.trim() || all.find((v) => codes.has(v)) || "";
    }
    form.forEach((value, name) => {
      if (name.startsWith("decided:")) values[name] = String(value);
    });
    onSubmit(answersFrom(card, values, skipped.current));
  };
  const paged = card.questions.length > 1;
  const first = card.questions[Math.min(Math.max(startAt ?? 0, 0), card.questions.length - 1)]?.key;
  return (
    <div
      data-slot="cc-guide-card"
      data-phase={phase}
      className="cc-steps-card cc-ui-questionnaire-card"
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
        // ⌘ / Ctrl + 回车 = 此刻的主按钮（下一步 / 提交），同旧提问卡
        if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey) || busy) return;
        event.preventDefault();
        const actions = event.currentTarget.querySelector('[data-slot="questionnaire-actions"]');
        const main = [...(actions?.querySelectorAll<HTMLButtonElement>('[data-slot="questionnaire-submit"], [data-slot="questionnaire-next"]') ?? [])]
          .find((button) => !button.hasAttribute("data-hidden") && !button.hidden);
        main?.click();
      }}
    >
      <Questionnaire onSubmit={submit} aria-busy={busy || undefined} defaultItem={first}>
        {card.questions.map((question, index) => (
          <QuestionnaireItem
            key={question.key}
            name={question.key}
            onStatusChange={(status) => {
              if (status === "skipped") skipped.current.add(question.key);
              else skipped.current.delete(question.key);
              rerender((n) => n + 1);
            }}
          >
            <QuestionnaireTitle>{question.title || question.title_params?.label}</QuestionnaireTitle>
            {paged ? (
              <div className="cc-ui-questionnaire-pager">
                {/* 首项没有上一项、末项没有下一项：部件此时不画按钮，这里放同样的按钮的禁用态（箭头变淡不消失，同 Claude） */}
                {index === 0 ? (
                  <Button type="button" variant="ghost" size="icon-xs" disabled aria-label={ask.prev}>
                    <ChevronLeftIcon />
                  </Button>
                ) : (
                  <QuestionnairePrevious variant="ghost" size="icon-xs" aria-label={ask.prev}>
                    <ChevronLeftIcon />
                  </QuestionnairePrevious>
                )}
                <QuestionnaireProgress />
                {index === card.questions.length - 1 ? (
                  <Button type="button" variant="ghost" size="icon-xs" disabled aria-label={ask.after}>
                    <ChevronRightIcon />
                  </Button>
                ) : (
                  <QuestionnaireNext variant="ghost" size="icon-xs" aria-label={ask.after}>
                    <ChevronRightIcon />
                  </QuestionnaireNext>
                )}
              </div>
            ) : null}
            {question.title && question.title_params?.label ? (
              <QuestionnaireDescription>
                {(question.count ?? 1) > 1 ? guide.documents(question.title_params.label, question.count ?? 1) : question.title_params.label}
              </QuestionnaireDescription>
            ) : null}
            <QuestionnaireChoices>
              {(question.options ?? []).map((option) => (
                <QuestionnaireChoice key={option.code} value={option.code} defaultChecked={option.code === preselectOf(question)} disabled={busy}>
                  <span className="flex flex-wrap items-center gap-(--cc-space-8)">
                    {optionText(option)}
                    {question.tier === "how" && option.recommended ? <Badge variant="secondary">{guide.recommended}</Badge> : null}
                  </span>
                  {option.note ? <QuestionnaireChoiceDescription>{option.note}</QuestionnaireChoiceDescription> : null}
                </QuestionnaireChoice>
              ))}
            </QuestionnaireChoices>
            {question.free ? <QuestionnaireInput placeholder={ask.other} aria-label={ask.other} /> : null}
          </QuestionnaireItem>
        ))}
        <Decided items={card.decided} disabled={!!busy} open={decidedOpen} />
        <QuestionnaireActions>
          <QuestionnaireSkip />
          <QuestionnaireNext>
            {ask.next}
            <Shortcut keys="Ctrl↵" />
          </QuestionnaireNext>
          <QuestionnaireSubmit disabled={busy}>
            {ask.submit}
            <Shortcut keys="Ctrl↵" />
          </QuestionnaireSubmit>
        </QuestionnaireActions>
      </Questionnaire>
    </div>
  );
};

/** 工具 pearnly_guide 的卡片：同 ask_user，调用登记成等人作答；「提交」= 对话的「允许」带上答案。 */
export const GuideCard: ToolCallMessagePartComponent = (props) => {
  const part = props as unknown as CardPart & { approval?: { id: string; failure?: string; sending?: boolean } };
  const argsKey = JSON.stringify(props.args ?? null);
  const card = useMemo(() => guideCardOf(JSON.parse(argsKey)), [argsKey]);
  const labels = useToolLabels();
  const sessionId = useSessionId();
  const [sent, setSent] = useState<number | null>(null);
  const phase = askPhase(part, labels);
  const submit = (answers: GuideAnswer[]) => {
    if (!sessionId || !part.approval) return;
    setSent(answers.length);
    getSessionController(sessionId).respondToApproval(part.approval.id, true, undefined, answers);
  };
  const result = answeredCount(props.result);
  return (
    <GuideCardBody
      card={card}
      phase={phase ?? "ask"}
      answered={result?.answered ?? sent}
      busy={phase === null || !!part.approval?.sending}
      onSubmit={submit}
    />
  );
};
