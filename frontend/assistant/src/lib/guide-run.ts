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
export type GuideReviewTable = { key: string; label: string; step?: string; rows: { code: string; name?: string; note?: string; amount?: string }[] };
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
export type GuideStatus = "asking" | "reviewing" | "confirmed" | "done" | "failed" | "cancelled" | "expired";
export type GuideView = {
  run: string; business: string; status: GuideStatus | string; version: number; company?: string; ledger?: string; period?: string;
  /** N5：confirm = 「确定」；allow = 允许卡样式（拒绝 / 允许） */
  consent?: "confirm" | "allow";
  stale?: boolean; reason?: string; outcome?: "changed" | "refused" | "stopped" | ""; refused?: Record<string, string>;
  card?: GuideCard; review?: GuideReview; result?: GuideResult;
};
export type GuidePage = { options: GuideOption[]; more: boolean };

export type GuideTransport = {
  start: (sessionId: string, business: string) => Promise<GuideView>;
  view: (run: string) => Promise<GuideView>;
  answer: (run: string, answers: Record<string, unknown>, version?: number) => Promise<GuideView>;
  confirm: (run: string, version: number) => Promise<GuideView>;
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
  confirm: (run, version) => post<GuideView>("guide_confirm", { run_name: run, version }),
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
export const confirmGuide = (run: string, version: number) => transport.confirm(run, version);
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
  !!view && !isClosed(view) && (!!view.card?.questions.some((q) => q.wait) || !!view.result?.wait);

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

/** 一题开头的值：答过的（服务端带回的 value）> 后台推荐（只有怎么办的题有 preselected，点「提交」才算）> 空。 */
export function initialValue(question: GuideQuestion): unknown {
  if (!isEmpty(question.value)) return question.value;
  if (question.tier === "how" && !isEmpty(question.preselected)) return question.preselected;
  if (question.input === "questions") {
    const pre = Object.fromEntries((question.questions ?? []).filter((s) => !isEmpty(s.preselected)).map((s) => [s.key, s.preselected]));
    return pre;
  }
  return undefined;
}
