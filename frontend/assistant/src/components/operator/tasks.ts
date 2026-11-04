// 「实操」任务记录的本页缓存（站点的任务记录，跨设备）：打开过的、新建的、收尾回来的就地换上，删了拿掉；别的设备上的变化由站点事件
// pearnly_operator_task（共用层 express_operator/tasks.py 提交后推给本人）换上或拿掉（watchTasks）。
// 用户 11-04：代录不再有自己的任务列表——侧栏就是直写的对话侧栏（代录任务 = 会话，统一框架方案由 W1 出），这里只剩会话页要用的记录。

import { create } from "zustand";
import { onSiteEvent } from "@/lib/managed-agents/realtime";
import { taskOf } from "./live-source";
import type { TaskRecord } from "./types";

export const useTasks = create<{ rows: TaskRecord[] }>(() => ({ rows: [] }));

/** 一个任务的记录换上（新的放最前面；收尾、改名回来的就地换，没带单据的留着原来的单据）。 */
export function upsertTask(task: TaskRecord) {
  if (!task.name) return;
  useTasks.setState((s) =>
    s.rows.some((row) => row.name === task.name)
      ? { rows: s.rows.map((row) => (row.name === task.name ? { ...task, documents: task.documents ?? row.documents } : row)) }
      : { rows: [task, ...s.rows] },
  );
}

export function dropTask(name: string) {
  useTasks.setState((s) => ({ rows: s.rows.filter((row) => row.name !== name) }));
}

/** 站点事件（共用层 express_operator/tasks.py 的 EVENT）。 */
export const TASK_EVENT = "pearnly_operator_task";

/** 有代录的产品挂一次（assistant.tsx）：任务记录变了就地换上（本页没有的不加）、删了拿掉；返回取消订阅。 */
export function watchTasks(): () => void {
  return onSiteEvent(TASK_EVENT, (data) => {
    const change = (data && typeof data === "object" ? data : {}) as { op?: string; task?: unknown; name?: string };
    if (change.op === "delete" && change.name) dropTask(String(change.name));
    else if (change.op === "upsert" && change.task) {
      const task = taskOf(change.task);
      if (useTasks.getState().rows.some((row) => row.name === task.name)) upsertTask(task);
    }
  });
}
