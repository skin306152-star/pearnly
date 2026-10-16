// 共用拖动（用户 10-15 晚：拖动要「全部参考视频，非常丝滑」；10-16：「所有拖拽的地方要改统一规则和效果」、「对话的拖动效果怎么没和
// 其它拖动效果统一呢」。参考 Claude 桌面版右侧栏拖卡片的录屏 IMG_5192）。会话栏卡片（session-panel.tsx）、侧栏公司组排序、置顶对话
// 排序、对话拖到别的公司（thread-list.tsx）都走这一份；修一处就在这里修。基于 dnd-kit（MIT，版本锁在 package.json）。
//
//   拿起   鼠标按住动 3px（点一下照样是点）；手机长按 250ms 且没挪出 5px（没到时间就动 = 在滚动）；键盘 空格。能拖的把手带
//          data-drag-handle，悬停出抓手光标（globals.css 同一条），拖动中整页 grabbing。侧栏的行与组头长按另有菜单（touch="move"）：按住 250ms 后再动才拿起，
//          按住不动到 500ms 照旧出长按菜单，按到 500ms 后（菜单出来或正要出来）就不再拿起，两者不冲突。
//   浮起   拿起的那一项换成浮层（DragOverlay，克隆原来那一项的样子，挂在 body 上）：弹层底、弹出阴影、略透明、略放大，实时跟手。
//          原位留空（空档）。排序（一列里调先后）：其余各项拖动中就带过渡滑开让位（dnd-kit 的排序位移，过渡用动效变量）。
//          在组之间移动（侧栏对话拖到别的公司：组里的先后由排序方式定、不能手调）：空档跟着去那一组，出现在调用方按排序方式算出的
//          位置（onPlace），原来那组的空档合上——两组之间整列各行、组头的位置变了都带同样的过渡滑过去（flowLayout，这段时间关掉
//          滚动锚定）；空档的位置不跟指针，落在滚动区可见范围外时平滑滚进来（revealGap）；在原组里动空档不动；去的组收着（放不下
//          空档）时组头高亮、原位空档留着。dnd-kit 多列排序的做法，只是空档的位置由排序方式定。
//   放下   松手当帧就从放手处平滑落到空档（拖动库在放下那次提交里用 Web Animations 播，不等之后的重排；动效变量），落到收着的组
//          就落到组头并收起；落点在滚动区可见范围外（拖着时又滚走了）就落到可见范围的边上并收起，不飞出列表、列表不跳；没落点 /
//          取消就滑回原位。在组之间移动的，落位播完才交给调用方（onDrop，如发移动请求，settleMove），请求失败
//          调用方退回原组时同样带过渡滑回去。第 4 轮测试机：原来拿起的就是列表里那张卡本身，放下靠自写的 translate 过渡，要等新顺序
//          提交、整列重测之后的下一次样式计算才开始，手机上卡在放手处停了约 0.4s、压着下一张卡才滑进空位。
//   范围   只在本列竖着拖：浮层上下边不出这一列（排序：这一列第一项顶边到最后一项底边；组之间：能放的各组加原来那组）。
//          浮层是 fixed、不撑大滚动区，拖到边上自动滚动只到列表首尾（第 4 轮：原来限位只限住指针位移，拖动库随后又把滚动差
//          加到那张卡上，卡被推出列表、撑大了可滚范围，会话栏一路滚进空白）。
//   键盘   空格拿起、方向键移动（组之间：一组一组跳，含原来那组）、空格（或回车）放下、Esc 取消，三种语言读屏播报（文案由调用方给）。
//          Esc 只取消拖动：不关浮着的会话栏、不清侧栏多选（lib/layers.ts 的 isDragEscape）。
//   动效   时长、缓动取 globals.css 的 --cc-dur / --cc-ease；系统或设置要求减少动态效果时不播过渡。
//
// 样式：globals.css 的 .cc-drag-*、[data-drag-source]、[data-drop-target]；这里只有拖动算出的位移是行内样式（组之间滑动用
// Web Animations，不写行内样式）。

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
  type Modifier,
  MouseSensor,
  type Over,
  pointerWithin,
  type SensorInstance,
  type SensorProps,
  useDndContext,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { createPortal } from "react-dom";
import {
  createContext,
  type CSSProperties,
  type FC,
  type HTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type TouchEvent as ReactTouchEvent,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { LONG_PRESS_MS } from "@/hooks/use-long-press";
import { cssMs } from "@/lib/hover-scroll";
import { OPEN_LAYERS, setDragging } from "@/lib/layers";

// ---------------------------------------------------------------- 拖的是什么
/** 一项拖动的说明（放在 dnd-kit 的 data.drag 里）：kind 是哪一种（card / company / pin / row / recents），label 是读屏念的名字，
 *  list 是排序的那一列（排序时才有），group 是侧栏所在的公司（"" = 最近），count 一起拖的条数（多选），session 对话编号。 */
export type DragData = {
  id: string;
  kind: string;
  label: string;
  list?: string;
  group?: string;
  count?: number;
  session?: string;
};

type WithData = { data: { current?: Record<string, unknown> | null } } | null | undefined;

export function dataOf(entry: WithData): DragData | undefined {
  return (entry?.data.current?.drag as DragData | undefined) ?? undefined;
}

/** 在组之间移动的一项（侧栏对话）：不在排序的一列里（没有 list），有所在的组（group，"" 也算）。组里的先后由调用方的排序方式定。 */
export function isMover(data: DragData | null | undefined): data is DragData {
  return !!data && !data.list && data.group !== undefined;
}

/** target 是不是拿着的那一项原来所在的那一组（拖回去 = 空档回原位、不动）。 */
export function isHome(active: DragData, target: DragData): boolean {
  return isMover(active) && target.id !== active.id && target.group === active.group;
}

/** 在组之间移动时指针（键盘拖时是浮层中心）落到了哪：能放的别的组 → 那一组；原来那组 → null（空档回原位）；
 *  哪组都不在（组与组之间、列外）或不能放 → undefined（空档留在上一次的地方，不来回跳）。 */
export function placeOf(active: DragData, over: DragData | null | undefined, accepts: AreaProps["accepts"]): DragData | null | undefined {
  if (!over) return undefined;
  if (isHome(active, over)) return null;
  return accepts(active, over) ? over : undefined;
}

/** 读屏播报的文案（三种语言由调用方给）：排序时念位置，拖到别的组时念组名。 */
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

/** dnd-kit 的读屏播报：名字用 label；排序时位置按这一列（看得见的）算，在组之间移动时念组名（回到原来那组念「已拿起」，
 *  放下时按空档所在：别的组念移到哪，原组念取消）。placed 是此刻空档所在的组（null = 原组）。 */
export function dragAnnouncements(
  texts: DragTexts,
  mover?: { accepts: AreaProps["accepts"]; placed: () => DragData | null; lifted?: (active: Active) => DragData | undefined },
): Announcements {
  const name = (entry: WithData) => dataOf(entry)?.label ?? "";
  const say = (active: Active, over: Over | null, end: boolean) => {
    const from = mover?.lifted?.(active) ?? dataOf(active);
    const to = dataOf(over);
    if (mover && isMover(from)) {
      if (end) {
        const target = mover.placed();
        return target ? texts.droppedIn?.(name(active), target.label) : texts.cancelled(name(active));
      }
      const target = to && placeOf(from, to, mover.accepts);
      if (target === undefined) return undefined;
      return target ? texts.over?.(name(active), target.label) : texts.pickedUp(name(active));
    }
    if (!over) return end ? texts.cancelled(name(active)) : undefined;
    if (from?.list && to?.list === from.list) {
      const at = position(over);
      if (!at) return undefined;
      return (end ? texts.droppedAt : texts.movedTo)(name(active), at.at, at.total);
    }
    return (end ? texts.droppedIn : texts.over)?.(name(active), to?.label ?? "");
  };
  return {
    onDragStart: ({ active }) => texts.pickedUp(name(active)),
    onDragOver: ({ active, over }) => say(active, over, false),
    onDragEnd: ({ active, over }) => say(active, over, true),
    onDragCancel: ({ active }) => texts.cancelled(name(active)),
  };
}

// ---------------------------------------------------------------- 手感
// 鼠标按住动 3px 才开始（点一下照样是点），手机长按 250ms 且没挪出 5px 才开始（滑动照样滚动）
export const MOUSE_START = { distance: 3 };
export const TOUCH_START = { delay: 250, tolerance: 5 };
// 空格拿起，空格 / 回车 / Tab 放下，Esc 取消（回车不拿起：对话行、组头上回车是打开 / 折叠）
const KEYS = {
  start: [KeyboardCode.Space],
  cancel: ["Escape" as KeyboardCode],
  end: [KeyboardCode.Space, KeyboardCode.Enter, KeyboardCode.Tab],
};

type HoldOptions = { delay: number; tolerance: number; moveToLift?: boolean };
type Listen = [EventTarget, string, EventListener, AddEventListenerOptions | undefined];

const stopClick = (event: Event) => {
  event.stopPropagation();
  event.preventDefault();
};
const clearSelection = () => window.getSelection?.()?.removeAllRanges();
const prevent = (event: Event) => event.preventDefault();

/** 手机长按拿起（dnd-kit 自带的触屏传感器到时间就拿起，侧栏行的长按菜单会跟它打架）：按住 delay 且没挪出 tolerance 才算
 *  按住；moveToLift 时按住后还要再动才拿起（按住不动留给长按菜单，菜单出来就不拿起），否则按住就拿起（会话栏卡片的把手）。
 *  拿起后滑动不再滚页面；拿起前松手、滑走都交还给页面（点、滚动、长按菜单照旧）。 */
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
  private readonly since = Date.now();
  private readonly listening: Listen[] = [];
  private timer = 0;
  private armed = false;
  private lifted = false;
  private done = false;

  constructor(props: SensorProps<HoldOptions>) {
    this.props = props;
    const event = props.event as TouchEvent;
    const touch = event.touches?.[0];
    this.origin = { x: touch?.clientX ?? 0, y: touch?.clientY ?? 0 };
    const target = event.target ?? document;
    this.listen(target, "touchmove", this.move, { passive: false });
    this.listen(target, "touchend", this.end);
    this.listen(target, "touchcancel", this.cancel);
    this.listen(window, "resize", this.cancel);
    this.listen(document, "visibilitychange", this.cancel);
    this.listen(window, "contextmenu", prevent);
    this.listen(document, "keydown", this.key);
    this.timer = window.setTimeout(this.arm, props.options.delay);
  }

  private listen(target: EventTarget, type: string, run: EventListener, options?: AddEventListenerOptions) {
    target.addEventListener(type, run, options);
    this.listening.push([target, type, run, options]);
  }

  private readonly arm = () => {
    this.timer = 0;
    if (this.props.options.moveToLift) this.armed = true;
    else this.lift();
  };

  private lift() {
    if (this.lifted) return;
    this.lifted = true;
    clearSelection();
    // 拿起后松手的那一下不算点击（不切对话、不收抽屉）；拖动中不选中文字
    this.listen(document, "click", stopClick, { capture: true });
    this.listen(document, "selectionchange", clearSelection);
    this.props.onStart(this.origin);
  }

  private readonly move = (event: Event) => {
    const touch = (event as TouchEvent).touches?.[0];
    if (!touch) return;
    const at = { x: touch.clientX, y: touch.clientY };
    const far = Math.hypot(at.x - this.origin.x, at.y - this.origin.y) > this.props.options.tolerance;
    if (!this.lifted) {
      if (!this.armed) {
        if (far) this.cancel(); // 没按够时间就动：是在滚动
        return;
      }
      if (event.cancelable) event.preventDefault(); // 已按住：不再让页面滚
      // 按到了长按菜单的时间就归菜单（菜单出来了，或计时已到、菜单要等下一次提交才挂上 data-state=open 的那一帧）：不拿起。
      // 两边用同一个时长（hooks/use-long-press.ts）；没到这个时间就动了，拿起时已标记在拖，长按计时到了也不出菜单
      if (Date.now() - this.since >= LONG_PRESS_MS || document.querySelector(OPEN_LAYERS)) return this.cancel();
      if (!far) return;
      this.lift();
    }
    if (event.cancelable) event.preventDefault();
    this.props.onMove(at);
  };

  private readonly key = (event: Event) => {
    if ((event as KeyboardEvent).key === "Escape") this.cancel();
  };

  private readonly end = () => this.finish(false);
  private readonly cancel = () => this.finish(true);

  private finish(cancelled: boolean) {
    if (this.done) return;
    this.done = true;
    window.clearTimeout(this.timer);
    const clicks = this.listening.filter(([target, type]) => target === document && (type === "click" || type === "selectionchange"));
    for (const [target, type, run, options] of this.listening) {
      if (!clicks.some((entry) => entry[2] === run && entry[1] === type)) target.removeEventListener(type, run, options);
    }
    // 松手后紧跟着的 click 还要拦下：稍后再摘（同 dnd-kit）
    window.setTimeout(() => clicks.forEach(([target, type, run, options]) => target.removeEventListener(type, run, options)), 50);
    if (!this.lifted) this.props.onAbort(this.props.active);
    if (cancelled) this.props.onCancel();
    else this.props.onEnd();
  }
}

// ---------------------------------------------------------------- 范围
/** 拿起的那一项在这一列的上下边之间（视口坐标）：y 是浮层相对拿起时的位移，card 是拿起时的位置，list 是这一列此刻的上下边。
 *  比整列还高（不会发生）时不动。 */
export function clampToList(y: number, card: { top: number; height: number }, list: { top: number; bottom: number }): number {
  const min = list.top - card.top;
  const max = list.bottom - (card.top + card.height);
  return Math.min(Math.max(y, min), Math.max(min, max));
}

/** 几个框合起来的上下边（不含拖动位移：排序让位时各项在动，按版面位置算才稳）。 */
function spanOf(nodes: ReadonlyArray<HTMLElement | null | undefined>): { top: number; bottom: number } | null {
  let top = Number.POSITIVE_INFINITY;
  let bottom = Number.NEGATIVE_INFINITY;
  for (const node of nodes) {
    if (!node?.isConnected) continue;
    const rect = getClientRect(node, { ignoreTransform: true });
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

// 在组之间移动时整列跟着动（对话的空档换了组：原来那组合上、去的那组让开，两组之间的各行、组头整体上下移）：版面一变，凡是位置
// 变了的（selector 选中的各项：侧栏的行、组头、「显示更多」）都从原来的位置带过渡滑到新位置（先记位置、变了再从差值滑回 0，
// 即 FLIP）。用 Web Animations 播 translate：不碰行内样式（排序项的 transform / transition 归 dnd-kit 与 React 管），
// 也不影响拖动库量版面（它量的是去掉 transform 的位置，与 translate 无关的各组整块）。位置按滚动区内容算，滚动不算移动；
// 正在滑的再变就从此刻看到的位置接着滑，不跳。空档（被拖的那一项）与正在落位的那一项看不见，不滑。
const STILL = "[data-drag-source], [data-drag-landing]";

/** 落位动画从松手的时刻起算：不等到下一帧才定起点（放下那一帧主线程忙时——侧栏一次提交要重画一整列——落位照样按松手的
 *  时刻开始走，不比会话栏卡片晚一帧；headless Chrome 实测侧栏原来晚约 17ms 才动）。代价：主线程忙多久，落位就少播多久的
 *  前段（卡片与对话行同一个函数，彼此一致）。只用于落位；整列滑动不用（见 flowLayout）。 */
function startNow(animation: Animation) {
  const now = typeof document !== "undefined" ? document.timeline?.currentTime : null;
  if (typeof now === "number" && animation.startTime === null) animation.startTime = now;
}

/** 此刻 translate 的竖向位移（动画播到哪算到哪；没有为 0）。 */
function shiftOf(el: Element): number {
  const value = getComputedStyle(el).translate;
  if (!value || value === "none") return 0;
  const parts = value.trim().split(/\s+/);
  return parts.length > 1 ? Number.parseFloat(parts[1]!) || 0 : 0;
}

/** 盯着 root 里 selector 选中的各项，版面一变就让挪了位置的滑过去；返回停止（已在滑的照常滑完）。
 *  盯着的这段时间关掉 root 的滚动锚定（overflow-anchor）：变动在可见区上方时（空档进了上面的组、多选其余各行从上面移走、
 *  失败退回上面的原组），浏览器会自动改 scrollTop 让眼前的行不动，这里却按内容坐标算，没动的行会先跳一下再滑回、下面的行
 *  直接跳（审查实测）。关掉后眼前的行照内容真实挪动、带过渡滑过去，同其他组。停止时还原。 */
export function flowLayout(root: HTMLElement, selector: string, transition: Motion): () => void {
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
      if (!animate || !before || Math.abs(before.top - top) < 0.5) continue;
      sliding.get(el)?.cancel();
      sliding.delete(el);
      if (el.matches(STILL)) continue;
      const dy = before.top + (before.el === el ? shift : 0) - top;
      if (Math.abs(dy) < 0.5) continue;
      // 起点不回拨（不用 startNow）：同排序项的 CSS 过渡，从这次版面变化画出来的那一帧起算。回拨到上一帧会把这次重画整列
      // 花掉的时间算进过渡里，主线程忙（手机）时让位一两帧就走完（审查：CPU 6 倍节流实测）
      const slide = el.animate([{ translate: `0 ${dy}px` }, { translate: "0 0" }], { duration: transition.duration, easing: transition.easing });
      sliding.set(el, slide);
      slide.onfinish = () => sliding.get(el) === slide && sliding.delete(el);
    }
    for (const key of [...records.keys()]) if (!seen.has(key)) records.delete(key);
  };
  pass(false);
  const observer = new MutationObserver(() => pass(true));
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

/** 空档要能看见：在滚动区可见范围之外时平滑滚动滚动区，正好让它进来（只滚差的那一段，滚动到不越过首尾由浏览器管）。
 *  在组之间移动时空档的位置由排序方式定、不跟指针，可能落在可见范围外（审查：拖到展开很长的组，空档在组顶、组顶在上面）。 */
export function revealGap(node: HTMLElement, smooth: boolean) {
  const view = viewOf(node);
  const rect = node.getBoundingClientRect();
  if (inView(rect, view)) return;
  const by = rect.top < view.top ? rect.top - view.top : rect.top + rect.height - view.bottom;
  const scroller: { scrollBy: (options: ScrollToOptions) => void } = view.root ?? window;
  scroller.scrollBy({ top: by, behavior: smooth ? "smooth" : "auto" });
}

/** 放下时空档在可见范围外（拖着时又滚走了）：拖动库在落位前会对它 scrollIntoView（只看窗口，滚动区会瞬间跳一下）。
 *  这一次不让它滚（落位改为落到可见范围的边上并收起，见 Lifted）；过 wait 毫秒还原。 */
function holdStill(node: HTMLElement | null | undefined, wait: number) {
  if (!node?.isConnected || inView(node.getBoundingClientRect(), viewOf(node))) return;
  node.scrollIntoView = () => undefined;
  setTimeout(() => delete (node as Partial<HTMLElement>).scrollIntoView, wait);
}

/** 在组之间移动的放下：落位动画播完才交给调用方（发移动请求）。land 由落位动画播完时调；动画没播（被打断、量不到）时
 *  过 wait 毫秒自己调。新拿起一项、拖动区卸下时先把没交的交掉。 */
export class Landing {
  private run: (() => void) | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  queue(run: () => void, wait: number) {
    this.land();
    this.run = run;
    this.timer = setTimeout(() => this.land(), wait);
  }

  get pending(): boolean {
    return !!this.run;
  }

  land() {
    const run = this.run;
    this.run = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    run?.();
  }
}

/** 在组之间移动的那一项放下了（松手；取消时 target 为 null）。空档在别的能放的组：落位播完（landing）才交给调用方（drop，
 *  如发移动请求），交过之后 clear（空档回 null，调用方那时已按移过去的样子排好），drop 回的 Promise 做完才 done（整列停止
 *  跟着动）；没播落位时过 wait 毫秒自己交。空档在原组 / 没有 / 不能放：clear、done，不交。回 true = 排上了要交。 */
export function settleMove(
  from: DragData,
  target: DragData | null,
  accepts: (active: DragData, target: DragData) => boolean,
  hooks: { landing: Landing; wait: number; drop: (target: DragData) => unknown; clear: () => void; done?: (() => void) | null },
): boolean {
  if (!target || !accepts(from, target)) {
    hooks.clear();
    hooks.done?.();
    return false;
  }
  hooks.landing.queue(() => {
    const work = hooks.drop(target);
    hooks.clear();
    void Promise.resolve(work)
      .catch(() => undefined)
      .finally(() => hooks.done?.());
  }, hooks.wait);
  return true;
}

// 落位播完停在落点（同 dnd-kit 默认），浮层随后才卸下
const STAY: FillMode = "forwards";

/** 放下的落位动画（同 dnd-kit 默认那一种：浮层从放手处移到落点，Web Animations、放下当帧开始），多两件：落点可以不是
 *  空档本身（into 回一个框：收着的组的组头、或空档在可见范围外时可见范围的边上），这时落到那里并收起（浮层带
 *  data-dropping="into"）；播完（或不播）调 landed。transition 为 null（减少动态效果）时不播。 */
export function dropAnimation(
  transition: Motion | null,
  landed: () => void,
  into: (node: HTMLElement) => { top: number; left: number } | null,
): DropAnimationFunction {
  return ({ active, dragOverlay, transform }) => {
    if (!transition) {
      landed();
      return;
    }
    const fold = into(active.node);
    const to = fold ?? active.rect;
    const final = { ...transform, x: transform.x - (dragOverlay.rect.left - to.left), y: transform.y - (dragOverlay.rect.top - to.top), scaleX: 1, scaleY: 1 };
    const frames = [{ transform: CSS.Transform.toString(transform) }, { transform: CSS.Transform.toString(final) }];
    // 浮层的略放大、阴影同时收回（落到收着的组：同时淡掉）；落点那一项在动画播完前不显示（不会一份在落点、一份在路上）
    active.node.dataset.dragLanding = "";
    dragOverlay.node.dataset.dropping = fold ? "into" : "";
    const done = () => {
      landed();
      delete active.node.dataset.dragLanding;
    };
    if (frames[0]!.transform === frames[1]!.transform && !fold) {
      done();
      return;
    }
    const animation = dragOverlay.node.animate(frames, { duration: transition.duration, easing: transition.easing, fill: STAY });
    startNow(animation);
    return new Promise<void>((resolve) => {
      animation.onfinish = () => {
        done();
        resolve();
      };
    });
  };
}

// ---------------------------------------------------------------- 拖动区
type AreaProps = {
  children: ReactNode;
  /** 拿着 active 能不能放到 target 上（排序：同一列；侧栏对话：别的公司组 / 最近）。 */
  accepts: (active: DragData, target: DragData) => boolean;
  /** 放下了（落在能放的地方、且不是原位）。排序：放下当时调（新顺序与落位动画同一次提交）；在组之间移动：落位动画播完才调，
   *  回的 Promise（移动请求）做完前整列仍跟着动（请求失败、调用方退回原组时也带过渡）。 */
  onDrop: (active: DragData, target: DragData) => unknown;
  /** 在组之间移动：空档该在哪一组（null = 原来那组）。调用方据此把这一项按排序方式排进那一组（收着的组不排，只高亮组头）；
   *  放下后到落位播完、onDrop 调过之后才回 null。 */
  onPlace?: (active: DragData, target: DragData | null) => void;
  /** 拿起 / 结束（放下或取消）：侧栏要在拖对话时临时显示空的「最近」、多选时让其余选中的行变淡。 */
  onActive?: (active: DragData | null) => void;
  /** 在组之间移动时整列一起滑的各项（侧栏的行、组头、「显示更多」），见 flowLayout。 */
  flow?: string;
  texts: DragTexts;
  /** 手机长按：hold = 按住就拿起；move = 按住后再动才拿起（按住不动留给长按菜单）。 */
  touch?: "hold" | "move";
};

/** 空档此刻在哪一组（组的拖动编号；null = 没在组之间移动或在原组）：收着的组据此高亮组头。 */
const PlacedContext = createContext<string | null>(null);

/** 一块共用拖动的区域：传感器、碰撞（只认能放的地方）、键盘移动、限位、浮层与放下动画、读屏播报、组之间移动的空档与整列滑动。 */
export const DragArea: FC<AreaProps> = ({ children, accepts, onDrop, onPlace, onActive, flow, texts, touch = "hold" }) => {
  const latest = useRef({ accepts, onDrop, onPlace, onActive });
  latest.current = { accepts, onDrop, onPlace, onActive };
  const transition = useMemo(motion, []);

  // 拖动中要用的：拿起时的位置（浮层固定在那里，位移从它算）、能放的各处与原来那一项的节点（限位用）
  const first = useRef<ClientRect | null>(null);
  const stash = useRef<{ containers: DroppableContainers | null; node: HTMLElement | null }>({ containers: null, node: null });

  // 在组之间移动：空档所在的组（null = 原组）、落位后才交的放下、整列跟着动的时段（拿起到请求做完）
  const placedRef = useRef<DragData | null>(null);
  const [placed, setPlaced] = useState<DragData | null>(null);
  // 拿起时的那份说明：在组之间移动的那一项会换组重挂（侧栏的行按所在的组给 group），拖动中一直按拿起时的认（原来那组不变）
  const lifted = useRef<DragData | null>(null);
  const activeOf = (active: WithData & { id: string | number }) =>
    active && lifted.current?.id === active.id ? lifted.current : dataOf(active);
  const landing = useMemo(() => new Landing(), []);
  const [busy, setBusy] = useState(0);
  const settleAfter = (transition?.duration ?? 0) + 50;
  const hold = () => {
    let held = true;
    setBusy((n) => n + 1);
    return () => {
      if (held) setTimeout(() => setBusy((n) => n - 1), settleAfter);
      held = false;
    };
  };
  const release = useRef<(() => void) | null>(null);
  const place = (active: DragData, target: DragData | null) => {
    if ((placedRef.current?.id ?? null) === (target?.id ?? null)) return;
    placedRef.current = target;
    setPlaced(target);
    latest.current.onPlace?.(active, target);
  };

  const allowed = (active: DragData, entry: DroppableContainer, activeId: string | number) => {
    const target = dataOf(entry);
    if (!target) return false;
    if (entry.id === activeId) return !!active.list;
    return latest.current.accepts(active, target) || isHome(active, target);
  };

  const collisions = useMemo<CollisionDetection>(
    () => (args) => {
      const active = activeOf(args.active);
      if (!active) return [];
      const droppableContainers = args.droppableContainers.filter((entry) => allowed(active, entry, args.active.id));
      if (active.list) return closestCenter({ ...args, droppableContainers });
      // 在组之间移动：指针高度（键盘拖时是浮层中心）落在哪一组；横向按浮层（只在本列，指针偏出列外照样认）
      const rect = args.collisionRect;
      const pointerCoordinates = { x: rect.left + rect.width / 2, y: args.pointerCoordinates?.y ?? rect.top + rect.height / 2 };
      return pointerWithin({ ...args, droppableContainers, pointerCoordinates });
    },
    [],
  );

  const coordinates = useMemo<KeyboardCoordinateGetter>(
    () => (event, args) => {
      const { context } = args;
      const active = context.active ? activeOf(context.active) : undefined;
      if (!active || !context.active) return undefined;
      const activeId = context.active.id;
      const enabled = () => context.droppableContainers.getEnabled().filter((entry) => allowed(active, entry, activeId));
      if (active.list) {
        const view = { getEnabled: enabled, get: (id: string | number) => context.droppableContainers.get(id) } as unknown as DroppableContainers;
        return sortableKeyboardCoordinates(event, { ...args, context: { ...context, droppableContainers: view } });
      }
      // 在组之间移动：↑ ↓ 跳到上一个 / 下一个组（含原来那组；浮层中心落进那一组的顶部）
      const codes: string[] = [KeyboardCode.Up, KeyboardCode.Down, KeyboardCode.Left, KeyboardCode.Right];
      if (!codes.includes(event.code)) return undefined;
      event.preventDefault();
      const rect = context.collisionRect;
      if (!rect || (event.code !== KeyboardCode.Up && event.code !== KeyboardCode.Down)) return undefined;
      const mid = rect.top + rect.height / 2;
      const targets = enabled()
        .map((entry) => ({ entry, box: context.droppableRects.get(entry.id) }))
        .filter((t): t is { entry: DroppableContainer; box: ClientRect } => !!t.box)
        .sort((a, b) => a.box.top - b.box.top);
      const here = targets.findIndex((t) => t.box.top <= mid && mid < t.box.top + t.box.height);
      const next =
        event.code === KeyboardCode.Down
          ? here >= 0 ? targets[here + 1] : targets.find((t) => t.box.top > mid)
          : here >= 0 ? targets[here - 1] : [...targets].reverse().find((t) => t.box.top + t.box.height <= mid);
      if (!next) return undefined;
      const to = next.box.top + Math.min(next.box.height, rect.height) / 2;
      return { x: args.currentCoordinates.x, y: args.currentCoordinates.y + (to - mid) };
    },
    [],
  );

  // 只能竖着拖，浮层上下边不出这一列（排序：这一列各项；组之间：能放的各组 + 原来那组）
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
        .filter((entry) => allowed(data, entry, active.id))
        .map((entry) => entry.node.current);
      // 原位也算在范围里（拖到别的组时原来那一组不是落点，但浮层从那里出发）；节点还没拿到的第一帧用拿起时的位置
      const span = spanOf([...nodes, node]);
      const own = node?.isConnected ? null : { top: start.top, bottom: start.top + start.height };
      if (!span && !own) return { ...transform, x: 0 };
      const list = { top: Math.min(span?.top ?? Infinity, own?.top ?? Infinity), bottom: Math.max(span?.bottom ?? -Infinity, own?.bottom ?? -Infinity) };
      return { ...transform, x: 0, y: clampToList(transform.y, { top: start.top, height: start.height }, list) };
    },
    [],
  );
  const modifiers = useMemo(() => [keep], [keep]);

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: MOUSE_START }),
    useSensor(HoldTouchSensor, { ...TOUCH_START, moveToLift: touch === "move" }),
    useSensor(KeyboardSensor, { coordinateGetter: coordinates, keyboardCodes: KEYS }),
  );
  const accessibility = useMemo(
    () => ({
      announcements: dragAnnouncements(texts, {
        accepts: (a, b) => latest.current.accepts(a, b),
        placed: () => placedRef.current,
        lifted: (active) => activeOf(active),
      }),
      screenReaderInstructions: { draggable: texts.instructions },
    }),
    [texts],
  );

  useEffect(
    () => () => {
      setDragging(false);
      landing.land(); // 卸下时还没交的放下照样交（移动请求照发）
    },
    [landing],
  );
  const finish = () => {
    setDragging(false);
    latest.current.onActive?.(null);
  };
  const landWait = (transition?.duration ?? 0) + 150;
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    finish();
    holdStill(stash.current.node, landWait);
    const from = activeOf(active);
    if (isMover(from)) {
      const done = release.current;
      release.current = null;
      // 空档已在去的那一组：落位播完再交给调用方（发请求），请求做完前整列仍跟着动；交过之后空档才回 null（调用方那时已按
      // 移过去的样子排好，同一处）。回到原组：落回原位，不交
      settleMove(from, placedRef.current, latest.current.accepts, {
        landing,
        wait: landWait,
        drop: (target) => latest.current.onDrop(from, target),
        clear: () => place(from, null),
        done,
      });
      return;
    }
    const to = dataOf(over);
    if (!from || !to || !over || over.id === active.id) return;
    if (!latest.current.accepts(from, to)) return;
    latest.current.onDrop(from, to);
  };
  return (
    <MotionContext.Provider value={transition}>
      <PlacedContext.Provider value={placed?.id ?? null}>
        <DndContext
          sensors={sensors}
          collisionDetection={collisions}
          modifiers={modifiers}
          accessibility={accessibility}
          onDragStart={({ active }) => {
            landing.land(); // 上一次的落位还没播完就又拿起：先交掉
            setDragging(true);
            const data = dataOf(active);
            lifted.current = data ?? null;
            if (isMover(data)) {
              placedRef.current = null;
              setPlaced(null);
              release.current?.();
              release.current = hold();
            }
            if (data) latest.current.onActive?.(data);
          }}
          onDragOver={({ active, over }) => {
            const from = activeOf(active);
            if (!isMover(from)) return;
            const target = placeOf(from, dataOf(over), latest.current.accepts);
            if (target !== undefined) place(from, target);
          }}
          onDragCancel={({ active }) => {
            finish();
            holdStill(stash.current.node, landWait);
            const from = activeOf(active);
            if (!isMover(from)) return;
            place(from, null); // 取消：空档回原位，浮层滑回去
            release.current?.();
            release.current = null;
          }}
          onDragEnd={onDragEnd}
        >
          {children}
          <Lifted stash={stash} transition={transition} landing={landing} placed={placedRef} />
          {flow && <Flow selector={flow} running={busy > 0} transition={transition} placed={placed?.id ?? null} />}
        </DndContext>
      </PlacedContext.Provider>
    </MotionContext.Provider>
  );
};

/** 在组之间移动的那段时间（拿起到请求做完）：整列跟着动（flowLayout）；空档换了组，各组的位置变了，重量能放的各处（碰撞按新位置算），
 *  空档在可见范围外就平滑滚进来（revealGap）。 */
const Flow: FC<{ selector: string; running: boolean; transition: Motion | null; placed: string | null }> = ({ selector, running, transition, placed }) => {
  const { active, activeNode, draggableNodes, droppableContainers, measureDroppableContainers } = useDndContext();
  const node = useRef<HTMLElement | null>(null);
  if (activeNode) node.current = activeNode;
  const containers = useRef(droppableContainers);
  containers.current = droppableContainers;
  const lifted = useRef({ active, draggableNodes });
  lifted.current = { active, draggableNodes };
  useLayoutEffect(() => {
    if (!running) return;
    measureDroppableContainers(containers.current.getEnabled().map((entry) => entry.id));
    // 换组重挂后的空档（这次提交里刚挂上，引用已更新）
    const { active: now, draggableNodes: nodes } = lifted.current;
    const gap = now ? nodes.get(now.id)?.node.current : null;
    if (gap?.isConnected) revealGap(gap, !!transition);
  }, [placed, running, measureDroppableContainers, transition]);
  useEffect(() => {
    const from = node.current;
    if (!running || !transition || !from?.isConnected) return;
    const root = (getScrollableAncestors(from, 1)[0] as HTMLElement | undefined) ?? (document.scrollingElement as HTMLElement | null) ?? document.body;
    return flowLayout(root, selector, transition);
  }, [running, selector, transition]);
  return null;
};

/** 排序的一列（同一个 DragArea 里可以有几列：侧栏的置顶、公司组）。 */
export const DragList: FC<{ id: string; items: string[]; children: ReactNode }> = ({ id, items, children }) => (
  <SortableContext id={id} items={items} strategy={verticalListSortingStrategy}>
    {children}
  </SortableContext>
);

// ---------------------------------------------------------------- 浮层
// 克隆出来的那一项只是样子：去掉会被别处按属性找到的标记（会话栏按 data-card 滚到卡片、侧栏按 data-row-id / data-roving
// 排键盘焦点与多选），整块 inert（不进 Tab、读屏不念）
const STRIP = [
  "id",
  "data-card",
  "data-row-id",
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

// 浮层里的样子在拿起时克隆一次（在组之间移动时那一项会换组重挂，不再重克隆）
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

const Lifted: FC<{
  stash: { current: { containers: DroppableContainers | null; node: HTMLElement | null } };
  transition: Motion | null;
  landing: Landing;
  placed: { current: DragData | null };
}> = ({ stash, transition, landing, placed }) => {
  const { active, activeNode, droppableContainers } = useDndContext();
  stash.current = { containers: droppableContainers, node: activeNode };
  const data = dataOf(active);
  // 放下：浮层从放手处滑到落点（同 dnd-kit 默认的落位，见 dropAnimation）。在组之间移动、去的那组收着（空档不在那一组里）：
  // 落到那一组（组头）并收起
  const drop = useMemo(
    () =>
      dropAnimation(transition, () => landing.land(), (node) => {
        const target = placed.current;
        const group = target ? stash.current.containers?.get(target.id)?.node.current : null;
        const fold = !!group && !group.contains(node);
        const rect = getClientRect(fold ? group! : node, { ignoreTransform: true });
        // 落点（空档或组头）在滚动区可见范围外（拖着时又滚走了）：落到可见范围的边上并收起，不飞出列表
        const shown = clampIntoView(rect, viewOf(node));
        return fold || shown !== rect ? shown : null;
      }),
    [transition, landing, placed, stash],
  );
  // 键盘拖动时浮层一格一格挪，也用动效变量过渡
  const keyboardMove = (event: Event | null) =>
    transition && event && "key" in event ? `transform ${transition.duration}ms ${transition.easing}` : undefined;

  const overlay = (
    <DragOverlay dropAnimation={drop} transition={keyboardMove} className="cc-drag-overlay">
      {active ? (
        <div className="cc-drag-float" data-kind={data?.kind} inert aria-hidden>
          <div className="cc-drag-lift">
            <Copy id={active.id} node={activeNode} />
            {(data?.count ?? 0) > 1 && <span className="cc-drag-count">{data!.count}</span>}
          </div>
        </div>
      ) : null}
    </DragOverlay>
  );
  // 挂到 body 上（见 cloneOf 上面的说明）；没有页面（服务端渲染的测试）时就地
  return typeof document !== "undefined" && document.body ? createPortal(overlay, document.body) : overlay;
};

// ---------------------------------------------------------------- 各项
type HandleOptions = {
  disabled?: boolean;
  /** 把手本来就是能聚焦的按钮（侧栏的行、组头开关）：不加拖动库的 role / tabIndex / 禁用标记，焦点仍归侧栏键盘管。 */
  ownFocus?: boolean;
  /** 从这些地方按下不拿起（行尾「···」、改名输入框）。 */
  skip?: string;
  /** 这一项是能放进去的一组、此刻收着（放不下空档）：空档要去这组时整块（就是组头）高亮，放下落到组头并收起。 */
  shut?: boolean;
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
    ...(listeners
      ? {
          // 能拖的把手：悬停出抓手光标（globals.css 的 [data-drag-handle]，各处同一条；不能拖时——搜索中、没存到服务端——不加）
          "data-drag-handle": "" as const,
          onMouseDown: (event: ReactMouseEvent) => !skipped(event) && listeners.onMouseDown?.(event),
          onTouchStart: (event: ReactTouchEvent) => !skipped(event) && listeners.onTouchStart?.(event),
          onKeyDown: (event: ReactKeyboardEvent) => listeners.onKeyDown?.(event),
          // 空格是拿起 / 放下：松开空格不再当成点击（不打开对话、不折叠组）
          onKeyUp: (event: ReactKeyboardEvent) => event.code === "Space" && event.preventDefault(),
        }
      : {}),
  };
}

/** 排序列表里的一项：node 给整项（位移、原位留空），handle 给把手（拿起的地方）。 */
export function useSortableItem(data: DragData, options: HandleOptions = {}) {
  const transition = useContext(MotionContext);
  const drag = useMemo(() => ({ drag: data }), [data.id, data.kind, data.label, data.list, data.group, data.count, data.session]);
  const sortable = useSortable({ id: data.id, data: drag, disabled: options.disabled, transition });
  const crossing = useContext(PlacedContext) === data.id && !!options.shut;
  return {
    node: {
      ref: sortable.setNodeRef,
      style: { transform: CSS.Translate.toString(sortable.transform), transition: sortable.transition } as CSSProperties,
      "data-drag-list": data.list,
      "data-drag-source": sortable.isDragging ? "gap" : undefined,
      "data-drop-target": crossing ? "" : undefined,
    },
    handle: handleOf(sortable, options),
    dragging: sortable.isDragging,
  };
}

/** 在组之间移动的一项（侧栏对话拖到别的公司；data.group 是所在的组）：同排序项一样原位留空档、浮层跟手；空档去哪一组由
 *  DragArea 的 onPlace 交给调用方排（组里的先后由排序方式定）。data-drag-id 让换组重挂的同一项接着滑（flowLayout）。 */
export function useDragItem(data: DragData, options: HandleOptions = {}) {
  const drag = useMemo(() => ({ drag: data }), [data.id, data.kind, data.label, data.list, data.group, data.count, data.session]);
  const draggable = useDraggable({ id: data.id, data: drag, disabled: options.disabled });
  return {
    node: { ref: draggable.setNodeRef, "data-drag-id": data.id, "data-drag-source": draggable.isDragging ? "gap" : undefined },
    handle: handleOf(draggable, options),
    dragging: draggable.isDragging,
  };
}

/** 只能放不能拖的一组（侧栏的「最近」）：shut = 此刻收着，空档要去这组时整块（就是组头）高亮。 */
export function useDropTarget(data: DragData, shut = false) {
  const drag = useMemo(() => ({ drag: data }), [data.id, data.kind, data.label, data.group]);
  const droppable = useDroppable({ id: data.id, data: drag });
  const placed = useContext(PlacedContext) === data.id;
  return { ref: droppable.setNodeRef, "data-drop-target": placed && shut ? "" : undefined };
}

/** Alt+↑ / Alt+↓ 挪一格（会话栏卡片、侧栏公司组头、置顶对话）；挪完焦点留在原来的按钮上。是这两个键回 true。 */
export function altMove(event: ReactKeyboardEvent, move: (delta: -1 | 1) => void): boolean {
  if (!event.altKey || event.metaKey || event.ctrlKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return false;
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
