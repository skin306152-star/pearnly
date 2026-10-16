// 侧栏 / 手机抽屉里的对话列表（docs/work/2026-10-16-cc-shell-design.md 3.4–3.8、3.16，同 Claude Code 侧栏）：
//
//   组     — 有公司分组（Cowork）时按「分组和排序」（lib/managed-agents/sidebar-view.ts）：按公司（已置顶 → 各公司 → 最近）、
//            按处理月份、不分组；组标题是 sidebar-group.tsx 的 GroupHeader。没有公司分组（ERP / DMS）同以前：已置顶 + 按日子分组
//   行     — 高按变体（网页 32 / 桌面窗口 30 / 手机 44）、行间 2、圆角 8；标题 14 次要色、超长渐隐，悬停时滚动（ui/marquee.tsx）；
//            公司组里的行缩进到公司名起点；行尾 24 的槽放装饰（在跑的
//            6px 脉冲点、未读点），悬停换成「···」；选中 = 悬停色 + 主色字；「显示 → 元数据」开时第二行「9 月 · 分店 00012」
//   多选   — 电脑上 Shift 点一段、⌘ / Ctrl 点单条（lib/sidebar-keys.ts）；行「···」菜单在选中两条以上时换成「置顶 N」「移到… (N)」
//            「删除 N」，逐条顺序执行；行菜单里单字母 P 置顶、R 重命名、D 删除
//   拖动   — 共用拖动（components/ui/drag.tsx，用户 10-16 选「公司可拖 + 置顶可拖」；只在有公司分组的产品）：
//            按公司分组时，对话行拖到别的公司组（组头或这组的行）= 移到那家（同「移到 ▸」：月份、分店留空，不改置顶），拖到「最近」
//            = 移出公司（没有「最近」组时拖动中临时出一个）；经过的组整块高亮；同组里不调序（照排序方式排），拖回原组不动。多选里拖
//            其中一条 = 整批逐条移（不并发，同批量「移到…」）。公司组拖成任意顺序（整组跟着组头走），按人记住，新出现的公司排最上面
//            （lib/managed-agents/sidebar-order.ts）。已置顶的对话可拖着排先后（三种分组方式都可以）。搜索时只能把对话拖到别的公司。
//            手机抽屉：长按 250ms 后再动 = 拖，长按不动 = 原来的长按菜单。Alt+↑/↓ 在公司组头、置顶行上挪一格。出错在侧栏出错行写服务端原文
import { Button } from "@/components/ui/button";
import { Marquee } from "@/components/ui/marquee";
import { Skeleton } from "@/components/ui/skeleton";
import { useSidebar } from "@/components/ui/sidebar";
import { BulkMoveMenu, focusComposer, MoveToMenu, ProjectHeader, SidebarError, useNewChatIn } from "@/components/assistant-ui/projects";
import {
  altMove,
  chain,
  DragArea,
  type DragData,
  type DragHandle,
  DragList,
  type DragTexts,
  useDragItem,
  useDropTarget,
  useSortableItem,
} from "@/components/ui/drag";
import { GroupHeader, GroupNew, GroupSortMenu, isMenuKey } from "@/components/assistant-ui/sidebar-group";
import { showShortcuts } from "@/components/shortcut";
import { useLongPress } from "@/hooks/use-long-press";
import { cn } from "@/lib/utils";
import { isImeEnter } from "@/lib/ime";
import { co, dayLabel, sb, t } from "@/i18n";
import { branchShort, monthLabel, monthShort } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { useActivity, useBumps } from "@/lib/managed-agents/activity";
import { NO_FILING, type ProjectRow, setDraft, setError, showMore, useProjects } from "@/lib/managed-agents/projects";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { bindFoldUser, toggleFold, toggleFoldSection, useSidebarFold } from "@/lib/managed-agents/sidebar-fold";
import { companySections, flatSections, groupOpen, monthSections } from "@/lib/managed-agents/sidebar-groups";
import { bindOrderUser, forgetPinned, moveBy, moveTo, setCompanyOrder, setPinnedOrder, useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import {
  companyDrag,
  fromDrag,
  moveDragged,
  pinDrag,
  RECENTS_TARGET,
  rowDrag,
  sidebarAccepts,
} from "@/lib/managed-agents/sidebar-drag";
import { bindSidebarViewUser, createdOf, useSidebarView } from "@/lib/managed-agents/sidebar-view";
import { useView } from "@/lib/nav";
import { useProfile } from "@/lib/profile";
import {
  clearSelection,
  currentRow,
  menuLetterKeys,
  rowOrder,
  runInOrder,
  selectRange,
  toggleSelected,
  useSidebarSelection,
} from "@/lib/sidebar-keys";
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
import type { MouseEvent as ReactMouseEvent } from "react";
import { DropdownMenu } from "@/components/ui/menu";
import {
  createContext,
  forwardRef,
  Fragment,
  useContext,
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
 *  按公司分组时滚到底只翻「最近」（各公司由组里的「显示更多」翻）；按月份、不分组时同没有公司分组，一页一页往下翻。 */
const ThreadListMore: FC<{ searching: boolean }> = ({ searching }) => {
  const aui = useAui();
  const kind = useProjects((s) => s.kind);
  const byCompany = useSidebarView((s) => s.by === "company");
  const recentMore = useProjects((s) => !!s.more.recent);
  const hasMore = useAuiState((s) => s.threads.hasMore) && (!kind || !byCompany || searching || recentMore);
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

// 分组可以点标题折叠（同 Claude 侧栏的「今天 ⌄」），折叠哪些记在本机、按用户（lib/managed-agents/sidebar-fold.ts）
function useCollapsedGroups() {
  const collapsed = useSidebarFold((s) => s.collapsed);
  return { collapsed, toggle: toggleFold, toggleSection: toggleFoldSection };
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

// 有公司分组时（Cowork）：按「分组和排序」分组、排序；手机上各组也能折叠。折叠状态每台设备各记各的（pinned、recents、
// project:<编号>、month:<月份>）。每个组标题右侧都有「···」（分组和排序）；公司组、最近另有「新建」。拖动见文件头
/** 侧栏行在哪一种拖动里（行组件由组件库按编号渲染，拿不到参数，靠所在的组给）。 */
type RowScope = { mode: "pin"; locked: boolean; move: (id: string, delta: -1 | 1) => void } | { mode: "row"; group: string };
const RowScopeContext = createContext<RowScope | null>(null);

const CompanyGroups: FC<{ searchQuery: string }> = ({ searchQuery }) => {
  const aui = useAui();
  const { threadIds, itemsById, dates } = useDates();
  const projects = useProjects((s) => s.projects);
  const filings = useProjects((s) => s.filings);
  const shown = useProjects((s) => s.shown);
  const serverMore = useProjects((s) => s.more.groups);
  const view = useSidebarView();
  const order = useSidebarOrder();
  const newChatIn = useNewChatIn();
  const { collapsed, toggleSection } = useCollapsedGroups();
  const query = searchQuery.trim();
  const [lifting, setLifting] = useState<DragData | null>(null);
  // 「分组和排序」、折叠、手排的顺序按用户记：知道是谁了就换成这个人的
  const user = useProfile()?.user;
  useEffect(() => {
    if (!user) return;
    bindSidebarViewUser(user);
    bindFoldUser(user);
    bindOrderUser(user);
  }, [user]);
  // 取消置顶了（单条、批量、别的设备上）：从手排的置顶顺序里去掉，以后再置顶算新的、排最上面。只看已加载的行（没加载的不知道）
  useEffect(() => {
    if (!order.pinned.length) return;
    const unpinned = threadIds.flatMap((id) => {
      const item = itemsById.get(id);
      return item && !item.custom?.starred ? [item.remoteId ?? id] : [];
    });
    const gone = unpinned.filter((key) => order.pinned.includes(key));
    if (gone.length) forgetPinned(gone);
  }, [threadIds, itemsById, order.pinned]);
  // 拖动中侧栏被卸下（换产品、退出）：多选整批变淡的标记一并去掉
  useEffect(() => () => void delete document.documentElement.dataset.ccDragBulk, []);

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
        period: filing?.project ? filing.period : "",
        created: createdOf(item?.remoteId),
        key: item?.remoteId ?? id,
      };
    });
    const sort = { by: view.sort, desc: view.desc };
    const pinnedOrder = order.pinned;
    if (view.by === "month") return monthSections(rows, projects, { query, sort, pinnedOrder });
    if (view.by === "none") return flatSections(rows, projects, { query, sort, pinnedOrder });
    return companySections(rows, projects, { query, shown, serverMore, sort, empty: view.empty, companyOrder: order.companies, pinnedOrder });
  }, [threadIds, itemsById, dates, projects, filings, shown, serverMore, query, view, order]);

  if (!sections) return <NoResults />;
  const local = (index: number) => threadIds[index]!;
  const sessionsOf = (indices: readonly number[]) =>
    indices.map((index) => itemsById.get(threadIds[index]!)?.remoteId).filter((id): id is string => !!id);
  const searching = !!query;
  const byCompany = view.by === "company";

  // 置顶：拖动编号（本页编号）↔ 记顺序用的对话编号
  const pinned = sections.find((section) => section.type === "pinned");
  const pinIds = pinned ? pinned.indices.map((index) => pinDrag(local(index))) : [];
  const pinKey = new Map((pinned?.indices ?? []).map((index) => [pinDrag(local(index)), itemsById.get(local(index))?.remoteId ?? local(index)]));
  const savePins = (next: string[] | null) => next && setPinnedOrder(next.map((id) => pinKey.get(id) ?? fromDrag(id)));
  const companies = sections.filter((section) => section.type === "project");
  const companyIds = companies.map((section) => companyDrag(section.project.id));
  const saveCompanies = (next: string[] | null) => next && setCompanyOrder(next.map(fromDrag));

  const onDrop = (active: DragData, target: DragData) => {
    if (active.kind === "pin") savePins(moveTo(pinIds, active.id, target.id));
    else if (active.kind === "company") saveCompanies(moveTo(companyIds, active.id, target.id));
    else if (active.kind === "row") {
      const selection = useSidebarSelection.getState().ids;
      const bulk = (active.count ?? 0) > 1 && selection.includes(fromDrag(active.id));
      const ids = bulk ? selection : [];
      const sessions = bulk
        ? ids.map((id) => itemsById.get(id)?.remoteId).filter((id): id is string => !!id)
        : active.session ? [active.session] : [];
      void moveDragged(sessions, target.group ?? "", bulk);
    }
  };
  const onActive = (active: DragData | null) => {
    setLifting(active);
    // 多选里拖其中一条：其余选中的行也变淡（整批一起走）
    if (active?.kind === "row" && (active.count ?? 0) > 1) document.documentElement.dataset.ccDragBulk = "";
    else delete document.documentElement.dataset.ccDragBulk;
  };
  const texts: DragTexts = sb.drag;
  // 按公司分组、拿着公司里的对话、眼前没有「最近」组：临时出一个空的「最近」，好拖出公司
  const recentsTarget =
    byCompany && lifting?.kind === "row" && !!lifting.group && !sections.some((section) => section.type === "recents");

  return (
    <DragArea accepts={sidebarAccepts} onDrop={onDrop} onActive={onActive} texts={texts} touch="move">
      <SidebarError />
      {pinned && (
        <SectionGroup label={t("starred")} open={groupOpen({ key: "pinned" }, collapsed, searching)} onToggle={() => toggleSection({ key: "pinned" })}>
          <RowScopeContext.Provider
            value={{ mode: "pin", locked: searching, move: (id, delta) => savePins(moveBy(pinIds, id, delta)) }}
          >
            <DragList id="pins" items={pinIds}>
              <Items indices={pinned.indices} threadIds={threadIds} />
            </DragList>
          </RowScopeContext.Provider>
        </SectionGroup>
      )}
      <DragList id="companies" items={companyIds}>
        {companies.map((section) =>
          section.type === "project" ? (
            <CompanyGroup
              key={section.key}
              project={section.project}
              open={groupOpen(section, collapsed, searching)}
              onToggle={() => toggleSection(section)}
              sessions={sessionsOf(section.all)}
              locked={searching}
              onMove={(delta) => saveCompanies(moveBy(companyIds, companyDrag(section.project.id), delta))}
              more={section.more ? () => void showMore(section.project.id, section.all.length, () => aui.threads().loadMore()) : undefined}
            >
              <Items indices={section.indices} threadIds={threadIds} />
            </CompanyGroup>
          ) : null,
        )}
      </DragList>
      {sections.map((section) => {
        if (section.type === "pinned" || section.type === "project") return null;
        const open = groupOpen({ key: section.key }, collapsed, searching);
        const toggle = () => toggleSection({ key: section.key });
        if (section.type === "recents") {
          const items = <Items indices={section.indices} threadIds={threadIds} />;
          return byCompany ? (
            <RecentsGroup key={section.key} open={open} onToggle={toggle} onNew={() => newChatIn("")}>
              {items}
            </RecentsGroup>
          ) : (
            <SectionGroup key={section.key} label={t("recents")} open={open} onToggle={toggle} onNew={() => newChatIn("")}>
              {items}
            </SectionGroup>
          );
        }
        return (
          <SectionGroup key={section.key} label={section.period ? monthLabel(section.period) : sb.noMonth} open={open} onToggle={toggle}>
            <Items indices={section.indices} threadIds={threadIds} />
          </SectionGroup>
        );
      })}
      {recentsTarget && <RecentsGroup open onToggle={() => undefined} onNew={() => newChatIn("")} />}
    </DragArea>
  );
};

/** 已置顶、最近、月份组的组标题：「···」（分组和排序）是受控菜单，键盘菜单键 / Shift+F10 也打开它（第 5 轮视觉审查）；
 *  菜单开着时 data-open 让「＋」照样露着、「···」不挪位。 */
const SectionHeader: FC<{ label: string; open: boolean; onToggle: () => void; onNew?: () => void }> = ({ label, open, onToggle, onNew }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <GroupHeader label={label} open={open} onToggle={onToggle} data-open={menuOpen || undefined} onMenuKey={() => setMenuOpen(true)}>
      <GroupSortMenu open={menuOpen} onOpenChange={setMenuOpen} />
      {onNew && <GroupNew onClick={onNew} />}
    </GroupHeader>
  );
};

/** 一组（标题 + 这组的行）包成一块：公司组整块拖动、对话拖进来整块高亮都按这一块算。 */
const SectionGroup: FC<{ label: string; open: boolean; onToggle: () => void; onNew?: () => void; children?: ReactNode }> = ({
  label,
  open,
  onToggle,
  onNew,
  children,
}) => (
  <div className="cc-group-section" data-slot="cc-group-section">
    <SectionHeader label={label} open={open} onToggle={onToggle} onNew={onNew} />
    {open && children && <div className="cc-group-items">{children}</div>}
  </div>
);

const RECENTS_SCOPE: RowScope = { mode: "row", group: "" };

/** 按公司分组时的「最近」：没挂公司的对话（可以拖进公司）；也是把对话拖出公司的落点。 */
const RecentsGroup: FC<{ open: boolean; onToggle: () => void; onNew: () => void; children?: ReactNode }> = ({ open, onToggle, onNew, children }) => {
  const target = useDropTarget({ id: RECENTS_TARGET, kind: "recents", label: t("recents"), group: "" });
  return (
    <div {...target} className="cc-group-section" data-slot="cc-group-section">
      <SectionHeader label={t("recents")} open={open} onToggle={onToggle} onNew={onNew} />
      {open && children && (
        <div className="cc-group-items">
          <RowScopeContext.Provider value={RECENTS_SCOPE}>{children}</RowScopeContext.Provider>
        </div>
      )}
    </div>
  );
};

/** 一家公司的组：组头开关是这一组的拖动把手（整组跟着走），这组的行可以拖到别的公司；对话拖进来时整块高亮。 */
const CompanyGroup: FC<{
  project: ProjectRow;
  open: boolean;
  onToggle: () => void;
  sessions: readonly string[];
  locked: boolean;
  onMove: (delta: -1 | 1) => void;
  more?: () => void;
  children: ReactNode;
}> = ({ project, open, onToggle, sessions, locked, onMove, more, children }) => {
  const drag = useSortableItem(
    { id: companyDrag(project.id), kind: "company", label: project.title, list: "companies", group: project.id },
    { disabled: locked, ownFocus: true },
  );
  const scope = useMemo<RowScope>(() => ({ mode: "row", group: project.id }), [project.id]);
  return (
    <div {...drag.node} className="cc-group-section" data-slot="cc-group-section">
      <ProjectHeader project={project} open={open} onToggle={onToggle} sessions={sessions} handle={drag.handle} onMove={locked ? undefined : onMove} />
      {open && (
        <div data-slot="cc-project-items" className="cc-group-items">
          <RowScopeContext.Provider value={scope}>{children}</RowScopeContext.Provider>
          {more && (
            <button type="button" data-roving="" className="cc-show-more" onClick={more}>
              {co.showMore}
            </button>
          )}
        </div>
      )}
    </div>
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
          data-roving=""
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
          data-roving=""
          title={t("newChat")}
          className={cn("cc-nav-item", className)}
          {...props}
          onClick={(e) => {
            // 顶部「新建」开在「最近」（组头 ＋、主档页输入框才开在公司里）；光标直接进输入框（同 Claude）
            setDraft({});
            props.onClick?.(e);
            focusComposer();
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

// 侧栏一行只有标题（同 Claude 侧栏，用户 2026-10-13：不要前面的对话气泡图标），「显示 → 元数据」开时下面一行月份、分店。
// 在置顶组里可拖着排先后，在公司组 / 最近里可拖到别的公司（RowScopeContext，共用拖动）；ERP / DMS 与按月份、不分组时的其余行不能拖
export const ThreadListItem: FC = () => {
  const scope = useContext(RowScopeContext);
  if (scope?.mode === "pin") return <PinnedRow scope={scope} />;
  if (scope?.mode === "row") return <MovableRow group={scope.group} />;
  return <Row />;
};

// 从行尾「···」、菜单、改名输入框按下不拿起
const ROW_SKIP = '[data-slot="aui_thread-list-item-more"], [role="menu"], input';

const PinnedRow: FC<{ scope: Extract<RowScope, { mode: "pin" }> }> = ({ scope }) => {
  const localId = useAuiState((s) => s.threadListItem.id);
  const title = useAuiState((s) => s.threadListItem.title) || t("untitled");
  const id = pinDrag(localId);
  const drag = useSortableItem({ id, kind: "pin", label: title, list: "pins" }, { disabled: scope.locked, ownFocus: true, skip: ROW_SKIP });
  return <Row drag={drag} onMove={scope.locked ? undefined : (delta) => scope.move(id, delta)} />;
};

const MovableRow: FC<{ group: string }> = ({ group }) => {
  const localId = useAuiState((s) => s.threadListItem.id);
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const title = useAuiState((s) => s.threadListItem.title) || t("untitled");
  // 多选里的一条：整批一起拖（浮层上写条数）
  const count = useSidebarSelection((s) => (s.ids.length > 1 && s.ids.includes(localId) ? s.ids.length : 0));
  const drag = useDragItem(
    { id: rowDrag(localId), kind: "row", label: count ? sb.drag.items(count) : title, group, count: count || undefined, session: remoteId },
    { disabled: !remoteId, ownFocus: true, skip: ROW_SKIP },
  );
  return <Row drag={drag} />;
};

type RowDrag = { node: { ref: (el: HTMLElement | null) => void } & Record<string, unknown>; handle: DragHandle };

const Row: FC<{ drag?: RowDrag; onMove?: (delta: -1 | 1) => void }> = ({ drag, onMove }) => {
  const [renaming, setRenaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const press = useLongPress(() => setMenuOpen(true));
  // 点的就是正开着的这个对话（例如手机抽屉里再点一次）：不会切换、也就不会重进，同样向服务端核对一次此刻的状态。
  // 点的是同一段对话的另一行数据：也不切换（切过去会另起一份，输入框里没发的字就没了）
  const id = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const { current, isMain } = useCurrent();
  const { isMobile } = useSidebar();
  const selected = useSidebarSelection((s) => s.ids.includes(localId));
  const companies = !!useProjects((s) => s.kind);
  const reenter = (event: React.MouseEvent) => {
    if (!current) return;
    if (!isMain) event.preventDefault();
    if (id) getSessionController(id).refreshStatus();
  };
  // 电脑上 Shift 点一段（第一次从正开着的那一行算起）、⌘ / Ctrl 点单条：只选不切对话；不带修饰键点一行就清空多选（同 Claude Code）。
  // 只在有公司分组的产品（Cowork）：ERP / DMS 的侧栏不变
  const pick = (event: ReactMouseEvent<HTMLElement>) => {
    if (isMobile || !companies || (event.target as HTMLElement).closest('[data-slot="aui_thread-list-item-more"], [role="menu"], input')) return;
    if (!(event.shiftKey || event.metaKey || event.ctrlKey)) return clearSelection();
    event.preventDefault();
    event.stopPropagation();
    if (event.shiftKey) selectRange(rowOrder(event.currentTarget), localId, currentRow(event.currentTarget));
    else toggleSelected(localId);
  };
  // 拖动：整行是拖动的那一项，行的开关按钮是把手（鼠标按住动 3px、手机长按后再动、键盘空格）
  const { ref: handleRef, onMouseDown: dragMouse, onTouchStart: dragTouch, onKeyDown: dragKey, onKeyUp: dragKeyUp, ...handleAria } =
    drag?.handle ?? ({} as Partial<DragHandle>);
  return (
    <ThreadListItemPrimitive.Root
      {...press}
      {...drag?.node}
      onTouchStart={chain(press.onTouchStart, renaming ? undefined : dragTouch)}
      onKeyUp={renaming ? undefined : dragKeyUp}
      data-active={current ? "true" : undefined}
      aria-current={current ? "true" : undefined}
      data-row-id={localId}
      data-selected={selected ? "" : undefined}
      data-slot="aui_thread-list-item"
      data-marquee-host=""
      onClickCapture={pick}
      onMouseDown={(e) => {
        if (e.shiftKey && !isMobile && companies) e.preventDefault(); // Shift 点不选中文字
        if (!renaming) dragMouse?.(e);
      }}
      onKeyDown={(e) => {
        if (renaming) return;
        if (isMenuKey(e)) {
          e.preventDefault();
          setMenuOpen(true);
          return;
        }
        if (onMove && altMove(e, onMove)) return;
        dragKey?.(e);
      }}
      className="group/item hover:bg-row-hover focus-visible:bg-row-hover data-active:bg-row-hover has-focus-visible:bg-row-hover has-data-[state=open]:bg-row-hover text-ink-2 data-active:text-ink has-focus-visible:text-ink relative flex min-h-(--cc-row-h) items-center rounded-md transition-colors focus-visible:outline-none"
    >
      {renaming ? (
        <ThreadListItemRename onDone={() => setRenaming(false)} />
      ) : (
        <>
          <ThreadListItemPrimitive.Trigger
            ref={handleRef}
            {...handleAria}
            data-slot="aui_thread-list-item-trigger"
            data-roving=""
            onClick={reenter}
            className="cc-row-trigger focus-visible:shadow-focus flex min-h-(--cc-row-h) min-w-0 flex-1 items-center self-stretch rounded-md text-start outline-none"
          >
            <span className="cc-row-text">
              <Marquee data-slot="aui_thread-list-item-title" className="cc-row-title">
                <ThreadListItemPrimitive.Title fallback={t("untitled")} />
              </Marquee>
              <RowMeta />
            </span>
            <span className="cc-row-end">
              <ThreadActivity />
            </span>
          </ThreadListItemPrimitive.Trigger>
          <ThreadListItemMore open={menuOpen} onOpenChange={setMenuOpen} onRename={() => setRenaming(true)} />
        </>
      )}
    </ThreadListItemPrimitive.Root>
  );
};

/** 行的第二行（「显示 → 元数据」开着、挂在公司下且有可写的才有）：按公司分组时「9 月 · 分店 00012」；按月份时「公司 · 分店」；
 *  不分组时「公司 · 月份 · 分店」。 */
const RowMeta: FC = () => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const meta = useSidebarView((s) => s.meta);
  const by = useSidebarView((s) => s.by);
  const kind = useProjects((s) => s.kind);
  const filing = useProjects((s) => (remoteId && s.filings[remoteId]) || (localId && s.filings[localId]) || NO_FILING);
  const company = useProjects((s) => s.projects.find((p) => p.id === filing.project)?.title ?? "");
  if (!kind || !meta || !filing.project) return null;
  const month = monthShort(filing.period);
  const branch = branchShort(filing.scope);
  const parts = by === "company" ? [month, branch] : by === "month" ? [company, branch] : [company, month, branch];
  const text = parts.filter(Boolean).join(" · ");
  if (!text) return null;
  return (
    <Marquee className="cc-row-meta" data-slot="cc-row-meta">
      {text}
    </Marquee>
  );
};

/** 行尾状态（同 Claude Code）：在跑一个 6px 的脉冲点，跑完没看过、或有没看过的提醒，一个蓝点；正在看的那个不标（顶栏已经在转）——
 *  在定时任务页时当前对话没人在看，照样标；鼠标移上去、键盘停在这一行时让位给「···」。 */
const ThreadActivity: FC = () => {
  const id = useAuiState((s) => s.threadListItem.remoteId);
  const { current } = useCurrent();
  const { view } = useView();
  const activity = useActivity(id);
  if (!activity || (current && view === "chat")) return null;
  const label = activity === "running" ? t("running") : activity === "notice" ? t("newNotice") : t("unread");
  return <span className="cc-activity" data-activity={activity} role="img" aria-label={label} />;
};

/** 菜单项右边的单字母快捷键（菜单开着时按这个键就执行；触屏不显示）。 */
const MenuKey: FC<{ letter: string }> = ({ letter }) => (showShortcuts() ? <span className="cc-menu-key">{letter}</span> : null);

// 就地改名（同 Claude Code）：输入框与原来的文字同字号、一行高、透明底，聚焦只有 1.5px 蓝描边（globals.css 的 .cc-inline-edit；
// 侧栏行里 .cc-row-edit，标题栏里调用方给 .cc-title-edit）。回车或离开输入框保存，Esc 放弃；空标题不保存；
// 输入法选字的回车不算（lib/ime.ts）；保存或放弃后输入框卸下时的失焦不再存第二次。
export const ThreadListItemRename: FC<{ onDone: () => void; className?: string }> = ({ onDone, className = "cc-row-edit" }) => {
  const aui = useAui();
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const [value, setValue] = useState(title);
  const finished = useRef(false);
  const finish = (save: boolean) => {
    if (finished.current) return;
    finished.current = true;
    const next = value.trim();
    if (save && next && next !== title) void aui.threadListItem().rename(next);
    onDone();
  };
  return (
    <input
      autoFocus
      value={value}
      aria-label={t("rename")}
      onChange={(e) => setValue(e.target.value)}
      onFocus={(e) => e.target.select()}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !isImeEnter(e)) {
          e.preventDefault();
          finish(true);
        }
        // 只取消改名：标记已处理，浮着的会话栏（session-panel 的 Esc）不跟着关；输入法选字时的 Esc 只取消候选词
        if (e.key === "Escape" && !e.nativeEvent.isComposing) {
          e.preventDefault();
          finish(false);
        }
      }}
      className={cn("cc-inline-edit", className)}
    />
  );
};

// 对话菜单（同 Claude Code）：置顶 P / 重命名 R / 移到 ▸（有公司分组时）/ 删除 D，向下开、右对齐。这一行在多选里、选了两条以上时
// 换成批量项（BulkItems）。用 radix 的下拉菜单才放得下「移到 ▸」这一层子菜单。
const ThreadListItemMore: FC<{ open: boolean; onOpenChange: (open: boolean) => void; onRename: () => void }> = ({
  open,
  onOpenChange,
  onRename,
}) => {
  const aui = useAui();
  const custom = useAuiState((s) => s.threadListItem.custom);
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const selection = useSidebarSelection((s) => s.ids);
  const starred = !!custom?.starred;
  const companies = !!useProjects((s) => s.kind);
  const bulk = companies && selection.length > 1 && selection.includes(localId);
  const close = () => onOpenChange(false);
  // 展开写法：组件库整份替换 custom，以后加的键不能被冲掉
  const pin = () => void aui.threadListItem().updateCustom({ ...custom, starred: !starred });
  const remove = () => void aui.threadListItem().delete();
  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          tabIndex={-1}
          data-slot="aui_thread-list-item-more"
          className="data-[state=open]:bg-hover absolute end-1 top-1/2 size-(--cc-row-ctl) -translate-y-1/2 rounded-sm p-0 opacity-0 group-hover/item:opacity-100 group-has-focus-visible/item:opacity-100 data-[state=open]:opacity-100"
        >
          <MoreHorizontalIcon className="size-(--cc-icon-sm)" />
          <span className="sr-only">{t("more")}</span>
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        {bulk ? (
          <BulkItems ids={selection} close={close} />
        ) : (
          <DropdownMenu.Content
            side="bottom"
            align="end"
            sideOffset={4}
            data-slot="aui_thread-list-item-more-content"
            className="cc-menu !min-w-44"
            onKeyDown={menuLetterKeys({ KeyP: pin, KeyR: onRename, KeyD: remove }, close)}
          >
            <DropdownMenu.Item className="cc-menu-item" onSelect={pin}>
              {starred ? <PinOffIcon className="size-4" /> : <PinIcon className="size-4" />}
              <span className="flex-1">{starred ? t("unstar") : t("star")}</span>
              <MenuKey letter="P" />
            </DropdownMenu.Item>
            <DropdownMenu.Item className="cc-menu-item" onSelect={onRename}>
              <PencilIcon className="size-4" />
              <span className="flex-1">{t("rename")}</span>
              <MenuKey letter="R" />
            </DropdownMenu.Item>
            <MoveToMenu sessionId={remoteId} localId={localId} />
            <DropdownMenu.Item data-slot="aui_thread-list-item-more-item" data-danger="" className="cc-menu-item" onSelect={remove}>
              <TrashIcon className="size-4" />
              <span className="flex-1">{t("delete")}</span>
              <MenuKey letter="D" />
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        )}
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

/** 多选时的行菜单（同 Claude Code，没有批量栏）：「置顶 N」（都已置顶时「取消置顶 N」）、「将 N 项移到…」、「删除 N」。
 *  用组件库各行自己的操作一条一条做（不并发，lib/sidebar-keys.ts 的 runInOrder），做完清空多选；出错显示在侧栏那一行。
 *  只做列表里还在的（别处刚删掉的不再去取；看不见的行由 sidebar-keys 从选中里去掉）。 */
const BulkItems: FC<{ ids: readonly string[]; close: () => void }> = ({ ids: selected, close }) => {
  const aui = useAui();
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const { ids, allPinned, sessions } = useMemo(() => {
    const rows = selected.flatMap((id) => threadItems.filter((item) => item.id === id).slice(0, 1));
    return {
      ids: rows.map((row) => row.id),
      allPinned: rows.every((row) => !!row.custom?.starred),
      sessions: rows.map((row) => row.remoteId).filter((id): id is string => !!id),
    };
  }, [selected, threadItems]);
  const report = (error: unknown) => setError(shownError(error));
  const pinAll = () =>
    void runInOrder(ids, async (id) => {
      const item = aui.threads().item({ id });
      const { custom } = item.getState();
      if (!!custom?.starred !== !allPinned) await item.updateCustom({ ...custom, starred: !allPinned });
    }, report).then(clearSelection);
  const deleteAll = () => void runInOrder(ids, async (id) => await aui.threads().item({ id }).delete(), report).then(clearSelection);
  return (
    <DropdownMenu.Content
      side="bottom"
      align="end"
      sideOffset={4}
      data-slot="aui_thread-list-item-more-content"
      className="cc-menu !min-w-44"
      onKeyDown={menuLetterKeys({ KeyP: pinAll, KeyD: deleteAll }, close)}
    >
      <DropdownMenu.Item className="cc-menu-item" onSelect={pinAll}>
        {allPinned ? <PinOffIcon className="size-4" /> : <PinIcon className="size-4" />}
        <span className="flex-1">{allPinned ? sb.unpinN(ids.length) : sb.pinN(ids.length)}</span>
        <MenuKey letter="P" />
      </DropdownMenu.Item>
      <BulkMoveMenu sessions={sessions} onDone={clearSelection} />
      <DropdownMenu.Item data-danger="" className="cc-menu-item" onSelect={deleteAll}>
        <TrashIcon className="size-4" />
        <span className="flex-1">{sb.deleteN(ids.length)}</span>
        <MenuKey letter="D" />
      </DropdownMenu.Item>
    </DropdownMenu.Content>
  );
};
