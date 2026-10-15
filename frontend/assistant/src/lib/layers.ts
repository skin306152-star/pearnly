// 页面上开着的组件库弹层：弹窗（含警告框）、下拉菜单与它的子菜单。关着的还在退场动画里的是 data-state="closed"，不算。
// 返回键栈（lib/back-stack.ts）、侧栏键盘（lib/sidebar-keys.ts）、会话栏的 Esc 共用；本文件不引任何模块。
export const OPEN_LAYERS =
  '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"], [data-radix-menu-content][data-state="open"]';

// 正在拖会话栏的卡片（components/session-panel.tsx 拖动期间在根节点上的标记）：这时的 Esc 只是取消拖动，
// 不关会话栏、不清侧栏多选。
const CARD_DRAG = "ccCardDrag";

export function setCardDragging(on: boolean): void {
  if (on) document.documentElement.dataset[CARD_DRAG] = "";
  else delete document.documentElement.dataset[CARD_DRAG];
}

export function isCardDragging(): boolean {
  return CARD_DRAG in document.documentElement.dataset;
}
