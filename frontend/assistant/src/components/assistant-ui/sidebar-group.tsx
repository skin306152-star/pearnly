// 侧栏的组标题（docs/work/2026-10-16-cc-shell-design.md 3.4，同 Claude Code）与「分组和排序」菜单（3.6）：
//
//   GroupHeader   — 带高 32（手机 44）：整行（除右侧操作区）是一个开关（aria-expanded），名字 13/16 淡色、鼠标在组标题上变
//                   次要色；名字后 12px 箭头（收起 ›、展开 ⌄），鼠标进侧栏才淡入（手机、触屏常显）。右侧操作 24×24：
//                   悬停组标题才出「···」与「新建」（手机、触屏常显）。已置顶、各公司、各月、最近共用。公司组头另有最左的公司图标
//                   （icon），名字同会话行标题的字号字色（projects.tsx 的 ProjectHeader，用户 2026-10-15 夜参考 DeepSeek 桌面端）。
//                   公司组头的开关也是这一组的拖动把手（共用拖动 components/ui/drag.tsx：拖成任意顺序，Alt+↑/↓ 挪一格）；
//                   菜单键 / Shift+F10 打开这一组的「···」（各组都是受控菜单）
//   GroupSortMenu — 组标题右侧的「···」，打开「分组和排序」（用户 2026-10-15 夜：原来的漏斗按钮去掉，公司组「···」里的
//                   打开公司、在这家新建对话换成这份菜单）：分组依据（公司 / 月份 / 无）、排序方式（上次更新 / 创建日期 /
//                   名称 + 降序 / 升序）、显示（元数据 / 空分组）、恢复默认设置（改过才可点）；父项右侧写当前值，选中的打 ✓
//                   （lib/managed-agents/sidebar-view.ts）。公司组头受控：手机长按组头、键盘菜单键打开的也是它
//   GroupNew      — 组标题右侧的「新建」：在这一组（公司 / 最近）开新对话
//
// 颜色、尺寸只用 globals.css 的变量（.cc-group-*、.cc-row-btn）。组名放不下时悬停滚动（components/ui/marquee.tsx）。

import { CheckIcon, ChevronRightIcon, MoreHorizontalIcon, PlusIcon } from "lucide-react";
import { type FC, type HTMLAttributes, type KeyboardEvent, type ReactNode, useLayoutEffect, useRef, useState } from "react";
import { altMove, chain, type DragHandle } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";
import { isDragging } from "@/lib/layers";
import { DropdownMenu, SubMenu } from "@/components/ui/menu";
import { useSidebar } from "@/components/ui/sidebar";
import { co, sb, t } from "@/i18n";
import {
  type GroupBy,
  isDefaultView,
  resetSidebarView,
  setSidebarView,
  type SortBy,
  useSidebarView,
} from "@/lib/managed-agents/sidebar-view";
import { hasManualOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { cn } from "@/lib/utils";

/** 组标题的菜单键（键盘上的菜单键、Shift + F10）：打开这一组的「···」。 */
export function isMenuKey(event: KeyboardEvent): boolean {
  return event.key === "ContextMenu" || (event.shiftKey && event.key === "F10");
}

type HeaderProps = {
  label: ReactNode;
  /** 名字前的图标（公司组头的公司图标；已置顶 / 最近 / 月份没有）。 */
  icon?: ReactNode;
  /** 名字超长时的完整名称（悬停提示、读屏）。 */
  title?: string;
  open: boolean;
  onToggle: () => void;
  /** 收起时名字后的点（组里有在跑、没看过的）。 */
  dot?: ReactNode;
  /** 键盘菜单键：打开这一组的「···」。 */
  onMenuKey?: () => void;
  /** 开关同时是这一组的拖动把手（公司组排序）。 */
  handle?: DragHandle;
  /** Alt+↑ / Alt+↓：这一组挪一格。 */
  onMove?: (delta: -1 | 1) => void;
  /** 右侧操作区（待处理数、···（分组和排序）、新建）。 */
  children?: ReactNode;
} & Omit<HTMLAttributes<HTMLDivElement>, "title" | "children">;

export const GroupHeader: FC<HeaderProps> = ({ label, icon, title, open, onToggle, dot, onMenuKey, handle, onMove, children, className, ...rest }) => (
  <div data-slot="cc-group" data-marquee-host="" {...rest} className={cn("cc-group-row", className)}>
    <button
      type="button"
      {...handle}
      data-roving=""
      data-slot="aui_thread-list-group-label"
      className="cc-group-toggle"
      aria-expanded={open}
      title={title}
      onClick={onToggle}
      onKeyUp={chain(handle?.onKeyUp)}
      onKeyDown={(e) => {
        // 键盘拖动中：键只归拖动（同对话行；不开「···」、Alt+方向键不挪组）
        if (isDragging()) return handle?.onKeyDown?.(e);
        if (onMenuKey && isMenuKey(e)) {
          e.preventDefault();
          onMenuKey();
          return;
        }
        if (onMove && altMove(e, onMove)) return;
        handle?.onKeyDown?.(e);
      }}
    >
      {icon}
      <Marquee className="cc-group-name">{label}</Marquee>
      <ChevronRightIcon className="cc-group-caret" aria-hidden />
      {dot}
    </button>
    {children && <div className="cc-group-actions">{children}</div>}
  </div>
);

/** 手机抽屉里点了组标题上的操作：收起抽屉（同点对话）。 */
export function useCloseDrawer() {
  const { isMobile, setOpenMobile } = useSidebar();
  return (run: () => void) => {
    run();
    if (isMobile) setOpenMobile(false);
  };
}

/** 组标题右侧的「新建」（悬停组标题才出现；手机、触屏常显）。 */
export const GroupNew: FC<{ onClick: () => void }> = ({ onClick }) => {
  const go = useCloseDrawer();
  return (
    <button
      type="button"
      tabIndex={-1}
      data-slot="aui_thread-list-new"
      className="cc-row-btn cc-group-new"
      aria-label={t("newChat")}
      title={t("newChat")}
      onClick={() => go(onClick)}
    >
      <PlusIcon />
    </button>
  );
};

// ---------------------------------------------------------------- 分组和排序
const GROUP_LABEL: Record<GroupBy, () => string> = { company: () => co.companies, month: () => sb.month, none: () => sb.none };
const SORT_LABEL: Record<SortBy, () => string> = { updated: () => sb.lastUpdated, created: () => sb.dateCreated, name: () => sb.name };

/** 父项：名字 + 右侧当前值（13px 淡色）。 */
const Parent: FC<{ label: string; value?: string }> = ({ label, value }) => (
  <span className="flex w-full min-w-0 items-center">
    <Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>
    {value && <span className="cc-menu-value">{value}</span>}
  </span>
);

/** 单选项（分组依据、排序方式、升降序）：读屏念成单选菜单项并带选中状态（menuitemradio + aria-checked，测试窗口第 7 轮：原来是
 *  普通菜单项，听不出选的是哪个）；选中的打 ✓，样子不变。放在 RadioGroup 里，value 是它的值。 */
const Choice: FC<{ label: string; value: string }> = ({ label, value }) => (
  <DropdownMenu.RadioItem className="cc-menu-item" value={value}>
    <Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>
    <DropdownMenu.ItemIndicator asChild>
      <CheckIcon />
    </DropdownMenu.ItemIndicator>
  </DropdownMenu.RadioItem>
);

/** 勾选项（显示里的元数据、空分组）：menuitemcheckbox + aria-checked；点了菜单不关（两项可以连着点），勾上的打 ✓。 */
const Toggle: FC<{ label: string; on: boolean; onChange: (on: boolean) => void }> = ({ label, on, onChange }) => (
  <DropdownMenu.CheckboxItem className="cc-menu-item" checked={on} onCheckedChange={onChange} onSelect={(event) => event.preventDefault()}>
    <Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>
    <DropdownMenu.ItemIndicator asChild>
      <CheckIcon />
    </DropdownMenu.ItemIndicator>
  </DropdownMenu.CheckboxItem>
);

/** 菜单左边贴着组头开关的左边（组头那一行的内容起点）要的 alignOffset：菜单按「···」左对齐（align start）后再挪这么多。
 *  量不到回 null（照常按「···」右对齐）。 */
export function headOffset(trigger: { left: number } | null | undefined, head: { left: number } | null | undefined): number | null {
  return trigger && head ? Math.round(head.left - trigger.left) : null;
}

/** 组标题右侧的「···」：打开「分组和排序」。open / onOpenChange 只有公司组头传（长按组头、菜单键也打开它）。
 *  alignHead（手机上的公司组头）：菜单左边贴着组头开关的左边、盖住组里各行的起点（测试窗口第 7 轮：长按组头出的菜单按「···」
 *  右对齐，左边缘正好落在组里各行文字起点右边几像素，各行首字从菜单左边露出一点）。菜单盖在列表上照常，只是不再切在字头上。 */
export const GroupSortMenu: FC<{ open?: boolean; onOpenChange?: (open: boolean) => void; alignHead?: boolean }> = ({ open, onOpenChange, alignHead }) => {
  const view = useSidebarView();
  // 「恢复默认设置」也清手排的公司组、置顶顺序：两样都跟默认一样才不可点
  const manual = useSidebarOrder(hasManualOrder);
  const trigger = useRef<HTMLButtonElement>(null);
  const [offset, setOffset] = useState<number | null>(null);
  // 打开时量（菜单定位在同一次提交里随后重算，看不到挪动）
  useLayoutEffect(() => {
    if (!alignHead || !open) return;
    const button = trigger.current;
    const head = button?.closest(".cc-group-row")?.querySelector(".cc-group-toggle");
    setOffset(headOffset(button?.getBoundingClientRect(), head?.getBoundingClientRect()));
  }, [alignHead, open]);
  const head = alignHead && offset !== null;
  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger asChild>
        <button ref={trigger} type="button" tabIndex={-1} className="cc-row-btn cc-group-more" aria-label={t("more")} title={t("more")}>
          <MoreHorizontalIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="bottom"
          align={head ? "start" : "end"}
          alignOffset={head ? offset : 0}
          sideOffset={4}
          className="cc-menu !min-w-52"
          data-slot="cc-group-sort-menu"
          aria-label={sb.groupAndSort}
        >
          <SubMenu label={<Parent label={sb.groupBy} value={GROUP_LABEL[view.by]()} />} className="cc-menu !min-w-40">
            <DropdownMenu.RadioGroup value={view.by} onValueChange={(by) => setSidebarView({ by: by as GroupBy })}>
              {(["company", "month", "none"] as const).map((by) => (
                <Choice key={by} label={GROUP_LABEL[by]()} value={by} />
              ))}
            </DropdownMenu.RadioGroup>
          </SubMenu>
          <SubMenu label={<Parent label={sb.sortBy} value={SORT_LABEL[view.sort]()} />} className="cc-menu !min-w-40">
            <DropdownMenu.RadioGroup value={view.sort} onValueChange={(sort) => setSidebarView({ sort: sort as SortBy })}>
              {(["updated", "created", "name"] as const).map((sort) => (
                <Choice key={sort} label={SORT_LABEL[sort]()} value={sort} />
              ))}
            </DropdownMenu.RadioGroup>
            <DropdownMenu.Separator className="cc-menu-sep" />
            <DropdownMenu.RadioGroup value={view.desc ? "desc" : "asc"} onValueChange={(dir) => setSidebarView({ desc: dir === "desc" })}>
              <Choice label={sb.descending} value="desc" />
              <Choice label={sb.ascending} value="asc" />
            </DropdownMenu.RadioGroup>
          </SubMenu>
          <SubMenu label={<Parent label={sb.show} />} className="cc-menu !min-w-40">
            <Toggle label={sb.metadata} on={view.meta} onChange={(meta) => setSidebarView({ meta })} />
            <Toggle label={sb.emptyGroups} on={view.empty} onChange={(empty) => setSidebarView({ empty })} />
          </SubMenu>
          <DropdownMenu.Separator className="cc-menu-sep" />
          <DropdownMenu.Item className="cc-menu-item" disabled={isDefaultView(view) && !manual} onSelect={resetSidebarView}>
            {sb.resetDefaults}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};
