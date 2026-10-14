
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSidebar } from "@/components/ui/sidebar";
import { FilingTag, MoveToMenu, ProjectHeader, SidebarError } from "@/components/assistant-ui/projects";
import { useLongPress } from "@/hooks/use-long-press";
import { cn } from "@/lib/utils";
import { co, dayLabel, t } from "@/i18n";
import { useActivity, useBumps } from "@/lib/managed-agents/activity";
import { setDraft, showMore, useProjects } from "@/lib/managed-agents/projects";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { companySections, groupOpen, toggleGroup } from "@/lib/managed-agents/sidebar-groups";
import { useView } from "@/lib/nav";
import {
  AuiIf,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  ChevronDownIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PinIcon,
  PinOffIcon,
  LoaderIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import {
  forwardRef,
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type FC,
  type ReactNode,
} from "react";

// 侧栏 / 手机抽屉里的对话列表：搜索框、「新建」「定时任务」「公司」「待处理」由外面统一排版（assistant.tsx），这里只排对话。
// 产品有公司分组（Cowork，lib/managed-agents/projects.ts）时按「已置顶 → 各公司 → 最近」排（CompanyGroups）；
// 没有时同以前：已置顶 + 按日子分组（手机上是「最近」）。
export const ThreadList: FC<{ search?: string }> = ({ search = "" }) => {
  return (
    <ThreadListRoot>
      <ThreadListItems searchQuery={search} />
    </ThreadListRoot>
  );
};

/** 搜索框（同 Claude 侧栏顶部）：样式见 globals.css 的 .cc-search。 */
export const ThreadListSearch = forwardRef<
  HTMLInputElement,
  { value: string; onValueChange: (value: string) => void; autoFocus?: boolean }
>(({ value, onValueChange, autoFocus }, ref) => {
  return (
    <label data-slot="aui_thread-list-search" className="cc-search">
      <SearchIcon data-slot="aui_thread-list-search-icon" className="size-4 shrink-0" />
      <input
        ref={ref}
        type="search"
        value={value}
        autoFocus={autoFocus}
        onChange={(event) => onValueChange(event.target.value)}
        aria-label={t("search")}
        placeholder={t("search")}
      />
    </label>
  );
});

ThreadListSearch.displayName = "ThreadListSearch";

export const ThreadListRoot: FC<
  ComponentPropsWithoutRef<typeof ThreadListPrimitive.Root>
> = ({ className, ...props }) => {
  // 手机宽度下侧栏是覆盖层：选了对话或点了新建就收起（同 claude.ai）。
  const { isMobile, setOpenMobile } = useSidebar();
  return (
    <ThreadListPrimitive.Root
      onClickCapture={(e) => {
        const target = e.target as HTMLElement;
        if (isMobile && target.closest('[data-slot="aui_thread-list-item-trigger"], [data-slot="aui_thread-list-new"]')) {
          setOpenMobile(false);
        }
      }}
      data-slot="aui_thread-list-root"
      className={cn("flex flex-col gap-0.5", className)}
      {...props}
    />
  );
};

export const ThreadListItems: FC<
  ComponentPropsWithoutRef<"div"> & { searchQuery?: string }
> = ({ className, searchQuery = "", ...props }) => {
  return (
    <div
      data-slot="aui_thread-list-items"
      className={cn("flex flex-col gap-0.5", className)}
      {...props}
    >
      {/* 只有第一次加载显示骨架；之后重拉列表（别的设备新建了对话等，list-sync.ts）时旧列表留着，拉回来就地换上 */}
      <AuiIf condition={(s) => s.threads.isLoading && !s.threads.threadIds.length}>
        <ThreadListSkeleton />
      </AuiIf>
      <AuiIf condition={(s) => !s.threads.isLoading || !!s.threads.threadIds.length}>
        <ThreadListItemGroups searchQuery={searchQuery} />
        <ThreadListMore searching={!!searchQuery.trim()} />
      </AuiIf>
    </div>
  );
};

/** 列表底部（同 Claude）：先显示一页，滚到这里再加载下一页；搜索时把剩下的都加载进来，较早的对话也搜得到。
 *  按公司分组时滚到底只翻「最近」（各公司由组里的「显示更多」翻）。 */
const ThreadListMore: FC<{ searching: boolean }> = ({ searching }) => {
  const aui = useAui();
  const kind = useProjects((s) => s.kind);
  const recentMore = useProjects((s) => !!s.more.recent);
  const hasMore = useAuiState((s) => s.threads.hasMore) && (!kind || searching || recentMore);
  const loadingMore = useAuiState((s) => s.threads.isLoadingMore);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!hasMore || loadingMore) return;
    if (searching) {
      void aui.threads().loadMore();
      return;
    }
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) void aui.threads().loadMore();
    }, { rootMargin: "200px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, [aui, hasMore, loadingMore, searching]);
  if (!hasMore && !loadingMore) return null;
  return (
    <div ref={ref} className="flex h-8 items-center px-2.5 text-ink-3">
      {loadingMore && <LoaderIcon className="size-3.5 animate-spin" />}
    </div>
  );
};

type ThreadListGroup = { key: string; label: string; indices: number[] };

// 分组可以点标题折叠（同 Claude 侧栏的「今天 ⌄」），折叠哪些记在本机
const COLLAPSED_KEY = "cc.sidebar.collapsed";

function readCollapsed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]") as string[]);
  } catch {
    return new Set();
  }
}

function saveCollapsed(next: Set<string>) {
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next]));
  } catch {
    // 无痕窗口等存不了：本次照样折叠
  }
}

function useCollapsedGroups() {
  const [collapsed, setCollapsed] = useState<Set<string>>(readCollapsed);
  const toggle = (key: string) =>
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      saveCollapsed(next);
      return next;
    });
  /** 公司组：前 5 家默认展开，其余默认收起（键 project:<编号>，见 sidebar-groups 的 groupOpen）。 */
  const toggleSection = (section: { key: string; rank?: number }) =>
    setCollapsed((previous) => {
      const next = toggleGroup(section, previous);
      saveCollapsed(next);
      return next;
    });
  return { collapsed, toggle, toggleSection };
}

const dayKey = (day: Date) => `${day.getFullYear()}-${day.getMonth() + 1}-${day.getDate()}`;

const ThreadListItemGroups: FC<{ searchQuery?: string }> = ({ searchQuery = "" }) => {
  const kind = useProjects((s) => s.kind);
  return kind ? <CompanyGroups searchQuery={searchQuery} /> : <DayGroups searchQuery={searchQuery} />;
};

/** 侧栏每行的最后活动时间：列表给的与本页收到提醒的时间里较晚的一个（提醒到的对话浮到最上面）。 */
function useDates() {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const bumps = useBumps();
  return useMemo(() => {
    const itemsById = new Map(threadItems.map((item) => [item.id, item]));
    const dates = threadIds.map((id) => {
      const item = itemsById.get(id);
      const bumped = item?.remoteId ? bumps.get(item.remoteId) : undefined;
      const listed = item?.lastMessageAt;
      return bumped && (!listed || bumped > listed.getTime()) ? new Date(bumped) : listed;
    });
    return { threadIds, itemsById, dates };
  }, [threadIds, threadItems, bumps]);
}

const NoResults: FC = () => (
  <div data-slot="aui_thread-list-empty" className="text-ink-3 px-2.5 py-4 text-sm">
    {t("noResults")}
  </div>
);

const Items: FC<{ indices: readonly number[]; threadIds: readonly string[] }> = ({ indices, threadIds }) =>
  indices.map((index) => <ThreadListPrimitive.ItemByIndex key={threadIds[index]} index={index} components={{ ThreadListItem }} />);

/** 可折叠的分组标题（同 Claude「今天 ⌄」）。 */
const GroupLabel: FC<{ open: boolean; onToggle: () => void; children: ReactNode }> = ({ open, onToggle, children }) => (
  <button type="button" data-slot="aui_thread-list-group-label" className="cc-section-label cc-section-toggle" aria-expanded={open} onClick={onToggle}>
    {children}
    <ChevronDownIcon className="cc-section-chevron" />
  </button>
);

// 按公司分组（同 Claude Code 按项目）：已置顶 → 各公司（前 5 家展开，每组先 5 条）→ 最近。手机上各组也能折叠；
// 折叠状态每台设备各记各的（pinned、recents、project:<编号>）。
const CompanyGroups: FC<{ searchQuery: string }> = ({ searchQuery }) => {
  const aui = useAui();
  const { threadIds, itemsById, dates } = useDates();
  const projects = useProjects((s) => s.projects);
  const filings = useProjects((s) => s.filings);
  const shown = useProjects((s) => s.shown);
  const serverMore = useProjects((s) => s.more.groups);
  const { collapsed, toggleSection } = useCollapsedGroups();
  const query = searchQuery.trim();

  const sections = useMemo(() => {
    const rows = threadIds.map((id, index) => {
      const item = itemsById.get(id);
      const filing = (item?.remoteId ? filings[item.remoteId] : undefined) ?? filings[id];
      return {
        index,
        title: item?.title || t("untitled"),
        starred: !!item?.custom?.starred,
        time: dates[index]?.getTime() ?? Number.MAX_SAFE_INTEGER,
        group: filing?.project ?? "",
      };
    });
    return companySections(rows, projects, { query, shown, serverMore });
  }, [threadIds, itemsById, dates, projects, filings, shown, serverMore, query]);

  if (!sections) return <NoResults />;
  const sessionsOf = (indices: readonly number[]) =>
    indices.map((index) => itemsById.get(threadIds[index]!)?.remoteId).filter((id): id is string => !!id);

  return (
    <>
      <SidebarError />
      {sections.map((section) => {
        const open = groupOpen(section.type === "project" ? section : { key: section.key }, collapsed, !!query);
        if (section.type === "project") {
          return (
            <Fragment key={section.key}>
              <ProjectHeader project={section.project} open={open} onToggle={() => toggleSection(section)} sessions={sessionsOf(section.all)} />
              {open && (
                <div data-slot="cc-project-items" className="cc-project-items">
                  <Items indices={section.indices} threadIds={threadIds} />
                  {section.more && (
                    <button
                      type="button"
                      className="cc-show-more"
                      onClick={() => void showMore(section.project.id, section.all.length, () => aui.threads().loadMore())}
                    >
                      {co.showMore}
                    </button>
                  )}
                </div>
              )}
            </Fragment>
          );
        }
        return (
          <Fragment key={section.key}>
            <GroupLabel open={open} onToggle={() => toggleSection({ key: section.key })}>
              {section.type === "pinned" ? t("starred") : t("recents")}
            </GroupLabel>
            {open && <Items indices={section.indices} threadIds={threadIds} />}
          </Fragment>
        );
      })}
    </>
  );
};

const DayGroups: FC<{ searchQuery?: string }> = ({
  searchQuery = "",
}) => {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const bumps = useBumps();
  const { isMobile } = useSidebar();
  const { collapsed, toggle } = useCollapsedGroups();

  const query = searchQuery.trim().toLowerCase();

  const { filteredIndices, groups } = useMemo(() => {
    const itemsById = new Map(threadItems.map((item) => [item.id, item]));
    // 最后活动时间取列表给的与本页收到提醒的时间里较晚的一个：提醒到的对话浮到最上面
    const dates = threadIds.map((id) => {
      const item = itemsById.get(id);
      const bumped = item?.remoteId ? bumps.get(item.remoteId) : undefined;
      const listed = item?.lastMessageAt;
      return bumped && (!listed || bumped > listed.getTime()) ? new Date(bumped) : listed;
    });
    const filteredIndices = threadIds
      .map((id, index) => ({ id, index }))
      .filter(
        ({ id }) =>
          !query ||
          (itemsById.get(id)?.title || t("untitled"))
            .toLowerCase()
            .includes(query),
      )
      .map(({ index }) => index);
    if (!filteredIndices.some((index) => dates[index] || itemsById.get(threadIds[index]!)?.custom?.starred)) {
      return { filteredIndices, groups: null };
    }

    const time = (index: number) =>
      dates[index]?.getTime() ?? Number.MAX_SAFE_INTEGER;
    const sorted = [...filteredIndices].sort((a, b) => time(b) - time(a));

    const result: ThreadListGroup[] = [];
    const starred = sorted.filter((index) => itemsById.get(threadIds[index]!)?.custom?.starred);
    if (starred.length) result.push({ key: "starred", label: t("starred"), indices: starred });
    const rest = sorted.filter((index) => !starred.includes(index));
    if (isMobile) {
      // 手机（同 Claude 手机端）：其余的都在「最近」
      if (rest.length) result.push({ key: "recents", label: t("recents"), indices: rest });
    } else {
      // 网页 / 桌面（同 Claude 网页版）：按最后活动的日子分组——今天、昨天、10月10日……
      const byDay: ThreadListGroup[] = [];
      for (const index of rest) {
        const day = dates[index] ?? new Date();
        const key = dayKey(day);
        const last = byDay[byDay.length - 1];
        if (last?.key === key) last.indices.push(index);
        else byDay.push({ key, label: dayLabel(day), indices: [index] });
      }
      result.push(...byDay);
    }
    return { filteredIndices, groups: result };
  }, [threadIds, threadItems, bumps, query, isMobile]);

  if (query && filteredIndices.length === 0) {
    return (
      <div
        data-slot="aui_thread-list-empty"
        className="text-ink-3 px-2.5 py-4 text-sm"
      >
        {t("noResults")}
      </div>
    );
  }

  if (!groups) {
    return filteredIndices.map((index) => (
      <ThreadListPrimitive.ItemByIndex
        key={threadIds[index]}
        index={index}
        components={{ ThreadListItem }}
      />
    ));
  }

  return groups.map((group) => {
    // 搜索时不折叠：要看到所有命中的；手机上分组不能折叠（同 Claude 手机端），只是标题
    const open = !!query || isMobile || !collapsed.has(group.key);
    if (isMobile) {
      return (
        <Fragment key={group.key}>
          <div data-slot="aui_thread-list-group-label" className="cc-section-label">
            {group.label}
          </div>
          {group.indices.map((index) => (
            <ThreadListPrimitive.ItemByIndex key={threadIds[index]} index={index} components={{ ThreadListItem }} />
          ))}
        </Fragment>
      );
    }
    return (
      <Fragment key={group.key}>
        <button
          type="button"
          data-slot="aui_thread-list-group-label"
          className="cc-section-label cc-section-toggle"
          aria-expanded={open}
          onClick={() => toggle(group.key)}
        >
          {group.label}
          <ChevronDownIcon className="cc-section-chevron" />
        </button>
        {open &&
          group.indices.map((index) => (
            <ThreadListPrimitive.ItemByIndex
              key={threadIds[index]}
              index={index}
              components={{ ThreadListItem }}
            />
          ))}
      </Fragment>
    );
  });
};

// 「新建」：与「定时任务」同一个导航项样式（.cc-nav-item / .cc-nav-icon），图标与文字对齐（同 Claude 侧栏）
export const ThreadListNew = forwardRef<HTMLButtonElement, ComponentPropsWithoutRef<"button">>(
  ({ className, ...props }, ref) => {
    return (
      <ThreadListPrimitive.New asChild>
        <button
          ref={ref}
          type="button"
          data-slot="aui_thread-list-new"
          title={t("newChat")}
          className={cn("cc-nav-item", className)}
          {...props}
          onClick={(e) => {
            // 顶部「新建」开在「最近」（组头 ＋、主档页输入框才开在公司里）
            setDraft({});
            props.onClick?.(e);
          }}
        >
          <span className="cc-nav-icon bg-subtle text-ink rounded-full">
            <PlusIcon data-slot="aui_thread-list-new-icon" className="!size-3.5" />
          </span>
          <span data-slot="aui_thread-list-new-label" className="cc-nav-label whitespace-nowrap">
            {t("newChat")}
          </span>
        </button>
      </ThreadListPrimitive.New>
    );
  },
);

ThreadListNew.displayName = "ThreadListNew";

const ThreadListSkeleton: FC = () => {
  return (
    <div className="flex flex-col gap-0.5">
      {Array.from({ length: 5 }, (_, i) => (
        <div
          key={i}
          role="status"
          aria-label={t("search")}
          data-slot="aui_thread-list-skeleton-wrapper"
          className="flex h-8 items-center px-2.5"
        >
          <Skeleton
            data-slot="aui_thread-list-skeleton"
            className="h-3.5 w-full"
          />
        </div>
      ))}
    </div>
  );
};

/** 这一行是不是正开着的对话。一段对话在组件库里可能有两行数据（本页新建的那行 + 重拉列表后服务端给的那行，组件库按
 *  编号各存一份，侧栏显示的是后者），所以按对话编号认：两行都算正开着（选中底色、行尾不标状态、点它不切换）。 */
function useCurrent(): { current: boolean; isMain: boolean } {
  const isMain = useAuiState((s) => s.threads.mainThreadId === s.threadListItem.id);
  const sameConversation = useAuiState((s) => {
    const remote = s.threadListItem.remoteId;
    if (!remote || s.threads.mainThreadId === s.threadListItem.id) return false;
    return s.threads.threadItems.some((item) => item.id === s.threads.mainThreadId && item.remoteId === remote);
  });
  return { current: isMain || sameConversation, isMain };
}

// 侧栏一行只有标题（同 Claude 侧栏，用户 2026-10-13：不要前面的对话气泡图标）；选中、悬停、行尾状态不变
export const ThreadListItem: FC = () => {
  const [renaming, setRenaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const press = useLongPress(() => setMenuOpen(true));
  // 点的就是正开着的这个对话（例如手机抽屉里再点一次）：不会切换、也就不会重进，同样向服务端核对一次此刻的状态。
  // 点的是同一段对话的另一行数据：也不切换（切过去会另起一份，输入框里没发的字就没了）
  const id = useAuiState((s) => s.threadListItem.remoteId);
  const { current, isMain } = useCurrent();
  const reenter = (event: React.MouseEvent) => {
    if (!current) return;
    if (!isMain) event.preventDefault();
    if (id) getSessionController(id).refreshStatus();
  };
  return (
    <ThreadListItemPrimitive.Root
      {...press}
      data-active={current ? "true" : undefined}
      aria-current={current ? "true" : undefined}
      data-slot="aui_thread-list-item"
      className="group/item hover:bg-hover focus-visible:bg-hover data-active:bg-active has-focus-visible:bg-hover has-data-[state=open]:bg-hover text-ink-2 data-active:text-ink relative flex h-8 items-center rounded-sm transition-colors focus-visible:outline-none"
    >
      {renaming ? (
        <ThreadListItemRename onDone={() => setRenaming(false)} />
      ) : (
        <>
          <ThreadListItemPrimitive.Trigger
            data-slot="aui_thread-list-item-trigger"
            onClick={reenter}
            className="focus-visible:ring-ring/50 flex h-full min-w-0 flex-1 items-center rounded-md px-2.5 text-start text-sm outline-none group-hover/item:pe-9 group-has-focus-visible/item:pe-9 group-has-data-[state=open]/item:pe-9 focus-visible:ring-[3px]"
          >
            <span
              data-slot="aui_thread-list-item-title"
              className="min-w-0 flex-1 truncate"
            >
              <ThreadListItemPrimitive.Title fallback={t("untitled")} />
              <FilingTag />
            </span>
            <ThreadActivity />
          </ThreadListItemPrimitive.Trigger>
          <ThreadListItemMore open={menuOpen} onOpenChange={setMenuOpen} onRename={() => setRenaming(true)} />
        </>
      )}
    </ThreadListItemPrimitive.Root>
  );
};

/** 行尾状态（同 Claude）：在跑转圈，跑完没看过、或有没看过的提醒，一个小圆点；正在看的那个不标（顶栏已经在转）——
 *  在定时任务页时当前对话没人在看，照样标；鼠标移上去让位给「更多」按钮。 */
const ThreadActivity: FC = () => {
  const id = useAuiState((s) => s.threadListItem.remoteId);
  const { current } = useCurrent();
  const { view } = useView();
  const activity = useActivity(id);
  if (!activity || (current && view === "chat")) return null;
  const label = activity === "running" ? t("running") : activity === "notice" ? t("newNotice") : t("unread");
  return (
    <span className="cc-activity group-hover/item:hidden group-has-data-[state=open]/item:hidden" data-activity={activity} aria-label={label}>
      {activity === "running" && <LoaderIcon className="size-3.5 animate-spin" />}
    </span>
  );
};

// 行内改名（同 claude.ai）：回车或离开输入框保存，Esc 放弃；空标题不保存。
export const ThreadListItemRename: FC<{ onDone: () => void; className?: string }> = ({ onDone, className }) => {
  const aui = useAui();
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const [value, setValue] = useState(title);
  const save = () => {
    const next = value.trim();
    if (next && next !== title) void aui.threadListItem().rename(next);
    onDone();
  };
  return (
    <input
      autoFocus
      value={value}
      aria-label={t("rename")}
      onChange={(e) => setValue(e.target.value)}
      onFocus={(e) => e.target.select()}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") save();
        if (e.key === "Escape") onDone();
      }}
      className={cn("cc-input w-full", className)}
    />
  );
};

// 对话菜单（同 Claude）：置顶 / 重命名 / 移到 ▸（有公司分组时）/ 删除。用 radix 的下拉菜单才放得下「移到 ▸」这一层子菜单。
const ThreadListItemMore: FC<{ open: boolean; onOpenChange: (open: boolean) => void; onRename: () => void }> = ({
  open,
  onOpenChange,
  onRename,
}) => {
  const aui = useAui();
  const custom = useAuiState((s) => s.threadListItem.custom);
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const starred = !!custom?.starred;
  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          data-slot="aui_thread-list-item-more"
          className="data-[state=open]:bg-hover absolute end-1.5 top-1/2 size-6 -translate-y-1/2 p-0 opacity-0 group-hover/item:opacity-100 group-has-focus-visible/item:opacity-100 data-[state=open]:opacity-100"
        >
          <MoreHorizontalIcon className="size-3.5" />
          <span className="sr-only">{t("more")}</span>
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="right"
          align="start"
          sideOffset={6}
          data-slot="aui_thread-list-item-more-content"
          className="cc-menu !min-w-36"
        >
          {/* 展开写法：组件库整份替换 custom，以后加的键不能被冲掉 */}
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => void aui.threadListItem().updateCustom({ ...custom, starred: !starred })}>
            {starred ? <PinOffIcon className="size-4" /> : <PinIcon className="size-4" />}
            {starred ? t("unstar") : t("star")}
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" onSelect={onRename}>
            <PencilIcon className="size-4" />
            {t("rename")}
          </DropdownMenu.Item>
          <MoveToMenu sessionId={remoteId} localId={localId} />
          <DropdownMenu.Item
            data-slot="aui_thread-list-item-more-item"
            data-danger=""
            className="cc-menu-item"
            onSelect={() => void aui.threadListItem().delete()}
          >
            <TrashIcon className="size-4" />
            {t("delete")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};
