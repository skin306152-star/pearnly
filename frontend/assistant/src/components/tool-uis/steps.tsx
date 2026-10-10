// 智能体的过程（思考、读写文件、运行命令、Pearnly 工具）按 claude.ai 的样子收成一行「步骤」：
// 进行中显示当前在做什么；点开是一行一步的列表；点某一步才看到命令 / 内容与输出（浅色小框）。
// 所有工具共用 StepRow，按工具名给图标与说明。停下来等用户批准的那一步不收进组（thread.tsx 的 groupBy），
// 直接在行下给「允许 / 拒绝」。

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
import { useToolLabels } from "@/lib/tool-labels";
import { cn } from "@/lib/utils";
import { steps } from "@/i18n";
import { ApprovalBar } from "./approval-bar";
import { TypingDots } from "@/components/typing-dots";

type Args = Record<string, unknown> | undefined;
type Step = { icon: typeof FileIcon; verb: string; target?: string; input?: string; showOutput: boolean };

const str = (value: unknown) => (typeof value === "string" ? value : "");
const base = (path: unknown) => str(path).split("/").pop() ?? "";
const firstLine = (text: string) => {
  const line = text.trim().split("\n")[0] ?? "";
  return line.length > 90 ? `${line.slice(0, 88)}…` : line;
};

/** 一步的图标、说明（进行中 / 完成）和点开后显示的内容。 */
function describe(toolName: string, args: Args, running: boolean, labels: Record<string, string>): Step {
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
      return { icon: WrenchIcon, verb: labels[toolName] ?? toolName, input, showOutput: true };
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
        className="group/step flex w-full min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-left text-[#5b5950] text-sm enabled:hover:bg-[#f5f5f5]"
      >
        {running ? (
          <TypingDots size="sm" className="w-3.5 shrink-0 justify-center" />
        ) : (
          <Icon className={cn("size-3.5 shrink-0", errored && "text-red-500")} />
        )}
        <span className="shrink-0">{asking ?? step.verb}</span>
        {step.target && <span className="min-w-0 truncate font-mono text-[#8a887f] text-[12.5px]">{step.target}</span>}
        {expandable && (
          <ChevronRightIcon className={cn("ms-auto size-3.5 shrink-0 opacity-0 transition group-hover/step:opacity-100", open && "rotate-90 opacity-100")} />
        )}
      </button>
      {open && expandable && (
        <div className="ms-7 mt-1 mb-2 overflow-hidden rounded-md border border-[#e5e5e5] bg-[#fafafa] font-mono text-[12px] text-[#3d3d3a] leading-5">
          {step.input && <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all px-3 py-2">{step.input}</pre>}
          {shownOutput && (
            <pre
              className={cn(
                "max-h-56 overflow-auto whitespace-pre-wrap break-all px-3 py-2",
                step.input && "border-[#e5e5e5] border-t",
                errored && "text-red-600",
              )}
            >
              {shownOutput}
            </pre>
          )}
        </div>
      )}
      {approval && (
        <ApprovalBar approval={approval} respondToApproval={respondToApproval} className="ms-7 mt-1 mb-2" />
      )}
    </div>
  );
};

type Group = { indices: readonly number[]; status: { type: string } };

/** 一段连续的思考与工具调用：一行标题，点开是步骤列表。只有思考、且已结束的不显示（没有可看的内容）。 */
export const StepsGroup: FC<PropsWithChildren<{ group: Group }>> = ({ group, children }) => {
  const [open, setOpen] = useState(false);
  const labels = useToolLabels();
  const parts = useAuiState((s) => s.message.parts);
  const running = group.status.type === "running";
  const inGroup = group.indices.map((i) => parts[i]).filter(Boolean);
  const tools = inGroup.filter((p) => p!.type === "tool-call");
  if (!tools.length && !running) return null;

  const last = inGroup[inGroup.length - 1];
  let title = steps.steps(tools.length);
  if (running) {
    title = steps.thinking;
    if (last?.type === "tool-call" && last.result === undefined) {
      const step = describe(last.toolName, last.args as Args, true, labels);
      title = step.target ? `${step.verb} ${step.target}` : step.verb;
    }
  }

  return (
    <div className="aui-steps my-1.5">
      <button
        type="button"
        disabled={!tools.length}
        onClick={() => setOpen((o) => !o)}
        className="flex max-w-full min-w-0 items-center gap-1.5 rounded-md py-0.5 text-[#5b5950] text-sm enabled:hover:text-[#1a1a18]"
      >
        {running && <TypingDots size="sm" className="shrink-0" />}
        <span className="truncate">{title}</span>
        {tools.length > 0 && <ChevronRightIcon className={cn("size-3.5 shrink-0 transition", open && "rotate-90")} />}
      </button>
      {open && <div className="mt-1 ms-1.5 border-[#e5e5e5] border-s ps-2">{children}</div>}
    </div>
  );
};
