// 「实操」工作区的纯逻辑（不碰界面、不调数据源）：资料分组、开始前确认、任务与每张单的状态、完成行、导出报告。
// 文案一律取 i18n.ts 的 op（按代码取词）；原因代码、内部状态名绝不原样显示，认不出的一律写「需要处理」。

import { formatAmount } from "@/lib/companies";
import { co, op, steps } from "@/i18n";
import {
  type Answers,
  type BatchSnapshot,
  type BatchStatus,
  DOC_GROUPS,
  type DocGroup,
  type DocSnapshot,
  type DocumentOption,
  DONE_STATES,
  levelOf,
  NEW_CODE,
  type PrecheckDoc,
  type PrecheckOption,
} from "./types";

// ── 资料分组 ───────────────────────────────────────────────────────────────
const isGroup = (value: unknown): value is DocGroup => (DOC_GROUPS as readonly unknown[]).includes(value);

/** 资料区的组：站点给了 group 用它；没给按 kind（sales 归销售，其余归采购）。 */
export function groupOf(doc: { group?: string; kind?: string }): DocGroup {
  if (isGroup(doc.group)) return doc.group;
  if (isGroup(doc.kind)) return doc.kind;
  return doc.kind === "sales" ? "sales" : "purchase";
}

/** 按组排好（组的先后按 DOC_GROUPS，组内按日期、单号）；all = 空组也列出（拖动时当落点）。 */
export function groupDocuments(docs: readonly DocumentOption[], all = false): Array<[DocGroup, DocumentOption[]]> {
  const byDate = (a: DocumentOption, b: DocumentOption) =>
    String(a.date ?? "").localeCompare(String(b.date ?? "")) || String(a.ref ?? a.id).localeCompare(String(b.ref ?? b.id));
  return DOC_GROUPS.map((group) => [group, docs.filter((doc) => groupOf(doc) === group).sort(byDate)] as [DocGroup, DocumentOption[]]).filter(
    ([, rows]) => all || rows.length > 0,
  );
}

/** 交给 CC 的单：不入账的不交，开始前确认里跳过的不交。 */
export const docsToStart = (docs: readonly DocumentOption[], skipped: readonly string[] = []) =>
  docs.filter((doc) => groupOf(doc) !== "skip" && !skipped.includes(doc.id)).map((doc) => doc.id);

/** 一张单的标题：单号 · 对方（没有的不写；都没有写编号）。 */
export function docTitle(doc: { ref?: string; party?: string }, fallback = ""): string {
  return [doc.ref, doc.party].filter(Boolean).join(" · ") || fallback;
}

// ── 原因 ────────────────────────────────────────────────────────────────────
/** 看着像内部代码的（[OPERATOR_X]、choice_needed:…、reading、HTTP 原文）：不给人看。 */
export function isRawCode(text: string): boolean {
  const value = text.trim();
  if (!value) return true;
  return (
    /\[[A-Z0-9_]+\]/.test(value) || /^[A-Z0-9]+_[A-Z0-9_]+/.test(value) || /^[a-z0-9_.-]+(:|$)/i.test(value) || /Traceback|Exception\b/.test(value)
  );
}

/** 原因代码 → 给人看的一句：实操原因、交单时没收的状态（已交过一类），站点给的已翻译原文；都不是写「需要处理」。 */
export function reasonText(code: string | undefined, message?: string): string {
  const key = String(code ?? "").trim();
  const known = op.reason[key] ?? op.refused[key] ?? (key.startsWith("already_") ? op.refused.already : undefined);
  if (known) return known;
  if (message && !isRawCode(message)) return message.trim();
  return op.unknownReason;
}

// ── 开始前确认 ───────────────────────────────────────────────────────────────
/** 一问：同一类、同一对象（同一个新商品、同一个新供应商）在几张单上只问一次。 */
export type Question = { key: string; kind: string; subject: string; label: string; options: PrecheckOption[]; doc_ids: string[] };
/** 类的先后。 */
export const KIND_ORDER = ["item", "party", "account", "payment", "vat", "receipt", "department"] as const;

export function questionsOf(prechecks: readonly PrecheckDoc[], skipped: readonly string[] = []): Question[] {
  const map = new Map<string, Question>();
  for (const doc of prechecks) {
    if (doc.state !== "choose" || skipped.includes(doc.doc_id)) continue;
    for (const need of doc.needed ?? []) {
      const key = `${need.kind}\n${need.subject}`;
      const found = map.get(key);
      if (found) {
        if (!found.doc_ids.includes(doc.doc_id)) found.doc_ids.push(doc.doc_id);
        continue;
      }
      map.set(key, { key, kind: need.kind, subject: need.subject, label: need.label || need.subject, options: need.options ?? [], doc_ids: [doc.doc_id] });
    }
  }
  const rank = (kind: string) => {
    const at = (KIND_ORDER as readonly string[]).indexOf(kind);
    return at < 0 ? KIND_ORDER.length : at;
  };
  return [...map.values()].sort((a, b) => rank(a.kind) - rank(b.kind) || a.label.localeCompare(b.label));
}

/** 按类分组：[类, 问题们]，先后按 KIND_ORDER。 */
export function questionGroups(questions: readonly Question[]): Array<[string, Question[]]> {
  const map = new Map<string, Question[]>();
  for (const q of questions) map.set(q.kind, [...(map.get(q.kind) ?? []), q]);
  return [...map.entries()];
}

/** 类的标题：供应商 / 客户按这几张单的方向写（全是采购写供应商、全是销售写客户）。 */
export function kindTitle(kind: string, groups: readonly DocGroup[] = []): string {
  if (kind === "party" && groups.length) {
    if (groups.every((g) => g === "sales")) return op.kind.customer!;
    if (groups.every((g) => g !== "sales")) return op.kind.supplier!;
  }
  return op.kind[kind] ?? op.kind.other!;
}

/** 代码就是选项的几项：现购 / 赊购、抵进项 / 记费用（同会话栏「记住的选择」的说法）。 */
const CODED: Record<string, () => string> = {
  cash: () => co.payCash,
  credit: () => co.payCredit,
  claim: () => co.vatClaim,
  expense: () => co.vatExpense,
};

/** 一个选项的说法：新建（商品写「库存（新建）」）、现购 / 赊购、抵进项 / 记费用；已有的写编码与名称。 */
export function optionLabel(kind: string, option: PrecheckOption): string {
  if (option.code === NEW_CODE) return kind === "item" ? op.option.newItem! : op.option.new!;
  const name = (option.name ?? "").trim();
  if (!name && CODED[option.code]) return CODED[option.code]!();
  return [option.code, name].filter(Boolean).join(" ") || op.unknownReason;
}

export const allAnswered = (questions: readonly Question[], answers: Readonly<Record<string, string>>) =>
  questions.every((q) => !!answers[q.key]);

/** 交给 start 的回答：{单据: [{kind, subject, value, label}]}。 */
export function answersFor(questions: readonly Question[], answers: Readonly<Record<string, string>>): Answers {
  const out: Answers = {};
  for (const q of questions) {
    const value = answers[q.key];
    if (!value) continue;
    for (const id of q.doc_ids) (out[id] ??= []).push({ kind: q.kind, subject: q.subject, value, label: q.label });
  }
  return out;
}

/** 上次推送结果不明（站点预检 blocked、原因 push.result_unknown）：在开始前确认里标已入账 / 没有入账，不在「需要处理」里。 */
export const RESULT_UNKNOWN = "push.result_unknown";

/** 预检里停下的单（等识别、挡下的；结果不明的另算，见 unknownDocs）：原因给人看的一句。 */
export const stoppedDocs = (prechecks: readonly PrecheckDoc[], skipped: readonly string[] = []) =>
  prechecks
    .filter((doc) => (doc.state === "blocked" || doc.state === "waiting") && doc.reason !== RESULT_UNKNOWN && !skipped.includes(doc.doc_id))
    .map((doc) => ({ doc_id: doc.doc_id, reason: reasonText(doc.reason, doc.message) }));

/** 上次推送结果不明的单：站点给的那一句（已翻译；认不出的写「需要处理」）。 */
export const unknownDocs = (prechecks: readonly PrecheckDoc[], skipped: readonly string[] = []) =>
  prechecks
    .filter((doc) => doc.state === "blocked" && doc.reason === RESULT_UNKNOWN && !skipped.includes(doc.doc_id))
    .map((doc) => ({ doc_id: doc.doc_id, message: reasonText(doc.reason, doc.message) }));

// ── 任务状态 ─────────────────────────────────────────────────────────────────
export type TaskTone = "preparing" | "running" | "attention" | "done";

/** 一张单停下等人：等人的状态、原因必须人工（不认识的也算）。 */
export const needsYou = (doc: Pick<DocSnapshot, "status" | "reason">) => doc.status === "waiting" && levelOf(doc.reason) === "human";

/** 整个任务停下等人：原因必须人工（登录、授权名额、公司不符、选公司、服务器、费用上限）。 */
export const batchNeedsHuman = (s: Pick<BatchSnapshot, "status" | "reason">) =>
  s.status !== "running" && s.status !== "finished" && !!s.reason && levelOf(s.reason) === "human";

export const isFinal = (status: BatchStatus) => status === "finished" || status === "stopped";

type TaskLike = Pick<BatchSnapshot, "status" | "reason" | "docs"> & { remote?: boolean };

/** 任务的状态点：进行中蓝、需要你处理橙、已完成绿，其余（还没开始、排队）灰。 */
export function taskTone(s: TaskLike): TaskTone {
  if (s.status === "running") return "running";
  if (batchNeedsHuman(s) || (isFinal(s.status) && s.docs.some(needsYou))) return "attention";
  if (isFinal(s.status)) return "done";
  if (s.status === "paused") return "running";
  return "preparing";
}

/** 侧栏第二行：状态；排队（交出去了、电脑还没开始，或账套正被别的电脑写）写「等电脑空闲」；进行中带进度。 */
export function taskMeta(s: TaskLike & Pick<BatchSnapshot, "total">): string {
  const tone = taskTone(s);
  if (tone === "preparing" || (s.status === "paused" && levelOf(s.reason) === "auto")) {
    if (s.reason === "account_busy" || s.remote || s.status === "paused") return op.queued;
    return op.state.preparing!;
  }
  if (tone === "running") return `${op.state.running} ${handled(s.docs)} / ${s.total}`;
  return op.state[tone]!;
}

// ── 对话框里的工作状态（同对话的状态行：assistant-ui/thread.tsx 的 RunLine，lib/managed-agents/turn.ts 的 statusText）──
/** tool：在录一张（计时 · 这张单）；queued：交出去了、还没录或暂停后等着自己恢复（计时 · 等电脑空闲 / 准备中）；
 *  input：停下等人（需要您的输入）；error：整个任务因必须人工的原因停了（错误框）；hidden：完成、没开始。since：计时起点（秒）。 */
export type BatchActivity =
  | { kind: "hidden" }
  | { kind: "input" }
  | { kind: "error"; text: string }
  | { kind: "tool" | "queued"; since: number; step: string };

const HIDDEN: BatchActivity = { kind: "hidden" };

export function batchActivity(s: (TaskLike & Pick<BatchSnapshot, "total" | "current" | "started" | "created">) | null): BatchActivity {
  if (!s) return HIDDEN;
  if (isFinal(s.status)) return s.status === "stopped" && s.reason && levelOf(s.reason) === "human" ? { kind: "error", text: reasonText(s.reason) } : HIDDEN;
  if (batchNeedsHuman(s)) return { kind: "input" };
  const since = s.started || s.created || 0;
  if (s.status === "running") {
    const doc = s.current ? s.docs.find((d) => d.doc_id === s.current) : undefined;
    return { kind: "tool", since, step: doc ? docTitle(doc.summary ?? {}, doc.doc_id) : "" };
  }
  return { kind: "queued", since, step: taskMeta(s) };
}

/** 状态行的字：「计时 · 在做什么」（同对话；没有在做的那一项只走计时）。 */
export function batchStatusText(activity: Extract<BatchActivity, { since: number }>, seconds: number): string {
  return [steps.elapsed(Math.max(0, Math.floor(seconds))), activity.step].filter(Boolean).join(" · ");
}

/** 处理过的张数（已入账、作废、跳过、停下等人）。 */
export const handled = (docs: readonly Pick<DocSnapshot, "status">[]) =>
  docs.filter((d) => DONE_STATES.has(d.status) || d.status === "waiting").length;

/** 已结束、能删除的任务（录单中的不能删）。 */
export const deletable = (s: Pick<BatchSnapshot, "status">) => isFinal(s.status);

// ── 每张单一行 ───────────────────────────────────────────────────────────────
export type DocTone = "done" | "running" | "waiting" | "attention" | "skipped";
export type DocRow = { tone: DocTone; state: string; detail: string };

/** 一张单：已入账（Express 单号 · 金额和税一致）、未完成（原因 · 已作废单号）、等待、进行中、已跳过。 */
export function docRow(doc: DocSnapshot): DocRow {
  switch (doc.status) {
    case "saved": {
      const match = doc.reason === "readback_mismatch" ? op.mismatch : op.match;
      return { tone: doc.reason === "readback_mismatch" ? "attention" : "done", state: op.doc.saved!, detail: [doc.docnum, match].filter(Boolean).join(" · ") };
    }
    case "entering":
      return { tone: "running", state: op.doc.entering!, detail: "" };
    case "pending":
      return { tone: "waiting", state: op.doc.waiting!, detail: "" };
    case "skipped":
      return { tone: "skipped", state: op.doc.skipped!, detail: "" };
    default:
      return { tone: "attention", state: op.doc.notDone!, detail: notDoneDetail(doc) };
  }
}

/** 未完成的一句：原因 · 已作废 {单号}。 */
export function notDoneDetail(doc: Pick<DocSnapshot, "reason" | "voided">): string {
  return [reasonText(doc.reason || "voided"), ...doc.voided.map((no) => op.voidedNo(no))].join(" · ");
}

/** 完成行：「完成 {n} 张，{m} 张需要处理 · 用时 {t}」（n = 已入账，m = 还没入账也没跳过的）。 */
export function finishLine(s: Pick<BatchSnapshot, "docs" | "elapsed">): string {
  const saved = s.docs.filter((d) => d.status === "saved").length;
  const left = s.docs.filter((d) => d.status !== "saved" && d.status !== "skipped").length;
  return op.finish(saved, left, duration(s.elapsed));
}

// ── 报告 ────────────────────────────────────────────────────────────────────
const cell = (value: string) => (/[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value);

/** 导出报告（CSV，Excel 直接打开）：每张单一行 —— 单号、对方、金额、状态、Express 单号、原因、用时。 */
export function reportCsv(s: Pick<BatchSnapshot, "docs">): string {
  const head = ["ref", "party", "total", "status", "number", "reason", "time"].map((key) => op.column[key]!);
  const rows = s.docs.map((doc) => {
    const row = docRow(doc);
    const reason = doc.status === "saved" ? (doc.reason === "readback_mismatch" ? op.mismatch : op.match) : row.detail;
    return [doc.summary?.ref || doc.doc_id, doc.summary?.party ?? "", formatAmount(doc.summary?.total), row.state, doc.docnum, reason, doc.seconds ? duration(doc.seconds) : ""];
  });
  // 开头的 BOM：Excel 按 UTF-8 认中文、泰文
  return "﻿" + [head, ...rows].map((line) => line.map((v) => cell(String(v ?? ""))).join(",")).join("\r\n");
}

// ── 时长 ──────────────────────────────────────────────────────────────────
/** 秒 → 「1 小时 5 分」「3 分 20 秒」「40 秒」。 */
export function duration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return op.dur.h(h, m);
  if (m) return op.dur.m(m, s);
  return op.dur.s(s);
}
