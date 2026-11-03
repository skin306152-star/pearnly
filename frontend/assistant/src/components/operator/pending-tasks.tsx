// 待处理页的「任务」一组（attention.ts）：本人「需要你处理」的代录任务（服务端已按单据去重），一行一个任务，点了切到代录、打开它。
// 只在不按公司筛的待处理页（侧栏「待处理 N」点进去）：那里的数 = 产品的待处理 + 这一组；按公司筛的（公司组头的数点进去）只是
// 产品的待处理，与组头的数一致。行同待处理的单据行（.cc-list-item，两行：名字、状态）。

import type { FC } from "react";
import { Marquee } from "@/components/ui/marquee";
import { op } from "@/i18n";
import { openOperator, useView } from "@/lib/nav";
import { useAttention } from "./attention-store";
import { titleOf } from "./task-title";
import type { TaskRecord } from "./types";

/** 页面搜索的口径：任务名或公司名。 */
export const taskMatches = (task: Pick<TaskRecord, "title" | "company" | "company_title" | "period">, search: string) => {
  const needle = search.trim().toLowerCase();
  return !needle || `${titleOf(task)} ${task.company_title}`.toLowerCase().includes(needle);
};

/** 这一组搜索后剩几条（待处理页按它决定空着时显示什么）。 */
export const usePendingTaskCount = (search: string) => useAttention((s) => s.tasks.filter((task) => taskMatches(task, search)).length);

export const PendingTasks: FC<{ search?: string }> = ({ search = "" }) => {
  const tasks = useAttention((s) => s.tasks);
  const { view, setView } = useView();
  const shown = tasks.filter((task) => taskMatches(task, search));
  if (!shown.length) return null;
  return (
    <section className="flex flex-col gap-(--cc-space-8)" data-slot="cc-pending-tasks">
      <h2 className="cc-section-label" data-flush="">{op.tasks}</h2>
      {shown.map((task) => (
        <button
          key={task.name}
          type="button"
          className="cc-list-item items-center text-start"
          data-slot="cc-pending-task"
          onClick={() => openOperator(task.name, view, setView)}
        >
          <span className="min-w-0 flex-1">
            <Marquee className="block truncate text-ink text-body">{titleOf(task)}</Marquee>
            <Marquee className="block truncate text-ink-3 text-caption">{op.state.attention}</Marquee>
          </span>
        </button>
      ))}
    </section>
  );
};
