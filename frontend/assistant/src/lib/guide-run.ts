// 按表单的引导（引导式分支 3 接线，服务端 pearnly_core/assistant/guide/run.py · view()，接口在 pearnly_core.assistant.api 的 guide_*）：
// 开一次、按 run 取卡、作答、确定、取消、搜索选项。卡的内容（题目、选项、复核、结果）都由服务端出好（标题已按本页语言翻好），
// 页面只画、只把点选 / 填写的值按键交回。形状照共享笔记「引导式-声明接口说明」「引擎通用能力 N1–N10 约定」「documents 源约定」。
// 传输可换（setGuideTransport）：页面走真接口，展示页（kit/guide-run.tsx）走替身，同一套组件。
import { get, post } from "@/lib/frappe";
import { listSessionFiles, uploadFile } from "@/lib/managed-agents/api";
import { type AmountRow, amountsProblem, rowsToSend, splitProblem, sumMinor, fromMinor, valueOk } from "@/lib/guide-inputs";

export type GuideOption = { code: string; name?: string; note?: string; amount?: string; max?: string; kind?: string };
/** 子题（N1a 输入 questions、N1b 结果里的追问）：输入只在 SUB_INPUTS 里；title 服务端已翻好。 */
export type GuideSubInput = "choice" | "multi" | "number" | "text" | "date" | "month" | "time";
export type GuideSub = {
  key: string; input: GuideSubInput; title: string; required: boolean;
  options?: { code: string; name: string }[]; preselected?: unknown; facts?: string[]; note?: string;
};
export type GuideInput = "choice" | "multi" | "amounts" | "split" | "file" | "month" | "date" | "time" | "number" | "text" | "questions";
export type GuideQuestion = {
  key: string; input: GuideInput; rule: string; tier: "how" | "whether" | string; title: string; required: boolean;
  options?: GuideOption[]; more?: boolean; search?: boolean; facts?: string[]; wait?: boolean; preselected?: unknown; value?: unknown;
  /** N4：金额多选行上可以是 0（盘点数到 0）。待定：view 还没带这个键（声明有），带上前当 false */
  zero?: boolean;
  /** 分摊的总额。待定：view 还没带；没带时取本卡前面金额多选那一步的合计 */
  total?: string;
  /** N1a：一组子题 */
  questions?: GuideSub[];
};
export type GuideDecided = { key: string; input?: string; title: string; value: unknown; value_label?: string; source?: string };
export type GuideReturned = Record<string, { reason: string; text?: string }>;
export type GuideCard = {
  version?: number; questions: GuideQuestion[]; decided: GuideDecided[]; ready: boolean; answers?: Record<string, unknown>;
  returned?: GuideReturned;
};
export type GuideReviewLine = { key: string; label: string; value: string; step?: string };
/** 表格里能就地改的一格（P3 预扣税逐行复核，11-16）：选项类给 options，其余按输入方式一格。 */
export type GuideCellEdit = { input: "choice" | "number" | "date" | "text"; options?: { code: string; name: string }[] };
export type GuideTableRow = {
  code: string; name?: string; note?: string; amount?: string;
  /** 各列的值（按 columns 的 key） */ cells?: Record<string, string>;
  /** 能改的格；不在里面的只读。改了走 guide_edit(run, row.code, 格, 值, version) */ editable?: Record<string, GuideCellEdit>;
  /** 这一行要人看的问题（已翻译的句子） */ issues?: string[];
};
export type GuideReviewTable = { key: string; label: string; step?: string; columns?: { key: string; label: string }[]; rows: GuideTableRow[] };
export type GuideReview = {
  version: number; ready: boolean; problem?: string; problem_text?: string; total?: string;
  lines?: GuideReviewLine[]; tables?: GuideReviewTable[];
};
export type GuideResultFile = { file: string; name: string };
export type GuideResult = {
  status: string; docnum?: string; reason_text?: string; total?: string; customer?: string; wait?: boolean;
  /** N2：结果带的文件（对话文件 id + 文件名） */
  files?: GuideResultFile[];
  /** N1b：确定之后冒出来的追问 */
  questions?: GuideSub[];
};
// ── 按单据（documents 源，共享笔记「引导式-documents源-约定」；服务端 assistant/guide/documents.py · view）──────────────
/** 票面（write_precheck/review.py · face）：金额是字符串，原样保留人填的值。 */
export type DocFace = {
  doc_no: string; party_name: string; party_tax: string; party_branch: string; date: string; subtotal: string; vat: string; total: string;
  lines: { description: string; qty: string; unit_price: string; amount: string }[];
};
/** 标记码（review.MARKS）：number 单号、duplicate 重复、tax_id 税号、amount 金额、date 日期。 */
export type DocMark = "number" | "duplicate" | "tax_id" | "amount" | "date";
export type DocRow = {
  doc_id: string; version?: string | number; face: DocFace; marks: DocMark[]; cells: Record<string, DocMark>; blocking: string[];
  /** 待定（已问 guided-erp）：这一行能改的格；没带时按 review.EDITABLE（表头八格 + 明细四列）都能改 */
  editable?: string[];
};
export type GuideDocReview = {
  rows: DocRow[]; held: string[]; seen: string; ready: boolean; facts?: Record<string, string[]>;
};
export type GuideUploads = { files: { batch: string; state: "reading" | "done" | "failed" | string; reason?: string; reason_text?: string }[]; wait: boolean };
export type GuideDocResult = {
  status: string; wait?: boolean;
  rows: { doc_id: string; status: "entered" | "queued" | "held" | "failed" | string; docnum?: string; reason?: string; reason_text?: string;
    /** 待定：结果行要带单据自己的号（票面 doc_no），没带时显示 doc_id */ doc_no?: string }[];
};
export const isDocReview = (review: unknown): review is GuideDocReview => !!review && Array.isArray((review as GuideDocReview).rows);
export const isDocResult = (result: unknown): result is GuideDocResult => !!result && Array.isArray((result as GuideDocResult).rows);

export type GuideStatus = "asking" | "reviewing" | "confirmed" | "done" | "failed" | "cancelled" | "expired";
export type GuideView = {
  run: string; business: string; status: GuideStatus | string; version: number; company?: string; ledger?: string; period?: string;
  /** N5：confirm = 「确定」；allow = 允许卡样式（拒绝 / 允许） */
  consent?: "confirm" | "allow";
  /** 允许式确定的问句（同线上允许卡「要……吗」，服务端按本页语言翻好；引擎窗口 11-16 约定）；没有时标题栏写业务名 */
  consent_title?: string; consent_title_key?: string;
  /** 允许式确定问句下面那行浅色事实（会动到哪家公司、哪本账套；同允许卡的 note），没有不出（已向引擎窗口约） */
  consent_note?: string;
  stale?: boolean; reason?: string; outcome?: "changed" | "refused" | "stopped" | "";
  /** 不收的原因：引擎回 {键: {code, text}}（text 已翻译，11-16 定）；documents 的 guide_edit 回 {格: 码} + refused_text {格: 已翻译} */
  refused?: Record<string, string | { code: string; text?: string }>; refused_text?: Record<string, string>;
  card?: GuideCard & { source?: "documents"; wait?: boolean;
    /** documents：停下 / 等待的单（带原因的那句，已翻译），事实行不是题（A4 约定） */ facts?: Record<string, string[]> };
  /** 代录开始前（A4，共享笔记「A4-代录开始前题卡-约定」）：卡头写进哪 · 没电脑能写时那句。Express 登录不在这里（服务端拿不到，
   *  页面照旧问本机 CC 桌面版，operator-shared.loginHere；ERP 直写窗口 11-16 更正） */
  operator?: { targets: { account_set?: string; account_set_title?: string; account_set_path?: string; connection_title?: string; computer?: string; count?: number }[];
    computer?: string };
  review?: GuideReview | GuideDocReview; result?: GuideResult | GuideDocResult;
  /** 按单据办的业务（采购、销售）回 "documents"：卡按 stage 分段 */
  source?: "documents";
  /** documents：此刻在哪一段（files 交文件 / reading 识别中 / asking 题卡 / review 复核） */
  stage?: "files" | "reading" | "asking" | "review";
  uploads?: GuideUploads;
};
export type GuidePage = { options: GuideOption[]; more: boolean };

export type GuideTransport = {
  start: (sessionId: string, business: string) => Promise<GuideView>;
  view: (run: string) => Promise<GuideView>;
  answer: (run: string, answers: Record<string, unknown>, version?: number) => Promise<GuideView>;
  confirm: (run: string, version: number, seen?: string) => Promise<GuideView>;
  /** documents 复核就地改一格（guided-erp 加 guide_edit；回整份 view，改不了带 outcome refused） */
  edit: (run: string, docId: string, field: string, value: string, version: number) => Promise<GuideView>;
  /** 复核卡点一行回那一步（待定：已问引擎窗口，接口名暂定 guide_reopen） */
  reopen: (run: string, step: string, version: number) => Promise<GuideView>;
  /** N1b：确定之后结果里的追问（P3 加 guide_follow） */
  follow: (run: string, answers: Record<string, unknown>, version: number) => Promise<GuideView>;
  cancel: (run: string) => Promise<GuideView>;
  search: (run: string, key: string, txt: string, start: number) => Promise<GuidePage>;
  /** 交文件那一题：本对话已交的文件、当场添加（传进本对话） */
  files: (sessionId: string) => GuideFiles;
};
export type GuideFiles = {
  list: () => Promise<{ id: string; filename: string }[]>;
  upload: (file: File) => Promise<{ fileId: string; filename: string }>;
};

export const SEARCH_PAGE = 20;

const real: GuideTransport = {
  start: (sessionId, business) => post<GuideView>("guide_start", { session_id: sessionId, business }),
  view: (run) => get<GuideView>("guide_view", { run_name: run }),
  answer: (run, answers, version) =>
    post<GuideView>("guide_answer", { run_name: run, answers: JSON.stringify(answers), ...(version === undefined ? {} : { version }) }),
  confirm: (run, version, seen) => post<GuideView>("guide_confirm", { run_name: run, version, ...(seen === undefined ? {} : { seen }) }),
  edit: (run, docId, field, value, version) => post<GuideView>("guide_edit", { run_name: run, doc_id: docId, field, value, version }),
  reopen: (run, step, version) => post<GuideView>("guide_reopen", { run_name: run, step, version }),
  follow: (run, answers, version) => post<GuideView>("guide_follow", { run_name: run, answers: JSON.stringify(answers), version }),
  cancel: (run) => post<GuideView>("guide_cancel", { run_name: run }),
  search: (run, key, txt, start) =>
    get<GuidePage>("guide_search", { run_name: run, key, txt, start, page_length: SEARCH_PAGE }),
  files: (sessionId) => ({
    list: () => listSessionFiles(sessionId).then((rows) => rows.filter((row) => row.kind === "upload")),
    upload: (file) => uploadFile(sessionId, file),
  }),
};

let transport: GuideTransport = real;
/** 展示页换成替身（只在 kit 里调）。 */
export const setGuideTransport = (next: GuideTransport) => { transport = next; };

export const startGuide = (sessionId: string, business: string) => transport.start(sessionId, business);
export const viewGuide = (run: string) => transport.view(run);
export const answerGuide = (run: string, answers: Record<string, unknown>, version?: number) => transport.answer(run, answers, version);
export const confirmGuide = (run: string, version: number, seen?: string) => transport.confirm(run, version, seen);
export const editGuide = (run: string, docId: string, field: string, value: string, version: number) => transport.edit(run, docId, field, value, version);
export const reopenGuide = (run: string, step: string, version: number) => transport.reopen(run, step, version);
export const followGuide = (run: string, answers: Record<string, unknown>, version: number) => transport.follow(run, answers, version);
export const cancelGuide = (run: string) => transport.cancel(run);
export const guideFiles = (sessionId: string | null): GuideFiles | null => (sessionId ? transport.files(sessionId) : null);
export const searchGuide = (run: string, key: string, txt: string, start = 0) => transport.search(run, key, txt, start);

/** 运行已经结束、卡只剩一行（F5）：取消、失效，或上下文变了（stale，服务端下次取时置 expired）。 */
export const isClosed = (view: Pick<GuideView, "status" | "stale">) => view.status === "cancelled" || view.status === "expired" || !!view.stale;

/** 等外部读取时再取的间隔（毫秒）：3、5、8、13 秒……封顶 30 秒，最多 WAIT_TRIES 次（不常驻轮询）。 */
export const WAIT_TRIES = 12;
export function waitDelay(attempt: number): number {
  let a = 3000, b = 5000;
  for (let i = 0; i < attempt; i++) [a, b] = [b, a + b];
  return Math.min(a, 30000);
}

/** 这张卡要不要过一会儿再取：有题在等读取，或结果还在等。 */
export const isWaiting = (view: GuideView | null) =>
  !!view && !isClosed(view) && (!!view.card?.wait || !!view.card?.questions.some((q) => q.wait) || !!view.result?.wait || !!view.uploads?.wait);

/** 一开始停在哪一题：有退回的停在第一道退回的题，否则第一道。 */
export function firstPosition(questions: readonly GuideQuestion[], returned: GuideReturned | undefined): number {
  const at = questions.findIndex((q) => !!returned?.[q.key]);
  return at < 0 ? 0 : at;
}

// ── 一题的值：何时算答好、交回什么形状（同服务端 steps.value_problem）────────────────────────────────────────────

const SUB_OK = (sub: GuideSub, value: unknown): boolean => {
  if (sub.input === "choice") return typeof value === "string" && !!value && (!sub.options || sub.options.some((o) => o.code === value));
  if (sub.input === "multi") return Array.isArray(value) && value.length > 0;
  return typeof value === "string" && valueOk(sub.input, value);
};

/** 一组子题答好了没有：要答的都答了、答了的都合形状。 */
export function subsAnswered(subs: readonly GuideSub[], value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const given = value as Record<string, unknown>;
  return subs.every((sub) => (given[sub.key] === undefined || given[sub.key] === "" ? !sub.required : SUB_OK(sub, given[sub.key])));
}

/** 分摊的总额：题目带了用题目的；没带用本卡（或已答）里金额多选那一步的合计。 */
export function splitTotal(question: GuideQuestion, all: readonly GuideQuestion[], values: Record<string, unknown>): string {
  if (question.total) return question.total;
  const source = all.find((q) => q.input === "amounts" && Array.isArray(values[q.key]));
  if (source) return fromMinor(sumMinor(values[source.key] as AmountRow[]));
  const answered = Object.values(values).find((v) => Array.isArray(v) && v.length && typeof v[0] === "object" && "amount" in (v[0] as object));
  return answered ? fromMinor(sumMinor(answered as AmountRow[])) : "";
}

/** 这一题此刻的值能不能交（required 的没答不能交；可选的没答可以跳过）。 */
export function answeredOk(question: GuideQuestion, value: unknown, total = ""): boolean {
  switch (question.input) {
    case "choice": return typeof value === "string" && !!value;
    case "multi":
    case "file": return Array.isArray(value) && value.length > 0;
    case "amounts": return Array.isArray(value) && amountsProblem(question.options ?? [], value as AmountRow[], !!question.zero).problem === "";
    case "split": return Array.isArray(value) && splitProblem(value as AmountRow[], total) === "";
    case "questions": return subsAnswered(question.questions ?? [], value);
    default: return typeof value === "string" && valueOk(question.input as "month" | "date" | "time" | "number" | "text", value);
  }
}

export const isEmpty = (value: unknown) =>
  value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0)
  || (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0);

/** 交回服务端的值：金额行规范成两位小数、只带填了的；数字去空白；子题只带答了的。 */
export function toSend(question: GuideQuestion, value: unknown): unknown {
  if (question.input === "amounts" || question.input === "split") return rowsToSend(value as AmountRow[]);
  if (question.input === "number" && typeof value === "string") return value.trim();
  if (question.input === "questions" && value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).filter(([, v]) => !isEmpty(v)));
  }
  return value;
}

/** 一题开头的值：只有答过的（服务端带回的 value）；后台推荐（preselected）只在选项上标「推荐」，不预选、点了才算（用户口径 11-16）。 */
export function initialValue(question: GuideQuestion): unknown {
  if (!isEmpty(question.value)) return question.value;
  return undefined;
}

// ── 单据复核的格 ─────────────────────────────────────────────────────────────────────────────────────
export const HEAD_FIELDS = ["doc_no", "party_name", "party_tax", "party_branch", "date", "subtotal", "vat", "total"] as const;
export const LINE_FIELDS = ["description", "qty", "unit_price", "amount"] as const;
const ALL_EDITABLE = new Set<string>([...HEAD_FIELDS, ...LINE_FIELDS.map((f) => `lines.*.${f}`)]);

/** 这一格能不能改：行上带了 editable 就照它（明细格写 lines.<行>.<列> 或 lines.*.<列>）；没带按 review.EDITABLE。 */
export function canEdit(row: DocRow, field: string): boolean {
  const line = /^lines\.(\d+)\.(\w+)$/.exec(field);
  const wild = line ? `lines.*.${line[2]}` : field;
  if (row.editable) return row.editable.includes(field) || row.editable.includes(wild);
  return ALL_EDITABLE.has(wild);
}

/** 一格此刻的值（表头或明细）。 */
export function cellValue(face: DocFace, field: string): string {
  const line = /^lines\.(\d+)\.(\w+)$/.exec(field);
  if (line) return String((face.lines[Number(line[1])] as Record<string, string> | undefined)?.[line[2]] ?? "");
  return String((face as unknown as Record<string, string>)[field] ?? "");
}

/** 某个键（不给就取第一个）不收的说明：只用服务端翻好的字（text / refused_text），没有就回 ""（不把码给人看）。 */
export function refusedText(view: Pick<GuideView, "refused" | "refused_text">, key?: string): string {
  const keys = key ? [key] : Object.keys(view.refused ?? {});
  for (const k of keys) {
    const found = view.refused?.[k];
    const text = (typeof found === "object" && found ? found.text : "") || view.refused_text?.[k] || "";
    if (text) return text;
  }
  return "";
}
export const isRefused = (view: Pick<GuideView, "refused">, key: string) => !!view.refused?.[key];
