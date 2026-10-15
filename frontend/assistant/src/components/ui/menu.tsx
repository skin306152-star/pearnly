// 下拉菜单（组件库 Radix DropdownMenu）的统一入口：页面一律从这里取 DropdownMenu，不直接从 radix-ui 取。
//
//   · 离屏幕边 8px（collisionPadding）：放不下时组件库自己翻面、平移，不贴边、不被截掉（2026-10-15 三星 A14 360 宽实测
//     「公司 ▾」贴右边、头像菜单的「语言 ›」「了解更多 ›」溢出）；高度放不下时菜单里滚动（globals.css 的 .cc-menu）。
//   · 子菜单用 SubMenu：宽屏在旁边展开（组件库原样）；窄屏（手机，同侧栏的 768 分界）子菜单不往旁边开 —— 组件库的子菜单
//     只会左右展开、不做横向平移，手机上总有一边被截掉（「移到 ›」向右被截、对话页「…」里的「移到 ›」向左被截）——
//     改为在原菜单里换成子菜单的内容，顶上一行「‹ 名称」回到上一层。
// 其余部件（Root、Trigger、Item、Separator、Portal …）与组件库同一个。

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { DropdownMenu as Radix } from "radix-ui";
import { createContext, useContext, useEffect, useId, useMemo, useState, type ComponentProps, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useIsMobile } from "@/hooks/use-mobile";

/** 菜单离屏幕边的最小距离（px）。 */
export const MENU_EDGE = 8;

type Pages = { open: string | null; setOpen: (id: string | null) => void; host: HTMLElement | null };
const PagesContext = createContext<Pages | null>(null);

function Content({ collisionPadding = MENU_EDGE, children, ...props }: ComponentProps<typeof Radix.Content>) {
  const narrow = useIsMobile();
  const [open, setOpen] = useState<string | null>(null);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const pages = useMemo(() => ({ open, setOpen, host }), [open, host]);
  return (
    <Radix.Content collisionPadding={collisionPadding} {...props}>
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
