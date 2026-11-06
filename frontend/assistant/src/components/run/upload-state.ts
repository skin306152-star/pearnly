// 上传状态卡片的读数（评估 docs/work/2026-11-04-cc-code-style.md 第 5 节）：B2 `task_uploads` 的一行 + 本页的上传进度 → 卡片状态与那一行字。
// 不碰 React、不读接口；字走 i18n。同一份再传就是又一行、又一张卡片，这里不比对、不提示（产品口径：上传识别都扣费）。

import { run, t } from "@/i18n";
import { readingText } from "@/lib/managed-agents/progress";
import type { TaskUpload, UploadState } from "./types";

/** 服务端一行 → 卡片状态。reason：识别失败码 → 给人看的原因（产品自己的说法）；month：任务月份给人看的写法。 */
export function uploadState(row: TaskUpload, reason: (code: string) => string, month?: string): UploadState {
  switch (row.status) {
    case "queued":
      return { kind: "queued" };
    case "reading":
      return { kind: "reading", pagesDone: Math.max(0, row.pages_done), pageCount: Math.max(0, row.page_count) };
    case "failed":
      return { kind: "failed", stage: "read", reason: reason(row.error_code ?? "") };
    default: {
      const outside = row.documents.filter((doc) => !doc.in_period).length;
      return { kind: "done", documents: row.documents.length, outside, ...(month ? { month } : {}) };
    }
  }
}

/** 还在路上（上传、排队、识别）：卡片那一行带在动的点，侧栏这一行算在跑。 */
export const uploadBusy = (state: UploadState) => state.kind === "uploading" || state.kind === "queued" || state.kind === "reading";

/** 卡片下面那一行字（失败的「失败」由卡片另画，这里只给原因，没有原因为空）。 */
export function uploadText(state: UploadState): string {
  switch (state.kind) {
    case "uploading":
      return `${t("uploading")} ${Math.min(100, Math.max(0, Math.round(state.percent)))}%`;
    case "queued":
      return run.queued;
    case "reading":
      return readingText(state.pagesDone, state.pageCount);
    case "done": {
      const read = run.read(state.documents);
      return state.outside > 0 && state.month ? `${read} · ${run.outside(state.outside, state.month)}` : read;
    }
    case "failed":
      return state.reason;
  }
}
