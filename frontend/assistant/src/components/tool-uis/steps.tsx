// 智能体的过程（思考、读写文件、运行命令、Pearnly 工具）按 claude.ai 的样子收成一行「步骤」：
// 进行中标题是「正在运行」、做完是「N 个步骤」；点开是一行一步的列表；点某一步才看到命令 / 内容与输出（浅色小框）。
// 在做什么、用了多久由对话底部的工作状态行说（thread.tsx 的 RunIndicator），这里不再另放一个在动的点。
// 所有工具共用 StepRow，按工具名给图标与说明。停下来等用户批准的那一步不收进组（thread.tsx 的 groupBy），
// 直接在行下给「允许 / 拒绝」。
// 卡片规矩（docs/development/ccs.md）：点开的内容框不在对话里再套一层滚动——长内容先显示前 CLIP_LINES 行，
// 点「… N 行」整段展开；手机（粗指针）上能点的行点按区不小于 44pt；颜色只用 globals.css 的变量。

import { useAuiState, type ToolCallMessagePartComponent } from "@assistant-ui/react";
import {
  ChevronRightIcon,
  FileIcon,
  FilePenIcon,
  FilePlusIcon,
  FolderSearchIcon,
  GlobeIcon,
  ScanSearchIcon,
  SearchIcon,
  TerminalIcon,
  WrenchIcon,
} from "lucide-react";
import { useState, type FC, type PropsWithChildren } from "react";
import type { ToolApproval, ToolResultPayload } from "@/lib/managed-agents/reducer";
import { toolLabel, useToolLabels, type ToolLabels } from "@/lib/tool-labels";
import { cn } from "@/lib/utils";
import { steps } from "@/i18n";
import { ApprovalBar } from "./approval-bar";
import { TypingDots } from "@/components/typing-dots";

type Args = Record<string, unknown> | undefined;
type Step = { icon: typeof FileIcon; verb: string; target?: string; input?: string; showOutput: boolean };

const str = (value: unknown) => (typeof value === "string" ? value : "");
const base = (path: unknown) => str(path).split("/").pop() ?? "";
/** 步骤详情先显示几行（多了点「… N 行」展开）、最多几个字（一整行很长的 JSON 也不会把对话撑得很高）。 */
export const CLIP_LINES = 12;
export const CLIP_CHARS = 1500;

/** 要显示的内容与收起的行数（full = 已展开）。 */
export function clipText(text: string, full = false): { text: string; hidden: number } {
  const lines = text.split("\n");
  if (full || (lines.length <= CLIP_LINES && text.length <= CLIP_CHARS)) return { text, hidden: 0 };
  const shown = lines.slice(0, CLIP_LINES).join("\n").slice(0, CLIP_CHARS);
  // 收起的行数：没显示到的行；最后一行只显示了一部分的也算一行
  const reached = shown.split("\n").length;
  const partial = shown.length < lines.slice(0, reached).join("\n").length;
  return { text: shown, hidden: lines.length - reached + (partial ? 1 : 0) };
}

/** 步骤详情里的一段（命令 / 内容、输出）：自动撑高，不套滚动；长了先收起，点一下整段展开。 */
const Clipped: FC<{ text: string; className?: string }> = ({ text, className }) => {
  const [full, setFull] = useState(false);
  const shown = clipText(text, full);
  return (
    <div className={className}>
      <pre className="whitespace-pre-wrap break-all px-3 py-2">{shown.text}</pre>
      {shown.hidden > 0 && (
        <button type="button" onClick={() => setFull(true)} className="cc-tap block w-full px-3 pb-2 text-start text-ink-3 hover:text-ink">
          … {steps.lines(shown.hidden)}
        </button>
      )}
    </div>
  );
};

const firstLine = (text: string) => {
  const line = text.trim().split("\n")[0] ?? "";
  return line.length > 90 ? `${line.slice(0, 88)}…` : line;
};

/** 一步的图标、说明（进行中 / 完成）和点开后显示的内容。 */
function describe(toolName: string, args: Args, running: boolean, labels: ToolLabels): Step {
  const pick = (triple: readonly string[]) => (running ? triple[0] : triple[1]) ?? "";
  switch (toolName) {
    case "bash": {
      const command = str(args?.command);
      return { icon: TerminalIcon, verb: pick(steps.bash), target: firstLine(command), input: command, showOutput: true };
    }
    case "read":
      return { icon: FileIcon, verb: pick(steps.read), target: base(args?.file_path), showOutput: true };
    case "write":
      return { icon: FilePlusIcon, verb: pick(steps.write), target: base(args?.file_path), input: str(args?.content), showOutput: false };
    case "edit":
      return { icon: FilePenIcon, verb: pick(steps.edit), target: base(args?.file_path), input: str(args?.new_string), showOutput: false };
    case "glob":
      return { icon: FolderSearchIcon, verb: pick(steps.glob), target: str(args?.pattern), showOutput: true };
    case "grep":
      return { icon: ScanSearchIcon, verb: pick(steps.grep), target: str(args?.pattern), showOutput: true };
    case "web_search":
      return { icon: SearchIcon, verb: pick(steps.search), target: str(args?.query), showOutput: true };
    case "web_fetch": {
      const url = str(args?.url);
      return { icon: GlobeIcon, verb: pick(steps.fetch), target: url.replace(/^https?:\/\/(www\.)?/, "").split("/")[0], showOutput: true };
    }
    default: {
      const input = args && Object.keys(args).length ? JSON.stringify(args, null, 2) : undefined;
      return { icon: WrenchIcon, verb: toolLabel(labels, toolName, running), input, showOutput: true };
    }
  }
}

function outputOf(result: unknown): { text: string; isError: boolean } | null {
  if (result === undefined || result === null) return null;
  if (typeof result === "object" && "text" in (result as object)) {
    const payload = result as ToolResultPayload;
    return { text: payload.text, isError: payload.isError };
  }
  return { text: typeof result === "string" ? result : JSON.stringify(result, null, 2), isError: false };
}

export const StepRow: ToolCallMessagePartComponent = ({
  toolName,
  args,
  result,
  status,
  isError,
  approval,
  respondToApproval,
}) => {
  const [open, setOpen] = useState(false);
  const labels = useToolLabels();
  const awaiting = !!approval && approval.approved === undefined;
  const output = outputOf(result);
  const running = !output && !awaiting && approval?.approved !== false && status.type === "running";
  const errored = !!output?.isError || !!isError || status.type === "incomplete";
  const step = describe(toolName, args as Args, running, labels);
  // Held for Allow/Deny: say what it is about to do, not the past-tense label ("Saved …" before anything ran).
  const asking = awaiting ? (approval as ToolApproval).ask : undefined;
  const shownOutput = step.showOutput && output?.text ? output.text : "";
  const expandable = Boolean(step.input || shownOutput);
  const Icon = step.icon;

  return (
    <div className="aui-step">
      <button
        type="button"
        disabled={!expandable}
        onClick={() => setOpen((o) => !o)}
        className="group/step cc-tap flex w-full min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-left text-ink-2 text-sm enabled:hover:bg-hover"
      >
        {running ? (
          <TypingDots size="sm" className="w-3.5 shrink-0 justify-center" />
        ) : (
          <Icon className={cn("size-3.5 shrink-0", errored && "text-danger")} />
        )}
        <span className="shrink-0">{asking ?? step.verb}</span>
        {step.target && <span className="min-w-0 truncate font-mono text-ink-3 text-(length:--cc-text-sm)">{step.target}</span>}
        {expandable && (
          <ChevronRightIcon className={cn("ms-auto size-3.5 shrink-0 opacity-0 transition group-hover/step:opacity-100", open && "rotate-90 opacity-100")} />
        )}
      </button>
      {open && expandable && (
        <div className="ms-7 mt-1 mb-2 overflow-hidden rounded-md border border-line bg-app font-mono text-(length:--cc-text-xs) text-ink-2 leading-5">
          {step.input && <Clipped text={step.input} />}
          {shownOutput && <Clipped text={shownOutput} className={cn(step.input && "border-line border-t", errored && "text-danger")} />}
        </div>
      )}
      {approval && (
        <ApprovalBar approval={approval} respondToApproval={respondToApproval} className="ms-7 mt-1 mb-2" />
      )}
    </div>
  );
};

/** 工作状态行上这一步的说法：进行时的步骤名，带上对象（文件名、命令、网址）。 */
export function runningStepLabel(toolName: string, args: Record<string, unknown> | undefined, labels: ToolLabels): string {
  const step = describe(toolName, args, true, labels);
  return step.target ? `${step.verb} ${step.target}` : step.verb;
}

type Group = { indices: readonly number[]; status: { type: string } };

/** 这组步骤还在进行：最后一步在跑，或停在一步本站正在执行的工具上（等结果，不是等用户点）。 */
export const groupLive = (status: { type: string }) => status.type === "running" || status.type === "requires-action";

/** 一段连续的思考与工具调用：一行标题，点开是步骤列表。只有思考、没有工具的不显示（没有可看的内容；在想由状态行说）。 */
export const StepsGroup: FC<PropsWithChildren<{ group: Group }>> = ({ group, children }) => {
  const [open, setOpen] = useState(false);
  const parts = useAuiState((s) => s.message.parts);
  const inGroup = group.indices.map((i) => parts[i]).filter(Boolean);
  const tools = inGroup.filter((p) => p!.type === "tool-call");
  if (!tools.length) return null;
  const title = groupLive(group.status) ? steps.running : steps.steps(tools.length);

  return (
    <div className="aui-steps my-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="cc-tap flex max-w-full min-w-0 items-center gap-1.5 rounded-md py-0.5 text-ink-2 text-sm enabled:hover:text-ink"
      >
        <span className="truncate">{title}</span>
        <ChevronRightIcon className={cn("size-3.5 shrink-0 transition", open && "rotate-90")} />
      </button>
      {open && <div className="mt-1 ms-1.5 border-line border-s ps-2">{children}</div>}
    </div>
  );
};
