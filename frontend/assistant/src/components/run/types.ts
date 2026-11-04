// 「Code 版 CC」会话组件的接口（评估 docs/work/2026-11-04-cc-code-style.md 第 1、4、5、13 节，施工块 2）。
// 对话（直写）与代录两种模式共用同一套组件：消息下的任务清单、结束框、侧栏行的「等你处理」标记、上传状态卡片、
// 出错 / 停止 / 被打断。这里只有类型：不碰 React、不读接口，块 3（代录对话化）照这份并行开工。
//
// 服务端回包（TaskUpload*、PlanToolInput、NextStepToolInput）按评估第 4 节 B2、B8 的形状写；块 1 的接口做出来有出入时在这里对齐，
// 组件只吃下面的 *Props。

// ── 任务清单（S3：消息下面，照 Code 的 update_plan；三种状态，同时只一项进行中） ────────────────────────────────

export type PlanStatus = "pending" | "in_progress" | "completed";

export type PlanItem = {
  id: string;
  text: string;
  status: PlanStatus;
  /** 完成了但没做成（代录「没录成」）：行尾危险色「失败」 */
  failed?: boolean;
  /** 行尾淡色补充（单号、对方、没录成的原因） */
  note?: string;
};

export type RunPlanProps = {
  items: readonly PlanItem[];
  /** 摘要行的字；不给就是「已完成 n/m」。代录给「第 n/N 张」 */
  summary?: string;
  /** 一开始展开没有；不给：电脑展开、手机收起，全部完成的收起 */
  defaultOpen?: boolean;
};

// ── 结束框（S4：一轮做完最后一个框，照 Code 的 wrap_up） ───────────────────────────────────────────────────

/** 结束框的按钮：1～2 个，第一个是推荐项（主按钮）。 */
export type NextStepAction = { id: string; label: string };

/** ask 等人点；sending 点了还没送到；answered 送到了；failed 没做成；cancelled 没答就没了（这一轮被新消息盖过、停了）。 */
export type NextStepPhase = "ask" | "sending" | "answered" | "failed" | "cancelled";

export type NextStepProps = {
  /** 回顾：2～3 句；回顾的第一句也是侧栏这一行的状态（由调用方取） */
  recap: string;
  /** 要人定的一件事（≤100 字）；没有就只留回顾，不画框 */
  ask?: string;
  actions?: readonly NextStepAction[];
  /** 不给 = ask */
  phase?: NextStepPhase;
  /** 点过的那一项（NextStepAction.id）：sending / answered / failed 时显示它的字 */
  chosen?: string;
  /** 没送到的原因：ask 阶段写在按钮上方一行 */
  error?: string;
  onAction?: (id: string) => void;
  /** inline：对话流里；sheet：手机上贴在输入框上方（同 approval-sheet，由调用方放进输入框那一块） */
  placement?: "inline" | "sheet";
};

// ── 侧栏行状态（S5、评估 1.3；Q2：加「等你处理」） ────────────────────────────────────────────────────────

/** running 在跑；waiting 等你处理（新）；unread 跑完没看；notice 有没看过的提醒。完成不标。 */
export type RunMarkState = "running" | "waiting" | "unread" | "notice";

export type RunMarkProps = { state: RunMarkState };

// ── 上传状态卡片（第 5 节；同一份再传照常识别、照常扣费，不加「已传过」之类的提示） ──────────────────────────────

export type UploadState =
  /** 按已传字节，0～100 */
  | { kind: "uploading"; percent: number }
  | { kind: "queued" }
  | { kind: "reading"; pagesDone: number; pageCount: number }
  /** documents：识别出几张；outside：其中几张不在任务月份（month 是给人看的月份，如「10 月」） */
  | { kind: "done"; documents: number; outside: number; month?: string }
  /** upload：上传没成功（可重试同一份）；read：识别失败（照产品口径不另做重试）。reason 是给人看的原因 */
  | { kind: "failed"; stage: "upload" | "read"; reason: string };

export type UploadCardProps = {
  name: string;
  /** 字节 */
  size?: number;
  pages?: number;
  /** 缩略图地址（图片本身、PDF 第一页） */
  image?: string;
  state: UploadState;
  /** 只在上传失败时出「重试」 */
  onRetry?: () => void;
  /** 能预览时点卡片打开 */
  onOpen?: () => void;
};

/** B2 `task_uploads(name)` 每份文件的识别状态。 */
export type TaskUploadStatus = "queued" | "reading" | "done" | "failed";

/** B2 里识别出的一张单。 */
export type TaskUploadDoc = {
  name: string;
  /** 单据日期 YYYY-MM-DD */
  date?: string | null;
  /** 在任务月份里 */
  in_period: boolean;
  /** 在这次任务的资料里 */
  in_sources: boolean;
};

/** B1 上传子表 + B2 识别读数（一份文件一行）。 */
export type TaskUpload = {
  /** 原件编号 */
  original: string;
  filename: string;
  size: number;
  /** 识别任务号 */
  job: string;
  /** 上传时间（ISO，UTC） */
  uploaded_at: string;
  status: TaskUploadStatus;
  pages_done: number;
  page_count: number;
  documents: TaskUploadDoc[];
  /** 识别失败码（status = failed 时） */
  error_code?: string | null;
};

// ── 出错、停止、被打断（S6、S7：两种模式同一种呈现） ─────────────────────────────────────────────────────────

/** error：这一轮 / 这一批出错；stopped：你按了停止；interrupted：被别的打断（Code 的样子不知道，沿用「已停止」）。 */
export type RunEndKind = "error" | "stopped" | "interrupted";

export type RunEndProps = {
  kind: RunEndKind;
  /** error：原因（必给）；stopped / interrupted：接在「已停止」后面的补充（「录好 18 张」），可不给 */
  text?: string;
  /** 落在 data-slot 上（流程断言用），不给就是 cc-run-end */
  slot?: string;
};

// ── B8：对话模式里 CC 自己输出的两个工具（Q5 已批；工具定义归块 1，这里只写前端认的入参） ──────────────────────────

export type PlanToolInput = { items: Array<{ content: string; status: PlanStatus }> };

export type NextStepToolInput = { recap: string; ask?: string; actions?: Array<{ label: string }> };
