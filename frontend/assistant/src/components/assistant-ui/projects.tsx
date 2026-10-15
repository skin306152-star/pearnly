// 按公司分组的界面零件（lib/managed-agents/projects.ts；只在产品有公司分组时出现，ERP / DMS 一律不出现）：
//
//   ProjectHeader       — 一家公司的组头：点名字打开主档页，箭头折叠；「⋯」（打开公司、新对话）与「＋」（在这家开新对话，
//                         月份 = 上个月）；手机上「＋」一直在、长按组头出菜单；组头显示这家的待处理数；收起时组里有在跑
//                         或没看过的，名字旁一个点
//   MoveToMenu          — 对话菜单里的「移到 ▸」：我最近的公司 + 搜索 + 添加公司… + 最近；移入公司时月份留空，不改置顶
//   ComposerCompany     — 新的空白对话里输入框上方的公司条「公司 ▾ / ABC · ก.ย. 2569 ▾」（至少有一家公司时才显示）
//   ConversationCompany — 对话顶栏「ABC 有限公司 ›」（点了打开主档页）与月份、分店胶囊（改了就 move）
//   FilingTag           — 侧栏对话行标题后面的月份、分店短写
//   CompanyTasks        — 公司组里对话列表上面一行「⏱ 定时任务 N」（这家有任务才出现；有出错或停了单的任务时数字旁一个点），
//                         点开 ?view=tasks&company=<公司>；数据来自一次取全部的 task_overview（lib/companies）
//
// 删公司在主档页的「⋯」里，不确认；出错时在侧栏显示一行（服务端给的原文）。颜色、圆角只用 globals.css 的变量。

import { useAui, useAuiState } from "@assistant-ui/react";
import { Building2Icon, CheckIcon, ChevronDownIcon, ChevronRightIcon, ClockIcon, MoreHorizontalIcon, PlusIcon, XIcon } from "lucide-react";
import { DropdownMenu, SubMenu } from "@/components/ui/menu";
import { type FC, type ReactNode, useEffect, useState } from "react";
import { openAddCompany } from "@/components/add-company";
import { BranchChip, PeriodChip } from "@/components/period-chip";
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
  monthLabel,
  monthShort,
  recentMonths,
  useBranches,
  useCompanyNav,
  usePendingCounts,
  useTaskOverview,
} from "@/lib/companies";
import { useAnyActivity } from "@/lib/managed-agents/activity";
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

/** 在某家公司（NO_FILING = 最近）开新对话：回到对话页、切到新对话；text 预填进输入框，send 才发出（交给 CC、草稿不发）。 */
export function useStartIn() {
  const aui = useAui();
  const { setView } = useView();
  return (filing: Partial<Filing>, text = "", send = false) => {
    setError("");
    setDraft(filing);
    setView("chat");
    void aui.threads().switchToNewThread();
    if (!text) return;
    // 新对话切过去后下一帧才是当前对话，再把文字放进输入框（同 window.tomo.ask）
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const composer = aui.thread().composer();
        composer.setText(text);
        if (send) composer.send();
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

// ---------------------------------------------------------------- 组里的定时任务
export const CompanyTasks: FC<{ company: string }> = ({ company }) => {
  const found = useTaskOverview((s) => s.companies[company]);
  const { setView } = useView();
  const { isMobile, setOpenMobile } = useSidebar();
  if (!found?.count) return null;
  return (
    <button
      type="button"
      className="cc-project-tasks"
      data-slot="cc-project-tasks"
      onClick={() => {
        useCompanyNav.setState({ taskCompany: company, taskFocus: "", taskSeed: null });
        setView("tasks");
        if (isMobile) setOpenMobile(false);
      }}
    >
      <ClockIcon />
      <span className="min-w-0 flex-1 truncate">{t("scheduledTasks")}</span>
      {found.alert && <span className="cc-task-alert" data-slot="cc-task-alert" aria-hidden />}
      <span className="cc-count">{found.count}</span>
    </button>
  );
};

// ---------------------------------------------------------------- 组头
export const ProjectHeader: FC<{ project: ProjectRow; open: boolean; onToggle: () => void; sessions: readonly string[] }> = ({
  project,
  open,
  onToggle,
  sessions,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const { isMobile, setOpenMobile } = useSidebar();
  const press = useLongPress(() => setMenuOpen(true));
  const busy = useAnyActivity(sessions);
  const pending = usePendingCounts((s) => s.companies[project.id] ?? 0);
  const newChatIn = useNewChatIn();
  const openCompany = useOpenCompany();
  const { setView } = useView();
  const openPending = (company: string) => {
    useCompanyNav.setState({ pendingCompany: company });
    setView("pending");
  };
  const go = (run: () => void) => {
    run();
    if (isMobile) setOpenMobile(false);
  };
  return (
    <div
      {...(isMobile ? press : {})}
      data-slot="cc-project"
      data-open={menuOpen || undefined}
      className="group/project cc-project-row"
      onContextMenu={isMobile ? (e) => e.preventDefault() : undefined}
    >
      <button type="button" className="cc-project-toggle" aria-expanded={open} aria-label={project.title} onClick={onToggle}>
        <ChevronDownIcon className="cc-project-chevron" />
      </button>
      <button type="button" className="cc-project-name" title={project.title} onClick={() => go(() => openCompany(project.id))}>
        <span className="min-w-0 truncate">{project.title}</span>
        {!open && busy && <span className="cc-activity" data-activity="unread" aria-label={t("unread")} />}
      </button>
      {pending > 0 && (
        // 这家的待处理数：点了打开只看这家的待处理
        <button type="button" className="cc-count" aria-label={`${co.pending} ${pending}`} title={co.pending} onClick={() => go(() => openPending(project.id))}>
          {pending}
        </button>
      )}
      <DropdownMenu.Root open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenu.Trigger asChild>
          <button type="button" className="cc-group-btn cc-project-more" aria-label={t("more")} title={t("more")}>
            <MoreHorizontalIcon />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content side={isMobile ? "bottom" : "right"} align="start" sideOffset={6} className="cc-menu !min-w-44">
            <DropdownMenu.Item className="cc-menu-item" onSelect={() => go(() => openCompany(project.id))}>
              <Building2Icon />
              {co.openCompany}
            </DropdownMenu.Item>
            <DropdownMenu.Item className="cc-menu-item" onSelect={() => go(() => newChatIn(project.id))}>
              <PlusIcon />
              {t("newChat")}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <button
        type="button"
        data-slot="aui_thread-list-new"
        className="cc-group-btn cc-project-add"
        aria-label={t("newChat")}
        title={t("newChat")}
        onClick={() => go(() => newChatIn(project.id))}
      >
        <PlusIcon />
      </button>
    </div>
  );
};

/** 侧栏公司下的一行出错提示（服务端给的原文），可关掉。 */
export const SidebarError: FC = () => {
  const error = useProjects((s) => s.error);
  if (!error) return null;
  return (
    <div role="alert" className="cc-sidebar-error">
      <span className="min-w-0 flex-1">{error}</span>
      <button type="button" className="cc-group-btn" aria-label={t("close")} onClick={() => setError("")}>
        <XIcon />
      </button>
    </div>
  );
};

// ---------------------------------------------------------------- 选公司（「移到 ▸」与公司条共用）
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
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {id === current && <CheckIcon />}
    </DropdownMenu.Item>
  );
  return (
    <>
      <div className="px-1 pb-1">
        <input
          className="cc-input h-8 w-full"
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

/** 对话菜单里的「移到 ▸」（radix DropdownMenu 的子菜单）。移入公司时月份、分店留空。 */
export const MoveToMenu: FC<{ sessionId: string | undefined; localId?: string | undefined }> = ({ sessionId, localId }) => {
  const kind = useProjects((s) => s.kind);
  useProjects((s) => s.filings);
  if (!kind || !sessionId) return null;
  const current = filingOf(sessionId, localId).project;
  const move = (project: string) => void fileSession(sessionId, { project, period: "", scope: "" }).catch(report);
  return (
    <SubMenu icon={<Building2Icon />} label={co.moveTo} className="cc-menu !min-w-52 max-w-72">
      <CompanyChoices current={current} onPick={move} />
    </SubMenu>
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

/** 新的空白对话（还没建会话）里，输入框上方「公司 ▾」，选了公司后接月份与分店胶囊；至少有一家公司才显示。 */
export const ComposerCompany: FC = () => {
  const kind = useProjects((s) => s.kind);
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  const sidebarHasOne = useProjects((s) => s.projects.length > 0);
  const draft = useProjects((s) => s.draft);
  const fresh = useAuiState((s) => s.threadListItem.status === "new");
  const [any, setAny] = useState(false);
  useEffect(() => {
    if (kind && !sidebarHasOne) void hasAnyCompany().then(setAny);
  }, [kind, sidebarHasOne]);
  const title = useCompanyTitle(draft.project);
  const branches = useBranches(withScope ? draft.project : "");
  if (!kind || !fresh || !(sidebarHasOne || any || draft.project)) return null;
  const pick = (project: string) =>
    setDraft(project ? { project, period: withPeriod ? lastMonth() : "", scope: "" } : NO_FILING);
  return (
    <div className="cc-composer-company" data-slot="cc-composer-company">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button type="button" className="cc-chip-btn" data-chosen={draft.project ? "" : undefined}>
            <Building2Icon />
            <span className="min-w-0 truncate">{draft.project ? title || co.companies : co.companies}</span>
            <ChevronDownIcon />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="start" sideOffset={6} className="cc-menu !min-w-52 max-w-72">
            <CompanyChoices current={draft.project} onPick={pick} />
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      {draft.project && withPeriod && <PeriodChip value={draft.period} onChange={(period) => setDraft({ ...draft, period })} />}
      {draft.project && withScope && <BranchChip branches={branches} value={draft.scope} onChange={(scope) => setDraft({ ...draft, scope })} />}
    </div>
  );
};

// ---------------------------------------------------------------- 顶栏「公司 ›」与月份分店胶囊
/** 顶栏对话名前的公司名（点了打开主档页）。 */
export const ConversationCompany: FC<{ className?: string }> = ({ className }) => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  useProjects((s) => s.filings);
  const project = filingOf(remoteId, localId).project;
  const title = useCompanyTitle(project);
  const openCompany = useOpenCompany();
  if (!project || !title) return null;
  return (
    <button type="button" className={cn("cc-title-company", className)} onClick={() => openCompany(project)}>
      <span className="truncate">{title}</span>
      <ChevronRightIcon aria-hidden />
    </button>
  );
};

/** 对话的处理月份与分店（挂在公司下才有）：改了就 move，不改最后活动时间。 */
export const ConversationFiling: FC = () => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  useProjects((s) => s.filings);
  const filing = filingOf(remoteId);
  const branches = useBranches(withScope ? filing.project : "");
  if (!remoteId || !filing.project) return null;
  const change = (next: Partial<Filing>) => void fileSession(remoteId, { ...filing, ...next }).catch(report);
  return (
    <>
      {withPeriod && <PeriodChip value={filing.period} onChange={(period) => change({ period })} />}
      {withScope && <BranchChip branches={branches} value={filing.scope} onChange={(scope) => change({ scope })} />}
    </>
  );
};

/** 手机胶囊菜单里的「移到…」「处理月份」两项。 */
export const MobileFilingItems: FC = () => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  const withPeriod = useProjects((s) => s.period);
  useProjects((s) => s.filings);
  if (!kind || !remoteId) return null;
  const filing = filingOf(remoteId);
  return (
    <>
      <MoveToMenu sessionId={remoteId} localId={localId} />
      {withPeriod && filing.project && (
        <SubMenu label={co.period} className="cc-menu !min-w-40 [--cc-menu-max:20rem]">
          <PeriodItems value={filing.period} onPick={(period) => void fileSession(remoteId, { ...filing, period }).catch(report)} />
        </SubMenu>
      )}
    </>
  );
};

const PeriodItems: FC<{ value: string; onPick: (period: string) => void }> = ({ value, onPick }) => {
  const months = recentMonths();
  if (value && !months.includes(value)) months.push(value);
  return (
    <>
      {[...months, ""].map((month) => (
        <DropdownMenu.Item key={month || "none"} className="cc-menu-item" onSelect={() => month !== value && onPick(month)}>
          <span className="flex-1">{monthLabel(month)}</span>
          {month === value && <CheckIcon />}
        </DropdownMenu.Item>
      ))}
    </>
  );
};

// ---------------------------------------------------------------- 侧栏对话行的月份、分店短写
/** 「· ก.ย. · สาขา 00001」：挂在公司下、指定了月份或分店才有。 */
export const FilingTag: FC = () => {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const kind = useProjects((s) => s.kind);
  const filing = useProjects((s) => (remoteId && s.filings[remoteId]) || (localId && s.filings[localId]) || NO_FILING);
  if (!kind || !filing.project) return null;
  const text = [monthShort(filing.period), branchShort(filing.scope)].filter(Boolean).join(" · ");
  if (!text) return null;
  return <span className="cc-filing-tag" data-slot="cc-filing-tag">{` · ${text}`}</span>;
};
