// 组件库的侧栏（shadcn）：只留在用的几块——Provider（展开 / 收起状态、⌘B、手机抽屉开关）、电脑上的侧栏本体（收起时整块移到屏外，
// offcanvas）、对话区（Inset）、侧栏的头 / 主体 / 脚。没人用的菜单、分组、搜索框、分隔线、拖边条、随机宽度骨架、图标收起档与
// 浮起 / 内嵌两种外观都已删（标准化记录 B2）；手机上的侧栏是 mobile-shell.tsx 的抽屉，这里不再有手机分支（原来那份 Sheet 从来没渲染过：
// assistant.tsx 在手机上不挂 <Sidebar>）。侧栏宽只在 globals.css（--cc-sidebar-w），拖分隔条后由页面在 Provider 上写本机记下的值
// （split-handle.tsx 的 sidebarSize）。
import * as React from "react";

import { useIsMobile } from "@/hooks/use-mobile";
import { handOffSidebarFocus, takeBackSidebarFocus, type Handed } from "@/lib/sidebar-focus";
import { cn } from "@/lib/utils";

const SIDEBAR_COOKIE_NAME = "sidebar_state";
const SIDEBAR_COOKIE_MAX_AGE = 60 * 60 * 24 * 7;
const SIDEBAR_KEYBOARD_SHORTCUT = "b";

type SidebarContextProps = {
  state: "expanded" | "collapsed";
  open: boolean;
  setOpen: (open: boolean) => void;
  openMobile: boolean;
  setOpenMobile: (open: boolean) => void;
  isMobile: boolean;
  toggleSidebar: () => void;
};

const SidebarContext = React.createContext<SidebarContextProps | null>(null);

function useSidebar() {
  const context = React.useContext(SidebarContext);
  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider.");
  }

  return context;
}

function SidebarProvider({
  defaultOpen = true,
  open: openProp,
  onOpenChange: setOpenProp,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const isMobile = useIsMobile();
  const [openMobile, setOpenMobile] = React.useState(false);

  // This is the internal state of the sidebar.
  // We use openProp and setOpenProp for control from outside the component.
  const [_open, _setOpen] = React.useState(defaultOpen);
  const open = openProp ?? _open;
  const setOpen = React.useCallback(
    (value: boolean | ((value: boolean) => boolean)) => {
      const openState = typeof value === "function" ? value(open) : value;
      if (setOpenProp) {
        setOpenProp(openState);
      } else {
        _setOpen(openState);
      }

      document.cookie = `${SIDEBAR_COOKIE_NAME}=${openState}; path=/; max-age=${SIDEBAR_COOKIE_MAX_AGE}`;
    },
    [setOpenProp, open],
  );

  // Helper to toggle the sidebar.
  const toggleSidebar = React.useCallback(() => {
    return isMobile ? setOpenMobile((open) => !open) : setOpen((open) => !open);
  }, [isMobile, setOpen, setOpenMobile]);

  // Adds a keyboard shortcut to toggle the sidebar.
  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.key === SIDEBAR_KEYBOARD_SHORTCUT &&
        (event.metaKey || event.ctrlKey)
      ) {
        event.preventDefault();
        toggleSidebar();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [toggleSidebar]);

  // We add a state so that we can do data-state="expanded" or "collapsed".
  // This makes it easier to style the sidebar with Tailwind classes.
  const state = open ? "expanded" : "collapsed";

  const contextValue = React.useMemo<SidebarContextProps>(
    () => ({
      state,
      open,
      setOpen,
      isMobile,
      openMobile,
      setOpenMobile,
      toggleSidebar,
    }),
    [state, open, setOpen, isMobile, openMobile, setOpenMobile, toggleSidebar],
  );

  return (
    <SidebarContext.Provider value={contextValue}>
      <div
        data-slot="sidebar-wrapper"
        className={cn("flex min-h-(--cc-svh) w-full", className)}
        {...props}
      >
        {children}
      </div>
    </SidebarContext.Provider>
  );
}

/** 电脑上的侧栏（手机上不挂，见文件头）：收起时整块移到屏外（offcanvas），占位宽变 0。 */
function Sidebar({ className, children, ...props }: React.ComponentProps<"div">) {
  const { isMobile, state } = useSidebar();
  // 收起（offcanvas，移到屏幕外）时整块 inert：不进 Tab 顺序、读屏读不到、程序也聚焦不上；展开恢复（第 11 轮重测 E 段：收起后行还能
  // 聚焦，Tab 会走进看不见的侧栏）。展开钮在对话区顶栏（不在侧栏里），⌘B 挂在 window 上，都不受影响
  const offscreen = !isMobile && state === "collapsed";
  const container = React.useRef<HTMLDivElement>(null);
  // 收起时交出焦点的那一处（侧栏是移到屏幕外，不卸载）与上一次是不是收起着（只在收起 / 展开那一刻交接，首次挂上不动焦点）
  const handed = React.useRef<Handed>(null);
  const wasOffscreen = React.useRef(offscreen);
  React.useLayoutEffect(() => {
    if (wasOffscreen.current === offscreen) return;
    wasOffscreen.current = offscreen;
    // 收起那一刻焦点在侧栏里（点了侧栏里的收起钮、焦点在行上按 ⌘B）：交给顶栏的展开钮（cc-nav-toggle，同一次提交里出现），
    // 不随 inert 掉到页面最外层；记下原来那一处
    if (offscreen) {
      handed.current = handOffSidebarFocus(container.current);
      return;
    }
    // 展开（第 11 轮 E 段补审）：展开钮只在收起时才有，同一次提交里卸下，焦点掉到页面最外层（⌘B 收起再 ⌘B 展开、在展开钮上按
    // Enter / 空格）。这时（inert 已去掉）还给收起前那一行，没有就落在侧栏的收起钮；焦点在别处（用户自己放的）不动
    takeBackSidebarFocus(container.current, handed.current);
    handed.current = null;
  }, [offscreen]);

  // data-variant / data-side 只有这一种（贴边、在左），照旧写在节点上
  return (
    <div
      className="group peer text-sidebar-foreground hidden md:block"
      data-state={state}
      data-collapsible={state === "collapsed" ? "offcanvas" : ""}
      data-variant="sidebar"
      data-side="left"
      data-slot="sidebar"
    >
      {/* 占位：对话区靠它让出侧栏的宽，收起时变 0 */}
      <div
        data-slot="sidebar-gap"
        className="relative w-(--cc-sidebar-w) bg-transparent transition-[width] duration-(--cc-dur) ease-linear group-data-[collapsible=offcanvas]:w-0"
      />
      <div
        ref={container}
        data-slot="sidebar-container"
        inert={offscreen || undefined}
        className={cn(
          "fixed inset-y-0 z-(--cc-z-float) hidden h-(--cc-svh) w-(--cc-sidebar-w) transition-[left,right,width] duration-(--cc-dur) ease-linear md:flex",
          "start-0 group-data-[collapsible=offcanvas]:-start-(--cc-sidebar-w) border-e",
          className,
        )}
        {...props}
      >
        <div
          data-sidebar="sidebar"
          data-slot="sidebar-inner"
          className="bg-sidebar flex h-full w-full flex-col"
        >
          {children}
        </div>
      </div>
    </div>
  );
}

function SidebarInset({ className, ...props }: React.ComponentProps<"main">) {
  return (
    <main
      data-slot="sidebar-inset"
      className={cn("bg-background relative flex w-full flex-1 flex-col", className)}
      {...props}
    />
  );
}

// 头、脚：左右内边距是侧栏的 --cc-sidebar-pad，上下 8（调用方按需改）
function SidebarHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-header"
      data-sidebar="header"
      className={cn("flex flex-col gap-(--cc-space-8) px-(--cc-sidebar-pad) py-(--cc-space-8)", className)}
      {...props}
    />
  );
}

function SidebarFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-footer"
      data-sidebar="footer"
      className={cn("flex flex-col gap-(--cc-space-8) px-(--cc-sidebar-pad) py-(--cc-space-8)", className)}
      {...props}
    />
  );
}

function SidebarContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-content"
      data-sidebar="content"
      className={cn("flex min-h-0 flex-1 flex-col gap-(--cc-space-8) overflow-auto", className)}
      {...props}
    />
  );
}

export {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarProvider,
  useSidebar,
};
