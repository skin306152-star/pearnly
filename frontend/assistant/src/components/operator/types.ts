// 「实操」工作区（CC 在 Express 窗口里逐张填写）的数据形状。设计稿：https://claude.ai/artifact/SgtxECheQE4e6gtF1G4WKW。
//
// 与 CC 桌面版 companion/src/companion/express_operator/ 一一对应，改一边要改另一边：
//   - contract.py：一张单的状态、任务（批次）状态、原因代码 → 级别（自动 / 自动后报告 / 必须人工）、整批停的原因（BATCH_STOPPERS）；
//   - batch.py 的 Runner.snapshot()：任务进度（BatchSnapshot）；
//   - 站点 pearnly_core.express_operator.api：documents / precheck / start / regroup / intake（live-source.ts）。
//
// OperatorSource 是接口：真实实现在 live-source.ts（站点接口 + CC 桌面版桥 operator_*），预览与测试用 mock-source.ts。
// 任务本身（列表、改名、置顶、状态）以站点的任务记录为准（TaskRecord，跨设备）；桥上的进度只给这台电脑上在跑的任务补实时进度
// （批次号 = 任务号）。
// 时间一律是秒（Unix 秒，同 Python time.time()）；金额是十进制字符串，只插千分位显示。界面上的词一律按代码取（i18n.ts 的 op），
// 原因代码、内部状态名不直接显示。

import type { TaskUpload } from "@/components/run/types";

// ── 一张单的状态（contract.py）──────────────────────────────────────────────
export const DOC_STATES = ["pending", "entering", "saved", "voided", "waiting", "skipped"] as const;
export type DocStatus = (typeof DOC_STATES)[number];
/** 处理完了的（续做时跳过）。等人（waiting）另算。 */
export const DONE_STATES: ReadonlySet<string> = new Set<DocStatus>(["saved", "voided", "skipped"]);

// ── 任务状态 ──────────────────────────────────────────────────────────────
export const BATCH_STATES = ["ready", "running", "paused", "stopped", "finished"] as const;
export type BatchStatus = (typeof BATCH_STATES)[number];

// ── 级别（三级）────────────────────────────────────────────────────────────
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
  crash_repeated: "human",
  cc_restarted: "human",
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
export type ReasonCode = keyof typeof REASONS;

/** 遇到就整个任务停（不是只停这一张）。 */
export const BATCH_STOPPERS: ReadonlySet<string> = new Set<ReasonCode>([
  "company_mismatch",
  "login_failed",
  "login_missing",
  "license",
  "network_down",
  "budget",
  "account_in_use",
  "account_set_unavailable",
]);

/** 原因的级别；不认识的按必须人工（同 contract.level）。 */
export function levelOf(reason: string): Level {
  return (REASONS as Record<string, Level>)[reason] ?? "human";
}

// ── 资料分组 ───────────────────────────────────────────────────────────────
/** 资料区的组：采购、销售、费用、收付款、不入账（不入账的不交给 CC）。 */
export const DOC_GROUPS = ["purchase", "sales", "expense", "payment", "skip"] as const;
export type DocGroup = (typeof DOC_GROUPS)[number];

// ── 资料（站点 documents）──────────────────────────────────────────────────
/** 这家这个月的一张单。group 是资料区的组（站点没给时按 kind 归采购 / 销售）；stopped：在产品里停下的原因。 */
export type DocumentOption = {
  id: string;
  kind?: string;
  group: DocGroup;
  date?: string;
  party?: string;
  ref?: string;
  total?: string;
  stopped?: string;
};
export type DocumentPage = { documents: DocumentOption[]; next: number | null; total?: number };

// ── 开始前确认（站点 precheck）────────────────────────────────────────────────
export type PrecheckOption = { code: string; name: string };
/** 一项要人定的：kind 是类（item 商品、party 供应商 / 客户、account 费用科目、payment 付款方式、vat 税率与票面不符……），
 *  subject 是对象（同一个新商品、同一个新供应商在几张单上只问一次）；options 里 code "__new__" = 新建。 */
export type PrecheckNeed = { kind: string; subject: string; label: string; options: PrecheckOption[] };
export type PrecheckState = "ready" | "choose" | "waiting" | "blocked";
export type PrecheckDoc = { doc_id: string; state: PrecheckState; reason?: string; message?: string; needed?: PrecheckNeed[] };
/** 这次写进哪份账套（站点 precheck 回包的 target，取值同 start 实际用的那一份）：「开始前」框第一行、输入框下面那一行的账套。 */
export type WriteTarget = { account_set: string; account_set_title: string; connection_title: string; computer: string };
export type PrecheckReply = { documents: PrecheckDoc[]; target: WriteTarget | null };
/** 交给 start 的回答：{单据: [{kind, subject, value, label}]}。 */
export type Answer = { kind: string; subject: string; value: string; label: string };
export type Answers = Record<string, Answer[]>;
export const NEW_CODE = "__new__";

// ── 进度（Runner.snapshot()）────────────────────────────────────────────────
/** kind：form_open 打开表单、dialog 弹窗、check 核对、takeover 接手、refocus 切回、saved 已存、abandon 放弃作废。 */
export type OperatorEvent = {
  t: number;
  kind: string;
  name?: string;
  where?: string;
  problem?: string;
  status?: string;
  note?: string;
  docnum?: string;
  ok?: boolean;
  doc_type?: string;
  error?: string;
};
export type DocReport = { reason: string; docnum?: string; where?: string };

/** 一张单的要点（桥从草稿补上，或这一页从资料里补上）。 */
export type DocSummary = { date?: string; party?: string; ref?: string; total?: string; lines?: number; group?: DocGroup };

export type DocSnapshot = {
  doc_id: string;
  doc_type: string;
  status: DocStatus;
  docnum: string;
  voided: string[];
  placeholders: string[];
  reason: string;
  reports: DocReport[];
  evidence?: "kept" | "expired" | "none";
  issues?: unknown[];
  seconds: number;
  summary?: DocSummary;
  /** 交单时站点就没收（只在这一页知道）：只能跳过。 */
  refused?: boolean;
  /** 排着的这张被同一份账套里哪张结果不明的单挡着（「单号 · 对方」，站点给的）。 */
  blocked?: string;
};

export type BatchSnapshot = {
  id: string;
  status: BatchStatus;
  reason: string;
  company: string;
  account_set: string;
  total: number;
  counts: Partial<Record<DocStatus, number>>;
  /** 正在做的那张，空 = 没在做。 */
  current: string;
  index: number;
  events: OperatorEvent[];
  started: number;
  finished: number;
  elapsed: number;
  remaining_estimate: number;
  docs: DocSnapshot[];
  pauses?: unknown[];
  /** 进度在执行的电脑上、这一页看不到（网页、手机，或桥上还没有这个任务）：只有交单结果，没有停止 / 跳过。 */
  remote?: boolean;
  /** 桥上：这台电脑现在在跑它（false = CC 重启前留下的、剩下的单还没来，进度以站点为准）。 */
  live?: boolean;
  /** 桥上：站点上的公司编号（向站点核这一批用）。 */
  site?: string;
  company_title?: string;
  created?: number;
  start_at?: number;
  computer?: string;
  /** 处理的月份（YYYY-MM）。 */
  period?: string;
};

/** 侧栏的一行。 */
export type BatchListItem = Pick<BatchSnapshot, "id" | "status" | "reason" | "company" | "total" | "counts" | "docs"> & {
  company_title?: string;
  created: number;
  period?: string;
  remote?: boolean;
};

// ── 任务（服务端记录，pearnly_core.express_operator.tasks；跨设备看、改、收尾以它为准）──────────────────────
/** 准备中 → 等电脑空闲 → 进行中 → 需要你处理 / 已完成 / 已停止。 */
export const TASK_STATES = ["preparing", "queued", "running", "attention", "finished", "stopped"] as const;
export type TaskStatus = (typeof TASK_STATES)[number];
/** 交出去的一张单在站点上的结论（state：queued / pending 还没定，saved / not_done / cancelled / skipped）。 */
export type TaskDocument = {
  doc_id: string;
  doc_group: string;
  state: string;
  docnum: string;
  voided_docnum: string;
  reason: string;
  ref: string;
  party: string;
  /** 排着的被哪张结果不明的单挡着（「单号 · 对方」），没有是空。 */
  blocked_by: string;
};
/** 一个任务（task_list 不带 documents，task_get 带）。时间是站点的日期时间字符串，空 = 没有。 */
export type TaskRecord = {
  name: string;
  company: string;
  company_title: string;
  period: string;
  /** 改过的名字；空 = 按「公司 · 月份」显示。 */
  title: string;
  starred: boolean;
  status: TaskStatus;
  computer: string;
  start_at: string;
  started_at: string;
  finished_at: string;
  /** 任务里的对话（对话框甲）：第一次打字发问时建；空 = 还没有。 */
  session_id: string;
  creation: string;
  modified: string;
  documents?: TaskDocument[];
};
export type TaskPage = { tasks: TaskRecord[]; next: number | null };
/** 改名、置顶随时可改；公司、月份只在准备中能改。 */
export type TaskPatch = { title?: string; starred?: boolean; company?: string; company_title?: string; period?: string };

// ── 准备 ──────────────────────────────────────────────────────────────────
export type CompanyOption = { id: string; title: string; tax_id?: string };

/** 一台能做实操的电脑。login：Express 登录设置了没有（null = 这一页不知道：网页、手机没有桥）。 */
export type OperatorComputer = { id: string; name: string; online: boolean; local: boolean; login: boolean | null };

export type CreateBatchInput = {
  company: string;
  company_title?: string;
  period: string;
  doc_ids: string[];
  computer: string;
  /** 0 = 现在开始。 */
  start_at: number;
  answers: Answers;
  /** 服务端的任务号：批次号就用它（CC 本机进度按它对上），交单结论写进任务。 */
  task?: string;
  /** 各张的资料分组（汇报用）。 */
  groups?: Record<string, DocGroup>;
};

/** 放一份资料进来：file 一份一个请求（上传失败可单独重传）。 */
export type IntakeInput = { company: string; period: string; task: string; file: File; onProgress?: (percent: number) => void };

/** Express 登录弹的是 CC 桌面版的原生小窗，页面拿不到密码，只知道成没成。 */
export type AuthorizeTarget = { computer: string } | { batch: string };

/** 实操的数据源：live-source.ts（站点 + 桥）与 mock-source.ts（预览与测试）。 */
export interface OperatorSource {
  // 任务（服务端记录：列表、改名、置顶、跨设备都以它为准）
  listTasks(start: number, limit: number): Promise<TaskPage>;
  /** 一个任务（带单据）；交出去还没收尾、这台电脑上没在跑的，先以站点为准收尾一次（task_settle）。 */
  getTask(name: string): Promise<TaskRecord>;
  createTask(input: { company: string; company_title: string; period: string }): Promise<TaskRecord>;
  updateTask(name: string, patch: TaskPatch): Promise<TaskRecord>;
  /** 任务的服务端记录变了（进行中、收尾）就回调；返回取消订阅。 */
  subscribeTasks(onChange: (task: TaskRecord) => void): () => void;
  // 进度（这台电脑上在跑的来自桥；别处的按站点的交单结论）
  listBatches(query?: { start?: number; limit?: number }): Promise<BatchListItem[]>;
  snapshot(batchId: string): Promise<BatchSnapshot>;
  /** 任一任务进度变了就回调；返回取消订阅。 */
  subscribe(onChange: (snapshot: BatchSnapshot) => void): () => void;
  // 准备
  companies(search: string, start: number, limit: number): Promise<CompanyOption[]>;
  documents(company: string, period: string, start: number, limit: number): Promise<DocumentPage>;
  /** 开始前确认（每张单）与这次写入的账套。 */
  precheck(company: string, docIds: readonly string[]): Promise<PrecheckReply>;
  /** 资料改分组（按公司记住由站点负责）。 */
  regroup(company: string, docId: string, group: DocGroup): Promise<void>;
  /** 放资料进来（输入框「＋」、拖进对话、拍照）：一份一个请求，onProgress 报已传字节的百分比；task 让站点把这份记进任务的上传记录。 */
  intake(input: IntakeInput): Promise<void>;
  /** 任务的上传记录与每份的识别读数（站点 task_uploads）；站点还没有这个接口时回 null。 */
  taskUploads(name: string): Promise<TaskUpload[] | null>;
  computers(company: string): Promise<OperatorComputer[]>;
  authorizeLogin(target: AuthorizeTarget): Promise<{ ok: boolean }>;
  createBatch(input: CreateBatchInput): Promise<{ id: string }>;
  // 控制
  start(batchId: string): Promise<void>;
  stop(batchId: string): Promise<void>;
  skip(batchId: string, docId: string): Promise<void>;
  /** 单张重试：站点把这张按原批次号交回执行的电脑（同作答，没有答案）。 */
  retry(batchId: string, docId: string): Promise<void>;
  /** 整批停下以后继续：站点把因整批停没录成的单（卡住的那张 + 因停止跳过的）按原批次号交回执行的电脑。 */
  resume(batchId: string): Promise<void>;
  /** 删除任务（服务端记录与桥上的进度；只删记录、不动账套）；交出去还没收尾的不能删。 */
  remove(batchId: string): Promise<void>;
  /** 「结果不明」的单（开始前确认里，或交出去后 CC 重启打断的）：在 Express 里核对后标已入账 / 没有入账（走产品复核页同一条路）；
   *  带 ``batchId`` 的标完以站点为准再收尾这个任务一次。 */
  resolve(company: string, docId: string, entered: boolean, batchId?: string): Promise<void>;
  // 任务里的对话（对话框甲）
  /** 这个任务的对话编号（没有就建一段，不进对话侧栏）。 */
  taskThread(name: string): Promise<string>;
  /** 录到中途停下等人选的那一张：记下选的，交回这个任务接着录（不调模型）。 */
  answerTask(name: string, docId: string, answers: Answers[string]): Promise<TaskRecord>;
}
