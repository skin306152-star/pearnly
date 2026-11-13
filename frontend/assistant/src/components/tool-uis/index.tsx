// 工具界面登记。Managed Agents 在服务端执行这些工具（Pearnly 工具由本站服务端执行），assistant-ui 只负责显示：
// 没有前端 execute。所有工具都用同一种「步骤行」显示（steps.tsx），连续的步骤收进一行可展开的标题；
// 没登记的（Pearnly 自己的工具、MCP 工具）走 ManagedAgentsToolFallback，也是步骤行。
// 代录（方案 cc-unified-plan 14.1，代录事件源投影进同一段对话的工具）：资料、上传、每张单是步骤组里的一行（operator-steps.tsx）；
// 开始前、中途选、继续是卡片（operator-*.tsx，样子同「允许」卡片），不进折叠的步骤组（OPERATOR_CARDS，thread.tsx 的 groupBy）。
// 通用提问卡片 ask_user（ask-card.tsx，照 Claude Code 的提问卡片，内容由 CC 定）同样单独一张。

import { defineToolkit, Tools, useAui } from "@assistant-ui/react";
import { AskUserCard } from "./ask-card";
import { GuideCard } from "./guide-card";
import { OperatorChooseCard } from "./operator-choose";
import { OperatorResumeCard } from "./operator-resume";
import { OperatorStartCard } from "./operator-start";
import { OperatorDocumentStep, OperatorSourcesStep, OperatorUploadStep } from "./operator-steps";
import { StepRow } from "./steps";

/** 代录的卡片：单独一张，不收进步骤组。 */
export const OPERATOR_CARDS: ReadonlySet<string> = new Set(["operator_start", "operator_choose", "operator_resume"]);

/** 单独一张、不收进步骤组的卡片：代录的卡片、通用提问卡片 ask_user 与引导卡 pearnly_guide（guide-card.tsx，后台出题）。 */
export const CARD_TOOLS: ReadonlySet<string> = new Set([...OPERATOR_CARDS, "ask_user", "pearnly_guide"]);

export const toolkit = defineToolkit({
  bash: { type: "backend", render: StepRow },
  web_search: { type: "backend", render: StepRow },
  web_fetch: { type: "backend", render: StepRow },
  read: { type: "backend", render: StepRow },
  write: { type: "backend", render: StepRow },
  edit: { type: "backend", render: StepRow },
  glob: { type: "backend", render: StepRow },
  grep: { type: "backend", render: StepRow },
  ask_user: { type: "backend", render: AskUserCard },
  pearnly_guide: { type: "backend", render: GuideCard },
  operator_start: { type: "backend", render: OperatorStartCard },
  operator_choose: { type: "backend", render: OperatorChooseCard },
  operator_resume: { type: "backend", render: OperatorResumeCard },
  operator_upload: { type: "backend", render: OperatorUploadStep },
  operator_document: { type: "backend", render: OperatorDocumentStep },
  operator_sources: { type: "backend", render: OperatorSourcesStep },
});

/** Register the toolkit with the runtime. Call once inside the provider. */
export function useManagedAgentsToolkit() {
  return useAui({ tools: Tools({ toolkit }) });
}

export const ManagedAgentsToolFallback = StepRow;
