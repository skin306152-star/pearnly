// 一组连续工具调用的摘要（同 Claude Code，设计文档 3.11）：按类计数、带失败数，各类按首次出现的顺序连成一行，
// 例「已运行 2 个命令（1 个失败），已读取 3 个文件」。纯函数：不碰 React、不读页面语言，文案由调用方传进来（i18n.ts 的 steps.summary）。
//
//   命令 bash · 读取 read · 编辑 write / edit · 搜索 glob / grep / web_search · 获取页面 web_fetch
//   本站工具（服务端声明了做完的说法，lib/tool-labels.ts）用它自己的说法，多次时「× n」；其余工具合成「已使用 N 个工具」
//   点了「拒绝」的一步（没做）不用做完的说法：写卡片上那句问话（去掉问号）加「已拒绝」（第 8 轮测试机：拒绝存公司规则后
//   摘要仍写「记下了公司选择」）；结果说没存上（status 为 not_saved / failed）的算失败

export type StepKind = "command" | "read" | "edit" | "search" | "fetch" | "tool";

export type SummaryCopy = Record<StepKind, (n: number) => string> & {
  failed: (n: number) => string;
  times: (label: string, n: number) => string;
  join: string;
};

/** 一步：工具名、是否失败（stepFailed）、是否点了「拒绝」没做。 */
export type StepPart = { toolName: string; failed: boolean; denied?: boolean };

/** 点了「拒绝」的步骤怎么写：「已拒绝」一词（沿用卡片上的词条）与各工具卡片上的问话（lib/tool-labels.ts 的 ask）。 */
export type Denial = { word: string; asks: Readonly<Record<string, string>> };

/** 摘要里的一项：kind 是上面六类之一，或 `own:<工具名>`（本站工具）。 */
export type SummaryEntry = { kind: string; count: number; failed: number };

const KINDS: Readonly<Record<string, StepKind>> = {
  bash: "command",
  read: "read",
  write: "edit",
  edit: "edit",
  glob: "search",
  grep: "search",
  web_search: "search",
  web_fetch: "fetch",
};

/** 只认对象自己的键：工具名叫 constructor、toString 时不能拿到原型上的函数。 */
const own = <T>(table: Readonly<Record<string, T>>, key: string): T | undefined =>
  Object.hasOwn(table, key) ? table[key] : undefined;

/** 一步算哪一类：点了「拒绝」的各工具单算一类（denied:）；内置工具按上表；本站工具（有做完的说法）各算一类；其余算「工具」。 */
export function stepKind(toolName: string, done: Readonly<Record<string, string>>, denied = false): string {
  if (denied) return `denied:${toolName}`;
  return own(KINDS, toolName) ?? (own(done, toolName) ? `own:${toolName}` : "tool");
}

/** 卡片上的问话当成没做的那一步的说法：去掉句末问号。 */
export function askStem(ask: string): string {
  return ask.replace(/[\s?？]+$/u, "");
}

// 结果里写明没做成的状态（服务端拒收、核对没过：例如存公司规则时对象对不上回 not_saved）
const UNDONE = new Set(["not_saved", "failed"]);

/** 本站工具结果里的 status（结果是 JSON 对象时）；没有回空串。 */
function resultStatus(result: unknown): string {
  const text = typeof result === "object" && result !== null && "text" in result ? (result as { text?: unknown }).text : null;
  if (typeof text !== "string") return "";
  const body = text.trim();
  if (!body.startsWith("{") || !body.includes('"status"')) return "";
  try {
    const status = (JSON.parse(body) as { status?: unknown }).status;
    return typeof status === "string" ? status : "";
  } catch {
    return "";
  }
}

/** 这一步点了「拒绝」、没做：本页点的（approval.approved 为 false），或重开对话时结果里的 status: denied
 *  （pearnly_core toolrun.decide）。 */
export function stepDenied(part: { result?: unknown; approval?: { approved?: boolean } | undefined }): boolean {
  return part.approval?.approved === false || resultStatus(part.result) === "denied";
}

/** 这一步失败了没有：结果标了出错、结果写明没做成（status 为 not_saved / failed）、步骤本身标了出错，或没做完就停了
 *  （同步骤行行尾的「失败」）。 */
export function stepFailed(part: { result?: unknown; isError?: boolean | undefined; status?: { type: string } | undefined }): boolean {
  const result = part.result;
  const resultError = typeof result === "object" && result !== null && "text" in result && (result as { isError?: unknown }).isError === true;
  return resultError || UNDONE.has(resultStatus(result)) || !!part.isError || part.status?.type === "incomplete";
}

/** 按类合并，保持首次出现的顺序。 */
export function summarize(parts: readonly StepPart[], done: Readonly<Record<string, string>>): SummaryEntry[] {
  const entries = new Map<string, SummaryEntry>();
  for (const part of parts) {
    const kind = stepKind(part.toolName, done, !!part.denied);
    const entry = entries.get(kind) ?? { kind, count: 0, failed: 0 };
    entry.count += 1;
    if (part.failed) entry.failed += 1;
    entries.set(kind, entry);
  }
  return [...entries.values()];
}

/** 摘要那一行的文字。 */
export function summaryText(
  entries: readonly SummaryEntry[],
  copy: SummaryCopy,
  done: Readonly<Record<string, string>>,
  denial: Denial = { word: "", asks: {} },
): string {
  return entries
    .map(({ kind, count, failed }) => {
      if (kind.startsWith("denied:")) {
        const tool = kind.slice(7);
        const ask = askStem(own(denial.asks, tool) ?? own(done, tool) ?? tool);
        const text = count > 1 ? copy.times(ask, count) : ask;
        return denial.word ? `${text} · ${denial.word}` : text;
      }
      const tool = kind.startsWith("own:") ? kind.slice(4) : "";
      const label = tool ? (own(done, tool) ?? tool) : "";
      const text = tool ? (count > 1 ? copy.times(label, count) : label) : copy[kind as StepKind](count);
      return failed ? `${text}${copy.failed(failed)}` : text;
    })
    .join(copy.join);
}
