// 代录与「开始前确认」卡片共用的字（纯函数，不碰桌面桥与站点请求，对话末尾的待处理框也引它）：
//   - 原因代码 → 给人看的一句（实操原因、交单时没收的状态、结果不明；原码不外露，认不出的写「需要处理」）；
//   - 一张单的标题（单号 · 对方）；一个选项的说法（新建、现购 / 赊购、抵进项 / 记费用、编码与名称）。
// operator-shared.ts 照旧转出这些名字，原来的调用方不用改。

import { co, op } from "@/i18n";

/** 上次推送结果不明（站点给的原因代码）。 */
export const RESULT_UNKNOWN = "push.result_unknown";

/** 看着像内部代码的（[OPERATOR_X]、choice_needed:…、reading、HTTP 原文）：不给人看。 */
export function isRawCode(text: string): boolean {
  const value = text.trim();
  if (!value) return true;
  return (
    /\[[A-Z0-9_]+\]/.test(value) || /^[A-Z0-9]+_[A-Z0-9_]+/.test(value) || /^[a-z0-9_.-]+(:|$)/i.test(value) || /Traceback|Exception\b/.test(value)
  );
}

/** 原因代码 → 给人看的一句：实操原因、交单时没收的状态、结果不明，站点给的已翻译原文；都不是写「需要处理」。 */
export function reasonText(code: string | undefined, message?: string): string {
  const key = String(code ?? "").trim();
  const known = key === RESULT_UNKNOWN ? co.ledger.code.result_unknown
    : op.reason[key] ?? op.refused[key] ?? (key.startsWith("already_") ? op.refused.already : undefined);
  if (known) return known;
  if (message && !isRawCode(message)) return message.trim();
  return op.unknownReason;
}

/** 一张单的标题：单号 · 对方（没有的不写）；都没有写 fallback，fallback 像内部编号（CWH-…、P:…、64 位哈希）就写「单据」，
 *  内部编号不给用户看（11-08 生产：任务卡列了 24 行 CWH-哈希）。 */
export function docTitle(doc: { ref?: string | undefined; party?: string | undefined }, fallback = ""): string {
  return [doc.ref, doc.party].filter(Boolean).join(" · ") || (fallback && isInternalId(fallback) ? op.untitled : fallback);
}

/** 站点内部的单据编号（不给用户看）：Cowork 识别单 CWH-…、ERP 的 P: / S: / I:…、哈希。 */
/** 列出过、已不在待录清单里的单此刻的样子（服务端 `listed.gone` 的 state → 已有状态词，同代录报告 `feed.STATE_KEY`）：
 *  已入账带 Express 单号；读不到的（unknown）给中性的「等待」。 */
const GONE_STATE: Record<string, string> = { saved: "saved", not_done: "notDone", cancelled: "skipped", queued: "waiting", unknown: "waiting" };
export function goneLabel(doc: { state?: string | undefined; docnum?: string | undefined }): string {
  const word = op.doc[GONE_STATE[doc.state ?? ""] ?? "waiting"] ?? op.doc.waiting;
  return [word, doc.state === "saved" ? doc.docnum : ""].filter(Boolean).join(" · ");
}

export const isInternalId = (text: string) => /^(CWH-|[PSI]:)|^[0-9a-f]{24,}$/i.test(text.trim());

export const NEW_CODE = "__new__";
/** 存货对照的「记费用」：这一行不建存货、写成费用 / 服务行（11-06 按行选物料与库存）。 */
export const EXPENSE_CODE = "__expense__";
export type ChoiceOption = { code: string; name: string };

/** 代码就是选项的几项：现购 / 赊购、抵进项 / 记费用。 */
const CODED: Record<string, () => string> = {
  cash: () => co.payCash,
  credit: () => co.payCredit,
  claim: () => co.vatClaim,
  expense: () => co.vatExpense,
};
/** 只给代码、没有名字的选项按类给说法（11-08 生产：入账方式一项写成英文原值「stock / service」）。 */
const CODED_BY_KIND: Record<string, Record<string, () => string>> = {
  goods_posting: { stock: () => op.option.stock!, service: () => op.option.expense! },
  posting_kind: { stock: () => op.option.stock!, service: () => op.option.expense! },
};

/** 一个选项的说法：新建（商品写「库存（新建）」）、记费用、现购 / 赊购、抵进项 / 记费用；已有的写编码与名称。 */
/** 编码是内部编号、不给人看的几类（账套：book_key / 连接|键，测试机1 11-13 开始卡把它露了出来）：只写名称。 */
const OPAQUE_CODES = new Set(["account_set", "current_book"]);

export function optionLabel(kind: string, option: ChoiceOption): string {
  if (OPAQUE_CODES.has(kind)) return (option.name ?? "").trim() || op.unknownReason;
  if (option.code === NEW_CODE) return kind === "item" ? op.option.newItem! : op.option.new!;
  if (option.code === EXPENSE_CODE) return op.option.expense!;
  const name = (option.name ?? "").trim();
  if (!name && CODED_BY_KIND[kind]?.[option.code]) return CODED_BY_KIND[kind]![option.code]!();
  if (!name && CODED[option.code]) return CODED[option.code]!();
  return [option.code, name].filter(Boolean).join(" ") || op.unknownReason;
}

