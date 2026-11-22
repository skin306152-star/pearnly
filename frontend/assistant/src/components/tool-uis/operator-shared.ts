// 代录的工具卡片与步骤共用的（方案 cc-unified-plan 14.10，U1 定稿的事件清单：代录状态由服务端的代录事件源翻成统一对话事件，页面只认事件）：
//   - 每种工具的 input / result 形状（同服务端 express_operator/feed.py 与 U1 交来的 14.10 节）；
//   - 原因代码 → 级别（同 CC 桌面版 companion/src/companion/express_operator/contract.py 的 REASONS，一行一条，测试逐条比对）与给人看的一句；
//   - 卡片上按钮照旧调的站点接口（pearnly_core.express_operator.api 的 documents / precheck / start / task_answer / task_resume / resolve /
//     task_settle / regroup / intake，行动接口不改；不走对话的 confirm / run_tool），点完先在本页定下卡片（结果到了以它为准）。
// 不碰 React 组件；字走 i18n（原码不外露，认不出的写「需要处理」）。

import { op } from "@/i18n";
import { desktop, OPERATOR } from "@/lib/desktop";
import { getMethod, postMethod, uploadMethod } from "@/lib/frappe";
import type { ToolResultPayload } from "@/lib/managed-agents/reducer";
import { reportCaught, withReport } from "@/lib/caught";

export const API = "pearnly_core.express_operator.api.";

export { type ChoiceOption, docTitle, isRawCode, NEW_CODE, optionLabel, reasonText, RESULT_UNKNOWN } from "./operator-words";
import type { ChoiceOption } from "./operator-words";
export { cardFailed, type CardPhase, cardPhase, settleCard } from "./card-phase";

// ── 原因（contract.py）───────────────────────────────────────────────────────
export type Level = "auto" | "auto_reported" | "human";

/** 原因代码 → 级别（contract.py 的 REASONS，一行一条）。 */
export const REASONS = {
  // 必须人工（这张停下等人）
  party_missing: "human",
  item_missing: "human",
  tax_setting_mismatch: "human",
  period_locked: "human",
  period_outside: "human",
  negative_stock: "human",
  login_failed: "human",
  login_missing: "human",
  license: "human",
  company_mismatch: "human",
  company_pick: "human",
  account_in_use: "human",
  account_set_unavailable: "human",
  recipe_mismatch: "human",
  unsupported: "human",
  readback_mismatch: "human",
  unknown_screen: "human",
  menu_choice: "human",
  crash_repeated: "human",
  cc_restarted: "human",
  partial_saved: "human",
  placeholder_left: "human",
  network_down: "human",
  budget: "human",
  // 自动后报告
  duplicate_ref: "auto_reported",
  voided: "auto_reported",
  ai_resolved: "auto_reported",
  express_restarted: "auto_reported",
  // 自动（暂停后自己恢复）
  screen_locked: "auto",
  user_active: "auto",
  network_wait: "auto",
  account_busy: "auto",
  waiting_start: "auto",
} as const satisfies Record<string, Level>;

/** 遇到就整个任务停（不是只停这一张）。 */
export const BATCH_STOPPERS: ReadonlySet<string> = new Set<keyof typeof REASONS>([
  "company_mismatch",
  "login_failed",
  "login_missing",
  "license",
  "network_down",
  "budget",
  "account_in_use",
  "account_set_unavailable",
]);

/** 要设置 Express 登录才能继续的原因（继续卡片上多一个「设置」）。 */
export const LOGIN_REASONS: ReadonlySet<string> = new Set(["login_missing", "login_failed"]);

/** 原因的级别；不认识的按必须人工（同 contract.level）。 */
export function levelOf(reason: string): Level {
  return (REASONS as Record<string, Level>)[reason] ?? "human";
}

// ── 单据、分组、选项 ─────────────────────────────────────────────────────────
/** 资料的组：采购、销售、费用、收付款、不入账。 */
export const DOC_GROUPS = ["purchase", "sales", "expense", "payment", "skip"] as const;
export type DocGroup = (typeof DOC_GROUPS)[number];

const isGroup = (value: unknown): value is DocGroup => (DOC_GROUPS as readonly unknown[]).includes(value);

/** 一张单的组：给了 group 用它，否则按 kind（sales 归销售，其余归采购）。 */
export function groupOf(doc: { group?: string; kind?: string }): DocGroup {
  if (isGroup(doc.group)) return doc.group;
  if (isGroup(doc.kind)) return doc.kind;
  return doc.kind === "sales" ? "sales" : "purchase";
}

/** 一问：同一类、同一对象在几张单上只问一次（站点按类合好）；groups：这几张单的组（供应商 / 客户按方向写）。 */
export type Question = { kind: string; subject: string; label: string; options: ChoiceOption[]; doc_ids: string[]; groups?: DocGroup[] };

/** 类的先后。 */
export const KIND_ORDER = ["item", "location", "party", "account", "payment", "vat", "receipt", "department"] as const;

/** 按类分组：[类, 问题们]，先后按 KIND_ORDER。 */
export function questionGroups(questions: readonly Question[]): Array<[string, Question[]]> {
  const rank = (kind: string) => {
    const at = (KIND_ORDER as readonly string[]).indexOf(kind);
    return at < 0 ? KIND_ORDER.length : at;
  };
  const map = new Map<string, Question[]>();
  for (const q of [...questions].sort((a, b) => rank(a.kind) - rank(b.kind))) map.set(q.kind, [...(map.get(q.kind) ?? []), q]);
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

/** 选了的 → 交给 start / task_answer 的回答：{单据: [{kind, subject, value, label}]}。 */
export type Answers = Record<string, Array<{ kind: string; subject: string; value: string; label: string }>>;
export const questionKey = (q: Pick<Question, "kind" | "subject">) => `${q.kind}\n${q.subject}`;

export function answersFor(questions: readonly Question[], picked: Readonly<Record<string, string>>): Answers {
  const out: Answers = {};
  for (const q of questions) {
    const value = picked[questionKey(q)];
    if (!value) continue;
    for (const id of q.doc_ids) (out[id] ??= []).push({ kind: q.kind, subject: q.subject, value, label: q.label });
  }
  return out;
}

// ── 事件形状（方案 14.10，U1 定稿）────────────────────────────────────────────
/** 一张单（资料清单、开始前框、单据行）。 */
/** moves：这张能拖到哪几组（站点按产品声明给，没有 = 不能拖）。 */
export type DocRef = { doc_id: string; ref?: string; party?: string; total?: string; date?: string; group?: string; kind?: string; moves?: string[] };

/** operator_sources 的 input；交单后的结果是按组的张数（"" = 未分组）。准备中没有结果，展开时调 documents（服务端分页）。 */
export type SourcesInput = { task: string; company: string; company_title?: string; period: string };
export type SourcesResult = { total?: number; groups?: Partial<Record<DocGroup | "", number>> };

/** operator_upload 的 input / result（已识别：单据列表；失败：is_error、state failed + code，unavailable = 读不到）。
 *  排队 / 识别中是 pearnly.progress（queued / reading_pages，done / total = 页）。 */
export type UploadInput = { task: string; request_key?: string; filename: string; size?: number; batch?: string; source?: string };
export type UploadResult = {
  state?: "done" | "failed";
  page_count?: number;
  code?: string;
  documents?: Array<{ doc_id?: string; ref?: string; date?: string; party?: string; in_period?: boolean; listed?: boolean }>;
};

/** operator_document 的 input / result（n 从 1 数；state saved / not_done / cancelled / skipped，not_done 时 is_error；reason 是代码，
 *  结果不明 = push.result_unknown 且 result_unknown）。排着是 pearnly.progress（queued；被结果不明的单挡着 blocked，detail = 「单号 · 对方」）。 */
export type DocumentInput = { task: string; n?: number; doc_id: string; ref?: string; party?: string; group?: string };
export type DocumentResult = {
  state?: "saved" | "not_done" | "cancelled" | "skipped";
  docnum?: string;
  voided_docnum?: string;
  reason?: string;
  level?: Level;
  result_unknown?: boolean;
  ref?: string;
  party?: string;
};

/** operator_start 的 input（卡片自己调 documents / precheck / resolve / regroup / start）；交单后 user.custom_tool_result 交来的结果。 */
export type StartInput = { task: string; company: string; company_title?: string; period: string };
export type StartResult = { sent?: number; not_sent?: Array<DocRef & { reason?: string }>; computer?: string; start_at?: number };

/** operator_choose 的 input：每张没做成、要人处理的单一张（整批停的不在这里）。 */
/** detail：CC 回执的说明原文（AI 接手的结论在最前，站点 feed.reason_detail，最多 2000 字），没有就不显示。 */
export type ChooseInput = { task: string; company: string; doc_id: string; ref?: string; party?: string; reason?: string; reason_text?: string; detail?: string; level?: Level; result_unknown?: boolean };

/** operator_resume 的 input：整批停（或停止，reason = stopped）时一张；doc_ids = 会被继续的几张。 */
export type ResumeInput = { task: string; company: string; reason: string; detail?: string; doc_id?: string; ref?: string; party?: string; doc_ids?: string[] };

/** 站点 precheck 一张单的结论（开始前、中途选都用）。 */
/** current：预选；value_label：预选的说法；search：只能去服务端搜的类（物料等，没有选项）。 */
export type PrecheckNeed = { kind: string; subject: string; label: string; options: ChoiceOption[]; current?: string; value_label?: string; search?: string };
/** state written：同一张票已写进 Express（docnum 是它在 Express 里的单号），默认不交，人可以点「再录一次」（用户 11-09）。 */
export type PrecheckDoc = { doc_id: string; state: "ready" | "choose" | "waiting" | "blocked" | "written"; reason?: string; message?: string; needed?: PrecheckNeed[]; docnum?: string };
/** 这次写进哪儿（B4，precheck 回包的 target / targets，取值同 start 实际用的那一份）。count：几张单写进这份（targets 里才有）。 */
export type WriteTarget = { account_set?: string; account_set_title?: string; account_set_path?: string; connection_title?: string; computer?: string; count?: number };

/** precheck 的结论 → 一问一项（同一类、同一对象在几张单上只问一次），groupOfDoc：单据的组（供应商 / 客户按方向写）。 */
export function questionsOf(prechecks: readonly PrecheckDoc[], groupOfDoc: (id: string) => DocGroup = () => "purchase"): Question[] {
  const map = new Map<string, Question>();
  for (const doc of prechecks) {
    if (doc.state !== "choose") continue;
    for (const need of doc.needed ?? []) {
      const key = questionKey(need);
      const found = map.get(key);
      if (found) {
        if (!found.doc_ids.includes(doc.doc_id)) found.doc_ids.push(doc.doc_id);
        found.groups = [...(found.groups ?? []), groupOfDoc(doc.doc_id)];
        continue;
      }
      map.set(key, { kind: need.kind, subject: need.subject, label: need.label || need.subject, options: need.options ?? [], doc_ids: [doc.doc_id], groups: [groupOfDoc(doc.doc_id)] });
    }
  }
  return [...map.values()];
}

/** 工具结果的正文按 JSON 读（站点交来的结果是一段 JSON 文字）；读不出回 { code: 原文 }。 */
export function resultOf<T extends object>(result: unknown): (Partial<T> & { code?: string }) | null {
  if (result === undefined || result === null) return null;
  const text = typeof result === "object" && "text" in (result as object) ? (result as ToolResultPayload).text : String(result);
  // 纯文字（失败码）不解析；像 JSON 的读不出才算出错（报一次，按纯文字显示）
  if (/^\s*[{[]/.test(text)) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed && typeof parsed === "object") return parsed as Partial<T>;
    } catch (caughtError) {
      reportCaught(caughtError, "operator-result");
    }
  }
  return { code: text.trim() } as Partial<T> & { code?: string };
}

// ── 行动（行动接口不改，卡片照旧调它们；不调对话的 confirm / run_tool）──────────────────
type Get = <T>(method: string, args?: Record<string, string | number | undefined>) => Promise<T>;
type Post = <T>(method: string, args?: Record<string, unknown>) => Promise<T>;
type Upload = <T>(method: string, args: Record<string, string>, file: File) => Promise<T>;
let site: { get: Get; post: Post; upload: Upload } = { get: getMethod, post: postMethod, upload: uploadMethod };
/** 测试换掉站点请求。 */
export const setOperatorSite = (next: Partial<typeof site>) => {
  site = { ...site, ...next };
};

/** 资料一页的大小（服务端分页，「显示更多」再取下一页）。 */
export const PAGE = 50;

type DocumentsReply = { documents?: DocRef[]; next_offset?: number | null; total?: number };

export const operatorActions = {
  documents: async (company: string, period: string, offset: number) => {
    const reply = await site.get<DocumentsReply>(API + "documents", { company, period, offset, limit: PAGE });
    const documents = (reply?.documents ?? []).map((d) => ({ ...d, doc_id: String(d.doc_id) }));
    return { documents, next: typeof reply?.next_offset === "number" ? reply.next_offset : null, total: reply?.total };
  },
  /** task：开始卡的代录任务（服务端在它的对话里预检：按本对话账套准备，已定就不问账套）；中途选那些不给。 */
  precheck: async (company: string, docIds: readonly string[], task = "") => {
    if (!docIds.length) return { documents: [] as PrecheckDoc[], targets: [] as WriteTarget[], card: undefined as unknown };
    // 一批写进几份账套时服务端 target 为空、targets 列出每一份（张数多的在前，api.precheck）；旧服务端只给 target。
    // card：共用写账前置判定合成的「开始前确认」卡片（write_precheck，形状见 precheck-shared.ts）；旧服务端没有，页面按 documents 合成
    const reply = await site.post<{ documents?: PrecheckDoc[]; target?: WriteTarget | null; targets?: WriteTarget[]; card?: unknown }>(API + "precheck", { company, doc_ids: JSON.stringify(docIds), ...(task ? { task } : {}) });
    const targets = reply?.targets?.length ? reply.targets : reply?.target ? [reply.target] : [];
    return { documents: (reply?.documents ?? []).map((d) => ({ ...d, doc_id: String(d.doc_id) })), targets, card: reply?.card };
  },
  /** 开始卡里答的题只记下（{单据: [{kind, subject, value, label}]}，同写账前置判定的记法），不开始写：开始只由用户在对话里一句话下令
   *  （写入计划，用户 11-10；页面没有开始接口）。 */
  remember: (company: string, answers: Answers, task = "") =>
    site.post(API + "remember", { company, answers: JSON.stringify(answers), ...(task ? { task } : {}) }),
  answer: (task: string, docId: string, answers: Answers) =>
    site.post(API + "task_answer", { name: task, doc_id: docId, answers: JSON.stringify(answers[docId] ?? []) }),
  /** 几张合成一张卡后的一次作答（{单据: [{kind, subject, value, label}]}）。 */
  answerMany: (task: string, answers: Answers) => site.post(API + "task_answer_many", { name: task, answers: JSON.stringify(answers) }),
  resume: (task: string) => site.post(API + "task_resume", { name: task }),
  settle: (task: string) => site.post(API + "task_settle", { name: task }),
  resolve: async (company: string, docId: string, entered: boolean) => {
    const reply = await site.post<{ ok?: boolean }>(API + "resolve", { company, doc_id: docId, entered: entered ? 1 : 0, docnum: "" });
    if (reply && reply.ok === false) throw new Error("resolve refused");
  },
  regroup: async (company: string, docId: string, group: DocGroup) => {
    const reply = await site.post<{ ok?: boolean }>(API + "regroup", { company, doc_id: docId, group });
    if (reply && reply.ok === false) throw new Error("regroup refused");
  },
  /** 放资料进来（一份一个新请求号：同一份再放照常存、照常识别）；task 让站点记进任务的上传记录，之后由事件源出 operator_upload。 */
  intake: (company: string, task: string, file: File) =>
    site.upload(API + "intake", { company, task, request_key: crypto.randomUUID() }, file),
};

/** 本页所在的电脑能设置 Express 登录（CC 桌面版窗口、支持实操）：原生小窗设置。不能回 false。 */
export async function authorizeHere(): Promise<boolean> {
  const found = await desktop().catch(withReport(null, "operator-bridge"));
  if (!found || !(await found.state()).features?.includes(OPERATOR)) return false;
  const reply = await found.call("operator_authorize");
  return !!reply?.ok;
}

/** 本页在支持实操的 CC 桌面版窗口里，Express 登录设置了没有、授权的是哪个账号（operator_state 的 login / login_user；
 *  这个账号也是直写单据的录入人）；不在回 null。 */
export async function loginHere(): Promise<{ set: boolean; user: string } | null> {
  const found = await desktop().catch(withReport(null, "operator-bridge"));
  if (!found || !(await found.state()).features?.includes(OPERATOR)) return null;
  const reply = (await found.call("operator_state")) as unknown as { ok?: boolean; login?: boolean; login_user?: string };
  return reply?.ok ? { set: !!reply.login, user: String(reply.login_user ?? "") } : null;
}
