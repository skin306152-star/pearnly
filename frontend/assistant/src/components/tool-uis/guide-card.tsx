// 引导卡（工具 pearnly_guide，CC 引导式录入第 1 期；评估 docs/work/2026-11-12-cc-guided-entry.md 6.2、10.1）：写入前要人定的事由服务端出题，
// 这里只照卡渲染、交回答案，不出题、不拼选项、不翻译（题目、选项、来源的文字服务端按本轮语言出好，放在调用的输入 card 里）。
// 标准组件库：Card + Questionnaire（一题一页、第 n 项共 m 项、跳过 / 下一步 / 提交）+ Badge（推荐）+ Collapsible（已定 n 项，展开可改）。
// 三档（服务端定）：怎么录的题推荐项预选（defaultChecked），点「提交」才算；做不做的题不预选；已定的不出题、收进「已定 n 项」。
// 交回：[{key, value}]（跳过的题不交；已定项只交改过的），随「允许」交给服务端（sessions.confirm → toolrun.decide → guide_tool）。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { type FC, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Item, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { guide, steps } from "@/i18n";
import type { CardPart } from "@/lib/approval-card";
import { reportCaught, withReport } from "@/lib/caught";
import { fetchGuideCard } from "@/lib/guide";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { useToolLabels } from "@/lib/tool-labels";
import { AskCard, askPhase } from "./ask-card";
import { CardLine } from "./card-frame";
import type { AskQuestion, AskResult } from "./ask-shared";
import { useSessionId } from "./operator-card";

export { GUIDE_TOOL } from "./guide-shared";

export type GuideOption = { code: string; label?: string; name?: string; note?: string; recommended?: boolean;
  /** 选它还要填一个数，交「码:数」（A3 四题 11-16） */ entry?: { input: "number"; min_exclusive?: string } };
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
  /** 这题涉及的单据（大列表题按第一张定账套去服务端搜） */
  docs?: string[];
  /** 大列表题：「其他」去服务端搜这一类（科目 / 物料 / 对方…，`write_precheck.api.search`），选中交编码 */
  search?: string;
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
  // 结果还没到全（补拉时先给空正文）：当作还没有，不解析、不报错（11-16 实验栈：手机格偶发 Unexpected end of JSON input）
  if (!text.trim()) return null;
  try {
    const parsed = JSON.parse(text) as { status?: string; answered?: number };
    return { status: String(parsed.status ?? ""), answered: Number(parsed.answered ?? 0) };
  } catch (caughtError) {
    reportCaught(caughtError, "guide-result");
    return null;
  }
}

/** 服务端没出卡（不用问，``nothing_to_ask``）：对话里不画这张卡。 */
export const nothingAsked = (result: unknown) => answeredCount(result)?.status === "nothing_to_ask";

const Decided: FC<{ items: GuideDecided[]; disabled: boolean; open?: boolean; onChange?: (key: string, value: string) => void }> = ({
  items,
  disabled,
  open,
  onChange,
}) => {
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
                <NativeSelect defaultValue={item.value} disabled={disabled} aria-label={item.title || item.label}
                  onChange={(event) => onChange?.(item.key, event.currentTarget.value)}>
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

/** 服务端的卡 → 提问卡的题（同一个组件 AskCard，用户 11-13：两张卡必须同一种版式）。推荐项标 recommended（徽章、默认选中，只有「怎么录」的题）；
 *  说明行是服务端给的；「其他」只在服务端标了 free 的题出；题目下一行写这一题管哪家几张。 */
export function askQuestionsOf(card: GuideCardData, company = ""): AskQuestion[] {
  return card.questions.map((question) => {
    const label = question.title_params?.label ?? "";
    const preselect = preselectOf(question);
    const options = (question.options ?? []).map((option) => ({
      label: optionText(option),
      value: option.code,
      ...(option.note ? { description: option.note } : {}),
      ...(option.code === preselect ? { recommended: true } : {}),
      ...(option.entry ? { entry: option.entry } : {}),
    }));
    // 推荐项排第一（AskCard 按第一个是不是推荐项定默认选中）
    options.sort((a, b) => Number(!!b.recommended) - Number(!!a.recommended));
    const count = question.count ?? 1;
    // 说明就是题目本身的（仓库题的说明是「仓库」）只管一张时不再写第二遍：11-22 测试环境「仓库」下面又一行「仓库」
    const same = !!question.title && label.trim().toLocaleLowerCase() === question.title.trim().toLocaleLowerCase();
    return {
      question: question.title || label,
      header: question.title || label,
      options,
      ...(question.title && label && !(same && count <= 1) ? { subtitle: count > 1 ? guide.documents(label, count) : label } : {}),
      ...(question.search && company ? { search: { company, doc_id: question.docs?.[0] ?? "", kind: question.search } } : {}),
      ...(question.free || (question.search && company) ? {} : { noOther: true }),
    };
  });
}

/** 提问卡交回的答（按题序）→ 服务端要的 [{key, value}]：跳过的不交；选项按编码；允许自己填的题「其他」交填的字。 */
export function guideAnswersOf(card: GuideCardData, result: AskResult): GuideAnswer[] {
  const out: GuideAnswer[] = [];
  card.questions.forEach((question, index) => {
    const answer = result.answers[index];
    if (!answer || answer.skipped || answer.not_asked) return;
    const picked = (question.options ?? []).find((option) => answer.labels.includes(optionText(option)));
    // 大列表题在「其他」里从服务端搜出来选的交编码；允许自己填的题交填的字
    // 带 entry 的选项交「码:数」（光交码服务端拒）
    const entered = picked?.entry ? answer.entries?.[optionText(picked)] : undefined;
    const value = picked ? (picked.entry ? (entered ? `${picked.code}:${entered}` : "") : picked.code)
      : (question.search && answer.code ? answer.code : question.free ? (answer.other ?? "").trim() : "");
    if (value) out.push({ key: question.key, value });
  });
  return out;
}

/** 卡片本体（页面与展示页共用）：就是提问卡 AskCard，加上「已定 n 项」附加区。phase answered 只剩一行；cancelled 写「已取消」。 */
export const GuideCardBody: FC<{
  card: GuideCardData;
  /** 大列表题去服务端搜要按公司（调用参数里的 company） */
  company?: string;
  phase: "ask" | "answered" | "cancelled" | "expired" | "ended";
  answered?: number | null;
  busy?: boolean;
  /** 「已定 n 项」一开始就展开（展示页截图用；对话里默认收起）。 */
  decidedOpen?: boolean;
  /** 一开始停在第几题（展示页截图用）。 */
  startAt?: number;
  /** 自己带外框（默认）；嵌在别的卡里（代录卡头下面）时 false */
  framed?: boolean;
  onSubmit: (answers: GuideAnswer[]) => void;
}> = ({ card, company, phase, answered, busy, decidedOpen, startAt, framed = true, onSubmit }) => {
  const questions = useMemo(() => askQuestionsOf(card, company), [card, company]);
  const changed = useRef<Record<string, string>>({});
  // 已结束、不说结果（执行记录是不认识的状态）：不画空框，同允许卡收起
  if (phase === "ended") return null;
  if (phase !== "ask") {
    // 一行态走共用骨架（card-frame.tsx CardLine）：头是这张卡问过的题，下面一行已答几项 / 已取消
    return (
      <CardLine slot="cc-guide-card" attrs={{ "data-phase": phase }} phase={phase === "answered" ? "answered" : "cancelled"}
        title={questions.map((q) => q.header || q.question).join(" · ")}
        answer={phase === "answered" ? guide.answered(answered ?? 0) : phase === "expired" ? guide.expired : steps.cancelled} />
    );
  }
  const submit = (result: AskResult) => {
    const decided = card.decided
      .filter((item) => changed.current[item.key] && changed.current[item.key] !== item.value)
      .map((item) => ({ key: item.key, value: changed.current[item.key]! }));
    onSubmit([...guideAnswersOf(card, result), ...decided]);
  };
  return (
    <div data-slot="cc-guide-card" data-phase={phase}>
      <AskCard
        questions={questions}
        preselect={false}
        framed={framed}
        phase="ask"
        busy={!!busy}
        failure=""
        onSubmit={submit}
        status={false}
        startAt={startAt}
        extra={<Decided items={card.decided} disabled={!!busy} open={decidedOpen} onChange={(key, value) => (changed.current[key] = value)} />}
      />
    </div>
  );
};

const GUIDE_CARD_RETRIES = 5;
const GUIDE_CARD_RETRY_MS = 800;

/** 工具 pearnly_guide 的卡片：同 ask_user，调用登记成等人作答；「提交」= 对话的「允许」带上答案。 */
export const GuideCard: ToolCallMessagePartComponent = (props) => {
  const part = props as unknown as CardPart & { approval?: { id: string; failure?: string; sending?: boolean } };
  const argsKey = JSON.stringify(props.args ?? null);
  const fromArgs = useMemo(() => guideCardOf(JSON.parse(argsKey)), [argsKey]);
  const sessionIdForCard = useSessionId();
  const useId = part.approval?.id ?? (props as { toolCallId?: string }).toolCallId ?? "";
  // 页面拿到的调用参数是模型给的单据；卡由服务端出卡前存在执行记录里，按本人本对话这一次调用取（assistant.api.guide_card）
  const [stored, setStored] = useState<GuideCardData | null>(null);
  // 服务端先出卡、存进执行记录，再回「等作答」（页面这时才挂上 approval）：等 approval 出现再取；取到空卡（记录还没读到）隔一会儿再取几次
  const gated = !!part.approval;
  useEffect(() => {
    if (fromArgs.questions.length || fromArgs.decided.length || !sessionIdForCard || !useId || !gated) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fetchCard = (left: number) => {
      void fetchGuideCard(sessionIdForCard, useId).then(
        (out) => {
          if (!alive) return;
          const found = guideCardOf(out);
          if (found.questions.length || found.decided.length || left <= 0) setStored(found);
          else timer = setTimeout(() => fetchCard(left - 1), GUIDE_CARD_RETRY_MS);
        },
        withReport(undefined, "guide-card"),
      );
    };
    fetchCard(GUIDE_CARD_RETRIES);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [fromArgs, sessionIdForCard, useId, gated]);
  const card = fromArgs.questions.length || fromArgs.decided.length ? fromArgs : (stored ?? fromArgs);
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
  if (result?.status === "nothing_to_ask") return null;
  return (
    <GuideCardBody
      card={card}
      company={String((props.args as { company?: unknown } | undefined)?.company ?? "")}
      phase={phase ?? "ask"}
      answered={result?.answered ?? sent}
      busy={phase === null || !!part.approval?.sending || (phase === "ask" && !card.questions.length && !card.decided.length)}
      onSubmit={submit}
    />
  );
};
