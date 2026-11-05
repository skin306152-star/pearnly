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

/** 一张单的标题：单号 · 对方（没有的不写；都没有写 fallback）。 */
export function docTitle(doc: { ref?: string | undefined; party?: string | undefined }, fallback = ""): string {
  return [doc.ref, doc.party].filter(Boolean).join(" · ") || fallback;
}

export const NEW_CODE = "__new__";
export type ChoiceOption = { code: string; name: string };

/** 代码就是选项的几项：现购 / 赊购、抵进项 / 记费用。 */
const CODED: Record<string, () => string> = {
  cash: () => co.payCash,
  credit: () => co.payCredit,
  claim: () => co.vatClaim,
  expense: () => co.vatExpense,
};

/** 一个选项的说法：新建（商品写「库存（新建）」）、现购 / 赊购、抵进项 / 记费用；已有的写编码与名称。 */
export function optionLabel(kind: string, option: ChoiceOption): string {
  if (option.code === NEW_CODE) return kind === "item" ? op.option.newItem! : op.option.new!;
  const name = (option.name ?? "").trim();
  if (!name && CODED[option.code]) return CODED[option.code]!();
  return [option.code, name].filter(Boolean).join(" ") || op.unknownReason;
}

