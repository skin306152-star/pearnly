// 任务显示的名字（侧栏、待处理页的「任务」一组同一个）。

import { monthLabel } from "@/lib/companies";
import type { TaskRecord } from "./types";

/** 标题：公司 · 月份（没有月份只写公司）。 */
export const taskTitle = (company: string, period?: string) => [company, period ? monthLabel(period) : ""].filter(Boolean).join(" · ");

/** 任务显示的名字：改过名的用名字，否则「公司 · 月份」。 */
export const titleOf = (task: Pick<TaskRecord, "title" | "company" | "company_title" | "period">) =>
  task.title.trim() || taskTitle(task.company_title || task.company, task.period);
