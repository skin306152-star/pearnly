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
// 其余部件（Root、Trigger、Item、Separator、Portal …）与组件库同一个。

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { DropdownMenu as Radix } from "radix-ui";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
  type ComponentProps,
  type ReactNode,
  type Ref,
} from "react";
import { createPortal } from "react-dom";
import { useIsMobile } from "@/hooks/use-mobile";

/** 菜单离屏幕边的最小距离（px）。 */
export const MENU_EDGE = 8;

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

function Content({ collisionPadding = MENU_EDGE, side, sideOffset = 0, ref, children, ...props }: ComponentProps<typeof Radix.Content>) {
  const narrow = useIsMobile();
  const [open, setOpen] = useState<string | null>(null);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const pages = useMemo(() => ({ open, setOpen, host }), [open, host]);
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [chosen, setChosen] = useState<Side | null>(null);
  const setRef = useCallback((node: HTMLDivElement | null) => setEl(node), []);
  const merged = useMemo(() => mergeRefs<HTMLDivElement>(setRef, ref), [setRef, ref]);
  // 绘制前按完整高度选上 / 下（组件库定位前菜单在屏外，不会闪）；只管上下开的菜单，旁边展开的子菜单不动
  useBrowserLayoutEffect(() => {
    if (!el || (side && side !== "top" && side !== "bottom")) return;
    const id = el.getAttribute("aria-labelledby");
    const trigger = id ? document.getElementById(id) : null;
    if (!trigger) return;
    const box = trigger.getBoundingClientRect();
    const view = window.visualViewport;
    const top = view?.offsetTop ?? 0;
    const bottom = top + (view?.height ?? window.innerHeight);
    const gap = sideOffset + MENU_EDGE;
    const style = getComputedStyle(el);
    const borders = (Number.parseFloat(style.borderTopWidth) || 0) + (Number.parseFloat(style.borderBottomWidth) || 0);
    const need = Math.min(el.scrollHeight + borders, lengthPx(style.getPropertyValue("--cc-menu-max")));
    setChosen(pickSide(side ?? "bottom", need, bottom - box.bottom - gap, box.top - top - gap));
  }, [el, side, sideOffset, open]);
  return (
    <Radix.Content
      ref={merged}
      collisionPadding={collisionPadding}
      side={chosen ?? side}
      sideOffset={sideOffset}
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

function SubContent({ collisionPadding = MENU_EDGE, ...props }: ComponentProps<typeof Radix.SubContent>) {
  return <Radix.SubContent collisionPadding={collisionPadding} {...props} />;
}

/** 子菜单（「移到 ›」「语言 ›」…）。className、sideOffset：宽屏旁边展开的那一块。 */
export function SubMenu({
  icon,
  label,
  className = "cc-menu",
  sideOffset = 6,
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
          <SubContent sideOffset={sideOffset} className={className}>
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
