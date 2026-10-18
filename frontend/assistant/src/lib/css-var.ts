// JS 里用到的 CSS 值（标准化记录 2.6）：数值只在 globals.css 定，JS 在用的时候读（打开菜单、提示框出现、拿起拖动、量宽度那一刻），
// 不在导入或首次渲染时读——生产页（www/assistant.html）用脚本动态插入样式表，模块脚本不等它生效，太早读到的是空串。
// 读不到（样式表还没生效、不在浏览器里）就用调用方给的回退值；回退值与 globals.css 同值，由测试核对两边一致。
//
// 原来三份「CSS 长度或时长 → 数字」的解析（lib/hover-scroll.ts 的 cssMs、lib/company-panel.ts 的 cssPx、components/split-handle.tsx 的
// px()）合成这一份；前两处改为从这里再导出（调用方不动，B8 删再导出），split-handle 随它所在的批次改为从这里导入。

/** CSS 时长（「150ms」「0.3s」）→ 毫秒；认不出回 fallback。 */
export function cssMs(value: string, fallback: number): number {
  const text = value.trim();
  const n = parseFloat(text);
  if (!Number.isFinite(n)) return fallback;
  return text.endsWith("ms") ? n : text.endsWith("s") ? n * 1000 : n;
}

/** CSS 长度 → 像素：px、rem（按 rootFont）、vw（按窗口宽）；认不出回 fallback（默认 0）。 */
export function cssPx(value: string, rootFont = 16, fallback = 0): number {
  const text = value.trim();
  const n = Number.parseFloat(text);
  if (!Number.isFinite(n)) return fallback;
  if (text.endsWith("rem")) return n * rootFont;
  if (text.endsWith("vw") && typeof window !== "undefined") return (n * window.innerWidth) / 100;
  return n;
}

/** 元素（默认 <html>）上一个 CSS 变量此刻的值；不在浏览器里、读不到时为空串。 */
export function cssVar(name: string, el?: Element): string {
  if (typeof document === "undefined" || typeof getComputedStyle !== "function") return "";
  const target = el ?? document.documentElement;
  if (!target) return "";
  try {
    return getComputedStyle(target).getPropertyValue(name).trim();
  } catch {
    return "";
  }
}

/** <html> 的字号（px），rem 换算用；读不到按浏览器默认 16。 */
export function rootFontPx(): number {
  return cssPx(cssVar("font-size"), 16, 16) || 16;
}

// 不随变体与深浅色变的几个值（菜单偏移与离边、提示框延时）：第一次读到就记住，之后不再读样式——调用方在每次渲染里取也不会逼浏览器
// 先把整页待算的样式算完（main.tsx 的 themeColor 记过这个代价）。没读到（样式表还没生效）不记，下次再读。
const fixed = new Map<string, number>();

function fixedValue(name: string, parse: (value: string) => number, fallback: number): number {
  const known = fixed.get(name);
  if (known !== undefined) return known;
  const text = cssVar(name);
  const value = text ? parse(text) : Number.NaN;
  if (!Number.isFinite(value)) return fallback;
  fixed.set(name, value);
  return value;
}

/** 不随变体变的长度变量（px），用时读、读到就记住；读不到回 fallback。 */
export function fixedPx(name: string, fallback: number): number {
  return fixedValue(name, (value) => cssPx(value, rootFontPx(), Number.NaN), fallback);
}

/** 不随变体变的时长变量（毫秒），用时读、读到就记住；读不到回 fallback。 */
export function fixedMs(name: string, fallback: number): number {
  return fixedValue(name, (value) => cssMs(value, Number.NaN), fallback);
}
