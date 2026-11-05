// 通用提问卡片（工具 ask_user，形状照 Claude Code 的 AskUserQuestion；用户 11-05：卡片 1:1 照 Code 的原生提问卡片，组件固定，问什么、
// 几道题、什么选项由 CC 按当前情况决定）的纯函数，不碰 React：
//   - 入参 {questions: 1–4 题}，每题 {question, header（≤12 字）, multiSelect, options: 2–4 个 {label, description}}；推荐项排第一、
//     label 带「(推荐)」，默认选中（这就是预选）；「其他」由卡片自己加，不在 options 里。我们多认一个可选的 search（「其他」接服务端
//     可搜索的选择框：{company, doc_id, kind}）与 choices（「其他」里可选的全部值，页面自己出题时给）。
//   - 每题此刻的答（选了哪几个 label、「其他」打的字或选的值、跳过）；整理成 {answers: [{header, question, labels, other?, skipped?}]}，
//     按题序，没答的题算跳过。ask_user 的作答随对话的「允许」交给服务端（confirm 的确认项带 answers：[{labels, other?, skipped?}]，
//     W2 定稿），服务端记下、以 user.custom_tool_result 交回模型。
//   - 页面自己出题的两处（对话末尾的待处理框按共用判定的回包、代录中途那一张按站点 precheck 的要选项）：一项一题；答案再换回
//     判定接口的 [{key, value}] 或代录 task_answer 的 {单据: [{kind, subject, value, label}]}。
// 字走 i18n（ask.*）。

import { ask, op } from "@/i18n";
import { getMethod, postMethod } from "@/lib/frappe";
import { type ChoiceOption, optionLabel } from "./operator-words";

// ── 形状（AskUserQuestion）──────────────────────────────────────────────────
export const ASK_TOOL = "ask_user";
export type AskOption = { label: string; description?: string };
/** 「其他」接服务端搜索：搜哪类（accounts / products …）、按哪家公司哪张单定账套。 */
export type AskSearch = { company: string; doc_id?: string; kind: string };
export type AskQuestion = {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options: AskOption[];
  search?: AskSearch;
  /** 「其他」里可选的全部值（页面自己出题时给；编码 → 说法）；不给就是打字。 */
  choices?: ChoiceOption[];
};
export type AskInput = { questions: AskQuestion[] };

/** 一题此刻的答：选了的 label（单选至多一个）；other：选了「其他」时打的字或选的值（null = 没选「其他」）；skipped：点了「跳过」。 */
export type AskState = { labels: string[]; other: string | null; otherCode?: string; skipped?: boolean };
/** 交回的一题。other：选「其他」时打的字，或选的条目「编码 名称」（给模型看的，W2 约定）；code：选的条目的编码（页面自己出题时
 *  存答案用，不交给模型）；skipped：跳过（含没答的）。 */
export type AskAnswer = { header: string; question: string; labels: string[]; other?: string; code?: string; skipped?: true };
export type AskResult = { answers: AskAnswer[] };

/** 「(推荐)」：推荐项排第一、名字带它（三语，大小写不论）。 */
const RECOMMENDED = /[(（]\s*(推荐|recommended|แนะนำ)\s*[)）]\s*$/i;
export const isRecommended = (option: AskOption | undefined) => !!option && RECOMMENDED.test(option.label);

/** 入参整理：最多 4 题；每题至少要有问题；选项去掉空的（最多 4 个，「其他」另加）。读不出的回空数组。 */
export function questionsOf(input: unknown): AskQuestion[] {
  const raw = (input && typeof input === "object" ? (input as { questions?: unknown }).questions : null) ?? [];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((q): q is Record<string, unknown> => !!q && typeof q === "object" && typeof (q as { question?: unknown }).question === "string")
    .slice(0, 4)
    .map((q) => ({
      question: String(q.question),
      header: typeof q.header === "string" ? q.header : "",
      multiSelect: q.multiSelect === true,
      options: (Array.isArray(q.options) ? q.options : [])
        .filter((o): o is { label: string; description?: unknown } => !!o && typeof (o as { label?: unknown }).label === "string" && !!(o as { label: string }).label.trim())
        .slice(0, 4)
        .map((o) => ({ label: o.label, description: typeof o.description === "string" ? o.description : "" })),
      ...(q.search && typeof q.search === "object" ? { search: q.search as AskSearch } : {}),
      ...(Array.isArray(q.choices) ? { choices: q.choices as ChoiceOption[] } : {}),
    }));
}

/** 初始的答：推荐项（第一个、带「(推荐)」）默认选中，其余空着。 */
export function initialStates(questions: readonly AskQuestion[]): AskState[] {
  return questions.map((q) => ({ labels: isRecommended(q.options[0]) ? [q.options[0]!.label] : [], other: null }));
}

/** 点了一个选项：单选换成它（并取消「其他」），多选加上或去掉。 */
export function toggle(question: AskQuestion, state: AskState, label: string): AskState {
  if (!question.multiSelect) return { labels: [label], other: null };
  const has = state.labels.includes(label);
  return { ...state, skipped: false, labels: has ? state.labels.filter((l) => l !== label) : [...state.labels, label] };
}

/** 点了「其他」：单选只剩它，多选开 / 关它。 */
export function toggleOther(question: AskQuestion, state: AskState): AskState {
  if (!question.multiSelect) return { labels: [], other: state.other ?? "" };
  return { ...state, skipped: false, other: state.other === null ? "" : null, otherCode: undefined };
}

/** 这一题答了没有（选了选项，或「其他」填了字 / 选了值）。 */
export const answered = (state: AskState | undefined) => !!state && !state.skipped && (state.labels.length > 0 || !!(state.other ?? "").trim());

/** 提交：按题序，没答的算跳过。 */
export function resultFrom(questions: readonly AskQuestion[], states: readonly AskState[]): AskResult {
  return {
    answers: questions.map((q, i) => {
      const s = states[i];
      const base = { header: q.header ?? "", question: q.question };
      if (!answered(s)) return { ...base, labels: [], skipped: true as const };
      const other = (s!.other ?? "").trim();
      const code = (s!.otherCode ?? "").trim();
      return { ...base, labels: [...s!.labels], ...(other ? { other } : {}), ...(other && code ? { code } : {}) };
    }),
  };
}

/** 随「允许」交给服务端的作答（confirm 的 answers）：按题序，每题只留选的、「其他」、跳过。 */
export function confirmAnswers(result: AskResult): Array<{ labels: string[]; other?: string; skipped?: true }> {
  return result.answers.map((a) => (a.skipped ? { labels: [], skipped: true as const } : { labels: a.labels, ...(a.other ? { other: a.other } : {}) }));
}

/** 点完那一行（同允许卡片点完只剩一行淡色所选）：每题「标签：所选」，跳过的写「跳过」。 */
export function answeredLine(result: AskResult | null | undefined): string {
  return (result?.answers ?? [])
    .map((a) => {
      const shown = a.skipped ? ask.skip : [...a.labels, ...(a.other ? [a.other] : [])].join("、");
      return a.header ? `${a.header}：${shown}` : shown;
    })
    .join(" · ");
}

// ── 页面自己出题（待处理框、代录中途那一张）──────────────────────────────────────
/** 一个要定的项（共用判定 write_precheck 回包里没定下的那一项，或代录 precheck 的一项 needed）。 */
export type AskItem = {
  key: string;
  kind: string;
  subject: string;
  label: string;
  /** 预选值（没有 = 未定）。 */
  value?: string;
  value_label?: string;
  options?: ChoiceOption[];
  search?: string;
  docs: string[];
};

const NEW = "__new__";
/** 选项按钮上的字：预选值排第一、带「(推荐)」；再列别的几个（最多 4 个），其余进「其他」。 */
export function itemQuestion(item: AskItem, company: string): AskQuestion {
  const name = (code: string) => {
    const found = item.options?.find((o) => o.code === code);
    if (found) return optionLabel(item.kind, found);
    if (code === item.value && item.value_label) return item.value_label;
    return optionLabel(item.kind, { code, name: "" });
  };
  const codes: string[] = [];
  if (item.value) codes.push(item.value);
  for (const o of item.options ?? []) if (codes.length < 4 && !codes.includes(o.code)) codes.push(o.code);
  if (codes.length < 2 && !codes.includes(NEW) && item.search) codes.push(NEW);
  const options = codes.map((code, i) => ({
    label: i === 0 && item.value ? ask.recommended(name(code)) : name(code),
    description: "",
  }));
  return {
    question: item.label ? `${op.precheck.kind[item.kind] ?? op.kind.other} · ${item.label}` : op.precheck.kind[item.kind] ?? op.kind.other!,
    header: (op.precheck.kind[item.kind] ?? op.kind.other!).slice(0, 12),
    multiSelect: false,
    options,
    ...(item.search ? { search: { company, doc_id: item.docs[0] ?? "", kind: item.search } } : {}),
    ...(item.options?.length ? { choices: item.options } : {}),
  };
}

/** 一题的答 → 这一项选的值（编码）：选项按出题时的顺序对回编码；「其他」用选的编码或打的字；跳过回空。 */
export function itemValue(item: AskItem, question: AskQuestion, answer: AskAnswer | undefined): string {
  if (!answer || answer.skipped) return "";
  if (answer.other) return answer.code ?? answer.other;
  const at = question.options.findIndex((o) => o.label === answer.labels[0]);
  if (at < 0) return "";
  const codes: string[] = [];
  if (item.value) codes.push(item.value);
  for (const o of item.options ?? []) if (codes.length < 4 && !codes.includes(o.code)) codes.push(o.code);
  if (codes.length < 2 && !codes.includes(NEW) && item.search) codes.push(NEW);
  return codes[at] ?? "";
}

/** 判定接口 answer 的入参：每项的值（跳过的不交，依赖它的单这次不写）。 */
export function keyAnswers(items: readonly AskItem[], questions: readonly AskQuestion[], result: AskResult): Array<{ key: string; value: string }> {
  return items
    .map((item, i) => ({ key: item.key, value: itemValue(item, questions[i]!, result.answers[i]) }))
    .filter((a) => a.value);
}

/** 代录 task_answer 的入参：{单据: [{kind, subject, value, label}]}（按每项影响的单展开）。 */
export function docAnswers(items: readonly AskItem[], questions: readonly AskQuestion[], result: AskResult) {
  const out: Record<string, Array<{ kind: string; subject: string; value: string; label: string }>> = {};
  items.forEach((item, i) => {
    const value = itemValue(item, questions[i]!, result.answers[i]);
    if (!value) return;
    for (const id of item.docs) (out[id] ??= []).push({ kind: item.kind, subject: item.subject, value, label: item.label });
  });
  return out;
}

/** 共用判定的回包（write_precheck check，形状同 contracts.merge）里没定下的项（预选、未定），按卡片上的顺序。 */
export function itemsOfCheck(reply: unknown): AskItem[] {
  const groups = (reply && typeof reply === "object" ? (reply as { groups?: unknown }).groups : null) ?? [];
  if (!Array.isArray(groups)) return [];
  return groups
    .flatMap((g) => (g && Array.isArray((g as { items?: unknown }).items) ? (g as { items: Record<string, unknown>[] }).items : []))
    .filter((i) => i && i.state !== "decided" && typeof i.key === "string")
    .map((i) => ({
      key: String(i.key), kind: String(i.kind ?? ""), subject: String(i.subject ?? ""), label: String(i.label ?? ""),
      value: i.state === "proposed" ? String(i.value ?? "") : "", value_label: String(i.value_label ?? ""),
      options: Array.isArray(i.options) ? (i.options as ChoiceOption[]) : [], ...(i.search ? { search: String(i.search) } : {}),
      docs: Array.isArray(i.docs) ? (i.docs as unknown[]).map(String) : [],
    }));
}

/** 代录 precheck 一张单要选的（needed：同一类同一对象只问一次）。 */
export function itemsOfNeeded(docs: ReadonlyArray<{ doc_id: string; state: string; needed?: Array<{ kind: string; subject: string; label: string; options: ChoiceOption[] }> }>): AskItem[] {
  const map = new Map<string, AskItem>();
  for (const doc of docs) {
    if (doc.state !== "choose") continue;
    for (const need of doc.needed ?? []) {
      const key = `${need.kind}\n${need.subject}`;
      const found = map.get(key);
      if (found) {
        if (!found.docs.includes(doc.doc_id)) found.docs.push(doc.doc_id);
        continue;
      }
      map.set(key, { key, kind: need.kind, subject: need.subject, label: need.label || need.subject, options: need.options ?? [], docs: [doc.doc_id] });
    }
  }
  return [...map.values()];
}

// ── 站点请求 ────────────────────────────────────────────────────────────────
export const PRECHECK_API = "pearnly_core.write_precheck.api.";
export const SEARCH_PAGE = 20;
export type SearchPage = { options: ChoiceOption[]; more: boolean };

type Get = <T>(method: string, args?: Record<string, string | number | undefined>) => Promise<T>;
type Post = <T>(method: string, args?: Record<string, unknown>) => Promise<T>;
let site: { get: Get; post: Post } = { get: getMethod, post: postMethod };
/** 测试换掉站点请求。 */
export const setAskSite = (next: Partial<typeof site>) => {
  site = { ...site, ...next };
};

export const askActions = {
  /** 这几张单判一次（待处理框自己出题用）。 */
  check: (company: string, docIds: readonly string[]) => site.post<unknown>(PRECHECK_API + "check", { company, doc_ids: JSON.stringify(docIds), mode: "pending" }),
  /** 存下答案（照各自现有的记法记住，服务端把这些单重新排队写入）。 */
  remember: (company: string, docIds: readonly string[], answers: ReadonlyArray<{ key: string; value: string }>) =>
    site.post(PRECHECK_API + "answer", { company, doc_ids: JSON.stringify(docIds), answers: JSON.stringify(answers) }),
  /** 「其他」里的服务端搜索：一页 SEARCH_PAGE 条，「显示更多」取下一页。 */
  search: async (search: AskSearch, txt: string, start: number): Promise<SearchPage> => {
    const reply = await site.get<{ options?: ChoiceOption[]; more?: boolean }>(PRECHECK_API + "search", {
      company: search.company, doc_id: search.doc_id ?? "", search: search.kind, txt, start, page_length: SEARCH_PAGE,
    });
    const options = (reply?.options ?? []).map((o) => ({ code: String(o.code ?? ""), name: String(o.name ?? "") })).filter((o) => o.code);
    return { options, more: !!reply?.more };
  },
};
