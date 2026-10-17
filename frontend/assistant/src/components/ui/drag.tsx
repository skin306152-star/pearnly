// 共用拖动（用户 10-15 晚：拖动要「全部参考视频，非常丝滑」；10-16：「所有拖拽的地方要改统一规则和效果」、「对话的拖动效果怎么没和
// 其它拖动效果统一呢」。参考 Claude 桌面版右侧栏拖卡片的录屏 IMG_5192）。会话栏卡片（session-panel.tsx）、侧栏公司组排序、置顶对话
// 排序、组里的对话排序与拖进已置顶（thread-list.tsx）都走这一份；修一处就在这里修。基于 dnd-kit（MIT，版本锁在 package.json）。
//
//   拿起   鼠标按住动 3px（点一下照样是点）；手机按住 250ms 且没挪出 5px 就拿起（没到时间就动 = 在滚动）；键盘 空格。能拖的把手带
//          data-drag-handle，悬停出抓手光标（globals.css 同一条），鼠标拖动中整页 grabbing。侧栏的行与组头长按另有菜单：拿起后手指
//          一动（离按下处超过 10px，同长按菜单作废的距离）就是拖动，不出菜单；一直不动按到菜单的时间（500ms，hooks/use-long-press.ts）
//          才出菜单，菜单出来后手指再动就收起菜单、接着拖（第 6 轮测试机：原来按住 250ms 后要再动才拿起、按到 500ms 就归菜单，慢慢
//          开始拖的人一直进菜单）。拿起后没动、没到菜单时间就松手是点按：浮层落回、照样打开对话 / 折叠组（动作慢的人点按常超过 250ms）。
//          浮层在抽屉、手机会话栏（50）之上、菜单（60）之下：按住不动出来的长按菜单不被拿起的那一项盖住。
//          拿起那一帧只做非做不可的（测试窗口第 11 轮 B14，A14 拿起 94–126ms）：各项的让位位移与过渡不经 React 传（useSortableItem
//          直接写节点，行、组头不重画）；播报区、浮层不挂在拖动库或整列滑动盯着的地方（body、滚动区）；不给每一项建尺寸观察器、
//          不做版面补偿的重量；整页光标与压住悬停的挡板只给鼠标、等这一帧画完再盖；整列滑动等这一帧画完再开始记位置。拖动库自己拿起时还要连着
//          重画所有能拖的项几次（激活、量拿起的那一项、开始排序、量各落点），这一层没法省，项越多越长。
//   浮起   拿起的那一项换成浮层（DragOverlay，克隆原来那一项的样子，挂在 body 上）：弹层底、弹出阴影、略透明、略放大，实时跟手。
//          原位留空（空档）。排序（一列里调先后）：其余各项拖动中就带过渡滑开让位（dnd-kit 的排序位移，过渡用动效变量）。
//          拖进另一列（侧栏对话拖进已置顶，dnd-kit 多列排序的做法）：指针进了那一列的整块落点（useDropZone）才算进去，调用方
//          （onOver）把这一项挪进那一列、排在指着的位置，原来那一列的空档合上；指针回到原来那一列的整块里就挪回去。有整块落点时
//          指针不在任何一块能放的整块里（别的公司组、两组之间）就没有落点：空档回原位，放下滑回原位（审查：原来留在此刻那一列、
//          排到本组首尾，或在已置顶里松手就置顶了）。两列之间整列各行、组头的位置变了都带同样的过渡滑过去（flowLayout，拖动中
//          关掉滚动锚定）。那一列收着（放不下空档）时整块（就是组头）高亮、原位空档留着。
//   放下   松手那一下只做一件事：浮层从放手处起播落位（Web Animations 只动 transform，交给合成线程；动效变量），落点在松手时
//          就量好（空档此刻的位置，排序时加上它让位的位移）；结束拖动（拖动库与调用方的重画：整列重排、存顺序）等落位播完
//          才做（landOverlay / ReleaseFirst；鼠标、键盘传感器包一层 landFirst）。落到收着的那一列就落到组头并收起；落点在滚动区
//          可见范围外（拖着时又滚走了）就落到可见范围的边上并收起，不飞出列表、列表不跳；没落点就滑回原位；浮层被限位停在落点上
//          （拖过首尾）也播这一段（略放大、阴影收回），同样等落定再结束；真的没挪过（拿起后原地放下、长按出菜单后松手）当场结束。
//          取消（Esc；手机的 touchcancel、转屏、切到后台）同放下一样先滑回（settleBack：这一列各项去掉让位位移、浮层滑回原位，
//          取消那次提交等滑回播完；滑回途中排序项不再写让位位移）。鼠标、手机拖进了另一列（侧栏对话拖进已置顶）以后取消：那一项
//          在那一列里重挂过，原位要等原来那一列重挂回来才量得到——Esc 那一下只按拿起时的位置起播浮层滑回（合成线程上走），这一帧
//          画完以后才请调用方把它挪回原来那一列（onReturn，同指针拖回去；整列跟着滑），重挂出来那次提交里核对落点、对不上从此刻
//          改滑，取消照样等滑回播完（homing）。已经取消、等滑回的这段时间碰撞不再算、不重量落点（cancelling）。测试窗口第 10 轮
//          B 段：原来由拖动库在 Esc 那一下同步取消、整列重画，Esc 那一帧 148 / 149ms。手机点按（拿起后没动就松手）也当场取消、
//          补点击。拿起过、按键 / 手指还没松开就取消了（Esc 等）：这次手势松开那一下不当点击（blockReleaseClick；第 11 轮重测 B 段：
//          Esc 后回到原行上松开打开了对话）。
//          第 9 轮测试机：手机拖公司组头到首尾松手卡 102 / 122ms（浮层被限位停在落点、原来不播就当场结束）；键盘公司组头 Esc
//          帧间隔约 1 秒，原因未查清（推断与证据见 docs/work/2026-10-16-cc-shell-rebuild.md 第 10 轮）。Esc、松手这一帧本身要
//          重画整列（6 倍节流约 70–90ms）是查实的，现在都挪到落定以后（那时浮层已停）。
//          第 4 轮测试机：原来拿起的就是列表里那张卡本身，放下靠自写的 translate 过渡，要等新顺序提交、整列重测之后的下一次样式
//          计算才开始，手机上卡在放手处停了约 0.4s。第 6 轮测试机：落位原来在结束拖动那次提交里起播、起点又回拨到松手前那一帧，
//          侧栏松手那一下要重画一整列（无界面 Chrome 6 倍节流实测约 100ms：结束拖动的整列重画 45ms、提交 24ms、量位置前改根节点标记
//          引起的整页样式与布局 25ms），一帧不出，帧一出来落位已走了九成，看着像瞬移。
//   范围   只在本列竖着拖：浮层上下边不出能放的范围（这一列第一项顶边到最后一项底边；能拖进的另一列的整块也算）。
//          浮层是 fixed、不撑大滚动区，拖到边上自动滚动只到列表首尾（第 4 轮：原来限位只限住指针位移，拖动库随后又把滚动差
//          加到那张卡上，卡被推出列表、撑大了可滚范围，会话栏一路滚进空白）。
//          浮层停在只能高亮组头的落点上（那一列收着）时变半透明（data-peek），透出组头的高亮，离开就恢复。
//   键盘   空格拿起、方向键移动（只在本列里，不拖进另一列）、空格（或回车）放下、Esc 取消，三种语言读屏播报（文案由调用方给）。
//          ↑ ↓ 按这一列的先后一格一格换瞄准的那一项，落点就是它，不按几何远近算（keyboardAim / keyboardCollision；第 11 轮重测 B 段：
//          第一张卡很高、下一张很矮时 ↓ 一格算回它自己，放下念「已取消移动」）。
//          Esc 只取消拖动：不关浮着的会话栏、不清侧栏多选（lib/layers.ts 的 isDragEscape）。焦点一直在被拖的那一项的把手上
//          （useFollowFocus）；拖动中方向键只归拖动（handleOf 标成已处理，组件库行自带的方向键导航不接手），放下 / 取消后焦点留在
//          移动后的那一行（之后按新顺序重挂一次也跟过去，只在焦点丢了时）。快按方向键每一下都动（测试窗口：间隔约 30ms 连按时
//          被吞掉一些。原因是拖动库的键盘传感器在目标落在滚动区下半截（往上拖时上半截）时不挪浮层、改成平滑滚动滚动区，下一下
//          按在滚动途中，从滚到一半的位置再加一格，前一下就丢了；改成不平滑地滚，每一下都从滚好的位置算，见 KEYBOARD_SCROLL）。
//   动效   时长、缓动取 globals.css 的 --cc-dur / --cc-ease；系统或设置要求减少动态效果时不播过渡。
//
// 样式：globals.css 的 .cc-drag-*、[data-drag-source]、[data-drop-target]；这里只有拖动算出的让位位移与过渡是行内样式（applyShift
// 直接写节点；整列滑动用 Web Animations，不写行内样式）。

import {
  type Active,
  type Announcements,
  type ClientRect,
  closestCenter,
  type CollisionDetection,
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DropAnimationFunction,
  type DroppableContainer,
  type DroppableContainers,
  getClientRect,
  getScrollableAncestors,
  KeyboardCode,
  type KeyboardCoordinateGetter,
  KeyboardSensor,
  type KeyboardSensorOptions,
  type Modifier,
  MouseSensor,
  type MouseSensorOptions,
  type Over,
  type Sensor,
  type SensorInstance,
  type SensorProps,
  useDndContext,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { createPortal } from "react-dom";
import {
  createContext,
  type FC,
  type HTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type TouchEvent as ReactTouchEvent,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { cssMs } from "@/lib/hover-scroll";
import { LONG_PRESS_MS, LONG_PRESS_SLOP } from "@/hooks/use-long-press";
import { isDragging, markDragMoved, releaseDrag, setDragShield, setDragging } from "@/lib/layers";

// ---------------------------------------------------------------- 拖的是什么
/** 一项拖动的说明（放在 dnd-kit 的 data.drag 里）：kind 是哪一种（card / company / pin / row / pins），label 是读屏念的名字，
 *  list 是排序的那一列（一列整块的落点 zone 是它代表的那一列），group 是侧栏所在的公司（"" = 最近），count 一起拖的条数（多选），
 *  session 对话编号；zone 是一列整块的落点（useDropZone），shut 是它此刻收着（放不下空档）。 */
export type DragData = {
  id: string;
  kind: string;
  label: string;
  list?: string;
  group?: string;
  count?: number;
  session?: string;
  zone?: boolean;
  shut?: boolean;
};

type WithData = { data: { current?: Record<string, unknown> | null } } | null | undefined;

export function dataOf(entry: WithData): DragData | undefined {
  return (entry?.data.current?.drag as DragData | undefined) ?? undefined;
}

/** 这一项此刻排在哪一列（排序项的 SortableContext 编号；拖进另一列的那一项重挂在那一列里）；不是排序项回它说明里的 list。 */
export function listOf(entry: WithData): string | undefined {
  const sortable = entry?.data.current?.sortable as { containerId?: string | number } | undefined;
  return sortable?.containerId !== undefined ? String(sortable.containerId) : dataOf(entry)?.list;
}

/** 读屏播报的文案（三种语言由调用方给）：排序时念位置，拖进另一列时念那一列的名字（over / droppedIn）。 */
export type DragTexts = {
  instructions: string;
  pickedUp: (name: string) => string;
  movedTo: (name: string, at: number, total: number) => string;
  droppedAt: (name: string, at: number, total: number) => string;
  cancelled: (name: string) => string;
  over?: (name: string, target: string) => string;
  droppedIn?: (name: string, target: string) => string;
};

function position(over: Over | Active | null | undefined): { at: number; total: number } | null {
  const sortable = over?.data.current?.sortable as { index: number; items: unknown[] } | undefined;
  return sortable ? { at: sortable.index + 1, total: sortable.items.length } : null;
}

/** dnd-kit 的读屏播报：名字用 label；排序时位置按这一列（看得见的）算；拖进另一列（拿着的那一项此刻排在别的列里，或指着那一列
 *  收着的整块）念那一列的名字（zoneLabel：一列的编号 → 它整块落点的名字）。放下按实际结果念：落在别的列里念 droppedIn；排序的
 *  念放到第几位；落回原位（经过的是它自己、还在原来那一列）或没有落点念取消，同放下的处理里动没动（onDragEnd）。
 *
 *  拿起的那一刻拖动库紧跟着报一次「经过」，经过的就是拿起的这一项自己（它在原位、离自己最近）：这一次不念，留着「已拿起」
 *  （测试窗口第 5 轮：公司组头空格拿起念成「移到第 3 位，共 4 项」，卡片、置顶同样）。挪开过以后再回到原位照常念。 */
export function dragAnnouncements(
  texts: DragTexts,
  lookup?: { lifted?: (active: Active) => DragData | undefined; zoneLabel?: (list: string) => string | undefined },
): Announcements {
  const name = (entry: WithData) => dataOf(entry)?.label ?? "";
  let moved = false; // 这一次拖动挪开过原位没有（拿起时清零）
  const say = (active: Active, over: Over | null, end: boolean) => {
    const from = lookup?.lifted?.(active) ?? dataOf(active);
    const to = dataOf(over);
    if (!over) return end ? texts.cancelled(name(active)) : undefined;
    // 进了另一列：拿着的那一项已排进那一列（经过的是它自己或那一列的某一项），或指着那一列收着的整块
    const into = to?.zone ? to.list : listOf(over);
    if (from?.list && into && into !== from.list) {
      moved = true;
      const label = lookup?.zoneLabel?.(into) ?? to?.label ?? "";
      return (end ? texts.droppedIn : texts.over)?.(name(active), label);
    }
    if (!end && over.id === active.id && !moved) return undefined;
    if (over.id !== active.id) moved = true;
    // 落回原位、落在原来那一列的整块上（不是哪一项）：没动
    if (end && (over.id === active.id || to?.zone)) return texts.cancelled(name(active));
    if (from?.list && to?.list === from.list) {
      const at = position(over);
      if (!at) return undefined;
      return (end ? texts.droppedAt : texts.movedTo)(name(active), at.at, at.total);
    }
    return (end ? texts.droppedIn : texts.over)?.(name(active), to?.label ?? "");
  };
  return {
    onDragStart: ({ active }) => {
      moved = false;
      return texts.pickedUp(name(active));
    },
    onDragOver: ({ active, over }) => say(active, over, false),
    onDragEnd: ({ active, over }) => say(active, over, true),
    onDragCancel: ({ active }) => texts.cancelled(name(active)),
  };
}

// ---------------------------------------------------------------- 手感
// 鼠标按住动 3px 才开始（点一下照样是点），手机长按 250ms 且没挪出 5px 才开始（滑动照样滚动）
export const MOUSE_START = { distance: 3 };
export const TOUCH_START = { delay: 250, tolerance: 5 };
// 手机拿起后手指离按下处动出这么多才算真的在拖（之前按住不动留给长按菜单）。同长按菜单作废的距离：「在拖」与「菜单不出」
// 同一个判断。原来是 3px（审查：拿起前允许漂 5px，拿起后再来一个 touchmove 就算在拖；iPhone 按住时手指自然抖几像素，长按菜单
// 就再也出不来——手机没有悬停，那是行、组头操作的唯一入口）
export const TOUCH_DRAG_SLOP = LONG_PRESS_SLOP;
// 空格拿起，空格 / 回车 / Tab 放下，Esc 取消（回车不拿起：对话行、组头上回车是打开 / 折叠）
const KEYS = {
  start: [KeyboardCode.Space],
  cancel: ["Escape" as KeyboardCode],
  end: [KeyboardCode.Space, KeyboardCode.Enter, KeyboardCode.Tab],
};
// 键盘拖动时目标落在滚动区下半截（往上拖时上半截），拖动库的键盘传感器不挪浮层、改成滚动滚动区让目标滚到浮层下面。默认平滑
// 滚动：下一下按在滚动途中，从滚到一半的位置再加一格，前一下就丢了（测试窗口：间隔约 30ms 连按时一些按键没反应，0.4 秒按一下
// 每下都动；无界面 Chrome 复现：40 行、从第 9 行起连按 4 下只挪 2 格）。auto：照滚动区的 CSS（各滚动区都没设平滑滚动），即
// 立即滚到位，每一下都从滚好的位置算，快按每一下都动。不用 "instant"：较晚才进规范，旧的 WebView 不认这个值时 scrollTo 会报错
export const KEYBOARD_SCROLL: ScrollBehavior = "auto";

/** 放下时先交给 beforeEnd（先播落位，播完才真正结束拖动，见 ReleaseFirst）；没给就照常立即结束。beforeCancel 同理管取消
 *  （先滑回、再取消，见 settleBack）。 */
type EndOptions = { beforeEnd?: (end: () => void) => void; beforeCancel?: (cancel: () => void) => void };
type HoldOptions = { delay: number; tolerance: number } & EndOptions;
type Listen = [EventTarget, string, EventListener, AddEventListenerOptions | undefined];

const stopClick = (event: Event) => {
  event.stopPropagation();
  event.preventDefault();
};
const clearSelection = () => window.getSelection?.()?.removeAllRanges();
const prevent = (event: Event) => event.preventDefault();

/** 手机长按拿起（dnd-kit 自带的触屏传感器到时间就拿起，但不认侧栏的长按菜单，放下也不先播落位）：按住 delay 且没挪出
 *  tolerance 就拿起（没到时间就动 = 在滚动，交还给页面）。拿起后手指离按下处动出 TOUCH_DRAG_SLOP 才算在拖：记到 lib/layers.ts
 *  （markDragMoved），长按菜单据此不出、已经出来的收起（hooks/use-long-press.ts）；按住不动到菜单时间，菜单照旧出来，
 *  拿起的那一项留着，手指再动就接着拖。拿起后滑动不再滚页面；松手先交给 beforeEnd（先播落位）。
 *  拿起后一直没动、没到菜单时间就松手：是点按（审查：动作慢的人点按常超过 250ms，原来这一下被吞掉，对话打不开、组不折叠）。
 *  取消拖动（浮层落回原位），这一下照样当点击：系统自己的 click 不要（拿起后的 touchmove 都拦了，各平台据此吞不吞 click 不一），
 *  在按下的那个元素上补一个。 */
export class HoldTouchSensor implements SensorInstance {
  autoScrollEnabled = true;

  static activators = [
    {
      eventName: "onTouchStart" as const,
      handler: ({ nativeEvent }: ReactTouchEvent) => nativeEvent.touches.length <= 1,
    },
  ];

  /** iOS Safari：页面上要有一个非被动的 touchmove 监听，后来挂上的 preventDefault 才管用（同 dnd-kit 自带的触屏传感器）。 */
  static setup() {
    const noop = () => undefined;
    window.addEventListener("touchmove", noop, { capture: false, passive: false });
    return () => window.removeEventListener("touchmove", noop);
  }

  private readonly props: SensorProps<HoldOptions>;
  private readonly origin: { x: number; y: number };
  private readonly listening: Listen[] = [];
  private readonly pressedAt = now();
  private timer = 0;
  private lifted = false;
  private moved = false;
  private done = false;

  constructor(props: SensorProps<HoldOptions>) {
    this.props = props;
    const event = props.event as TouchEvent;
    const touch = event.touches?.[0];
    this.origin = { x: touch?.clientX ?? 0, y: touch?.clientY ?? 0 };
    const target = event.target ?? document;
    this.listen(target, "touchmove", this.move, { passive: false });
    this.listen(target, "touchend", this.end);
    this.listen(target, "touchcancel", this.gone);
    this.listen(window, "resize", this.cancel);
    this.listen(document, "visibilitychange", this.cancel);
    this.listen(window, "contextmenu", prevent);
    this.listen(document, "keydown", this.key);
    this.timer = window.setTimeout(this.lift, props.options.delay);
  }

  private listen(target: EventTarget, type: string, run: EventListener, options?: AddEventListenerOptions) {
    target.addEventListener(type, run, options);
    this.listening.push([target, type, run, options]);
  }

  private readonly lift = () => {
    this.timer = 0;
    if (this.lifted || this.done) return;
    this.lifted = true;
    clearSelection();
    // 拿起后松手的那一下不算点击（不切对话、不收抽屉）；拖动中不选中文字
    this.listen(document, "click", stopClick, { capture: true });
    this.listen(document, "selectionchange", clearSelection);
    this.props.onStart(this.origin);
  };

  private readonly move = (event: Event) => {
    const touch = (event as TouchEvent).touches?.[0];
    if (!touch) return;
    const at = { x: touch.clientX, y: touch.clientY };
    const away = Math.hypot(at.x - this.origin.x, at.y - this.origin.y);
    if (!this.lifted) {
      if (away > this.props.options.tolerance) this.cancel(); // 没按够时间就动：是在滚动
      return;
    }
    if (event.cancelable) event.preventDefault(); // 已拿起：不再让页面滚
    if (!this.moved && away > TOUCH_DRAG_SLOP) {
      this.moved = true;
      markDragMoved(); // 真的在拖了：长按菜单不出、已经出来的收起
    }
    this.props.onMove(at);
  };

  private readonly key = (event: Event) => {
    if ((event as KeyboardEvent).key === "Escape") this.cancel();
  };

  private readonly end = (event: Event) => {
    if (!this.done && this.lifted && !this.moved && now() - this.pressedAt < LONG_PRESS_MS) {
      if (event?.cancelable) event.preventDefault();
      this.finish(true, true);
      return;
    }
    this.finish(false);
  };
  private readonly cancel = () => this.finish(true);
  // 系统收回了这次触摸（touchcancel）：手势已经结束，后面没有松手、也没有点击
  private readonly gone = () => this.finish(true, false, true);

  private finish(cancelled: boolean, tap = false, over = false) {
    if (this.done) return;
    this.done = true;
    window.clearTimeout(this.timer);
    const clicks = this.listening.filter(([target, type]) => target === document && (type === "click" || type === "selectionchange"));
    const off = ([target, type, run, options]: Listen) => target.removeEventListener(type, run, options);
    for (const entry of this.listening) if (tap || !clicks.includes(entry)) off(entry);
    // 松手后紧跟着的 click 还要拦下：稍后再摘（同 dnd-kit）。点按不拦（上面已经摘了），下面补一个
    if (!tap) window.setTimeout(() => clicks.forEach(off), 50);
    if (!this.lifted) this.props.onAbort(this.props.active);
    // 拿起后手指还按着就取消了（Esc、转屏、切到后台）：之后松手那一下不当点击（见 blockReleaseClick）
    if (cancelled && this.lifted && !tap && !over) blockReleaseClick("touch");
    // 拿起后取消（Esc、touchcancel、转屏、切到后台）：先滑回再取消（beforeCancel）。点按当场取消（下面补点击，不等滑回）
    if (cancelled && this.lifted && !tap && this.props.options.beforeCancel) this.props.options.beforeCancel(this.props.onCancel);
    else if (cancelled) this.props.onCancel();
    else if (this.lifted && this.props.options.beforeEnd) this.props.options.beforeEnd(this.props.onEnd);
    else this.props.onEnd();
    if (tap) clickOn(this.props.event.target, this.origin);
  }
}

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** 在按下的那个元素上补一次点击（冒泡，同系统的 click：行打开对话、组头折叠）。元素已经不在页面上就不补。 */
function clickOn(target: EventTarget | null, at: { x: number; y: number }) {
  const el = target as (EventTarget & { isConnected?: boolean }) | null;
  if (!el || el.isConnected === false || typeof el.dispatchEvent !== "function" || typeof MouseEvent === "undefined") return;
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, composed: true, view: window, detail: 1, clientX: at.x, clientY: at.y }));
}

// 松开以后接着拦点击的时长（同拖动库松手后拦点击的那 50ms：系统的 click 紧跟在 mouseup / touchend 后面）
const RELEASE_CLICK_MS = 50;

/** 拿起过的指针拖动，按键 / 手指还没松开就结束了（Esc 取消；转屏、切到后台）：这次按下到松开整个手势里拿起过，松开那一下
 *  不当点击——不打开对话、不折叠组；剩下这一段按着移动也同拖动中一样不选中文字、不起系统拖放（拖动库取消时把这两道一起摘了，
 *  按着往回移会选中经过的行字，下次按在选中的字上一动就成了系统拖文字）。一直拦到松开（mouseup / pointerup；手机 touchend /
 *  touchcancel）后 RELEASE_CLICK_MS；又按下了（下一次手势）当场不拦。测试窗口第 11 轮重测 B 段：鼠标拿起对话行拖开 25px、Esc 取消、回到原行上松开，就打开了这段对话。拖动库
 *  只把点击拦到取消后 50ms；第 11 轮 B14 起浮层不接指针、挡板在起播滑回那一帧画完就撤（releaseDrag），之后松开落在原来那一行
 *  上，按下与松开在同一行，系统照常补一次点击（在别的行上松开时点击落在两行共同的上层，不打开）。 */
export function blockReleaseClick(kind: "mouse" | "touch") {
  if (typeof document === "undefined" || typeof document.addEventListener !== "function") return;
  const doc = document;
  const capture = { capture: true };
  // 自己的一份处理函数：同一个函数、同样的捕获再挂一次，浏览器只认一份。手机长按传感器拿起时也挂了 stopClick / clearSelection，
  // 它取消后 50ms 摘掉自己那份，用同一个函数的话这里的也一起没了
  const block = (event: Event) => stopClick(event);
  const unselect = () => clearSelection();
  const noDrag = (event: Event) => prevent(event);
  const release = kind === "mouse" ? ["mouseup", "pointerup"] : ["touchend", "touchcancel"];
  let timer: ReturnType<typeof setTimeout> | null = null;
  const off = () => {
    if (timer) clearTimeout(timer);
    doc.removeEventListener("click", block, capture);
    doc.removeEventListener("selectionchange", unselect);
    doc.removeEventListener("dragstart", noDrag, capture);
    doc.removeEventListener("pointerdown", off, capture);
    for (const type of release) doc.removeEventListener(type, released, capture);
  };
  const released = () => {
    timer ??= setTimeout(off, RELEASE_CLICK_MS);
  };
  doc.addEventListener("click", block, capture);
  doc.addEventListener("selectionchange", unselect);
  doc.addEventListener("dragstart", noDrag, capture);
  doc.addEventListener("pointerdown", off, capture);
  for (const type of release) doc.addEventListener(type, released, capture);
}

/** 拖动库自带的鼠标、键盘传感器加一道：放下时先交给 options.beforeEnd（先播落位，播完才真正结束拖动）；给了
 *  options.beforeCancel 时取消也先交给它（Esc、转屏、切到后台：先滑回）。release（鼠标）：拿起后按键还没松开就取消了，松开那一下
 *  不当点击（blockReleaseClick）。拿起的条件、移动都不变；静态的 activators / setup 照样继承。 */
function landFirst<O extends object>(Base: Sensor<O>, release?: "mouse"): Sensor<O & EndOptions> {
  const Landed = class extends (Base as unknown as new (props: SensorProps<O>) => SensorInstance) {
    constructor(props: SensorProps<O & EndOptions>) {
      const { beforeEnd, beforeCancel } = props.options;
      // 拖动库的指针传感器拿起后记 activated（没拿起时按下就取消——拿起前按 Esc——照常是点击）
      let self: { activated?: boolean } | null = null;
      const cancel = () => {
        if (release && self?.activated) blockReleaseClick(release);
        if (beforeCancel) beforeCancel(props.onCancel);
        else props.onCancel();
      };
      super({
        ...props,
        ...(beforeEnd ? { onEnd: () => beforeEnd(props.onEnd) } : {}),
        ...(beforeCancel || release ? { onCancel: cancel } : {}),
      });
      self = this as unknown as { activated?: boolean };
    }
  };
  return Landed as unknown as Sensor<O & EndOptions>;
}

export const LandingMouseSensor = landFirst<MouseSensorOptions>(MouseSensor, "mouse");
export const LandingKeyboardSensor = landFirst<KeyboardSensorOptions>(KeyboardSensor);

// ---------------------------------------------------------------- 范围
/** 拿起的那一项在这一列的上下边之间（视口坐标）：y 是浮层相对拿起时的位移，card 是拿起时的位置，list 是这一列此刻的上下边。
 *  比整列还高（不会发生）时不动。 */
export function clampToList(y: number, card: { top: number; height: number }, list: { top: number; bottom: number }): number {
  const min = list.top - card.top;
  const max = list.bottom - (card.top + card.height);
  return Math.min(Math.max(y, min), Math.max(min, max));
}

/** 一项的版面位置（视口坐标）：去掉排序让位的位移（行内 transform，拖动库自己也这么量）和整列滑动的位移（flowLayout 用 Web
 *  Animations 播的 translate，拖动库不认得）。拖进另一列以后整列正在滑的那 0.2 秒里重量落点、算范围、松手量落点，原来量到的是
 *  各项滑动起点的旧位置，之后一直按旧位置算落点（审查第 11 轮 B14）。 */
export function layoutRect(node: HTMLElement): ClientRect {
  const rect = getClientRect(node, { ignoreTransform: true });
  const slid = typeof getComputedStyle === "function" ? shiftOf(node) : 0;
  return slid ? { ...rect, top: rect.top - slid, bottom: rect.bottom - slid } : rect;
}

/** 几个框合起来的上下边（不含拖动位移：排序让位时各项在动，按版面位置算才稳）。 */
function spanOf(nodes: ReadonlyArray<HTMLElement | null | undefined>): { top: number; bottom: number } | null {
  let top = Number.POSITIVE_INFINITY;
  let bottom = Number.NEGATIVE_INFINITY;
  for (const node of nodes) {
    if (!node?.isConnected) continue;
    const rect = layoutRect(node);
    top = Math.min(top, rect.top);
    bottom = Math.max(bottom, rect.top + rect.height);
  }
  return Number.isFinite(top) ? { top, bottom } : null;
}

// ---------------------------------------------------------------- 动效
type Motion = { duration: number; easing: string };

/** 动效变量（--cc-dur / --cc-ease）；系统或设置要求减少动态效果时为 null（不播过渡）。 */
function motion(): Motion | null {
  if (typeof window === "undefined" || typeof document === "undefined" || typeof getComputedStyle !== "function") return null;
  const root = document.documentElement;
  if (root.dataset.ccMotion === "reduce" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return null;
  const css = getComputedStyle(root);
  return { duration: cssMs(css.getPropertyValue("--cc-dur"), 200), easing: css.getPropertyValue("--cc-ease").trim() || "ease" };
}

const MotionContext = createContext<Motion | null>(null);

// 拖进另一列时整列跟着动（对话拖进已置顶：原来那组合上、已置顶让开，两组之间的各行、组头整体上下移）：版面一变，凡是位置
// 变了的（selector 选中的各项：侧栏的行、组头、「显示更多」）都从原来的位置带过渡滑到新位置（先记位置、变了再从差值滑回 0，
// 即 FLIP）。用 Web Animations 播 translate：不碰行内样式（排序项的 transform / transition 归 dnd-kit 与 React 管），
// 也不影响拖动库量版面（它量的是去掉 transform 的位置，与 translate 无关的各组整块）。位置按滚动区内容算，滚动不算移动；
// 正在滑的再变就从此刻看到的位置接着滑，不跳。空档（被拖的那一项）与正在落位的那一项看不见，不滑。
const STILL = "[data-drag-source], [data-drag-landing]";

/** 此刻 translate 的竖向位移（动画播到哪算到哪；没有为 0）。 */
function shiftOf(el: Element): number {
  const value = getComputedStyle(el).translate;
  if (!value || value === "none") return 0;
  const parts = value.trim().split(/\s+/);
  return parts.length > 1 ? Number.parseFloat(parts[1]!) || 0 : 0;
}

/** 盯着 root 里 selector 选中的各项，版面一变就让挪了位置的滑过去（changed：版面变过一次，拖动区据此重量各落点）；返回停止
 *  （已在滑的照常滑完）。
 *  盯着的这段时间关掉 root 的滚动锚定（overflow-anchor）：变动在可见区上方时（空档进了上面的组、多选其余各行从上面移走、
 *  失败退回上面的原组），浏览器会自动改 scrollTop 让眼前的行不动，这里却按内容坐标算，没动的行会先跳一下再滑回、下面的行
 *  直接跳（审查实测）。关掉后眼前的行照内容真实挪动、带过渡滑过去，同其他组。停止时还原。 */
export function flowLayout(root: HTMLElement, selector: string, transition: Motion | null, changed?: () => void): () => void {
  const records = new Map<unknown, { top: number; el: Element }>();
  const sliding = new Map<Element, Animation>();
  const page = root === document.scrollingElement || root === document.documentElement || root === document.body;
  const anchored = root.style.overflowAnchor;
  root.style.overflowAnchor = "none";
  const origin = () => (page ? -window.scrollY : root.getBoundingClientRect().top - root.scrollTop);
  const pass = (animate: boolean) => {
    const base = origin();
    // 先全读再全写；同一项换了位置会被卸下重挂（侧栏的行换了组），按 data-drag-id 认是同一项
    const reads = Array.from(root.querySelectorAll<HTMLElement>(selector), (el) => {
      const shift = shiftOf(el);
      return { el, key: el.dataset.dragId || el, shift, top: el.getBoundingClientRect().top - shift - base };
    });
    const seen = new Set<unknown>();
    for (const { el, key, shift, top } of reads) {
      seen.add(key);
      const before = records.get(key);
      records.set(key, { top, el });
      if (!animate || !transition || !before || Math.abs(before.top - top) < 0.5) continue;
      sliding.get(el)?.cancel();
      sliding.delete(el);
      if (el.matches(STILL)) continue;
      const dy = before.top + (before.el === el ? shift : 0) - top;
      if (Math.abs(dy) < 0.5) continue;
      // 起点不回拨：同排序项的 CSS 过渡，从这次版面变化画出来的那一帧起算。回拨到上一帧会把这次重画整列花掉的时间算进
      // 过渡里，主线程忙（手机）时让位一两帧就走完（审查：CPU 6 倍节流实测）。落位同样不回拨（第 6 轮，见 landOverlay）
      const slide = el.animate([{ translate: `0 ${dy}px` }, { translate: "0 0" }], { duration: transition.duration, easing: transition.easing });
      sliding.set(el, slide);
      slide.onfinish = () => sliding.get(el) === slide && sliding.delete(el);
    }
    for (const key of [...records.keys()]) if (!seen.has(key)) records.delete(key);
  };
  pass(false);
  const observer = new MutationObserver(() => {
    pass(true);
    changed?.();
  });
  observer.observe(root, { childList: true, subtree: true });
  return () => {
    observer.disconnect();
    root.style.overflowAnchor = anchored;
  };
}

// ---------------------------------------------------------------- 看得见的范围
/** 一项所在滚动区此刻看得见的上下边（视口坐标，与窗口取交）；root 是那个滚动区（整页滚动时为 null）。 */
export function viewOf(node: HTMLElement): { root: HTMLElement | null; top: number; bottom: number } {
  const found = getScrollableAncestors(node, 1)[0] as HTMLElement | undefined;
  const page = !found || found === document.scrollingElement || found === document.documentElement || found === document.body;
  const box = page ? { top: 0, bottom: window.innerHeight } : found.getBoundingClientRect();
  return { root: page ? null : found, top: Math.max(box.top, 0), bottom: Math.min(box.bottom, window.innerHeight) };
}

/** 框在不在看得见的范围里（差半像素不算出界）。 */
export function inView(rect: { top: number; height: number }, view: { top: number; bottom: number }): boolean {
  return rect.top >= view.top - 0.5 && rect.top + rect.height <= view.bottom + 0.5;
}

/** 把框挪进看得见的范围（上下边贴着范围的边；比范围还高时贴上边）。 */
export function clampIntoView<R extends { top: number; height: number }>(rect: R, view: { top: number; bottom: number }): R {
  const top = Math.max(view.top, Math.min(rect.top, view.bottom - rect.height));
  return top === rect.top ? rect : { ...rect, top, bottom: top + rect.height };
}

/** 放下时空档在可见范围外（拖着时又滚走了）：拖动库在落位前会对它 scrollIntoView（只看窗口，滚动区会瞬间跳一下）。
 *  这一次不让它滚（落位改为落到可见范围的边上并收起，见 Lifted）；过 wait 毫秒还原。 */
function holdStill(node: HTMLElement | null | undefined, wait: number) {
  if (!node?.isConnected || inView(node.getBoundingClientRect(), viewOf(node))) return;
  node.scrollIntoView = () => undefined;
  setTimeout(() => delete (node as Partial<HTMLElement>).scrollIntoView, wait);
}

// 落位播完停在落点（同 dnd-kit 默认），浮层随后才卸下
const STAY: FillMode = "forwards";

type Box = { top: number; left: number };
type Shape = { x: number; y: number; scaleX: number; scaleY: number };
/** 落点：into(node, shift) 回一个框 = 落到那里并收起（收着的组的组头、空档在可见范围外时可见范围的边上）；null = 落到 node
 *  自己（版面位置 + shift，shift 是排序时那一项让位的位移）。 */
export type Into = (node: HTMLElement, shift?: number) => Box | null;

/** 浮层从此刻的样子（transform）落到落点的两帧：from 是浮层此刻的框，to 是落点。 */
function landingFrames(transform: Shape, from: Box, to: Box): Keyframe[] {
  const final = { ...transform, x: transform.x - (from.left - to.left), y: transform.y - (from.top - to.top), scaleX: 1, scaleY: 1 };
  return [{ transform: CSS.Transform.toString(transform) }, { transform: CSS.Transform.toString(final) }];
}

/** 计算后的 transform（matrix / matrix3d）→ 位移与缩放（同 dnd-kit 落位时的读法）；none 回 null。 */
function shapeOf(value: string): Shape | null {
  const flat = value.startsWith("matrix3d(");
  if (!flat && !value.startsWith("matrix(")) return null;
  const n = value.slice(flat ? 9 : 7, -1).split(/,\s*/).map(Number);
  return flat ? { x: n[12]!, y: n[13]!, scaleX: n[0]!, scaleY: n[5]! } : { x: n[4]!, y: n[5]!, scaleX: n[0]!, scaleY: n[3]! };
}

/** 排序时那一项让位的竖向位移（拖动库写在行内的 translate3d；没有为 0）：松手时新顺序还没提交，那一项
 *  正由这段位移挪在新位置上，提交后它的版面位置就是这里。 */
export function sortShift(node: HTMLElement): number {
  const match = /translate(?:3d)?\(\s*(-?[\d.]+)px,\s*(-?[\d.]+)px/.exec(node.style?.transform || "");
  return match ? Number.parseFloat(match[2]!) || 0 : 0;
}

// 松手那一下已经起播的落位（浮层 → 那次动画）：拖动库随后结束拖动、要播它自己那一次时，接着等这一次播完
const early = new WeakMap<HTMLElement, Animation>();

/** 松手那一下起播落位：浮层（overlay，拖动库 DragOverlay 的外层）从此刻的样子落到 node 的落点（见 Into）。只量、只起播
 *  Web Animations（只动 transform，合成线程播；略放大、阴影由 data-dropping 收回），不改版面、不提交 React；从松手这一刻起算
 *  （fromNow）。浮层被限位停在落点上也照样播（位移为 0，略放大、阴影照样在这段时间里收回）：测试窗口第 9 轮，手机把公司组头
 *  拖过列表首尾，浮层被限位停在的正是落点，原来就不播、当场结束拖动，松手那一帧要重画整列（6 倍节流复现约 90ms，手机实测
 *  102 / 122ms），播了就同其他放下一样等落定后再结束。真的没挪过（浮层位移为 0：拿起后原地放下、长按出菜单后松手）不播，
 *  当场结束（审查：结束拖动——焦点、播报、侧栏多选变淡、手机外壳认不认边缘手势——不为它晚 0.2 秒）；always（取消先滑回）
 *  照样播。回 null = 没播（没挪过、量不到、减少动态效果），调用方立即结束拖动。 */
export function landOverlay(overlay: HTMLElement, node: HTMLElement, transition: Motion | null, into: Into, always = false): Animation | null {
  if (!transition || !overlay.isConnected || !node.isConnected) return null;
  const transform = shapeOf(getComputedStyle(overlay).transform);
  if (!transform) return null;
  // 同拖动库量浮层的那一层：只有一个子元素时量它（浮层外层的大小由它撑）
  const measurable = (overlay.children.length === 1 ? (overlay.firstElementChild as HTMLElement | null) : null) ?? overlay;
  const shift = sortShift(node);
  const fold = into(node, shift);
  const own = layoutRect(node);
  const frames = landingFrames(transform, getClientRect(measurable), fold ?? { top: own.top + shift, left: own.left });
  if (!always && !fold && !transform.x && !transform.y && frames[0]!.transform === frames[1]!.transform) return null;
  overlay.dataset.dropping = fold ? "into" : "";
  const animation = overlay.animate(frames, { duration: transition.duration, easing: transition.easing, fill: STAY });
  fromNow(animation);
  early.set(overlay, animation);
  return animation;
}

/** 浮层从此刻的样子滑到 box（视口坐标）：拖进另一列以后取消，原位还没重挂出来时按拿起时的位置先滑（见 DragArea 的 homing）。
 *  同 landOverlay：只起播 Web Animations、从这一刻起算；回 null = 没播（减少动态效果、量不到）。 */
export function slideOverlay(overlay: HTMLElement, box: Box, transition: Motion | null): Animation | null {
  if (!transition || !overlay.isConnected) return null;
  const transform = shapeOf(getComputedStyle(overlay).transform);
  if (!transform) return null;
  const measurable = (overlay.children.length === 1 ? (overlay.firstElementChild as HTMLElement | null) : null) ?? overlay;
  overlay.dataset.dropping = "";
  const animation = overlay.animate(landingFrames(transform, getClientRect(measurable), box), {
    duration: transition.duration,
    easing: transition.easing,
    fill: STAY,
  });
  fromNow(animation);
  early.set(overlay, animation);
  aims.set(animation, box.top);
  return animation;
}

/** 取消（Esc 等）同放下一样先落位：这一下只把这一列各项让位的位移去掉（各项行内带着过渡，照旧滑回原位）、浮层从此刻滑回那一项
 *  的原位（landOverlay，没挪过也播：略放大、阴影收回）；取消（拖动库与调用方的重画）等滑回播完才做。原来由拖动库在取消那次提交
 *  里滑回：那次提交要重画整列（各排序项、侧栏整列；6 倍节流约 70–90ms），Esc 那一帧不出、浮层停在原处。调用方只在那一项还在
 *  拿起时那一列里时用它（没有换列重挂，原位就是它此刻的版面位置），并在滑回途中让排序项不再写让位位移（SettleContext）。
 *  into 只管原位在可见范围外时落到边上并收起（不落进收着的组）。 */
export function settleBack(overlay: HTMLElement, node: HTMLElement, transition: Motion | null, into: Into): Animation | null {
  const list = node.dataset.dragList;
  if (list && typeof document?.querySelectorAll === "function") {
    for (const el of document.querySelectorAll<HTMLElement>("[data-drag-list]")) {
      if (el.dataset.dragList === list && el.style.transform) el.style.transform = "";
    }
  }
  return landOverlay(overlay, node, transition, into, true);
}

/** 原位的落点（取消滑回用）：在可见范围里落到它自己（null），拖着时滚走了就落到可见范围的边上并收起。 */
export const homeInto: Into = (node) => {
  const rect = layoutRect(node);
  const shown = clampIntoView(rect, viewOf(node));
  return shown !== rect ? shown : null;
};

/** 落位从松手的这一刻起算：不回拨到上一帧（主线程在松手前忙过多久就会少播多久），也不等合成线程回报开始（那样头两帧原地
 *  不动，无界面 Chrome 实测）。松手这一下很轻，下一帧照实走了多少就画多少。时间与 document.timeline 同一个起点。 */
function fromNow(animation: Animation) {
  if (typeof performance !== "undefined" && animation.startTime === null) animation.startTime = performance.now();
}

/** 播完（或被取消）时调 run，只调一次。 */
function afterLanding(animation: Animation, run: () => void) {
  void animation.finished.then(run, run);
}

/** 拖进另一列以后取消，等那一项挪回原来那一列重挂出来最多等这么久（调用方没挪、页面在后台）；到时照旧当场取消。 */
const HOME_WAIT = 600;

/** 拿起时的位置（start，视口坐标）减去之后滚过的（scrolled）：取消时原位的框，在可见范围外就落到边上（同 homeInto）。 */
function homeBox(start: ClientRect, scrolled: number, node: HTMLElement | null): Box & { height: number } {
  const rect = { top: start.top - scrolled, left: start.left, height: start.height };
  return node?.isConnected ? clampIntoView(rect, viewOf(node)) : rect;
}

// 起播的滑回要落到的竖向位置（视口坐标，slideOverlay 记下）：原位重挂出来后核对对不对得上
const aims = new WeakMap<Animation, number>();
const landingTarget = (animation: Animation) => aims.get(animation) ?? null;

/** 这一帧画出来以后再做（requestAnimationFrame 在画之前回调，里面再排一个任务）：Esc 那一帧只记下，重活放到下一帧。 */
function afterPaint(run: () => void) {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => setTimeout(run, 0));
  else setTimeout(run, 0);
}

/** 拖动编号 id 那一项此刻在 list 那一列里的节点（挪回原来那一列、重挂出来了）；还没有回 null。 */
function nodeIn(id: string | number, list: string): HTMLElement | null {
  if (typeof document?.querySelectorAll !== "function") return null;
  const key = String(id);
  return (
    Array.from(document.querySelectorAll<HTMLElement>("[data-drag-id]")).find(
      (el) => el.dataset.dragId === key && el.dataset.dragList === list && el.isConnected,
    ) ?? null
  );
}

/** 拖动编号 id 那一项此刻在页面上的节点（拖进另一列又回来时重挂过）；找不到用 fallback。 */
function landingNode(id: string | number, fallback: HTMLElement): HTMLElement {
  if (fallback.isConnected !== false || typeof document?.querySelectorAll !== "function") return fallback;
  const key = String(id);
  return Array.from(document.querySelectorAll<HTMLElement>("[data-drag-id]")).find((el) => el.dataset.dragId === key) ?? fallback;
}

/** 放下时拖动库要播的落位（结束拖动那次提交里调）。松手那一下已经起播过（landOverlay）：不再另播，等那一次播完（还没播完时
 *  落点那一项先不显示）。没起播过（取消时滑回原位、减少动态效果）：同 dnd-kit 默认那一种（浮层从此刻移到落点，Web
 *  Animations），多两件：落点可以不是空档本身（into 回一个框：收着的那一列的组头、或空档在可见范围外时可见范围的边上），这时
 *  落到那里并收起（浮层带 data-dropping="into"）。transition 为 null（减少动态效果）时不播。 */
export function dropAnimation(transition: Motion | null, into: Into): DropAnimationFunction {
  return ({ active, dragOverlay, transform }) => {
    // 落点那一项：拖进另一列以后取消（Esc），拖动库给的还是那一列里已经卸下的节点，原来那一列重挂了一份新的（审查：滑回的
    // 那 0.2 秒里同一段对话出现两份）。按拖动编号在此刻的页面里找
    const node = landingNode(active.id, active.node);
    const rect = node === active.node ? active.rect : layoutRect(node);
    // 浮层的略放大、阴影同时收回（落到收着的那一列：同时淡掉）；落点那一项在动画播完前不显示（不会一份在落点、一份在路上）
    const done = () => {
      delete node.dataset.dragLanding;
    };
    const started = early.get(dragOverlay.node);
    early.delete(dragOverlay.node);
    if (started && started.playState !== "finished") {
      node.dataset.dragLanding = "";
      return new Promise<void>((resolve) => afterLanding(started, () => (done(), resolve())));
    }
    if (started || !transition) return;
    const fold = into(node);
    const frames = landingFrames(transform, dragOverlay.rect, fold ?? rect);
    node.dataset.dragLanding = "";
    dragOverlay.node.dataset.dropping = fold ? "into" : "";
    if (frames[0]!.transform === frames[1]!.transform && !fold) {
      done();
      return;
    }
    const animation = dragOverlay.node.animate(frames, { duration: transition.duration, easing: transition.easing, fill: STAY });
    return new Promise<void>((resolve) => {
      animation.onfinish = () => {
        done();
        resolve();
      };
    });
  };
}

/** 先落位、再结束拖动（松手那一下只起播落位，结束拖动——拖动库与调用方的重画：整列重排、量位置、存顺序——等落位播完才做，
 *  主线程忙多久都不卡落位）。hold(end, animation)：animation 播完才调 end；没有 animation 立即调；过 wait 毫秒（动画没在走：
 *  页面在后台）或又按下了（鼠标、手指、键盘：要开始下一次）就当场调。等着的时候 freeze 回的还原函数在调 end 前执行（拖动库的
 *  自动滚动这段时间不滚，落点不跑）。 */
export class ReleaseFirst {
  private pending: { end: () => void; timer: ReturnType<typeof setTimeout>; off: () => void } | null = null;

  hold(end: () => void, animation: Animation | null, wait: number, freeze?: () => () => void) {
    this.flush();
    if (!animation) return end();
    // 已经放手了：这段时间的 Esc 不再算取消拖动的 Esc（审查：落位那 0.2 秒按 Esc，浮着的会话栏不关、多选不清，要再按一次）。
    // 按下 Esc 照样当场结束拖动（press），不吞这个事件
    releaseDrag();
    const unfreeze = freeze?.();
    const press = () => this.flush();
    const PRESS = ["pointerdown", "touchstart", "keydown"];
    for (const type of PRESS) window.addEventListener(type, press, true);
    const entry = {
      end,
      timer: setTimeout(() => this.pending === entry && this.flush(), wait),
      off: () => {
        for (const type of PRESS) window.removeEventListener(type, press, true);
        unfreeze?.();
      },
    };
    this.pending = entry;
    afterLanding(animation, () => this.pending === entry && this.flush());
  }

  get waiting(): boolean {
    return !!this.pending;
  }

  flush() {
    const entry = this.pending;
    if (!entry) return;
    this.pending = null;
    clearTimeout(entry.timer);
    entry.off();
    entry.end();
  }
}

/** 等着结束拖动的那一小段时间，拖动库的自动滚动（按住在列表边上时它定时 scrollBy）不滚：落点在松手时量好了，滚了就落偏。
 *  回还原函数。 */
function freezeScroll(node: HTMLElement): () => void {
  const scrollers = getScrollableAncestors(node) as HTMLElement[];
  for (const el of scrollers) el.scrollBy = () => undefined;
  return () => scrollers.forEach((el) => delete (el as Partial<HTMLElement>).scrollBy);
}

// ---------------------------------------------------------------- 拖动区
type AreaProps = {
  children: ReactNode;
  /** 拿着 active 能不能放到 target 上（排序：同一列；侧栏对话：自己那一组的行或整块、已置顶的行或整块）。 */
  accepts: (active: DragData, target: DragData) => boolean;
  /** 放下了，落位播完才调（新顺序与结束拖动同一次提交）：target 是落点（排序：落在哪一项上；拖进了另一列、停在那一列里它自己
   *  的位置上：那一列整块的落点，调用方按自己排好的位置算；落在收着的那一列整块上：那一块）。落回原位、没有落点不调。 */
  onDrop: (active: DragData, target: DragData) => unknown;
  /** 指针拖动中经过了 over（键盘拖动不调：键盘只在本列里挪）：调用方据此把拿着的那一项挪进另一列（排在 over 那里，below = 指在
   *  over 的下半截），或挪回原来那一列。 */
  onOver?: (active: DragData, over: DragData | null, below: boolean) => void;
  /** 拿起 / 结束（放下或取消）：侧栏多选时让其余选中的行变淡。 */
  onActive?: (active: DragData | null) => void;
  /** 取消（Esc 等）时拿着的那一项已排进另一列：把它挪回原来那一列（同指针拖回去；还原为拿起时的摆法）。给了才先滑回再取消，
   *  没给照旧由拖动库在取消那次提交里滑回。 */
  onReturn?: (active: DragData) => void;
  /** 指针拖动中指针的竖向位置（视口坐标；指针动了、滚动了都交，键盘拖动不交）：侧栏据此在指针拖到顶上时才临时显示空的「已置顶」。 */
  onPointer?: (active: DragData, y: number) => void;
  /** 拖进另一列时整列一起滑的各项（侧栏的行、组头、「显示更多」），见 flowLayout。 */
  flow?: string;
  texts: DragTexts;
};

/** 指针此刻指着的那一列整块落点、而它收着（组头高亮、浮层半透明、落到组头并收起）：那一块的拖动编号；别的时候 null。 */
const ShutOverContext = createContext<string | null>(null);

/** 取消后正在滑回原位（见 DragArea 的 settle）：排序项这段时间不写让位位移。 */
const SettleContext = createContext<{ readonly current: boolean }>({ current: false });

/** 键盘拿起的那一项的拖动编号（鼠标、手机拿起或没在拖时 null）：焦点跟着它（useFollowFocus）。 */
const KeyedContext = createContext<string | number | null>(null);

/** Alt+↑ / Alt+↓ 挪完的位置：这一列（看得见的）里第几项、共几项。 */
export type MoveSpot = { at: number; total: number };

/** 挪完的新顺序里 id 在第几位；没挪（到头了，新顺序为 null）或找不到回 null。 */
export function spotIn(list: readonly string[] | null | undefined, id: string): MoveSpot | null {
  const index = list ? list.indexOf(id) : -1;
  return list && index >= 0 ? { at: index + 1, total: list.length } : null;
}

/** 区域里的播报（Alt+↑/↓ 挪一格不经过拖动库，播报走这里）：念「名字 移到第几位，共几项」，文案同键盘拖动的 movedTo。 */
const SayMoveContext = createContext<((name: string, spot: MoveSpot) => void) | null>(null);

/** 点 y 在不在框的上下边之间。 */
const within = (box: ClientRect | null | undefined, y: number) => !!box && y >= box.top && y <= box.top + box.height;

/** 拿着 active 时要算的落点（碰撞、键盘、限位共用）：pool 是能放的各处（accepts，加上它自己）。键盘拖动：只认它原来那一列
 *  （不拖进另一列）。指针拖动：指针在哪一列的整块落点（zone）里，就只认那一列此刻的各项（它自己已排进去也算；那一列没有别的项、
 *  或收着时认整块）；有能放的整块而指针不在任何一块里（侧栏对话拖到别的公司组、两组之间）：没有落点（空档回原位，放下滑回
 *  原位、不交调用方。审查：原来认它此刻所在那一列，排到本组首尾存成手排，或拖进过已置顶的在别的公司上松手也置顶了）；
 *  没有整块落点（只在一列里排序：卡片、公司组、置顶）认它此刻所在那一列的各项。 */
export function narrowPool(
  pool: readonly DroppableContainer[],
  activeId: string | number,
  active: DragData,
  pointer: { keyboard: boolean; y: number | null; rects: { get: (id: string | number) => ClientRect | undefined } },
): DroppableContainer[] {
  const item = (entry: DroppableContainer) => !dataOf(entry)?.zone;
  const self = pool.find((entry) => entry.id === activeId);
  const here = (self && listOf(self)) ?? active.list;
  const inList = (list: string | undefined) => pool.filter((entry) => item(entry) && listOf(entry) === list);
  if (pointer.keyboard) return pool.filter((entry) => item(entry) && (entry.id === activeId || listOf(entry) === active.list));
  const y = pointer.y;
  const zone = y === null ? undefined : pool.find((entry) => dataOf(entry)?.zone && within(pointer.rects.get(entry.id), y));
  if (zone) {
    const data = dataOf(zone)!;
    const items = data.shut ? [] : inList(data.list);
    return items.some((entry) => entry.id !== activeId) ? items : [...items, zone];
  }
  if (pool.some((entry) => dataOf(entry)?.zone)) return [];
  return inList(here);
}

/** 键盘拖动按一下方向键瞄准的下一项：order 是这一列（看得见、能放的）各项的先后，aim 是此刻瞄着的那一项（拿起时是它自己），
 *  down = ↓。到头了（或这一列里找不到它）回 null（不动）。 */
export function keyboardAim(
  order: readonly (string | number)[],
  activeId: string | number,
  aim: string | number | null,
  down: boolean,
): string | number | null {
  const from = order.indexOf(aim ?? activeId);
  const at = from >= 0 ? from : order.indexOf(activeId);
  if (at < 0) return null;
  const next = at + (down ? 1 : -1);
  return next >= 0 && next < order.length ? order[next]! : null;
}

/** 键盘拖动把拿着的那一项挪到 target 的位置时浮层的上边（同排序让位以后它落在那里的位置）：往后挪是它的底边对齐 target 的底边，
 *  往前挪是它的顶边对齐 target 的顶边，回到原位是它自己的版面位置。height 是浮层的高。 */
export function keyboardSlot(
  order: readonly (string | number)[],
  activeId: string | number,
  target: string | number,
  rectOf: (id: string | number) => ClientRect | null | undefined,
  height: number,
): number | null {
  const rect = rectOf(target);
  if (!rect) return null;
  const from = order.indexOf(activeId);
  const to = order.indexOf(target);
  return to > from ? rect.top + rect.height - height : rect.top;
}

/** 键盘拖动的落点就是方向键瞄着的那一项（不在能放的各处里就是它自己）：不按几何远近算。测试窗口第 11 轮重测 B 段：会话栏第一张
 *  卡「公司」展开着很高，下一张收着很矮，空格拿起按 ↓，浮层按拖动库的算法挪到「底边对齐下一张」，它的中心离原位（自己）比离
 *  下一张还近（高差的一半大于下一张高加间距），最近中心算出来还是它自己，放下念「已取消移动」、顺序不变。 */
export function keyboardCollision(pool: readonly DroppableContainer[], activeId: string | number, aim: string | number | null) {
  const hit = pool.find((entry) => entry.id === (aim ?? activeId)) ?? pool.find((entry) => entry.id === activeId);
  return hit ? [{ id: hit.id, data: { droppableContainer: hit, value: 0 } }] : [];
}

// 量能放的各处按版面位置（layoutRect：整列滑动途中重量也不量到滑动起点）
const MEASURING = { droppable: { measure: layoutRect } };

// 自动滚动照拖动库默认；不做「拿起后版面挪了就滚回去」的补偿：拿起的那一项原位留着同样大小的空档（浮层在 body 上），拿起不会挪动
// 版面，补偿只是在拿起那次提交里多量一次那一项（刚改过页面，要当场把整页样式、布局算一遍；第 11 轮 B14）
const AUTO_SCROLL = { layoutShiftCompensation: false } as const;

/** 一块共用拖动的区域：传感器、碰撞（只认能放的地方）、键盘移动、限位、浮层与放下动画、读屏播报、拖进另一列时整列滑动。 */
export const DragArea: FC<AreaProps> = ({ children, accepts, onDrop, onOver, onActive, onReturn, onPointer, flow, texts }) => {
  const latest = useRef({ accepts, onDrop, onOver, onActive, onReturn, onPointer });
  latest.current = { accepts, onDrop, onOver, onActive, onReturn, onPointer };
  const transition = useMemo(motion, []);

  // 拖动中要用的：拿起时的位置（浮层固定在那里，位移从它算）、能放的各处与原来那一项的节点（限位用）
  const first = useRef<ClientRect | null>(null);
  // 拿起时那一项所在滚动区的滚动位置（拖进另一列以后取消：原位 = 拿起时的位置减去这之后滚过的）
  const liftScroll = useRef<{ el: HTMLElement | null; top: number } | null>(null);
  const stash = useRef<Stash>({ containers: null, node: null, overlay: null, active: null });
  // 取消后滑回原位的那 0.2 秒：排序项不再写让位位移（settleBack 已在页面上去掉了，这段时间里别的原因重画一次——实时推送、
  // 列表同步增删了行——也不写回去，React 与页面一致）。不是状态（滑回那一下不重画整列），重画时读它
  const settle = useRef(false);
  // 已经取消、在等滑回（拖动库这时还在拖）：碰撞不再算（不经过、不写让位、不再把那一项排进别的列），指针的位置不再交，不重量落点
  const cancelling = useRef(false);
  // 拖进另一列以后取消、等那一项挪回原来那一列重挂出来（见文件头「取消」）：要等的那一项、它原来那一列、真正的取消；等不到
  // （调用方没挪、页面在后台）到时照旧当场取消
  const homing = useRef<{
    id: string | number;
    list: string;
    cancel: () => void;
    timer: ReturnType<typeof setTimeout>;
    /** Esc 那一下按拿起时的位置先起播的滑回（原位重挂出来后对得上就接着用，对不上从此刻改滑到真正的原位）。 */
    slide: Animation | null;
    unfreeze: () => void;
  } | null>(null);

  const [keyed, setKeyed] = useState<string | number | null>(null);
  const keyedRef = useRef(false);
  // 键盘拖动此刻瞄着的那一项（拿起时是它自己，方向键一格一格换；落点就是它）
  const keyAim = useRef<string | number | null>(null);
  // 此刻指着的落点（落位要看它是不是收着的那一列整块）；指着的收着的整块（高亮组头）
  const overRef = useRef<DragData | null>(null);
  const [shutOver, setShutOver] = useState<string | null>(null);
  // 拿起时的那份说明：拖进另一列的那一项会在那一列里重挂，拖动中一直按拿起时的认（原来那一列不变）
  const lifted = useRef<DragData | null>(null);
  const activeOf = (active: WithData & { id: string | number }) =>
    active && lifted.current?.id === active.id ? lifted.current : dataOf(active);
  const letGo = useMemo(() => new ReleaseFirst(), []);
  // 指针此刻的竖向位置（碰撞每算一次记一次；键盘拖动不记），PointerWatch 交给调用方。只在拿起到结束之间交：结束拖动那次提交里
  // 拖动库清掉落点，PointerWatch 随后还会跑一次，原来照样把松手 / Esc 时停着的位置交出去，侧栏据此又临时出一个空的「已置顶」，
  // 一直留着（测试窗口第 11 轮：拖进已置顶后按 Esc，滑回原位以后侧栏还留着空的「已置顶」组头约 1.6 秒）
  const pointerY = useRef<number | null>(null);
  const live = useRef(false);
  const reportPointer = useMemo(
    () => () => {
      const data = lifted.current;
      const y = pointerY.current;
      if (live.current && data && y !== null && !keyedRef.current && !cancelling.current) latest.current.onPointer?.(data, y);
    },
    [],
  );

  const allowed = (active: DragData, entry: DroppableContainer, activeId: string | number) => {
    const target = dataOf(entry);
    if (!target) return false;
    if (entry.id === activeId) return !!active.list;
    return latest.current.accepts(active, target);
  };

  const collisions = useMemo<CollisionDetection>(
    () => (args) => {
      const active = activeOf(args.active);
      if (!active?.list || cancelling.current) return [];
      const rect = args.collisionRect;
      pointerY.current = keyedRef.current ? null : (args.pointerCoordinates?.y ?? null);
      const pool = narrowPool(
        args.droppableContainers.filter((entry) => allowed(active, entry, args.active.id)),
        args.active.id,
        active,
        { keyboard: keyedRef.current, y: args.pointerCoordinates?.y ?? rect.top + rect.height / 2, rects: args.droppableRects },
      );
      // 键盘：方向键瞄着哪一项就是哪一项（各项高矮不一时按远近会算回它自己，见 keyboardCollision）
      if (keyedRef.current) return keyboardCollision(pool, args.active.id, keyAim.current);
      return closestCenter({ ...args, droppableContainers: pool });
    },
    [],
  );

  // 键盘：↑ ↓ 在本列里一格一格挪（不拖进另一列）。瞄准按这一列的先后（keyboardAim），浮层挪到挪过去以后它在的位置
  // （keyboardSlot，同拖动库 sortableKeyboardCoordinates 的对齐法），落点就是瞄着的那一项（上面 collisions）。原来瞄准与落点都按
  // 几何远近算，第一张卡很高、下一张很矮时 ↓ 一格算回它自己（测试窗口第 11 轮重测 B 段，见 keyboardCollision）
  const coordinates = useMemo<KeyboardCoordinateGetter>(
    () => (event, args) => {
      const { context } = args;
      const active = context.active ? activeOf(context.active) : undefined;
      if (!active?.list || !context.active) return undefined;
      const down = event.code === KeyboardCode.Down;
      if (!down && event.code !== KeyboardCode.Up) {
        if (event.code === KeyboardCode.Left || event.code === KeyboardCode.Right) event.preventDefault();
        return undefined;
      }
      event.preventDefault();
      const activeId = context.active.id;
      const rect = context.collisionRect;
      if (!rect) return undefined;
      const enabled = new Set(
        context.droppableContainers
          .getEnabled()
          .filter((entry) => !dataOf(entry)?.zone && allowed(active, entry, activeId) && listOf(entry) === active.list)
          .map((entry) => entry.id),
      );
      const own = context.droppableContainers.get(activeId)?.data.current?.sortable as { items?: (string | number)[] } | undefined;
      const order = (own?.items ?? []).filter((id) => id === activeId || enabled.has(id));
      const target = keyboardAim(order, activeId, keyAim.current, down);
      if (target === null) return undefined;
      const y = keyboardSlot(order, activeId, target, (id) => context.droppableRects.get(id), rect.height);
      if (y === null) return undefined;
      keyAim.current = target;
      return { x: rect.left, y };
    },
    [],
  );

  // 只能竖着拖，浮层上下边不出能放的范围（这一列各项；能拖进的另一列的整块也算；键盘只在这一列）
  const keep = useMemo<Modifier>(
    () => ({ transform, active, activeNodeRect }) => {
      if (!active) {
        first.current = null;
        return transform;
      }
      if (!first.current && activeNodeRect) first.current = activeNodeRect;
      const start = first.current;
      const data = activeOf(active);
      const { containers, node } = stash.current;
      if (!start || !data || !containers) return { ...transform, x: 0 };
      const nodes = containers
        .getEnabled()
        .filter((entry) => allowed(data, entry, active.id) && (!keyedRef.current || (!dataOf(entry)?.zone && listOf(entry) === data.list)))
        .map((entry) => entry.node.current);
      // 原位也算在范围里（拖进另一列时原来那一列也是浮层出发的地方）；节点还没拿到的第一帧用拿起时的位置
      const span = spanOf([...nodes, node]);
      const own = node?.isConnected ? null : { top: start.top, bottom: start.top + start.height };
      if (!span && !own) return { ...transform, x: 0 };
      const list = { top: Math.min(span?.top ?? Infinity, own?.top ?? Infinity), bottom: Math.max(span?.bottom ?? -Infinity, own?.bottom ?? -Infinity) };
      return { ...transform, x: 0, y: clampToList(transform.y, { top: start.top, height: start.height }, list) };
    },
    [],
  );
  const modifiers = useMemo(() => [keep], [keep]);

  // 落点（放下时量）：指着的是收着的那一列整块（拿着的那一项不在里面）就落到那一块（组头）并收起；落点在滚动区可见范围外
  // （拖着时又滚走了）就落到可见范围的边上并收起，不飞出列表。shift：排序时那一项让位的位移（松手时新顺序还没提交）
  const into = useMemo<Into>(
    () => (node, shift = 0) => {
      const target = overRef.current;
      const zone = target?.zone && target.shut ? stash.current.containers?.get(target.id)?.node.current : null;
      const fold = !!zone && !zone.contains(node);
      const base = layoutRect(fold ? zone! : node);
      const rect = fold || !shift ? base : { ...base, top: base.top + shift, bottom: base.bottom + shift };
      const shown = clampIntoView(rect, viewOf(node));
      return fold || shown !== rect ? shown : null;
    },
    [],
  );
  const landWait = (transition?.duration ?? 0) + 150;
  // 放下：松手那一下只起播落位（landOverlay），结束拖动等落位播完（ReleaseFirst）。三种传感器（鼠标、手机、键盘）同一道
  const beforeEnd = useMemo(
    () => (end: () => void) => {
      const { node, overlay } = stash.current;
      const box = overlay?.current;
      const animation = node && box ? landOverlay(box, node, transition, into) : null;
      letGo.hold(end, animation, landWait, node ? () => freezeScroll(node) : undefined);
    },
    [],
  );
  // 取消（Esc；手机还有 touchcancel、转屏、切到后台）：先滑回原位，滑回播完才取消（settleBack）。三种传感器同一道。那一项已不在
  // 拿起时那一列里（指针把侧栏对话拖进了已置顶，它在那里重挂过）：原位要等原来那一列重挂回来才量得到——这一下只记下，画完这一帧
  // 再请调用方挪回去（onReturn），重挂出来时（下面的 useLayoutEffect）再滑回；调用方不管挪回去的，照旧由拖动库在取消那次提交里滑回
  const beforeCancel = useMemo(
    () => (cancel: () => void) => {
      const { node, overlay, active } = stash.current;
      const box = overlay?.current;
      const from = lifted.current;
      const home = !!node?.isConnected && !!from?.list && listOf(active) === from.list;
      cancelling.current = true;
      if (!home && from?.list && active && box && transition && latest.current.onReturn) {
        settle.current = true;
        // 这一下只起播浮层滑回（拿起时的位置减去之后滚过的；只动 transform，下一帧主线程再忙也照样走），停住自动滚动
        const start = first.current;
        const scroll = liftScroll.current;
        const scrolled = scroll?.el ? scroll.el.scrollTop - scroll.top : 0;
        const slide = start ? slideOverlay(box, homeBox(start, scrolled, node), transition) : null;
        // 已经取消了：之后的 Esc 照常关浮着的会话栏、清多选（同放下后等落位）。鼠标拖动的挡板这时不撤（"keep"）：撤掉那一下指针
        // 下面的侧栏要换悬停、整块重算样式（原来去根节点标记更重，整页 838 个元素），放在 Esc 这一帧（下面 freezeScroll 读计算样式会
        // 当场逼出来）或下一帧（挪回原组、整列重挂）都压在长帧上（第 11 轮 B14 审查）；起播滑回时 homeLanded 的 letGo.hold 再放一次手，
        // 那一帧画完再撤，滑回只动 transform 不受它卡
        releaseDrag("keep");
        const entry = {
          id: active.id,
          list: from.list,
          cancel,
          timer: setTimeout(() => homeLanded(entry, null), HOME_WAIT),
          slide,
          unfreeze: node ? freezeScroll(node) : () => undefined,
        };
        homing.current = entry;
        afterPaint(() => homing.current === entry && latest.current.onReturn?.(from));
        return;
      }
      settle.current = home;
      const animation = home && box ? settleBack(box, node!, transition, homeInto) : null;
      if (!animation) settle.current = false;
      letGo.hold(cancel, animation, landWait, node ? () => freezeScroll(node) : undefined);
    },
    [],
  );
  // 挪回原来那一列的那一项重挂出来了（或等不到）：从此刻滑回原位，滑回播完才取消（没挪回来就当场取消，同原来）
  const homeLanded = (entry: NonNullable<typeof homing.current>, node: HTMLElement | null) => {
    if (homing.current !== entry) return;
    homing.current = null;
    clearTimeout(entry.timer);
    const box = stash.current.overlay?.current;
    let animation = entry.slide;
    if (node && box) {
      // 原位与 Esc 时按拿起位置算的对不上（拖着时列表增减了行）：从此刻（滑到一半的样子）改滑到真正的原位
      const target = homeInto(node) ?? layoutRect(node);
      const aimed = entry.slide ? landingTarget(entry.slide) : null;
      if (!aimed || Math.abs(aimed - target.top) > 1) {
        const next = settleBack(box, node, transition, homeInto);
        entry.slide?.cancel();
        animation = next;
      }
    }
    if (!animation) settle.current = false;
    letGo.hold(entry.cancel, animation, landWait, () => entry.unfreeze);
  };
  // 每次重画后看一眼：等着挪回去的那一项已经在原来那一列里重挂出来了，就在这次提交里起播滑回（量的是挪回去以后的版面）
  useLayoutEffect(() => {
    const entry = homing.current;
    if (!entry) return;
    const node = nodeIn(entry.id, entry.list);
    if (node) homeLanded(entry, node);
  });
  // 传感器的选项记住：拖动库按 [传感器, 选项] 记 useSensor，行内对象字面量每次重画都是新的一份，传感器、把手的事件处理跟着换，
  // 所有把手（行、组头）的记住都失效（审查：键盘拿起、拖进置顶、挪回原组时 DragArea 重画，整列行与组头跟着重画）
  const sensorOptions = useMemo(
    () => ({
      mouse: { activationConstraint: MOUSE_START, beforeEnd, beforeCancel },
      touch: { ...TOUCH_START, beforeEnd, beforeCancel },
      keyboard: { coordinateGetter: coordinates, keyboardCodes: KEYS, scrollBehavior: KEYBOARD_SCROLL, beforeEnd, beforeCancel },
    }),
    [],
  );
  const sensors = useSensors(
    useSensor(LandingMouseSensor, sensorOptions.mouse),
    useSensor(HoldTouchSensor, sensorOptions.touch),
    useSensor(LandingKeyboardSensor, sensorOptions.keyboard),
  );
  // Alt+↑/↓ 挪一格的播报（第 5 轮：公司组头挪动时读屏什么也没念）：同拖动库的播报区一样是 assertive、整段念；
  // 同一句连着两次（极少）末尾补一个不占位的空格，读屏才会再念
  const [said, setSaid] = useState("");
  const sayMove = useMemo(
    () => (name: string, spot: MoveSpot) => {
      const text = texts.movedTo(name, spot.at, spot.total);
      setSaid((before) => (before === text ? `${text} ` : text));
    },
    [texts],
  );
  // 一列的编号 → 它整块落点的名字（读屏念「将放进已置顶」）
  const zoneOf = (list: string) =>
    stash.current.containers?.getEnabled().map((entry) => dataOf(entry)).find((data) => !!data?.zone && data.list === list);
  // 播报区挂到 body 上（拖动库默认就地挂在拖动区里，也就是侧栏、会话栏的滚动区里）：每念一句（拿起、每经过一项、放下）都是
  // 滚动区里的一次增删，整列滑动（flowLayout）据此重量所有行、拖动库重量所有落点并重画所有能拖的项，侧栏键盘也重排一遍
  // （测试窗口第 11 轮 B14：拿起后连着两三帧 60–100ms 里有一份是这个）
  const accessibility = useMemo(
    () => ({
      announcements: dragAnnouncements(texts, { lifted: (active) => activeOf(active), zoneLabel: (list) => zoneOf(list)?.label }),
      screenReaderInstructions: { draggable: texts.instructions },
      container: typeof document !== "undefined" && document.body ? document.body : undefined,
    }),
    [texts],
  );

  useEffect(
    () => () => {
      const entry = homing.current;
      homing.current = null;
      if (entry) {
        clearTimeout(entry.timer);
        entry.unfreeze();
        entry.cancel(); // 卸下时还在等挪回去的取消当场结束
      }
      letGo.flush(); // 卸下时还在等落位的放下当场结束
      setDragging(false);
    },
    [letGo],
  );
  const finish = () => {
    live.current = false;
    pointerY.current = null;
    settle.current = false;
    cancelling.current = false;
    setKeyed(null);
    keyedRef.current = false;
    keyAim.current = null;
    overRef.current = null;
    setShutOver(null);
    setDragging(false);
    latest.current.onActive?.(null);
  };
  const onDragEnd = ({ active, over, activatorEvent }: DragEndEvent) => {
    const from = activeOf(active);
    // 键盘放下：焦点留在移动后的那一行（键盘不拖进另一列，不会落进收着的整块）
    keepFocusOn(active.id, activatorEvent);
    // 先量再结束拖动（撤挡板、清标记：反过来量的时候样式、布局要重算一遍，第 6 轮 6 倍节流实测 25ms）
    holdStill(stash.current.node, landWait);
    finish();
    const to = dataOf(over);
    if (!from || !to || !over) return;
    if (over.id === active.id) {
      // 停在自己的位置上：还在原来那一列 = 没动；已排进另一列 = 落在那一列的这个位置（交那一列的整块，调用方按自己排好的算）
      const into = listOf(over);
      const zone = into && into !== from.list ? zoneOf(into) : undefined;
      if (zone) latest.current.onDrop(from, zone);
      return;
    }
    if (!latest.current.accepts(from, to)) return;
    latest.current.onDrop(from, to);
  };
  return (
    <MotionContext.Provider value={transition}>
      <SettleContext.Provider value={settle}>
        <SayMoveContext.Provider value={sayMove}>
          <ShutOverContext.Provider value={shutOver}>
            <KeyedContext.Provider value={keyed}>
              <DndContext
                sensors={sensors}
                collisionDetection={collisions}
                modifiers={modifiers}
                accessibility={accessibility}
                autoScroll={AUTO_SCROLL}
                measuring={MEASURING}
                onDragStart={({ active, activatorEvent }) => {
                  live.current = true;
                  settle.current = false;
                  cancelling.current = false;
                  keepFocusOn(active.id, null);
                  const keyboard = byKeyboard(activatorEvent);
                  keyedRef.current = keyboard;
                  keyAim.current = active.id;
                  setKeyed(keyboard ? active.id : null);
                  setDragging(true, byMouse(activatorEvent));
                  const data = dataOf(active);
                  lifted.current = data ?? null;
                  const own = data?.list ? nodeIn(active.id, data.list) : null;
                  const scroller = own ? (getScrollableAncestors(own, 1)[0] as HTMLElement | undefined) : undefined;
                  liftScroll.current = { el: scroller ?? null, top: scroller?.scrollTop ?? 0 };
                  overRef.current = null;
                  pointerY.current = null;
                  if (data) latest.current.onActive?.(data);
                }}
                onDragOver={({ active, over }) => {
                  if (cancelling.current) return; // 已经取消、在等滑回：不再经过哪里
                  const from = activeOf(active);
                  const to = dataOf(over) ?? null;
                  overRef.current = to;
                  setShutOver(to?.zone && to.shut && over ? String(over.id) : null);
                  // 经过的是它自己（拿起时、排进另一列重挂以后）不交：它自己的说明还是原来那一列的，交了调用方会当成回到原来那一列
                  if (!from || keyedRef.current || over?.id === active.id) return;
                  // 指在 over 的下半截（拖进另一列时排在它后面）：按浮层此刻的框算
                  const box = active.rect.current.translated;
                  const below = !!box && !!over && box.top + box.height / 2 > over.rect.top + over.rect.height / 2;
                  latest.current.onOver?.(from, to, below);
                }}
                onDragCancel={({ active, activatorEvent }) => {
                  keepFocusOn(active.id, activatorEvent);
                  holdStill(stash.current.node, landWait);
                  finish();
                }}
                onDragEnd={onDragEnd}
              >
                <FlowAreaContext.Provider value={!!flow}>{children}</FlowAreaContext.Provider>
                <Lifted stash={stash} transition={transition} into={into} />
                {flow && <Flow selector={flow} transition={transition} paused={cancelling} />}
                {onPointer && <PointerWatch report={reportPointer} />}
              </DndContext>
            </KeyedContext.Provider>
            <MoveLive text={said} />
          </ShutOverContext.Provider>
        </SayMoveContext.Provider>
      </SettleContext.Provider>
    </MotionContext.Provider>
  );
};

/** Alt+↑/↓ 挪一格的播报区（挂在 body 上，同拖动库自己的播报区；看不见、不占位）。 */
const MoveLive: FC<{ text: string }> = ({ text }) => {
  const region = (
    <div role="status" aria-live="assertive" aria-atomic="true" className="sr-only" data-slot="cc-move-live">
      {text}
    </div>
  );
  return typeof document !== "undefined" && document.body ? createPortal(region, document.body) : region;
};

/** 拖动中（拿起到结束那一次提交）：版面一变（拖进另一列、挪回原来那一列）整列跟着动（flowLayout），并重量能放的各处（碰撞按新
 *  位置算；拖动库只重量变了的那几列，中间各组的位置也变了）。结束拖动那一次提交里停（同步停在布局阶段：放下后的整列重排是排序
 *  让位已经摆好的位置，不能再滑一遍）。 */
const Flow: FC<{ selector: string; transition: Motion | null; paused: { readonly current: boolean } }> = ({ selector, transition, paused }) => {
  const { active, activeNode, droppableContainers, measureDroppableContainers } = useDndContext();
  const node = useRef<HTMLElement | null>(null);
  if (activeNode) node.current = activeNode;
  const latest = useRef({ droppableContainers, measureDroppableContainers });
  latest.current = { droppableContainers, measureDroppableContainers };
  const running = !!active;
  useLayoutEffect(() => {
    if (!running) return;
    // 拿起那一帧画完以后才开始盯（先记下各项的位置要读整列的版面：拿起那次提交刚改过页面，在这里读就要当场把整页样式、布局算
    // 一遍，第 11 轮 B14 拿起那一帧里有一份是它）。版面最早要等指针拖进另一列才变，拿起后一帧之内不会有
    let stop: (() => void) | null = null;
    let live = true;
    afterPaint(() => {
      const from = node.current;
      if (!live || !from?.isConnected) return;
      const root = (getScrollableAncestors(from, 1)[0] as HTMLElement | undefined) ?? (document.scrollingElement as HTMLElement | null) ?? document.body;
      // 版面变了的那一帧画完以后再重量（几次变动合成一次）：重量一次拖动库要连着重画所有能拖的项两遍，原来叠在拖进另一列、
      // 挪回原组那一帧里（那一帧已有调用方的整列重画、拖动库重挂那一项的重量；第 11 轮 B14）。量的是版面位置（layoutRect），
      // 整列还在滑也量得准
      let queued = false;
      const remeasure = () => {
        // 已经取消、在等滑回：不再找落点，不重量（重量一次拖动库要把所有能拖的项再重画一遍）
        if (paused.current || queued) return;
        queued = true;
        afterPaint(() => {
          queued = false;
          if (!live || paused.current) return;
          const { droppableContainers: all, measureDroppableContainers: measure } = latest.current;
          measure(all.getEnabled().map((entry) => entry.id));
        });
      };
      stop = flowLayout(root, selector, transition, remeasure);
    });
    return () => {
      live = false;
      stop?.();
    };
  }, [running, selector, transition]);
  return null;
};

/** 指针拖动中拖动库每重算一次落点（指针动了、拖动中滚动了）就交一次指针的位置（onPointer）。拖动库算落点在它自己重画时，
 *  算完的落点随公开的拖动状态（collisions）变，这里跟着它在提交后交。 */
const PointerWatch: FC<{ report: () => void }> = ({ report }) => {
  const { collisions } = useDndContext();
  useEffect(report, [collisions, report]);
  return null;
};

/** 排序的一列（同一个 DragArea 里可以有几列：侧栏的置顶、公司组）。items 按内容记住：调用方每次重画都算出一份新数组，拖动库
 *  据此认为这一列变了、这一列每一项都跟着重画（第 11 轮 B14：拖进已置顶、Esc 挪回原组、落定时整列行全部重画）。 */
export const DragList: FC<{ id: string; items: string[]; children: ReactNode }> = ({ id, items, children }) => {
  const key = items.join("\u0000");
  const same = useMemo(() => items, [key]);
  return (
    <SortableContext id={id} items={same} strategy={verticalListSortingStrategy}>
      {children}
    </SortableContext>
  );
};

// ---------------------------------------------------------------- 浮层
// 克隆出来的那一项只是样子：去掉会被别处按属性找到的标记（会话栏按 data-card 滚到卡片、侧栏按 data-row-id / data-roving
// 排键盘焦点与多选），整块 inert（不进 Tab、读屏不念）
const STRIP = [
  "id",
  "data-card",
  "data-row-id",
  "data-drag-id",
  "data-roving",
  "data-drag-list",
  "data-drag-handle",
  "data-drag-source",
  "data-drag-landing",
  "data-drop-target",
  "data-selected",
  "data-marquee",
  "aria-current",
  "aria-describedby",
  "aria-roledescription",
];

// 浮层挂在 body 上（不在会话栏、侧栏里：它们是滚动区，又可能因为动画、变换成了 fixed 的定位基准，浮层会跟着滚走——
// 第 5 轮实测会话栏滚到底时浮层被带出了可视区）。挂到 body 上就离开了原来的祖先：靠祖先生效的样式（公司组里对话行的缩进变量
// --cc-row-indent 定义在 [data-slot="cc-project-items"] 上，手机抽屉的行、组头规则挂在 .cc-drawer 下，字体字色从祖先继承）
// 都会丢，拿起时文字横跳、变色（第 5 轮审查）。所以克隆外面再套一层层祖先的空壳（只抄元素本身、不带兄弟，display: contents
// 不占版面）：后代选择器、变量与继承都同原来一样，看起来就是原来那一项。
function strip(el: Element) {
  for (const name of STRIP) el.removeAttribute(name);
}

export function cloneOf(node: HTMLElement): HTMLElement {
  const copy = node.cloneNode(true) as HTMLElement;
  for (const el of [copy, ...copy.querySelectorAll<HTMLElement>("*")]) strip(el);
  for (const name of ["transform", "transition", "opacity"]) copy.style.removeProperty(name);
  // 外边距不要（公司组类上的组上间距也盖掉）：浮层的高度是原来那一项的框高，带着外边距整块会下移、底部被裁
  copy.style.setProperty("margin", "0");
  let shell: HTMLElement = copy;
  for (let up = node.parentElement; up && up !== document.body && up !== document.documentElement; up = up.parentElement) {
    const wrap = up.cloneNode(false) as HTMLElement;
    strip(wrap);
    // 外壳不算一层弹层（浮着的会话栏、抽屉是对话框时，按 role 数开着的层——返回键、Esc——不能多数一份）
    for (const name of ["role", "aria-modal"]) wrap.removeAttribute(name);
    wrap.style.setProperty("display", "contents", "important");
    wrap.appendChild(shell);
    shell = wrap;
  }
  return shell;
}

// 浮层里的样子在拿起时克隆一次（拖进另一列时那一项会在那里重挂，不再重克隆）
const Copy: FC<{ id: string | number; node: HTMLElement | null }> = ({ id, node }) => {
  const box = useRef<HTMLDivElement>(null);
  const cloned = useRef<string | number | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || !node || cloned.current === id) return;
    cloned.current = id;
    el.replaceChildren(cloneOf(node));
  }, [id, node]);
  return <div ref={box} className="cc-drag-copy" />;
};

// 浮层的层级：在抽屉、手机会话栏（50）之上，在菜单（.cc-menu 60）之下。拖动库默认 999，手机按住不动拿起后到 500ms 弹出的
// 长按菜单（行、公司组头）落在拿起的那一项范围里时被浮层盖住、字叠在一起（审查）
export const OVERLAY_Z = 55;

type Stash = {
  containers: DroppableContainers | null;
  node: HTMLElement | null;
  overlay: { current: HTMLElement | null } | null;
  /** 拖着的那一项（它此刻在哪一列：listOf）。 */
  active: Active | null;
};

const Lifted: FC<{ stash: { current: Stash }; transition: Motion | null; into: Into }> = ({ stash, transition, into }) => {
  const { active, activeNode, droppableContainers, dragOverlay } = useDndContext();
  stash.current = { containers: droppableContainers, node: activeNode, overlay: dragOverlay.nodeRef, active };
  const data = dataOf(active);
  // 浮层停在只能高亮组头的落点上（那一列收着、放不下空档：组头带 data-drop-target）：浮层变半透明，透出组头的高亮，离开就恢复
  // （测试窗口第 7 轮：手机上浮层正好盖住收着的组头，看不到高亮）。高亮与指着它同一次提交，提交后看一眼
  const shutOver = useContext(ShutOverContext);
  const [peek, setPeek] = useState(false);
  useLayoutEffect(() => {
    setPeek(!!active && !!shutOver && typeof document !== "undefined" && !!document.querySelector?.("[data-drop-target]"));
  }, [active, shutOver]);
  // 结束拖动那次提交里拖动库要播的落位（见 dropAnimation）：松手时已经起播（landOverlay）的接着等它播完；取消时从此刻滑回原位
  const drop = useMemo(() => dropAnimation(transition, into), [transition, into]);
  // 键盘拖动时浮层一格一格挪，也用动效变量过渡
  const keyboardMove = (event: Event | null) =>
    transition && event && "key" in event ? `transform ${transition.duration}ms ${transition.easing}` : undefined;

  const overlay = (
    <DragOverlay dropAnimation={drop} transition={keyboardMove} zIndex={OVERLAY_Z} className="cc-drag-overlay">
      {active ? (
        <div className="cc-drag-float" data-kind={data?.kind} data-peek={peek ? "" : undefined} inert aria-hidden>
          <div className="cc-drag-lift">
            <Copy id={active.id} node={activeNode} />
            {(data?.count ?? 0) > 1 && <span className="cc-drag-count">{data!.count}</span>}
          </div>
        </div>
      ) : null}
    </DragOverlay>
  );
  // 挂到 body 上的拖动层里（见 cloneOf 上面的说明、dragLayer）；没有页面（服务端渲染的测试）时就地
  const layer = dragLayer();
  return layer ? createPortal(overlay, layer) : overlay;
};

/** 浮层挂的那一层：body 下面一个一直留着的空 div（第一次用时建）。不直接挂在 body 上：拖动库盯着 body 里的增删（被拖的那一项
 *  与它的父元素各一个观察器，增删的地方包着它就重量一次），浮层一挂上 body 它就要当场量两次，刚改过页面，整页样式、布局要现算
 *  （第 11 轮 B14：拿起那一帧里约 20ms，6 倍节流）。挂在这一层里，增删的是这一层，不包着它们。 */
let layer: HTMLElement | null = null;
export function dragLayer(): HTMLElement | null {
  if (typeof document === "undefined" || !document.body) return null;
  if (!layer?.isConnected) {
    layer = document.createElement("div");
    layer.dataset.slot = "cc-drag-layer";
    layer.appendChild(dragShield());
    document.body.appendChild(layer);
  }
  return layer;
}

/** 鼠标拖动中盖住整页的透明挡板（lib/layers.ts 的 setDragShield：拿起那一帧画完盖上、松手那一帧画完撤掉）：整页 grabbing 光标，
 *  指针下面的行、组头、卡片不出悬停；与浮层同在拖动层里（增删、显隐都不在拖动库盯着的地方）。挡着的时候滚轮转给指针下面那个
 *  滚动区（侧栏、会话栏拖着时照样能滚）。 */
function dragShield(): HTMLElement {
  const el = document.createElement("div");
  el.className = "cc-drag-shield";
  el.dataset.slot = "cc-drag-shield";
  el.setAttribute("aria-hidden", "true");
  el.addEventListener("wheel", (event) => wheelUnder(el, event), { passive: true });
  setDragShield(el);
  return el;
}

/** 挡板上的滚轮：交给指针下面最近的、能往这个方向滚的滚动区。 */
export function wheelUnder(shield: HTMLElement, event: WheelEvent) {
  const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1;
  const dy = event.deltaY * scale;
  const dx = event.deltaX * scale;
  for (const hit of document.elementsFromPoint(event.clientX, event.clientY)) {
    if (hit === shield) continue;
    for (let el: Element | null = hit; el && el !== document.documentElement; el = el.parentElement) {
      if (!(el instanceof HTMLElement) || el.scrollHeight <= el.clientHeight) continue;
      if (!/(auto|scroll)/.test(getComputedStyle(el).overflowY)) continue;
      el.scrollBy({ top: dy, left: dx });
      return;
    }
    return;
  }
}

// ---------------------------------------------------------------- 键盘拖动的焦点
// 拖动中归拖动的键：方向键、Home / End、翻页（拖动库收在 document 上；按在把手上时标成已处理，组件库行自带的方向键导航不再移焦点）
const DRAG_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);
const byKeyboard = (event: Event | null | undefined) => !!event && "key" in event;
// 鼠标拿起的（不是键盘、不是手指）：只有它要挡板（整页 grabbing 光标、不出悬停，lib/layers.ts）
const byMouse = (event: Event | null | undefined) => !!event && !("key" in event) && !("touches" in event);
// 各项眼前的把手（拖动编号 → 节点），键盘放下后找回焦点用
const handles = new Map<string | number, HTMLElement>();
// 键盘拖动放下 / 取消之后的几秒里（落位后交给调用方、按新顺序重排，这一项可能又重挂）：焦点丢了（落到 body）就接回这一项的
// 把手。焦点在别处（人自己挪了）不抢
let refocus: { id: string | number; until: number } | null = null;
let refocusFrame = 0;
const REFOCUS_MS = 5000;

function focusLost(): boolean {
  const el = document.activeElement;
  return !el || el === document.body || !el.isConnected;
}

const shown = (el: HTMLElement | undefined): el is HTMLElement => !!el?.isConnected && el.getClientRects().length > 0;

/** 登记 / 注销一项眼前的把手（useFollowFocus 的 ref；同一项换组重挂时先卸后挂，卸的不是登记着的那个就不动）。 */
export function rememberHandle(id: string | number, node: HTMLElement | null, before: HTMLElement | null = null) {
  if (node) handles.set(id, node);
  else if (handles.get(id) === before) handles.delete(id);
}

/** 拖动结束（放下 / 取消）后接下来几秒：键盘拿起的（activatorEvent 带 key）焦点丢了就接回这一项的把手；别的拿起方式不管。 */
export function keepFocusOn(id: string | number, activatorEvent: Event | null) {
  if (typeof window === "undefined") return;
  if (refocusFrame) window.cancelAnimationFrame(refocusFrame);
  refocusFrame = 0;
  refocus = byKeyboard(activatorEvent) ? { id, until: performance.now() + REFOCUS_MS } : null;
  const watch = () => {
    refocusFrame = 0;
    if (!refocus || performance.now() > refocus.until) {
      refocus = null;
      return;
    }
    if (focusLost()) {
      const own = handles.get(refocus.id);
      if (shown(own)) own.focus({ preventScroll: true });
    }
    refocusFrame = window.requestAnimationFrame(watch);
  };
  if (!refocus) return;
  // 人用鼠标 / 手指点了别处：不再把焦点接回来（只停这一次的，之后的拖动不受影响）
  const mine = refocus;
  window.addEventListener("pointerdown", () => refocus === mine && (refocus = null), { once: true, capture: true });
  refocusFrame = window.requestAnimationFrame(watch);
}

/** 把手的 ref：键盘拖动中焦点一直在被拖的这一项的把手上（换组重挂、原来那个节点卸下也跟到新节点）；登记眼前的把手，放下 / 取消后
 *  焦点丢了按它接回来（keepFocusOn）。 */
function useFollowFocus(id: string | number, drag: Draggable & { isDragging: boolean }) {
  const el = useRef<HTMLElement | null>(null);
  const { setActivatorNodeRef } = drag;
  const ref = useCallback(
    (node: HTMLElement | null) => {
      rememberHandle(id, node, el.current);
      el.current = node;
      setActivatorNodeRef(node);
    },
    [setActivatorNodeRef, id],
  );
  const keyed = useContext(KeyedContext) === id && drag.isDragging;
  useLayoutEffect(() => {
    const node = el.current;
    if (keyed && node?.isConnected && document.activeElement !== node) node.focus({ preventScroll: true });
  });
  return ref;
}

// ---------------------------------------------------------------- 各项
type HandleOptions = {
  disabled?: boolean;
  /** 把手本来就是能聚焦的按钮（侧栏的行、组头开关）：不加拖动库的 role / tabIndex / 禁用标记，焦点仍归侧栏键盘管。 */
  ownFocus?: boolean;
  /** 从这些地方按下不拿起（行尾「···」、改名输入框）。 */
  skip?: string;
};

type Draggable = Pick<ReturnType<typeof useDraggable>, "attributes" | "listeners" | "setActivatorNodeRef">;

export type DragHandle = { ref: (el: HTMLElement | null) => void; "data-drag-handle"?: "" } & Pick<
  HTMLAttributes<HTMLElement>,
  | "role"
  | "tabIndex"
  | "aria-roledescription"
  | "aria-describedby"
  | "aria-disabled"
  | "aria-pressed"
  | "onMouseDown"
  | "onTouchStart"
  | "onKeyDown"
  | "onKeyUp"
>;

function handleOf(drag: Draggable, { disabled, ownFocus, skip }: HandleOptions): DragHandle {
  const { role, tabIndex, "aria-pressed": _pressed, "aria-disabled": _disabled, ...aria } = drag.attributes;
  const listeners = disabled ? undefined : drag.listeners;
  const skipped = (event: { target: EventTarget | null }) => !!skip && !!(event.target as Element | null)?.closest?.(skip);
  return {
    ref: drag.setActivatorNodeRef,
    ...(ownFocus ? aria : { ...drag.attributes, role, tabIndex }),
    // 拖动中按在把手上的方向键只归拖动（拖动库在 document 上收）：标成已处理，接在后面的方向键导航（组件库对话行的 ↑ ↓）不接手
    onKeyDown: (event: ReactKeyboardEvent) => {
      if (isDragging() && DRAG_KEYS.has(event.key)) event.preventDefault();
      else listeners?.onKeyDown?.(event);
    },
    ...(listeners
      ? {
          // 能拖的把手：悬停出抓手光标（globals.css 的 [data-drag-handle]，各处同一条；不能拖时——搜索中、没存到服务端——不加）
          "data-drag-handle": "" as const,
          onMouseDown: (event: ReactMouseEvent) => !skipped(event) && listeners.onMouseDown?.(event),
          onTouchStart: (event: ReactTouchEvent) => !skipped(event) && listeners.onTouchStart?.(event),
          // 空格是拿起 / 放下：松开空格不再当成点击（不打开对话、不折叠组）
          onKeyUp: (event: ReactKeyboardEvent) => event.code === "Space" && event.preventDefault(),
        }
      : {}),
  };
}

// 有整列滑动（flow）的拖动区（侧栏）拖动中不给每一项、每一块落点各挂一个尺寸观察器（拖动库默认拿起时给所有能放的地方各建一个
// ResizeObserver：侧栏约 60 个，拿起那一帧里建、下一帧各回调一次，第 11 轮 B14）：那里各项高度固定，增删、挪列由整列滑动
// （flowLayout）的观察器重量。没有整列滑动的区（会话栏）照拖动库默认观察：卡片只有几张，拖动中内容会变高（本月卡定时刷新、
// 公司页转圈换成内容、输出卡跟着对话变长），不观察碰撞与让位就按旧尺寸算（审查）
const NO_RESIZE_WATCH = { disabled: true } as const;

/** 这个拖动区有没有整列滑动（DragArea 的 flow）：有才关掉各项、各落点的尺寸观察（NO_RESIZE_WATCH）。 */
const FlowAreaContext = createContext(false);

/** 排序列表里的一项：node 给整项（位移、原位留空），handle 给把手（拿起的地方）。data-drag-id 让拖进另一列、在那里重挂的同一项
 *  接着滑（flowLayout）。
 *
 *  拖动库每次拿起、经过、放下、重量位置都重画所有能拖的项（它们都读拖动的上下文），这一层躲不开；躲得开的是用它的组件（侧栏的
 *  行、组头、会话栏的卡，memo）。回的几样按内容记住，拖动中不变：让位的位移与过渡不经 React 传给各行，由这里在提交时直接写到
 *  那一项的节点上（applyShift）。测试窗口第 11 轮 B14：原来位移、过渡放在 node 的 style 里，拖动库一开始排序，每一项的过渡就
 *  从无变成一段字符串，node 跟着变，每次拿起、落定整列行与组头全部重画（A14 拿起那一帧 94–126ms）。现在只有拿起的那一项
 *  （data-drag-source）变。 */
export function useSortableItem(data: DragData, options: HandleOptions = {}) {
  const transition = useContext(MotionContext);
  const drag = useMemo(() => ({ drag: data }), [data.id, data.kind, data.label, data.list, data.group, data.count, data.session]);
  const flowArea = useContext(FlowAreaContext);
  const sortable = useSortable({
    id: data.id,
    data: drag,
    disabled: options.disabled,
    transition,
    resizeObserverConfig: flowArea ? NO_RESIZE_WATCH : undefined,
  });
  const handleRef = useFollowFocus(data.id, sortable);
  const say = useContext(SayMoveContext);
  // 取消后滑回原位的途中（settleBack 已去掉让位位移）：重画也不写回去
  const settling = useContext(SettleContext).current;
  const shift = shiftStyle(sortable.transform, sortable.transition, settling);
  const { setNodeRef, isDragging, attributes, listeners } = sortable;
  const el = useRef<HTMLElement | null>(null);
  const ref = useCallback(
    (node: HTMLElement | null) => {
      el.current = node;
      setNodeRef(node);
    },
    [setNodeRef],
  );
  // 每次提交都对一下（节点重挂过的也在这次提交里补上）；同样的不写
  useLayoutEffect(() => applyShift(el.current, shift));
  // 此刻排在哪一列（拖进另一列、在那里重挂的那一项是那一列，不是它说明里原来那一列）：取消时认它挪回了原来那一列、滑回时只清
  // 这一列的让位位移，都按它
  const list = (sortable.data as { sortable?: { containerId?: string | number } }).sortable?.containerId ?? data.list;
  const node = useMemo(
    () => ({
      ref,
      "data-drag-id": data.id,
      "data-drag-list": list === undefined ? undefined : String(list),
      "data-drag-source": isDragging ? ("gap" as const) : undefined,
    }),
    [ref, data.id, list, isDragging],
  );
  const { disabled, ownFocus, skip } = options;
  const handle = useMemo(
    () => handleOf({ attributes, listeners, setActivatorNodeRef: handleRef }, { disabled, ownFocus, skip }),
    [attributes, listeners, handleRef, disabled, ownFocus, skip],
  );
  const label = data.label;
  /** Alt+↑/↓ 挪完：念新位置（没挪动——到头了——不念）。 */
  const moved = useCallback((spot: MoveSpot | null | void) => {
    if (spot) say?.(label, spot);
  }, [say, label]);
  return useMemo(() => ({ moved, node, handle, dragging: isDragging }), [moved, node, handle, isDragging]);
}

/** 拖动库给这一项的让位（位移、过渡）换成要写到节点上的行内样式：取消后滑回原位的途中（settling）不写位移；位移取整后是零
 *  （拿起时拖动库给每一项一个 {x:0, y:0}）当作没有位移——写出来虽然不动，那一项也要重算一次样式（第 11 轮 B14 审查：键盘
 *  拿起组头、鼠标拿起行时不让位的各项照样写了一遍）。位移按浏览器读回的写法拼（第三个参数 0px）。 */
export function shiftStyle(transform: { x: number; y: number } | null | undefined, transition: string | null | undefined, settling = false) {
  const x = transform ? Math.round(transform.x) : 0;
  const y = transform ? Math.round(transform.y) : 0;
  return { transform: settling || (!x && !y) ? "" : `translate3d(${x}px, ${y}px, 0px)`, transition: transition ?? "" };
}

// 每个节点上次由 applyShift 写的值（raw）与写完浏览器读回的（seen：浏览器会改写法，如 0 → 0px、过渡补全默认值）。读回的还是
// seen 就按 raw 比，不拿读回的写法与要写的比（那样永远不等，每次提交都重写一遍）；别处改过（滑回时 settleBack 直接清位移）
// 读回的就不是 seen，按读回的比
const written = new WeakMap<CSSStyleDeclaration, { transform: [string, string]; transition: [string, string] }>();

/** 把让位的位移与过渡写到那一项的节点上（空串 = 去掉）。过渡只跟着位移写：位移要变（或身上还带着位移）时先写过渡、再写位移，
 *  同一次样式计算里位移的变化按新的过渡走；没在让位的项不写——拖动库一开始排序就给每一项一段过渡，原来拿起那一下整列每一项
 *  都改一次行内样式、都要重算样式（第 11 轮 B14），结束时又整列清一遍。身上留着过渡、已经不在让位的，结束时清掉。同样的值
 *  不写第二遍（按上次写的比，见 written）。 */
export function applyShift(node: HTMLElement | null, shift: { transform: string; transition: string }) {
  if (!node) return;
  const style = node.style;
  const last = written.get(style);
  const now = (name: "transform" | "transition") => {
    const seen = style[name];
    return last && seen === last[name][1] ? last[name][0] : seen;
  };
  const record = { transform: [now("transform"), style.transform], transition: [now("transition"), style.transition] } as {
    transform: [string, string];
    transition: [string, string];
  };
  const set = (name: "transform" | "transition", value: string) => {
    style[name] = value;
    record[name] = [value, style[name]];
  };
  const moving = record.transform[0] !== shift.transform;
  if (moving || record.transform[0]) {
    if (record.transition[0] !== shift.transition) set("transition", shift.transition);
  } else if (record.transition[0] && !shift.transition) set("transition", "");
  if (moving) set("transform", shift.transform);
  written.set(style, record);
}

/** 一列整块的落点（侧栏的已置顶、各组）：data.list 是它代表的那一列，shut = 此刻收着（放不下空档）——指着它时整块（就是组头）
 *  高亮，放下落到组头并收起。ref 给整块（组头 + 这一列的各项）。 */
export function useDropZone(data: DragData, shut = false) {
  const drag = useMemo(() => ({ drag: { ...data, zone: true, shut } }), [data.id, data.kind, data.label, data.list, shut]);
  const flowArea = useContext(FlowAreaContext);
  const droppable = useDroppable({ id: data.id, data: drag, resizeObserverConfig: flowArea ? NO_RESIZE_WATCH : undefined });
  const over = useContext(ShutOverContext) === data.id;
  return { ref: droppable.setNodeRef, "data-drop-target": over && shut ? "" : undefined };
}

/** Alt+↑ / Alt+↓ 挪一格（会话栏卡片、侧栏公司组头、置顶对话）；挪完焦点留在原来的按钮上。是这两个键回 true。
 *  读屏播报由调用方在 move 里交给 useSortableItem 的 moved（同键盘拖动一套文案）。 */
export function altMove(event: ReactKeyboardEvent, move: (delta: -1 | 1) => void): boolean {
  // 键盘拖动中 Alt+方向键也归拖动（交给把手的 onKeyDown 标成已处理）
  if (isDragging() || !event.altKey || event.metaKey || event.ctrlKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return false;
  event.preventDefault();
  const el = event.target as HTMLElement;
  move(event.key === "ArrowUp" ? -1 : 1);
  requestAnimationFrame(() => {
    if (el.isConnected && document.activeElement !== el) el.focus();
  });
  return true;
}

/** 把几份事件处理接到一起（拖动的 + 原来的），依次调用。 */
export function chain<E>(...handlers: Array<((event: E) => unknown) | undefined>): (event: E) => void {
  return (event: E) => {
    for (const handler of handlers) handler?.(event);
  };
}
