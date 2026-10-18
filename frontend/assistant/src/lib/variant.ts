// 外壳变体（docs/work/2026-10-16-cc-shell-design.md 4.1）：启动时写在 <html data-cc-variant>，globals.css 的「变体」一段按它切
// 侧栏底色、行高、组间距、组操作是否常显、对话正文字号。
//
//   phone   — 窄屏（hooks/use-mobile.ts 的同一条分界，手机外壳 MobileShell 也按它）：抽屉行 44，正文 16/24。
//             窄屏优先：CC 桌面版窗口最窄 420，拉到分界以下也换成手机外壳（抽屉），密度跟着外壳走
//   desktop — CC 桌面版窗口里（lib/desktop.ts 的 isDesktopApp）且不窄：侧栏浮成卡片，行 30
//   web     — 其余（浏览器、电脑上装的网页应用）：侧栏贴边，行 32
//
// 窗口拉窄 / 拉宽跨过分界时跟着手机外壳一起换（桌面窗口也一样）。
//
// 触屏标记（docs/work/2026-10-17-cc-frontend-standards.md 3.4）：同时写 <html data-cc-touch>——手机变体，或粗指针且不是桌面窗口
// （平板、触屏笔记本上的网页）。globals.css 的点按区 44、手机档尺寸只按它一条选择器（:root[data-cc-touch]），组件用
// in-data-[cc-touch]:；桌面窗口在触屏上也不跟（ccs.md「不改电脑值」）。外接鼠标、平板接键盘时主指针会变，监听着跟着换。
// 三种触屏判定各管各的：data-cc-touch 管尺寸与点按区；data-cc-variant 管外壳；lib/touch.ts 的 touchOnly()（hover: none）
// 只管「没有悬停」时的行为（长按、操作常显），不管尺寸。

import { MOBILE_QUERY } from "@/hooks/use-mobile";
import { isDesktopApp } from "@/lib/desktop";

export type Variant = "web" | "desktop" | "phone";

/** 主指针是粗指针（手指）：与 globals.css 原来的 @media (pointer: coarse) 同一条查询。 */
export const COARSE_QUERY = "(pointer: coarse)";

export function pickVariant(desktop: boolean, narrow: boolean): Variant {
  if (narrow) return "phone";
  return desktop ? "desktop" : "web";
}

/** 要不要按触屏的尺寸与点按区：手机变体，或粗指针且不是桌面窗口。 */
export function pickTouch(variant: Variant, coarse: boolean): boolean {
  return variant === "phone" || (coarse && variant !== "desktop");
}

export function installVariant(root: HTMLElement = document.documentElement): void {
  const narrow = window.matchMedia(MOBILE_QUERY);
  const coarse = window.matchMedia(COARSE_QUERY);
  const desktop = isDesktopApp();
  const apply = () => {
    const variant = pickVariant(desktop, narrow.matches);
    root.dataset.ccVariant = variant;
    if (pickTouch(variant, coarse.matches)) root.dataset.ccTouch = "";
    else delete root.dataset.ccTouch;
  };
  apply();
  narrow.addEventListener("change", apply);
  coarse.addEventListener("change", apply);
}
