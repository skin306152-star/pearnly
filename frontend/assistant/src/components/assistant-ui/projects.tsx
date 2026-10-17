// 按公司分组的界面零件（lib/managed-agents/projects.ts；只在产品有公司分组时出现，ERP / DMS 一律不出现）：
//
//   ProjectHeader       — 一家公司的组头（sidebar-group.tsx 的 GroupHeader，设计文档 3.4）：最左公司图标、公司名（同会话行标题
//                         字号字色），整行折叠 / 展开，下面这家的对话行缩进到公司名起点；右侧这家的待处理数（淡色数字，点了只看
//                         这家的待处理）、「···」（分组和排序，用户 2026-10-15 夜：打开公司、在这家新建对话不要了）、「＋」（在这家
//                         开新对话，月份 = 上个月）；手机上长按组头、键盘菜单键出「···」菜单；收起时组里有在跑或没看过的，名字后一个点。
//                         组头开关同时是拖动把手（thread-list.tsx 的公司组排序，共用拖动）
//   ComposerChin        — 输入框下巴（thread.tsx 的 Chin 插槽，设计文档 3.12）：公司、月份、分店三个幽灵按钮。新对话里「公司 ▾」选这段
//                         对话挂哪家（我最近的公司 + 搜索 + 添加公司… + 最近；至少有一家公司时才显示）；已有对话里点公司名打开右侧
//                         会话栏的公司卡（同顶栏，lib/company-panel 的 openCompanyCard），月份、分店改了就 move。已有对话不能换公司
//                         （用户 10-17：「移到」已删，没挂公司的已有对话下巴里也不再有「公司 ▾」）
//   ConversationCompany — 对话顶栏「ABC 有限公司 ›」（点了打开右侧会话栏的公司卡）；手机在胶囊菜单最上面（MobileCompanyItem）
//   MobileChatTitle     — 手机对话页顶栏中间的两行标题：对话名、公司名（挂在公司下时；照 Claude 手机端 8.webp，不可点）
//
// 公司组里不再有「⏱ 定时任务 N」一行（用户 2026-10-15 夜同意设计文档 4.2：改由右侧栏「定时任务」卡与公司页「定时任务」标签承担）。
// 删公司在主档页的「⋯」里，不确认；出错时在侧栏显示一行（服务端给的原文）。颜色、圆角只用 globals.css 的变量。

import { useAui, useAuiState } from "@assistant-ui/react";
import { Building2Icon, CalendarIcon, CheckIcon, ChevronDownIcon, ChevronRightIcon, PlusIcon, StoreIcon, XIcon } from "lucide-react";
import { DropdownMenu, SubMenu } from "@/components/ui/menu";
import { type FC, type ReactNode, useEffect, useState } from "react";
import { openAddCompany } from "@/components/add-company";
import { BranchChip, BranchChoices, PeriodChip, PeriodChoices } from "@/components/period-chip";
import { GroupHeader, GroupNew, GroupSortMenu, useCloseDrawer } from "@/components/assistant-ui/sidebar-group";
import type { DragHandle } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";
import { useSidebar } from "@/components/ui/sidebar";
import { useLongPress } from "@/hooks/use-long-press";
import { co, t } from "@/i18n";
import {
  branchShort,
  cachedDetail,
  type CompanyRow,
  hasAnyCompany,
  lastMonth,
  listCompanies,
  useBranches,
  useCompanyNav,
  usePendingCounts,
} from "@/lib/companies";
import { openCompanyCard } from "@/lib/company-panel";
import { useGroupActivity } from "@/lib/managed-agents/activity";
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
import { useView } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { shownError } from "@/lib/frappe";

const report = (error: unknown) => setError(shownError(error));

const COMPOSER_INPUT = "textarea.aui-composer-input";

/** 新建对话后光标直接进输入框（同 Claude；侧栏组头「＋」、顶部「新建」、公司页、手机都一样）。点击当下先聚焦一次
 *  （手机上只有在点击当下聚焦才弹键盘），新对话换上来后的下两帧再聚焦一次（输入框可能换了一个）。 */
export function focusComposer(now = true) {
  const focus = () => document.querySelector<HTMLTextAreaElement>(COMPOSER_INPUT)?.focus({ preventScroll: true });
  if (now) focus();
  requestAnimationFrame(() => requestAnimationFrame(focus));
}

/** 在某家公司（NO_FILING = 最近）开新对话：回到对话页、切到新对话，光标进输入框；text 预填进输入框，send 才发出（交给 CC、草稿不发）。 */
export function useStartIn() {
  const aui = useAui();
  const { setView } = useView();
  return (filing: Partial<Filing>, text = "", send = false, files: readonly File[] = []) => {
    setError("");
    setDraft(filing);
    setView("chat");
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
        for (const file of files) void composer.addAttachment(file).catch(() => undefined);
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

/** 打开一家公司的主档页。 */
export function useOpenCompany() {
  const { setView } = useView();
  return (company: string) => {
    useCompanyNav.setState({ company });
    setView("company");
  };
}

// ---------------------------------------------------------------- 组头
export const ProjectHeader: FC<{
  project: ProjectRow;
  open: boolean;
  onToggle: () => void;
  sessions: readonly string[];
  /** 组头开关同时是拖动把手（公司组排序）；Alt+↑/↓ 挪一格。 */
  handle?: DragHandle;
  onMove?: (delta: -1 | 1) => void;
}> = ({ project, open, onToggle, sessions, handle, onMove }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const { isMobile } = useSidebar();
  const press = useLongPress(() => setMenuOpen(true), () => setMenuOpen(false));
  const activity = useGroupActivity(sessions);
  const pending = usePendingCounts((s) => s.companies[project.id] ?? 0);
  const newChatIn = useNewChatIn();
  const { setView } = useView();
  const go = useCloseDrawer();
  const openPending = (company: string) => {
    useCompanyNav.setState({ pendingCompany: company });
    setView("pending");
  };
  return (
    <GroupHeader
      {...(isMobile ? press : {})}
      data-slot="cc-project"
      data-open={menuOpen || undefined}
      className="group/project cc-project-row"
      onContextMenu={isMobile ? (e) => e.preventDefault() : undefined}
      icon={<Building2Icon className="cc-group-icon" aria-hidden />}
      label={project.title}
      title={project.title}
      open={open}
      onToggle={onToggle}
      onMenuKey={() => setMenuOpen(true)}
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
      {pending > 0 && (
        // 这家的待处理数：点了打开只看这家的待处理
        <button
          type="button"
          tabIndex={-1}
          className="cc-group-count"
          aria-label={`${co.pending} ${pending}`}
          title={co.pending}
          onClick={() => go(() => openPending(project.id))}
        >
          {pending}
        </button>
      )}
      <GroupSortMenu open={menuOpen} onOpenChange={setMenuOpen} alignHead={isMobile} />
      <GroupNew onClick={() => newChatIn(project.id)} />
    </GroupHeader>
  );
};

/** 侧栏公司下的一行出错提示（服务端给的原文），可关掉。 */
export const SidebarError: FC = () => {
  const error = useProjects((s) => s.error);
  if (!error) return null;
  return (
    <div role="alert" className="cc-sidebar-error">
      <span className="min-w-0 flex-1">{error}</span>
      <button type="button" className="cc-row-btn cc-tap" aria-label={t("close")} onClick={() => setError("")}>
        <XIcon />
      </button>
    </div>
  );
};

// ---------------------------------------------------------------- 选公司（新对话的公司条）
/** 记住选过的公司名（搜出来、刚添加的公司不在侧栏里）。 */
const titles = new Map<string, string>();

/** 我最近的 10 家（侧栏里的）；输入了就搜全所公司；末尾「添加公司…」「最近」。 */
const CompanyChoices: FC<{ current: string; onPick: (id: string) => void }> = ({ current, onPick }) => {
  const projects = useProjects((s) => s.projects);
  const [search, setSearch] = useState("");
  const [found, setFound] = useState<CompanyRow[] | null>(null);
  useEffect(() => {
    const text = search.trim();
    if (!text) return setFound(null);
    let alive = true;
    const timer = window.setTimeout(
      () => void listCompanies(text, 0, 20).then((rows) => alive && setFound(rows), () => alive && setFound([])),
      250,
    );
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [search]);
  const rows: Array<{ id: string; title: string }> = found ?? projects.slice(0, 10);
  const choice = (id: string, label: ReactNode) => (
    <DropdownMenu.Item
      key={id || "recents"}
      className="cc-menu-item"
      onSelect={() => {
        if (id && typeof label === "string") {
          titles.set(id, label);
          hintTitle(id, label); // 挂进侧栏还没有的公司时先按这个名称搭出这一组
        }
        if (id !== current) onPick(id);
      }}
    >
      <Marquee className="min-w-0 flex-1 truncate">{label}</Marquee>
      {id === current && <CheckIcon />}
    </DropdownMenu.Item>
  );
  return (
    <>
      <div className="px-1 pb-1">
        <input
          className="cc-input w-full"
          value={search}
          aria-label={t("search")}
          placeholder={t("search")}
          onChange={(e) => setSearch(e.target.value)}
          // 菜单的按字母跳转不吃输入框里的键
          onKeyDown={(e) => e.key !== "Escape" && e.key !== "ArrowDown" && e.stopPropagation()}
        />
      </div>
      {rows.map((p) => choice(p.id, p.title))}
      {found !== null && !found.length && <div className="px-2.5 py-1.5 text-ink-3 text-sm">{t("noResults")}</div>}
      <DropdownMenu.Separator className="cc-menu-sep" />
      <DropdownMenu.Item className="cc-menu-item" onSelect={() => openAddCompany(onPick)}>
        <PlusIcon />
        {co.addCompany}
      </DropdownMenu.Item>
      {choice("", t("recents"))}
    </>
  );
};

// ---------------------------------------------------------------- 输入框上方的公司条

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

/** 输入框下巴：ERP / DMS（没有公司分组）不出现；新对话「公司 ▾」+ 月份 + 分店（草稿）；已有对话「公司」+ 月份 + 分店（改了就 move）。
 *  手机没有下巴（用户 10-17：基础信息放上面，同线上旧版）：新对话放在问候下面（WelcomeFiling），已有对话在右上「…」里
 *  （公司名、处理月份、分店）。 */
export const ComposerChin: FC = () => {
  const kind = useProjects((s) => s.kind);
  const fresh = useAuiState((s) => s.threadListItem.status === "new");
  const { isMobile } = useSidebar();
  if (!kind || isMobile) return null;
  return fresh ? <DraftChin /> : <SessionChin />;
};

/** 手机新对话：问候下面一行「公司 ▾」+ 月份 + 分店（同线上旧版的公司条），菜单向下开；电脑在下巴里。 */
export const WelcomeFiling: FC = () => {
  const kind = useProjects((s) => s.kind);
  const fresh = useAuiState((s) => s.threadListItem.status === "new");
  const { isMobile } = useSidebar();
  if (!kind || !fresh || !isMobile) return null;
  return (
    <div className="cc-welcome-filing" data-slot="cc-welcome-filing">
      <DraftChin side="bottom" />
    </div>
  );
};

/** 新的空白对话（还没建会话）：「公司 ▾」，选了公司后接月份与分店；至少有一家公司才显示。菜单默认向上开（下巴），
 *  手机问候下面那一行向下开。 */
const DraftChin: FC<{ side?: "top" | "bottom" }> = ({ side = "top" }) => {
  const kind = useProjects((s) => s.kind);
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  const sidebarHasOne = useProjects((s) => s.projects.length > 0);
  const draft = useProjects((s) => s.draft);
  const [any, setAny] = useState(false);
  useEffect(() => {
    if (kind && !sidebarHasOne) void hasAnyCompany().then(setAny);
  }, [kind, sidebarHasOne]);
  const title = useCompanyTitle(draft.project);
  const branches = useBranches(withScope ? draft.project : "");
  if (!(sidebarHasOne || any || draft.project)) return null;
  const pick = (project: string) =>
    setDraft(project ? { project, period: withPeriod ? lastMonth() : "", scope: "" } : NO_FILING);
  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button type="button" className="cc-chin-btn" data-slot="cc-chin-company" data-chosen={draft.project ? "" : undefined}>
            <Building2Icon />
            <Marquee className="min-w-0 truncate">{draft.project ? title || co.companies : co.companies}</Marquee>
            <ChevronDownIcon />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content side={side} align="start" sideOffset={6} className="cc-menu !min-w-52 max-w-72">
            <CompanyChoices current={draft.project} onPick={pick} />
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
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
  const change = (next: Partial<Pick<Filing, "period" | "scope">>) => void fileSession(remoteId, next).catch(report);
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
  const change = (next: Partial<Pick<Filing, "period" | "scope">>) => void fileSession(remoteId, next).catch(report);
  return (
    <>
      {withPeriod && (
        <SubMenu icon={<CalendarIcon />} label={co.period} className="cc-menu !min-w-40 [--cc-menu-max:var(--cc-menu-max-list)]">
          <PeriodChoices value={filing.period} onChange={(period) => change({ period })} />
        </SubMenu>
      )}
      {withScope && branches.length > 1 && (
        <SubMenu icon={<StoreIcon />} label={co.branch} className="cc-menu !min-w-40 [--cc-menu-max:var(--cc-menu-max-list)]">
          <BranchChoices branches={branches} value={filing.scope} onChange={(scope) => change({ scope })} />
        </SubMenu>
      )}
    </>
  );
};
