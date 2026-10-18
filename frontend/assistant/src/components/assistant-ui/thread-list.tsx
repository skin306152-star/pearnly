// 侧栏 / 手机抽屉里的对话列表（docs/work/2026-10-16-cc-shell-design.md 3.4–3.8、3.16，同 Claude Code 侧栏）：
//
//   组     — 有公司分组（Cowork）时按「分组和排序」（lib/managed-agents/sidebar-view.ts）：按公司（已置顶 → 各公司 → 最近）、
//            按处理月份、不分组；组标题是 sidebar-group.tsx 的 GroupHeader。没有公司分组（ERP / DMS）同以前：已置顶 + 按日子分组
//   行     — 高按变体（网页 32 / 桌面窗口 30 / 手机 44）、行间 2、圆角 8；标题 14 次要色、超长渐隐，悬停时滚动（ui/marquee.tsx）；
//            公司组里的行缩进到公司名起点；行尾 24 的槽放装饰（在跑的
//            6px 脉冲点、未读点），悬停换成「···」；选中 = 悬停色 + 主色字；「显示 → 元数据」开时第二行「9 月 · 分店 00012」
//   多选   — 电脑上 Shift 点一段、⌘ / Ctrl 点单条（lib/sidebar-keys.ts）；行「···」菜单在选中两条以上时换成「置顶 N」「删除 N」，
//            逐条顺序执行；行菜单里单字母 P 置顶、R 重命名、D 删除
//   拖动   — 共用拖动（components/ui/drag.tsx，用户 10-16 选「公司可拖 + 置顶可拖」；只在有公司分组的产品），效果同其他拖动
//            （用户 10-16「对话的拖动效果怎么没和其它拖动效果统一呢」）。按公司分组时对话行只在它自己那一组（某家公司，或「最近」）
//            里拖着排先后，或拖进「已置顶」= 置顶它、按落点排进置顶的顺序（用户 10-17：不能跨公司，「移到」已删；规则见
//            lib/managed-agents/sidebar-drag.ts）。没有已置顶时，指针拖到侧栏顶上（第一组的组头上半截以上）才临时出一个空的
//            「已置顶」（拿起那一刻不出：会把整列往下推，拿起的那一行离开指针；键盘拖动不出），指针离开它往下走远了再收起；指针进了
//            已置顶那一块，拖的这一行就排进去（原组的空档合上、置顶的行让开），回到自己那一组就挪回去，停在别处（别的公司组、两组
//            之间）就没有落点：空档回原位，松手滑回原位、什么都不改；已置顶收着时只高亮组头，放下落到组头并收起、排在置顶最上面。
//            组里手排的先后按人记住（lib/managed-agents/sidebar-order.ts：新对话出现在顶上，「分组和排序」里选排序方式就回到
//            按排序方式排；刷新、换设备后第一页带回手排列表里的对话）。多选里拖其中一条：拖进已置顶 = 整批置顶（按侧栏里的先后排进落点）；选中的全在这一组时落在本组 = 整批作为
//            一块排到落点，否则本组不是落点。公司组拖成任意顺序（整组跟着组头走），按人记住，新出现的公司排最上面。已置顶的对话可拖着
//            排先后（三种分组方式都可以），不拖出去取消置顶。按处理月份、不分组时其余行不能拖；搜索时都不能拖。
//            手机抽屉：长按 250ms 后再动 = 拖，长按不动 = 原来的长按菜单。键盘空格拿起、方向键只在本组里挪（不拖进置顶），Alt+↑/↓
//            在公司组头、置顶行、组里的行上挪一格。出错在侧栏出错行写服务端原文
import { Button } from "@/components/ui/button";
import { Marquee } from "@/components/ui/marquee";
import { Skeleton } from "@/components/ui/skeleton";
import { useSidebar } from "@/components/ui/sidebar";
import { focusComposer, ProjectHeader, SidebarError, useNewChatIn } from "@/components/assistant-ui/projects";
import {
  altMove,
  chain,
  DragArea,
  type DragData,
  type DragHandle,
  DragList,
  type DragTexts,
  type MoveSpot,
  spotIn,
  useDropZone,
  useSortableItem,
} from "@/components/ui/drag";
import { GroupHeader, GroupNew, GroupSortMenu, isMenuKey } from "@/components/assistant-ui/sidebar-group";
import { showShortcuts } from "@/components/shortcut";
import { askDelete, deleteChat } from "@/components/delete-chats";
import { useLongPressMenu } from "@/hooks/use-long-press";
import { cn } from "@/lib/utils";
import { isImeEnter } from "@/lib/ime";
import { isDragging } from "@/lib/layers";
import { co, dayLabel, sb, t } from "@/i18n";
import { branchShort, monthLabel, monthShort } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { useActivity, useBumps } from "@/lib/managed-agents/activity";
import { NO_FILING, type ProjectRow, setDraft, setError, showMore, useProjects } from "@/lib/managed-agents/projects";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { bindFoldUser, toggleFold, toggleFoldSection, useSidebarFold } from "@/lib/managed-agents/sidebar-fold";
import {
  companySections,
  flatSections,
  groupOpen,
  groupStamp,
  monthSections,
  pinGuest,
  type SidebarRow,
  unsentDrafts,
  withPinned,
} from "@/lib/managed-agents/sidebar-groups";
import {
  bindOrderUser,
  forgetGroups,
  forgetInGroups,
  forgetPinned,
  applyLifts,
  type LiftSpot,
  moveBy,
  moveTo,
  setCompanyOrder,
  setGroupOrder,
  setPinnedOrder,
  starChat,
  starPending,
  useLifts,
  useSidebarOrder,
} from "@/lib/managed-agents/sidebar-order";
import {
  companyDrag,
  fromDrag,
  PINS,
  pinDrag,
  pinDrop,
  pinSpot,
  placeBlock,
  rowDrag,
  rowList,
  sidebarAccepts,
  zoneDrag,
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
import { DropdownMenu, menuOffset } from "@/components/ui/menu";
import { fixedPx } from "@/lib/css-var";
import {
  createContext,
  forwardRef,
  Fragment,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type FC,
  type ReactNode,
} from "react";

/** 菜单里的「置顶 / 取消置顶」（行菜单、P 键、手机长按菜单、标题栏菜单同一道，sidebar-order.ts 的 starChat）：失败时组件库撤回、
 *  这一条原样留在原位，侧栏出错行写一句通用的原因（断网等；测试窗口第 10 轮 B 段：原来什么都不说）。 */
export function toggleStar(item: Parameters<typeof starChat>[0], starred: boolean) {
  void starChat(item, starred).catch((error: unknown) => setError(shownError(error)));
}

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
      <SearchIcon data-slot="aui_thread-list-search-icon" className="size-(--cc-icon-sm) shrink-0" />
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
      className={cn("flex flex-col gap-(--cc-space-2)", className)}
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
      className={cn("flex flex-col gap-(--cc-space-2)", className)}
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

/** 列表滚到离底这么远就预取下一页（IntersectionObserver 的 rootMargin）：值在 globals.css 的 --cc-observe-margin，建观察器时读
 *  （标准化记录 2.6）；读不到用回退值，与 globals.css 同值，测试核对。 */
export const OBSERVE_MARGIN = 200;
const observeMargin = () => fixedPx("--cc-observe-margin", OBSERVE_MARGIN);

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
    }, { rootMargin: `${observeMargin()}px` });
    observer.observe(el);
    return () => observer.disconnect();
  }, [aui, hasMore, loadingMore, searching]);
  if (!hasMore && !loadingMore) return null;
  return (
    <div ref={ref} className="flex h-(--cc-row-h) items-center px-(--cc-row-pad) text-ink-3">
      {loadingMore && <LoaderIcon className="cc-spinner" />}
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

/** 选了文件还没发的空对话不进侧栏行，当前打开的那条照常显示（sidebar-groups.ts 的 unsentDrafts，用户 10-16）。 */
function useUnsent(): ReadonlySet<string> {
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const mainId = useAuiState((s) => s.threads.mainThreadId);
  return useMemo(() => unsentDrafts(threadItems, mainId), [threadItems, mainId]);
}

/** 侧栏每行的最后活动时间（「上次更新」按它排）：列表给的（服务端在发消息、CC 回复、挂提醒时记）与本页收到提醒、新消息的时间里
 *  较晚的一个（有动静的对话不等重拉列表就浮到最上面，activity.ts 的 useBumps）。 */
function useDates() {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const bumps = useBumps();
  const unsent = useUnsent();
  return useMemo(() => {
    const itemsById = new Map(threadItems.map((item) => [item.id, item]));
    const dates = threadIds.map((id) => {
      const item = itemsById.get(id);
      const bumped = item?.remoteId ? bumps.get(item.remoteId) : undefined;
      const listed = item?.lastMessageAt;
      return bumped && (!listed || bumped > listed.getTime()) ? new Date(bumped) : listed;
    });
    return { threadIds, itemsById, dates, unsent };
  }, [threadIds, threadItems, bumps, unsent]);
}

const NoResults: FC = () => (
  <div data-slot="aui_thread-list-empty" className="cc-empty-line">
    {t("noResults")}
  </div>
);

// 记住（memo）：侧栏整列重画（拖进已置顶、挪回原组、落定存顺序）时，没变的组的行不跟着重画（第 11 轮 B14）
const Items = memo(function Items({ indices, threadIds }: { indices: readonly number[]; threadIds: readonly string[] }) {
  return indices.map((index) => <ThreadListPrimitive.ItemByIndex key={threadIds[index]} index={index} components={ITEM_COMPONENTS} />);
});

// 有公司分组时（Cowork）：按「分组和排序」分组、排序；手机上各组也能折叠。折叠状态每台设备各记各的（pinned、recents、
// project:<编号>、month:<月份>）。每个组标题右侧都有「···」（分组和排序）；公司组、最近另有「新建」。拖动见文件头
/** 侧栏行在哪一种拖动里（行组件由组件库按编号渲染，拿不到参数，靠所在的组给）：置顶里排先后（guest：拖进置顶、还没放下的那一行，
 *  照旧按它那一组的拖动挂着）；组里排先后（group：公司编号，最近 ""）。 */
type GroupScope = { mode: "row"; group: string; locked: boolean; move: (id: string, delta: -1 | 1) => MoveSpot | null };
type RowScope =
  | { mode: "pin"; locked: boolean; move: (id: string, delta: -1 | 1) => MoveSpot | null; guest?: { local: string; scope: GroupScope } }
  | GroupScope;
const RowScopeContext = createContext<RowScope | null>(null);

/** 拖进已置顶、还没放下的那一行（本页编号、它在组件库 threadIds 里的位置、排在置顶的第几位、它原来那一组）。 */
type Guest = { local: string; index: number; at: number; group?: string };

const CompanyGroups: FC<{ searchQuery: string }> = ({ searchQuery }) => {
  const aui = useAui();
  const { threadIds, itemsById, dates, unsent } = useDates();
  const projects = useProjects((s) => s.projects);
  const kind = useProjects((s) => s.kind);
  const filings = useProjects((s) => s.filings);
  const shown = useProjects((s) => s.shown);
  const serverMore = useProjects((s) => s.more.groups);
  const view = useSidebarView();
  const order = useSidebarOrder();
  const newChatIn = useNewChatIn();
  const { collapsed, toggleSection } = useCollapsedGroups();
  const query = searchQuery.trim();
  // 拿起、放下不重画整列（测试窗口第 10 轮 B 段：拿起那一刻 150–200ms 的长帧里有一份是这里整列重算）：拖的是什么不记成状态，
  // 摆法只看下面两样（指针到了顶上、拖进已置顶的那一行），它们变了才重画
  // 拖对话、没有已置顶时，指针拖到了侧栏顶上：临时出一个空的已置顶（onPointer）；first：它前面的标记（下一个就是第一组）
  const [reveal, setReveal] = useState(false);
  const first = useRef<HTMLSpanElement>(null);
  // 拖进已置顶、还没放下的那一行（onOver 改；ref 给连着几次经过用，state 给摆法）
  const [guest, setGuest] = useState<Guest | null>(null);
  const guestRef = useRef<Guest | null>(null);
  const placeGuest = (next: Guest | null) => {
    guestRef.current = next;
    setGuest(next);
  };
  // 拖进已置顶放下、服务端还没回的（对话编号）：先按置顶摆在落点（置顶是逐条做的，整批的其余各条不先回到原组再跳上来）
  const [pinning, setPinning] = useState<ReadonlySet<string>>(() => new Set());
  // 多选里拖其中一条时，选中的是不是全在拖的这一条那一组（不全在：本组不是落点，只能置顶）
  const wholeBatch = useRef(true);
  const scopes = useRef<{ searching: boolean; map: Map<string, GroupScope> }>({ searching: false, map: new Map() });
  const latestGroups = useRef<{
    rowIdsOf: (group: string) => string[];
    saveGroup: (group: string, next: string[] | null, moved?: readonly string[]) => string[] | null;
  }>({ rowIdsOf: () => [], saveGroup: (_group, next) => next });
  // 取消置顶的对话回到它那一组的最上面（用户 10-17；记号由取消置顶这件事本身记下，sidebar-order.ts 的 noteUnpinned）：眼前有这一行
  // 时照数据挪（它在哪一组、这组此刻最新的时间，各种分组方式、搜索中都一样）。放在下面「置顶了的从组里手排的先后里去掉」之前：
  // 请求失败、组件库撤回置顶时先撤回这次挪动，再按置顶收拾
  const liftTick = useLifts((s) => s.tick);
  const spotRef = useRef<(key: string) => LiftSpot>(() => null);
  // 「分组和排序」、折叠、手排的顺序按用户记：知道是谁了就换成这个人的
  const user = useProfile()?.user;
  useEffect(() => {
    if (!user) return;
    bindSidebarViewUser(user);
    bindFoldUser(user);
    bindOrderUser(user);
  }, [user]);
  useEffect(() => applyLifts(spotRef.current), [liftTick, threadIds, itemsById, order.groups, pinning]);
  // 取消置顶了（单条、批量、别的设备上）：从手排的置顶顺序里去掉，以后再置顶算新的、排最上面；置顶了的从组里手排的先后里去掉。
  // 只看已加载的行（没加载的不知道），正在置顶的、本机请求还没回的不算（失败撤回时置顶顺序原样，不跳位；本机的确认后由 starChat 收拾）
  useEffect(() => {
    const unpinned: string[] = [];
    const pinnedNow: string[] = [];
    for (const id of threadIds) {
      const item = itemsById.get(id);
      if (!item) continue;
      const key = item.remoteId ?? id;
      if (starPending(key)) continue; // 本机刚点、服务端还没回：组件库是乐观地先这样显示，确认了才收拾（sidebar-order.ts）
      if (item.custom?.starred) pinnedNow.push(key);
      else if (!pinning.has(key)) unpinned.push(key);
    }
    const gone = unpinned.filter((key) => order.pinned.includes(key));
    if (gone.length) forgetPinned(gone);
    const listed = new Set(Object.values(order.groups).flatMap((group) => group.keys));
    const left = pinnedNow.filter((key) => listed.has(key));
    if (left.length) forgetInGroups(left, true);
  }, [threadIds, itemsById, order.pinned, order.groups, pinning]);
  // 侧栏里没有这家公司了（删了、我在这家没有对话了）：这一组手排的先后整组去掉（公司列表读到了才算；最近 "" 不算）
  useEffect(() => {
    if (!kind) return;
    const known = new Set(projects.map((project) => project.id));
    const gone = Object.keys(order.groups).filter((group) => group && !known.has(group));
    if (gone.length) forgetGroups(gone);
  }, [kind, projects, order.groups]);
  // 拖动中侧栏被卸下（换产品、退出）：多选整批变淡的标记一并去掉
  useEffect(() => () => void delete document.documentElement.dataset.ccDragBulk, []);

  const searching = !!query;
  const { rows, sections } = useMemo(() => {
    // 选了文件还没发的空对话不列（当前打开的那条照列）；index 仍是组件库 threadIds 里的位置
    const rows = threadIds.flatMap((id, index) => {
      if (unsent.has(id)) return [];
      const item = itemsById.get(id);
      const filing = (item?.remoteId ? filings[item.remoteId] : undefined) ?? filings[id];
      const key = item?.remoteId ?? id;
      return [{
        index,
        title: item?.title || t("untitled"),
        starred: !!item?.custom?.starred || pinning.has(key),
        time: dates[index]?.getTime() ?? Number.MAX_SAFE_INTEGER,
        group: filing?.project ?? "",
        period: filing?.project ? filing.period : "",
        created: createdOf(item?.remoteId),
        key,
      }];
    });
    const sort = { by: view.sort, desc: view.desc };
    const pinnedOrder = order.pinned;
    if (view.by === "month") return { rows, sections: monthSections(rows, projects, { query, sort, pinnedOrder }) };
    if (view.by === "none") return { rows, sections: flatSections(rows, projects, { query, sort, pinnedOrder }) };
    return {
      rows,
      sections: companySections(rows, projects, {
        query, shown, serverMore, sort, empty: view.empty, companyOrder: order.companies, pinnedOrder, groupOrders: order.groups,
      }),
    };
  }, [threadIds, itemsById, dates, unsent, pinning, projects, filings, shown, serverMore, query, view, order]);
  const byCompany = view.by === "company";
  spotRef.current = (key) => {
    const row = rows.find((entry) => entry.key === key);
    if (!row) return null;
    if (row.starred) return "starred";
    const known = new Set(projects.map((project) => project.id));
    const groupOf = (entry: SidebarRow) => (known.has(entry.group) ? entry.group : "");
    const group = groupOf(row);
    const peers = rows.filter((entry) => !entry.starred && groupOf(entry) === group);
    return { group, stamp: groupStamp(peers, { by: view.sort, desc: view.desc }) };
  };

  // 眼前的摆法：拖对话时指针到了顶上、没有「已置顶」就补一个空的（拖进去 = 置顶）；拖进已置顶、还没放下的那一行排在置顶里
  const shownSections = useMemo(() => {
    if (!sections || !byCompany) return sections;
    if (guest) return pinGuest(sections, guest.index, guest.at);
    return reveal ? withPinned(sections) : sections; // 只在拖对话时出（onPointer）
  }, [sections, byCompany, guest, reveal]);

  // 各组行的拖动范围：同一组、同一搜索状态时是同一份（行不跟着整列每次重画），Alt+↑/↓ 按此刻眼前的行算
  const scopeOf = (group: string): GroupScope => {
    const cache = scopes.current;
    if (cache.searching !== searching) cache.map = new Map();
    cache.searching = searching;
    let scope = cache.map.get(group);
    if (!scope) {
      const move = (id: string, delta: -1 | 1) => {
        const { rowIdsOf: idsOf, saveGroup: save } = latestGroups.current;
        return spotIn(save(group, moveBy(idsOf(group), id, delta), [id]), id);
      };
      scope = { mode: "row", group, locked: searching, move };
      cache.map.set(group, scope);
    }
    return scope;
  };
  // 置顶那一列的拖动范围：搜索状态、拖进来的那一行变了才换一份（置顶的行不跟着整列每次重画）；Alt+↑/↓ 按此刻眼前的置顶算
  const pinsNow = useRef<{ ids: string[]; save: (next: string[] | null) => string[] | null }>({ ids: [], save: (next) => next });
  const pinScope = useMemo<RowScope>(
    () => ({
      mode: "pin",
      locked: searching,
      move: (id, delta) => spotIn(pinsNow.current.save(moveBy(pinsNow.current.ids, id, delta)), id),
      guest: guest && guest.group !== undefined ? { local: guest.local, scope: scopeOf(guest.group) } : undefined,
    }),
    // scopeOf 只读 ref 与 searching
    [searching, guest],
  );

  if (!sections || !shownSections) return <NoResults />;
  const local = (index: number) => threadIds[index]!;
  const keyOf = (index: number) => itemsById.get(local(index))?.remoteId ?? local(index);
  const sessionsOf = (indices: readonly number[]) =>
    indices.map((index) => itemsById.get(threadIds[index]!)?.remoteId).filter((id): id is string => !!id);
  // 拖动编号（本页编号）→ 记顺序用的对话编号
  const keyOfDrag = (id: string) => itemsById.get(fromDrag(id))?.remoteId ?? fromDrag(id);

  // 置顶：拖进来还没放下的那一行仍用它在组里的拖动编号（同一项，拖动不断）。拖对话时临时补的已置顶（本来没有）：开着、点了不收
  const pinned = shownSections.find((section) => section.type === "pinned");
  const tempPins = !sections.some((section) => section.type === "pinned");
  const pinIds = (pinned?.indices ?? []).map((index) => (index === guest?.index ? rowDrag(local(index)) : pinDrag(local(index))));
  // 存下新顺序，回同一份（Alt+↑/↓ 据此念新位置；没挪动为 null）
  const savePins = (next: string[] | null) => {
    if (next) setPinnedOrder(next.map(keyOfDrag));
    return next;
  };
  pinsNow.current = { ids: pinIds, save: savePins };
  const companies = shownSections.filter((section) => section.type === "project");
  const companyIds = companies.map((section) => companyDrag(section.project.id));
  const saveCompanies = (next: string[] | null) => {
    if (next) setCompanyOrder(next.map(fromDrag));
    return next;
  };
  // 组里：每组眼前的行（拖动编号）；挪完存这一组手排的先后，at 取这组此刻各行里最新的时间（groupStamp，不用本机时钟）
  const groupSection = (group: string) =>
    shownSections.find((section) => (group ? section.type === "project" && section.project.id === group : section.type === "recents"));
  const rowIdsOf = (group: string) => (groupSection(group)?.indices ?? []).map((index) => rowDrag(local(index)));
  const rowAt = new Map(rows.map((row) => [row.index, row]));
  const stampOf = (group: string) => {
    const section = groupSection(group);
    const indices = section?.type === "project" ? section.all : (section?.indices ?? []);
    return groupStamp(indices.flatMap((index) => rowAt.get(index) ?? ([] as SidebarRow[])), { by: view.sort, desc: view.desc });
  };
  const saveGroup = (group: string, next: string[] | null, moved: readonly string[] = []) => {
    if (next) setGroupOrder(group, next.map(keyOfDrag), stampOf(group), moved.map(keyOfDrag));
    return next;
  };
  latestGroups.current = { rowIdsOf, saveGroup };
  // 侧栏里眼前的先后（多选整批按它排）
  const shownOrder = shownSections.flatMap((section) => section.indices.map(local));
  const batchOf = (active: DragData) => {
    const selected = useSidebarSelection.getState().ids;
    const bulk = (active.count ?? 0) > 1 && selected.includes(fromDrag(active.id));
    return { bulk, locals: bulk ? shownOrder.filter((id) => selected.includes(id)) : [fromDrag(active.id)] };
  };

  // 拖进已置顶放下：拖的这一条（多选 = 整批，按侧栏里的先后）排进落点，存置顶的顺序、从组里手排的先后里去掉，再逐条置顶
  // （不并发，同批量「置顶 N」；服务端回之前先按置顶摆着）
  const pinDropped = (active: DragData, target: DragData) => {
    const { bulk, locals } = batchOf(active);
    const anchor = keyOfDrag(active.id);
    const block = locals.map((id) => itemsById.get(id)?.remoteId ?? id);
    const keys = placeBlock(pinDrop(pinIds, active.id, target).map(keyOfDrag), anchor, block);
    setPinnedOrder(keys);
    forgetInGroups(block);
    if (tempPins && collapsed.has("pinned")) toggleSection({ key: "pinned" }); // 临时补的已置顶：放进去以后开着
    const todo = locals.filter((id) => !itemsById.get(id)?.custom?.starred);
    const waiting = todo.map((id) => itemsById.get(id)?.remoteId ?? id);
    setPinning((set) => new Set([...set, ...waiting]));
    void runInOrder(
      todo,
      async (id) => {
        const item = aui.threads().item({ id });
        const { custom } = item.getState();
        if (!custom?.starred) await item.updateCustom({ ...custom, starred: true });
      },
      (error) => setError(shownError(error)),
    ).finally(() => {
      setPinning((set) => new Set([...set].filter((key) => !waiting.includes(key))));
      if (bulk) clearSelection();
    });
  };
  // 落在本组：拖的这一条挪到落点（多选整批——选中的全在这一组——作为一块排在那里），存这一组手排的先后（落点不因条数上限丢）
  const groupDropped = (active: DragData, target: DragData) => {
    const group = active.group ?? "";
    const next = moveTo(rowIdsOf(group), active.id, target.id);
    if (!next) return;
    const { bulk, locals } = batchOf(active);
    const block = locals.map((id) => itemsById.get(id)?.remoteId ?? id);
    setGroupOrder(group, placeBlock(next.map(keyOfDrag), keyOfDrag(active.id), block), stampOf(group), [keyOfDrag(active.id), ...block]);
    if (bulk) clearSelection();
  };

  const accepts = (active: DragData, target: DragData) => sidebarAccepts(active, target, wholeBatch.current);
  const onDrop = (active: DragData, target: DragData) => {
    if (active.kind === "pin") savePins(moveTo(pinIds, active.id, target.id));
    else if (active.kind === "company") saveCompanies(moveTo(companyIds, active.id, target.id));
    else if (active.kind === "row") {
      if (target.list === PINS) pinDropped(active, target);
      else if (target.kind === "row" && target.list === active.list) groupDropped(active, target);
    }
  };
  // 指针拖着对话经过：进了已置顶（指着置顶的某一行或整块）就排进去，回到自己那一组就挪回去，到了别处（没有落点：别的公司组、
  // 两组之间）也挪回去（松手不置顶）；收着的已置顶只高亮组头
  const onOver = (active: DragData, over: DragData | null, below: boolean) => {
    if (active.kind !== "row") return;
    const now = guestRef.current;
    if (!over) {
      if (now) placeGuest(null);
      return;
    }
    if (over.list === PINS && (over.kind === "pin" || over.zone)) {
      if (now || over.shut) return;
      const local = fromDrag(active.id);
      const index = threadIds.indexOf(local);
      if (index >= 0) placeGuest({ local, index, at: pinSpot(pinIds, over, below), group: active.group });
    } else if (now && over.list === active.list) placeGuest(null);
  };
  // 指针拖着对话、没有已置顶：到了第一组的组头上半截以上就临时出空的已置顶；出来以后指针离开它往下走远了（没排进去时）再收起。
  // 收起后第一组的组头回到原处，指针已在它下面很远，不会来回出、收。按版面位置量（组这一块不滑；组头在整列滑动里，按它的
  // 版面高度算）：滑动中的位置会让刚收起、还在往回滑的组头被当成指针还在它上半截，来回出、收（复刻侧栏实测）
  const onPointer = (active: DragData, y: number) => {
    if (active.kind !== "row" || !byCompany || !tempPins) return;
    const top = first.current?.nextElementSibling;
    if (!top) return;
    const box = top.getBoundingClientRect();
    if (!reveal) {
      const head = (top.firstElementChild as HTMLElement | null)?.offsetHeight ?? box.height;
      if (y < box.top + head / 2) setReveal(true);
    } else if (!guestRef.current && y > box.bottom + box.height) setReveal(false);
  };
  const onActive = (active: DragData | null) => {
    setReveal(false);
    if (!active) placeGuest(null);
    // 多选里拖其中一条：其余选中的行也变淡（整批一起走）；选中的不全在这一组时本组不是落点
    const { bulk, locals } = active?.kind === "row" ? batchOf(active) : { bulk: false, locals: [] };
    const groupOfLocal = new Map(shownSections.flatMap((section) => section.indices.map((index) => [local(index), section.key] as const)));
    wholeBatch.current = !bulk || locals.every((id) => groupOfLocal.get(id) === groupOfLocal.get(fromDrag(active!.id)));
    if (bulk) document.documentElement.dataset.ccDragBulk = "";
    else delete document.documentElement.dataset.ccDragBulk;
  };
  // 拖进已置顶以后取消（Esc、手机 touchcancel 等）：挪回原来那一组、收起临时补的已置顶（还原拿起时的摆法），共用拖动随后从此刻
  // 滑回原位、滑回播完才取消（Esc 那一帧只记下，这一下在下一帧，drag.tsx 的 homing）
  const onReturn = () => {
    placeGuest(null);
    setReveal(false);
  };
  const texts: DragTexts = sb.drag;
  const pinsOpen = tempPins || groupOpen({ key: "pinned" }, collapsed, searching);
  return (
    <DragArea accepts={accepts} onDrop={onDrop} onOver={onOver} onActive={onActive} onReturn={onReturn} onPointer={onPointer} flow={FLOW} texts={texts}>
      <SidebarError />
      <span ref={first} hidden />
      {pinned && (
        <PinnedGroup open={pinsOpen} onToggle={tempPins ? () => undefined : () => toggleSection({ key: "pinned" })}>
          <RowScopeContext.Provider value={pinScope}>
            <DragList id={PINS} items={pinIds}>
              <Items indices={pinned.indices} threadIds={threadIds} />
            </DragList>
          </RowScopeContext.Provider>
        </PinnedGroup>
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
              onMove={(delta) => spotIn(saveCompanies(moveBy(companyIds, companyDrag(section.project.id), delta)), companyDrag(section.project.id))}
              more={section.more ? () => void showMore(section.project.id, section.all.length, () => aui.threads().loadMore()) : undefined}
            >
              <RowScopeContext.Provider value={scopeOf(section.project.id)}>
                <DragList id={rowList(section.project.id)} items={rowIdsOf(section.project.id)}>
                  <Items indices={section.indices} threadIds={threadIds} />
                </DragList>
              </RowScopeContext.Provider>
            </CompanyGroup>
          ) : null,
        )}
      </DragList>
      {shownSections.map((section) => {
        if (section.type === "pinned" || section.type === "project") return null;
        const open = groupOpen({ key: section.key }, collapsed, searching);
        const toggle = () => toggleSection({ key: section.key });
        if (section.type === "recents") {
          return byCompany ? (
            <RecentsGroup key={section.key} open={open} onToggle={toggle} onNew={() => newChatIn("")}>
              <RowScopeContext.Provider value={scopeOf("")}>
                <DragList id={rowList("")} items={rowIdsOf("")}>
                  <Items indices={section.indices} threadIds={threadIds} />
                </DragList>
              </RowScopeContext.Provider>
            </RecentsGroup>
          ) : (
            <SectionGroup key={section.key} label={t("recents")} open={open} onToggle={toggle} onNew={() => newChatIn("")}>
              <Items indices={section.indices} threadIds={threadIds} />
            </SectionGroup>
          );
        }
        return (
          <SectionGroup key={section.key} label={section.period ? monthLabel(section.period) : sb.noMonth} open={open} onToggle={toggle}>
            <Items indices={section.indices} threadIds={threadIds} />
          </SectionGroup>
        );
      })}
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

/** 一组（标题 + 这组的行）包成一块：公司组整块拖动、对话拖进已置顶整块高亮都按这一块算。 */
const SectionGroup = forwardRef<
  HTMLDivElement,
  { label: string; open: boolean; onToggle: () => void; onNew?: () => void; children?: ReactNode } & Record<`data-${string}`, string | undefined>
>(({ label, open, onToggle, onNew, children, ...rest }, ref) => (
  <div ref={ref} {...rest} className="cc-group-section" data-slot="cc-group-section">
    <SectionHeader label={label} open={open} onToggle={onToggle} onNew={onNew} />
    {open && children && <div className="cc-group-items">{children}</div>}
  </div>
));
SectionGroup.displayName = "SectionGroup";

// 拖进另一列时整列一起滑的各项（共用拖动的 flowLayout）：行、组头、「显示更多」
const FLOW = '[data-slot="aui_thread-list-item"], .cc-group-row, .cc-show-more';

/** 已置顶：置顶的对话在这里排先后；整块是对话拖进置顶的落点（收着时组头高亮、落到组头并收起）。 */
const PinnedGroup: FC<{ open: boolean; onToggle: () => void; children?: ReactNode }> = ({ open, onToggle, children }) => {
  const zone = useDropZone({ id: zoneDrag(PINS), kind: "pins", label: t("starred"), list: PINS }, !open);
  return (
    <SectionGroup ref={zone.ref} data-drop-target={zone["data-drop-target"]} label={t("starred")} open={open} onToggle={onToggle}>
      {children}
    </SectionGroup>
  );
};

/** 按公司分组时的「最近」：没挂公司的对话，在这里排先后；整块是拖进置顶以后挪回这一组的落点。 */
const RecentsGroup: FC<{ open: boolean; onToggle: () => void; onNew: () => void; children?: ReactNode }> = ({ open, onToggle, onNew, children }) => {
  const zone = useDropZone({ id: zoneDrag(rowList("")), kind: "group", label: t("recents"), list: rowList("") }, !open);
  return (
    <SectionGroup ref={zone.ref} label={t("recents")} open={open} onToggle={onToggle} onNew={onNew}>
      {children}
    </SectionGroup>
  );
};

/** 一家公司的组：组头开关是这一组的拖动把手（整组跟着走），这组的行在组里排先后；整块也是拖进置顶以后挪回这一组的落点。 */
const CompanyGroup: FC<{
  project: ProjectRow;
  open: boolean;
  onToggle: () => void;
  sessions: readonly string[];
  locked: boolean;
  onMove: (delta: -1 | 1) => MoveSpot | null;
  more?: () => void;
  children: ReactNode;
}> = ({ project, open, onToggle, sessions, locked, onMove, more, children }) => {
  const drag = useSortableItem(
    { id: companyDrag(project.id), kind: "company", label: project.title, list: "companies", group: project.id },
    { disabled: locked, ownFocus: true },
  );
  const zone = useDropZone({ id: zoneDrag(rowList(project.id)), kind: "group", label: project.title, list: rowList(project.id) }, !open);
  const { ref: dragRef, ...node } = drag.node;
  // 组头记住（memo）：拖动库重画这一组时，把手、开关、挪一格都还是同一份就不重画组头
  const latestMove = useRef(onMove);
  latestMove.current = onMove;
  // 只依赖播报（drag.moved 按名字记住）：整份 drag 在拖动中随位移、过渡变，依赖它组头的 onMove 也跟着变（审查）
  const said = drag.moved;
  const move = useCallback((delta: -1 | 1) => said(latestMove.current(delta)), [said]);
  // 开关、这组的对话也给稳定的一份：侧栏整列重画（拖进已置顶、挪回原组、落定存顺序）时上面每次都是新的函数、新的数组，组头
  // （memo）原来跟着全部重画（第 11 轮 B14：落定、Esc 下一帧的整列重画里一半是 14 个组头）
  const latestToggle = useRef(onToggle);
  latestToggle.current = onToggle;
  const toggle = useCallback(() => latestToggle.current(), []);
  const sessionsKey = sessions.join("\n");
  const shownSessions = useMemo(() => sessions, [sessionsKey]);
  const ref = useCallback(
    (el: HTMLElement | null) => {
      dragRef(el);
      zone.ref(el);
    },
    [dragRef, zone.ref],
  );
  return (
    <div {...node} ref={ref} className="cc-group-section" data-slot="cc-group-section">
      <ProjectHeader project={project} open={open} onToggle={toggle} sessions={shownSessions} handle={drag.handle} onMove={locked ? undefined : move} />
      {open && (
        <div data-slot="cc-project-items" className="cc-group-items">
          {children}
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

// 不按公司分组的产品（ERP / DMS）同样在列表上面显示侧栏出错行（置顶 / 取消置顶失败等，toggleStar；审查：原来只有按公司分组的侧栏有）
const DayGroups: FC<{ searchQuery?: string }> = (props) => (
  <>
    <SidebarError />
    <DayRows {...props} />
  </>
);

const DayRows: FC<{ searchQuery?: string }> = ({
  searchQuery = "",
}) => {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const bumps = useBumps();
  const unsent = useUnsent();
  const { isMobile } = useSidebar();
  const { collapsed, toggle } = useCollapsedGroups();

  const query = searchQuery.trim().toLowerCase();

  const { filteredIndices, groups } = useMemo(() => {
    const itemsById = new Map(threadItems.map((item) => [item.id, item]));
    // 最后活动时间取列表给的与本页收到提醒、新消息的时间里较晚的一个：有动静的对话浮到最上面
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
          !unsent.has(id) &&
          (!query ||
            (itemsById.get(id)?.title || t("untitled"))
              .toLowerCase()
              .includes(query)),
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
  }, [threadIds, threadItems, bumps, unsent, query, isMobile]);

  if (query && filteredIndices.length === 0) {
    return (
      <div data-slot="aui_thread-list-empty" className="cc-empty-line">
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
    // 电脑上同 Cowork 的组标题（sidebar-group.tsx 的 GroupHeader：带 32、开关 24、圆角 8，组上间距在带外；按钮排查 M6）
    return (
      <Fragment key={group.key}>
        <GroupHeader label={group.label} open={open} onToggle={() => toggle(group.key)} />
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
          {/* 28 的槽里放 20 的圆，加号图标框 16（同 Claude 侧栏，按钮排查 M5） */}
          <span className="cc-nav-icon">
            <span className="cc-new-dot">
              <PlusIcon data-slot="aui_thread-list-new-icon" />
            </span>
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
    <div className="flex flex-col gap-(--cc-space-2)">
      {Array.from({ length: 5 }, (_, i) => (
        <div
          key={i}
          role="status"
          aria-label={t("search")}
          data-slot="aui_thread-list-skeleton-wrapper"
          className="flex h-(--cc-row-h) items-center px-(--cc-row-pad)"
        >
          <Skeleton data-slot="aui_thread-list-skeleton" className="w-full" />
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
// 在置顶组里可拖着排先后，按公司分组时在公司组 / 最近里可在本组排先后或拖进已置顶（RowScopeContext，共用拖动）；ERP / DMS 与
// 按月份、不分组时的其余行不能拖
export const ThreadListItem: FC = () => {
  const scope = useContext(RowScopeContext);
  const localId = useAuiState((s) => s.threadListItem.id);
  if (scope?.mode === "pin") return scope.guest?.local === localId ? <GroupRow scope={scope.guest.scope} /> : <PinnedRow scope={scope} />;
  if (scope?.mode === "row") return <GroupRow scope={scope} />;
  return <Row />;
};

// 侧栏各组的行用的同一份（Items 记住时组件表不每次新建）
const ITEM_COMPONENTS = { ThreadListItem };

// 从行尾「···」、菜单、改名输入框按下不拿起
const ROW_SKIP = '[data-slot="aui_thread-list-item-more"], [role="menu"], input';

const PinnedRow: FC<{ scope: Extract<RowScope, { mode: "pin" }> }> = ({ scope }) => {
  const localId = useAuiState((s) => s.threadListItem.id);
  const title = useAuiState((s) => s.threadListItem.title) || t("untitled");
  const id = pinDrag(localId);
  const drag = useSortableItem({ id, kind: "pin", label: title, list: PINS }, { disabled: scope.locked, ownFocus: true, skip: ROW_SKIP });
  // Alt+↑/↓ 挪完念新位置（同键盘拖动的播报）；只依赖播报（drag.moved），不随拖动中的位移变
  const said = drag.moved;
  const onMove = useCallback((delta: -1 | 1) => said(scope.move(id, delta)), [said, scope, id]);
  return <Row drag={drag} onMove={scope.locked ? undefined : onMove} />;
};

/** 公司组 / 最近里的一行：在本组里排先后，或拖进已置顶（拖进去还没放下时照旧是这一项，挂在置顶里）。 */
const GroupRow: FC<{ scope: GroupScope }> = ({ scope }) => {
  const localId = useAuiState((s) => s.threadListItem.id);
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const title = useAuiState((s) => s.threadListItem.title) || t("untitled");
  // 多选里的一条：整批一起拖（浮层上写条数）
  const count = useSidebarSelection((s) => (s.ids.length > 1 && s.ids.includes(localId) ? s.ids.length : 0));
  const id = rowDrag(localId);
  const locked = scope.locked || !remoteId;
  const drag = useSortableItem(
    { id, kind: "row", label: count ? sb.drag.items(count) : title, list: rowList(scope.group), group: scope.group, count: count || undefined, session: remoteId },
    { disabled: locked, ownFocus: true, skip: ROW_SKIP },
  );
  const said = drag.moved;
  const onMove = useCallback((delta: -1 | 1) => said(scope.move(id, delta)), [said, scope, id]);
  return <Row drag={drag} onMove={locked ? undefined : onMove} />;
};

type RowDrag = { node: { ref: (el: HTMLElement | null) => void } & Record<string, unknown>; handle: DragHandle };

// 记住（memo）：省下的是行里的字（RowLabel）、行菜单（ThreadListItemMore）与 Marquee 子树——它们的属性在拖动中不变。行本身在拿起、
// 落定时仍会全部重画（审查实测）：拖动库一开始排序，每一项的 transition 就从无变成一段字符串，useSortableItem 回的 drag 跟着变；
// 不在拖动时（实时推送、列表同步重画侧栏）位移、把手、onMove 都是同一份，行不重画
const Row = memo(function Row({ drag, onMove }: { drag?: RowDrag; onMove?: (delta: -1 | 1) => void }) {
  const [renaming, setRenaming] = useState(false);
  // 长按（手机）开的菜单不按模态开（useLongPressMenu：出菜单那一帧不再整页重算样式）
  const { open: menuOpen, setOpen: setMenuOpen, modal: menuModal, press } = useLongPressMenu();
  const startRename = useCallback(() => setRenaming(true), []);
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
        // 键盘拖动中：键只归拖动（方向键由把手标成已处理，组件库行自带的 ↑ ↓ 不移焦点；不开菜单）
        if (isDragging()) return dragKey?.(e);
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
            <RowLabel />
          </ThreadListItemPrimitive.Trigger>
          <ThreadListItemMore open={menuOpen} modal={menuModal} onOpenChange={setMenuOpen} onRename={startRename} />
        </>
      )}
    </ThreadListItemPrimitive.Root>
  );
});

/** 行里的字（标题、第二行、行尾状态）：不随拖动变（拖动库每次拿起、经过、放下都重画所有能拖的行，只有行的位移与把手的属性变），
 *  单独记住，不跟着整行重画（测试窗口第 10 轮 B 段：拿起那一刻整列重画 150–200ms）。里面各自按组件库的状态更新。 */
const RowLabel = memo(function RowLabel() {
  return (
    <>
      <span className="cc-row-text">
        <Marquee data-slot="aui_thread-list-item-title" className="cc-row-title">
          <ThreadListItemPrimitive.Title fallback={t("untitled")} />
        </Marquee>
        <RowMeta />
      </span>
      <span className="cc-row-end">
        <ThreadActivity />
      </span>
    </>
  );
});

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

// 对话菜单（同 Claude Code）：置顶 P / 重命名 R / 删除 D，向下开、右对齐（用户 10-17：对话不能换公司，「移到 ▸」已删）。这一行在
// 多选里、选了两条以上时换成批量项（BulkItems）。
// 记住：拖动时整行重画不连带菜单（开关、改名都是稳定的回调）
const ThreadListItemMore = memo(function ThreadListItemMore({
  open,
  modal = true,
  onOpenChange,
  onRename,
}: {
  open: boolean;
  /** 长按开的不按模态（useLongPressMenu） */
  modal?: boolean;
  onOpenChange: (open: boolean) => void;
  onRename: () => void;
}) {
  const aui = useAui();
  const custom = useAuiState((s) => s.threadListItem.custom);
  const localId = useAuiState((s) => s.threadListItem.id);
  const selection = useSidebarSelection((s) => s.ids);
  const starred = !!custom?.starred;
  const companies = !!useProjects((s) => s.kind);
  const bulk = companies && selection.length > 1 && selection.includes(localId);
  const close = () => onOpenChange(false);
  // 取消置顶回到它那一组的最上面（starChat）；失败写进侧栏出错行
  const pin = () => toggleStar(aui.threadListItem(), !starred);
  const remove = () => deleteChat(aui.threadListItem());
  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange} modal={modal}>
      <DropdownMenu.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          tabIndex={-1}
          data-slot="aui_thread-list-item-more"
          className="data-[state=open]:bg-hover absolute end-(--cc-space-4) top-1/2 size-(--cc-row-ctl) -translate-y-1/2 rounded-sm p-0 opacity-0 group-hover/item:opacity-100 group-has-focus-visible/item:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
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
            sideOffset={menuOffset()}
            data-slot="aui_thread-list-item-more-content"
            className="cc-menu min-w-(--cc-menu-w-md)"
            onKeyDown={menuLetterKeys({ KeyP: pin, KeyR: onRename, KeyD: remove }, close)}
          >
            <DropdownMenu.Item className="cc-menu-item" onSelect={pin}>
              {starred ? <PinOffIcon /> : <PinIcon />}
              <span className="flex-1">{starred ? t("unstar") : t("star")}</span>
              <MenuKey letter="P" />
            </DropdownMenu.Item>
            <DropdownMenu.Item className="cc-menu-item" onSelect={onRename}>
              <PencilIcon />
              <span className="flex-1">{t("rename")}</span>
              <MenuKey letter="R" />
            </DropdownMenu.Item>
            <DropdownMenu.Item data-slot="aui_thread-list-item-more-item" data-danger="" className="cc-menu-item" onSelect={remove}>
              <TrashIcon />
              <span className="flex-1">{t("delete")}</span>
              <MenuKey letter="D" />
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        )}
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
});

/** 多选时的行菜单（同 Claude Code，没有批量栏）：「置顶 N」（都已置顶时「取消置顶 N」）、「删除 N」。
 *  用组件库各行自己的操作一条一条做（不并发，lib/sidebar-keys.ts 的 runInOrder），做完清空多选；出错显示在侧栏那一行。
 *  只做列表里还在的（别处刚删掉的不再去取；看不见的行由 sidebar-keys 从选中里去掉）。 */
const BulkItems: FC<{ ids: readonly string[]; close: () => void }> = ({ ids: selected, close }) => {
  const aui = useAui();
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const { ids, allPinned } = useMemo(() => {
    const rows = selected.flatMap((id) => threadItems.filter((item) => item.id === id).slice(0, 1));
    return { ids: rows.map((row) => row.id), allPinned: rows.every((row) => !!row.custom?.starred) };
  }, [selected, threadItems]);
  const report = (error: unknown) => setError(shownError(error));
  const pinAll = () => {
    // 一起取消置顶的按侧栏里眼前的先后回到组顶：逐条做，后一条排在同批已经回来的那几条后面（starChat 的 after）
    const shown = allPinned ? rowOrder(document.querySelector('[data-slot="aui_thread-list-root"]')) : [];
    const ordered = allPinned ? [...ids].sort((a, b) => shown.indexOf(a) - shown.indexOf(b)) : ids;
    const back: string[] = [];
    void runInOrder(ordered, async (id) => {
      const item = aui.threads().item({ id });
      const { custom, remoteId } = item.getState();
      if (!!custom?.starred === !allPinned) return;
      await starChat(item, !allPinned, [...back]);
      back.push(remoteId ?? id);
    }, report).then(clearSelection);
  };
  const deleteAll = () =>
    askDelete(ids, () => runInOrder(ids, async (id) => await aui.threads().item({ id }).delete(), report).then(clearSelection));
  return (
    <DropdownMenu.Content
      side="bottom"
      align="end"
      sideOffset={menuOffset()}
      data-slot="aui_thread-list-item-more-content"
      className="cc-menu min-w-(--cc-menu-w-md)"
      onKeyDown={menuLetterKeys({ KeyP: pinAll, KeyD: deleteAll }, close)}
    >
      <DropdownMenu.Item className="cc-menu-item" onSelect={pinAll}>
        {allPinned ? <PinOffIcon /> : <PinIcon />}
        <span className="flex-1">{allPinned ? sb.unpinN(ids.length) : sb.pinN(ids.length)}</span>
        <MenuKey letter="P" />
      </DropdownMenu.Item>
      <DropdownMenu.Item data-danger="" className="cc-menu-item" onSelect={deleteAll}>
        <TrashIcon />
        <span className="flex-1">{sb.deleteN(ids.length)}</span>
        <MenuKey letter="D" />
      </DropdownMenu.Item>
    </DropdownMenu.Content>
  );
};
