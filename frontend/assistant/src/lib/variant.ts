// 外壳变体（docs/work/2026-10-16-cc-shell-design.md 4.1）：启动时写在 <html data-cc-variant>，globals.css 的「变体」一段按它切
// 侧栏底色、行高、组间距、组操作是否常显、对话正文字号。
//
//   desktop — CC 桌面版窗口里（lib/desktop.ts 的 isDesktopApp）：侧栏浮成卡片，行 30
//   phone   — 窄屏（hooks/use-mobile.ts 的同一条分界，手机外壳 MobileShell 也按它）：抽屉行 44，正文 16/24
//   web     — 其余（浏览器、电脑上装的网页应用）：侧栏贴边，行 32
//
// 窗口拉窄 / 拉宽跨过分界时跟着手机外壳一起换（桌面窗口不换）。

import { MOBILE_QUERY } from "@/hooks/use-mobile";
import { isDesktopApp } from "@/lib/desktop";

export type Variant = "web" | "desktop" | "phone";

export function pickVariant(desktop: boolean, narrow: boolean): Variant {
  if (desktop) return "desktop";
  return narrow ? "phone" : "web";
}

export function installVariant(root: HTMLElement = document.documentElement): void {
  const narrow = window.matchMedia(MOBILE_QUERY);
  const desktop = isDesktopApp();
  const apply = () => {
    root.dataset.ccVariant = pickVariant(desktop, narrow.matches);
  };
  apply();
  if (!desktop) narrow.addEventListener("change", apply);
}
