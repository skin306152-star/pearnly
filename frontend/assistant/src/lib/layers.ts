// 页面上开着的组件库弹层：弹窗（含警告框）、下拉菜单与它的子菜单。关着的还在退场动画里的是 data-state="closed"，不算。
// 返回键栈（lib/back-stack.ts）、侧栏键盘（lib/sidebar-keys.ts）、会话栏的 Esc 共用；本文件不引任何模块。
//
// Esc 一次只关一层，最上面的先关：弹窗、菜单（组件库）> 拖动 > 改名、编辑框 > 浮着的会话栏 > 侧栏多选。关掉一层的处理都
// 标记已处理（preventDefault），下面各层见到已处理的 Esc 就不动（测试窗口第 5 轮第 4 项）。
export const OPEN_LAYERS =
  '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"], [data-radix-menu-content][data-state="open"]';

// 正在拖动（components/ui/drag.tsx 共用拖动：会话栏卡片、侧栏公司组、置顶对话、组里的对话排先后或拖进已置顶；isDragging）：
// 这时的 Esc 只是取消拖动，不关浮着的会话栏、不清侧栏多选，方向键不移侧栏焦点，悬停滚动不滚。
//
// 取消拖动的那一下 Esc 本身也要认得出来：拖动库在 document 上收到 Esc 就当场结束拖动，挂在 window 上的
// （侧栏多选的 Esc）随后才收到同一个事件，只看标记会以为没在拖（测试窗口第 4 轮第 9 项：多选被清空）。所以拖动期间在
// window 的捕获阶段（最先收到）先记下这次 Esc（isDragEscape），各处的 Esc 都先问它。
const DRAG = "ccDrag";
const escapes = new WeakSet<Event>();
// 已经放手、在等落位播完（共用拖动松手后约 0.2 秒还算在拖，见 drag.tsx 的 ReleaseFirst）：这时的 Esc 不再是取消拖动的 Esc，
// 照常关浮着的会话栏、清多选（审查：原来这一下被当成取消拖动吞掉，要再按一次）
let released = false;

function markEscape(event: KeyboardEvent) {
  if (event.key === "Escape") escapes.add(event);
}

// 手机按住拿起后，手指是不是真的动了（动出共用拖动的 TOUCH_DRAG_SLOP）：没动 = 还可能是长按菜单（按住不动到菜单时间照旧出
// 菜单），动了 = 在拖，菜单不出、已经出来的收起（hooks/use-long-press.ts）。拿起、结束时清掉。
let moved = false;

export function markDragMoved(): void {
  moved = true;
}

/** 正在拖、且按住拿起后手指已经挪动了（只有手机按住拿起时记；鼠标、键盘拖动没有长按菜单）。 */
export function isDragMoving(): boolean {
  return moved && isDragging();
}

// 正在拖（逻辑上：拿起到结束拖动那一次提交）。根节点上的标记（data-cc-drag）只管样式——拖动中整页 grabbing 光标
// （globals.css 的 :root[data-cc-drag] *）——拿起那一帧不加，等这一帧画出来以后再加（markRoot）：加的那一下整页每个元素都要
// 重新算样式（无界面 Chrome 6 倍节流 15–30ms），原来与拿起那一帧拖动库整列重画叠在一起（测试窗口第 10 轮 B 段：拿起那一刻
// 长帧 160–186ms）。拖动中各处判断都问 isDragging()，不看根节点标记。
let dragging = false;
let marking: object | null = null;

/** 下一帧画出来以后给根节点加上拖动标记（这期间结束了拖动就不加）。 */
function markRoot() {
  const token = {};
  marking = token;
  const apply = () => {
    if (marking !== token) return;
    marking = null;
    if (dragging) document.documentElement.dataset[DRAG] = "";
  };
  // requestAnimationFrame 在这一帧画之前回调，里面再排一个任务：等画完
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => setTimeout(apply, 0));
  else setTimeout(apply, 0);
}

export function setDragging(on: boolean): void {
  dragging = on;
  marking = null;
  if (typeof document === "undefined") return;
  moved = false;
  released = false;
  if (on) {
    markRoot();
    window.addEventListener("keydown", markEscape, true);
  } else {
    delete document.documentElement.dataset[DRAG];
    // 这次 Esc 在捕获阶段已经记下了，现在摘掉监听不影响后面的处理
    window.removeEventListener("keydown", markEscape, true);
  }
}

/** 已经放手了，只等落位播完：之后的 Esc 不再记成取消拖动的（拖动中按下、已经记下的照旧算）。 */
export function releaseDrag(): void {
  released = true;
  if (typeof window !== "undefined") window.removeEventListener("keydown", markEscape, true);
}

export function isDragging(): boolean {
  return dragging;
}

/** 这个 Esc 是不是用来取消拖动的（拖动中按下的；拖动库处理完已经结束拖动也算）。 */
export function isDragEscape(event: Event): boolean {
  return escapes.has(event) || (isDragging() && !released);
}
