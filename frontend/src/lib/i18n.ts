// 页面文案：**代码里只写文案编号**，正文从服务端的语言包取（ADR-006）。
//
// 为什么不把四语文案写进前端：那会变成第二份语言包 —— 同一句话在后端（LINE 卡片）和
// 前端（业务页）各维护一份，早晚不一致。这里一次把整页要用的键取回来（四语齐全），
// 页面上的语言开关切换时不再跑网络。
//
// 取词规则：当前语言 → 兜底语言（泰语）→ **显示 key 本身**。
// 显示 key 不好看，但它是**看得见**的（缺译要补，不能静默变成空白或英文）。

import { ref, type Ref } from "vue";
import { call } from "./api";

export type Locale = "th" | "zh" | "en" | "ja";

export const LOCALES: Locale[] = ["th", "en", "zh", "ja"];
export const LOCALE_LABELS: Record<Locale, string> = {
  th: "ไทย",
  en: "English",
  zh: "中文",
  ja: "日本語",
};

/** 兜底语言：老产品那份编辑器包缺译时也是回落到泰语（收件人是泰国销售）。 */
const FALLBACK: Locale = "th";
/** 浏览器缓存的语言（ADR-006：账号为准、浏览器只当缓存）。 */
const CACHE_KEY = "pearnly_lang";

export function cachedLocale(): Locale | "" {
  try {
    const saved = localStorage.getItem(CACHE_KEY) || "";
    return (LOCALES as string[]).includes(saved) ? (saved as Locale) : "";
  } catch {
    return "";
  }
}

export function rememberLocale(locale: Locale): void {
  try {
    localStorage.setItem(CACHE_KEY, locale);
  } catch {
    /* 隐私模式下存不下，不影响本次会话 */
  }
}

/**
 * 当前语言是**全局共享**的一份，不是每个取词器各一份。
 *
 * 为什么必须共享：侧栏（App.vue）和页面正文（HomePage.vue 等）各自建取词器；
 * 如果各自持有语言状态，切语言时只会变一半 —— 侧栏泰文、正文中文，
 * 而且"按账号语言设定"要写两遍、漏一处就不一致。
 */
const current = ref<Locale>(cachedLocale() || guessLocale());

export const localeRef: Ref<Locale> = current;

/**
 * 按**账号**的语言设定（ADR-006：账号为准，浏览器只当缓存）。
 *
 * 原来前端只按浏览器语言猜：同一个账号换台电脑就看到不同语言，
 * 与"账号为准"直接冲突。
 */
export function adoptAccountLocale(value: string): void {
  const next = (value || "").slice(0, 2) as Locale;
  if (!LOCALES.includes(next)) return;
  current.value = next;
  rememberLocale(next);
  document.documentElement.lang = next;
}

/** 没缓存时按浏览器语言猜（`zh-CN` → `zh`），猜不到就是泰语。 */
export function guessLocale(): Locale {
  const nav = (navigator.language || "").toLowerCase();
  if (nav.startsWith("zh")) return "zh";
  if (nav.startsWith("ja")) return "ja";
  if (nav.startsWith("en")) return "en";
  return FALLBACK;
}

export interface Texts {
  texts: Ref<Record<string, Record<string, string>>>;
  locale: Ref<Locale>;
  t: (key: string) => string;
  /** 带占位符的取词：语言包里写成 `{0}` `{1}`，这里按位置填。 */
  tf: (key: string, ...args: (string | number)[]) => string;
  load: () => Promise<void>;
  setLocale: (locale: Locale) => void;
  /** 按**账号**的语言设定（ADR-006：账号为准，浏览器只当缓存）。 */
}

/**
 * 建一套取词器。
 *
 * `keys` 传空数组 → 服务端把**这一页的整个命名空间**（由 `prefix` 指定，例如 `dms.edit.`）给回来。
 * 这样页面不用维护一份"我要用哪些键"的清单：清单和语言包一旦不同步，
 * 页面就会露出 key（真发生过）。
 *
 * 接口挂在公共层 `pearnly_core.browser_text`（2026-09-19 定的方案 A）：
 * 原来挂在 `pearnly_dms` 里，而 Cowork / ERP 的站点不装 DMS，那两个产品的页面
 * 根本取不到文案。公共层按**键的命名空间**决定读哪个 App 的语言包。
 */
export function createTexts(keys: string[] = [], prefix = ""): Texts {
  const texts = ref<Record<string, Record<string, string>>>({});
  const locale = current;

  function t(key: string): string {
    const bag = texts.value[key];
    if (!bag) return key;
    return bag[locale.value] || bag[FALLBACK] || bag.en || key;
  }

  /**
   * 带占位符取词。
   *
   * 为什么不让页面自己拼字符串：拼出来的 `"已订阅套餐 S，月费…"` 在语言包里
   * **查不到**（泰文/日文的语序和中文不一样，拼不出来），就会永远显示中文。
   * 所以文案整条进包、变量留 `{0}` 占位。
   */
  function tf(key: string, ...args: (string | number)[]): string {
    const text = t(key);
    if (!args.length) return text;
    return text.replace(/\{(\d+)\}/g, (_all, i) => String(args[Number(i)] ?? ""));
  }

  async function load(): Promise<void> {
    texts.value = await call<Record<string, Record<string, string>>>(
      "pearnly_core.browser_text.browser_text",
      { keys: JSON.stringify(keys), prefix },
    );
  }

  function setLocale(next: Locale): void {
    locale.value = next;
    rememberLocale(next);
    document.documentElement.lang = next;
  }

  return { texts, locale, t, tf, load, setLocale };
}
