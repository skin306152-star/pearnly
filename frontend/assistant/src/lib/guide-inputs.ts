// 引导卡上「不是单选」的几种输入的取值与核对（总设计 docs/work/2026-11-14-cc-guided-master-design.md 2.2 九种输入；
// 服务端同一套规则在 pearnly_core/assistant/guide/steps.py 的 value_problem，值的形状两边一致）。纯函数，不碰界面。
//   amounts  多选 + 每行金额（收款勾发票）：交回 [{code, amount}]，金额是正数、同一个码只出现一次、不超过这一行最多能填的数
//   split    一个总额分到几种方式上（现金 / 转账 / 折扣）：交回 [{code, amount}]，各项合计要等于总额
//   month / date / number / text：一格输入
// 金额一律按「分」的整数算（BigInt），不用浮点：0.1 + 0.2 这种误差不能出现在钱上；显示与交回都是两位小数的字符串。

export type AmountOption = { code: string; name?: string; note?: string; /** 勾上时默认填的数（发票的未收余额） */ amount?: string; /** 这一行最多能填多少 */ max?: string };
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
export function amountsProblem(options: readonly AmountOption[], rows: readonly AmountRow[]): { problem: AmountProblem; code?: string } {
  if (!rows.length) return { problem: "empty" };
  for (const row of rows) {
    const value = toMinor(row.amount);
    if (value === null || value <= 0n) return { problem: "amount", code: row.code };
    const max = toMinor(options.find((item) => item.code === row.code)?.max);
    if (max !== null && value > max) return { problem: "over", code: row.code };
  }
  return { problem: "" };
}

/** 分摊还差多少（分）：正数 = 还没分完，负数 = 分多了。 */
export const splitRemainder = (rows: readonly AmountRow[], total: string): bigint => (toMinor(total) ?? 0n) - sumMinor(rows);

/** 分摊哪里不对：一项都没填 / 有一项不是正数 / 合计不等于总额。空着的项不算（没用这种方式）。 */
export function splitProblem(rows: readonly AmountRow[], total: string): SplitProblem {
  const filled = rows.filter((row) => row.amount.trim() !== "");
  if (!filled.length) return "empty";
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

export type ValueKind = "month" | "date" | "number" | "text";
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
  if (kind === "number") return /^-?\d+(\.\d+)?$/.test(text.trim());
  return text.trim() !== "" && text.length <= TEXT_MAX;
}

/** 分 → 给人看的金额（整数部分按语言加千分位，小数两位）。 */
export function formatMinor(value: bigint, locale: string): string {
  const negative = value < 0n;
  const abs = negative ? -value : value;
  return `${negative ? "-" : ""}${new Intl.NumberFormat(locale).format(abs / 100n)}.${String(abs % 100n).padStart(2, "0")}`;
}
