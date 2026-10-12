
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSidebar } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { t } from "@/i18n";
import {
  AuiIf,
  ThreadListItemMorePrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  MoreHorizontalIcon,
  PencilIcon,
  StarIcon,
  StarOffIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
} from "lucide-react";
import {
  forwardRef,
  Fragment,
  useMemo,
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
      </AuiIf>
    </div>
  );
};

type ThreadListGroup = { label: string; indices: number[] };

const ThreadListItemGroups: FC<{ searchQuery?: string }> = ({
  searchQuery = "",
}) => {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const threadItems = useAuiState((s) => s.threads.threadItems);

  const query = searchQuery.trim().toLowerCase();

  const { filteredIndices, groups } = useMemo(() => {
    const itemsById = new Map(threadItems.map((item) => [item.id, item]));
    const dates = threadIds.map((id) => itemsById.get(id)?.lastMessageAt);
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
    if (starred.length) result.push({ label: t("starred"), indices: starred });
    const rest = sorted.filter((index) => !starred.includes(index));
    if (rest.length) result.push({ label: t("recents"), indices: rest });
    return { filteredIndices, groups: result };
  }, [threadIds, threadItems, query]);

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

  return groups.map((group) => (
    <Fragment key={group.label}>
      <div data-slot="aui_thread-list-group-label" className="cc-section-label">
        {group.label}
      </div>
      {group.indices.map((index) => (
        <ThreadListPrimitive.ItemByIndex
          key={threadIds[index]}
          index={index}
          components={{ ThreadListItem }}
        />
      ))}
    </Fragment>
  ));
};

export const ThreadListNew = forwardRef<
  HTMLButtonElement,
  ComponentPropsWithoutRef<typeof Button> & { labelClassName?: string }
>(({ className, labelClassName, children, ...props }, ref) => {
  return (
    <ThreadListPrimitive.New asChild>
      <Button
        ref={ref}
        variant="ghost"
        data-slot="aui_thread-list-new"
        className={cn("cc-nav-item justify-start font-normal", className)}
        {...props}
      >
        {children ?? (
          <>
            <span className="bg-subtle text-ink flex size-6 shrink-0 items-center justify-center rounded-full">
              <PlusIcon data-slot="aui_thread-list-new-icon" className="!size-3.5" />
            </span>
            <span
              data-slot="aui_thread-list-new-label"
              className={cn("whitespace-nowrap", labelClassName)}
            >
              {t("newChat")}
            </span>
          </>
        )}
      </Button>
    </ThreadListPrimitive.New>
  );
});

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

export const ThreadListItem: FC = () => {
  const [renaming, setRenaming] = useState(false);
  return (
    <ThreadListItemPrimitive.Root
      data-slot="aui_thread-list-item"
      className="group hover:bg-hover focus-visible:bg-hover data-active:bg-active has-focus-visible:bg-hover has-data-[state=open]:bg-hover text-ink-2 data-active:text-ink relative flex h-8 items-center rounded-sm transition-colors focus-visible:outline-none"
    >
      {renaming ? (
        <ThreadListItemRename onDone={() => setRenaming(false)} />
      ) : (
        <>
          <ThreadListItemPrimitive.Trigger
            data-slot="aui_thread-list-item-trigger"
            className="focus-visible:ring-ring/50 flex h-full min-w-0 flex-1 items-center rounded-md px-2.5 text-start text-sm outline-none group-hover:pe-9 group-has-focus-visible:pe-9 group-has-data-[state=open]:pe-9 group-data-active:pe-9 focus-visible:ring-[3px]"
          >
            <span
              data-slot="aui_thread-list-item-title"
              className="min-w-0 flex-1 truncate"
            >
              <ThreadListItemPrimitive.Title fallback={t("untitled")} />
            </span>
          </ThreadListItemPrimitive.Trigger>
          <ThreadListItemMore onRename={() => setRenaming(true)} />
        </>
      )}
    </ThreadListItemPrimitive.Root>
  );
};

// 行内改名（同 claude.ai）：回车或离开输入框保存，Esc 放弃；空标题不保存。
const ThreadListItemRename: FC<{ onDone: () => void }> = ({ onDone }) => {
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
      className="cc-input w-full"
    />
  );
};

const ThreadListItemMore: FC<{ onRename: () => void }> = ({ onRename }) => {
  const aui = useAui();
  const starred = useAuiState((s) => !!s.threadListItem.custom?.starred);
  const itemClass = "cc-menu-item";
  return (
    <ThreadListItemMorePrimitive.Root sharedFocusGroup>
      <ThreadListItemMorePrimitive.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          data-slot="aui_thread-list-item-more"
          className="data-[state=open]:bg-hover absolute end-1.5 top-1/2 size-6 -translate-y-1/2 p-0 opacity-0 group-hover:opacity-100 group-has-focus-visible:opacity-100 group-data-active:opacity-100 data-[state=open]:opacity-100"
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
