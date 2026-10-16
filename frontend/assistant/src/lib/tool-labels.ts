// 工具在对话步骤里显示的名字（站点按当前用户语言给出，来自各工具声明的 label）：done 是做完的说法（步骤列表），
// running 是进行时（工作状态行、正在执行的那一步，产品在 pearnly_assistant_step_names 里声明）；没声明进行时的工具用 done；
// ask 是要点「允许」的工具卡片上的问话，点了「拒绝」的那一步照它写（没做，不用做完的说法）。

import { useEffect, useState } from "react";
import { get } from "@/lib/frappe";

export type ToolLabels = { done: Record<string, string>; running: Record<string, string>; ask: Record<string, string> };

const EMPTY: ToolLabels = { done: {}, running: {}, ask: {} };
let cached: Promise<ToolLabels> | null = null;

export function useToolLabels(): ToolLabels {
  const [labels, setLabels] = useState<ToolLabels>(EMPTY);
  useEffect(() => {
    cached ??= get<Partial<ToolLabels>>("tool_steps")
      .then((steps) => ({ done: steps?.done ?? {}, running: steps?.running ?? {}, ask: steps?.ask ?? {} }))
      .catch(() => EMPTY);
    void cached.then(setLabels);
  }, []);
  return labels;
}

/** 一个本站工具此刻的说法：进行中用进行时（没声明就用做完的说法），做完用 label；都没有就是工具名。 */
export function toolLabel(labels: ToolLabels, toolName: string, running: boolean): string {
  return (running ? labels.running[toolName] : undefined) ?? labels.done[toolName] ?? toolName;
}
