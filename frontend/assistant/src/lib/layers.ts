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
const escapes = new WeakSet<Event>();
// 已经放手、在等落位播完（共用拖动松手后约 0.2 秒还算在拖，见 drag.tsx 的 ReleaseFirst）：这时的 Esc 不再是取消拖动的 Esc，
// 照常关浮着的会话栏、清多选（审查：原来这一下被当成取消拖动吞掉，要再按一次）
let released = false;

function markEscape(event: KeyboardEvent) {
  if (event.key === "Escape") escapes.add(event);
}

// 手机按住拿起后，手指是不是真的动了（动出共用拖动的 TOUCH_DRAG_SLOP）：没动 = 还可能是长按菜单（按住不动到菜单时间照旧出
// 菜单），动了 = 在拖，菜单不出、已经出来的收起（hooks/use-long-press.ts）。拿起、结束时清掉。
//
// 动了的那一刻当场告诉正在等的长按（watchDragMoved），不由长按到点时回头来问（第 13 轮测试机：按住 0.35 秒就开始拖，菜单偶发
// 照样出来；到点的那一刻，手指到点前就已经动出去的那一下 touchmove 可能还没发给页面，问到的是旧状态）。
let moved = false;
let watcher: { owner: object; run: () => void } | null = null;

export function markDragMoved(): void {
  moved = true;
  watcher?.run();
}

/** 长按（hooks/use-long-press.ts）手指按下时挂上 run：这次按住拿起后手指真的动了（markDragMoved）就在收到那一下 touchmove 时
 *  当场调。一次只有一根手指在按，只留一个；run 为 null 摘掉 owner 自己挂的那个（别人挂的不动）。 */
export function watchDragMoved(owner: object, run: (() => void) | null): void {
  if (run) watcher = { owner, run };
  else if (watcher?.owner === owner) watcher = null;
}

/** 正在拖、且按住拿起后手指已经挪动了（只有手机按住拿起时记；鼠标、键盘拖动没有长按菜单）。 */
export function isDragMoving(): boolean {
  return moved && isDragging();
}

// 正在拖（逻辑上：拿起到结束拖动那一次提交）。鼠标拖动中盖一层透明的挡板（共用拖动在浮层那一层里建好交给这里，setDragShield）：
// 整页 grabbing 光标、侧栏与会话栏里不出悬停（浮层不接指针，指针压在相邻的行、组头、卡片上时原来会露出悬停底、「···」、折叠箭头、
// 卡片把手，第 11 轮 B14 审查）都靠它。原来是根节点上的标记（data-cc-drag）配「:root[data-cc-drag] *」，加、去的那一下整页约
// 800 个元素都要重新匹配样式（无界面 Chrome 6 倍节流 15–25ms），挡板只改它自己的 hidden（悬停从侧栏换到它上面的那一下照旧
// 要重算侧栏，6 倍节流约 12ms）。所以：只在鼠标拖动时盖（手机、键盘拖动没有指针光标、悬停）；拿起那一帧不盖，等这一帧画出来
// 以后再盖（markRoot；测试窗口第 10 轮 B 段：原来与拿起那一帧拖动库整列重画叠在一起，长帧 160–186ms）；松手（releaseDrag：
// 开始播落位、取消后起播滑回）那一帧画完就撤，不留到落定那一次整列重画里（第 11 轮 B14：落定那一帧里有一份是它）。拖动中各处
// 判断都问 isDragging()，不看挡板。
let dragging = false;
let marking: object | null = null;
let shield: HTMLElement | null = null;
let shown = false;

/** 共用拖动建好的挡板（一直留在页面上，平时 hidden）。 */
export function setDragShield(el: HTMLElement | null): void {
  if (shield && shield !== el) shield.hidden = true;
  shield = el;
  shown = false;
  if (el) el.hidden = true;
}

/** 鼠标拖动的挡板此刻盖着没有。 */
export function dragShieldShown(): boolean {
  return shown;
}

function showShield(on: boolean) {
  shown = on;
  if (shield && shield.hidden === on) shield.hidden = !on;
}

/** 下一帧画出来以后盖上（on）/ 撤掉挡板（这期间又改了主意就不做）。 */
function markRoot(on: boolean) {
  const token = {};
  marking = token;
  const apply = () => {
    if (marking !== token) return;
    marking = null;
    if (on && dragging && !released) showShield(true);
    else if (!on) showShield(false);
  };
  // requestAnimationFrame 在这一帧画之前回调，里面再排一个任务：等画完
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => setTimeout(apply, 0));
  else setTimeout(apply, 0);
}

/** 开始 / 结束拖动。cursor：要不要挡板（整页 grabbing 光标、不出悬停；鼠标拖动才要）。 */
export function setDragging(on: boolean, cursor = true): void {
  dragging = on;
  marking = null;
  if (typeof document === "undefined") return;
  moved = false;
  released = false;
  if (on) {
    if (cursor) markRoot(true);
    window.addEventListener("keydown", markEscape, true);
  } else {
    if (shown) showShield(false);
    // 这次 Esc 在捕获阶段已经记下了，现在摘掉监听不影响后面的处理
    window.removeEventListener("keydown", markEscape, true);
  }
}

/** 已经放手了，只等落位播完：之后的 Esc 不再记成取消拖动的（拖动中按下、已经记下的照旧算）；挡板在这一帧画完后撤掉，还没
 *  盖上的不再盖。"keep"：挡板先不动（Esc 后要挪回原组：那一帧与下一帧整列重挂都不再叠一次侧栏的悬停重算，第 11 轮 B14
 *  审查），等起播滑回时（ReleaseFirst.hold 再调一次不带 keep 的）那一帧画完再撤；没有滑回、当场结束的由 setDragging(false) 撤。 */
export function releaseDrag(cursor: "drop" | "keep" = "drop"): void {
  released = true;
  if (typeof window === "undefined") return;
  window.removeEventListener("keydown", markEscape, true);
  if (cursor === "keep") return;
  if (shown) markRoot(false);
  else marking = null;
}

export function isDragging(): boolean {
  return dragging;
}

/** 这个 Esc 是不是用来取消拖动的（拖动中按下的；拖动库处理完已经结束拖动也算）。 */
export function isDragEscape(event: Event): boolean {
  return escapes.has(event) || (isDragging() && !released);
}
