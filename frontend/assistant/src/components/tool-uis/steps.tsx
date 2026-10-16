// 智能体的过程（思考、读写文件、运行命令、Pearnly 工具）照 Claude Code 收成一行摘要（设计文档 3.11）：
// 进行中是「正在运行」，做完按类计数、带失败数（「已运行 2 个命令（1 个失败），已读取 3 个文件」，lib/step-summary.ts）；
// 点开是一张细框卡片，一步一行、淡色一句说明（没有图标；本站工具后面带「 · 对象」，lib/step-subject.ts），失败的行尾红字「失败」，
// 能展开的行尾一个 12px 箭头；再点某一步才看到命令 / 内容与输出（等宽；本站工具只显示给人看的字段，lib/step-detail.ts）。
// 在做什么、用了多久由对话底部的工作状态行说（thread.tsx 的 RunIndicator），摘要行不再另放一个在动的点。
// 所有工具共用 StepRow。停下来等用户批准的那一步不收进组（thread.tsx 的 groupBy），直接在行下给「允许 / 拒绝」。
// 卡片规矩（docs/development/ccs.md）：点开的内容框不在对话里再套一层滚动——长内容先显示前 CLIP_LINES 行，
// 点「… N 行」整段展开；手机（粗指针）上能点的行点按区不小于 44pt；颜色、尺寸只用 globals.css 的变量（.cc-steps*）。

import { useAuiState, type ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ChevronRightIcon } from "lucide-react";
import { createContext, useContext, useMemo, useState, type FC, type PropsWithChildren } from "react";
import type { ToolApproval, ToolResultPayload } from "@/lib/managed-agents/reducer";
import { askStem, stepDenied, stepFailed, summarize, summaryText } from "@/lib/step-summary";
import { detailArgs, detailOutput } from "@/lib/step-detail";
import { knownEntries, parseResult, stepSubject } from "@/lib/step-subject";
import { toolLabel, useToolLabels, type ToolLabels } from "@/lib/tool-labels";
import { cn } from "@/lib/utils";
import { steps, t } from "@/i18n";
import { ApprovalBar } from "./approval-bar";
import { TypingDots } from "@/components/typing-dots";
import { Marquee } from "@/components/ui/marquee";

type Args = Record<string, unknown> | undefined;
type Step = { verb: string; target?: string; input?: string; showOutput: boolean; own?: boolean };

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

/** 一步的说明（进行中 / 完成）和点开后显示的内容。 */
function describe(toolName: string, args: Args, running: boolean, labels: ToolLabels): Step {
  const pick = (triple: readonly string[]) => (running ? triple[0] : triple[1]) ?? "";
  switch (toolName) {
    case "bash": {
      const command = str(args?.command);
      return { verb: pick(steps.bash), target: firstLine(command), input: command, showOutput: true };
    }
    case "read":
      return { verb: pick(steps.read), target: base(args?.file_path), showOutput: true };
    case "write":
      return { verb: pick(steps.write), target: base(args?.file_path), input: str(args?.content), showOutput: false };
    case "edit":
      return { verb: pick(steps.edit), target: base(args?.file_path), input: str(args?.new_string), showOutput: false };
    case "glob":
      return { verb: pick(steps.glob), target: str(args?.pattern), showOutput: true };
    case "grep":
      return { verb: pick(steps.grep), target: str(args?.pattern), showOutput: true };
    case "web_search":
      return { verb: pick(steps.search), target: str(args?.query), showOutput: true };
    case "web_fetch": {
      const url = str(args?.url);
      return { verb: pick(steps.fetch), target: url.replace(/^https?:\/\/(www\.)?/, "").split("/")[0], showOutput: true };
    }
    default: {
      // 本站工具：参数只显示给人看的（连接、账套键、记录名、本地路径不显示，lib/step-detail.ts）
      return { verb: toolLabel(labels, toolName, running), input: detailArgs(args), showOutput: true, own: true };
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

/** 一个结果里认出的记录名字与账套文件夹原写法（结果对象不变就只解析一次）。 */
const labelsCache = new WeakMap<object, Array<[string, string]>>();
function resultLabels(result: unknown): Array<[string, string]> {
  const text = outputOf(result)?.text;
  if (!text || typeof result !== "object" || result === null) return text ? knownEntries(parseResult(text)) : [];
  let labels = labelsCache.get(result);
  if (!labels) labelsCache.set(result, (labels = knownEntries(parseResult(text))));
  return labels;
}

type MessageLike = { content?: readonly unknown[]; parts?: readonly unknown[] };

/** 整段对话里各步结果认出的记录（所有步骤共用一份）：同一个消息数组只算一次（一屏的各步共用）；结果都没变（流式只来
 *  文字时）就交回同一个 Map，订阅它的步骤不随每个字重画。 */
const knownByMessages = new WeakMap<object, ReadonlyMap<string, string>>();
let lastKnown: { results: unknown[]; known: ReadonlyMap<string, string> } = { results: [], known: new Map() };
function knownRecords(messages: readonly MessageLike[]): ReadonlyMap<string, string> {
  const cached = knownByMessages.get(messages);
  if (cached) return cached;
  const results: unknown[] = [];
  for (const message of messages) {
    for (const part of message.content ?? message.parts ?? []) {
      if ((part as { type?: string }).type !== "tool-call") continue;
      const result = (part as { result?: unknown }).result;
      if (result !== undefined) results.push(result);
    }
  }
  const same = results.length === lastKnown.results.length && results.every((result, i) => result === lastKnown.results[i]);
  if (!same) {
    const known = new Map<string, string>();
    for (const result of results) for (const [id, label] of resultLabels(result)) if (!known.has(id)) known.set(id, label);
    lastKnown = { results, known };
  }
  knownByMessages.set(messages, lastKnown.known);
  return lastKnown.known;
}

/** 整段对话里各步结果认出的记录：内部名字 → 给人看的名字（单号、标题…），让「推送了单据」也写出是哪张——单号常在
 *  前几条回复列过 / 识别过的结果里（测试窗口第 2 轮：「推送了单据 · t9niexcp」）；另有账套文件夹的原写法。 */
function useKnownRecords(): ReadonlyMap<string, string> {
  return useAuiState((s) => knownRecords(s.thread.messages as readonly MessageLike[]));
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
  const errored = stepFailed({ result, isError, status });
  const step = describe(toolName, args as Args, running, labels);
  const known = useKnownRecords();
  const subject = step.own ? stepSubject(args as Args, output?.text, known) : "";
  // Held for Allow/Deny: say what it is about to do, not the past-tense label ("Saved …" before anything ran).
  const asking = awaiting ? (approval as ToolApproval).ask : undefined;
  // 点了「拒绝」：没做，写卡片上那句问话（下面的结论行写「已拒绝」），不写做完的说法（第 8 轮测试机：仍写「记下了公司选择」）
  const denied = stepDenied({ result, approval }) ? askStem((approval as ToolApproval | undefined)?.ask ?? labels.ask[toolName] ?? step.verb) : undefined;
  // 本站工具的结果只显示给人看的（同参数，lib/step-detail.ts）；内置工具（命令、文件）照原样
  const outputText = step.showOutput ? output?.text ?? "" : "";
  const shownOutput = useMemo(() => (outputText ? (step.own ? detailOutput(outputText) : outputText) : ""), [outputText, step.own]);
  const expandable = Boolean(step.input || shownOutput);

  return (
    <div className="aui-step">
      <button
        type="button"
        disabled={!expandable}
        aria-expanded={expandable ? open : undefined}
        onClick={() => setOpen((o) => !o)}
        className="cc-step-row cc-tap"
      >
        <Marquee className="min-w-0 flex-1 truncate">
          {asking ?? denied ?? step.verb}
          {step.target && <span className="cc-step-target">{` ${step.target}`}</span>}
          {subject && <span className="cc-step-target">{` · ${subject}`}</span>}
        </Marquee>
        {running && <TypingDots size="sm" className="shrink-0" />}
        {errored && <span className="cc-step-failed">{steps.failed}</span>}
        {/* 重开对话时没有卡片下面那行结论：「已拒绝」写在行尾（同「失败」的位置，淡色） */}
        {denied && approval?.approved === undefined && <span className="cc-step-denied">{t("denied")}</span>}
        {expandable && <ChevronRightIcon aria-hidden />}
      </button>
      {open && expandable && (
        <div className="cc-step-detail">
          {step.input && <Clipped text={step.input} />}
          {shownOutput && <Clipped text={shownOutput} className={cn(step.input && "border-line border-t", errored && "text-danger")} />}
        </div>
      )}
      {approval && (
        <ApprovalBar approval={approval} respondToApproval={respondToApproval} className="mx-2 mb-2" />
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

/** 在步骤组的卡片里：组里的每一步只是卡片里的一行，不再各自包一层卡片（测试窗口 S2：外卡套内卡）。 */
const InStepsGroup = createContext(false);

/** 不进组的那一步（等批准的）单独放进同样的带环卡片；在组里时原样渲染（组已经给了卡片）。 */
export const LoneStep: FC<PropsWithChildren> = ({ children }) =>
  useContext(InStepsGroup) ? <>{children}</> : <div className="cc-steps-card">{children}</div>;

/** 一段连续的思考与工具调用：一行摘要（进行中「正在运行」，做完按类计数、带失败数），点开是一张一步一行的卡片。
 *  只有思考、没有工具的不显示（没有可看的内容；在想由状态行说）。 */
export const StepsGroup: FC<PropsWithChildren<{ group: Group }>> = ({ group, children }) => {
  const [open, setOpen] = useState(false);
  const labels = useToolLabels();
  const parts = useAuiState((s) => s.message.parts);
  const inGroup = group.indices.map((i) => parts[i]).filter(Boolean);
  const tools = inGroup.filter((p) => p!.type === "tool-call");
  if (!tools.length) return null;
  const title = groupLive(group.status)
    ? steps.running
    : summaryText(summarize(tools.map(toStepPart), labels.done), steps.summary, labels.done, { word: t("denied"), asks: labels.ask });

  return (
    <div className="aui-steps cc-steps">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="cc-steps-summary cc-tap">
        <Marquee className="truncate">{title}</Marquee>
        <ChevronRightIcon aria-hidden />
      </button>
      {open && (
        <div className="cc-steps-card">
          <InStepsGroup.Provider value={true}>{children}</InStepsGroup.Provider>
        </div>
      )}
    </div>
  );
};

/** 组里的一个工具调用部件 → 摘要要的工具名与是否失败。 */
function toStepPart(part: unknown) {
  const tool = part as { toolName: string; result?: unknown; isError?: boolean; status?: { type: string }; approval?: ToolApproval };
  return { toolName: tool.toolName, failed: stepFailed(tool), denied: stepDenied(tool) };
}
