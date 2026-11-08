// 日期时间的显示（按页面语言；泰文用佛历）：服务端给的「YYYY-MM-DD」只显示日期，带时区的时间显示日期 + 时分，别的原样。
// 单放一个小模块：设置页、MR.ERP 授权这类不需要公司数据的地方用它，不必连带加载 lib/companies 的实时连接。
import { lang } from "@/i18n";

export const LOCALE: Record<string, string> = { th: "th-TH-u-ca-buddhist", zh: "zh-CN", en: "en-US", ja: "ja-JP" };

export function formatWhen(value: string | undefined | null): string {
  if (!value) return "";
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (day) {
    const date = new Date(Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3])));
    return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { dateStyle: "medium", timeZone: "UTC" }).format(date);
  }
  if (!/[zZ]$|[+-]\d\d:?\d\d$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
