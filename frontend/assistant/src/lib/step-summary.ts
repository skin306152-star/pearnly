// 一组连续工具调用的摘要（同 Claude Code，设计文档 3.11）：按类计数、带失败数，各类按首次出现的顺序连成一行，
// 例「已运行 2 个命令（1 个失败），已读取 3 个文件」。纯函数：不碰 React、不读页面语言，文案由调用方传进来（i18n.ts 的 steps.summary）。
//
//   命令 bash · 读取 read · 编辑 write / edit · 搜索 glob / grep / web_search · 获取页面 web_fetch
//   本站工具（服务端声明了做完的说法，lib/tool-labels.ts）用它自己的说法，多次时「× n」；其余工具合成「已使用 N 个工具」
//   点了「拒绝」的一步（没做）不用做完的说法：写卡片上那句问话（去掉问号）加「已拒绝」（第 8 轮测试机：拒绝存公司规则后
//   摘要仍写「记下了公司选择」）；结果说没存上（status 为 not_saved / failed）的算失败，要点「允许」的工具同样写问话、加「失败」
//   （第 8 轮审查：不能先读到「记下了」）。问话去掉疑问的写法：问号，中文的「要…吗」，泰文句末的 ไหม / หรือไม่，日文句末的「か」

export type StepKind = "command" | "read" | "edit" | "search" | "fetch" | "tool";

export type SummaryCopy = Record<StepKind, (n: number) => string> & {
  failed: (n: number) => string;
  times: (label: string, n: number) => string;
  join: string;
};

/** 一步：工具名、是否失败（stepFailed）、是否点了「拒绝」没做、结果是否写明没做成（stepUndone）。 */
export type StepPart = { toolName: string; failed: boolean; denied?: boolean; undone?: boolean };

/** 没做的步骤怎么写：拒绝时跟在问话后的一词（要点「允许」的步骤是卡片、不进组，组里用不到，可不给）、「失败」一词（沿用步骤行
 *  行尾的词条）与各工具卡片上的问话（lib/tool-labels.ts 的 ask）。 */
export type Denial = { word?: string; asks: Readonly<Record<string, string>>; failed?: string };

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

/** 一步算哪一类：点了「拒绝」的各工具单算一类（denied:），结果写明没做成的各工具单算一类（undone:）；内置工具按上表；
 *  本站工具（有做完的说法）各算一类；其余算「工具」。 */
export function stepKind(toolName: string, done: Readonly<Record<string, string>>, denied = false, undone = false): string {
  if (denied) return `denied:${toolName}`;
  if (undone) return `undone:${toolName}`;
  return own(KINDS, toolName) ?? (own(done, toolName) ? `own:${toolName}` : "tool");
}

/** 卡片上的问话当成没做的那一步的说法：去掉疑问的写法——问号；中文「（确定）要更改…吗」去掉「确定要 / 要」「吗」；泰文句末的
 *  ไหม / มั้ย / หรือไม่ / หรือเปล่า；日文「…しますか」去掉句末「か」（第 8 轮审查：只去问号时泰文、日文、中文「吗」读着仍是在问；
 *  存量文案 C2：中文确认句统一为 Frappe 的「确定要…吗？」）。 */
export function askStem(ask: string): string {
  let stem = ask.replace(/[\s?？]+$/u, "");
  if (/吗$/u.test(stem)) stem = stem.replace(/吗$/u, "").replace(/^(?:确定)?要/u, "");
  stem = stem.replace(/\s*(?:หรือไม่|หรือเปล่า|ไหม|มั้ย)$/u, "");
  stem = stem.replace(/(ます|です)か$/u, "$1");
  return stem.trim();
}

// 结果里写明没做成的状态（服务端拒收、核对没过：例如存公司规则时对象对不上回 not_saved）
const UNDONE = new Set(["not_saved", "failed"]);

/** 结果的正文（去掉首尾空白）；不是文字回空串。 */
function resultText(result: unknown): string {
  const text = typeof result === "object" && result !== null && "text" in result ? (result as { text?: unknown }).text : null;
  return typeof text === "string" ? text.trim() : "";
}

/** 本站工具结果（JSON 对象时）里的一个文字字段；没有回空串。 */
function resultField(result: unknown, field: "status" | "approval"): string {
  const body = resultText(result);
  if (!body.startsWith("{") || !body.includes(`"${field}"`)) return "";
  try {
    const value = (JSON.parse(body) as Record<string, unknown>)[field];
    return typeof value === "string" ? value : "";
  } catch {
    return "";
  }
}

/** 本站工具结果里的 status；没有回空串。 */
const resultStatus = (result: unknown) => resultField(result, "status");

/** 出卡片前就核出要拒绝、没问过用户（结果带 approval: not_asked，pearnly_core tools.not_asked）：那一步没出过卡片。 */
export function stepNotAsked(part: { result?: unknown }): boolean {
  return resultField(part.result, "approval") === "not_asked";
}

// 改成带 status: denied 之前（第 8 轮以前）点「拒绝」交回的结果原文（服务端不翻译这一句）：旧对话重开时照它认
const OLD_DENIAL = "The user did not allow this change. Do not try it again unless the user asks.";

/** 这一步点了「拒绝」、没做：本页点的（approval.approved 为 false），或重开对话时结果里的 status: denied
 *  （pearnly_core toolrun.decide），或旧对话里那句原文。 */
export function stepDenied(part: { result?: unknown; approval?: { approved?: boolean } | undefined }): boolean {
  return part.approval?.approved === false || resultStatus(part.result) === "denied" || resultText(part.result) === OLD_DENIAL;
}

/** 这一步结果写明没做成（status 为 not_saved / failed：服务端拒收、核对没过）。 */
export function stepUndone(part: { result?: unknown }): boolean {
  return UNDONE.has(resultStatus(part.result));
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
    const kind = stepKind(part.toolName, done, !!part.denied, !!part.undone);
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
  denial: Denial = { asks: {} },
): string {
  return entries
    .map(({ kind, count, failed }) => {
      if (kind.startsWith("denied:")) {
        const tool = kind.slice(7);
        const ask = askStem(own(denial.asks, tool) ?? own(done, tool) ?? tool);
        const text = count > 1 ? copy.times(ask, count) : ask;
        return denial.word ? `${text} · ${denial.word}` : text;
      }
      if (kind.startsWith("undone:")) {
        // 要点「允许」的工具：写问话加「失败」；不要允许的（没有问话）照做完的说法加失败数
        const tool = kind.slice(7);
        const ask = own(denial.asks, tool);
        if (ask && denial.failed) {
          const stem = askStem(ask);
          return `${count > 1 ? copy.times(stem, count) : stem} · ${denial.failed}`;
        }
        const label = own(done, tool) ?? tool;
        return `${count > 1 ? copy.times(label, count) : label}${copy.failed(failed)}`;
      }
      const tool = kind.startsWith("own:") ? kind.slice(4) : "";
      const label = tool ? (own(done, tool) ?? tool) : "";
      const text = tool ? (count > 1 ? copy.times(label, count) : label) : copy[kind as StepKind](count);
      return failed ? `${text}${copy.failed(failed)}` : text;
    })
    .join(copy.join);
}
