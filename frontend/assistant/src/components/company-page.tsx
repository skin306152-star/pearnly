// 公司页（?company=<编号>；公司列表、侧栏组「···」「打开公司」、会话栏公司卡点进来）。一家客户一份，全所共用（Cowork companies 域）。
// 设计文档 3.15 / 4.5：照 Claude Code 的项目页做成管理页，日常的公司信息在右侧会话栏（components/session-panel.tsx）。
//
//   顶部     名称（点了就地改名，手机用输入框）；「···」：改名、刷新账套、删除公司（不确认）
//   左边     「在这家新建对话」输入框（月份胶囊：最近 12 个月 + 不指定，默认上个月；多分店时分店胶囊；发出 = 在这家开对话并发出）
//            → 概览（法定名 · 税号 · 分店，合并申报的写「合并申报」）→ 账套（多分店按分店分组，覆盖本月的高亮）→ 待处理（这家的）
//   右边     大面板，标签「对话 / 资料 / 定时任务」：
//            对话  只列我的，从服务端按这家取（分组列表 group=<公司>，分页），按 进行中 / 等你处理 / 已完成 分组（只用真实信号，
//                  lib/company-panel 的 chatState），组内按活动倒序，带月份短写
//            资料  知识库、单据文件夹、记住的选择、申报草稿（卡片同会话栏，components/company-cards.tsx）
//            定时任务  这家的任务（点一行跳任务页，「＋」带预填）；没有录入权限时不出现
//   手机单栏：先左边，再大面板。
//
// 别处改了这家（pearnly.project upsert）重取，删了（delete）回到公司列表。文案走 i18n.ts 的 co；样式只用 globals.css 的 .cc-* 与变量。

import { useAui } from "@assistant-ui/react";
import { ArrowUpIcon, LoaderIcon, MoreHorizontalIcon, PencilIcon, RefreshCwIcon, TrashIcon } from "lucide-react";
import { Tabs } from "radix-ui";
import { DropdownMenu } from "@/components/ui/menu";
import { type FC, useCallback, useEffect, useRef, useState } from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { companyLine } from "@/components/companies";
import {
  ChoicesSection,
  DraftsSection,
  FolderSection,
  NotesSection,
  PanelCard,
  PendingCard,
  SetsSection,
  TasksSection,
} from "@/components/company-cards";
import { BranchChip, PeriodChip } from "@/components/period-chip";
import { useSidebar } from "@/components/ui/sidebar";
import { co, t } from "@/i18n";
import {
  type Branch,
  branchesOf,
  deleteCompany,
  type Detail,
  dropCachedDetail,
  forgetAnyCompany,
  lastMonth,
  loadDetail,
  monthShort,
  refreshCatalogs,
  renameCompany,
  useCompanyNav,
  TASK_EVENTS,
} from "@/lib/companies";
import { type ChatState, cachedPanelDetail, chatState, forgetPanelDetail, groupChats, rememberDetail, unreadRuns } from "@/lib/company-panel";
import { useLiveRefresh } from "@/lib/live";
import { noteRun, runningSessions, useActivities } from "@/lib/managed-agents/activity";
import { type ListedSession, listGrouped } from "@/lib/managed-agents/api";
import type { RunStatus } from "@/lib/managed-agents/events";
import { forgetProject, hintTitle, renameProjectLocally, useProjects } from "@/lib/managed-agents/projects";
import { onProjectChange } from "@/lib/managed-agents/realtime";
import { useView } from "@/lib/nav";
import { shownError } from "@/lib/frappe";

// 卡片与各节的内容搬到了 company-cards.tsx（会话栏与本页共用）；原来从本文件导入的照样能用
export {
  ChoiceRow,
  choiceSection,
  commitNote,
  FolderSection,
  folderState,
  NoteEditor,
  noteKey,
  noteText,
  putNote,
  removeNote,
  setChoiceShown,
  setFor,
  SetsSection,
} from "@/components/company-cards";

// ---------------------------------------------------------------- 页面
export const CompanyPage: FC = () => {
  const company = useCompanyNav((s) => s.company);
  const { setView } = useView();
  const withPeriod = useProjects((s) => s.period);
  const [period, setPeriod] = useState(() => (withPeriod ? lastMonth() : ""));
  const [detail, setDetail] = useState<Detail | null>(() => cachedPanelDetail(company, period));
  const [error, setError] = useState("");
  const asked = useRef(0);

  const reload = useCallback(async () => {
    if (!company) return;
    const mine = ++asked.current;
    try {
      const next = await loadDetail(company, period);
      if (mine !== asked.current) return;
      hintTitle(next.company.id, next.company.title);
      setDetail(next);
      setError("");
      dropCachedDetail(company);
      rememberDetail(company, period, next);
    } catch (e) {
      if (mine === asked.current) setError(shownError(e));
    }
  }, [company, period]);

  useEffect(() => {
    setDetail(cachedPanelDetail(company, period));
    setError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company]);
  useEffect(() => void reload(), [reload]);
  // 推来的主档变化之外，再按站点通用的做法核对：电脑同步文件夹、回到前台、窗口拿回焦点、实时通道重连时重取，另有 60 秒兜底
  // （2026-10-15 验收 F8：另一台设备「更换…」文件夹后，这页错过了那次推送就一直停在旧路径）
  useLiveRefresh(reload, { events: TASK_EVENTS });
  useEffect(
    () =>
      onProjectChange((change) => {
        if (change.op === "upsert" && change.project.id === company) void reload();
        if (change.op === "delete" && change.id === company) {
          forgetPanelDetail(company);
          setView("companies");
        }
      }),
    [company, reload, setView],
  );

  if (!company) return null;
  if (!detail) {
    return (
      <div className="flex flex-1 items-center justify-center text-ink-3">
        {error ? <p role="alert" className="text-danger text-sm">{error}</p> : <LoaderIcon className="size-4 animate-spin" />}
      </div>
    );
  }
  // 键带公司：换到另一家（有缓存时不经过加载态）时整页重新挂上，输入框的字与分店、所在标签、新增 / 编辑框不串到另一家
  return <Master key={detail.company.id} detail={detail} period={period} onPeriod={setPeriod} reload={reload} error={error} />;
};

const Master: FC<{ detail: Detail; period: string; onPeriod: (p: string) => void; reload: () => Promise<void>; error: string }> = ({
  detail,
  period,
  onPeriod,
  reload,
  error,
}) => {
  const id = detail.company.id;
  const branches = branchesOf(detail);
  const multi = branches.length > 1;
  return (
    <div data-slot="cc-scroll" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="cc-page cc-company-page" data-slot="cc-company-page">
        <Header detail={detail} reload={reload} />
        {error && <div role="alert" className="mb-4 rounded-md bg-danger-soft px-3 py-2 text-danger text-sm">{error}</div>}
        <div className="cc-company-cols">
          <div className="cc-company-overview">
            <CompanyComposer company={id} period={period} onPeriod={onPeriod} branches={branches} />
            <Overview detail={detail} />
            <SetsSection sets={detail.account_sets} branches={multi ? branches : []} period={period} />
            <PendingCard company={id} />
          </div>
          <CompanyTabs detail={detail} period={period} reload={reload} />
        </div>
      </div>
    </div>
  );
};

/** 概览：法定名（与名称不同才写）、税号 · 分店 · 年度、合并申报。 */
const Overview: FC<{ detail: Detail }> = ({ detail }) => {
  const c = detail.company;
  const legal = c.legal_name && c.legal_name !== c.title ? c.legal_name : "";
  const line = companyLine(c);
  if (!legal && !line && !c.combined_vat) return null;
  return (
    <PanelCard id="overview" title={co.company} slot="cc-company-overview">
      {legal && <div className="cc-panel-row text-ink">{legal}</div>}
      {line && <div className="cc-panel-row">{line}</div>}
      {!!c.combined_vat && <span className="cc-chip self-start">{co.combinedVat}</span>}
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 顶部
const Header: FC<{ detail: Detail; reload: () => Promise<void> }> = ({ detail, reload }) => {
  const { isMobile } = useSidebar();
  const { setView } = useView();
  const c = detail.company;
  // 改名成功就显示新名字（服务端回的那份），不等整页重取；重取到了以服务端为准
  const [title, setTitle] = useState(c.title);
  useEffect(() => setTitle(c.title), [c.title]);
  const [renaming, setRenaming] = useState(false);
  const [value, setValue] = useState(c.title);
  const [error, setError] = useState("");
  const save = async (input: string) => {
    const next = input.trim();
    setRenaming(false);
    if (!next || next === title) return;
    setError("");
    try {
      const out = await renameCompany(c.id, next);
      const saved = (out as { title?: string } | null)?.title || next;
      setTitle(saved);
      renameProjectLocally(c.id, saved);
      dropCachedDetail(c.id);
    } catch (e) {
      setError(shownError(e));
    }
  };
  const rename = () => {
    if (!isMobile) {
      setValue(title);
      return setRenaming(true);
    }
    const next = window.prompt(t("rename"), title);
    if (next) void save(next);
  };
  // 刷新账套：本人能用的每条连接各请电脑读一次目录（同添加公司里的「刷新账套」），再重取这家
  const refreshSets = async () => {
    setError("");
    try {
      await refreshCatalogs();
      dropCachedDetail(c.id);
      await reload();
    } catch (e) {
      setError(shownError(e));
    }
  };
  const remove = async () => {
    try {
      await deleteCompany(c.id);
      forgetProject(c.id);
      forgetAnyCompany();
      dropCachedDetail(c.id);
      forgetPanelDetail(c.id);
      setView("companies");
    } catch (e) {
      setError(shownError(e));
    }
  };
  return (
    <div className="cc-page-head items-start">
      <div className="min-w-0 flex-1">
        {renaming ? (
          <input
            autoFocus
            value={value}
            aria-label={t("rename")}
            onChange={(e) => setValue(e.target.value)}
            onFocus={(e) => e.target.select()}
            onBlur={() => void save(value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save(value);
              if (e.key === "Escape") setRenaming(false);
            }}
            className="cc-input w-full text-lg"
          />
        ) : (
          <h1 className="cc-page-title">
            <button type="button" className="cc-page-title-btn" title={t("rename")} onClick={rename}>{title}</button></h1>
        )}
        {error && <p role="alert" className="mt-1 text-danger text-sm">{error}</p>}
      </div>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger className="cc-icon-btn" aria-label={t("more")}>
          <MoreHorizontalIcon />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={4} className="cc-menu !min-w-44">
            <DropdownMenu.Item className="cc-menu-item" onSelect={rename}>
              <PencilIcon />
              {t("rename")}
            </DropdownMenu.Item>
            <DropdownMenu.Item className="cc-menu-item" onSelect={() => void refreshSets()}>
              <RefreshCwIcon />
              {co.refreshSets}
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="cc-menu-sep" />
            <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={() => void remove()}>
              <TrashIcon />
              {co.deleteCompany}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
};

// ---------------------------------------------------------------- 输入框
const CompanyComposer: FC<{ company: string; period: string; onPeriod: (p: string) => void; branches: Branch[] }> = ({
  company,
  period,
  onPeriod,
  branches,
}) => {
  const start = useStartIn();
  const withPeriod = useProjects((s) => s.period);
  const withScope = useProjects((s) => s.scope);
  const [scope, setScope] = useState("");
  const [text, setText] = useState("");
  const send = () => {
    const value = text.trim();
    if (!value) return;
    setText("");
    start({ project: company, period: withPeriod ? period : "", scope: withScope ? scope : "" }, value, true);
  };
  return (
    <div className="cc-composer" data-slot="cc-company-composer">
      <textarea
        rows={2}
        value={text}
        aria-label={t("placeholder")}
        placeholder={t("placeholder")}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        className="block max-h-72 min-h-6 w-full resize-none bg-transparent px-1 font-sans text-base outline-none"
      />
      <div className="flex items-center gap-1.5">
        {withPeriod && <PeriodChip value={period} onChange={onPeriod} />}
        {withScope && <BranchChip branches={branches} value={scope} onChange={setScope} />}
        <span className="flex-1" />
        <button type="button" className="cc-send" aria-label={t("send")} title={t("send")} disabled={!text.trim()} onClick={send}>
          <ArrowUpIcon />
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- 大面板
type Tab = "chats" | "library" | "tasks";

const CompanyTabs: FC<{ detail: Detail; period: string; reload: () => Promise<void> }> = ({ detail, period, reload }) => {
  const id = detail.company.id;
  const [tab, setTab] = useState<Tab>("chats");
  const branches = branchesOf(detail);
  const tabs: Array<[Tab, string]> = [["chats", co.conversations], ["library", co.library]];
  if (detail.tasks) tabs.push(["tasks", t("scheduledTasks")]);
  return (
    <Tabs.Root value={tab} onValueChange={(next) => setTab(next as Tab)} className="cc-company-pane" data-slot="cc-company-pane">
      <Tabs.List className="cc-tabs" aria-label={detail.company.title}>
        {tabs.map(([value, label]) => (
          <Tabs.Trigger key={value} value={value} className="cc-tab">
            {label}
          </Tabs.Trigger>
        ))}
      </Tabs.List>
      <Tabs.Content value="chats" className="outline-none">
        <Conversations company={id} />
      </Tabs.Content>
      <Tabs.Content value="library" className="cc-company-cards outline-none">
        <NotesSection company={id} notes={detail.notes} reload={reload} keepEmpty />
        <FolderSection company={id} folders={detail.folders} reload={reload} />
        <ChoicesSection company={id} choices={detail.choices} period={period} reload={reload} />
        {detail.drafts && (
          <DraftsSection company={id} drafts={detail.drafts} branches={branches.length > 1 ? branches : []} combined={!!detail.company.combined_vat} />
        )}
      </Tabs.Content>
      {detail.tasks && (
        <Tabs.Content value="tasks" className="cc-company-cards outline-none">
          <TasksSection company={id} detail={detail} period={period} keepEmpty />
        </Tabs.Content>
      )}
    </Tabs.Root>
  );
};

// ---------------------------------------------------------------- 对话
// 页面开着时开始跑的对话：等这么久再补读运行状态（几段一起开始跑时合成一次请求）
const RUN_RECHECK_MS = 1000;
// 侧栏条数变化与移动推送常常前后脚到：这么久内的合成一次重取
const RELOAD_MERGE_MS = 150;

const STATE_LABEL: Record<ChatState, () => string> = {
  running: () => co.chatRunning,
  waiting: () => co.chatWaiting,
  done: () => co.chatDone,
};

/** 我在这家的对话：按这家从服务端取（分组列表 group=<公司>，同侧栏「显示更多」的翻页），按 进行中 / 等你处理 / 已完成 分组，
 *  组内按活动倒序，带月份短写；点了切过去。顺带请服务端核对侧栏以为在跑的那几个（check，最多 10 个，同侧栏的做法），
 *  读回的运行状态交给 noteRun 校正实时状态（同侧栏 session-list-adapter 的 absorb），分组只看实时状态；读回里有等你点
 *  「允许」的、且还在跑的算「等你处理」。页面开着时才开始跑的对话补读一次它的运行状态；不在跑了丢掉读回的，下次开始跑再读。
 *  这家的对话有进出（新建、移入移出、侧栏挂上的条数变了）或公司有变化时重取第一页。 */
export const Conversations: FC<{ company: string }> = ({ company }) => {
  const aui = useAui();
  const { setView } = useView();
  const [rows, setRows] = useState<ListedSession[] | null>(null);
  const [runs, setRuns] = useState<Record<string, RunStatus>>({});
  const [next, setNext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const asked = useRef(0);
  // 已请服务端读过运行状态的对话（读了没带回的不反复读）
  const checked = useRef(new Set<string>());
  const absorbRuns = useCallback((ids: readonly string[], found: Record<string, RunStatus> | undefined) => {
    ids.forEach((id) => checked.current.add(id));
    for (const [id, run] of Object.entries(found ?? {})) noteRun(id, run);
    setRuns((before) => ({ ...before, ...(found ?? {}) }));
  }, []);
  const load = useCallback(
    async (after = "") => {
      const mine = ++asked.current;
      setBusy(true);
      try {
        const check = runningSessions();
        const page = await listGrouped({ group: company, after, check });
        if (mine !== asked.current) return;
        setRows((before) => {
          const kept = after ? (before ?? []) : [];
          const seen = new Set(kept.map((row) => row.id));
          return [...kept, ...page.sessions.filter((row) => !seen.has(row.id) && !row.archived_at)];
        });
        absorbRuns(check, page.runs);
        setNext(page.nextCursor);
      } catch {
        if (mine === asked.current) setRows((before) => before ?? []);
      } finally {
        if (mine === asked.current) setBusy(false);
      }
    },
    [company, absorbRuns],
  );
  useEffect(() => void load(), [load]);
  // 有进出时重取第一页：只看移进这家、或从这一页里移走的（别家的移动不管）；侧栏条数变化与推送常常一起来，合成一次
  const rowsNow = useRef(rows);
  rowsNow.current = rows;
  const soon = useRef(0);
  useEffect(() => {
    const reload = () => {
      window.clearTimeout(soon.current);
      soon.current = window.setTimeout(() => void load(), RELOAD_MERGE_MS);
    };
    // 侧栏知道的、挂在这家的对话条数（新建 / 移入移出时会变）：变了就重取
    const filedHere = () => Object.values(useProjects.getState().filings).filter((f) => f.project === company).length;
    let filed = filedHere();
    const offFiled = useProjects.subscribe(() => {
      const count = filedHere();
      if (count === filed) return;
      filed = count;
      reload();
    });
    const offMove = onProjectChange((change) => {
      if (change.op !== "move") return;
      if (change.project === company || rowsNow.current?.some((row) => row.id === change.session_id)) reload();
    });
    return () => {
      offFiled();
      offMove();
      window.clearTimeout(soon.current);
    };
  }, [company, load]);
  const activities = useActivities((rows ?? []).map((row) => row.id));
  const running = (rows ?? []).filter((row) => activities[row.id] === "running").map((row) => row.id);
  const runningKey = running.join(",");
  const missing = unreadRuns(running, runs, checked.current).join(",");
  // 不在跑了：丢掉读回的运行状态，下次开始跑重新读
  useEffect(() => {
    const now = new Set(runningKey ? runningKey.split(",") : []);
    for (const id of [...checked.current]) if (!now.has(id)) checked.current.delete(id);
    setRuns((before) => {
      const kept = Object.fromEntries(Object.entries(before).filter(([id]) => now.has(id)));
      return Object.keys(kept).length === Object.keys(before).length ? before : kept;
    });
  }, [runningKey]);
  // 页面开着时才开始跑的：稍等合并后补读一次（只读运行状态，不换列表，翻过的页不丢）
  useEffect(() => {
    if (!missing) return;
    const ids = missing.split(",");
    const timer = setTimeout(() => {
      listGrouped({ group: company, check: ids })
        .then((page) => absorbRuns(ids, page.runs))
        .catch(() => ids.forEach((id) => checked.current.add(id)));
    }, RUN_RECHECK_MS);
    return () => clearTimeout(timer);
  }, [missing, company, absorbRuns]);
  if (!rows) return <LoaderIcon className="size-4 animate-spin text-ink-3" aria-hidden />;
  if (!rows.length) return <p className="text-ink-3 text-sm">{co.noChats}</p>;
  const groups = groupChats(rows, (row) => chatState(activities[row.id], runs[row.id], row.unread));
  return (
    <div className="flex flex-col gap-2" data-slot="cc-company-chats">
      {groups.map(([state, list]) => (
        <section key={state} className="flex flex-col" data-chat-state={state}>
          <div className="cc-subheading flex items-center gap-1.5">
            <span>{STATE_LABEL[state]()}</span>
            <span className="tabular-nums">{list.length}</span>
          </div>
          {list.map((row) => (
            <button
              key={row.id}
              type="button"
              className="cc-list-row"
              onClick={() => {
                setView("chat");
                void aui.threads().switchToThread(row.id);
              }}
            >
              <span className="min-w-0 flex-1 truncate text-ink">{row.title || t("untitled")}</span>
              {row.period && <span className="shrink-0 text-ink-3 text-xs">{monthShort(row.period)}</span>}
            </button>
          ))}
        </section>
      ))}
      {next && (
        <button type="button" className="cc-show-more" disabled={busy} onClick={() => void load(next)}>
          {co.showMore}
        </button>
      )}
    </div>
  );
};
