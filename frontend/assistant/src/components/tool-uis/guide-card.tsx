// 引导卡（工具 pearnly_guide，CC 引导式录入第 1 期；评估 docs/work/2026-11-12-cc-guided-entry.md 6.2、10.1）：写入前要人定的事由服务端出题，
// 这里只照卡渲染、交回答案，不出题、不拼选项、不翻译（题目、选项、来源的文字服务端按本轮语言出好，放在调用的输入 card 里）。
// 标准组件库：Card + Questionnaire（一题一页、第 n 项共 m 项、跳过 / 下一步 / 提交）+ Badge（推荐）+ Collapsible（已定 n 项，展开可改）。
// 三档（服务端定）：怎么录的题推荐项预选（defaultChecked），点「提交」才算；做不做的题不预选；已定的不出题、收进「已定 n 项」。
// 交回：[{key, value}]（跳过的题不交；已定项只交改过的），随「允许」交给服务端（sessions.confirm → toolrun.decide → guide_tool）。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ChevronRightIcon } from "lucide-react";
import { type FC, type FormEvent, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnaireProgress,
  QuestionnaireSkip,
  QuestionnaireSubmit,
  QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { guide, steps } from "@/i18n";
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
    .filter((q) => !skipped.has(q.key) && values[q.key])
    .map((q) => ({ key: q.key, value: values[q.key] }));
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

const Decided: FC<{ items: GuideDecided[]; disabled: boolean }> = ({ items, disabled }) => {
  if (!items.length) return null;
  return (
    <Collapsible>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" type="button">
          <ChevronRightIcon />
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

/** 卡片本体（页面与展示页共用）：phase ask 能答；answered 只剩一行「已答 n 项」；cancelled 写「已取消」。 */
export const GuideCardBody: FC<{
  card: GuideCardData;
  phase: "ask" | "answered" | "cancelled";
  answered?: number | null;
  busy?: boolean;
  onSubmit: (answers: GuideAnswer[]) => void;
}> = ({ card, phase, answered, busy, onSubmit }) => {
  const skipped = useRef(new Set<string>());
  const [, rerender] = useState(0);
  if (phase !== "ask") {
    return (
      <Card data-slot="cc-guide-card" data-phase={phase} className="px-(--cc-space-16)">
        <ItemDescription>{phase === "answered" ? guide.answered(answered ?? 0) : steps.cancelled}</ItemDescription>
      </Card>
    );
  }
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values: Record<string, string> = {};
    new FormData(event.currentTarget).forEach((value, name) => {
      values[name] = String(value);
    });
    onSubmit(answersFrom(card, values, skipped.current));
  };
  return (
    <Card data-slot="cc-guide-card" data-phase={phase} className="px-(--cc-space-16)">
      <Questionnaire onSubmit={submit} aria-busy={busy || undefined}>
        {card.questions.length > 1 ? <QuestionnaireProgress /> : null}
        {card.questions.map((question) => (
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
            {question.title && question.title_params?.label ? (
              <QuestionnaireDescription>
                {(question.count ?? 1) > 1 ? guide.documents(question.title_params.label, question.count ?? 1) : question.title_params.label}
              </QuestionnaireDescription>
            ) : null}
            <QuestionnaireChoices>
              {(question.options ?? []).map((option) => (
                <QuestionnaireChoice
                  key={option.code}
                  value={option.code}
                  defaultChecked={option.code === preselectOf(question)}
                  disabled={busy}
                >
                  <span className="flex flex-wrap items-center gap-(--cc-space-8)">
                    {optionText(option)}
                    {question.tier === "how" && option.recommended ? <Badge variant="secondary">{guide.recommended}</Badge> : null}
                  </span>
                  {option.note ? <QuestionnaireChoiceDescription>{option.note}</QuestionnaireChoiceDescription> : null}
                </QuestionnaireChoice>
              ))}
            </QuestionnaireChoices>
          </QuestionnaireItem>
        ))}
        <Decided items={card.decided} disabled={!!busy} />
        <QuestionnaireActions>
          <QuestionnaireSkip />
          <QuestionnaireNext />
          <QuestionnaireSubmit disabled={busy} />
        </QuestionnaireActions>
      </Questionnaire>
    </Card>
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
