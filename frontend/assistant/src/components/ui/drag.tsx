// 共用拖动（用户 10-15 晚：拖动要「全部参考视频，非常丝滑」；10-16：「所有拖拽的地方要改统一规则和效果」。参考 Claude 桌面版
// 右侧栏拖卡片的录屏 IMG_5192）。会话栏卡片（session-panel.tsx）、侧栏公司组排序、置顶对话排序、对话拖到别的公司
// （thread-list.tsx）都走这一份；修一处就在这里修。基于 dnd-kit（MIT，版本锁在 package.json）。
//
//   拿起   鼠标按住动 3px（点一下照样是点）；手机长按 250ms 且没挪出 5px（没到时间就动 = 在滚动）；键盘 空格。把手 / 抓手光标
//          由各处样式给（悬停出把手，拖动中整页 grabbing）。侧栏的行与组头长按另有菜单（touch="move"）：按住 250ms 后再动才拿起，
//          按住不动到 500ms 照旧出长按菜单，菜单出来后就不再拿起，两者不冲突。
//   浮起   拿起的那一项换成浮层（DragOverlay，克隆原来那一项的样子，挂在 body 上）：弹层底、弹出阴影、略透明、略放大，实时跟手。
//          排序时原位留空（其余各项拖动中就带过渡滑开让位，过渡用动效变量）；拖到别的组时原位留淡影（没有谁让位），
//          落点那一组高亮（悬停色）。
//   放下   松手当帧就从放手处平滑落到新位置（拖动库在放下那次提交里用 Web Animations 播，不等之后的重排；动效变量），
//          没落点 / 取消就滑回原位。第 4 轮测试机：原来拿起的就是列表里那张卡本身，放下靠自写的 translate 过渡，要等新顺序提交、
//          整列重测之后的下一次样式计算才开始，手机上卡在放手处停了约 0.4s、压着下一张卡才滑进空位。
//   范围   只在本列竖着拖：浮层上下边不出这一列（排序：这一列第一项顶边到最后一项底边；拖到别的组：能放的各组加原位）。
//          浮层是 fixed、不撑大滚动区，拖到边上自动滚动只到列表首尾（第 4 轮：原来限位只限住指针位移，拖动库随后又把滚动差
//          加到那张卡上，卡被推出列表、撑大了可滚范围，会话栏一路滚进空白）。
//   键盘   空格拿起、方向键移动、空格（或回车）放下、Esc 取消，三种语言读屏播报（文案由调用方给）。Esc 只取消拖动：不关浮着的
//          会话栏、不清侧栏多选（lib/layers.ts 的 isDragEscape）。
//   动效   时长、缓动取 globals.css 的 --cc-dur / --cc-ease；系统或设置要求减少动态效果时不播过渡。
//
// 样式：globals.css 的 .cc-drag-*、[data-drag-source]、[data-drop-target]；这里只有拖动算出的位移是行内样式。

import {
  type Active,
  type Announcements,
  type ClientRect,
  closestCenter,
  type CollisionDetection,
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DropAnimation,
  type DroppableContainer,
  type DroppableContainers,
  getClientRect,
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
} from "react";
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

/** dnd-kit 的读屏播报：名字用 label；排序时位置按这一列（看得见的）算，拖到别的组时念组名。 */
export function dragAnnouncements(texts: DragTexts): Announcements {
  const name = (entry: WithData) => dataOf(entry)?.label ?? "";
  const say = (active: Active, over: Over | null, end: boolean) => {
    if (!over) return end ? texts.cancelled(name(active)) : undefined;
    const from = dataOf(active);
    const to = dataOf(over);
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
      if (document.querySelector(OPEN_LAYERS)) return this.cancel(); // 长按菜单已经出来：不拿起
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

// ---------------------------------------------------------------- 拖动区
type AreaProps = {
  children: ReactNode;
  /** 拿着 active 能不能放到 target 上（排序：同一列；侧栏对话：别的公司组 / 最近）。 */
  accepts: (active: DragData, target: DragData) => boolean;
  /** 放下了（落在能放的地方、且不是原位）。 */
  onDrop: (active: DragData, target: DragData) => void;
  /** 拿起 / 结束（放下或取消）：侧栏要在拖对话时临时显示空的「最近」、多选时让其余选中的行变淡。 */
  onActive?: (active: DragData | null) => void;
  texts: DragTexts;
  /** 手机长按：hold = 按住就拿起；move = 按住后再动才拿起（按住不动留给长按菜单）。 */
  touch?: "hold" | "move";
};

/** 一块共用拖动的区域：传感器、碰撞（只认能放的地方）、键盘移动、限位、浮层与放下动画、读屏播报。 */
export const DragArea: FC<AreaProps> = ({ children, accepts, onDrop, onActive, texts, touch = "hold" }) => {
  const latest = useRef({ accepts, onDrop, onActive });
  latest.current = { accepts, onDrop, onActive };
  const transition = useMemo(motion, []);

  // 拖动中要用的：拿起时的位置（浮层固定在那里，位移从它算）、能放的各处与原来那一项的节点（限位用）
  const first = useRef<ClientRect | null>(null);
  const stash = useRef<{ containers: DroppableContainers | null; node: HTMLElement | null }>({ containers: null, node: null });

  const allowed = (active: DragData, entry: DroppableContainer, activeId: string | number) => {
    const target = dataOf(entry);
    return !!target && (entry.id === activeId ? !!active.list : latest.current.accepts(active, target));
  };

  const collisions = useMemo<CollisionDetection>(
    () => (args) => {
      const active = dataOf(args.active);
      if (!active) return [];
      const droppableContainers = args.droppableContainers.filter((entry) => allowed(active, entry, args.active.id));
      if (active.list) return closestCenter({ ...args, droppableContainers });
      // 拖到别的组：指针（键盘拖时是浮层中心）落在哪一组
      const rect = args.collisionRect;
      const pointerCoordinates = args.pointerCoordinates ?? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      return pointerWithin({ ...args, droppableContainers, pointerCoordinates });
    },
    [],
  );

  const coordinates = useMemo<KeyboardCoordinateGetter>(
    () => (event, args) => {
      const { context } = args;
      const active = dataOf(context.active);
      if (!active || !context.active) return undefined;
      const activeId = context.active.id;
      const enabled = () => context.droppableContainers.getEnabled().filter((entry) => allowed(active, entry, activeId));
      if (active.list) {
        const view = { getEnabled: enabled, get: (id: string | number) => context.droppableContainers.get(id) } as unknown as DroppableContainers;
        return sortableKeyboardCoordinates(event, { ...args, context: { ...context, droppableContainers: view } });
      }
      // 拖到别的组：↑ ↓ 跳到上一个 / 下一个能放的组（浮层中心落进那一组的顶部）
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

  // 只能竖着拖，浮层上下边不出这一列（排序：这一列各项；拖到别的组：能放的各组 + 原位）
  const keep = useMemo<Modifier>(
    () => ({ transform, active, activeNodeRect }) => {
      if (!active) {
        first.current = null;
        return transform;
      }
      if (!first.current && activeNodeRect) first.current = activeNodeRect;
      const start = first.current;
      const data = dataOf(active);
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
    () => ({ announcements: dragAnnouncements(texts), screenReaderInstructions: { draggable: texts.instructions } }),
    [texts],
  );

  useEffect(() => () => setDragging(false), []);
  const finish = () => {
    setDragging(false);
    latest.current.onActive?.(null);
  };
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    finish();
    const from = dataOf(active);
    const to = dataOf(over);
    if (!from || !to || !over || over.id === active.id) return;
    if (!latest.current.accepts(from, to)) return;
    latest.current.onDrop(from, to);
  };
  return (
    <MotionContext.Provider value={transition}>
      <DndContext
        sensors={sensors}
        collisionDetection={collisions}
        modifiers={modifiers}
        accessibility={accessibility}
        onDragStart={({ active }) => {
          setDragging(true);
          const data = dataOf(active);
          if (data) latest.current.onActive?.(data);
        }}
        onDragCancel={finish}
        onDragEnd={onDragEnd}
      >
        {children}
        <Lifted stash={stash} transition={transition} />
      </DndContext>
    </MotionContext.Provider>
  );
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
// 第 5 轮实测会话栏滚到底时浮层被带出了可视区）。原来那一项从祖先继承来的字体、字色照抄到克隆上，看起来同原来一样
const INHERITED = ["color", "font-family", "font-size", "font-weight", "line-height", "letter-spacing", "direction"];

function cloneOf(node: HTMLElement): HTMLElement {
  const copy = node.cloneNode(true) as HTMLElement;
  for (const el of [copy, ...copy.querySelectorAll<HTMLElement>("*")]) for (const name of STRIP) el.removeAttribute(name);
  for (const name of ["transform", "transition", "opacity", "margin"]) copy.style.removeProperty(name);
  const style = getComputedStyle(node);
  for (const name of INHERITED) copy.style.setProperty(name, style.getPropertyValue(name));
  return copy;
}

const Copy: FC<{ node: HTMLElement | null }> = ({ node }) => {
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || !node) return;
    el.replaceChildren(cloneOf(node));
  }, [node]);
  return <div ref={box} className="cc-drag-copy" />;
};

const Lifted: FC<{ stash: { current: { containers: DroppableContainers | null; node: HTMLElement | null } }; transition: Motion | null }> = ({
  stash,
  transition,
}) => {
  const { active, activeNode, droppableContainers } = useDndContext();
  stash.current = { containers: droppableContainers, node: activeNode };
  const data = dataOf(active);
  // 放下：浮层从放手处滑到落点，落点那一项在动画播完前不显示（不会一份在落点、一份在路上）；浮层的略放大、阴影同时收回
  const dropAnimation = useMemo<DropAnimation | null>(
    () =>
      transition && {
        duration: transition.duration,
        easing: transition.easing,
        sideEffects: ({ active: landing, dragOverlay }) => {
          landing.node.dataset.dragLanding = "";
          dragOverlay.node.dataset.dropping = "";
          return () => {
            delete landing.node.dataset.dragLanding;
          };
        },
      },
    [transition],
  );
  // 键盘拖动时浮层一格一格挪，也用动效变量过渡
  const keyboardMove = (event: Event | null) =>
    transition && event && "key" in event ? `transform ${transition.duration}ms ${transition.easing}` : undefined;

  const overlay = (
    <DragOverlay dropAnimation={dropAnimation} transition={keyboardMove} className="cc-drag-overlay">
      {active ? (
        <div className="cc-drag-float" data-kind={data?.kind} inert aria-hidden>
          <div className="cc-drag-lift">
            <Copy node={activeNode} />
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
};

type Draggable = Pick<ReturnType<typeof useDraggable>, "attributes" | "listeners" | "setActivatorNodeRef">;

export type DragHandle = { ref: (el: HTMLElement | null) => void } & Pick<
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
  const crossing = sortable.isOver && !!sortable.active && dataOf(sortable.active)?.list !== data.list;
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

/** 只拖不排序的一项（侧栏对话拖到别的公司）：原位留淡影，浮层跟手。 */
export function useDragItem(data: DragData, options: HandleOptions = {}) {
  const drag = useMemo(() => ({ drag: data }), [data.id, data.kind, data.label, data.list, data.group, data.count, data.session]);
  const draggable = useDraggable({ id: data.id, data: drag, disabled: options.disabled });
  return {
    node: { ref: draggable.setNodeRef, "data-drag-source": draggable.isDragging ? "ghost" : undefined },
    handle: handleOf(draggable, options),
    dragging: draggable.isDragging,
  };
}

/** 只能放不能拖的一处（侧栏的「最近」）：拿着能放的东西经过时高亮。 */
export function useDropTarget(data: DragData) {
  const drag = useMemo(() => ({ drag: data }), [data.id, data.kind, data.label, data.group]);
  const droppable = useDroppable({ id: data.id, data: drag });
  return { ref: droppable.setNodeRef, "data-drop-target": droppable.isOver && !!droppable.active ? "" : undefined };
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
