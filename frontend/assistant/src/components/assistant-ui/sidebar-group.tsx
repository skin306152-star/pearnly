// 侧栏的组标题（docs/work/2026-10-16-cc-shell-design.md 3.4，同 Claude Code）与「分组和排序」菜单（3.6）：
//
//   GroupHeader   — 带高 32（手机 44）：整行（除右侧操作区）是一个开关（aria-expanded），名字 13/16 淡色、鼠标在组标题上变
//                   次要色；名字后 12px 箭头（收起 ›、展开 ⌄），鼠标进侧栏才淡入（手机、触屏常显）。右侧操作 24×24：
//                   平时只露「分组和排序」，悬停组标题再出「···」与「新建」（手机、触屏常显）。已置顶、各公司、各月、最近共用
//   GroupSortMenu — 分组依据（公司 / 处理月份 / 无）、排序方式（上次更新 / 创建日期 / 名称 + 降序 / 升序）、显示（元数据 /
//                   空分组）、恢复默认设置（改过才可点）；父项右侧写当前值，选中的打 ✓（lib/managed-agents/sidebar-view.ts）
//   GroupNew      — 组标题右侧的「新建」：在这一组（公司 / 最近）开新对话
//
// 颜色、尺寸只用 globals.css 的变量（.cc-group-*、.cc-row-btn）。

import { CheckIcon, ChevronRightIcon, ListFilterIcon, PlusIcon } from "lucide-react";
import type { FC, HTMLAttributes, KeyboardEvent, ReactNode } from "react";
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
import { cn } from "@/lib/utils";

/** 组标题的菜单键（键盘上的菜单键、Shift + F10）：打开这一组的「···」。 */
export function isMenuKey(event: KeyboardEvent): boolean {
  return event.key === "ContextMenu" || (event.shiftKey && event.key === "F10");
}

type HeaderProps = {
  label: ReactNode;
  /** 名字超长时的完整名称（悬停提示、读屏）。 */
  title?: string;
  open: boolean;
  onToggle: () => void;
  /** 收起时名字后的点（组里有在跑、没看过的）。 */
  dot?: ReactNode;
  /** 键盘菜单键：打开这一组的「···」。 */
  onMenuKey?: () => void;
  /** 右侧操作区（待处理数、···、分组和排序、新建）。 */
  children?: ReactNode;
} & Omit<HTMLAttributes<HTMLDivElement>, "title" | "children">;

export const GroupHeader: FC<HeaderProps> = ({ label, title, open, onToggle, dot, onMenuKey, children, className, ...rest }) => (
  <div data-slot="cc-group" {...rest} className={cn("cc-group-row", className)}>
    <button
      type="button"
      data-roving=""
      data-slot="aui_thread-list-group-label"
      className="cc-group-toggle"
      aria-expanded={open}
      title={title}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (onMenuKey && isMenuKey(e)) {
          e.preventDefault();
          onMenuKey();
        }
      }}
    >
      <span className="cc-group-name">{label}</span>
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
const GROUP_LABEL: Record<GroupBy, () => string> = { company: () => co.companies, month: () => co.period, none: () => sb.none };
const SORT_LABEL: Record<SortBy, () => string> = { updated: () => sb.lastUpdated, created: () => sb.dateCreated, name: () => sb.name };

/** 父项：名字 + 右侧当前值（13px 淡色）。 */
const Parent: FC<{ label: string; value?: string }> = ({ label, value }) => (
  <span className="flex w-full min-w-0 items-center">
    <span className="min-w-0 flex-1 truncate">{label}</span>
    {value && <span className="cc-menu-value">{value}</span>}
  </span>
);

/** 单选 / 勾选项：选中的打 ✓。keep：点了菜单不关（显示里的两项可以连着点）。 */
const Choice: FC<{ label: string; on: boolean; onPick: () => void; keep?: boolean }> = ({ label, on, onPick, keep }) => (
  <DropdownMenu.Item
    className="cc-menu-item"
    onSelect={(event) => {
      if (keep) event.preventDefault();
      onPick();
    }}
  >
    <span className="min-w-0 flex-1 truncate">{label}</span>
    {on && <CheckIcon />}
  </DropdownMenu.Item>
);

export const GroupSortMenu: FC = () => {
  const view = useSidebarView();
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" tabIndex={-1} className="cc-row-btn cc-group-sort" aria-label={sb.groupAndSort} title={sb.groupAndSort}>
          <ListFilterIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="bottom" align="end" sideOffset={4} className="cc-menu !min-w-52" data-slot="cc-group-sort-menu">
          <SubMenu label={<Parent label={sb.groupBy} value={GROUP_LABEL[view.by]()} />} className="cc-menu !min-w-40">
            {(["company", "month", "none"] as const).map((by) => (
              <Choice key={by} label={GROUP_LABEL[by]()} on={view.by === by} onPick={() => setSidebarView({ by })} />
            ))}
          </SubMenu>
          <SubMenu label={<Parent label={sb.sortBy} value={SORT_LABEL[view.sort]()} />} className="cc-menu !min-w-40">
            {(["updated", "created", "name"] as const).map((sort) => (
              <Choice key={sort} label={SORT_LABEL[sort]()} on={view.sort === sort} onPick={() => setSidebarView({ sort })} />
            ))}
            <DropdownMenu.Separator className="cc-menu-sep" />
            <Choice label={sb.descending} on={view.desc} onPick={() => setSidebarView({ desc: true })} />
            <Choice label={sb.ascending} on={!view.desc} onPick={() => setSidebarView({ desc: false })} />
          </SubMenu>
          <SubMenu label={<Parent label={sb.show} />} className="cc-menu !min-w-40">
            <Choice keep label={sb.metadata} on={view.meta} onPick={() => setSidebarView({ meta: !view.meta })} />
            <Choice keep label={sb.emptyGroups} on={view.empty} onPick={() => setSidebarView({ empty: !view.empty })} />
          </SubMenu>
          <DropdownMenu.Separator className="cc-menu-sep" />
          <DropdownMenu.Item className="cc-menu-item" disabled={isDefaultView(view)} onSelect={resetSidebarView}>
            {sb.resetDefaults}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};
