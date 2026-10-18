// 侧栏收起 / 展开、删完对话时焦点放哪（第 11 轮 E 段补审）。
//
// 两个落点，都是看得见、点了不弹键盘的按钮：
//   NAV_TOGGLE   — 对话区顶栏的导航钮：手机的菜单钮、侧栏收起时的展开钮（assistant.tsx 的 MainTopBar），侧栏看不到时用
//   NAV_FALLBACK — 侧栏 / 抽屉里一直在的那一颗：网页侧栏标题区的收起钮、桌面端侧栏标题栏里的侧栏开关、手机抽屉顶部的圆形搜索钮，
//                  侧栏看得到但里面一行不剩时用（只标属性，不改 data-slot：globals.css 按 cc-sidebar-toggle 定尺寸）
// 看不到的那一份在 inert 里（收起的侧栏、关着的抽屉、抽屉开着时的对话区），聚焦不上，按先后试下一个就行。

export const NAV_TOGGLE = '[data-slot="cc-nav-toggle"]';
export const NAV_FALLBACK = "[data-cc-nav-fallback]";

const ROW = '[data-slot="aui_thread-list-item"]';
const ROW_TRIGGER = '[data-slot="aui_thread-list-item-trigger"]';
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** 收起时交出焦点的那一处：元素本身；侧栏行再记行号（收起期间行被重画换了元素时按行号找回）。 */
export type Handed = { el: HTMLElement; row: string } | null;

/** 聚焦并核对真的到了（inert 里的、已卸下的聚焦不上）。 */
export function focusOn(el: Element | null | undefined): boolean {
  if (!(el as HTMLElement | null)?.isConnected) return false;
  (el as HTMLElement).focus({ preventScroll: true });
  return document.activeElement === el;
}

/** 焦点已经丢了：掉到页面最外层，或停在已卸下的元素上。 */
export function focusLost(): boolean {
  const a = document.activeElement;
  return !a || a === document.body || a === document.documentElement || !a.isConnected;
}

/** 侧栏收起那一刻（box 是侧栏容器）：焦点在侧栏里就交给顶栏的展开钮（同一次提交里出现），并记下原来那一处；不在侧栏里回 null。 */
export function handOffSidebarFocus(box: Element | null): Handed {
  const active = document.activeElement as HTMLElement | null;
  if (!box || !active || !box.contains(active)) return null;
  const row = active.closest<HTMLElement>(ROW)?.dataset.rowId ?? "";
  document.querySelector<HTMLElement>(NAV_TOGGLE)?.focus();
  return { el: active, row };
}

/** 侧栏展开那一刻：展开钮在同一次提交里卸下，焦点掉到页面最外层时才拿回来——回到收起前那一处（行被重画就按行号找回），
 *  没有就落在侧栏的 NAV_FALLBACK，再没有就是侧栏里第一个能聚焦的。焦点在别处（用户展开期间自己放的）不动。 */
export function takeBackSidebarFocus(box: Element | null, handed: Handed): void {
  if (!box || !focusLost()) return;
  const row = handed?.row ? box.querySelector(`${ROW}[data-row-id="${CSS.escape(handed.row)}"] ${ROW_TRIGGER}`) : null;
  for (const el of [handed?.el, row, box.querySelector(NAV_FALLBACK), box.querySelector(FOCUSABLE)]) {
    if (el && box.contains(el) && focusOn(el)) return;
  }
}
