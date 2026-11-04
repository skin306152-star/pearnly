// 「Code 版 CC」对话里的上传状态卡片接口（评估 docs/work/2026-11-04-cc-code-style.md 第 5 节，施工块 2；11-04 用户更正后的范围）。
//
// 用户更正：任务清单、结束框、侧栏「等你处理」新标记、另一套出错显示都**不做**——Code 左侧只有对话简要，执行中、结束、出错、停止
// 都在对话框里，直写现有的那套就是标准，代录照着用：
//   - 执行中：thread.tsx 的 RunLine（工作状态行）；
//   - 出错：.cc-alert 出错框（thread.tsx 的 SendFailure / MessageError 同一个样子）；
//   - 停止：输入框里的 ■（thread.tsx 的 Composer，代录经 `stop` 传自己的在跑状态与停止动作，见下面 ComposerStop）；
//   - 侧栏：thread-list.tsx 的 ActivityMark（在跑脉冲点、跑完没看蓝点），不加新标记。
// 这里只剩上传状态卡片（根治「看不到状态 → 重复上传、重复扣费」）：同一份再传照常识别、照常扣费，不加「已传过」之类的提示。
// 样子用直写已有的附件卡片（attachment.tsx 的 AttachmentCard）加一行步骤行的字拼，不新造风格。
//
// TaskUpload* 按评估第 4 节 B1、B2 写；块 1 的接口做出来有出入时在这里对齐，组件只吃 UploadCardProps。

/** 一份上传此刻的状态。 */
export type UploadState =
  /** 按已传字节，0～100 */
  | { kind: "uploading"; percent: number }
  | { kind: "queued" }
  /** pageCount 还不知道时为 0 */
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

/** 输入框（thread.tsx 的 Composer）的 `stop`：不在对话运行时里的调用方（代录）交自己的在跑状态与停止动作，
 *  发送键在跑时换成直写同一个 ■，Esc 同效。不给就是对话自己的（这一轮在跑时停这一轮）。 */
export type ComposerStop = { running: boolean; onStop: () => void };
