// 按公司分组的界面零件（lib/managed-agents/projects.ts；只在产品有公司分组时出现（Cowork、ERP），DMS 不出现）：
//
//   ProjectHeader       — 一家公司的组头（sidebar-group.tsx 的 GroupHeader，设计文档 3.4）：最左公司图标、公司名（同会话行标题
//                         字号字色），整行折叠 / 展开，下面这家的对话行缩进到公司名起点；右侧「···」（分组和排序，用户 2026-10-15
//                         夜：打开公司、在这家新建对话不要了）、「＋」（在这家开新对话，月份 = 上个月）；手机上长按组头、键盘菜单键出
//                         「···」菜单；收起时组里有在跑或没看过的，名字后一个点。组头开关同时是拖动把手（thread-list.tsx 的公司组排序，
//                         共用拖动）。组头不再有待处理数（用户 11-04：左侧待处理不要，并进对话）
//   PendingPlaceholder  — 待处理并进对话（用户 11-04）：这家这个月有要处理的单、本人还没有这个月的对话时，组里一行「{月份}」，
//                         行尾一个提醒点（同对话行的提醒圆点，不新造标记）；点了在这家这个月开新对话（不在后台建、不开模型轮），
//                         对话末尾就是「需要处理」框（pending.tsx 的 PendingInChat）。侧栏还没有这家的组、归不上公司的（「未归属
//                         公司」）放在列表最上面（SidebarError 那一块）
//   ComposerChin        — 输入框下巴（thread.tsx 的 Chin 插槽，设计文档 3.12）：公司、月份、分店三个幽灵按钮。新对话里「公司 ▾」选这段
//                         对话挂哪家（用户 11-07：点了弹选择公司窗口，company-picker.tsx；有公司分组的产品一直显示）；已有对话里点公司名打开右侧
//                         会话栏的公司卡（同顶栏，lib/company-panel 的 openCompanyCard），月份、分店改了就 move。已有对话不能换公司
//                         （用户 10-17：「移到」已删，没挂公司的已有对话下巴里也不再有「公司 ▾」）
//   ConversationCompany — 对话顶栏「ABC 有限公司 ›」（点了打开右侧会话栏的公司卡）；手机在胶囊菜单最上面（MobileCompanyItem）
//   MobileChatTitle     — 手机对话页顶栏中间的两行标题：对话名、公司名（挂在公司下时；照 Claude 手机端 8.webp，不可点）
//
// 公司组里不再有「⏱ 定时任务 N」一行（用户 2026-10-15 夜同意设计文档 4.2：改由右侧栏「定时任务」卡与公司页「定时任务」标签承担）。
// 删公司在主档页的「⋯」里，不确认；出错时在侧栏显示一行（服务端给的原文）。颜色、圆角只用 globals.css 的变量。

import { useAui, useAuiState } from "@assistant-ui/react";
import { Building2Icon, CalendarIcon, ChevronDownIcon, ChevronRightIcon, StoreIcon, XIcon } from "lucide-react";
import { DropdownMenu, SubMenu } from "@/components/ui/menu";
import { type FC, type HTMLAttributes, memo, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { openCompanyPicker } from "@/components/company-picker";
import { BranchChip, BranchChoices, PeriodChip, PeriodChoices } from "@/components/period-chip";
import { GroupHeader, GroupNew, GroupSortMenu, useCloseDrawer } from "@/components/assistant-ui/sidebar-group";
import type { DragHandle } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";
import { useSidebar } from "@/components/ui/sidebar";
import { useLongPressMenu } from "@/hooks/use-long-press";
import { co, t } from "@/i18n";
import {
  branchShort,
  cachedDetail,
  lastMonth,
  monthLabel,
  type PendingMonth,
  placeholderTitle,
  useBranches,
  useCompanyNav,
  usePendingCounts,
} from "@/lib/companies";
import { openCompanyCard } from "@/lib/company-panel";
import { type Activity, useGroupActivity } from "@/lib/managed-agents/activity";
import {
  type Filing,
  fileSession,
  filingOf,
  hintTitle,
  NO_FILING,
  type ProjectRow,
  setDraft,
  setError,
  useProjects,
} from "@/lib/managed-agents/projects";
import { useModePlace, useView } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { shownError } from "@/lib/frappe";
import { withReport } from "../../lib/caught";

const report = (error: unknown) => setError(shownError(error));

export const COMPOSER_INPUT = "textarea.aui-composer-input";

/** 新建对话后光标直接进输入框（同 Claude；侧栏组头「＋」、顶部「新建」、公司页、手机都一样）。点击当下先聚焦一次
 *  （手机上只有在点击当下聚焦才弹键盘），新对话换上来后的下两帧再聚焦一次（输入框可能换了一个）。 */
export function focusComposer(now = true) {
  const focus = () => document.querySelector<HTMLTextAreaElement>(COMPOSER_INPUT)?.focus({ preventScroll: true });
  if (now) focus();
  requestAnimationFrame(() => requestAnimationFrame(focus));
}

/** 在某家公司（NO_FILING = 最近）开新对话：回到对话页、切到新对话，光标进输入框；text 预填进输入框，send 才发出（交给 CC、草稿不发）。
 *  代录模式下留在代录（新建页就是直写的新建页，用户 11-04），不切回对话模式。 */
export function useStartIn() {
  const aui = useAui();
  const { setView } = useView();
  return (filing: Partial<Filing>, text = "", send = false, files: readonly File[] = []) => {
    setError("");
    setDraft(filing);
    setView(useModePlace.getState().mode === "operator" ? "operator" : "chat");
    void aui.threads().switchToNewThread();
    if (!text && !files.length) {
      if (!send) focusComposer();
      return;
    }
    // 新对话切过去后下一帧才是当前对话，再把文字放进输入框（同 window.tomo.ask）；带着的文件同拖进输入框（lib/desktop.ts），
    // 上传、进度、出错都由新对话的输入框显示，不替用户发出
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const composer = aui.thread().composer();
        if (text) composer.setText(text);
        for (const file of files) void composer.addAttachment(file).catch(withReport(() => undefined, "projects"));
        if (send && !files.length) composer.send();
        else focusComposer(false);
      }),
    );
  };
}

/** 在某家公司开新对话，月份默认上个月（服务端不收月份的产品不带）。 */
export function useNewChatIn() {
  const start = useStartIn();
  return (project: string) => {
    const { period } = useProjects.getState();
    start(project ? { project, period: period ? lastMonth() : "" } : NO_FILING);
  };
}

/** 打开一家公司：对话模式进公司页；代录模式进这家的新建页（同组头「＋」：直写的新建页、公司选好、月份上个月）。title：公司显示名。 */
export function useOpenCompany() {
  const { setView } = useView();
  const start = useStartIn();
  return (company: string, title = "") => {
    if (useModePlace.getState().mode === "operator") {
      if (title) hintTitle(company, title);
      start({ project: company, period: useProjects.getState().period ? lastMonth() : "" });
      return;
    }
    useCompanyNav.setState({ company });
    setView("company");
  };
}

// ---------------------------------------------------------------- 组头
// 记住（memo）：公司组头是拖动项，拖动库每次拿起、经过、放下都重画所有组头；把手（useSortableItem 记住的）、开关、挪一格没变就不重画
export const ProjectHeader = memo(function ProjectHeader({
  project,
  open,
  onToggle,
  sessions,
  handle,
  onMove,
}: {
  project: ProjectRow;
  open: boolean;
  onToggle: () => void;
  sessions: readonly string[];
  /** 组头开关同时是拖动把手（公司组排序）；Alt+↑/↓ 挪一格。 */
  handle?: DragHandle;
  onMove?: (delta: -1 | 1) => void;
}) {
  // 长按（手机）开的菜单不按模态开（useLongPressMenu：出菜单那一帧不再整页重算样式）
  const { open: menuOpen, setOpen: setMenuOpen, modal: menuModal, press } = useLongPressMenu();
  const { isMobile } = useSidebar();
  const chats = useGroupActivity(sessions);
  const waiting = usePendingCounts((s) => placeholderMonths(s.months, project.id).length > 0);
  // 收起时名字后的点：组里有在跑、没看过的对话按它们；只有待处理的占位行时是提醒点（同占位行行尾的点）
  const activity: Activity | undefined = chats ?? (waiting ? "notice" : undefined);
  const newChatIn = useNewChatIn();
  // 「新建」给一个稳定的回调：组头随拖动重画时它不跟着重画（GroupNew 是 memo）
  const newChat = useRef(newChatIn);
  newChat.current = newChatIn;
  const onNew = useCallback(() => newChat.current(project.id), [project.id]);
  return (
    <>
    <CompanyHeaderView
      {...(isMobile ? press : {})}
      project={project}
      open={open}
      onToggle={onToggle}
      handle={handle}
      onMove={onMove}
      activity={activity}
      menuOpen={menuOpen}
      onMenuKey={() => setMenuOpen(true)}
      onContextMenu={isMobile ? (e) => e.preventDefault() : undefined}
    >
      <GroupSortMenu open={menuOpen} modal={menuModal} onOpenChange={setMenuOpen} alignHead={isMobile} />
      <GroupNew onClick={onNew} />
    </CompanyHeaderView>
    </>
  );
});

// ---------------------------------------------------------------- 待处理的占位行
/** 还没有对话的「公司 · 月份」（company 空 = 归不上公司）。 */
export function placeholderMonths(months: readonly PendingMonth[] | null, company: string): PendingMonth[] {
  return (months ?? []).filter((row) => row.company === company && !row.conversation);
}

/** 侧栏要列的占位行（所有还没有对话的「公司 · 月份」）与侧栏里还没有组的公司（占位行要一个组放：编号 → 公司名）。
 *  侧栏三处 11-07：占位行同对话行一样归组（lib/managed-agents/sidebar-groups.ts），直写、代录同一份。 */
export function usePlaceholders(): { rows: PendingMonth[]; extra: ProjectRow[] } {
  const months = usePendingCounts((s) => s.months);
  const listed = useProjects((s) => s.projects);
  const rows = useMemo(() => (months ?? []).filter((row) => !row.conversation), [months]);
  const missing = useMemo(
    () => [...new Set(rows.map((row) => row.company).filter((id) => id && !listed.some((p) => p.id === id)))],
    [rows, listed],
  );
  const names = useCompanyTitles(missing);
  const extra = useMemo(() => missing.map((id) => ({ id, title: names[id] ?? "", count: 0, last_active: "" })), [missing, names]);
  return { rows, extra };
}

// map: pending-in-chat
/** 一行占位（同对话行的样子：同一个行容器、同样缩进，标题按内容起名，行尾提醒点）：点了在这家这个月开新对话，末尾是
 *  「需要处理」框。 */
const PendingPlaceholder: FC<{ row: PendingMonth }> = ({ row }) => {
  const start = useStartIn();
  const go = useCloseDrawer();
  const withPeriod = useProjects((s) => s.period);
  const title = placeholderTitle(row);
  const company = useCompanyTitle(row.company);
  const open = () =>
    go(() => {
      if (row.company && company) hintTitle(row.company, company); // 侧栏里还没有这家的组时先按公司名搭出来
      start(row.company ? { project: row.company, period: withPeriod ? row.month : "" } : NO_FILING);
    });
  return (
    <div
      className="cc-thread-row group/item"
      data-slot="cc-pending-placeholder"
      data-marquee-host=""
      data-company={row.company || undefined}
      data-month={row.month || undefined}
    >
      <button
        type="button"
        data-roving=""
        data-slot="cc-pending-placeholder-trigger"
        onClick={open}
        className="cc-row-trigger focus-visible:shadow-focus flex min-h-(--cc-row-h) min-w-0 flex-1 items-center self-stretch rounded-md text-start outline-none"
      >
        <span className="cc-row-text">
          <Marquee className="cc-row-title">{title}</Marquee>
        </span>
        <span className="cc-row-end">
          <span className="cc-activity" data-activity="notice" role="img" aria-label={co.pendingDocs(row.count)} />
        </span>
      </button>
    </div>
  );
};

/** 一组里的占位行：放在这组的行容器里、对话行前面（thread-list.tsx 的各组）。 */
export const PendingRows: FC<{ rows?: readonly PendingMonth[] }> = ({ rows }) =>
  rows?.length ? rows.map((row) => <PendingPlaceholder key={`${row.company}:${row.month}`} row={row} />) : null;

/** 公司组头的样子（对话侧栏 ProjectHeader；代录模式用的也是这一个侧栏）：最左公司图标、公司名
 *  （同会话行标题的字号字色），整行折叠 / 展开；收起时组里有在跑或没看过的，名字后一个点；右侧操作（待处理数、···、＋）由调用方给。
 *  菜单开着时 data-open 让「＋」照样露着。 */
export function CompanyHeaderView({
  project,
  open,
  onToggle,
  handle,
  onMove,
  activity,
  menuOpen = false,
  onMenuKey,
  children,
  ...rest
}: {
  project: ProjectRow;
  open: boolean;
  onToggle: () => void;
  handle?: DragHandle;
  onMove?: (delta: -1 | 1) => void;
  activity?: Activity;
  menuOpen?: boolean;
  onMenuKey?: () => void;
  children?: ReactNode;
} & Omit<HTMLAttributes<HTMLDivElement>, "title" | "children">) {
  return (
    <GroupHeader
      {...rest}
      data-slot="cc-project"
      data-open={menuOpen || undefined}
      className="group/project cc-project-row"
      icon={<Building2Icon className="cc-group-icon" aria-hidden />}
      label={project.title}
      title={project.title}
      open={open}
      onToggle={onToggle}
      onMenuKey={onMenuKey}
      handle={handle}
      onMove={onMove}
      dot={
        !open && activity ? (
          <span
            className="cc-activity cc-group-dot"
            data-activity={activity}
            aria-label={activity === "running" ? t("running") : activity === "notice" ? t("newNotice") : t("unread")}
          />
        ) : undefined
      }
    >
      {children}
    </GroupHeader>
  );
}

/** 侧栏列表最上面：出错提示（服务端给的原文，可关掉）。 */
export const SidebarError: FC = () => {
  const error = useProjects((s) => s.error);
  return <SidebarErrorLine error={error} onClose={() => setError("")} />;
};

/** 侧栏出错行的样子（对话侧栏、实操的任务侧栏同一个）：原文 + 关掉。 */
export const SidebarErrorLine: FC<{ error: string; onClose: () => void }> = ({ error, onClose }) => {
  if (!error) return null;
  return (
    <div role="alert" className="cc-sidebar-error">
      <span className="min-w-0 flex-1">{error}</span>
      <button type="button" className="cc-row-btn cc-tap" aria-label={t("close")} onClick={onClose}>
        <XIcon />
      </button>
    </div>
  );
};

// ---------------------------------------------------------------- 选公司（新对话的公司条）
/** 记住选过的公司名（弹窗里选的、刚建的公司不在侧栏里）。 */
const titles = new Map<string, string>();

// ---------------------------------------------------------------- 输入框上方的公司条

/** 几家公司的名称（侧栏里没有的，按公司详情取一次、记住）：编号 → 名称，还没取到的不在里面。 */
function useCompanyTitles(ids: readonly string[]): Readonly<Record<string, string>> {
  const [found, setFound] = useState<Record<string, string>>({});
  const key = ids.join("\n");
  useEffect(() => {
    let alive = true;
    const known = Object.fromEntries(ids.filter((id) => titles.has(id)).map((id) => [id, titles.get(id)!]));
    setFound(known);
    for (const id of ids.filter((id) => !titles.has(id))) {
      void cachedDetail(id).then((d) => {
        if (!alive || !d) return;
        titles.set(id, d.company.title);
        setFound((now) => ({ ...now, [id]: d.company.title }));
      });
    }
    return () => {
      alive = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps -- 按编号列表变了才重取
  return found;
}

function useCompanyTitle(id: string): string {
  const fromSidebar = useProjects((s) => s.projects.find((p) => p.id === id)?.title);
  const [title, setTitle] = useState(titles.get(id) ?? "");
  useEffect(() => {
    if (!id || fromSidebar || titles.has(id)) return setTitle(titles.get(id) ?? "");
    let alive = true;
    void cachedDetail(id).then((d) => {
      if (!alive || !d) return;
      titles.set(id, d.company.title);
      setTitle(d.company.title);
    });
    return () => {
      alive = false;
    };
  }, [id, fromSidebar]);
  return fromSidebar ?? title;
}

/** 这段对话还没建（新对话；代录模式的新建页也是它）。 */
function useFreshThread(): boolean {
  return useAuiState((s) => s.threadListItem.status === "new");
}

/** 输入框下巴：DMS（没有公司分组）不出现；新对话「公司 ▾」+ 月份 + 分店（草稿）；已有对话「公司」+ 月份 + 分店（改了就 move）。
 *  手机没有下巴（用户 10-17：基础信息放上面，同线上旧版）：新对话放在问候下面（WelcomeFiling），已有对话在右上「…」里
 *  （公司名、处理月份、分店）。 */
export const ComposerChin: FC = () => {
  const kind = useProjects((s) => s.kind);
  const fresh = useFreshThread();
  const { isMobile } = useSidebar();
  if (!kind || isMobile) return null;
  return fresh ? <DraftChin /> : <SessionChin />;
};

/** 手机新对话：问候下面一行「公司 ▾」+ 月份 + 分店（同线上旧版的公司条），菜单向下开；电脑在下巴里。 */
export const WelcomeFiling: FC = () => {
  const kind = useProjects((s) => s.kind);
  const fresh = useFreshThread();
  const { isMobile } = useSidebar();
  if (!kind || !fresh || !isMobile) return null;
  return (
    <div className="cc-welcome-filing" data-slot="cc-welcome-filing">
      <DraftChin side="bottom" />
    </div>
  );
};

/** 新的空白对话（还没建会话）：「公司 ▾」点了弹选择公司窗口（company-picker.tsx：连接里读到的全部账套公司，选了就在这段对话里
 *  用它，没建过的顺手建），选了公司后接月份与分店。有公司分组的产品（Cowork、ERP）一直显示：一家都没有时弹窗里给「连接」。
 *  月份、分店的菜单默认向上开（下巴），手机问候下面那一行向下开。 */
const DraftChin: FC<{ side?: "top" | "bottom" }> = ({ side = "top" }) => {
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  const draft = useProjects((s) => s.draft);
  const title = useCompanyTitle(draft.project);
  const branches = useBranches(withScope ? draft.project : "");
  const pick = (project: string, label: string) => {
    if (project && label) {
      titles.set(project, label);
      hintTitle(project, label); // 挂进侧栏还没有的公司时先按这个名称搭出这一组
    }
    if (project === draft.project) return;
    setDraft(project ? { project, period: withPeriod ? lastMonth() : "", scope: "" } : NO_FILING);
  };
  return (
    <>
      <button
        type="button"
        className="cc-chin-btn"
        data-slot="cc-chin-company"
        data-chosen={draft.project ? "" : undefined}
        aria-haspopup="dialog"
        onClick={() => openCompanyPicker(pick, draft.project)}
      >
        <Building2Icon />
        <Marquee className="min-w-0 truncate">{draft.project ? title || co.companies : co.companies}</Marquee>
        <ChevronDownIcon />
      </button>
      {draft.project && withPeriod && (
        <PeriodChip look="cc-chin-btn" side={side} value={draft.period} onChange={(period) => setDraft({ ...draft, period })} />
      )}
      {draft.project && withScope && (
        <BranchChip look="cc-chin-btn" side={side} branches={branches} value={draft.scope} onChange={(scope) => setDraft({ ...draft, scope })} />
      )}
    </>
  );
};

/** 已有对话：挂在公司下才有（没挂公司的已有对话不能再归到公司，下巴空着）。公司名点了打开右侧会话栏并展开公司卡（同顶栏）；
 *  月份、分店改了就 move（会话还没建好时先不可改）。 */
const SessionChin: FC = () => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  useProjects((s) => s.filings);
  const project = filingOf(remoteId, localId).project;
  const title = useCompanyTitle(project);
  if (!project) return null;
  return (
    <>
      {/* 会话栏要对话已存到服务端：还没存好的那一小段时间先不可点 */}
      <button type="button" className="cc-chin-btn" data-slot="cc-chin-company" disabled={!remoteId} onClick={openCompanyCard}>
        <Building2Icon />
        <Marquee className="min-w-0 truncate">{title || co.companies}</Marquee>
      </button>
      <FilingChips look="cc-chin-btn" side="top" />
    </>
  );
};

// ---------------------------------------------------------------- 顶栏「公司 ›」与月份分店胶囊
/** 顶栏对话名前的公司名（设计文档 4.3：点了打开右侧会话栏并展开公司卡；公司页从公司卡的名称、公司列表页进）。 */
export const ConversationCompany: FC<{ className?: string }> = ({ className }) => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  useProjects((s) => s.filings);
  const project = filingOf(remoteId, localId).project;
  const title = useCompanyTitle(project);
  // 会话栏要对话已存到服务端：还没存好时不显示
  if (!remoteId || !project || !title) return null;
  return (
    <button type="button" className={cn("cc-title-company", className)} onClick={openCompanyCard}>
      <Marquee className="truncate">{title}</Marquee>
      <ChevronRightIcon aria-hidden />
    </button>
  );
};

/** 手机对话页胶囊菜单最上面的公司名（挂在公司下时）：点了全屏打开右侧会话栏，返回键关。 */
export const MobileCompanyItem: FC = () => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  useProjects((s) => s.filings);
  const project = filingOf(remoteId, localId).project;
  const title = useCompanyTitle(kind ? project : "");
  if (!kind || !remoteId || !project || !title) return null;
  return (
    <>
      <DropdownMenu.Item className="cc-menu-item" onSelect={openCompanyCard}>
        <Building2Icon />
        <Marquee className="min-w-0 flex-1 truncate">{title}</Marquee>
      </DropdownMenu.Item>
      <DropdownMenu.Separator className="cc-menu-sep" />
    </>
  );
};

/** 手机对话页顶栏中间的标题（照 Claude 手机端 8.webp）：上行对话名，下行公司名（产品有公司分组、对话挂在公司下时才有）。
 *  只是标题，不可点（置顶、改名、公司卡都在右上「…」里）。 */
export const MobileChatTitle: FC = () => {
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  useProjects((s) => s.filings);
  const project = filingOf(remoteId, localId).project;
  const company = useCompanyTitle(kind ? project : "");
  return (
    <div className="cc-mtitle" data-slot="cc-mobile-title">
      <Marquee className="cc-mtitle-name">{title || t("untitled")}</Marquee>
      {kind && project && company && <Marquee className="cc-mtitle-sub">{company}</Marquee>}
    </div>
  );
};

/** 对话的处理月份与分店（挂在公司下才有，只在输入框下巴）：改了就 move，不改最后活动时间。 */
const FilingChips: FC<{ look?: "cc-chip-btn" | "cc-chin-btn"; side?: "top" | "bottom" }> = ({ look, side }) => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  useProjects((s) => s.filings);
  const filing = filingOf(remoteId);
  const branches = useBranches(withScope ? filing.project : "");
  if (!remoteId || !filing.project) return null;
  const change = (next: Partial<Pick<Filing, "period" | "scope">>) => void fileSession(remoteId, next).catch(withReport(report, "projects"));
  return (
    <>
      {withPeriod && <PeriodChip look={look} side={side} value={filing.period} onChange={(period) => change({ period })} />}
      {withScope && <BranchChip look={look} side={side} branches={branches} value={filing.scope} onChange={(scope) => change({ scope })} />}
    </>
  );
};

/** 手机对话页「…」里的「处理月份」「分店」（手机没有输入框下巴，用户 10-17：基础信息放上面，同线上旧版；挂在公司下才有，
 *  分店只在这家有不止一个分店时出现）：改了就 move（公司不变），不改最后活动时间。 */
export const MobileFilingItems: FC = () => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  useProjects((s) => s.filings);
  const filing = filingOf(remoteId, localId);
  const branches = useBranches(kind && withScope && remoteId ? filing.project : "");
  if (!kind || !remoteId || !filing.project) return null;
  const change = (next: Partial<Pick<Filing, "period" | "scope">>) => void fileSession(remoteId, next).catch(withReport(report, "projects"));
  return (
    <>
      {withPeriod && (
        <SubMenu icon={<CalendarIcon />} label={co.period} className="cc-menu min-w-(--cc-menu-w-sm) [--cc-menu-max:var(--cc-menu-max-list)]">
          <PeriodChoices value={filing.period} onChange={(period) => change({ period })} />
        </SubMenu>
      )}
      {withScope && branches.length > 1 && (
        <SubMenu icon={<StoreIcon />} label={co.branch} className="cc-menu min-w-(--cc-menu-w-sm) [--cc-menu-max:var(--cc-menu-max-list)]">
          <BranchChoices branches={branches} value={filing.scope} onChange={(scope) => change({ scope })} />
        </SubMenu>
      )}
    </>
  );
};
