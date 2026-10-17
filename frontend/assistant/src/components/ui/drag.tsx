// 共用拖动（用户 10-15 晚：拖动要「全部参考视频，非常丝滑」；10-16：「所有拖拽的地方要改统一规则和效果」、「对话的拖动效果怎么没和
// 其它拖动效果统一呢」。参考 Claude 桌面版右侧栏拖卡片的录屏 IMG_5192）。会话栏卡片（session-panel.tsx）、侧栏公司组排序、置顶对话
// 排序、组里的对话排序与拖进已置顶（thread-list.tsx）都走这一份；修一处就在这里修。
//
// 第 12 轮起是自写的指针排序（原来基于 dnd-kit；docs/work/2026-10-17-drag-engine.md）。A14 真机 trace：拿起 122–168ms、落定 48–60ms
// 主要是拖动库自己的逐项开销——拿起时所有排序项都订阅拖动上下文、全部重画，每个落点各量一次滚动祖先、各挂尺寸观察，浮层挂载与
// body 上的观察器还要同步量框。现在：
//   · 各项只订阅「自己是不是被拖的那一项」（useSyncExternalStore 按编号），拿起、落定只重画被拖的那一项；
//   · 拿起时一次性量好能放的各项（版面位置），拖动中跟手、碰撞、限位、让位都按这份算（滚动只加滚动差），不再逐项量；
//   · 让位位移直接写到各项节点上（带过渡），浮层位置直接写行内 transform：拖动中每一下都不经过 React；
//   · 浮层、读屏播报区是自己建的节点（浮层在 body 下常驻的拖动层里，播报区在拖动区挂上时建好常驻），拖动中 body 下不增删节点。
//
//   拿起   鼠标按住动 3px（点一下照样是点）；手机按住 250ms 且没挪出 5px 就拿起（没到时间就动 = 在滚动）；键盘 空格。能拖的把手带
//          data-drag-handle，悬停出抓手光标（globals.css 同一条），鼠标拖动中整页 grabbing（挡板，拿起那一帧画完再盖）。侧栏的行与组头
//          长按另有菜单：拿起后手指一动（离按下处超过 10px，同长按菜单作废的距离）就是拖动，不出菜单；一直不动按到菜单的时间（500ms，
//          hooks/use-long-press.ts）才出菜单，菜单出来后手指再动就收起菜单、接着拖。拿起后没动、没到菜单时间就松手是点按：照样打开对话
//          / 折叠组。浮层在抽屉、手机会话栏（50）之上、菜单（60）之下：按住不动出来的长按菜单不被拿起的那一项盖住。
//   浮起   拿起的那一项换成浮层（克隆原来那一项的样子）：弹层底、弹出阴影、略透明、略放大，实时跟手。原位留空（空档）。排序（一列里调
//          先后）：其余各项拖动中就带过渡滑开让位（同 dnd-kit 的竖排让位：原位与落点之间的各项挪「拖动项高 + 间距」，空档挪到落点）。
//          拖进另一列（侧栏对话拖进已置顶）：指针进了那一列的整块落点（useDropZone）才算进去，调用方（onOver）把这一项挪进那一列、排在
//          指着的位置，原来那一列的空档合上；指针回到原来那一列的整块里就挪回去。有整块落点时指针不在任何一块能放的整块里（别的公司组、
//          两组之间）就没有落点：空档回原位，放下滑回原位。两列之间整列各行、组头的位置变了都带同样的过渡滑过去（flowLayout，拖动中
//          关掉滚动锚定），版面变了的下一帧重量一次。那一列收着（放不下空档）时整块（就是组头）高亮、原位空档留着。
//   放下   松手那一下只做一件事：浮层从放手处起播落位（Web Animations 只动 transform；动效变量），落点在松手时量好（空档此刻的位置，
//          加上它让位的位移）；结束拖动（调用方存顺序、整列重排，与各项去掉让位、浮层卸下同一次提交）等落位播完才做（ReleaseFirst）。
//          落到收着的那一列就落到组头并收起；落点在滚动区可见范围外（拖着时又滚走了）就落到可见范围的边上并收起；没落点就滑回原位；
//          浮层被限位停在落点上（拖过首尾）也播这一段；真的没挪过（拿起后原地放下、长按出菜单后松手）当场结束。
//          取消（Esc；手机的 touchcancel、转屏、切到后台）同放下一样先滑回（settleBack），取消等滑回播完。拖进了另一列以后取消：Esc
//          那一下只按拿起时的位置起播浮层滑回，这一帧画完以后才请调用方把它挪回原来那一列（onReturn），重挂出来那次提交里核对落点、
//          对不上从此刻改滑（homing）。拿起过、按键 / 手指还没松开就取消了：这次手势松开那一下不当点击（blockReleaseClick）。
//   范围   只在本列竖着拖：浮层上下边不出能放的范围（这一列第一项顶边到最后一项底边；能拖进的另一列的整块也算）。浮层是 fixed、
//          不撑大滚动区，拖到边上自动滚动（滚动区上下 20% 的边缘区，越深越快，最快每 5ms 10px，只朝指针动过的方向）只到列表首尾。
//          浮层停在只能高亮组头的落点上（那一列收着）时变半透明（data-peek），透出组头的高亮，离开就恢复。
//   键盘   空格拿起、方向键移动（只在本列里，不拖进另一列）、空格（或回车、Tab）放下、Esc 取消，三种语言读屏播报（文案由调用方给）。
//          ↑ ↓ 按这一列的先后一格一格换瞄准的那一项，落点就是它（keyboardAim / keyboardCollision），浮层挪到挪过去以后它在的位置；
//          目标落在滚动区下半截（往上时上半截）就改为立即滚动滚动区（KEYBOARD_SCROLL），快按每一下都动。
//          Esc 只取消拖动：不关浮着的会话栏、不清侧栏多选（lib/layers.ts 的 isDragEscape）。焦点一直在被拖的那一项的把手上；拖动中
//          方向键只归拖动（handleOf 标成已处理），放下 / 取消后焦点留在移动后的那一行（keepFocusOn）。
//   读屏   每个拖动区一份说明（aria-describedby）、一份 assertive 播报区（拿起、经过、放下、取消），Alt+↑/↓ 另一份播报区。
//   动效   时长、缓动取 globals.css 的 --cc-dur / --cc-ease；系统或设置要求减少动态效果时不播过渡。
//
// 样式：globals.css 的 .cc-drag-*、[data-drag-source]、[data-drop-target]；这里只有让位位移与过渡（applyShift 直接写节点）、浮层的位置
// 是行内样式（整列滑动用 Web Animations，不写行内样式）。

import { createPortal, flushSync } from "react-dom";
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
  useSyncExternalStore,
} from "react";
import { cssMs } from "@/lib/hover-scroll";
import { LONG_PRESS_MS, LONG_PRESS_SLOP } from "@/hooks/use-long-press";
import { isDragging, markDragMoved, releaseDrag, setDragShield, setDragging } from "@/lib/layers";

// ---------------------------------------------------------------- 拖的是什么
/** 一项拖动的说明：kind 是哪一种（card / company / pin / row / pins），label 是读屏念的名字，list 是排序的那一列（一列整块的落点 zone
 *  是它代表的那一列），group 是侧栏所在的公司（"" = 最近），count 一起拖的条数（多选），session 对话编号；zone 是一列整块的落点
 *  （useDropZone），shut 是它此刻收着（放不下空档）。 */
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

/** 一块的框（视口坐标）。 */
export type ClientRect = { top: number; left: number; width: number; height: number; right: number; bottom: number };
type Coordinates = { x: number; y: number };

type WithData = { data: { current?: Record<string, unknown> | null } } | null | undefined;
/** 拖动区里登记的一项或一块（读屏播报、落点筛选看的那一份）：data.current.drag 是说明，sortable 是它此刻所在的那一列与位置。 */
type Seen = { id: string | number } & NonNullable<WithData>;

export function dataOf(entry: WithData): DragData | undefined {
  return (entry?.data.current?.drag as DragData | undefined) ?? undefined;
}

/** 这一项此刻排在哪一列（它所在的 DragList；拖进另一列的那一项重挂在那一列里）；不是排序项回它说明里的 list。 */
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

/** 拿起、经过、放下、取消各念什么（回 undefined = 不改播报区，留着上一句）。 */
export type Announcements = {
  onDragStart: (event: { active: Seen }) => string | undefined;
  onDragOver: (event: { active: Seen; over: Seen | null }) => string | undefined;
  onDragEnd: (event: { active: Seen; over: Seen | null }) => string | undefined;
  onDragCancel: (event: { active: Seen }) => string | undefined;
};

function position(over: WithData): { at: number; total: number } | null {
  const sortable = over?.data.current?.sortable as { index: number; items: unknown[] } | undefined;
  return sortable ? { at: sortable.index + 1, total: sortable.items.length } : null;
}

/** 读屏播报：名字用 label；排序时位置按这一列（看得见的）算；拖进另一列（拿着的那一项此刻排在别的列里，或指着那一列收着的整块）念
 *  那一列的名字（zoneLabel：一列的编号 → 它整块落点的名字）。放下按实际结果念：落在别的列里念 droppedIn；排序的念放到第几位；落回
 *  原位（经过的是它自己、还在原来那一列）或没有落点念取消，同放下的处理里动没动。
 *
 *  拿起的那一刻紧跟着算一次落点，经过的就是拿起的这一项自己（它在原位、离自己最近）：这一次不念，留着「已拿起」（测试窗口第 5 轮：
 *  公司组头空格拿起念成「移到第 3 位，共 4 项」，卡片、置顶同样）。挪开过以后再回到原位照常念。 */
export function dragAnnouncements(
  texts: DragTexts,
  lookup?: { lifted?: (active: Seen) => DragData | undefined; zoneLabel?: (list: string) => string | undefined },
): Announcements {
  const name = (entry: WithData) => dataOf(entry)?.label ?? "";
  let moved = false; // 这一次拖动挪开过原位没有（拿起时清零）
  const say = (active: Seen, over: Seen | null, end: boolean) => {
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
/** 键盘拖动认的键（KeyboardEvent.code）。 */
export const KeyboardCode = {
  Space: "Space",
  Down: "ArrowDown",
  Right: "ArrowRight",
  Left: "ArrowLeft",
  Up: "ArrowUp",
  Escape: "Escape",
  Enter: "Enter",
  Tab: "Tab",
} as const;
// 空格拿起，空格 / 回车 / Tab 放下，Esc 取消（回车不拿起：对话行、组头上回车是打开 / 折叠）
const KEYS: KeyboardCodes = {
  start: [KeyboardCode.Space],
  cancel: [KeyboardCode.Escape],
  end: [KeyboardCode.Space, KeyboardCode.Enter, KeyboardCode.Tab],
};
// 键盘拖动时目标落在滚动区下半截（往上拖时上半截），不挪浮层、改成滚动滚动区让目标滚到浮层下面。平滑滚动的话下一下按在滚动
// 途中，从滚到一半的位置再加一格，前一下就丢了（测试窗口：间隔约 30ms 连按时一些按键没反应）。auto：照滚动区的 CSS（各滚动区都
// 没设平滑滚动），即立即滚到位，每一下都从滚好的位置算，快按每一下都动。不用 "instant"：较晚才进规范，旧的 WebView 不认这个值时
// scrollTo 会报错
export const KEYBOARD_SCROLL: ScrollBehavior = "auto";

/** 放下时先交给 beforeEnd（先播落位，播完才真正结束拖动，见 ReleaseFirst）；没给就照常立即结束。beforeCancel 同理管取消
 *  （先滑回、再取消，见 settleBack）。 */
type EndOptions = { beforeEnd?: (end: () => void) => void; beforeCancel?: (cancel: () => void) => void };
type HoldOptions = { delay: number; tolerance: number } & EndOptions;
type KeyboardCodes = { start: string[]; cancel: string[]; end: string[] };
type Listen = [EventTarget, string, EventListener, AddEventListenerOptions | undefined];

/** 一种输入方式（鼠标、手机长按、键盘）的一次按下：拿起前判断算不算拖动，拿起后把移动、放下、取消交给拖动区。 */
export type SensorProps<O> = {
  /** 按下的那一项的拖动编号。 */
  active: string;
  /** 按下的那个原生事件。 */
  event: Event;
  options: O;
  onStart: (coordinates: Coordinates) => void;
  onMove: (coordinates: Coordinates) => void;
  onEnd: () => void;
  onCancel: () => void;
  /** 没拿起就结束了（点一下、拿起前就动了、拿起前 Esc）。 */
  onAbort: (active: string) => void;
  onPending?: (...args: unknown[]) => void;
};
export interface SensorInstance {
  autoScrollEnabled: boolean;
}
export type Sensor<O> = new (props: SensorProps<O>) => SensorInstance;

const stopClick = (event: Event) => {
  event.stopPropagation();
  event.preventDefault();
};
const stopPropagation = (event: Event) => event.stopPropagation();
const clearSelection = () => window.getSelection?.()?.removeAllRanges();
const prevent = (event: Event) => event.preventDefault();

/** 事件目标所在的页面（不是页面上的节点时用当前页面）。 */
function ownerDocumentOf(target: EventTarget | null | undefined): Document {
  const node = target as (Node & { nodeType?: number }) | null | undefined;
  if (node && typeof node === "object" && typeof node.nodeType === "number") {
    if (node.nodeType === 9) return node as unknown as Document;
    if (node.ownerDocument) return node.ownerDocument;
  }
  return document;
}

/** 一组挂在同一处的监听，一次全摘。 */
class Listeners {
  private readonly target: EventTarget | null;
  private list: Array<[string, EventListener, AddEventListenerOptions | undefined]> = [];
  constructor(target: EventTarget | null) {
    this.target = target;
  }
  add(type: string, run: EventListener, options?: AddEventListenerOptions) {
    this.target?.addEventListener(type, run, options);
    this.list.push([type, run, options]);
  }
  readonly removeAll = () => {
    for (const [type, run, options] of this.list) this.target?.removeEventListener(type, run, options);
    this.list = [];
  };
}

function coordinatesOf(event: Event): Coordinates | null {
  const touch = (event as TouchEvent).touches?.[0] ?? (event as TouchEvent).changedTouches?.[0];
  if (touch) return { x: touch.clientX, y: touch.clientY };
  const mouse = event as MouseEvent;
  return typeof mouse.clientX === "number" ? { x: mouse.clientX, y: mouse.clientY } : null;
}

export type MouseSensorOptions = { activationConstraint?: { distance: number } };

/** 鼠标：在把手上按下（右键除外），从按下处移动超过 distance 才拿起（没动够就松手照常是一次点击，拿起前移动多远都不会作废）。拿起后
 *  松手紧跟着的那次点击在 document 捕获阶段停止传播（摘监听晚 50ms：系统的 click 紧跟在 mouseup 后面）；拖动中不选中文字、不起
 *  系统拖放、不出右键菜单；Esc、窗口 resize、切到后台都取消。按下以后的移动、松开挂在页面上（拖着的那一项可能重挂）。 */
export class MouseSensor implements SensorInstance {
  autoScrollEnabled = true;
  activated = false;
  private readonly props: SensorProps<MouseSensorOptions>;
  private readonly doc: Document;
  private readonly listeners: Listeners;
  private readonly documentListeners: Listeners;
  private readonly windowListeners: Listeners;
  private readonly initial: Coordinates;

  constructor(props: SensorProps<MouseSensorOptions>) {
    this.props = props;
    this.doc = ownerDocumentOf(props.event.target);
    this.listeners = new Listeners(this.doc);
    this.documentListeners = new Listeners(this.doc);
    this.windowListeners = new Listeners(typeof window !== "undefined" ? window : null);
    this.initial = coordinatesOf(props.event) ?? { x: 0, y: 0 };
    this.listeners.add("mousemove", this.move, { passive: false });
    this.listeners.add("mouseup", this.end);
    this.windowListeners.add("resize", this.cancel);
    this.windowListeners.add("dragstart", prevent);
    this.windowListeners.add("visibilitychange", this.cancel);
    this.windowListeners.add("contextmenu", prevent);
    this.documentListeners.add("keydown", this.key);
    if (props.options.activationConstraint) props.onPending?.(props.active);
    else this.start();
  }

  private readonly unselect = () => (this.doc.getSelection?.() ?? window.getSelection?.())?.removeAllRanges();

  private readonly start = () => {
    this.activated = true;
    // 拿起后松手的那一下不算点击（不打开对话、不折叠组、不切换多选）；拖动中不选中文字
    this.documentListeners.add("click", stopPropagation, { capture: true });
    this.unselect();
    this.documentListeners.add("selectionchange", this.unselect);
    this.props.onStart(this.initial);
  };

  private readonly move = (event: Event) => {
    const at = coordinatesOf(event) ?? { x: 0, y: 0 };
    if (!this.activated) {
      const distance = this.props.options.activationConstraint?.distance ?? 0;
      if (Math.hypot(at.x - this.initial.x, at.y - this.initial.y) > distance) this.start();
      return;
    }
    if (event.cancelable) event.preventDefault();
    this.props.onMove(at);
  };

  private detach() {
    this.listeners.removeAll();
    this.windowListeners.removeAll();
    setTimeout(this.documentListeners.removeAll, RELEASE_CLICK_MS);
  }

  private readonly end = () => {
    this.detach();
    if (!this.activated) this.props.onAbort(this.props.active);
    this.props.onEnd();
  };

  private readonly cancel = () => {
    this.detach();
    if (!this.activated) this.props.onAbort(this.props.active);
    this.props.onCancel();
  };

  private readonly key = (event: Event) => {
    if ((event as KeyboardEvent).code === KeyboardCode.Escape) this.cancel();
  };
}

export type KeyboardSensorOptions = {
  keyboardCodes?: KeyboardCodes;
  scrollBehavior?: ScrollBehavior;
  /** 拖动中按了放下、取消以外的键（方向键等）：拖动区挪一格。 */
  step?: (event: KeyboardEvent) => void;
};

/** 键盘：按下就拿起；之后的键在页面上收（下一个任务里才开始收，拿起的那一下空格不算）：放下的键放下、取消的键取消，其余交给 step；
 *  窗口 resize、切到后台取消。 */
export class KeyboardSensor implements SensorInstance {
  autoScrollEnabled = false;
  private readonly props: SensorProps<KeyboardSensorOptions>;
  private readonly listeners: Listeners;
  private readonly windowListeners: Listeners;
  private detached = false;

  constructor(props: SensorProps<KeyboardSensorOptions>) {
    this.props = props;
    this.listeners = new Listeners(ownerDocumentOf(props.event.target));
    this.windowListeners = new Listeners(typeof window !== "undefined" ? window : null);
    props.onStart({ x: 0, y: 0 });
    this.windowListeners.add("resize", this.cancel);
    this.windowListeners.add("visibilitychange", this.cancel);
    setTimeout(() => !this.detached && this.listeners.add("keydown", this.keydown));
  }

  private readonly keydown = (event: Event) => {
    const key = event as KeyboardEvent;
    if (typeof key.code !== "string") return;
    const codes = this.props.options.keyboardCodes ?? KEYS;
    if (codes.end.includes(key.code)) {
      key.preventDefault();
      this.detach();
      this.props.onEnd();
      return;
    }
    if (codes.cancel.includes(key.code)) {
      this.cancel(key);
      return;
    }
    this.props.options.step?.(key);
  };

  private readonly cancel = (event?: Event) => {
    event?.preventDefault?.();
    this.detach();
    this.props.onCancel();
  };

  private detach() {
    this.detached = true;
    this.listeners.removeAll();
    this.windowListeners.removeAll();
  }
}

/** 手机长按拿起：按住 delay 且没挪出 tolerance 就拿起（没到时间就动 = 在滚动，交还给页面）。拿起后手指离按下处动出 TOUCH_DRAG_SLOP
 *  才算在拖：记到 lib/layers.ts（markDragMoved），长按菜单据此不出、已经出来的收起（hooks/use-long-press.ts）；按住不动到菜单时间，
 *  菜单照旧出来，拿起的那一项留着，手指再动就接着拖。拿起后滑动不再滚页面；松手先交给 beforeEnd（先播落位）。
 *  拿起后一直没动、没到菜单时间就松手：是点按（审查：动作慢的人点按常超过 250ms，原来这一下被吞掉，对话打不开、组不折叠）。
 *  取消拖动（浮层落回原位），这一下照样当点击：系统自己的 click 不要（拿起后的 touchmove 都拦了，各平台据此吞不吞 click 不一），
 *  在按下的那个元素上补一个。移动、松开挂在按下的那个元素上（它从页面上卸下了，触摸事件照样发给它）。 */
export class HoldTouchSensor implements SensorInstance {
  autoScrollEnabled = true;

  /** iOS Safari：页面上要有一个非被动的 touchmove 监听，后来挂上的 preventDefault 才管用。拖动区挂着期间一直留着。 */
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
    // 松手后紧跟着的 click 还要拦下：稍后再摘。点按不拦（上面已经摘了），下面补一个
    if (!tap) window.setTimeout(() => clicks.forEach(off), RELEASE_CLICK_MS);
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

// 松开以后接着拦点击的时长（系统的 click 紧跟在 mouseup / touchend 后面）
const RELEASE_CLICK_MS = 50;

/** 拿起过的指针拖动，按键 / 手指还没松开就结束了（Esc 取消；转屏、切到后台）：这次按下到松开整个手势里拿起过，松开那一下
 *  不当点击——不打开对话、不折叠组；剩下这一段按着移动也同拖动中一样不选中文字、不起系统拖放（传感器取消时把这两道一起摘了，
 *  按着往回移会选中经过的行字，下次按在选中的字上一动就成了系统拖文字）。一直拦到松开（mouseup / pointerup；手机 touchend /
 *  touchcancel）后 RELEASE_CLICK_MS；又按下了（下一次手势）当场不拦。测试窗口第 11 轮重测 B 段：鼠标拿起对话行拖开 25px、Esc 取消、
 *  回到原行上松开，就打开了这段对话（浮层不接指针、挡板在起播滑回那一帧画完就撤，之后松开落在原来那一行上，按下与松开在同一行，
 *  系统照常补一次点击）。 */
export function blockReleaseClick(kind: "mouse" | "touch") {
  if (typeof document === "undefined" || typeof document.addEventListener !== "function") return;
  const doc = document;
  // 页面已经切到后台（切标签页、窗口被挡住时按 visibilitychange 取消）：之后的松开落在别的页面 / 应用里，本页收不到
  // mouseup / touchend，拦了就一直撤不掉——切回来键盘点按钮被吞、输入框打字光标被清（第 11 轮重测 B 段交叉审查）
  if (doc.visibilityState === "hidden") return;
  const capture = { capture: true };
  // 自己的一份处理函数：同一个函数、同样的捕获再挂一次，浏览器只认一份。手机长按传感器拿起时也挂了 stopClick / clearSelection，
  // 它取消后 50ms 摘掉自己那份，用同一个函数的话这里的也一起没了
  // 只拦指针松开补的那次点击：键盘回车 / 空格激活的点击（detail 为 0）照常
  const block = (event: Event) => {
    if ((event as MouseEvent).detail === 0) return;
    stopClick(event);
  };
  // 焦点在输入框里（打字、选字）不清选区：这里只防按着往回移时选中经过的行字
  const unselect = () => {
    if (!typing()) clearSelection();
  };
  const noDrag = (event: Event) => prevent(event);
  const release = kind === "mouse" ? ["mouseup", "pointerup"] : ["touchend", "touchcancel"];
  // 鼠标兜底：松开那一下没传回本页（在别的应用里松开等），之后再动鼠标时左键已经不是按着的，就当已经松开
  const moves = kind === "mouse" ? ["mousemove", "pointermove"] : [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  const off = () => {
    if (timer) clearTimeout(timer);
    doc.removeEventListener("click", block, capture);
    doc.removeEventListener("selectionchange", unselect);
    doc.removeEventListener("dragstart", noDrag, capture);
    doc.removeEventListener("pointerdown", off, capture);
    doc.removeEventListener("visibilitychange", hidden);
    for (const type of release) doc.removeEventListener(type, released, capture);
    for (const type of moves) doc.removeEventListener(type, moved, capture);
  };
  const released = () => {
    timer ??= setTimeout(off, RELEASE_CLICK_MS);
  };
  // 拦着的时候切到后台（先 Esc、再按着切走）：松开回不到本页，当场撤
  const hidden = () => {
    if (doc.visibilityState === "hidden") off();
  };
  const moved = (event: Event) => {
    const buttons = (event as MouseEvent).buttons;
    if (typeof buttons === "number" && (buttons & 1) === 0) released();
  };
  doc.addEventListener("click", block, capture);
  doc.addEventListener("selectionchange", unselect);
  doc.addEventListener("dragstart", noDrag, capture);
  doc.addEventListener("pointerdown", off, capture);
  doc.addEventListener("visibilitychange", hidden);
  for (const type of release) doc.addEventListener(type, released, capture);
  for (const type of moves) doc.addEventListener(type, moved, capture);
}

/** 焦点在可输入的地方（输入框、文本框、可编辑区）。 */
function typing(): boolean {
  const el = typeof document !== "undefined" ? (document.activeElement as HTMLElement | null) : null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable === true;
}

/** 鼠标、键盘传感器加一道：放下时先交给 options.beforeEnd（先播落位，播完才真正结束拖动）；给了 options.beforeCancel 时取消也先
 *  交给它（Esc、转屏、切到后台：先滑回）。release（鼠标）：拿起后按键还没松开就取消了，松开那一下不当点击（blockReleaseClick）。
 *  拿起的条件、移动都不变。 */
function landFirst<O extends object>(Base: Sensor<O>, release?: "mouse"): Sensor<O & EndOptions> {
  const Landed = class extends (Base as unknown as new (props: SensorProps<O>) => SensorInstance) {
    constructor(props: SensorProps<O & EndOptions>) {
      const { beforeEnd, beforeCancel } = props.options;
      // 鼠标传感器拿起后记 activated（没拿起时按下就取消——拿起前按 Esc——照常是点击）
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

// ---------------------------------------------------------------- 量与范围
/** 拿起的那一项在这一列的上下边之间（视口坐标）：y 是浮层相对拿起时的位移，card 是拿起时的位置，list 是这一列此刻的上下边。
 *  比整列还高（不会发生）时不动。 */
export function clampToList(y: number, card: { top: number; height: number }, list: { top: number; bottom: number }): number {
  const min = list.top - card.top;
  const max = list.bottom - (card.top + card.height);
  return Math.min(Math.max(y, min), Math.max(min, max));
}

function boundsOf(el: Element): ClientRect {
  const { top, left, width, height } = el.getBoundingClientRect();
  return { top, left, width, height, right: left + width, bottom: top + height };
}

/** 计算后的 transform（matrix / matrix3d）→ 位移与缩放；none 回 null。 */
function shapeOf(value: string): Shape | null {
  const flat = value.startsWith("matrix3d(");
  if (!flat && !value.startsWith("matrix(")) return null;
  const n = value.slice(flat ? 9 : 7, -1).split(/,\s*/).map(Number);
  return flat ? { x: n[12]!, y: n[13]!, scaleX: n[0]!, scaleY: n[5]! } : { x: n[4]!, y: n[5]!, scaleX: n[0]!, scaleY: n[3]! };
}

/** 一项去掉 transform（排序让位写在行内的 translate3d）以后的框。 */
function untransformedRect(node: HTMLElement): ClientRect {
  const rect = boundsOf(node);
  const { transform, transformOrigin } = getComputedStyle(node);
  const shape = transform ? shapeOf(transform) : null;
  if (!shape) return rect;
  const origin = (transformOrigin || "0 0").split(" ");
  const left = rect.left - shape.x - (1 - shape.scaleX) * Number.parseFloat(origin[0] ?? "0");
  const top = rect.top - shape.y - (1 - shape.scaleY) * Number.parseFloat(origin[1] ?? origin[0] ?? "0");
  const width = shape.scaleX ? rect.width / shape.scaleX : rect.width;
  const height = shape.scaleY ? rect.height / shape.scaleY : rect.height;
  return { top, left, width, height, right: left + width, bottom: top + height };
}

/** 一项的版面位置（视口坐标）：去掉排序让位的位移（行内 transform）和整列滑动的位移（flowLayout 用 Web Animations 播的 translate）。
 *  拖进另一列以后整列正在滑的那 0.2 秒里重量落点、松手量落点，量到的是版面位置，不是滑动起点（审查第 11 轮 B14）。 */
export function layoutRect(node: HTMLElement): ClientRect {
  const rect = untransformedRect(node);
  const slid = typeof getComputedStyle === "function" ? shiftOf(node) : 0;
  return slid ? { ...rect, top: rect.top - slid, bottom: rect.bottom - slid } : rect;
}

/** 拿起时量各项（这时没有在滑的、只可能带着行内的让位位移）：只读框与行内样式，不读计算样式。 */
function liftRect(node: HTMLElement): ClientRect {
  const rect = boundsOf(node);
  const shift = sortShift(node);
  return shift ? { ...rect, top: rect.top - shift, bottom: rect.bottom - shift } : rect;
}

/** 几个框合起来的上下边。 */
function spanOf(rects: Iterable<ClientRect | undefined>): { top: number; bottom: number } | null {
  let top = Number.POSITIVE_INFINITY;
  let bottom = Number.NEGATIVE_INFINITY;
  for (const rect of rects) {
    if (!rect) continue;
    top = Math.min(top, rect.top);
    bottom = Math.max(bottom, rect.top + rect.height);
  }
  return Number.isFinite(top) ? { top, bottom } : null;
}

const isPage = (el: Element | null | undefined) =>
  !el || el === document.scrollingElement || el === document.documentElement || el === document.body;

/** 一项的滚动祖先（由近到远，不含它自己；页面滚动时最后是 document.scrollingElement）。遇到 fixed 定位就停。 */
function scrollAncestors(element: Element | null, limit?: number): HTMLElement[] {
  const found: HTMLElement[] = [];
  if (!element || typeof getComputedStyle !== "function") return found;
  for (let node: Node | null = element; node; node = node.parentNode) {
    if (limit !== undefined && found.length >= limit) break;
    if (node.nodeType === 9) {
      const page = (node as Document).scrollingElement as HTMLElement | null;
      if (page && !found.includes(page)) found.push(page);
      break;
    }
    if (node.nodeType !== 1) break;
    const style = getComputedStyle(node as Element);
    if (node !== element && /(auto|scroll|overlay)/.test(`${style.overflow} ${style.overflowX} ${style.overflowY}`)) {
      found.push(node as HTMLElement);
    }
    if (style.position === "fixed") break;
  }
  return found;
}

/** 滚动区的框（页面滚动时是窗口）。 */
function scrollBox(el: HTMLElement): ClientRect {
  if (isPage(el)) return { top: 0, left: 0, width: window.innerWidth, height: window.innerHeight, right: window.innerWidth, bottom: window.innerHeight };
  return boundsOf(el);
}

const scrollTopOf = (el: HTMLElement) => (isPage(el) ? window.scrollY : el.scrollTop);

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

// 拖进另一列时整列跟着动（对话拖进已置顶：原来那组合上、已置顶让开，两组之间的各行、组头整体上下移）：版面一变，凡是位置
// 变了的（selector 选中的各项：侧栏的行、组头、「显示更多」）都从原来的位置带过渡滑到新位置（先记位置、变了再从差值滑回 0，
// 即 FLIP）。用 Web Animations 播 translate：不碰行内样式（排序项的 transform / transition 归让位写），量版面时去掉它（layoutRect）。
// 位置按滚动区内容算，滚动不算移动；正在滑的再变就从此刻看到的位置接着滑，不跳。空档（被拖的那一项）与正在落位的那一项看不见，不滑。
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
  const found = scrollAncestors(node, 1)[0];
  const page = isPage(found);
  const box = page ? { top: 0, bottom: window.innerHeight } : found!.getBoundingClientRect();
  return { root: page ? null : found!, top: Math.max(box.top, 0), bottom: Math.min(box.bottom, window.innerHeight) };
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

// 落位播完停在落点，浮层随后才卸下
const STAY: FillMode = "forwards";

type Box = { top: number; left: number };
type Shape = { x: number; y: number; scaleX: number; scaleY: number };
/** 落点：into(node, shift) 回一个框 = 落到那里并收起（收着的组的组头、空档在可见范围外时可见范围的边上）；null = 落到 node
 *  自己（版面位置 + shift，shift 是排序时那一项让位的位移）。 */
export type Into = (node: HTMLElement, shift?: number) => Box | null;

/** 浮层的 transform（行内写、落位两帧共用的写法）。 */
function transformOf({ x, y, scaleX, scaleY }: Shape): string {
  return `translate3d(${x ? Math.round(x) : 0}px, ${y ? Math.round(y) : 0}px, 0) scaleX(${scaleX}) scaleY(${scaleY})`;
}

/** 浮层从此刻的样子（transform）落到落点的两帧：from 是浮层此刻的框，to 是落点。 */
function landingFrames(transform: Shape, from: Box, to: Box): Keyframe[] {
  const final = { ...transform, x: transform.x - (from.left - to.left), y: transform.y - (from.top - to.top), scaleX: 1, scaleY: 1 };
  return [{ transform: transformOf(transform) }, { transform: transformOf(final) }];
}

/** 排序时那一项让位的竖向位移（applyShift 写在行内的 translate3d；没有为 0）：松手时新顺序还没提交，那一项正由这段位移挪在新位置
 *  上，提交后它的版面位置就是这里。 */
export function sortShift(node: HTMLElement): number {
  const match = /translate(?:3d)?\(\s*(-?[\d.]+)px,\s*(-?[\d.]+)px/.exec(node.style?.transform || "");
  return match ? Number.parseFloat(match[2]!) || 0 : 0;
}

// 松手那一下已经起播的落位（浮层 → 那次动画）：结束拖动时接着等这一次播完
const early = new WeakMap<HTMLElement, Animation>();

/** 浮层量框的那一层：只有一个子元素时量它（浮层外层的大小由它撑）。 */
const measurableOf = (overlay: HTMLElement) =>
  (overlay.children.length === 1 ? (overlay.firstElementChild as HTMLElement | null) : null) ?? overlay;

/** 松手那一下起播落位：浮层（overlay）从此刻的样子落到 node 的落点（见 Into）。只量、只起播 Web Animations（只动 transform，合成
 *  线程播；略放大、阴影由 data-dropping 收回），不改版面、不提交 React；从松手这一刻起算（fromNow）。浮层被限位停在落点上也照样播
 *  （位移为 0，略放大、阴影照样在这段时间里收回）：测试窗口第 9 轮，手机把公司组头拖过列表首尾，浮层被限位停在的正是落点，原来不播、
 *  当场结束拖动，松手那一帧要重画整列，播了就同其他放下一样等落定后再结束。真的没挪过（浮层位移为 0：拿起后原地放下、长按出菜单后
 *  松手）不播，当场结束（审查：结束拖动——焦点、播报、侧栏多选变淡、手机外壳认不认边缘手势——不为它晚 0.2 秒）；always（取消先滑回）
 *  照样播。回 null = 没播（没挪过、量不到、减少动态效果），调用方立即结束拖动。 */
export function landOverlay(overlay: HTMLElement, node: HTMLElement, transition: Motion | null, into: Into, always = false): Animation | null {
  if (!transition || !overlay.isConnected || !node.isConnected) return null;
  const transform = shapeOf(getComputedStyle(overlay).transform);
  if (!transform) return null;
  const shift = sortShift(node);
  const fold = into(node, shift);
  const own = layoutRect(node);
  const frames = landingFrames(transform, boundsOf(measurableOf(overlay)), fold ?? { top: own.top + shift, left: own.left });
  if (!always && !fold && !transform.x && !transform.y && frames[0]!.transform === frames[1]!.transform) return null;
  overlay.dataset.dropping = fold ? "into" : "";
  const animation = overlay.animate(frames, { duration: transition.duration, easing: transition.easing, fill: STAY });
  fromNow(animation);
  early.set(overlay, animation);
  return animation;
}

/** 浮层从此刻的样子滑到 box（视口坐标）：拖进另一列以后取消，原位还没重挂出来时按拿起时的位置先滑（见拖动区的 homing）。
 *  同 landOverlay：只起播 Web Animations、从这一刻起算；回 null = 没播（减少动态效果、量不到）。 */
export function slideOverlay(overlay: HTMLElement, box: Box, transition: Motion | null): Animation | null {
  if (!transition || !overlay.isConnected) return null;
  const transform = shapeOf(getComputedStyle(overlay).transform);
  if (!transform) return null;
  overlay.dataset.dropping = "";
  const animation = overlay.animate(landingFrames(transform, boundsOf(measurableOf(overlay)), box), {
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
 *  的原位（landOverlay，没挪过也播：略放大、阴影收回）；取消（调用方的重画）等滑回播完才做。调用方只在那一项还在拿起时那一列里时
 *  用它（没有换列重挂，原位就是它此刻的版面位置），滑回途中不再写让位位移。into 只管原位在可见范围外时落到边上并收起。 */
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

/** 拖动编号 id 那一项此刻在页面上的节点（拖进另一列又回来时重挂过）；找不到用 fallback。 */
function landingNode(id: string | number, fallback: HTMLElement): HTMLElement {
  if (fallback.isConnected !== false || typeof document?.querySelectorAll !== "function") return fallback;
  const key = String(id);
  return Array.from(document.querySelectorAll<HTMLElement>("[data-drag-id]")).find((el) => el.dataset.dragId === key) ?? fallback;
}

/** 结束拖动时浮层要播的落位。松手那一下已经起播过（landOverlay）：不再另播，等那一次播完（还没播完时落点那一项先不显示）。
 *  没起播过（量不到等）：浮层从此刻移到落点（Web Animations），落点可以不是那一项本身（into 回一个框：收着的那一列的组头、或在可见
 *  范围外时可见范围的边上），这时落到那里并收起（浮层带 data-dropping="into"）。transition 为 null（减少动态效果）时不播。
 *  回 Promise = 播完再卸下浮层。 */
export type DropArgs = {
  active: { id: string | number; node: HTMLElement; rect: ClientRect };
  dragOverlay: { node: HTMLElement; rect: ClientRect };
  transform: Shape;
};
export function dropAnimation(transition: Motion | null, into: Into): (args: DropArgs) => Promise<void> | undefined {
  return ({ active, dragOverlay, transform }) => {
    // 落点那一项：拖进另一列以后取消（Esc），原来那一列重挂了一份新的（审查：滑回的那 0.2 秒里同一段对话出现两份）。按拖动编号在
    // 此刻的页面里找
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

/** 先落位、再结束拖动（松手那一下只起播落位，结束拖动——调用方的重画：整列重排、存顺序——等落位播完才做，主线程忙多久都不卡
 *  落位）。hold(end, animation)：animation 播完才调 end；没有 animation 立即调；过 wait 毫秒（动画没在走：页面在后台）或又按下了
 *  （鼠标、手指、键盘：要开始下一次）就当场调。等着的时候 freeze 回的还原函数在调 end 前执行（自动滚动这段时间不滚，落点不跑）。 */
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

/** 等着结束拖动的那一小段时间，这些滚动区不滚（自动滚动已停；挡板上的滚轮转交也不滚）：落点在松手时量好了，滚了就落偏。
 *  回还原函数。 */
function freezeScroll(scrollers: readonly HTMLElement[]): () => void {
  for (const el of scrollers) el.scrollBy = () => undefined;
  return () => scrollers.forEach((el) => delete (el as Partial<HTMLElement>).scrollBy);
}

// ---------------------------------------------------------------- 落点
/** Alt+↑ / Alt+↓ 挪完的位置：这一列（看得见的）里第几项、共几项。 */
export type MoveSpot = { at: number; total: number };

/** 挪完的新顺序里 id 在第几位；没挪（到头了，新顺序为 null）或找不到回 null。 */
export function spotIn(list: readonly string[] | null | undefined, id: string): MoveSpot | null {
  const index = list ? list.indexOf(id) : -1;
  return list && index >= 0 ? { at: index + 1, total: list.length } : null;
}

/** 点 y 在不在框的上下边之间。 */
const within = (box: { top: number; height: number } | null | undefined, y: number) => !!box && y >= box.top && y <= box.top + box.height;

/** 拿着 active 时要算的落点（碰撞、键盘、限位共用）：pool 是能放的各处（accepts，加上它自己）。键盘拖动：只认它原来那一列
 *  （不拖进另一列）。指针拖动：指针在哪一列的整块落点（zone）里，就只认那一列此刻的各项（它自己已排进去也算；那一列没有别的项、
 *  或收着时认整块）；有能放的整块而指针不在任何一块里（侧栏对话拖到别的公司组、两组之间）：没有落点（空档回原位，放下滑回
 *  原位、不交调用方）；没有整块落点（只在一列里排序：卡片、公司组、置顶）认它此刻所在那一列的各项。 */
export function narrowPool<E extends Seen>(
  pool: readonly E[],
  activeId: string | number,
  active: DragData,
  pointer: { keyboard: boolean; y: number | null; rects: { get: (id: string) => { top: number; height: number } | undefined } },
): E[] {
  const item = (entry: E) => !dataOf(entry)?.zone;
  const self = pool.find((entry) => entry.id === activeId);
  const here = (self && listOf(self)) ?? active.list;
  const inList = (list: string | undefined) => pool.filter((entry) => item(entry) && listOf(entry) === list);
  if (pointer.keyboard) return pool.filter((entry) => item(entry) && (entry.id === activeId || listOf(entry) === active.list));
  const y = pointer.y;
  const zone = y === null ? undefined : pool.find((entry) => dataOf(entry)?.zone && within(pointer.rects.get(String(entry.id)), y));
  if (zone) {
    const data = dataOf(zone)!;
    const items = data.shut ? [] : inList(data.list);
    return items.some((entry) => entry.id !== activeId) ? items : [...items, zone];
  }
  if (pool.some((entry) => dataOf(entry)?.zone)) return [];
  return inList(here);
}

/** 离 collisionRect 中心最近的中心排在最前（指针拖动的落点）。 */
export function closestCenter<E extends { id: string | number }>(args: {
  collisionRect: ClientRect;
  droppableRects: { get: (id: string) => ClientRect | undefined };
  droppableContainers: readonly E[];
}): Array<{ id: string | number; data: { droppableContainer: E; value: number } }> {
  const { collisionRect: box, droppableRects, droppableContainers } = args;
  const cx = box.left + box.width / 2;
  const cy = box.top + box.height / 2;
  const hits: Array<{ id: string | number; data: { droppableContainer: E; value: number } }> = [];
  for (const entry of droppableContainers) {
    const rect = droppableRects.get(String(entry.id));
    if (!rect) continue;
    const value = Math.hypot(rect.left + rect.width / 2 - cx, rect.top + rect.height / 2 - cy);
    hits.push({ id: entry.id, data: { droppableContainer: entry, value } });
  }
  return hits.sort((a, b) => a.data.value - b.data.value);
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
  rectOf: (id: string | number) => { top: number; height: number } | null | undefined,
  height: number,
): number | null {
  const rect = rectOf(target);
  if (!rect) return null;
  const from = order.indexOf(activeId);
  const to = order.indexOf(target);
  return to > from ? rect.top + rect.height - height : rect.top;
}

/** 键盘拖动的落点就是方向键瞄着的那一项（不在能放的各处里就是它自己）：不按几何远近算。测试窗口第 11 轮重测 B 段：会话栏第一张
 *  卡「公司」展开着很高，下一张收着很矮，空格拿起按 ↓，浮层挪到「底边对齐下一张」，它的中心离原位（自己）比离下一张还近，按最近
 *  中心算出来还是它自己，放下念「已取消移动」、顺序不变。 */
export function keyboardCollision<E extends { id: string | number }>(pool: readonly E[], activeId: string | number, aim: string | number | null) {
  const hit = pool.find((entry) => entry.id === (aim ?? activeId)) ?? pool.find((entry) => entry.id === activeId);
  return hit ? [{ id: hit.id, data: { droppableContainer: hit, value: 0 } }] : [];
}

/** 排序让位（竖排）：rects 是这一列各项的版面位置，activeIndex 是拿着的那一项此刻在这一列的位置，overIndex 是落点。原位与落点之间
 *  的各项挪「拖动项高 + 两项间距」；拿着的那一项（空档）挪到落点：往后拖时底边对齐落点的底边，往前拖时顶边对齐落点的顶边。 */
export function sortOffset(
  rects: ReadonlyArray<ClientRect | undefined>,
  index: number,
  activeIndex: number,
  overIndex: number,
  fallback: ClientRect,
): number {
  const active = rects[activeIndex] ?? fallback;
  if (index === activeIndex) {
    const over = rects[overIndex];
    if (!over) return 0;
    return activeIndex < overIndex ? over.top + over.height - (active.top + active.height) : over.top - active.top;
  }
  const gap = itemGap(rects, index, activeIndex);
  if (index > activeIndex && index <= overIndex) return -active.height - gap;
  if (index < activeIndex && index >= overIndex) return active.height + gap;
  return 0;
}

function itemGap(rects: ReadonlyArray<ClientRect | undefined>, index: number, activeIndex: number): number {
  const current = rects[index];
  const previous = rects[index - 1];
  const next = rects[index + 1];
  if (!current) return 0;
  if (activeIndex < index) {
    if (previous) return current.top - (previous.top + previous.height);
    return next ? next.top - (current.top + current.height) : 0;
  }
  if (next) return next.top - (current.top + current.height);
  return previous ? current.top - (previous.top + previous.height) : 0;
}

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

// 浮层的层级：在抽屉、手机会话栏（50）之上，在菜单（.cc-menu 60）之下。手机按住不动拿起后到 500ms 弹出的长按菜单（行、公司组头）
// 落在拿起的那一项范围里时不被浮层盖住（审查）
export const OVERLAY_Z = 55;

/** 浮层：拿起那一刻在拖动层里建好（克隆原来那一项一次；拖进另一列时那一项在那里重挂，不再重克隆），fixed 定位在拿起时那一项的框，
 *  跟手只改它的 transform；外层大小由唯一的子元素撑开（量落点时量它）。整块 inert、aria-hidden：读屏不念，进不了 Tab。 */
function liftOverlay(node: HTMLElement, data: DragData, start: ClientRect, keyboard: Motion | null): { overlay: HTMLElement; float: HTMLElement } {
  const overlay = document.createElement("div");
  overlay.className = "cc-drag-overlay";
  const style = overlay.style;
  style.position = "fixed";
  style.top = `${start.top}px`;
  style.left = `${start.left}px`;
  style.width = `${start.width}px`;
  style.height = `${start.height}px`;
  style.transform = transformOf({ x: 0, y: 0, scaleX: 1, scaleY: 1 });
  style.zIndex = String(OVERLAY_Z);
  // 键盘拖动时浮层一格一格挪，也用动效变量过渡
  if (keyboard) style.transition = `transform ${keyboard.duration}ms ${keyboard.easing}`;
  const float = document.createElement("div");
  float.className = "cc-drag-float";
  float.dataset.kind = data.kind;
  float.setAttribute("inert", "");
  float.setAttribute("aria-hidden", "true");
  const lift = document.createElement("div");
  lift.className = "cc-drag-lift";
  const copy = document.createElement("div");
  copy.className = "cc-drag-copy";
  copy.appendChild(cloneOf(node));
  lift.appendChild(copy);
  if ((data.count ?? 0) > 1) {
    const count = document.createElement("span");
    count.className = "cc-drag-count";
    count.textContent = String(data.count);
    lift.appendChild(count);
  }
  float.appendChild(lift);
  overlay.appendChild(float);
  dragLayer()?.appendChild(overlay);
  return { overlay, float };
}

/** 浮层挂的那一层：body 下面一个一直留着的空 div（第一次用时建，被移走了就重建）。拖动中增删的是这一层里面，不是 body 的直接子节点。 */
let layer: HTMLElement | null = null;
export function dragLayer(): HTMLElement | null {
  if (typeof document === "undefined" || !document.body) return null;
  if (!layer?.isConnected) {
    layer = document.createElement("div");
    // 挂在 body 下的常驻节点不用 data-slot 标识（按 data-slot 做键的兄弟选择器会让 body 下增删节点时整页样式失效，第 11 轮重测 trace）
    layer.className = "cc-drag-layer";
    layer.appendChild(dragShield());
    document.body.appendChild(layer);
  }
  return layer;
}

/** 鼠标拖动中盖住整页的透明挡板（lib/layers.ts 的 setDragShield：拿起那一帧画完盖上、松手那一帧画完撤掉）：整页 grabbing 光标，
 *  指针下面的行、组头、卡片不出悬停；与浮层同在拖动层里。挡着的时候滚轮转给指针下面那个滚动区（侧栏、会话栏拖着时照样能滚）。 */
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

// ---------------------------------------------------------------- 读屏
// 说明与播报区的编号按页面里拖动区建的先后递增（同原来的写法，读屏只看 aria-describedby 指过去的那一份）
const counters = { DndDescribedBy: -1, DndLiveRegion: -1 };
const nextId = (prefix: keyof typeof counters) => `${prefix}-${(counters[prefix] += 1)}`;

// 拖动的播报区：看不见、不占位，常驻（不只在拖动时）
const LIVE_STYLE: Partial<CSSStyleDeclaration> = {
  position: "fixed",
  top: "0",
  left: "0",
  width: "1px",
  height: "1px",
  margin: "-1px",
  border: "0",
  padding: "0",
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  clipPath: "inset(100%)",
  whiteSpace: "nowrap",
};

/** Alt+↑/↓ 挪一格的播报区（挂在 body 上，同拖动的播报区；看不见、不占位）。 */
const MoveLive: FC<{ text: string }> = ({ text }) => {
  const region = (
    <div role="status" aria-live="assertive" aria-atomic="true" className="cc-move-live sr-only">
      {text}
    </div>
  );
  return typeof document !== "undefined" && document.body ? createPortal(region, document.body) : region;
};

// ---------------------------------------------------------------- 键盘拖动的焦点
// 拖动中归拖动的键：方向键、Home / End、翻页（拖动在 document 上收；按在把手上时标成已处理，组件库行自带的方向键导航不再移焦点）
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

/** 登记 / 注销一项眼前的把手（同一项换组重挂时先卸后挂，卸的不是登记着的那个就不动）。 */
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
  /** 取消（Esc 等）时拿着的那一项已排进另一列：把它挪回原来那一列（同指针拖回去；还原为拿起时的摆法）。给了才先滑回再取消。 */
  onReturn?: (active: DragData) => void;
  /** 指针拖动中指针的竖向位置（视口坐标；指针动了、滚动了都交，键盘拖动不交）：侧栏据此在指针拖到顶上时才临时显示空的「已置顶」。 */
  onPointer?: (active: DragData, y: number) => void;
  /** 拖进另一列时整列一起滑的各项（侧栏的行、组头、「显示更多」），见 flowLayout。 */
  flow?: string;
  texts: DragTexts;
};
type Callbacks = Omit<AreaProps, "children" | "flow" | "texts">;

type Kind = "mouse" | "touch" | "keyboard";

/** 拖动区里登记的一项（useSortableItem）或一块整块落点（useDropZone）。entry 是给落点筛选、读屏看的那一份（同 Seen）。 */
type Item = {
  id: string;
  data: DragData;
  /** 它此刻所在的那一列（DragList；整块落点没有）。 */
  list: string | undefined;
  disabled: boolean;
  zone: boolean;
  node: HTMLElement | null;
  handle: HTMLElement | null;
  entry: Seen;
};

/** 一次拖动（拿起到结束）。 */
type Session = {
  id: string;
  /** 拿起时的那份说明：拖进另一列的那一项会在那一列里重挂，拖动中一直按拿起时的认（原来那一列不变）。 */
  data: DragData;
  kind: Kind;
  event: Event;
  /** 拿起时那一项的框（视口坐标）：浮层固定在那里，位移从它算。 */
  start: ClientRect;
  origin: Coordinates;
  pointer: Coordinates | null;
  /** 浮层想去的位移、限位以后实际的位移。 */
  rawY: number;
  y: number;
  overlay: HTMLElement;
  float: HTMLElement;
  /** 能放的各处（含它自己）的拖动编号与版面位置；rects 量的时候主滚动区滚到 base，此刻滚到 scrolled。 */
  candidates: string[];
  version: number;
  rects: Map<string, ClientRect>;
  /** 量的时候各项在哪一列（拖进另一列重挂以后、重量之前，那一列的位置还是旧的）。 */
  measuredIn: Map<string, string | undefined>;
  base: number;
  scrolled: number;
  scroller: HTMLElement | null;
  scrollers: HTMLElement[];
  boxes: Map<HTMLElement, ClientRect>;
  extents: Map<HTMLElement, number>;
  /** 拿起时主滚动区的滚动位置（拖进另一列以后取消：原位 = 拿起时的位置减去这之后滚过的）。 */
  liftScroll: number;
  overId: string | null;
  over: DragData | null;
  /** 键盘拖动此刻瞄着的那一项（拿起时是它自己，方向键一格一格换；落点就是它）。 */
  aim: string | null;
  /** 写过让位位移的各项。 */
  touched: Set<string>;
  /** 自动滚动只朝指针动过的方向。 */
  intent: { up: boolean; down: boolean };
  scrolling: { el: HTMLElement; step: number; timer: ReturnType<typeof setInterval> } | null;
  /** 已经松手（在等落位）。 */
  released: boolean;
  /** 已经取消、在等滑回：不再找落点、不交指针位置、不重量。 */
  cancelling: boolean;
  /** 取消后滑回原位的途中：不写让位位移。 */
  settle: boolean;
  queued: boolean;
  stops: Array<() => void>;
};

type Homing = {
  id: string;
  list: string;
  cancel: () => void;
  timer: ReturnType<typeof setTimeout>;
  /** Esc 那一下按拿起时的位置先起播的滑回（原位重挂出来后对得上就接着用，对不上从此刻改滑到真正的原位）。 */
  slide: Animation | null;
  unfreeze: () => void;
};

const NO_SHIFT = { transform: "", transition: "" };

/** 一个拖动区的拖动本身（不经过 React）：登记的各项、量好的位置、一次拖动的全部状态都在这里，拖动中每一下直接写节点；只有被拖的那
 *  一项（是不是在拖）和被指着的收着的整块（高亮）通过订阅让 React 重画。导出给行为测试直接驱动。 */
export class DragEngine {
  props: Callbacks = { accepts: () => false, onDrop: () => undefined };
  flow: string | undefined;
  readonly transition: Motion | null;
  readonly describedBy: string;
  private readonly liveId: string;
  private texts: DragTexts | null = null;
  private say: Announcements = dragAnnouncements({ instructions: "", pickedUp: () => "", movedTo: () => "", droppedAt: () => "", cancelled: () => "" });
  sayMove: (name: string, spot: MoveSpot) => void = () => undefined;

  private readonly items = new Map<string, Item>();
  private readonly lists = new Map<string, readonly string[]>();
  private version = 0;
  private readonly watchers = new Map<string, Set<() => void>>();
  /** 正在拖的那一次（拿起到结束；落位、滑回的等待期也算）。 */
  private drag: Session | null = null;
  /** 有一次按下正在判断 / 正在拖（同一时间只认一个）。 */
  private pending: SensorInstance | null = null;
  /** 指着的收着的整块（高亮组头）。 */
  shutOver: string | null = null;
  private homing: Homing | null = null;
  private readonly letGo = new ReleaseFirst();
  private readonly landWait: number;
  private alive = false;
  /** 正在 React 的提交里（登记各项的布局副作用）：这时结束拖动不能再同步提交一次。 */
  private committing = false;
  private hidden: HTMLElement | null = null;
  private live: HTMLElement | null = null;
  private said = "";
  private flowStopped = 0;

  readonly options: {
    mouse: MouseSensorOptions & EndOptions;
    touch: HoldOptions;
    keyboard: KeyboardSensorOptions & EndOptions;
  };

  constructor(transition: Motion | null, numbered = true) {
    this.transition = transition;
    this.describedBy = numbered ? nextId("DndDescribedBy") : "";
    this.liveId = numbered ? nextId("DndLiveRegion") : "";
    this.landWait = (transition?.duration ?? 0) + 150;
    // 放下：松手那一下只起播落位（landOverlay），结束拖动等落位播完（ReleaseFirst）。三种输入同一道
    const beforeEnd = (end: () => void) => this.beforeEnd(end);
    // 取消（Esc；手机还有 touchcancel、转屏、切到后台）：先滑回原位，滑回播完才取消（settleBack）。三种输入同一道
    const beforeCancel = (cancel: () => void) => this.beforeCancel(cancel);
    const step = (event: KeyboardEvent) => this.step(event);
    this.options = {
      mouse: { activationConstraint: MOUSE_START, beforeEnd, beforeCancel },
      touch: { ...TOUCH_START, beforeEnd, beforeCancel },
      keyboard: { keyboardCodes: KEYS, scrollBehavior: KEYBOARD_SCROLL, step, beforeEnd, beforeCancel },
    };
  }

  // ---- 读屏
  setTexts(texts: DragTexts) {
    if (texts === this.texts) return;
    this.texts = texts;
    this.say = dragAnnouncements(texts, {
      lifted: (active) => (this.drag && this.drag.id === String(active.id) ? this.drag.data : dataOf(active)),
      zoneLabel: (list) => this.zoneOf(list)?.label,
    });
    if (this.hidden) this.hidden.textContent = texts.instructions;
  }

  /** 播报（null / undefined 不改，留着上一句；同一句不重写：读屏不重念）。 */
  private announce(text: string | undefined) {
    if (text === undefined || text === null || text === this.said) return;
    this.said = text;
    if (this.live) this.live.textContent = text;
  }

  /** 拖动区挂上：说明与播报区挂到 body 上（常驻，拖动中不增删）；iOS 要的非被动 touchmove 监听。回卸下。 */
  mount(): () => void {
    this.alive = true;
    const off = typeof window !== "undefined" ? HoldTouchSensor.setup() : () => undefined;
    if (typeof document !== "undefined" && document.body) {
      dragLayer(); // 浮层与挡板那一层：拖动区挂上时就建好，拿起时 body 下不增删

      const hidden = document.createElement("div");
      hidden.id = this.describedBy;
      hidden.style.display = "none";
      hidden.textContent = this.texts?.instructions ?? "";
      const live = document.createElement("div");
      live.id = this.liveId;
      live.setAttribute("role", "status");
      live.setAttribute("aria-live", "assertive");
      live.setAttribute("aria-atomic", "true");
      Object.assign(live.style, LIVE_STYLE);
      live.textContent = this.said;
      document.body.append(hidden, live);
      this.hidden = hidden;
      this.live = live;
    }
    return () => {
      this.alive = false;
      off();
      // 卸下时还在等挪回去的取消、还在等落位的放下当场结束；还在拖的当场取消
      const entry = this.homing;
      this.homing = null;
      if (entry) {
        clearTimeout(entry.timer);
        entry.unfreeze();
        entry.cancel();
      }
      this.letGo.flush(); // 卸下时还在等落位的放下当场结束
      if (this.drag) this.finish(this.drag, () => undefined);
      setDragging(false);
      this.hidden?.remove();
      this.live?.remove();
      this.hidden = null;
      this.live = null;
    };
  }

  // ---- 订阅：各项只听自己
  subscribe(id: string, run: () => void): () => void {
    let set = this.watchers.get(id);
    if (!set) this.watchers.set(id, (set = new Set()));
    set.add(run);
    return () => {
      set.delete(run);
      if (!set.size && this.watchers.get(id) === set) this.watchers.delete(id);
    };
  }

  private notify(id: string | null) {
    if (id) for (const run of this.watchers.get(id) ?? []) run();
  }

  /** 这一项是不是被拖的那一项：0 不是，1 是（指针），2 是（键盘拿起：焦点跟着它）。 */
  stateOf(id: string): number {
    const d = this.drag;
    return d && d.id === id ? (d.kind === "keyboard" ? 2 : 1) : 0;
  }

  // ---- 登记
  register(item: Item, data: DragData, list: string | undefined, disabled: boolean) {
    if (item.id !== data.id) {
      this.unregister(item);
      item.id = data.id;
    }
    const moved = this.items.get(item.id) !== item || item.list !== list || item.disabled !== disabled;
    if (moved || item.data !== data) {
      item.data = data;
      item.list = list;
      item.disabled = disabled;
      item.entry = entryOf(item);
      this.items.set(item.id, item);
    }
    if (moved) this.version += 1;
    // 等着挪回去的那一项在原来那一列里重挂出来了：在这次提交里起播滑回（量的是挪回去以后的版面）
    const homing = this.homing;
    if (homing && homing.id === item.id && item.list === homing.list && item.node?.isConnected) {
      this.committing = true;
      try {
        this.homeLanded(homing, item.node);
      } finally {
        this.committing = false;
      }
    }
  }

  unregister(item: Item) {
    if (this.items.get(item.id) !== item) return;
    this.items.delete(item.id);
    this.version += 1;
  }

  attach(item: Item, node: HTMLElement | null) {
    item.node = node;
  }

  /** 把手挂上了：键盘拖动中被拖的那一项重挂过，焦点跟到新的把手上。 */
  attachHandle(item: Item, node: HTMLElement | null) {
    rememberHandle(item.id, node, item.handle);
    item.handle = node;
    this.follow(item);
  }

  follow(item: Item) {
    const node = item.handle;
    if (this.stateOf(item.id) === 2 && node?.isConnected && document.activeElement !== node) node.focus({ preventScroll: true });
  }

  setList(id: string, items: readonly string[]) {
    this.lists.set(id, items);
  }

  dropList(id: string, items: readonly string[]) {
    if (this.lists.get(id) === items) this.lists.delete(id);
  }

  private zoneOf(list: string): DragData | undefined {
    for (const item of this.items.values()) if (item.zone && item.data.list === list) return item.data;
    return undefined;
  }

  /** 读屏、落点看的那一项（排序项带它此刻那一列的位置）。 */
  private view(id: string): Seen {
    const item = this.items.get(id);
    const list = item && !item.zone ? item.list : undefined;
    const items = list !== undefined ? (this.lists.get(list) ?? []) : undefined;
    return {
      id,
      data: { current: { drag: item?.data, ...(list !== undefined ? { sortable: { containerId: list, index: items!.indexOf(id), items } } : {}) } },
    };
  }

  // ---- 按下
  /** 把手上按下（鼠标、手指、键盘）：能拖、此刻没有别的拖动时交给那一种输入去判断拿不拿起。 */
  press(kind: Kind, id: string, event: ReactMouseEvent | ReactTouchEvent | ReactKeyboardEvent) {
    const native = event.nativeEvent as Event & { ccDrag?: boolean };
    // 别的拖动正在进行（落位等待期间一按下就先把上一次当场结束，见 ReleaseFirst）、这一下别处已经处理过（Shift 点对话行为多选不选字）
    if (this.pending || native.ccDrag || native.defaultPrevented) return;
    const item = this.items.get(id);
    if (!item || item.zone || item.disabled || !item.node) return;
    if (kind === "mouse" && (native as MouseEvent).button === 2) return;
    if (kind === "touch" && ((native as TouchEvent).touches?.length ?? 0) > 1) return;
    if (kind === "keyboard") {
      // 空格拿起；焦点在把手本身上（行里的「···」上按空格不拿起）
      if (!KEYS.start.includes((native as KeyboardEvent).code)) return;
      if (item.handle && event.target !== item.handle) return;
      event.preventDefault();
    }
    native.ccDrag = true;
    const props = {
      active: id,
      event: native,
      onStart: (at: Coordinates) => this.lift(id, native, kind, at),
      onMove: (at: Coordinates) => this.move(at),
      onEnd: () => this.dropped(),
      onCancel: () => this.cancelled(),
      onAbort: () => {
        if (!this.drag) this.pending = null;
      },
    };
    const sensor =
      kind === "mouse"
        ? new LandingMouseSensor({ ...props, options: this.options.mouse })
        : kind === "touch"
          ? new HoldTouchSensor({ ...props, options: this.options.touch })
          : new LandingKeyboardSensor({ ...props, options: this.options.keyboard });
    // 键盘按下当场拿起；没拿起就结束的（onAbort）不留
    if (this.drag || kind !== "keyboard") this.pending = sensor;
  }

  // ---- 拿起
  private lift(id: string, activatorEvent: Event, kind: Kind, at: Coordinates) {
    const item = this.items.get(id);
    const node = item?.node;
    if (!item || !node?.isConnected || this.drag) return;
    // 键盘拿起时那一项完全在窗口之外：滚到中间
    if (kind === "keyboard") revealNode(node);
    // 先只读（这时页面还没改过，读版面不用现算样式与布局）：滚动祖先、拿起时的框
    const scrollers = scrollAncestors(node);
    const scroller = scrollers[0] ?? null;
    const recent = now() - this.flowStopped < (this.transition?.duration ?? 0) + 50;
    const start = recent ? layoutRect(node) : liftRect(node);
    const base = scroller ? scrollTopOf(scroller) : 0;
    const data = item.data;
    const d: Session = {
      id,
      data,
      kind,
      event: activatorEvent,
      start,
      origin: at,
      pointer: kind === "keyboard" ? null : at,
      rawY: 0,
      y: 0,
      overlay: null as unknown as HTMLElement,
      float: null as unknown as HTMLElement,
      candidates: [],
      version: this.version,
      rects: new Map(),
      measuredIn: new Map(),
      base,
      scrolled: base,
      scroller,
      scrollers,
      boxes: new Map(),
      extents: new Map(),
      liftScroll: base,
      overId: null,
      over: null,
      aim: id,
      touched: new Set(),
      intent: { up: false, down: false },
      scrolling: null,
      released: false,
      cancelling: false,
      settle: false,
      queued: false,
      stops: [],
    };
    this.drag = d;
    setDragging(true, byMouse(activatorEvent));
    keepFocusOn(id, null);
    this.props.onActive?.(data);
    // 能放的各处（accepts 要在 onActive 之后：侧栏据此记下多选整批在不在本组）一次量好
    d.candidates = this.candidatesFor(d);
    this.measure(d, recent);
    // 再写：浮层、播报、被拖的那一项重画成空档
    const { overlay, float } = liftOverlay(node, data, start, kind === "keyboard" ? this.transition : null);
    d.overlay = overlay;
    d.float = float;
    this.announce(this.say.onDragStart({ active: this.view(id) }));
    this.notify(id);
    if (scroller) {
      const target: EventTarget = isPage(scroller) ? window : scroller;
      target.addEventListener("scroll", this.onScroll, { passive: true });
      d.stops.push(() => target.removeEventListener("scroll", this.onScroll));
    }
    // 拿起的那一刻紧跟着算一次落点（就是它自己，不念、不交调用方）
    this.collide();
    // 拿起那一帧画完以后才开始盯版面（整列滑动要先记下整列的位置；会话栏卡片拖动中会变高）
    afterPaint(() => this.drag === d && this.watch(d));
  }

  /** 能放的各处（含它自己）：登记着、挂在页面上、没禁用、调用方说能放的。 */
  private candidatesFor(d: Session): string[] {
    const out: string[] = [];
    for (const item of this.items.values()) {
      if (!item.node) continue;
      if (item.id === d.id) {
        if (d.data.list) out.push(item.id);
        continue;
      }
      if (item.disabled) continue;
      if (this.props.accepts(d.data, item.data)) out.push(item.id);
    }
    return out;
  }

  /** 量能放的各处（版面位置）与滚动区。full：整列正在滑（Web Animations 的 translate 要去掉），读计算样式。 */
  private measure(d: Session, full: boolean) {
    for (const id of d.candidates) {
      const item = this.items.get(id);
      const node = item?.node;
      if (!node?.isConnected) continue;
      d.rects.set(id, full ? layoutRect(node) : liftRect(node));
      d.measuredIn.set(id, item!.list);
    }
    d.base = d.scroller ? scrollTopOf(d.scroller) : 0;
    d.scrolled = d.base;
    for (const el of d.scrollers) {
      d.boxes.set(el, scrollBox(el));
      d.extents.set(el, isPage(el) ? document.documentElement.scrollHeight - window.innerHeight : el.scrollHeight - el.clientHeight);
    }
  }

  /** 这一项此刻的版面位置（量的时候的位置减去之后滚过的）。 */
  private rectNow(d: Session, id: string): ClientRect | undefined {
    const rect = d.rects.get(id);
    const dy = d.scrolled - d.base;
    return rect && dy ? { ...rect, top: rect.top - dy, bottom: rect.bottom - dy } : rect;
  }

  /** 拿起那一帧画完以后：有整列滑动的区盯着版面（变了就让整列滑过去、下一帧重量）；没有的区（会话栏）盯着各项尺寸（卡片拖动中会
   *  变高：本月卡定时刷新、公司页转圈换成内容、输出卡跟着对话变长）。 */
  private watch(d: Session) {
    const node = this.items.get(d.id)?.node;
    if (!node?.isConnected) return;
    if (this.flow) {
      const root = d.scroller ?? (document.scrollingElement as HTMLElement | null) ?? document.body;
      const stop = flowLayout(root, this.flow, this.transition, () => this.remeasureSoon());
      d.stops.push(() => {
        stop();
        this.flowStopped = now();
      });
    } else if (typeof ResizeObserver === "function") {
      let first = true;
      const observer = new ResizeObserver(() => {
        if (first) first = false;
        else this.remeasureSoon();
      });
      for (const id of d.candidates) {
        const el = this.items.get(id)?.node;
        if (el) observer.observe(el);
      }
      d.stops.push(() => observer.disconnect());
    }
  }

  /** 版面变了的那一帧画完以后重量一次（几次变动合成一次），再按新位置算限位、落点与让位。已经取消、在等滑回的不量。 */
  private remeasureSoon() {
    const d = this.drag;
    if (!d || d.cancelling || d.queued) return;
    d.queued = true;
    afterPaint(() => {
      d.queued = false;
      if (this.drag !== d || d.cancelling || d.released) return;
      d.candidates = this.candidatesFor(d);
      d.version = this.version;
      this.measure(d, true);
      this.position();
      this.collide();
      this.shifts();
    });
  }

  // ---- 拖动中
  private readonly onScroll = () => {
    const d = this.drag;
    if (!d?.scroller) return;
    d.scrolled = scrollTopOf(d.scroller);
    if (d.cancelling || d.released) return;
    this.position();
    this.collide();
  };

  private move(at: Coordinates) {
    const d = this.drag;
    if (!d || d.kind === "keyboard") return;
    const before = d.pointer;
    d.pointer = at;
    if (before && at.y < before.y) d.intent.up = true;
    else if (before && at.y > before.y) d.intent.down = true;
    if (d.cancelling || d.released) return;
    d.rawY = at.y - d.origin.y;
    this.position();
    this.collide();
    this.autoScroll();
  }

  /** 浮层只竖着动，上下边不出能放的范围（这一列各项、能拖进的另一列的整块，加上原位；键盘只在这一列）。 */
  private position() {
    const d = this.drag;
    if (!d) return;
    const keyboard = d.kind === "keyboard";
    const rects: Array<ClientRect | undefined> = [];
    for (const id of d.candidates) {
      if (keyboard && id !== d.id) {
        const item = this.items.get(id);
        if (!item || item.zone || item.list !== d.data.list) continue;
      }
      rects.push(this.rectNow(d, id));
    }
    if (!d.rects.has(d.id)) rects.push(d.start);
    const list = spanOf(rects);
    const y = list ? clampToList(d.rawY, d.start, list) : d.rawY;
    if (y === d.y) return;
    d.y = y;
    d.overlay.style.transform = transformOf({ x: 0, y, scaleX: 1, scaleY: 1 });
  }

  /** 落点：按 narrowPool 收窄后，指针拖动取离浮层中心最近的中心，键盘取瞄着的那一项。指针拖动顺带交指针的位置（onPointer）。 */
  private collide() {
    const d = this.drag;
    if (!d || d.cancelling || !d.data.list) return;
    if (d.version !== this.version) {
      d.candidates = this.candidatesFor(d);
      d.version = this.version;
      this.remeasureSoon();
    }
    const keyboard = d.kind === "keyboard";
    const box: ClientRect = { ...d.start, top: d.start.top + d.y, bottom: d.start.bottom + d.y };
    const entries: Seen[] = [];
    for (const id of d.candidates) {
      const item = this.items.get(id);
      if (item) entries.push(item.entry);
    }
    const rects = { get: (id: string) => this.rectNow(d, id) };
    const pool = narrowPool(entries, d.id, d.data, { keyboard, y: d.pointer?.y ?? box.top + box.height / 2, rects });
    const hit = keyboard ? keyboardCollision(pool, d.id, d.aim)[0] : closestCenter({ collisionRect: box, droppableRects: rects, droppableContainers: pool })[0];
    const next = hit ? String(hit.id) : null;
    if (!keyboard && d.pointer) this.props.onPointer?.(d.data, d.pointer.y);
    if (this.drag === d && next !== d.overId) this.overChanged(d, next);
  }

  private overChanged(d: Session, next: string | null) {
    d.overId = next;
    const to = next ? (this.items.get(next)?.data ?? null) : null;
    d.over = to;
    const shut = next && to?.zone && to.shut ? next : null;
    if (shut !== this.shutOver) {
      const before = this.shutOver;
      this.shutOver = shut;
      this.notify(before);
      this.notify(shut);
    }
    this.announce(this.say.onDragOver({ active: this.view(d.id), over: next ? this.view(next) : null }));
    // 经过的是它自己（拿起时、排进另一列重挂以后）不交：它自己的说明还是原来那一列的，交了调用方会当成回到原来那一列
    if (d.kind !== "keyboard" && next !== d.id) {
      // 指在 over 的下半截（拖进另一列时排在它后面）：按浮层此刻的框算
      const rect = next ? this.rectNow(d, next) : undefined;
      const below = !!rect && d.start.top + d.y + d.start.height / 2 > rect.top + rect.height / 2;
      this.props.onOver?.(d.data, to, below);
    }
    this.shifts();
  }

  /** 让位：落点与拿着的那一项在同一列时，这一列原位与落点之间的各项让开（位移直接写到节点上，带过渡）；别的都回原位。 */
  private shifts() {
    const d = this.drag;
    if (!d || d.settle) return;
    const want = new Map<string, number>();
    const over = d.overId ? this.items.get(d.overId) : undefined;
    const list = over && !over.zone ? over.list : undefined;
    // 拖进另一列重挂了、还没重量（下一帧）：那一列的空档已经由版面让出来，先不算让位，重量后再算
    if (list !== undefined && this.items.get(d.id)?.list === list && d.measuredIn.get(d.id) === list) {
      const ids = this.lists.get(list) ?? [];
      const activeIndex = ids.indexOf(d.id);
      const overIndex = ids.indexOf(over!.id);
      if (activeIndex >= 0 && overIndex >= 0) {
        const rects = ids.map((id) => d.rects.get(id));
        ids.forEach((id, index) => {
          const y = Math.round(sortOffset(rects, index, activeIndex, overIndex, d.start));
          if (y) want.set(id, y);
        });
      }
    }
    const transition = this.transition ? `transform ${this.transition.duration}ms ${this.transition.easing}` : "";
    for (const id of new Set([...d.touched, ...want.keys()])) {
      const node = this.items.get(id)?.node;
      if (!node) continue;
      const y = want.get(id) ?? 0;
      applyShift(node, shiftStyle({ x: 0, y }, transition));
      if (y) d.touched.add(id);
    }
  }

  /** 拖到滚动区上下 20% 的边缘区就朝那边滚（只朝指针动过的方向；越深越快，最快每 5ms 10px），只滚到列表首尾。 */
  private autoScroll() {
    const d = this.drag;
    if (!d || d.kind === "keyboard" || d.released || d.cancelling || !d.pointer) return this.stopScroll();
    const y = d.pointer.y;
    for (const el of d.scrollers) {
      const box = d.boxes.get(el);
      if (!box) continue;
      const top = el === d.scroller ? d.scrolled : scrollTopOf(el);
      const max = d.extents.get(el) ?? 0;
      const edge = box.height * 0.2;
      let step = 0;
      if (top > 0 && y <= box.top + edge) step = -10 * Math.abs((box.top + edge - y) / edge);
      else if (top < max && y >= box.bottom - edge) step = 10 * Math.abs((box.bottom - edge - y) / edge);
      if (step && (step < 0 ? d.intent.up : d.intent.down)) {
        if (d.scrolling?.el === el) {
          d.scrolling.step = step;
          return;
        }
        this.stopScroll();
        const scrolling = { el, step, timer: setInterval(() => el.scrollBy({ top: scrolling.step }), 5) };
        d.scrolling = scrolling;
        return;
      }
    }
    this.stopScroll();
  }

  private stopScroll() {
    const d = this.drag;
    if (!d?.scrolling) return;
    clearInterval(d.scrolling.timer);
    d.scrolling = null;
  }

  /** 键盘：↑ ↓ 在本列里一格一格挪（不拖进另一列）。瞄准按这一列的先后（keyboardAim），浮层挪到挪过去以后它在的位置（keyboardSlot）；
   *  目标落在滚动区下半截（往上时上半截）改为立即滚动滚动区，让目标滚到浮层下面。落点就是瞄着的那一项。 */
  private step(event: KeyboardEvent) {
    const d = this.drag;
    if (!d || d.kind !== "keyboard" || d.cancelling || d.released) return;
    const down = event.code === KeyboardCode.Down;
    if (!down && event.code !== KeyboardCode.Up) {
      if (event.code === KeyboardCode.Left || event.code === KeyboardCode.Right) event.preventDefault();
      return;
    }
    event.preventDefault();
    const list = d.data.list;
    if (!list) return;
    const enabled = new Set(
      d.candidates.filter((id) => {
        const item = this.items.get(id);
        return !!item && !item.zone && item.list === list;
      }),
    );
    const order = (this.lists.get(this.items.get(d.id)?.list ?? list) ?? []).filter((id) => id === d.id || enabled.has(id));
    const target = keyboardAim(order, d.id, d.aim, down);
    if (target === null) return;
    if (d.scroller) d.scrolled = scrollTopOf(d.scroller);
    const y = keyboardSlot(order, d.id, target, (id) => this.rectNow(d, String(id)), d.start.height);
    if (y === null) return;
    d.aim = String(target);
    const current = d.start.top + d.y;
    let scrollDelta = 0;
    for (const el of d.scrollers) {
      const box = isPage(el) ? scrollBox(el) : boundsOf(el);
      const top = scrollTopOf(el);
      const max = isPage(el) ? document.documentElement.scrollHeight - window.innerHeight : el.scrollHeight - el.clientHeight;
      const clamped: number = down ? Math.min(box.bottom - box.height / 2, Math.max(box.top, y)) : Math.min(box.bottom, Math.max(box.top + box.height / 2, y));
      if ((down ? top < max : top > 0) && clamped !== y) {
        const next = top + (y - current);
        if (down ? next <= max : next >= 0) {
          el.scrollTo({ top: next, behavior: this.options.keyboard.scrollBehavior });
          this.collide();
          return;
        }
        scrollDelta = down ? top - max : top;
        if (scrollDelta) el.scrollBy({ top: -scrollDelta, behavior: this.options.keyboard.scrollBehavior });
        break;
      }
    }
    d.rawY = y - d.start.top + scrollDelta;
    this.position();
    this.collide();
  }

  // ---- 放下与取消
  /** 落点（放下时量）：指着的是收着的那一列整块（拿着的那一项不在里面）就落到那一块（组头）并收起；落点在滚动区可见范围外（拖着时
   *  又滚走了）就落到可见范围的边上并收起，不飞出列表。shift：排序时那一项让位的位移（松手时新顺序还没提交）。 */
  private readonly into: Into = (node, shift = 0) => {
    const target = this.drag?.over;
    const zone = target?.zone && target.shut ? this.items.get(target.id)?.node : null;
    const fold = !!zone && !zone.contains(node);
    const base = layoutRect(fold ? zone! : node);
    const rect = fold || !shift ? base : { ...base, top: base.top + shift, bottom: base.bottom + shift };
    const shown = clampIntoView(rect, viewOf(node));
    return fold || shown !== rect ? shown : null;
  };

  private beforeEnd(end: () => void) {
    const d = this.drag;
    if (!d) return end();
    d.released = true;
    this.stopScroll();
    const node = this.items.get(d.id)?.node ?? null;
    const animation = node ? landOverlay(d.overlay, node, this.transition, this.into) : null;
    this.letGo.hold(end, animation, this.landWait, () => freezeScroll(d.scrollers));
  }

  /** 取消：那一项还在拿起时那一列里就滑回原位、滑回播完才取消。已不在（指针把侧栏对话拖进了已置顶，它在那里重挂过）：原位要等原来
   *  那一列重挂回来才量得到——这一下只起播浮层滑回（按拿起时的位置），画完这一帧再请调用方挪回去（onReturn），重挂出来时（register）
   *  再核对。 */
  private beforeCancel(cancel: () => void) {
    const d = this.drag;
    if (!d) return cancel();
    const node = this.items.get(d.id)?.node ?? null;
    const from = d.data;
    const home = !!node?.isConnected && !!from.list && this.items.get(d.id)?.list === from.list;
    d.cancelling = true;
    this.stopScroll();
    const onReturn = this.props.onReturn;
    if (!home && from.list && this.transition && onReturn) {
      d.settle = true;
      // 这一下只起播浮层滑回（拿起时的位置减去之后滚过的；只动 transform，下一帧主线程再忙也照样走），停住自动滚动
      const scrolled = d.scroller ? scrollTopOf(d.scroller) - d.liftScroll : 0;
      const slide = slideOverlay(d.overlay, homeBox(d.start, scrolled, node), this.transition);
      // 已经取消了：之后的 Esc 照常关浮着的会话栏、清多选。鼠标拖动的挡板这时不撤（"keep"）：撤掉那一下指针下面的侧栏要换悬停、
      // 整块重算样式，放在 Esc 这一帧或下一帧（挪回原组、整列重挂）都压在长帧上；起播滑回时 homeLanded 的 letGo.hold 再放一次手，
      // 那一帧画完再撤，滑回只动 transform 不受它卡
      releaseDrag("keep");
      const entry: Homing = {
        id: d.id,
        list: from.list,
        cancel,
        timer: setTimeout(() => this.homeLanded(entry, null), HOME_WAIT),
        slide,
        unfreeze: freezeScroll(d.scrollers),
      };
      this.homing = entry;
      afterPaint(() => this.homing === entry && onReturn(from));
      return;
    }
    d.settle = home;
    const animation = home && node ? settleBack(d.overlay, node, this.transition, homeInto) : null;
    if (!animation) d.settle = false;
    this.letGo.hold(cancel, animation, this.landWait, () => freezeScroll(d.scrollers));
  }

  /** 挪回原来那一列的那一项重挂出来了（或等不到）：从此刻滑回原位，滑回播完才取消（没挪回来就当场取消）。 */
  private homeLanded(entry: Homing, node: HTMLElement | null) {
    if (this.homing !== entry) return;
    this.homing = null;
    clearTimeout(entry.timer);
    const d = this.drag;
    let animation = entry.slide;
    if (node && d) {
      // 原位与 Esc 时按拿起位置算的对不上（拖着时列表增减了行）：从此刻（滑到一半的样子）改滑到真正的原位
      const target = homeInto(node) ?? layoutRect(node);
      const aimed = entry.slide ? landingTarget(entry.slide) : null;
      if (!aimed || Math.abs(aimed - target.top) > 1) {
        const next = settleBack(d.overlay, node, this.transition, homeInto);
        entry.slide?.cancel();
        animation = next;
      }
    }
    if (!animation && d) d.settle = false;
    this.letGo.hold(entry.cancel, animation, this.landWait, () => entry.unfreeze);
  }

  /** 放下（落位播完）：按落点交给调用方，与结束拖动同一次提交。 */
  private dropped() {
    const d = this.drag;
    if (!d) {
      this.pending = null;
      return;
    }
    const from = d.data;
    const overId = d.overId;
    const to = d.over;
    // 键盘放下：焦点留在移动后的那一行（键盘不拖进另一列，不会落进收着的整块）
    keepFocusOn(d.id, d.event);
    const text = this.say.onDragEnd({ active: this.view(d.id), over: overId ? this.view(overId) : null });
    this.finish(d, () => {
      if (!to || !overId) return;
      if (overId === d.id) {
        // 停在自己的位置上：还在原来那一列 = 没动；已排进另一列 = 落在那一列的这个位置（交那一列的整块，调用方按自己排好的算）
        const into = this.items.get(d.id)?.list;
        const zone = into && into !== from.list ? this.zoneOf(into) : undefined;
        if (zone) this.props.onDrop(from, zone);
        return;
      }
      if (!this.props.accepts(from, to)) return;
      this.props.onDrop(from, to);
    });
    this.announce(text);
  }

  /** 取消（滑回播完）。 */
  private cancelled() {
    const d = this.drag;
    if (!d) {
      this.pending = null;
      return;
    }
    keepFocusOn(d.id, d.event);
    const text = this.say.onDragCancel({ active: this.view(d.id) });
    this.finish(d, () => undefined);
    this.announce(text);
  }

  /** 结束拖动：调用方的重画（新顺序、整列重排）与被拖的那一项去掉空档在同一次同步提交里；提交后各项去掉让位位移（不带过渡，新顺序
   *  已排好）、浮层卸下（落位还没播完就等它播完，落点那一项先不显示）。 */
  private finish(d: Session, work: () => void) {
    if (this.drag !== d) return;
    for (const stop of d.stops.splice(0)) stop();
    this.stopScroll();
    this.drag = null;
    this.pending = null;
    const shut = this.shutOver;
    this.shutOver = null;
    const run = () => {
      setDragging(false);
      this.props.onActive?.(null);
      work();
      this.notify(d.id);
      this.notify(shut);
    };
    if (this.alive && !this.committing) flushSync(run);
    else run();
    for (const id of d.touched) {
      const node = this.items.get(id)?.node;
      if (node) applyShift(node, NO_SHIFT);
    }
    this.release(d);
  }

  /** 浮层卸下：松手时起播的落位还没播完（又按下了、等太久）就等它播完；没起播过的（量不到等）从此刻落到那一项再卸。 */
  private release(d: Session) {
    const overlay = d.overlay;
    const remove = () => overlay.remove();
    const node = this.items.get(d.id)?.node ?? null;
    if (node && (early.has(overlay) || (this.transition && d.y !== 0))) {
      const shape = () => shapeOf(getComputedStyle(overlay).transform) ?? { x: 0, y: d.y, scaleX: 1, scaleY: 1 };
      const done = dropAnimation(this.transition, this.into)({
        active: { id: d.id, node, rect: d.start },
        dragOverlay: {
          node: overlay,
          get rect() {
            return boundsOf(measurableOf(overlay));
          },
        },
        get transform() {
          return shape();
        },
      });
      if (done) {
        void done.then(remove, remove);
        return;
      }
    }
    early.delete(overlay);
    remove();
  }

  /** 浮层停在只能高亮组头的落点上（那一列收着）：半透明，透出组头的高亮（与高亮同一次提交，useDropZone 里调）。 */
  peek(on: boolean) {
    const float = this.drag?.float;
    if (!float) return;
    if (on) float.dataset.peek = "";
    else delete float.dataset.peek;
  }
}

/** 键盘拿起时那一项完全在窗口之外：滚到中间（有滚动祖先时）。 */
function revealNode(node: HTMLElement) {
  const { top, left, bottom, right } = node.getBoundingClientRect();
  if (!scrollAncestors(node, 1).length) return;
  if (bottom <= 0 || right <= 0 || top >= window.innerHeight || left >= window.innerWidth) node.scrollIntoView({ block: "center", inline: "center" });
}

function entryOf(item: Item): Seen {
  return {
    id: item.id,
    data: { current: { drag: item.data, ...(item.zone || item.list === undefined ? {} : { sortable: { containerId: item.list } }) } },
  };
}

const AreaContext = createContext<DragEngine | null>(null);
// 不在拖动区里（不会发生）：一个永远不拖的
const IDLE = new DragEngine(null, false);
/** 这一项所在的那一列（DragList 的编号）。 */
const ListContext = createContext<string | undefined>(undefined);

/** 一块共用拖动的区域：三种输入、落点（只认能放的地方）、键盘移动、限位、浮层与放下动画、读屏播报、拖进另一列时整列滑动。拖动本身
 *  在 DragEngine 里，这个组件只把调用方给的最新回调交给它；拿起、放下都不重画它。 */
export const DragArea: FC<AreaProps> = ({ children, accepts, onDrop, onOver, onActive, onReturn, onPointer, flow, texts }) => {
  const engine = useMemo(() => new DragEngine(motion()), []);
  engine.props = { accepts, onDrop, onOver, onActive, onReturn, onPointer };
  engine.flow = flow;
  engine.setTexts(texts);
  // Alt+↑/↓ 挪一格的播报（第 5 轮：公司组头挪动时读屏什么也没念）：同拖动的播报区一样是 assertive、整段念；
  // 同一句连着两次（极少）末尾补一个不占位的空格，读屏才会再念
  const [said, setSaid] = useState("");
  engine.sayMove = useMemo(
    () => (name: string, spot: MoveSpot) => {
      const text = texts.movedTo(name, spot.at, spot.total);
      setSaid((before) => (before === text ? `${text} ` : text));
    },
    [texts],
  );
  useEffect(() => engine.mount(), [engine]);
  return (
    <AreaContext.Provider value={engine}>
      {children}
      <MoveLive text={said} />
    </AreaContext.Provider>
  );
};

/** 排序的一列（同一个 DragArea 里可以有几列：侧栏的置顶、公司组）。items 按内容记住：调用方每次重画都算出一份新数组，内容不变就当
 *  同一份。各项从这里知道自己在哪一列；列里的先后交给拖动区（让位、键盘、读屏的位置都按它）。 */
export const DragList: FC<{ id: string; items: string[]; children: ReactNode }> = ({ id, items, children }) => {
  const engine = useContext(AreaContext) ?? IDLE;
  const key = items.join("\u0000");
  const same = useMemo(() => items, [key]);
  useLayoutEffect(() => {
    engine.setList(id, same);
    return () => engine.dropList(id, same);
  }, [engine, id, same]);
  return <ListContext.Provider value={id}>{children}</ListContext.Provider>;
};

// ---------------------------------------------------------------- 各项
type HandleOptions = {
  disabled?: boolean;
  /** 把手本来就是能聚焦的按钮（侧栏的行、组头开关）：不加 role / tabIndex / 禁用标记，焦点仍归侧栏键盘管。 */
  ownFocus?: boolean;
  /** 从这些地方按下不拿起（行尾「···」、改名输入框）。 */
  skip?: string;
};

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

/** 把手的属性：读屏说明（aria-roledescription「sortable」、aria-describedby 指向拖动区的说明）；不带 ownFocus 的把手另有 role、
 *  tabIndex、aria-disabled，被拖时 aria-pressed。能拖时带 data-drag-handle 与按下的处理。 */
function handleOf(
  engine: DragEngine,
  id: string,
  ref: (el: HTMLElement | null) => void,
  { disabled, ownFocus, skip }: HandleOptions,
  dragging: boolean,
): DragHandle {
  const aria = { "aria-roledescription": "sortable", "aria-describedby": engine.describedBy };
  const attributes = {
    role: "button",
    tabIndex: 0,
    "aria-disabled": !!disabled,
    "aria-pressed": dragging ? true : undefined,
    ...aria,
  };
  const skipped = (event: { target: EventTarget | null }) => !!skip && !!(event.target as Element | null)?.closest?.(skip);
  return {
    ref,
    ...(ownFocus ? aria : attributes),
    // 拖动中按在把手上的方向键只归拖动（拖动在 document 上收）：标成已处理，接在后面的方向键导航（组件库对话行的 ↑ ↓）不接手
    onKeyDown: (event: ReactKeyboardEvent) => {
      if (isDragging() && DRAG_KEYS.has(event.key)) event.preventDefault();
      else if (!disabled) engine.press("keyboard", id, event);
    },
    ...(disabled
      ? {}
      : {
          // 能拖的把手：悬停出抓手光标（globals.css 的 [data-drag-handle]，各处同一条；不能拖时——搜索中、没存到服务端——不加）
          "data-drag-handle": "" as const,
          onMouseDown: (event: ReactMouseEvent) => !skipped(event) && engine.press("mouse", id, event),
          onTouchStart: (event: ReactTouchEvent) => !skipped(event) && engine.press("touch", id, event),
          // 空格是拿起 / 放下：松开空格不再当成点击（不打开对话、不折叠组）
          onKeyUp: (event: ReactKeyboardEvent) => event.code === "Space" && event.preventDefault(),
        }),
  };
}

function useItem(engine: DragEngine, data: DragData, zone: boolean): Item {
  const own = useRef<Item | null>(null);
  if (!own.current) {
    const item: Item = { id: data.id, data, list: undefined, disabled: false, zone, node: null, handle: null, entry: null as unknown as Seen };
    item.entry = entryOf(item);
    own.current = item;
  }
  const item = own.current;
  useLayoutEffect(() => () => engine.unregister(item), [engine, item]);
  return item;
}

/** 排序列表里的一项：node 给整项（位移、原位留空），handle 给把手（拿起的地方）。data-drag-id 让拖进另一列、在那里重挂的同一项
 *  接着滑（flowLayout）。
 *
 *  各项只订阅「自己是不是被拖的那一项」：拿起、落定只有被拖的那一项重画（data-drag-source、键盘拿起时焦点跟着它）。回的几样按
 *  内容记住，拖动中不变；让位的位移与过渡由拖动区直接写到节点上（applyShift），不经 React（测试窗口第 11 轮 B14：原来位移、过渡
 *  放在 node 的 style 里，拖动一开始排序整列行与组头全部重画）。 */
export function useSortableItem(data: DragData, options: HandleOptions = {}) {
  const engine = useContext(AreaContext) ?? IDLE;
  const list = useContext(ListContext);
  const drag = useMemo(() => data, [data.id, data.kind, data.label, data.list, data.group, data.count, data.session]);
  const item = useItem(engine, drag, false);
  const { disabled = false, ownFocus, skip } = options;
  const subscribe = useCallback((run: () => void) => engine.subscribe(drag.id, run), [engine, drag.id]);
  const state = useSyncExternalStore(subscribe, () => engine.stateOf(drag.id), () => 0);
  const isDragging = state !== 0;
  // 每次提交都登记一下（节点重挂过的、换了列的也在这次提交里补上；等着挪回原列的取消据此起播滑回）
  useLayoutEffect(() => engine.register(item, drag, list, disabled));
  // 键盘拖动中焦点一直在被拖的这一项的把手上
  useLayoutEffect(() => {
    if (state === 2) engine.follow(item);
  }, [engine, item, state]);
  const ref = useCallback((node: HTMLElement | null) => engine.attach(item, node), [engine, item]);
  const handleRef = useCallback((node: HTMLElement | null) => engine.attachHandle(item, node), [engine, item]);
  // 此刻排在哪一列（拖进另一列、在那里重挂的那一项是那一列，不是它说明里原来那一列）：取消时认它挪回了原来那一列、滑回时只清这一列
  // 的让位位移，都按它
  const where = list ?? drag.list;
  const node = useMemo(
    () => ({
      ref,
      "data-drag-id": drag.id,
      "data-drag-list": where,
      "data-drag-source": isDragging ? ("gap" as const) : undefined,
    }),
    [ref, drag.id, where, isDragging],
  );
  const handle = useMemo(
    () => handleOf(engine, drag.id, handleRef, { disabled, ownFocus, skip }, isDragging),
    [engine, drag.id, handleRef, disabled, ownFocus, skip, isDragging],
  );
  const label = drag.label;
  const say = engine.sayMove;
  /** Alt+↑/↓ 挪完：念新位置（没挪动——到头了——不念）。 */
  const moved = useCallback((spot: MoveSpot | null | void) => {
    if (spot) say(label, spot);
  }, [say, label]);
  return useMemo(() => ({ moved, node, handle, dragging: isDragging }), [moved, node, handle, isDragging]);
}

/** 让位（位移、过渡）换成要写到节点上的行内样式：取消后滑回原位的途中（settling）不写位移；位移取整后是零当作没有位移——写出来
 *  虽然不动，那一项也要重算一次样式（第 11 轮 B14 审查）。位移按浏览器读回的写法拼（第三个参数 0px）。 */
export function shiftStyle(transform: { x: number; y: number } | null | undefined, transition: string | null | undefined, settling = false) {
  const x = transform ? Math.round(transform.x) : 0;
  const y = transform ? Math.round(transform.y) : 0;
  return { transform: settling || (!x && !y) ? "" : `translate3d(${x}px, ${y}px, 0px)`, transition: transition ?? "" };
}

// 每个节点上次由 applyShift 写的值（raw）与写完浏览器读回的（seen：浏览器会改写法，如 0 → 0px、过渡补全默认值）。读回的还是
// seen 就按 raw 比，不拿读回的写法与要写的比（那样永远不等，每次都重写一遍）；别处改过（滑回时 settleBack 直接清位移）
// 读回的就不是 seen，按读回的比
const written = new WeakMap<CSSStyleDeclaration, { transform: [string, string]; transition: [string, string] }>();

/** 把让位的位移与过渡写到那一项的节点上（空串 = 去掉）。过渡只跟着位移写：位移要变（或身上还带着位移）时先写过渡、再写位移，
 *  同一次样式计算里位移的变化按新的过渡走；没在让位的项不写。身上留着过渡、已经不在让位的，结束时清掉。同样的值不写第二遍
 *  （按上次写的比，见 written）。 */
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
 *  高亮、浮层半透明，放下落到组头并收起。ref 给整块（组头 + 这一列的各项）。只订阅「是不是被指着的收着的那一块」。 */
export function useDropZone(data: DragData, shut = false) {
  const engine = useContext(AreaContext) ?? IDLE;
  const zone = useMemo(() => ({ ...data, zone: true, shut }), [data.id, data.kind, data.label, data.list, shut]);
  const item = useItem(engine, zone, true);
  const subscribe = useCallback((run: () => void) => engine.subscribe(zone.id, run), [engine, zone.id]);
  const over = useSyncExternalStore(subscribe, () => engine.shutOver === zone.id, () => false);
  useLayoutEffect(() => engine.register(item, zone, undefined, false));
  // 浮层停在只能高亮组头的落点上：浮层变半透明，透出组头的高亮，离开就恢复（测试窗口第 7 轮：手机上浮层正好盖住收着的组头）
  useLayoutEffect(() => {
    if (!over || !shut) return;
    engine.peek(true);
    return () => engine.peek(false);
  }, [engine, over, shut]);
  const ref = useCallback((node: HTMLElement | null) => engine.attach(item, node), [engine, item]);
  return { ref, "data-drop-target": over && shut ? ("" as const) : undefined };
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
