// 关掉一层（浮着的会话栏、设置弹窗）以后焦点回到打开它的那个控件（测试窗口第 6 轮：Esc 关掉后焦点落到 BODY，键盘用户只能从头 Tab）。
// 打开时记下此刻的焦点（opener）；焦点在下拉菜单里（菜单项打开的）就记那个菜单的按钮——菜单项随菜单一起卸下了。关掉后还给它；
// 它不在了（卸下、藏起）就还给 fallback 选中的第一个看得见的控件（合理的上层：会话栏开关、设置齿轮）。
// 焦点描边只在键盘关的时候出现：:focus-visible 由浏览器按最近一次是键盘还是鼠标判断，支持 focusVisible 选项的浏览器这里再明说一次。
// 本文件只碰 DOM，不引别的模块。

let keyboard = false;

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("keydown", (event) => {
    if (!event.metaKey && !event.ctrlKey && !event.altKey) keyboard = true;
  }, true);
  for (const type of ["pointerdown", "mousedown", "touchstart"]) window.addEventListener(type, () => (keyboard = false), true);
}

/** 此刻是谁要打开新的一层：焦点所在的控件；在下拉菜单里时是那个菜单的按钮（aria-controls 指着菜单）。没有焦点回 null。 */
export function opener(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const el = document.activeElement as HTMLElement | null;
  if (!el || el === document.body || el === document.documentElement) return null;
  const menu = el.closest?.<HTMLElement>('[role="menu"]');
  if (menu?.id) {
    const id = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(menu.id) : menu.id;
    const trigger = document.querySelector<HTMLElement>(`[aria-controls="${id}"]`);
    if (trigger) return trigger;
  }
  return el;
}

/** 看得见、能拿焦点：还在页面上、有框、不在 inert 里。 */
function usable(el: HTMLElement | null | undefined): el is HTMLElement {
  return !!el?.isConnected && el.getClientRects().length > 0 && !el.closest("[inert]");
}

/** 把焦点还给 el；el 不在了就给 fallback 选中的第一个看得见的控件；都没有就不动。键盘关的才出焦点描边。 */
export function giveFocusBack(el: HTMLElement | null | undefined, fallback: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const target = usable(el) ? el : Array.from(document.querySelectorAll<HTMLElement>(fallback)).find(usable);
  if (!target) return null;
  target.focus({ preventScroll: true, focusVisible: keyboard } as FocusOptions);
  return target;
}

/** 一层关掉、它的节点卸下以后（焦点这时掉到 BODY），焦点原来在这一层里（或已经在 BODY）才还回去；用户已经点去了别处就不抢。
 *  inside 是关之前焦点在不在这一层里（卸下前量）。 */
export function returnFocusAfterClose(inside: boolean, el: HTMLElement | null | undefined, fallback: string) {
  if (typeof document === "undefined") return;
  const before = document.activeElement;
  if (!inside && before && before !== document.body) return;
  queueMicrotask(() => {
    const now = document.activeElement;
    if (!now || now === document.body || !now.isConnected) giveFocusBack(el, fallback);
  });
}
