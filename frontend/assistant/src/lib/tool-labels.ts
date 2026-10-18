// 工具在对话步骤里显示的名字（站点按当前用户语言给出，来自各工具声明的 label）：done 是做完的说法（步骤列表），
// running 是进行时（工作状态行、正在执行的那一步，产品在 pearnly_assistant_step_names 里声明）；没声明进行时的工具用 done；
// ask 是要点「允许」的工具卡片上的问话（重开对话时点完的卡片照它写问话），ask_when 是问法随参数变的工具 {参数: 问句}（按声明顺序，
// 给了哪个参数用哪句，同服务端 approval_view）；gate 是带了哪些参数才要「允许」的工具（其余每次都要）。
// ready：取回了（或取失败了，按空的用）。重开对话时卡片要靠它认（lib/approval-card.ts），没取回前对话页先不画要靠它认的回复
// （thread.tsx），免得先进折叠的步骤组、取回后再跳出来。取失败不记住：下次用到时再取。

import { useEffect, useState } from "react";
import { get } from "@/lib/frappe";

export type ToolLabels = {
  done: Record<string, string>;
  running: Record<string, string>;
  ask: Record<string, string>;
  ask_when: Record<string, Record<string, string>>;
  gate: Record<string, string[]>;
  ready?: boolean;
};

const EMPTY: ToolLabels = { done: {}, running: {}, ask: {}, ask_when: {}, gate: {} };
let cached: Promise<ToolLabels> | null = null;
let loaded: ToolLabels | null = null;

/** 取一次（同一页共用）；取失败交回空的（ready），并忘掉这次，下次再取。 */
export function loadToolLabels(): Promise<ToolLabels> {
  cached ??= get<Partial<ToolLabels>>("tool_steps")
    .then((steps) => {
      loaded = {
        done: steps?.done ?? {},
        running: steps?.running ?? {},
        ask: steps?.ask ?? {},
        ask_when: steps?.ask_when ?? {},
        gate: steps?.gate ?? {},
        ready: true,
      };
      return loaded;
    })
    .catch(() => {
      cached = null;
      return { ...EMPTY, ready: true };
    });
  return cached;
}

export function useToolLabels(): ToolLabels {
  const [labels, setLabels] = useState<ToolLabels>(() => loaded ?? EMPTY);
  useEffect(() => {
    if (loaded) return;
    let live = true;
    void loadToolLabels().then((next) => live && setLabels(next));
    return () => {
      live = false;
    };
  }, []);
  return labels;
}

/** 一个本站工具此刻的说法：进行中用进行时（没声明就用做完的说法），做完用 label；都没有就是工具名。 */
export function toolLabel(labels: ToolLabels, toolName: string, running: boolean): string {
  return (running ? labels.running[toolName] : undefined) ?? labels.done[toolName] ?? toolName;
}
