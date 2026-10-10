// 工具界面登记。Managed Agents 在服务端执行这些工具（Pearnly 工具由本站服务端执行），assistant-ui 只负责显示：
// 没有前端 execute。所有工具都用同一种「步骤行」显示（steps.tsx），连续的步骤收进一行可展开的标题；
// 没登记的（Pearnly 自己的工具、MCP 工具）走 ManagedAgentsToolFallback，也是步骤行。

import { defineToolkit, Tools, useAui } from "@assistant-ui/react";
import { StepRow } from "./steps";

export const toolkit = defineToolkit({
  bash: { type: "backend", render: StepRow },
  web_search: { type: "backend", render: StepRow },
  web_fetch: { type: "backend", render: StepRow },
  read: { type: "backend", render: StepRow },
  write: { type: "backend", render: StepRow },
  edit: { type: "backend", render: StepRow },
  glob: { type: "backend", render: StepRow },
  grep: { type: "backend", render: StepRow },
});

/** Register the toolkit with the runtime. Call once inside the provider. */
export function useManagedAgentsToolkit() {
  return useAui({ tools: Tools({ toolkit }) });
}

export const ManagedAgentsToolFallback = StepRow;
