// 引导卡上「不是单选」的几种输入的取值与核对（总设计 docs/work/2026-11-14-cc-guided-master-design.md 2.2 九种输入；
// 服务端同一套规则在 pearnly_core/assistant/guide/steps.py 的 value_problem，值的形状两边一致）。纯函数，不碰界面。
//   amounts  多选 + 每行金额（收款勾发票）：交回 [{code, amount}]，金额是正数、同一个码只出现一次、不超过这一行最多能填的数
//   split    一个总额分到几种方式上（现金 / 转账 / 折扣）：交回 [{code, amount}]，各项合计要等于总额
//   month / date / number / text：一格输入
// 金额一律按「分」的整数算（BigInt），不用浮点：0.1 + 0.2 这种误差不能出现在钱上；显示与交回都是两位小数的字符串。

export type AmountOption = { code: string; name?: string; note?: string; /** 勾上时默认填的数（发票的未收余额） */ amount?: string; /** 这一行最多能填多少 */ max?: string;
  /** 不占 max_rows 的名额（折扣，wire 50ba9dfb9） */ free?: boolean;
  /** 这一行从合计里扣（收款的抵减单：减项、定金；行上照填正数） */ negative?: boolean };
export type AmountRow = { code: string; amount: string };
export type AmountProblem = "" | "empty" | "amount" | "over";
export type SplitProblem = "" | "empty" | "amount" | "mismatch";

const MONEY = /^\d{1,12}(\.\d{1,2})?$/;

/** 用户打的金额（可带千分位逗号、首尾空白）→ 分；不是金额（负数、三位小数、字母）回 null。 */
export function toMinor(text: string | undefined | null): bigint | null {
  const clean = String(text ?? "").trim().replace(/,/g, "");
  if (!MONEY.test(clean)) return null;
  const [whole, fraction = ""] = clean.split(".");
  return BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
}

/** 分 → 两位小数的字符串（交回服务端、填进输入框用；千分位由显示的地方按语言加）。 */
export function fromMinor(value: bigint): string {
  const negative = value < 0n;
  const abs = negative ? -value : value;
  return `${negative ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

/** 各行金额的合计（分）；有一行不是金额就当 0（合计只作显示，交回前另有核对）。 */
export const sumMinor = (rows: readonly AmountRow[]): bigint => rows.reduce((total, row) => total + (toMinor(row.amount) ?? 0n), 0n);

/** 金额多选的合计：带 negative 的选项那几行减（收款：发票 − 抵减单）。 */
export function signedSumMinor(options: readonly AmountOption[], rows: readonly AmountRow[]): bigint {
  const minus = new Set(options.filter((option) => option.negative).map((option) => option.code));
  return rows.reduce((total, row) => total + (minus.has(row.code) ? -1n : 1n) * (toMinor(row.amount) ?? 0n), 0n);
}

/** 勾上一行时填进去的数：这一行的默认数（没有就空着让人填），规范成两位小数。 */
export function defaultAmount(option: AmountOption): string {
  const found = toMinor(option.amount);
  return found === null ? "" : fromMinor(found);
}

/** 勾 / 取消勾一行之后的各行（照选项原来的顺序）。 */
export function toggleRow(options: readonly AmountOption[], rows: readonly AmountRow[], code: string, checked: boolean): AmountRow[] {
  const kept = new Map(rows.filter((row) => row.code !== code).map((row) => [row.code, row.amount]));
  const option = options.find((item) => item.code === code);
  if (checked && option) kept.set(code, defaultAmount(option));
  return options.filter((item) => kept.has(item.code)).map((item) => ({ code: item.code, amount: kept.get(item.code) ?? "" }));
}

/** 改一行的金额（没勾的行不变）。 */
export const setAmount = (rows: readonly AmountRow[], code: string, amount: string): AmountRow[] =>
  rows.map((row) => (row.code === code ? { code, amount } : row));

/** 金额多选哪里不对：一行都没勾 / 有一行不是正数 / 有一行超过它最多能填的数；回哪一种与是哪一行。 */
export function amountsProblem(options: readonly AmountOption[], rows: readonly AmountRow[], zero = false): { problem: AmountProblem; code?: string } {
  if (!rows.length) return { problem: "empty" };
  for (const row of rows) {
    const value = toMinor(row.amount);
    // zero（N4 盘点记数）：数到 0 是有效的
    if (value === null || value < 0n || (value === 0n && !zero)) return { problem: "amount", code: row.code };
    const max = toMinor(options.find((item) => item.code === row.code)?.max);
    if (max !== null && value > max) return { problem: "over", code: row.code };
  }
  return { problem: "" };
}

/** 分摊还差多少（分）：正数 = 还没分完，负数 = 分多了。 */
/** max_rows（题上给，例：MR.ERP 账本一笔只收一种方式）：不带 free 的行里金额不是 0 的算名额，满了以后其余不带 free 的行要锁；
 *  回这一刻该锁的选项码（组件里再放过已勾 / 已填了字的那一行，清空就放开）。没给 max_rows 回空。服务端超出时拒收 too_many_rows。 */
export function lockedRows(options: readonly AmountOption[], rows: readonly AmountRow[], maxRows?: number): ReadonlySet<string> {
  if (!maxRows || maxRows < 1) return new Set();
  const free = new Set(options.filter((option) => option.free).map((option) => option.code));
  const counted = new Set(rows.filter((row) => !free.has(row.code) && (toMinor(row.amount) ?? 0n) !== 0n).map((row) => row.code));
  if (counted.size < maxRows) return new Set();
  return new Set(options.filter((option) => !option.free && !counted.has(option.code)).map((option) => option.code));
}

export const splitRemainder = (rows: readonly AmountRow[], total: string): bigint => (toMinor(total) ?? 0n) - sumMinor(rows);

/** 分摊哪里不对：一项都没填 / 有一项不是正数 / 合计不等于总额。空着的项不算（没用这种方式）。
 *  总额是 0（收款：抵减单正好抵平发票，没有钱要分）时一项都不填就对。 */
export function splitProblem(rows: readonly AmountRow[], total: string): SplitProblem {
  const filled = rows.filter((row) => row.amount.trim() !== "");
  if (!filled.length) {
    // 应分不是正数（收款抵减单抵平为 0、抵过头为负、差额退现金）：一项不填就能交
    const clean = String(total ?? "").trim();
    const due = toMinor(clean.startsWith("-") ? clean.slice(1) : clean);
    return due !== null && (due === 0n || clean.startsWith("-")) ? "" : "empty";
  }
  if (filled.some((row) => (toMinor(row.amount) ?? 0n) <= 0n)) return "amount";
  return splitRemainder(filled, total) === 0n ? "" : "mismatch";
}

/** 交回服务端的各行：只带填了的，金额规范成两位小数。 */
export const rowsToSend = (rows: readonly AmountRow[]): AmountRow[] =>
  rows.filter((row) => toMinor(row.amount) !== null && row.amount.trim() !== "").map((row) => ({ code: row.code, amount: fromMinor(toMinor(row.amount) as bigint) }));

/** 只剩一种方式没填时，把还差的数整笔填给它（点「余下的都算这种」）；分多了或别的项有不是金额的就不动。 */
export function fillRest(rows: readonly AmountRow[], total: string, code: string): AmountRow[] {
  const others = rows.filter((row) => row.code !== code);
  if (others.some((row) => row.amount.trim() !== "" && toMinor(row.amount) === null)) return [...rows];
  const rest = splitRemainder(others, total);
  return rest > 0n ? setAmount(rows, code, fromMinor(rest)) : [...rows];
}

export type ValueKind = "month" | "date" | "time" | "number" | "text";
export const TEXT_MAX = 500;

/** 一格输入的值对不对（规则同服务端）。 */
export function valueOk(kind: ValueKind, value: string): boolean {
  const text = String(value ?? "");
  if (kind === "month") return /^\d{4}-(0[1-9]|1[0-2])$/.test(text);
  if (kind === "date") {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
    const [year, month, day] = text.split("-").map(Number);
    const found = new Date(Date.UTC(year, month - 1, day));
    return found.getUTCFullYear() === year && found.getUTCMonth() === month - 1 && found.getUTCDate() === day;
  }
  // N8 时刻：HH:MM，24 小时
  if (kind === "time") return /^([01]\d|2[0-3]):[0-5]\d$/.test(text);
  if (kind === "number") return /^-?\d+(\.\d+)?$/.test(text.trim());
  return text.trim() !== "" && text.length <= TEXT_MAX;
}

/** 分 → 给人看的金额（整数部分按语言加千分位，小数两位）。 */
export function formatMinor(value: bigint, locale: string): string {
  const negative = value < 0n;
  const abs = negative ? -value : value;
  return `${negative ? "-" : ""}${new Intl.NumberFormat(locale).format(abs / 100n)}.${String(abs % 100n).padStart(2, "0")}`;
}
