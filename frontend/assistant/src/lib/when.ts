// 日期时间的显示（按页面语言；泰文用佛历）：服务端给的「YYYY-MM-DD」只显示日期，带时区的时间按这台设备的时区显示日期 + 时分，
// 不带时区的 Frappe 时间（「2026-11-28 11:08:35.095638」，站点时区的钟点）不猜时区、照它的钟点显示日期 + 时分——秒和小数一律不显示
// （11-28 用户在设置 → 连接看到「…11:08:35.095638」问后面那串数字）；别的原样。
// 单放一个小模块：设置页、MR.ERP 授权这类不需要公司数据的地方用它，不必连带加载 lib/companies 的实时连接。
import { lang } from "@/i18n";

export const LOCALE: Record<string, string> = { th: "th-TH-u-ca-buddhist", zh: "zh-CN", en: "en-US", ja: "ja-JP" };

const ZONED = /[zZ]$|[+-]\d\d:?\d\d$/;
const NAIVE = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/;

/** 服务端给的时间 → 能交给 Intl 的时刻 + 时区：只有日期的、不带时区的钟点都按 UTC 摆出它字面上的数（显示时也用 UTC，即照原样）；
 *  带时区的按这台设备的时区；认不出回 null。``hasTime``：带不带时分。 */
export function serverTime(value: string | undefined | null): { date: Date; timeZone?: "UTC"; hasTime: boolean } | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (day) return { date: new Date(Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3]))), timeZone: "UTC", hasTime: false };
  const naive = NAIVE.exec(text);
  if (naive) {
    const [, y, mo, d, h, mi] = naive.map(Number);
    return { date: new Date(Date.UTC(y, mo - 1, d, h, mi)), timeZone: "UTC", hasTime: true };
  }
  if (!ZONED.test(text)) return null;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : { date, hasTime: true };
}

export function formatWhen(value: string | undefined | null): string {
  if (!value) return "";
  const at = serverTime(value);
  if (!at) return value;
  const style: Intl.DateTimeFormatOptions = at.hasTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" };
  return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { ...style, ...(at.timeZone ? { timeZone: at.timeZone } : {}) }).format(at.date);
}
