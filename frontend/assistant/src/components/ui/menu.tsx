// 下拉菜单（组件库 Radix DropdownMenu）的统一入口：页面一律从这里取 DropdownMenu，不直接从 radix-ui 取。
//
//   · 离屏幕边 8px（collisionPadding）：放不下时组件库自己平移，不贴边、不被截掉（2026-10-15 三星 A14 360 宽实测
//     「公司 ▾」贴右边、头像菜单的「语言 ›」「了解更多 ›」溢出）；高度放不下时菜单里滚动（globals.css 的 .cc-menu）。
//   · 上下翻面（F11d）：.cc-menu 按可用高度限高之后，组件库量到的总是「放得下」，自己不会翻面（靠下的行菜单只收缩）。
//     所以这里按内容的完整高度（scrollHeight，不受限高影响；页面写的 [--cc-menu-max:…] 封顶）自己选上 / 下（pickSide）：
//     原方向放得下不动、反方向放得下就翻、两边都放不下取空间大的一边在菜单里滚动。窄屏换子菜单那一页时重新选。
//   · 子菜单用 SubMenu：宽屏在旁边展开（组件库原样）；窄屏（手机，同侧栏的 768 分界）子菜单不往旁边开 —— 组件库的子菜单
//     只会左右展开、不做横向平移，手机上总有一边被截掉（「移到 ›」向右被截、对话页「…」里的「移到 ›」向左被截）——
//     改为在原菜单里换成子菜单的内容，顶上一行「‹ 名称」回到上一层。
//   · 触屏点菜单外（测试窗口第 10 轮 B 段）：只关菜单，这一下不再落到下面（同原生菜单、电脑鼠标）。组件库在按下时就关菜单、
//     撤掉整页的 pointer-events: none，手机随后补发的 click 按松手时的位置找元素，就落到了下面那一行上（长按菜单开着时点另一条
//     对话：菜单关了、那条对话也打开了）。鼠标不会：按下时落在页面根上（整页还不接指针），按下、松开不在同一个元素上就没有
//     click（同 Claude）。所以只在非鼠标按下关菜单时吞掉紧跟着的那一次 click（swallowNextClick）。各处菜单都从这里取，一致。
//     只管整个菜单（Content）：宽屏旁边展开的子菜单开着时点回父菜单的另一项，那一下要照常选中，不吞。
//     手机长按开的菜单不按模态开（hooks/use-long-press.ts 的 useLongPressMenu：省掉出菜单那一帧的整页样式重算），整页照常接指针，
//     点菜单外那一下的 click 同样由这里吞掉。
//   · 弹层（Portal）直接给 container（document.body，同组件库的默认位置）：菜单内容在打开的那一遍就渲染出来。组件库不给 container
//     时第一遍先渲染空、等布局副作用 setMounted 再同步重画一遍（第 13 轮 A14 trace：长按出菜单多一遍渲染与提交）。服务端渲染照旧。
// 其余部件（Root、Trigger、Item、Separator …）与组件库同一个。

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { DropdownMenu as RadixMenu } from "radix-ui";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ComponentProps,
  type ReactNode,
  type Ref,
} from "react";
import { createPortal } from "react-dom";
import { useIsMobile } from "@/hooks/use-mobile";
import { fixedPx } from "@/lib/css-var";

const unsubscribed = () => () => undefined;

/** 菜单的弹层：挂到 body，第一遍就渲染内容（见文件头）。服务端渲染（测试）不给 container，同组件库。 */
function Portal({ container, ...props }: ComponentProps<typeof RadixMenu.Portal>) {
  const client = useSyncExternalStore(unsubscribed, () => true, () => false);
  return <RadixMenu.Portal container={container ?? (client ? document.body : undefined)} {...props} />;
}

// 组件库的下拉菜单，弹层换成上面那个（本文件里的子菜单同样用它）
const Radix = { ...RadixMenu, Portal };

// 菜单离触发器、子菜单离父菜单、离屏幕边的距离只在 globals.css（--cc-menu-offset / --cc-menu-sub-offset / --cc-menu-edge，标准化记录 2.6）：
// 菜单打开时读（这一层只在菜单开着时渲染），读到就记住（lib/css-var.ts 的 fixedPx）；样式表还没生效时用下面的回退值，与 globals.css 同值（测试核对）。
// 调用方要写离触发器的距离时用 menuOffset()，不写数。

/** 菜单离屏幕边的最小距离（px）：回退值。 */
export const MENU_EDGE = 8;
/** 主菜单离触发器（px）：回退值。 */
export const MENU_OFFSET = 4;
/** 子菜单离父菜单（px）：回退值。 */
export const MENU_SUB_OFFSET = 6;

export const menuEdge = () => fixedPx("--cc-menu-edge", MENU_EDGE);
export const menuOffset = () => fixedPx("--cc-menu-offset", MENU_OFFSET);
export const menuSubOffset = () => fixedPx("--cc-menu-sub-offset", MENU_SUB_OFFSET);

type Pages = { open: string | null; setOpen: (id: string | null) => void; host: HTMLElement | null };
const PagesContext = createContext<Pages | null>(null);

type Side = "top" | "bottom";

/** 菜单开在哪一边：pref 原方向，need 菜单要的高度，below / above 触发按钮下方 / 上方的可用高度（已减去间距与离边距离）。 */
export function pickSide(pref: Side, need: number, below: number, above: number): Side {
  const flip: Side = pref === "bottom" ? "top" : "bottom";
  const room = pref === "bottom" ? below : above;
  const other = pref === "bottom" ? above : below;
  if (need <= room) return pref; // 原方向放得下
  if (need <= other) return flip; // 反方向放得下就翻
  return other > room ? flip : pref; // 两边都放不下：取大的一边，菜单里滚动
}

/** CSS 长度（--cc-menu-max 的写法：20rem / 320px）换成 px；没写或认不出 = 不封顶。 */
function lengthPx(value: string): number {
  const match = /^([\d.]+)(px|rem)$/.exec(value.trim());
  if (!match) return Infinity;
  const size = Number(match[1]);
  return match[2] === "px" ? size : size * (Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16);
}

function mergeRefs<T>(...refs: Array<Ref<T> | undefined>) {
  return (node: T | null) => {
    for (const ref of refs) {
      if (typeof ref === "function") ref(node);
      else if (ref) ref.current = node;
    }
  };
}

// 服务端渲染（测试）没有布局：effect 不跑
const useBrowserLayoutEffect = typeof document !== "undefined" && "createElement" in document ? useLayoutEffect : useEffect;

/** 按下到补发的 click 之间最多等这么久（手机点按约 100–300ms；超过就是在滚动、拖动，没有 click）。 */
export const OUTSIDE_CLICK_MS = 800;

/** 吞掉接下来的那一次 click（捕获阶段、在页面上最先收到）：点菜单外关菜单的这一下不再落到下面。没等到 click（手指滑动成了
 *  滚动）就到时摘掉；下一次按下是新的一下，也摘掉。 */
export function swallowNextClick(target: Window = window, wait = OUTSIDE_CLICK_MS): () => void {
  const stop = (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
    done();
  };
  const capture = { capture: true };
  const done = () => {
    target.removeEventListener("click", stop, capture);
    target.removeEventListener("pointerdown", done, capture);
    clearTimeout(timer);
  };
  target.addEventListener("click", stop, capture);
  target.addEventListener("pointerdown", done, capture);
  const timer = setTimeout(done, wait);
  return done;
}

type OutsideEvent = Parameters<NonNullable<ComponentProps<typeof Radix.Content>["onPointerDownOutside"]>>[0];

/** 菜单外按下（组件库随后关菜单）：不是鼠标、真的要关（调用方没拦）时，这一下的 click 不再落到下面。 */
export function closeWithoutClickThrough(event: OutsideEvent) {
  const pointer = (event.detail?.originalEvent as PointerEvent | undefined)?.pointerType;
  if (!event.defaultPrevented && pointer && pointer !== "mouse" && typeof window !== "undefined") swallowNextClick();
}

function Content({
  collisionPadding = menuEdge(),
  side,
  sideOffset = 0,
  ref,
  children,
  onPointerDownOutside,
  ...props
}: ComponentProps<typeof Radix.Content>) {
  const narrow = useIsMobile();
  const [open, setOpen] = useState<string | null>(null);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const pages = useMemo(() => ({ open, setOpen, host }), [open, host]);
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [chosen, setChosen] = useState<Side | null>(null);
  const setRef = useCallback((node: HTMLDivElement | null) => setEl(node), []);
  const merged = useMemo(() => mergeRefs<HTMLDivElement>(setRef, ref), [setRef, ref]);
  // 绘制前按完整高度选上 / 下（组件库定位前菜单在屏外，不会闪）；只管上下开的菜单，旁边展开的子菜单不动。
  // 打开期间内容变高（异步加载出更多项）、键盘弹出 / 收起（visualViewport 变化）时重选；离边距离按传进来的 collisionPadding
  const sideways = side === "left" || side === "right";
  const edgeTop = typeof collisionPadding === "number" ? collisionPadding : (collisionPadding?.top ?? 0);
  const edgeBottom = typeof collisionPadding === "number" ? collisionPadding : (collisionPadding?.bottom ?? 0);
  useBrowserLayoutEffect(() => {
    if (!el || sideways) return;
    const id = el.getAttribute("aria-labelledby");
    const trigger = id ? document.getElementById(id) : null;
    if (!trigger) return;
    const measure = () => {
      const box = trigger.getBoundingClientRect();
      const view = window.visualViewport;
      const top = view?.offsetTop ?? 0;
      const bottom = top + (view?.height ?? window.innerHeight);
      const style = getComputedStyle(el);
      const borders = (Number.parseFloat(style.borderTopWidth) || 0) + (Number.parseFloat(style.borderBottomWidth) || 0);
      const need = Math.min(el.scrollHeight + borders, lengthPx(style.getPropertyValue("--cc-menu-max")));
      const below = bottom - box.bottom - sideOffset - edgeBottom;
      const above = box.top - top - sideOffset - edgeTop;
      setChosen(pickSide(side ?? "bottom", need, below, above));
    };
    measure();
    // 菜单本身变高（还没到限高）用 ResizeObserver；限高后再加项只改 scrollHeight，靠 MutationObserver 看增减的项
    const resized = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resized?.observe(el);
    const changed = new MutationObserver(measure);
    changed.observe(el, { childList: true, subtree: true });
    window.visualViewport?.addEventListener("resize", measure);
    return () => {
      resized?.disconnect();
      changed.disconnect();
      window.visualViewport?.removeEventListener("resize", measure);
    };
  }, [el, side, sideways, sideOffset, edgeTop, edgeBottom, open]);
  return (
    <Radix.Content
      ref={merged}
      collisionPadding={collisionPadding}
      side={sideways ? side : (chosen ?? side)}
      sideOffset={sideOffset}
      onPointerDownOutside={(event) => {
        onPointerDownOutside?.(event);
        closeWithoutClickThrough(event);
      }}
      {...props}
    >
      {narrow ? (
        <PagesContext.Provider value={pages}>
          <div data-slot="cc-menu-page" style={{ display: open ? "none" : "contents" }}>
            {children}
          </div>
          <div ref={setHost} data-slot="cc-menu-subpage" style={{ display: open ? "contents" : "none" }} />
        </PagesContext.Provider>
      ) : (
        children
      )}
    </Radix.Content>
  );
}

function SubContent({ collisionPadding = menuEdge(), ...props }: ComponentProps<typeof Radix.SubContent>) {
  return <Radix.SubContent collisionPadding={collisionPadding} {...props} />;
}

/** 子菜单（「处理月份 ›」「语言 ›」…）。className、sideOffset：宽屏旁边展开的那一块（默认离父菜单 --cc-menu-sub-offset）。 */
export function SubMenu({
  icon,
  label,
  className = "cc-menu",
  sideOffset,
  children,
}: {
  icon?: ReactNode;
  label: ReactNode;
  className?: string;
  sideOffset?: number;
  children: ReactNode;
}) {
  const pages = useContext(PagesContext);
  const id = useId();
  const open = pages?.open === id;
  const host = pages?.host ?? null;
  // 换到子菜单后，焦点落到「‹ 返回」那一行（原来那一行已经藏起来）
  useEffect(() => {
    if (open) host?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open, host]);
  const row = (
    <>
      {icon}
      <span className="flex-1">{label}</span>
      <ChevronRightIcon className="text-ink-3" />
    </>
  );
  if (!pages) {
    return (
      <Radix.Sub>
        <Radix.SubTrigger className="cc-menu-item">{row}</Radix.SubTrigger>
        <Radix.Portal>
          <SubContent sideOffset={sideOffset ?? menuSubOffset()} className={className}>
            {children}
          </SubContent>
        </Radix.Portal>
      </Radix.Sub>
    );
  }
  return (
    <>
      <Radix.Item
        className="cc-menu-item"
        data-slot="cc-menu-sub"
        onSelect={(event) => {
          event.preventDefault(); // 菜单不关，换成子菜单
          pages.setOpen(id);
        }}
      >
        {row}
      </Radix.Item>
      {open &&
        host &&
        createPortal(
          <>
            <Radix.Item
              className="cc-menu-item"
              data-slot="cc-menu-back"
              onSelect={(event) => {
                event.preventDefault();
                pages.setOpen(null);
              }}
            >
              <ChevronLeftIcon />
              <span className="flex-1">{label}</span>
            </Radix.Item>
            <Radix.Separator className="cc-menu-sep" />
            {children}
          </>,
          host,
        )}
    </>
  );
}

export const DropdownMenu = { ...Radix, Content, SubContent };
