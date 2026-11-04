// 代录工作区的上下文与「在这家新建任务」：外壳别处（公司列表、侧栏组头）也要用，单放一处，不把数据源（live-source）一起带进去。
// 状态与动作本身在 controller.tsx（OperatorProvider 挂上这里的上下文）。

import { createContext, useContext } from "react";
import { lastMonth } from "@/lib/companies";
import type { View } from "@/lib/nav";
import type { Operator } from "./controller";

export const OperatorContext = createContext<Operator | null>(null);

/** 不在实操里（没有实操的产品、外壳别处）回 null。 */
export const useOperatorOrNull = (): Operator | null => useContext(OperatorContext);

/** 在这家公司新建任务（月份默认上个月），主栏换到任务页：侧栏组头「＋」、代录模式下在公司列表选一家、添加公司之后都是这一个动作
 *  （用户 11-03：代录模式下不管从组头「＋」还是「公司」选公司进，都进代录新建任务页）。 */
export function newTaskIn(o: Operator, company: { id: string; title: string }, view: View, setView: (v: View) => void) {
  if (view !== "operator") setView("operator");
  o.create({ company: company.id, company_title: company.title, period: lastMonth() });
}
