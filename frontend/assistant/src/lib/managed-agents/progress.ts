// 代录在对话里的几句（方案 cc-unified-plan 14.10）：
//   - 工作状态行上「进行到哪」：整段对话的 pearnly.progress（tool_use_id 空串）——等电脑空闲、第 7/22 张、第 7/22 张 · 电脑已锁屏；
//   - 一步此刻是进行中还是有结论：同一个 tool_use_id 下 processed_at 最新的那条说了算（重交的单先出一条更新的进度，再出新结果）；
//   - 收尾那条 agent.message 的一句（operator 字段）。
// 纯函数，不碰 React；字走 i18n（代码 → 文案，原码不外露）。

import { co, op, run } from "@/i18n";
import type { OperatorEnd, ProgressEvent } from "./events";

/** 服务端时间 → 毫秒（解析不了回 null）。 */
const ms = (iso: string | null | undefined) => {
  const at = Date.parse(String(iso ?? "").replace(/(\.\d{3})\d+/, "$1"));
  return Number.isFinite(at) ? at : null;
};

/** 状态行用的进度：整段对话那一条；没有就用正在执行的那一步的（直写对话里服务端挂着等识别的那一步），
 *  都没有回 null。 */
export function liveProgress(state: { progress: ReadonlyMap<string, ProgressEvent> }, toolCallId?: string): ProgressEvent | null {
  return state.progress.get("") ?? (toolCallId ? state.progress.get(toolCallId) ?? null : null);
}

/** 识别中的一句：有真实的已读页数才写「n/m 页」；还没读完一页（Claude 一次读完 8 页以内的文件，中途没有页数）只写共几页，
 *  不写「0/m」（2026-11-06：代录卡片一直「0 / 12 页」，用户以为卡住、重传了两次）。 */
export function readingText(done: number, total: number): string {
  const pages = Math.max(0, total);
  if (pages <= 0) return run.reading;
  const read = Math.min(Math.max(0, done), pages);
  return read > 0 ? run.readingPages(read, pages) : run.readingTotal(pages);
}

/** 这一步此刻的进度：有进度且比结果新（或还没结果）才算，否则以结果为准回 null。 */
export function stepProgress(
  progress: ReadonlyMap<string, ProgressEvent> | undefined,
  part: { toolCallId: string; result?: unknown; resultAt?: string | undefined },
): ProgressEvent | null {
  const event = progress?.get(part.toolCallId);
  if (!event) return null;
  if (part.result === undefined) return event;
  const at = ms(event.processed_at);
  const done = ms(part.resultAt);
  return at !== null && done !== null && at > done ? event : null;
}

/** 一条进度的说法（状态行「计时 · 」后面那一段、步骤行行尾）。认不出的代码只写张数。 */
export function progressText(event: Pick<ProgressEvent, "done" | "total" | "label" | "detail">): string {
  const done = Math.max(0, event.done);
  const total = Math.max(0, event.total);
  switch (event.label) {
    case "reading_pages":
      return readingText(done, total);
    case "queued":
      return run.queued;
    case "blocked":
      return [event.detail, co.ledger.code.result_unknown].filter(Boolean).join(" · ");
    default: {
      // 等电脑空闲时是已有结论的张数；在做（或暂停）时说正在第几张
      const count = total > 0 ? op.nth(Math.min(done + (event.label === "waiting_computer" ? 0 : 1), total), total) : "";
      const label = event.label === "waiting_computer" ? op.queued : event.label === "entering" ? "" : (op.reason[event.label] ?? "");
      return [count, label].filter(Boolean).join(" · ");
    }
  }
}

/** 收尾那条的一句：完成 N 张（，M 张需要处理）/ 已停止 · 已入账 N 张 / M 张需要处理。 */
export function operatorEndText(end: OperatorEnd): string {
  const saved = Math.max(0, Number(end.counts?.saved ?? 0));
  const left = Math.max(0, Number(end.counts?.not_done ?? 0));
  if (end.kind === "stopped") return op.end.stopped(saved);
  if (end.kind === "attention") return op.end.attention(left);
  return op.end.finished(saved, left);
}
