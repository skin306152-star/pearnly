
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSidebar } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { dayLabel, t } from "@/i18n";
import { useActivity, useBumps } from "@/lib/managed-agents/activity";
import {
  AuiIf,
  ThreadListItemMorePrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  ChevronDownIcon,
  MoreHorizontalIcon,
  PencilIcon,
  StarIcon,
  StarOffIcon,
  LoaderIcon,
  MessageCircleIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
} from "lucide-react";
import {
  forwardRef,
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type FC,
} from "react";

// 侧栏 / 手机抽屉里的对话列表：搜索框、「新建」「定时任务」由外面统一排版（assistant.tsx），这里只排对话。
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
      <AuiIf condition={(s) => s.threads.isLoading}>
        <ThreadListSkeleton />
      </AuiIf>
      <AuiIf condition={(s) => !s.threads.isLoading}>
        <ThreadListItemGroups searchQuery={searchQuery} />
        <ThreadListMore searching={!!searchQuery.trim()} />
      </AuiIf>
    </div>
  );
};

/** 列表底部（同 Claude）：先显示一页，滚到这里再加载下一页；搜索时把剩下的都加载进来，较早的对话也搜得到。 */
const ThreadListMore: FC<{ searching: boolean }> = ({ searching }) => {
  const aui = useAui();
  const hasMore = useAuiState((s) => s.threads.hasMore);
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

function useCollapsedGroups() {
  const [collapsed, setCollapsed] = useState<Set<string>>(readCollapsed);
  const toggle = (key: string) =>
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      try {
        localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next]));
      } catch {
        // 无痕窗口等存不了：本次照样折叠
      }
      return next;
    });
  return { collapsed, toggle };
}

const dayKey = (day: Date) => `${day.getFullYear()}-${day.getMonth() + 1}-${day.getDate()}`;

const ThreadListItemGroups: FC<{ searchQuery?: string }> = ({
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
        <button ref={ref} type="button" data-slot="aui_thread-list-new" title={t("newChat")} className={cn("cc-nav-item", className)} {...props}>
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

const LONG_PRESS_MS = 500;
const LONG_PRESS_SLOP = 10;

/** 手机上长按一条对话弹出它的菜单（同 Claude 手机端）：手机没有悬停，「…」按钮不出现。手指一动就不算长按（在滚列表）。 */
function useLongPress(onLongPress: () => void) {
  const timer = useRef<number | null>(null);
  const origin = useRef({ x: 0, y: 0 });
  const fired = useRef(false);
  const cancel = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };
  return {
    onTouchStart: (e: React.TouchEvent) => {
      const p = e.touches[0];
      fired.current = false;
      cancel();
      if (!p || e.touches.length > 1) return;
      origin.current = { x: p.clientX, y: p.clientY };
      timer.current = window.setTimeout(() => {
        timer.current = null;
        fired.current = true;
        navigator.vibrate?.(10);
        onLongPress();
      }, LONG_PRESS_MS);
    },
    onTouchMove: (e: React.TouchEvent) => {
      const p = e.touches[0];
      if (p && Math.hypot(p.clientX - origin.current.x, p.clientY - origin.current.y) > LONG_PRESS_SLOP) cancel();
    },
    onTouchEnd: (e: React.TouchEvent) => {
      cancel();
      if (fired.current) e.preventDefault(); // 长按松手不再算一次点击（不切到这个对话、不收起抽屉）
    },
    onTouchCancel: cancel,
  };
}

export const ThreadListItem: FC = () => {
  const [renaming, setRenaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const press = useLongPress(() => setMenuOpen(true));
  return (
    <ThreadListItemPrimitive.Root
      {...press}
      data-slot="aui_thread-list-item"
      className="group/item hover:bg-hover focus-visible:bg-hover data-active:bg-active has-focus-visible:bg-hover has-data-[state=open]:bg-hover text-ink-2 data-active:text-ink relative flex h-8 items-center rounded-sm transition-colors focus-visible:outline-none"
    >
      {renaming ? (
        <ThreadListItemRename onDone={() => setRenaming(false)} />
      ) : (
        <>
          <ThreadListItemPrimitive.Trigger
            data-slot="aui_thread-list-item-trigger"
            className="focus-visible:ring-ring/50 flex h-full min-w-0 flex-1 items-center rounded-md px-2.5 text-start text-sm outline-none group-hover/item:pe-9 group-has-focus-visible/item:pe-9 group-has-data-[state=open]/item:pe-9 focus-visible:ring-[3px]"
          >
            <span className="cc-row-icon" aria-hidden>
              <MessageCircleIcon />
            </span>
            <span
              data-slot="aui_thread-list-item-title"
              className="min-w-0 flex-1 truncate"
            >
              <ThreadListItemPrimitive.Title fallback={t("untitled")} />
            </span>
            <ThreadActivity />
          </ThreadListItemPrimitive.Trigger>
          <ThreadListItemMore open={menuOpen} onOpenChange={setMenuOpen} onRename={() => setRenaming(true)} />
        </>
      )}
    </ThreadListItemPrimitive.Root>
  );
};

/** 行尾状态（同 Claude）：在跑转圈，跑完没看过、或有没看过的提醒，一个小圆点；正在看的那个不标（顶栏已经在转）；
 *  鼠标移上去让位给「更多」按钮。 */
const ThreadActivity: FC = () => {
  const id = useAuiState((s) => s.threadListItem.remoteId);
  const isMain = useAuiState((s) => s.threads.mainThreadId === s.threadListItem.id);
  const activity = useActivity(id);
  if (!activity || isMain) return null;
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

const ThreadListItemMore: FC<{ open: boolean; onOpenChange: (open: boolean) => void; onRename: () => void }> = ({
  open,
  onOpenChange,
  onRename,
}) => {
  const aui = useAui();
  const starred = useAuiState((s) => !!s.threadListItem.custom?.starred);
  const itemClass = "cc-menu-item";
  return (
    <ThreadListItemMorePrimitive.Root sharedFocusGroup open={open} onOpenChange={onOpenChange}>
      <ThreadListItemMorePrimitive.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          data-slot="aui_thread-list-item-more"
          className="data-[state=open]:bg-hover absolute end-1.5 top-1/2 size-6 -translate-y-1/2 p-0 opacity-0 group-hover/item:opacity-100 group-has-focus-visible/item:opacity-100 data-[state=open]:opacity-100"
        >
          <MoreHorizontalIcon className="size-3.5" />
          <span className="sr-only">{t("more")}</span>
        </Button>
      </ThreadListItemMorePrimitive.Trigger>
      <ThreadListItemMorePrimitive.Content
        side="right"
        align="start"
        sideOffset={6}
        data-slot="aui_thread-list-item-more-content"
        className="cc-menu !min-w-36"
      >
        <ThreadListItemMorePrimitive.Item
          className={itemClass}
          onClick={() => void aui.threadListItem().updateCustom({ starred: !starred })}
        >
          {starred ? <StarOffIcon className="size-4" /> : <StarIcon className="size-4" />}
          {starred ? t("unstar") : t("star")}
        </ThreadListItemMorePrimitive.Item>
        <ThreadListItemMorePrimitive.Item className={itemClass} onClick={onRename}>
          <PencilIcon className="size-4" />
          {t("rename")}
        </ThreadListItemMorePrimitive.Item>
        <ThreadListItemPrimitive.Delete asChild>
          <ThreadListItemMorePrimitive.Item
            data-slot="aui_thread-list-item-more-item"
            data-danger=""
            className="cc-menu-item"
          >
            <TrashIcon className="size-4" />
            {t("delete")}
          </ThreadListItemMorePrimitive.Item>
        </ThreadListItemPrimitive.Delete>
      </ThreadListItemMorePrimitive.Content>
    </ThreadListItemMorePrimitive.Root>
  );
};
