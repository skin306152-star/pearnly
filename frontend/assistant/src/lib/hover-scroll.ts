// 悬停滚动（用户 2026-10-15 夜，参考 DeepSeek 桌面端的录屏）：单行、放不下被截断的文字，鼠标放到这一行上（或键盘焦点落到
// 这一行）时，从开头匀速向左滚到末尾刚好露出、停住；离开 / 失焦立即回到开头（没有回滚动画）。放得下的不滚。
// 用户 10-16：「所有超出的内容，例如某列内容超出，鼠标放上去自动会滚动」——覆盖所有单行截断的文字。
//
//   参数   — 都在 globals.css：--cc-marquee-delay（约 150ms 后开始，行尾操作按钮已出现、标题区已变窄再量）、
//            --cc-marquee-speed（每秒约 40px，线性）、--cc-marquee-min（最短 0.3s）、--cc-marquee-fade（滚动中左端 24 渐隐）；
//            元素自己的 --cc-marquee-end（已有的右端渐隐宽度）
//   放不放得下 — 同静止时的末尾渐隐一个量法（lib/overflow-fade.ts 的 overflowOf，用户 10-16「统一渐隐」：只有真的放不下才
//            渐隐）：文字比框宽才滚，放得下的末尾不淡、也不滚；滚到末尾时最后一个字越过右端渐隐才算露出
//   键盘焦点 — 只有真正的键盘操作才因焦点滚（上一次输入是键盘，且 :focus-visible）。用鼠标关掉菜单、焦点被程序还给
//            「···」时不算，鼠标不在这一行就立即回到开头（第 5 轮：原来一直停在滚完的位置，要等焦点移走才复位）
//   滚动中文字变了 — 立即回到开头、重新量（只在滚动中观察这一个元素，不常驻）
//   不滚   — 系统减少动态效果（prefers-reduced-motion）、触屏 / 不能悬停的设备、这一行里正开着输入框（就地改名）、
//            拖动中（按着键移进来、正在拖动 lib/layers.ts、页面上有 [data-dragging]）；按下（开始拖动、点进去）就回到开头
//   悬停时才量滚多远（静止时放不放得下由 lib/overflow-fade.ts 整页一个观察器判定）。静止时的末尾渐隐不变：滚动中左右两端渐隐
//
// 用法：components/ui/marquee.tsx 的 <Marquee>（外层是原来那个截断的元素，里面包一层平移的文字）。触发的「这一行」是最近的
// [data-marquee-host]，没有就取最近的菜单项 / 按钮 / 链接 / 会话栏行 / 列表行，再没有就是文字自己（整行都该触发的地方
// 给那一行标 data-marquee-host）。
import { useEffect, useRef } from "react";
import { isDragging } from "@/lib/layers";
import { overflowOf, overflows } from "@/lib/overflow-fade";
import { touchOnly } from "@/lib/touch";

const HOST = "[data-marquee-host]";
const ROW =
  '[role^="menuitem"], [role="option"], button, a, label, .cc-panel-row, .cc-list-item, .cc-list-row, .cc-grid-card, .cc-folder-line, .cc-choice-row';

/** 能不能悬停滚动：系统没要求减少动态效果、主要输入方式能悬停。 */
export function canHoverScroll(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return !touchOnly() && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** CSS 时长（「150ms」「0.3s」）→ 毫秒（共用拖动的动效时长也用它）。 */
export function cssMs(value: string, fallback: number): number {
  const text = value.trim();
  const n = parseFloat(text);
  if (!Number.isFinite(n)) return fallback;
  return text.endsWith("ms") ? n : text.endsWith("s") ? n * 1000 : n;
}

function rootVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name);
}

/** 这一行忙着：开着输入框（就地改名），或页面上正在拖动。 */
function busy(host: HTMLElement): boolean {
  return !!host.querySelector("input, textarea") || isDragging() || !!document.querySelector("[data-dragging]");
}

// 上一次输入是不是键盘（整页一份，第一次挂上时才开始记）：只有键盘操作带来的焦点才滚
let keyboardLast = false;
let watching = false;
function watchInput() {
  if (watching || typeof window === "undefined") return;
  watching = true;
  window.addEventListener("keydown", () => (keyboardLast = true), true);
  window.addEventListener("pointerdown", () => (keyboardLast = false), true);
}

/** 文字要滚多远才露出最后一个字（0 就是放得下、不滚）：放不下时 = 多出的宽 + 右端渐隐宽；放得下（末尾不淡）不滚。 */
export function scrollDistance(over: number, fade: number): number {
  return overflows(over) ? over + fade : 0;
}

function distanceOf(box: HTMLElement): number {
  return scrollDistance(overflowOf(box), parseFloat(getComputedStyle(box).getPropertyValue("--cc-marquee-end")) || 0);
}

/** 给截断的那个元素挂上悬停滚动，返回卸下的函数。元素里面要有一层 .cc-marquee-text（平移的就是它）。 */
export function attachHoverScroll(box: HTMLElement): () => void {
  watchInput();
  const host = box.closest<HTMLElement>(HOST) ?? box.closest<HTMLElement>(ROW) ?? box;
  let hovered = false;
  let focused = false;
  let timer = 0;
  let changes: MutationObserver | null = null;

  const stop = () => {
    window.clearTimeout(timer);
    timer = 0;
    changes?.disconnect();
    changes = null;
    if (!box.hasAttribute("data-marquee")) return;
    // 立即回到开头：去掉滚动状态，平移与过渡一起没了（没有回滚动画）
    box.removeAttribute("data-marquee");
    box.style.removeProperty("--cc-marquee-x");
    box.style.removeProperty("--cc-marquee-dur");
  };
  const run = () => {
    timer = 0;
    if (!(hovered || focused) || busy(host)) return;
    const distance = distanceOf(box);
    if (!distance) return; // 放得下（末尾不淡）：不滚
    const speed = parseFloat(rootVar("--cc-marquee-speed")) || 40;
    const duration = Math.max(cssMs(rootVar("--cc-marquee-min"), 300), (distance / speed) * 1000);
    box.setAttribute("data-marquee", "run");
    void box.offsetWidth; // 先停在开头，再开始过渡
    box.style.setProperty("--cc-marquee-dur", `${Math.round(duration)}ms`);
    box.style.setProperty("--cc-marquee-x", `${-Math.ceil(distance)}px`);
    // 滚动中文字变了（标题改了、计数变了）：回到开头，还在悬停 / 键盘焦点上就重新量
    changes = new MutationObserver(() => {
      stop();
      start();
    });
    changes.observe(box, { childList: true, characterData: true, subtree: true });
  };
  const start = () => {
    if (timer || box.hasAttribute("data-marquee") || !canHoverScroll()) return;
    timer = window.setTimeout(run, cssMs(rootVar("--cc-marquee-delay"), 150));
  };

  const enter = (event: PointerEvent) => {
    if (event.pointerType === "touch" || event.buttons) return; // 触屏、按着键拖进来的不滚
    hovered = true;
    start();
  };
  const leave = () => {
    hovered = false;
    if (!focused) stop();
  };
  const focusIn = (event: FocusEvent) => {
    const target = event.target as Element | null;
    // 只认键盘操作带来的焦点；鼠标关掉菜单后焦点被还回来不算，鼠标也不在这一行就回到开头
    if (!keyboardLast || !target?.matches?.(":focus-visible")) {
      focused = false;
      if (!hovered) stop();
      return;
    }
    focused = true;
    start();
  };
  const focusOut = (event: FocusEvent) => {
    const next = event.relatedTarget as Node | null;
    if (next && host.contains(next)) return; // 焦点还在这一行里
    focused = false;
    if (!hovered) stop();
  };
  const down = () => {
    hovered = false;
    focused = false;
    stop();
  };

  host.addEventListener("pointerenter", enter);
  host.addEventListener("pointerleave", leave);
  host.addEventListener("focusin", focusIn);
  host.addEventListener("focusout", focusOut);
  host.addEventListener("pointerdown", down);
  return () => {
    host.removeEventListener("pointerenter", enter);
    host.removeEventListener("pointerleave", leave);
    host.removeEventListener("focusin", focusIn);
    host.removeEventListener("focusout", focusOut);
    host.removeEventListener("pointerdown", down);
    window.clearTimeout(timer);
    changes?.disconnect();
  };
}

/** 组件里用：返回截断那个元素的 ref（components/ui/marquee.tsx）。 */
export function useHoverScroll<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => (ref.current ? attachHoverScroll(ref.current) : undefined), []);
  return ref;
}
