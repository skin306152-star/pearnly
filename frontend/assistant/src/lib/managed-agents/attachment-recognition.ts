// 代录会话输入框里的附件识别到哪（2026-11-06「选中就上传」）：上传口回包带着这一份交给任务识别的那一行（task / upload，
// assistant/files._operator_take）时，输入框那一项上传完成后接着显示排队中、识别中 n/m 页、已识别 N 张、失败。按附件编号记，
// 卡片订阅（attachment.tsx 的 ComposerAttachment）。状态现读 task_uploads（只读这一份），隔几秒一次；有结论、附件离开输入框
// （发出、移除）就停。字同对话里的上传卡片（i18n 的 run）。

import { useSyncExternalStore } from "react";
import { getMethod } from "@/lib/frappe";
import { run, steps } from "@/i18n";

/** 服务端 shapes.upload_view 里这里用到的几项。 */
export type OperatorUpload = { batch: string; state: string; pages_done?: number; page_count?: number; documents?: unknown[] };
export type Recognition = { busy: boolean; failed: boolean; text: string };

const TASK_UPLOADS = "pearnly_core.express_operator.api.task_uploads";
const POLL_MS = 3000;
/** 最多读这么多次（约 15 分钟），读不到结论也停，免得页面一直开着时一直读。 */
const POLL_MAX = 300;

const states = new Map<string, Recognition>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const listeners = new Set<() => void>();

/** 一行识别状态 → 卡片上的字（纯计算，测试直接调）。 */
export function recognitionOf(row: OperatorUpload): Recognition {
  const done = Math.max(0, row.pages_done ?? 0);
  const total = Math.max(0, row.page_count ?? 0);
  switch (row.state) {
    case "queued":
      return { busy: true, failed: false, text: run.queued };
    case "done":
      return { busy: false, failed: false, text: run.read((row.documents ?? []).length) };
    case "failed":
      return { busy: false, failed: true, text: steps.failed };
    default:
      return { busy: true, failed: false, text: total > 0 ? run.readingPages(Math.min(done, total), total) : run.reading };
  }
}

function set(id: string, value: Recognition) {
  states.set(id, value);
  for (const listener of listeners) listener();
}

/** 上传完成、回包带着任务那一行：开始盯这一份。 */
export function watchRecognition(id: string, task: string, upload: OperatorUpload) {
  stopRecognition(id);
  set(id, recognitionOf(upload));
  if (!upload.batch || !recognitionOf(upload).busy) return;
  let tries = 0;
  const tick = async () => {
    timers.delete(id);
    if (!states.has(id) || ++tries > POLL_MAX) return;
    try {
      const found = await getMethod<{ uploads: OperatorUpload[] }>(TASK_UPLOADS, { name: task, batches: JSON.stringify([upload.batch]) });
      const row = found.uploads.find((item) => item.batch === upload.batch && item.state);
      if (!states.has(id)) return;
      if (row) set(id, recognitionOf(row));
      if (row && !recognitionOf(row).busy) return;
    } catch {
      // 一时读不到（断网等）：保留上一次的样子，下一次再读
    }
    if (states.has(id)) timers.set(id, setTimeout(tick, POLL_MS));
  };
  timers.set(id, setTimeout(tick, POLL_MS));
}

/** 附件离开输入框（发出、移除）：不再读。 */
export function stopRecognition(id: string) {
  const timer = timers.get(id);
  if (timer) clearTimeout(timer);
  timers.delete(id);
  if (states.delete(id)) for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 输入框里这一项此刻的识别状态；不是代录任务里的附件（直写）没有。 */
export function recognitionNow(id: string): Recognition | undefined {
  return states.get(id);
}

export function useRecognition(id: string): Recognition | undefined {
  return useSyncExternalStore(subscribe, () => states.get(id), () => states.get(id));
}
