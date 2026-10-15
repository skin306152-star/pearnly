// 悬停滚动（用户 2026-10-15 夜，参考 DeepSeek 桌面端的录屏）：单行、放不下被截断的文字，鼠标放到这一行上（或键盘焦点落到
// 这一行）时，从开头匀速向左滚到末尾刚好露出、停住；离开 / 失焦立即回到开头（没有回滚动画）。放得下的不滚。
//
//   参数   — 都在 globals.css：--cc-marquee-delay（约 150ms 后开始，行尾操作按钮已出现、标题区已变窄再量）、
//            --cc-marquee-speed（每秒约 40px，线性）、--cc-marquee-min（最短 0.3s）、--cc-marquee-fade（滚动中左端 24 渐隐）；
//            元素自己的 --cc-marquee-end（已有的右端渐隐宽度，滚到末尾时最后一个字要越过它才算露出）
//   不滚   — 系统减少动态效果（prefers-reduced-motion）、触屏 / 不能悬停的设备、这一行里正开着输入框（就地改名）、
//            拖动中（按着键移进来、会话栏卡片在拖、页面上有 [data-dragging]）；按下（开始拖动、点进去）就回到开头
//   只在悬停时量：不给每一行常驻 ResizeObserver。静止时的截断样式（渐隐 / 省略号）不变：只有滚动中才换成 inline-block 平移
//
// 用法：components/ui/marquee.tsx 的 <Marquee>（外层是原来那个截断的元素，里面包一层平移的文字）。触发的「这一行」是最近的
// [data-marquee-host]，没有就取最近的菜单项 / 按钮 / 链接 / 会话栏行 / 列表行，再没有就是文字自己。
import { useEffect, useRef } from "react";
import { isCardDragging } from "@/lib/layers";
import { touchOnly } from "@/lib/touch";

const HOST = "[data-marquee-host]";
const ROW =
  '[role^="menuitem"], [role="option"], button, a, label, .cc-panel-row, .cc-list-item, .cc-list-row, .cc-grid-card, .cc-folder-line, .cc-choice-row';

/** 能不能悬停滚动：系统没要求减少动态效果、主要输入方式能悬停。 */
export function canHoverScroll(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return !touchOnly() && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** CSS 时长（「150ms」「0.3s」）→ 毫秒。 */
function ms(value: string, fallback: number): number {
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
  return !!host.querySelector("input, textarea") || isCardDragging() || !!document.querySelector("[data-dragging]");
}

/** 给截断的那个元素挂上悬停滚动，返回卸下的函数。元素里面要有一层 .cc-marquee-text（平移的就是它）。 */
export function attachHoverScroll(box: HTMLElement): () => void {
  const host = box.closest<HTMLElement>(HOST) ?? box.closest<HTMLElement>(ROW) ?? box;
  let hovered = false;
  let focused = false;
  let timer = 0;

  const stop = () => {
    window.clearTimeout(timer);
    timer = 0;
    if (!box.hasAttribute("data-marquee")) return;
    // 立即回到开头：去掉滚动状态，平移与过渡一起没了（没有回滚动画）
    box.removeAttribute("data-marquee");
    box.style.removeProperty("--cc-marquee-x");
    box.style.removeProperty("--cc-marquee-dur");
  };
  const run = () => {
    timer = 0;
    if (!(hovered || focused) || busy(host)) return;
    const over = box.scrollWidth - box.clientWidth;
    if (over <= 1) return; // 放得下：不滚
    const distance = over + (parseFloat(getComputedStyle(box).getPropertyValue("--cc-marquee-end")) || 0);
    const speed = parseFloat(rootVar("--cc-marquee-speed")) || 40;
    const duration = Math.max(ms(rootVar("--cc-marquee-min"), 300), (distance / speed) * 1000);
    box.setAttribute("data-marquee", "run");
    void box.offsetWidth; // 先停在开头，再开始过渡
    box.style.setProperty("--cc-marquee-dur", `${Math.round(duration)}ms`);
    box.style.setProperty("--cc-marquee-x", `${-Math.ceil(distance)}px`);
  };
  const start = () => {
    if (timer || box.hasAttribute("data-marquee") || !canHoverScroll()) return;
    timer = window.setTimeout(run, ms(rootVar("--cc-marquee-delay"), 150));
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
    if (!target?.matches?.(":focus-visible")) return; // 只认键盘焦点
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
  };
}

/** 组件里用：返回截断那个元素的 ref（components/ui/marquee.tsx）。 */
export function useHoverScroll<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => (ref.current ? attachHoverScroll(ref.current) : undefined), []);
  return ref;
}
