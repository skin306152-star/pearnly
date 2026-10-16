// 页面上开着的组件库弹层：弹窗（含警告框）、下拉菜单与它的子菜单。关着的还在退场动画里的是 data-state="closed"，不算。
// 返回键栈（lib/back-stack.ts）、侧栏键盘（lib/sidebar-keys.ts）、会话栏的 Esc 共用；本文件不引任何模块。
export const OPEN_LAYERS =
  '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"], [data-radix-menu-content][data-state="open"]';

// 正在拖动（components/ui/drag.tsx 共用拖动：会话栏卡片、侧栏公司组、置顶对话、对话拖到别的公司；拖动期间根节点上的标记）：
// 这时的 Esc 只是取消拖动，不关浮着的会话栏、不清侧栏多选，方向键不移侧栏焦点，悬停滚动不滚。
//
// 取消拖动的那一下 Esc 本身也要认得出来：拖动库在 document 上收到 Esc 就当场结束拖动、去掉标记，挂在 window 上的
// （侧栏多选的 Esc）随后才收到同一个事件，只看标记会以为没在拖（测试窗口第 4 轮第 9 项：多选被清空）。所以拖动期间在
// window 的捕获阶段（最先收到）先记下这次 Esc（isDragEscape），各处的 Esc 都先问它。
const DRAG = "ccDrag";
const escapes = new WeakSet<Event>();

function markEscape(event: KeyboardEvent) {
  if (event.key === "Escape") escapes.add(event);
}

export function setDragging(on: boolean): void {
  if (typeof document === "undefined") return;
  if (on) {
    document.documentElement.dataset[DRAG] = "";
    window.addEventListener("keydown", markEscape, true);
  } else {
    delete document.documentElement.dataset[DRAG];
    // 这次 Esc 在捕获阶段已经记下了，现在摘掉监听不影响后面的处理
    window.removeEventListener("keydown", markEscape, true);
  }
}

export function isDragging(): boolean {
  return typeof document !== "undefined" && DRAG in document.documentElement.dataset;
}

/** 这个 Esc 是不是用来取消拖动的（拖动中按下的；拖动库处理完已经结束拖动也算）。 */
export function isDragEscape(event: Event): boolean {
  return escapes.has(event) || isDragging();
}
