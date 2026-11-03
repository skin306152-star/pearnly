// 「待处理」里的代录一组（主会话 11-03）：本人在本站「需要你处理」的代录任务（共用层 task_attention），服务端按单据去重——
// 任务里没录成的单都已在产品自己的待处理里列着的，不另算这个任务。侧栏「待处理 N」= 产品的待处理 + 这一组（两种模式同一个数），
// 待处理页多列一组「任务」，点了切到代录、打开那个任务。
//
//   重取：挂上时一次；任务变了（站点事件 pearnly_operator_task，tasks.py 提交后推给本人，别的设备上的变化也到）、产品的待处理变了
//   （去重看它）、回到前台、实时通道重连时合成一次。同一个事件顺手更新侧栏的任务列表（列表取过时）。

import { onPendingChange, PENDING_EVENTS } from "@/lib/companies";
import { getMethod } from "@/lib/frappe";
import { onConnectionChange, onSiteEvent } from "@/lib/managed-agents/realtime";
import { withReport } from "@/lib/caught";
import { useAttention } from "./attention-store";
import { API, taskOf } from "./live-source";
import { dropTask, upsertTask, useTasks } from "./tasks";

export { useAttention } from "./attention-store";

/** 站点事件（共用层 express_operator/tasks.py 的 EVENT）。 */
export const TASK_EVENT = "pearnly_operator_task";

type Reply = { total?: number; companies?: Record<string, number>; tasks?: unknown[] } | null;

let asking: Promise<void> | null = null;
export function refreshAttention(): Promise<void> {
  asking ??= getMethod<Reply>(API + "task_attention")
    .then((out) => {
      const tasks = (out?.tasks ?? []).map(taskOf).filter((task) => task.name);
      const companies: Record<string, number> = {};
      for (const [company, n] of Object.entries(out?.companies ?? {})) companies[company] = Number(n) || 0;
      useAttention.setState({ loaded: true, total: tasks.length, companies, tasks });
    })
    .catch(withReport(() => undefined, "operator-attention"))
    .finally(() => {
      asking = null;
    });
  return asking;
}

/** 有代录的产品挂一次（assistant.tsx）；返回取消订阅。 */
export function watchAttention(): () => void {
  void refreshAttention();
  let timer = 0;
  const soon = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => void refreshAttention(), 1500);
  };
  const onTask = (data: unknown) => {
    const change = (data && typeof data === "object" ? data : {}) as { op?: string; task?: unknown; name?: string };
    if (useTasks.getState().loaded) {
      if (change.op === "delete" && change.name) dropTask(String(change.name));
      else if (change.op === "upsert" && change.task) upsertTask(taskOf(change.task));
    }
    soon();
  };
  const offs = [onSiteEvent(TASK_EVENT, onTask), ...PENDING_EVENTS.map((name) => onSiteEvent(name, soon)), onPendingChange(soon)];
  const offConnection = onConnectionChange((connected) => connected && soon());
  const onVisible = () => document.visibilityState === "visible" && soon();
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.clearTimeout(timer);
    offs.forEach((off) => off());
    offConnection();
    document.removeEventListener("visibilitychange", onVisible);
  };
}
