// 定时任务（CC 侧栏「定时任务」，只在 Cowork 出现；用户 2026-10-12：功能同 Pearnly 那边，界面照 Claude）。
//
// 功能逐项对应 Cowork 网页的定时任务页（apps/pearnly_cowork/.../desk/intake-folders.js 与 targets.js）：
//   多台电脑（ADR-021 第 6 批）：文件夹下拉的值用读者编号（「电脑|连接」，`folderValue` 编码，同 Desk 页），读它的电脑被移除的
//   任务标「电脑已移除」；Express 卡片按池的状态写「已连接 / 电脑离线（仍可选）/ 没有能写的电脑 / CC 桌面版未连接」，
//   选定账套后够得到它的电脑都不支持排头的载荷版本时写「CC 桌面版需更新」。跟随公司当年账套（T1）的任务在账套处写
//   「跟随公司当年账套」，结果与运行记录列出实际推往的账套与张数。
//   列表：任务名、目标 ERP、账套、文件夹、每月几号几点、结果（已推送 / 待处理 / 处理中）、文件夹里还没处理的份数、状态；
//         立即运行、待处理的单据、编辑、运行记录、停用 / 启用、删除。列表自己刷新（lib/live.ts：定时任务服务的实时事件
//         cowork_folder_changed 为主，有处理中的每 10 秒、否则每 60 秒兜底，回到前台马上刷）。
//   新建先选公司（主档页「＋」直接是这家），表单只摆公司与「目标 ERP · 本月账套」（ERP 没连上时写状态、给「连接」；几个分店各有
//         账套时选分店），任务跟着公司走（用户 10-20）；公司还没有账套、没记公司的旧任务照旧：目标卡片（MR.ERP / Express，
//         状态与「连接」）→ 两级账套。之后同为 进项销项 → 入账方式（Express 过账类型、
//         MR.ERP 付款方式）→ 存货科目组（要选时）→ 文件夹（下面显示文件夹里现有几份）→ 每月几号几点 → 提醒（打开后
//         最多 3 个提醒时刻：当天 / 前 1–7 天 + 任意时间）→
//         Express 的默认处理；保存前 MR.ERP 的菜单与默认值没定就问一次。
// 接口与 Cowork 页面同一套（pearnly_cowork.folder_intake.api / connections.api / companion.api），权限由服务端判；
// 词条取 Cowork 的术语表（folder_intake.api.terms），不另存翻译。样式只用 globals.css 的 .cc-* 与变量。
//
// 公司主档（ADR-020）：任务表不加字段，属于哪家公司按账套推出来（companies.api.task_overview，一次取全部，不逐家读详情）。
//   ?view=tasks&company=<公司>（公司页「定时任务」标签、会话栏定时任务卡里点任务或「＋」）只看这家，顶上一枚可关掉的公司胶囊；
//   点的那条定位并高亮；主档页「＋」接着打开新建表单，预填覆盖本月的账套（本机绑了文件夹时连同文件夹）。
//   全部任务时按公司分组（侧栏顺序的公司在前，其余按名称），归不到公司的放「其他」；新建统一从公司里建：「＋」先选公司
//   （添加公司对话框里的公司选择），再同主档页「＋」一样预填。换文件夹、删公司都不改任务（避免重跑、重复扣费）。

import {
  AlertCircleIcon,
  ChevronRightIcon,
  ClockIcon,
  ExternalLinkIcon,
  FolderIcon,
  HistoryIcon,
  MoreHorizontalIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
  XIcon,
} from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { DropdownMenu, menuOffset } from "@/components/ui/menu";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Switch } from "@/components/settings";
import { openAddCompany } from "@/components/add-company";
import {
  cachedDetail,
  folderParts,
  folderValue,
  groupTasks,
  lastMonth,
  refreshTaskOverview,
  type TaskSeed,
  taskSeedOf,
  useCompanyNav,
  useTaskOverview,
  watchTaskOverview,
} from "@/lib/companies";
import { knownReaders, useDesktopFeatures } from "@/lib/desktop";
import { getMethod, postMethod, shownError } from "@/lib/frappe";
import { useLiveRefresh } from "@/lib/live";
import { useProjects } from "@/lib/managed-agents/projects";
import { co, t } from "@/i18n";
import { cn } from "@/lib/utils";
import { Marquee } from "@/components/ui/marquee";

const API = "pearnly_cowork.folder_intake.api.";
const CONN = "pearnly_cowork.connections.api.";
const COMPANION = "pearnly_cowork.companion.api.";
// 定时任务服务的实时事件（folder_intake/live.py，载荷 {rule}）：文件状态、运行记录、待处理数有变化时推给负责人
const FOLDER_EVENT = "cowork_folder_changed";

// ── 词条 ────────────────────────────────────────────────────────────────────────────────────────

let termsCache: Record<string, string> | null = null;
let termsLoading: Promise<Record<string, string>> | null = null;

function loadTerms(): Promise<Record<string, string>> {
  if (termsCache) return Promise.resolve(termsCache);
  termsLoading ??= getMethod<Record<string, string>>(API + "terms").then((terms) => (termsCache = terms ?? {}));
  return termsLoading;
}

function useTerms() {
  const [terms, setTerms] = useState(termsCache);
  useEffect(() => {
    if (!terms) void loadTerms().then(setTerms);
  }, [terms]);
  return useCallback(
    (key: string, ...args: Array<string | number>) =>
      (terms?.[key] ?? key).replace(/\{(\d+)\}/g, (_m, i: string) => String(args[Number(i)] ?? "")),
    [terms],
  );
}
type Tr = ReturnType<typeof useTerms>;

// ── 数据 ────────────────────────────────────────────────────────────────────────────────────────

type Rule = {
  name: string; title: string; connection: string; connection_label?: string; enabled: number; adapter: string;
  reader: string; account_set_key: string; account_label?: string; direction: string; posting_kind: string;
  payment: string; root_id: string; root_label?: string; folder: string; run_day: number; run_time: string;
  defaults: Record<string, string>; agent_fresh?: boolean; folder_state?: string | null;
  results?: { entered?: number; attention?: number; moving?: number; account_sets?: AccountSetCount[] };
  // 多台电脑（ADR-021）：reader_id = 读者编号（「电脑|连接」），reader_gone = 读它的电脑已被移除；company = 跟随公司当年账套（T1）
  reader_id?: string; reader_gone?: boolean; company?: string;
  // 读文件夹的电脑名：池里不止一台电脑时服务端才给，列表的文件夹一栏与表单补回的那一项前面带上（第 6 批审查第 14 条）
  reader_label?: string;
  // include_existing：到点处理文件夹里所有还没处理过的（新任务）；remind：运行前提醒，remind_times：提醒时刻（服务端给
  // 生效的那组，没选过是前一天 08:00、14:00）；waiting：文件夹里还没处理的份数（不知道是 null），waiting_partial：数到上限（显示 N+）
  include_existing?: number; remind?: number; remind_times?: RemindTime[]; waiting?: number | null; waiting_partial?: boolean;
};
// 跟随公司当年账套的任务：这些票实际推往了哪份账套、各几张（服务端 `contracts.account_sets`）
type AccountSetCount = { account_set_key: string; account_label: string; tickets: number };
// 每份写成「账套名：张数」（现有词条 cowork.folder.pair），几份之间用分号：账套名本身是「公司 · 年度」，不再用「 · 」连
// （第 6 批审查第 10 条；Desk 定时任务页同一写法）
const accountSetsText = (tr: Tr, rows?: AccountSetCount[]) =>
  (rows ?? []).map((row) => tr("cowork.folder.pair", row.account_label, row.tickets)).join("; ");
// 提醒时刻：运行日前几天（0 = 当天，最多 7 天）+ 曼谷 HH:MM，最多 3 个（同服务端 contracts.remind_times）
type RemindTime = { days_before: number; time: string };
type Connection = { name: string; adapter: string; enabled: number | boolean; label?: string; revision?: number; config?: Record<string, string>; main?: boolean };
// 池的状态（`companion.api.online`，ADR-021）：writers_known = 有能写的电脑（关着也算，能选、排队），computers = 池里有电脑
type PoolState = { online?: boolean; writers_known?: boolean; needs_update?: boolean; computers?: boolean };
type Snapshot = { connections: Record<string, Connection | null>; agent: PoolState | null };
type Group = { value: string; label: string; items: Array<{ value: string; label: string }> };
// counts：文件夹（相对路径，"" 是根目录）里现有几份会被录入的文件，没数过就没有这个键；partial：数到上限；
// table_counts：POS 导出（Excel / CSV）另数，只有推往 Express 的销售任务收它们
type Root = { id: string; label: string; folders?: string[]; counts?: Record<string, number>; table_counts?: Record<string, number>; partial?: boolean };
// reader：读者编号（「电脑|连接」，第 4 批起服务端给；旧服务端没有时退回 connection）
type Reader = { connection: string; reader?: string; device?: string; label: string; version?: string; fresh?: boolean; roots?: Root[] };
const readerId = (r: Pick<Reader, "reader" | "connection">) => r.reader || r.connection;
const ruleFolder = (rule: Rule) => folderValue(rule.reader_id || rule.reader, rule.root_id, rule.folder || "");
type StockGroup = { known?: boolean; chosen?: string; groups?: Array<{ acccod: string; label: string; used_by?: number }> };
type Options = { readers?: Reader[]; accounts?: Array<{ code: string; name: string; income?: boolean }>; departments?: Array<{ code: string; name: string }>; stock_group?: StockGroup };

const ADAPTERS = [
  { adapter: "mrerp", label: "MR.ERP" },
  { adapter: "express", label: "Express" },
];
// 每种 ERP 只认主连接（服务端 `records` 标的 `main`：最早建、启用、不挂客户），与电脑登记、录入入口、LINE、CC 同一条（ADR-021）
const mainOf = (rows: Connection[] | null | undefined, adapter: string) => (rows || []).find((r) => r.adapter === adapter && r.main) || null;
const key = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function snapshotNow(): Promise<Snapshot> {
  const rows = (await getMethod<Connection[]>(CONN + "records", { limit: 100 })) || [];
  const connections: Snapshot["connections"] = {};
  for (const a of ADAPTERS) connections[a.adapter] = mainOf(rows, a.adapter);
  const express = connections.express;
  const agent = express ? await getMethod<PoolState>(COMPANION + "online", { connection: express.name }).catch(() => null) : null;
  return { connections, agent };
}

function describe(adapter: string, s: Snapshot, tr: Tr): { text: string; tone: "ok" | "bad" | "wait" | "" } {
  const c = s.connections[adapter];
  if (!c) return { text: tr("connect.state.notConnected"), tone: "" };
  if (!c.enabled) return { text: tr("connect.state.disabled"), tone: "bad" };
  if (adapter === "mrerp" || s.agent?.online) return { text: tr("connect.state.connected"), tone: "ok" };
  // 能写的电脑都关着：仍可选（排队，开机后写，D4）；池里有电脑却没有一台能写；一台电脑都没有（同 Desk 录入入口）
  if (s.agent?.writers_known) return { text: tr("intake.agent.state.offline"), tone: "wait" };
  return { text: tr(s.agent?.computers ? "intake.agent.state.noWriter" : "intake.agent.state.missing"), tone: "wait" };
}
function ready(adapter: string, s: Snapshot) {
  const c = s.connections[adapter];
  if (!c || !c.enabled) return false;
  return adapter === "mrerp" || Boolean(s.agent?.writers_known ?? s.agent?.online);
}

const days = (tr: Tr) => [
  ...Array.from({ length: 31 }, (_, i) => ({ value: String(i + 1), label: tr("cowork.folder.day", i + 1) })),
  { value: "0", label: tr("cowork.folder.lastDay") },
];
const TIMES = Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`);
const REMIND_MAX = 3;
const DEFAULT_REMIND: RemindTime[] = [{ days_before: 1, time: "08:00" }, { days_before: 1, time: "14:00" }];
// 「添加时间」依次给出还没有的那一个（Desk 空行同一顺序）；当天的要早于运行时间，服务端会拒，所以不拿当天当默认
const REMIND_NEXT: RemindTime[] = [...DEFAULT_REMIND, { days_before: 2, time: "08:00" }, { days_before: 1, time: "18:00" }];
const nextRemind = (rows: RemindTime[]) =>
  REMIND_NEXT.find((c) => !rows.some((r) => r.days_before === c.days_before && r.time === c.time)) ?? REMIND_NEXT[0]!;
const remindDays = (tr: Tr) => Array.from({ length: 8 }, (_, d) => ({
  value: String(d),
  label: d === 0 ? tr("cowork.folder.remind.sameDay") : d === 1 ? tr("cowork.folder.remind.dayBefore") : tr("cowork.folder.remind.daysBefore", d),
}));

/** N 份（数到上限是 N+）。 */
const plus = (n: number, partial?: boolean) => (partial ? `${n}+` : String(n));

function status(rule: Rule, tr: Tr): { text: string; tone: "ok" | "bad" | "wait" | "" } {
  if (!rule.enabled) return { text: tr("cowork.folder.off"), tone: "" };
  // 读文件夹的电脑已被移除：任务留着原电脑，等负责人另选文件夹（不自动换电脑读）
  if (rule.reader_gone) return { text: tr("cowork.folder.readerGone"), tone: "bad" };
  if (!rule.agent_fresh) return { text: tr("cowork.folder.agentStale"), tone: "wait" };
  if (["root_missing", "folder_missing"].includes(rule.folder_state || "")) return { text: tr("cowork.folder.folderMissing"), tone: "bad" };
  return { text: tr("cowork.folder.on"), tone: "ok" };
}

// ── 通用小件（统一样式） ────────────────────────────────────────────────────────────────────────

function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="cc-overlay z-(--cc-z-sheet)" />
        <DialogPrimitive.Content
          data-size={wide ? "lg" : "sm"}
          className="cc-dialog cc-enter cc-modal fixed left-1/2 z-(--cc-z-sheet) flex -translate-x-1/2 -translate-y-1/2 flex-col"
        >
          <div className="cc-dialog-head">
            <DialogPrimitive.Title className="cc-dialog-title">
              <Marquee className="block truncate">{title}</Marquee>
            </DialogPrimitive.Title>
            <DialogPrimitive.Close className="cc-icon-btn" aria-label={t("close")}>
              <XIcon />
            </DialogPrimitive.Close>
          </div>
          <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
          <div className="cc-dialog-body">{children}</div>
          {footer && <div className="cc-dialog-foot">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function Field({ label, hint, children, hidden }: { label: string; hint?: string; children: ReactNode; hidden?: boolean }) {
  if (hidden) return null;
  return (
    <label className="cc-field">
      <span>{label}</span>
      {children}
      {hint && <span className="text-ink-3 text-caption">{hint}</span>}
    </label>
  );
}

type Option = { value: string; label: string };
function Select({ value, options, onChange, disabled, placeholder = "" }: {
  value: string; options: Option[]; onChange: (v: string) => void; disabled?: boolean; placeholder?: string;
}) {
  return (
    <select className="cc-input" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

function Chip({ tone, children }: { tone: string; children: ReactNode }) {
  return (
    <span className="cc-chip" data-tone={tone || undefined}>
      {children}
    </span>
  );
}

function useToast() {
  const [message, setMessage] = useState("");
  const timer = useRef<number>(0);
  const show = useCallback((text: string) => {
    setMessage(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(""), 2600);
  }, []);
  const node = message ? (
    <div className="cc-toast">{message}</div>
  ) : null;
  return { show, node };
}

function Confirm({ text, label, onYes, onClose, danger }: { text: string; label: string; onYes: () => Promise<void> | void; onClose: () => void; danger?: boolean }) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      open
      onClose={onClose}
      title={text}
      footer={
        <>
          <button type="button" className="cc-btn" onClick={onClose}>{t("cancel")}</button>
          <button
            type="button"
            className="cc-btn"
            data-primary={danger ? undefined : ""}
            data-danger={danger ? "" : undefined}
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onYes();
              } finally {
                setBusy(false);
                onClose();
              }
            }}
          >
            {label}
          </button>
        </>
      }
    >
      <span />
    </Modal>
  );
}

// ── 公司 ────────────────────────────────────────────────────────────────────────────────────────

/** 只看这家时的公司名：总览里有（这家有任务）直接用，侧栏里有用侧栏的，都没有读一次这家的详情。 */
function useCompanyTitle(company: string): string {
  const known = useTaskOverview((s) => s.titles[company]) ?? "";
  const listed = useProjects((s) => s.projects.find((p) => p.id === company)?.title) ?? "";
  const [read, setRead] = useState("");
  useEffect(() => {
    setRead("");
    if (!company || known || listed) return;
    let alive = true;
    void cachedDetail(company).then((d) => alive && setRead(d?.company.title ?? ""));
    return () => {
      alive = false;
    };
  }, [company, known, listed]);
  return known || listed || read;
}

/** 新建任务从公司里建：给了公司就按这家预填（同主档页「＋」）；没给先在添加公司对话框里选公司。 */
function useNewTask(open: (seed: TaskSeed | null) => void) {
  const { app, companyFolders, deviceName } = useDesktopFeatures();
  const withPeriod = useProjects((s) => s.period);
  const seedFor = async (company: string) => {
    const detail = await cachedDetail(company);
    useCompanyNav.setState({ taskCompany: company, taskFocus: "" });
    open(detail ? taskSeedOf(detail, withPeriod ? lastMonth() : "", knownReaders(), app && companyFolders, deviceName) : null);
  };
  return (company: string) => (company ? void seedFor(company) : openAddCompany((id) => void seedFor(id), co.chooseCompany));
}

// ── 列表 ────────────────────────────────────────────────────────────────────────────────────────

/** 定时任务页的搜索：任务名、目标账套、文件夹里有这几个字（不分大小写）。 */
export function matchesRule(rule: Pick<Rule, "title" | "account_label" | "folder" | "root_label">, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return [rule.title, rule.account_label, rule.folder, rule.root_label].map((part) => String(part ?? "")).join(" ").toLowerCase().includes(needle);
}

export function ScheduledTasks() {
  const tr = useTerms();
  const toast = useToast();
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [runsOf, setRunsOf] = useState<Rule | null>(null);
  const [confirm, setConfirm] = useState<{ text: string; label: string; danger?: boolean; run: () => Promise<void> } | null>(null);
  // 按公司分组用的总览（一次取全部，不逐家读详情）：只在定时任务页开着时跟着任务事件、回到前台再读
  const companyKind = useProjects((s) => s.kind);
  useEffect(() => (companyKind ? watchTaskOverview() : undefined), [companyKind]);

  const reload = useCallback(async () => {
    try {
      const data = await getMethod<{ rules: Rule[] }>(API + "listing");
      setRules(data?.rules ?? []);
      setError("");
      void refreshTaskOverview();  // 任务建、删、改了：按公司的分组跟着变
    } catch (e) {
      setError(shownError(e));
      // 自动刷新偶尔失败：已显示的列表留着，不闪成空的
      setRules((prev) => prev ?? []);
    }
  }, []);
  useEffect(() => void reload(), [reload]);
  useLiveRefresh(reload, { events: [FOLDER_EVENT], busy: Boolean(rules?.some((r) => (r.results?.moving ?? 0) > 0 && r.agent_fresh)) });

  const dayLabel = (d: number) => days(tr).find((x) => x.value === String(d))?.label ?? "";
  const taskCompany = useCompanyNav((s) => s.taskCompany);
  const focus = useCompanyNav((s) => s.taskFocus);
  const seed = useCompanyNav((s) => s.taskSeed);
  const overview = useTaskOverview();
  const companyTitle = useCompanyTitle(taskCompany);
  const order = useProjects((s) => s.projects).map((p) => p.id);
  const [seeded, setSeeded] = useState<TaskSeed | null>(null);
  const newTask = useNewTask((next) => {
    setSeeded(next);
    setEditing("new");
  });
  // 主档页「＋」：接着打开新建表单（预填账套、文件夹）
  useEffect(() => {
    if (!seed) return;
    setSeeded(seed);
    setEditing("new");
    useCompanyNav.setState({ taskSeed: null });
  }, [seed]);
  // 主档页点的那条：滚到它
  useEffect(() => {
    if (!focus || !rules) return;
    document.querySelector(`[data-task="${CSS.escape(focus)}"]`)?.scrollIntoView({ block: "center" });
  }, [focus, rules]);
  // 只看这家：总览还没取到时先不列（不闪出别家的任务）
  const [search, setSearch] = useState("");
  const byCompany = rules && taskCompany ? (overview.loaded ? rules.filter((rule) => overview.tasks[rule.name] === taskCompany) : null) : rules;
  // 搜索（列表页样式，设计文档 4.5）：任务名、账套、文件夹里有这几个字
  const shown = byCompany && search.trim() ? byCompany.filter((rule) => matchesRule(rule, search)) : byCompany;
  const groups = shown && !taskCompany ? groupTasks(shown, overview.tasks, order, overview.titles) : shown ? [[taskCompany, shown] as [string, Rule[]]] : [];
  const headed = !taskCompany && groups.some(([id]) => id);

  return (
    <div data-slot="cc-scroll" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="cc-page">
        <div className="cc-page-head">
          <h1 className="cc-page-title"><Marquee className="cc-page-title-text">{tr("cowork.folder.title")}</Marquee></h1>
          <button type="button" className="cc-btn-sm" data-primary="" onClick={() => newTask(taskCompany)}>
            <PlusIcon />
            {tr("cowork.folder.new")}
          </button>
        </div>
        {!!byCompany?.length && (
          <label className="cc-page-search">
            <SearchIcon />
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} aria-label={t("search")} placeholder={t("search")} />
          </label>
        )}
        {taskCompany && (
          <div className="mb-(--cc-space-16)">
            <span className="cc-chip">
              {companyTitle}
              <button
                type="button"
                className="cc-chip-x"
                aria-label={t("close")}
                onClick={() => useCompanyNav.setState({ taskCompany: "", taskFocus: "" })}
              >
                <XIcon />
              </button>
            </span>
          </div>
        )}
        {error && <div className="cc-alert mb-(--cc-space-16)">{error}</div>}
        {rules === null || shown === null ? (
          <div className="flex flex-col gap-(--cc-space-8)">
            {[0, 1, 2].map((i) => <div key={i} className="cc-card h-(--cc-task-skeleton-h) animate-cc-pulse" />)}
          </div>
        ) : shown.length === 0 && search.trim() ? (
          <div className="cc-empty-line">{t("noResults")}</div>
        ) : shown.length === 0 ? (
          <div className="cc-empty cc-enter">
            <ClockIcon />
            <button type="button" className="cc-btn" data-primary="" onClick={() => newTask(taskCompany)}>
              <PlusIcon />
              {tr("cowork.folder.new")}
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-(--cc-space-16)">
            {groups.map(([group, rows]) => (
              <section key={group || "other"} className="flex flex-col gap-(--cc-space-8)" data-slot="cc-task-group">
                {headed && <h2 className="cc-section-label" data-flush="">{group ? (overview.titles[group] ?? "") : co.other}</h2>}
            {rows.map((rule) => {
              const s = status(rule, tr);
              const target = ADAPTERS.find((a) => a.adapter === rule.adapter)?.label ?? "";
              return (
                <div
                  key={rule.name}
                  data-task={rule.name}
                  data-selected={focus === rule.name ? "" : undefined}
                  className="cc-list-item cc-enter items-start"
                >
                  <button type="button" className="min-w-0 flex-1 text-start" onClick={() => setEditing(rule)}>
                    <div className="flex flex-wrap items-center gap-(--cc-space-8)">
                      <Marquee className="truncate font-medium text-ink">{rule.title}</Marquee>
                      <Chip tone={s.tone}>{s.text}</Chip>
                    </div>
                    <div className="mt-(--cc-space-4) flex flex-wrap items-center gap-x-(--cc-space-12) gap-y-(--cc-space-4) text-ink-3 text-body">
                      <span>{[target, rule.company ? tr("cowork.folder.followCompany") : rule.account_label].filter(Boolean).join(" · ")}</span>
                      <span className="flex items-center gap-(--cc-space-4)">
                        <FolderIcon className="size-(--cc-icon-xs)" />
                        {[rule.reader_label, rule.folder || rule.root_label || ""].filter(Boolean).join(" · ")}
                      </span>
                      <span className="flex items-center gap-(--cc-space-4)">
                        <ClockIcon className="size-(--cc-icon-xs)" />
                        {`${dayLabel(rule.run_day)} ${rule.run_time || ""}`}
                      </span>
                    </div>
                    <div className="mt-(--cc-space-4) text-ink-2 text-body">
                      {tr("cowork.folder.counts", rule.results?.entered || 0, rule.results?.attention || 0)}
                      {rule.results?.moving ? ` · ${tr("cowork.folder.moving", rule.results.moving)}` : ""}
                    </div>
                    {rule.results?.account_sets?.length ? (
                      <div className="mt-(--cc-space-2) text-ink-3 text-body">{accountSetsText(tr, rule.results.account_sets)}</div>
                    ) : null}
                    {rule.enabled && rule.waiting ? (
                      <div className="mt-(--cc-space-2) text-ink-3 text-body">{tr("cowork.folder.pending", plus(rule.waiting, rule.waiting_partial))}</div>
                    ) : null}
                  </button>
                  {rule.enabled ? (
                    <button
                      type="button"
                      className="cc-row-btn cc-tap"
                      title={tr("cowork.folder.runNow")}
                      aria-label={tr("cowork.folder.runNow")}
                      onClick={async () => {
                        await postMethod(API + "run_now", { name: rule.name });
                        toast.show(tr("cowork.folder.runRequested"));
                        await reload();
                      }}
                    >
                      <PlayIcon />
                    </button>
                  ) : null}
                  <DropdownMenu.Root>
                    <DropdownMenu.Trigger className="cc-row-btn cc-tap" aria-label={t("more")}>
                      <MoreHorizontalIcon />
                    </DropdownMenu.Trigger>
                    <DropdownMenu.Portal>
                      <DropdownMenu.Content align="end" sideOffset={menuOffset()} className="cc-menu">
                        <DropdownMenu.Item className="cc-menu-item" onSelect={() => setEditing(rule)}>
                          <PencilIcon />
                          {t("edit")}
                        </DropdownMenu.Item>
                        <DropdownMenu.Item className="cc-menu-item" onSelect={() => setRunsOf(rule)}>
                          <HistoryIcon />
                          {tr("cowork.folder.runs")}
                        </DropdownMenu.Item>
                        {rule.results?.attention ? (
                          <DropdownMenu.Item asChild className="cc-menu-item">
                            <a href="/desk/pearnly-intake?push_state=failed" target="_blank" rel="noopener">
                              <AlertCircleIcon />
                              <span className="flex-1">{tr("cowork.folder.viewAttention")}</span>
                              <ExternalLinkIcon className="text-ink-3" />
                            </a>
                          </DropdownMenu.Item>
                        ) : null}
                        <DropdownMenu.Item
                          className="cc-menu-item"
                          onSelect={() =>
                            setConfirm({
                              // 新任务恢复后处理文件夹里所有还没处理过的（含停用期间放进来的）；老任务只收恢复之后放入的
                              text: tr(rule.enabled ? "cowork.folder.disableConfirm"
                                : rule.include_existing ? "cowork.folder.enableConfirmAll" : "cowork.folder.enableConfirm"),
                              label: tr(rule.enabled ? "cowork.folder.disable" : "cowork.folder.enable"),
                              run: async () => {
                                await postMethod(API + "set_enabled", { name: rule.name, enabled: rule.enabled ? 0 : 1 });
                                await reload();
                              },
                            })
                          }
                        >
                          {rule.enabled ? <PauseIcon /> : <PlayIcon />}
                          {tr(rule.enabled ? "cowork.folder.disable" : "cowork.folder.enable")}
                        </DropdownMenu.Item>
                        <DropdownMenu.Separator className="cc-menu-sep" />
                        <DropdownMenu.Item
                          className="cc-menu-item"
                          data-danger=""
                          onSelect={() =>
                            setConfirm({
                              text: tr("Delete {0} item permanently?", 1),
                              label: t("delete"),
                              danger: true,
                              run: async () => {
                                await postMethod(API + "delete", { names: JSON.stringify([rule.name]) });
                                await reload();
                              },
                            })
                          }
                        >
                          <TrashIcon />
                          {t("delete")}
                        </DropdownMenu.Item>
                      </DropdownMenu.Content>
                    </DropdownMenu.Portal>
                  </DropdownMenu.Root>
                </div>
              );
            })}
              </section>
            ))}
          </div>
        )}
      </div>
      {editing && (
        <RuleDialog
          tr={tr}
          rule={editing === "new" ? null : editing}
          seed={editing === "new" ? seeded : null}
          onClose={() => {
            setEditing(null);
            setSeeded(null);
          }}
          onSaved={async () => {
            setEditing(null);
            toast.show(tr("Saved"));
            await reload();
          }}
        />
      )}
      {runsOf && <RunsDialog tr={tr} rule={runsOf} onClose={() => setRunsOf(null)} />}
      {confirm && <Confirm text={confirm.text} label={confirm.label} danger={confirm.danger} onYes={confirm.run} onClose={() => setConfirm(null)} />}
      {toast.node}
    </div>
  );
}

// ── 运行记录（同 Cowork：每次扫了几份、传了几份；已推送 / 待处理；还在路上的写走到哪一步） ─────────────────

type Run = {
  started_at: string; trigger?: string; note?: string; found?: number; new_files?: number; skipped?: number;
  upload_failed?: number; results?: { entered?: number; attention?: number; account_sets?: AccountSetCount[] };
  states?: Record<string, number>;
};
const STATES = ["pushing", "recognized", "submitted", "source_ready", "uploaded", "registered", "not_invoice"];

function RunsDialog({ tr, rule, onClose }: { tr: Tr; rule: Rule; onClose: () => void }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  useEffect(() => {
    void getMethod<{ runs: Run[] }>(API + "runs", { name: rule.name }).then((v) => setRuns(v?.runs ?? []), () => setRuns([]));
  }, [rule.name]);
  return (
    <Modal open onClose={onClose} title={`${tr("cowork.folder.runs")} · ${rule.title}`}>
      {runs === null ? (
        <div className="cc-empty-line">…</div>
      ) : runs.length === 0 ? (
        <div className="cc-empty-line">{tr("cowork.folder.noRuns")}</div>
      ) : (
        <div className="flex flex-col divide-y divide-line">
          {runs.map((run, i) => {
            const { entered = 0, attention = 0 } = run.results || {};
            const states = [
              ...(entered || attention ? [tr("cowork.folder.counts", entered, attention)] : []),
              ...STATES.filter((n) => run.states?.[n]).map((n) => `${tr(`cowork.folder.state.${n}`)} ${run.states![n]}`),
            ].join(" · ");
            const sets = accountSetsText(tr, run.results?.account_sets);
            const missing = ["root_missing", "folder_missing"].includes(run.note || "") ? ` · ${tr("cowork.folder.folderMissing")}` : "";
            return (
              <div key={i} className="py-(--cc-run-row-py) text-body">
                <div className="text-ink">
                  {new Date(run.started_at.replace(" ", "T")).toLocaleString()} · {tr(`cowork.folder.trigger.${run.trigger || "schedule"}`)}
                  {missing}
                </div>
                <div className="text-ink-3 text-caption">
                  {tr("cowork.folder.runScan", run.found || 0, run.new_files || 0, run.skipped || 0, run.upload_failed || 0)}
                  {states ? ` · ${states}` : ""}
                </div>
                {sets && <div className="text-ink-3 text-caption">{sets}</div>}
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}

// ── 新建 / 编辑 ────────────────────────────────────────────────────────────────────────────────

const POLL = { fast_ms: 800, fast_for_ms: 10000, slow_ms: 2500, give_up_ms: 330000 };

function defaultKey(c: Connection | null) {
  const config = c?.config || {};
  if (c?.adapter === "mrerp") return config.comidyear && config.seldb ? `${config.comidyear}:${config.seldb}` : "";
  return config.account_set || config.account_dir || "";
}
const norm = (v: string) => String(v || "").replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
function remembered(name: string) {
  try {
    return name ? localStorage.getItem(`pearnly.intake.account_set.${name}`) || "" : "";
  } catch {
    return "";
  }
}

/** 从公司建的任务：公司与目标 ERP（目标 · 账套名）只摆出来；ERP 没连上时写状态、给「连接」。 */
function FromCompany({ tr, company, target, state, onConnect }: {
  tr: Tr; company: string; target: string; state: { text: string; tone: string } | null; onConnect?: () => void;
}) {
  // 只摆值，不是表单控件：用 div（Field 的 label 包着按钮时，点标签文字也会按下「连接」）
  return (
    <div className="grid grid-cols-1 gap-(--cc-space-16) md:grid-cols-2">
      <div className="cc-field">
        <span>{tr("Company")}</span>
        <span className="truncate text-ink">{company}</span>
      </div>
      <div className="cc-field">
        <span>{tr("intake.target.title")}</span>
        <span className="truncate text-ink">{target}</span>
        {onConnect && (
          <span className="flex min-w-0 items-center gap-(--cc-space-8)">
            {state && <span className={cn("min-w-0 truncate text-caption", state.tone === "bad" ? "text-danger" : "text-ink-3")}>{state.text}</span>}
            <button type="button" className="cc-btn shrink-0" onClick={onConnect}>{tr("Connect")}</button>
          </span>
        )}
      </div>
    </div>
  );
}

function RuleDialog({ tr, rule, seed = null, onClose, onSaved }: {
  tr: Tr; rule: Rule | null; seed?: TaskSeed | null; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot>({ connections: {}, agent: null });
  const [target, setTarget] = useState<string>(rule?.adapter || seed?.adapter || "");
  const [title, setTitle] = useState(rule?.title || "");
  const [groups, setGroups] = useState<Group[]>([]);
  const [catalogNote, setCatalogNote] = useState("");
  const [year, setYear] = useState("");
  const [accountSet, setAccountSet] = useState(rule?.account_set_key || seed?.account_set_key || "");
  // 从公司建（新建带了公司）或跟着公司走的旧任务：目标 ERP 与账套由公司定，表单只摆出来、不让再选（用户 10-20：选了公司又要选
  // 目标 ERP、再选公司账套不合理）。服务端按所选账套记下公司与分店，推送时按单据日期选这家公司的账套（`folder_intake.follow`）
  const fromCompany = rule ? Boolean(rule.company) : Boolean(seed?.company);
  const seedBranches = rule ? [] : seed?.branches ?? [];
  const [branch, setBranch] = useState(seedBranches.find((b) => b.account_set_key === seed?.account_set_key)?.branch ?? "");
  const companyTitle = useCompanyTitle(rule?.company || seed?.company || "");
  const [direction, setDirection] = useState(rule?.direction || "purchase");
  const [postingKind, setPostingKind] = useState(rule?.posting_kind || "service");
  const [payment, setPayment] = useState(rule?.payment || "");
  const [stockGroup, setStockGroup] = useState("");
  const [folder, setFolder] = useState(rule ? ruleFolder(rule) : seed?.folder || "");
  const [runDay, setRunDay] = useState(String(rule?.run_day ?? 1));
  const [runTime, setRunTime] = useState(rule?.run_time || "22:00");
  const [remind, setRemind] = useState(Boolean(rule?.remind));
  const [remindTimes, setRemindTimes] = useState<RemindTime[]>(rule?.remind_times?.length ? rule.remind_times : DEFAULT_REMIND);
  const setRemindTime = (index: number, patch: Partial<RemindTime>) =>
    setRemindTimes((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const [defaults, setDefaults] = useState<Record<string, string>>(rule?.defaults || {});
  const [options, setOptions] = useState<Options | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [menuAsk, setMenuAsk] = useState<null | ((ok: boolean) => void)>(null);
  const [menuView, setMenuView] = useState<MenuView | null>(null);
  const [connectOpen, setConnectOpen] = useState<string | null>(null);
  const catalogRun = useRef(0);
  const optionsRun = useRef(0);

  const express = target === "express";
  const connection: Connection | null = rule ? { name: rule.connection, adapter: rule.adapter, enabled: 1 } : target ? snapshot.connections[target] ?? null : null;

  const refreshSnapshot = useCallback(async () => setSnapshot(await snapshotNow().catch(() => ({ connections: {}, agent: null }))), []);
  useEffect(() => void refreshSnapshot(), [refreshSnapshot]);

  // 两级账套目录（同 Cowork：用服务端存着的那份；还没有时让助手 / 服务端读一次再给）
  const loadCatalog = useCallback(async () => {
    if (!connection) return;
    const run = ++catalogRun.current;
    const alive = () => run === catalogRun.current;
    const apply = (data: { groups?: Group[]; status?: string; error_code?: string | null }, loading: boolean, code?: string | null) => {
      if (!alive()) return;
      const list = data.groups || [];
      setGroups(list);
      const err = code ?? data.error_code;
      const label = ADAPTERS.find((a) => a.adapter === target)?.label || "";
      const text = loading ? tr("intake.catalog.loading", label)
        : err ? (tr(`intake.catalog.${err}`) === `intake.catalog.${err}` ? tr("intake.catalog.ERR_UNEXPECTED") : tr(`intake.catalog.${err}`))
        : !list.length ? tr("intake.catalog.ERR_ACCOUNT_SET_EMPTY") : "";
      setCatalogNote(text);
      let found: { group: Group; item: Option } | null = null;
      for (const k of [accountSet, remembered(connection.name), defaultKey(connection)]) {
        for (const g of k ? list : []) {
          const item = g.items.find((row) => norm(row.value) === norm(k));
          if (item && !found) found = { group: g, item };
        }
      }
      if (found) {
        setYear(found.group.value);
        setAccountSet(found.item.value);
      } else if (list.length === 1) setYear(list[0]!.value);
    };
    const read = () => getMethod<{ groups?: Group[]; status?: string; error_code?: string; collector_available?: boolean; revision?: number }>(CONN + "targets", { name: connection.name });
    let data = await read().catch(() => ({ groups: [], status: "missing", collector_available: false }) as never);
    if (!alive()) return;
    const empty = !(data.groups || []).length && data.status === "missing";
    if (!data.collector_available || !empty) return apply(data, false);
    apply(data, true);
    const request = await postMethod<{ status: string; request_id: string; result_revision?: number; error_code?: string }>(CONN + "request_refresh", { name: connection.name, request_key: key() });
    const started = Date.now();
    let st = request;
    while (alive()) {
      if (st.status === "succeeded") {
        data = await read();
        if (Number(data.revision) >= Number(st.result_revision)) return apply(data, false);
      } else if (st.status === "failed" || st.status === "superseded") return apply(data, false, st.error_code || "ERR_UNEXPECTED");
      const elapsed = Date.now() - started;
      if (elapsed > POLL.give_up_ms) return apply(data, false, "ERR_TIMEOUT");
      await wait(elapsed < POLL.fast_for_ms ? POLL.fast_ms : POLL.slow_ms);
      if (!alive()) return;
      st = await getMethod(CONN + "refresh_status", { name: connection.name, request_id: request.request_id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection?.name, target]);

  const loadOptions = useCallback(async () => {
    if (!connection) return;
    const run = ++optionsRun.current;
    const k = express ? accountSet || undefined : undefined;
    const result = await getMethod<Options>(API + "options", { connection: connection.name, account_set_key: k }).catch(() => null);
    if (run !== optionsRun.current || !result) return;
    setOptions(result);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection?.name, express, accountSet]);

  useEffect(() => {
    if (!fromCompany && target && (rule || ready(target, snapshot))) void loadCatalog();
  }, [fromCompany, target, snapshot, rule, loadCatalog]);
  useEffect(() => {
    if (target) void loadOptions();
  }, [target, accountSet, loadOptions]);
  // 选了 Express 账套就请小助手读这份账套的主档（同「新建录入」；打开已有任务不重复请求）
  useEffect(() => {
    if (express && connection && accountSet && accountSet !== rule?.account_set_key) {
      void postMethod(CONN + "request_refresh", { name: connection.name, request_key: key(), account_set_key: accountSet }).catch(() => null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [express, accountSet]);

  // 选定的 Express 账套：够得到它的电脑都不支持排头要的载荷版本（`api.online` 按账套问，同推送记录上的
  // payload_version_outdated）→ 账套下面写「CC 桌面版需更新」，仍可选
  const [needsUpdate, setNeedsUpdate] = useState(false);
  useEffect(() => {
    setNeedsUpdate(false);
    if (!express || !connection || !accountSet) return;
    let alive = true;
    void getMethod<PoolState>(COMPANION + "online", { connection: connection.name, account_set_key: accountSet })
      .then((found) => alive && setNeedsUpdate(Boolean(found?.needs_update)), () => undefined);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [express, connection?.name, accountSet]);
  // 「跟随公司当年账套」只写在列表与主档页的任务行：表单上看的是存下的旧任务、不是当前选的账套，写了会和保存结果对不上
  // （第 6 批审查第 9 条）；表单只提示「CC 桌面版需更新」
  const accountHint = needsUpdate ? tr("intake.agent.state.needsUpdate") : undefined;

  const yearItems = groups.find((g) => g.value === year)?.items ?? [];
  useEffect(() => {
    if (yearItems.length === 1 && !yearItems.some((i) => i.value === accountSet)) setAccountSet(yearItems[0]!.value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, groups]);

  const purchase = direction === "purchase";
  const stock = postingKind === "stock";
  const sg = options?.stock_group;
  const needsStockGroup = express && stock && Boolean(sg?.known && !sg.chosen && (sg.groups || []).length >= 2);
  const paymentOptions: Option[] = [...(purchase ? [] : [{ value: "cash", label: tr("Cash") }]), { value: "credit", label: tr("Credit") }];
  useEffect(() => {
    if (!express && target && !paymentOptions.some((o) => o.value === payment)) setPayment(paymentOptions.length === 1 ? paymentOptions[0]!.value : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [direction, target]);

  // 文件夹：能读文件夹的电脑允许读取的根目录与其下文件夹，值是读者编号 + 根目录编号 + 相对路径（`folderValue`）。
  // 池里不止一台电脑有根目录时前面带电脑名（几台都映射了 S 盘也分得清是哪台的）
  const readers = options?.readers || [];
  const many = readers.filter((r) => (r.roots || []).length).length > 1;
  const folders: Option[] = readers.flatMap((r) =>
    (r.roots || []).flatMap((item) => [
      { value: folderValue(readerId(r), item.id), label: (many ? `${r.label} · ` : "") + item.label },
      ...(item.folders || []).map((name) => ({ value: folderValue(readerId(r), item.id, name), label: `${many ? `${r.label} · ` : ""}${item.label}\\${name}` })),
    ]),
  );
  const saved = rule ? ruleFolder(rule) : null;
  if (saved && !folders.some((f) => f.value === saved)) {
    // 电脑这会儿没报上来、或读它的电脑已被移除：原来的文件夹照样摆着（编辑时不被清空），移除了的写明
    const label = [rule!.reader_label, [rule!.root_label, rule!.folder].filter(Boolean).join("\\") || rule!.folder]
      .filter(Boolean).join(" · ");
    folders.unshift({ value: saved, label: rule!.reader_gone ? `${tr("cowork.folder.readerGone")} · ${label}` : label });
  }
  const unpaired = Boolean(target) && Boolean(options) && !readers.some((r) => r.version);
  const folderHint = !options ? "cowork.folder.folderHint" : unpaired ? "cowork.folder.noRootsAny"
    : !readers.some((r) => r.fresh) ? "cowork.folder.agentStaleHint"
    : !folders.length ? (express ? "cowork.folder.noRoots" : "cowork.folder.noRootsAny") : "cowork.folder.folderHint";
  // 选中的文件夹现有几份（小助手随文件夹上报数好的）；编辑没换文件夹时改说这条任务还没处理的份数（pendingHint）。
  // 不知道、0 份都不显示
  const pendingHint = Boolean(rule) && folder === saved;
  const folderCount = ((): string => {
    if (!folder) return "";
    if (rule && pendingHint) return rule.enabled && rule.waiting ? plus(rule.waiting, rule.waiting_partial) : "";
    const { reader, root_id: rootId, folder: path } = folderParts(folder);
    const root = readers.find((r) => readerId(r) === reader)?.roots?.find((item) => item.id === rootId);
    if (!root?.counts) return "";
    const pick = (counts?: Record<string, number>) =>
      counts ? (counts[path] ?? Object.entries(counts).find(([k]) => k.toLowerCase() === path.toLowerCase())?.[1] ?? 0) : 0;
    // 推往 Express 的销售任务也收 POS 导出（同 Desk 表单）：连它们一起数
    const n = pick(root.counts) + (express && direction === "sales" ? pick(root.table_counts) : 0);
    return n > 0 ? plus(n, root.partial) : "";
  })();

  // 默认处理只摆这条任务用得到的项（同服务端 contracts.applicable）；MR.ERP 整段不摆。销项的科目是收入科目，只列收入类
  const sales = direction === "sales";
  const showDefault: Record<string, boolean> = {
    payment: purchase, vat: purchase, account: (purchase && !stock) || sales, item: stock, department: Boolean(options?.departments?.length),
  };
  const accountRows = (options?.accounts || []).filter((r) => !sales || r.income);
  // 换了方向后原来选的科目不在可选里（费用科目留到了销项）：不带去保存
  const accountKept = (code: string) => !options?.accounts || accountRows.some((r) => r.code === code);
  const setDefault = (kind: string, value: string) => setDefaults((d) => ({ ...d, [kind]: value }));

  const canSave = Boolean(connection && title.trim() && accountSet && direction && folder && runDay && runTime &&
    (express ? postingKind : payment) && (!needsStockGroup || stockGroup) &&
    (!remind || remindTimes.every((row) => /^\d{2}:\d{2}$/.test(row.time))));

  async function save() {
    if (!connection) return;
    setBusy(true);
    setError("");
    try {
      if (needsStockGroup) {
        await postMethod(CONN + "choose_stock_acc_group", { name: connection.name, account_set_key: accountSet, acccod: stockGroup });
      }
      // MR.ERP 的单据菜单与默认值：没定过就问一次（同「新建录入」提交前那个弹窗），之后自动推送照用
      if (!express && !(await mrerpMenuReady(connection, accountSet, direction, setMenuView, setMenuAsk))) return;
      const chosen: Record<string, string> = {};
      if (express) for (const kind of Object.keys(showDefault)) {
        if (showDefault[kind] && defaults[kind] && (kind !== "account" || accountKept(defaults[kind]!))) chosen[kind] = defaults[kind]!;
      }
      const { reader, root_id, folder: path } = folderParts(folder);
      await postMethod(API + "save", {
        values: JSON.stringify({
          name: rule?.name || null, title: title.trim(), connection: connection.name, reader, account_set_key: accountSet,
          direction, posting_kind: express ? postingKind : "", payment: express ? "" : payment, root_id,
          folder: path, run_day: Number(runDay), run_time: runTime, defaults: chosen, remind: remind ? 1 : 0,
          ...(remind ? { remind_times: remindTimes } : {}),
        }),
      });
      await onSaved();
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  }

  const enabled = Boolean(target);
  return (
    <>
      <Modal
        open
        wide
        onClose={onClose}
        title={rule ? rule.title : tr("cowork.folder.new")}
        footer={
          <>
            {error && <span className="me-auto text-danger text-body">{error}</span>}
            <button type="button" className="cc-btn" onClick={onClose}>{t("cancel")}</button>
            <button type="button" className="cc-btn" data-primary="" disabled={!canSave || busy} onClick={() => void save()}>
              {tr("Save")}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-(--cc-space-16) pb-(--cc-space-8)">
          <Field label={tr("cowork.folder.field.title")}>
            <input className="cc-input" value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>

          {fromCompany ? (
            <FromCompany
              tr={tr}
              company={companyTitle}
              target={[ADAPTERS.find((a) => a.adapter === target)?.label ?? "", rule ? tr("cowork.folder.followCompany") : seedBranches.find((b) => b.branch === branch)?.account_label ?? seed?.account_label ?? ""]
                .filter(Boolean).join(" · ")}
              state={rule ? null : describe(target, snapshot, tr)}
              onConnect={!rule && !ready(target, snapshot) ? () => setConnectOpen(target) : undefined}
            />
          ) : (
            <div className="cc-field">
              <span>{tr("intake.target.title")}</span>
              <div className="grid grid-cols-1 gap-(--cc-space-8) md:grid-cols-2">
                {ADAPTERS.map((a) => {
                  const state = rule ? null : describe(a.adapter, snapshot, tr);
                  const ok = rule ? rule.adapter === a.adapter : ready(a.adapter, snapshot);
                  const picked = target === a.adapter;
                  // 卡片的内边距在选择按钮里：整张卡都能点，手机点按区至少 44（同对话里的文件卡，按钮排查 H11；第 11 轮复量附录 #83）
                  return (
                    <div key={a.adapter} className="cc-card flex items-center justify-between gap-(--cc-space-8)" data-selected={picked || undefined}>
                      <button
                        type="button"
                        className={cn(
                          "cc-tap flex min-w-0 flex-1 flex-col items-start justify-center self-stretch rounded-md px-(--cc-space-12) py-(--cc-target-card-py) text-start disabled:cursor-default",
                          !picked && (rule || !ok) && "opacity-(--cc-disabled-opacity)",
                        )}
                        disabled={Boolean(rule) || !ok}
                        onClick={() => {
                          setTarget(a.adapter);
                          setGroups([]); setYear(""); setAccountSet(""); setFolder(""); setOptions(null); setDefaults({});
                        }}
                      >
                        <span className="font-medium text-ink">{a.label}</span>
                        {state && <span className={cn("text-caption", state.tone === "ok" ? "text-success" : state.tone === "bad" ? "text-danger" : "text-ink-3")}>{state.text}</span>}
                      </button>
                      {!rule && !ok && (
                        <button type="button" className="cc-btn me-(--cc-space-12)" onClick={() => setConnectOpen(a.adapter)}>
                          {tr("Connect")}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 gap-(--cc-space-16) md:grid-cols-2">
            {seedBranches.length > 1 && (
              <Field label={co.branch}>
                <Select
                  value={branch}
                  options={seedBranches.map((b) => ({ value: b.branch, label: b.label }))}
                  onChange={(v) => {
                    const picked = seedBranches.find((b) => b.branch === v);
                    setBranch(v);
                    if (picked) setAccountSet(picked.account_set_key);
                  }}
                />
              </Field>
            )}
            {!fromCompany && (
              <>
                <Field label={express ? tr("intake.target.year") : tr("Company")} hint={catalogNote}>
                  <Select value={year} disabled={!enabled || !groups.length} options={groups.map((g) => ({ value: g.value, label: g.label }))} onChange={(v) => { setYear(v); setAccountSet(""); }} />
                </Field>
                <Field label={express ? tr("intake.target.accountSet") : tr("intake.target.year")} hint={express ? accountHint : undefined}>
                  <Select value={accountSet} disabled={!enabled || !yearItems.length} options={yearItems} onChange={setAccountSet} />
                </Field>
              </>
            )}
            <Field label={tr("intake.field.direction")}>
              <Select value={direction} disabled={!enabled} onChange={setDirection} options={[{ value: "purchase", label: tr("intake.dir.purchase") }, { value: "sales", label: tr("intake.dir.sales") }]} />
            </Field>
            {express ? (
              <Field label={tr("Posting Kind")}>
                <Select value={postingKind} disabled={!enabled} onChange={setPostingKind} options={[{ value: "service", label: tr("Service") }, { value: "stock", label: tr("Stock") }]} />
              </Field>
            ) : (
              <Field label={tr("Payment Method")} hidden={!target}>
                <Select value={payment} disabled={!enabled} onChange={setPayment} options={paymentOptions} />
              </Field>
            )}
            <Field label={tr("Inventory account group")} hidden={!needsStockGroup} hint={tr("cowork.folder.stock_group_needed")}>
              <Select value={stockGroup} onChange={setStockGroup} options={(sg?.groups || []).map((g) => ({ value: g.acccod, label: [g.label, typeof g.used_by === "number" ? tr("used by {0} items", g.used_by) : ""].filter(Boolean).join(" · ") }))} />
            </Field>
          </div>

          <Field
            label={tr("cowork.folder.field.folder")}
            hint={folderCount ? tr(pendingHint ? "cowork.folder.pending" : "cowork.folder.waiting", folderCount) : tr(folderHint)}
          >
            <Select value={folder} disabled={!enabled || !folders.length} options={folders} onChange={setFolder} />
          </Field>
          {unpaired && !rule && (
            <button type="button" className="cc-btn self-start" onClick={() => setConnectOpen("helper")}>
              {tr("cowork.folder.connectHelper")}
            </button>
          )}

          <div className="grid grid-cols-2 gap-(--cc-space-16)">
            <Field label={tr("cowork.folder.field.day")}>
              <Select value={runDay} disabled={!enabled} options={days(tr)} onChange={setRunDay} />
            </Field>
            <Field label={tr("cowork.folder.field.time")}>
              <Select value={runTime} disabled={!enabled} options={TIMES.map((v) => ({ value: v, label: v }))} onChange={setRunTime} />
            </Field>
          </div>

          <div className="flex items-center justify-between gap-(--cc-space-12) text-ink-2 text-body">
            <span>{tr("cowork.folder.field.remind")}</span>
            <Switch checked={remind} disabled={!enabled} label={tr("cowork.folder.field.remind")} onChange={setRemind} />
          </div>
          {remind && (
            <div className="flex flex-col gap-(--cc-space-8)">
              {remindTimes.map((row, i) => (
                <div key={i} className="flex items-center gap-(--cc-space-8)">
                  <select
                    className="cc-input"
                    aria-label={tr("cowork.folder.field.remindDay")}
                    value={String(row.days_before)}
                    disabled={!enabled}
                    onChange={(e) => setRemindTime(i, { days_before: Number(e.target.value) })}
                  >
                    {remindDays(tr).map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <input
                    type="time"
                    step={60}
                    className="cc-input"
                    aria-label={tr("cowork.folder.field.remindTime")}
                    value={row.time}
                    disabled={!enabled}
                    onChange={(e) => setRemindTime(i, { time: e.target.value.slice(0, 5) })}
                  />
                  {remindTimes.length > 1 && (
                    <button type="button" className="cc-icon-btn shrink-0" aria-label={t("removeFile")} onClick={() => setRemindTimes((rows) => rows.filter((_r, j) => j !== i))}>
                      <XIcon />
                    </button>
                  )}
                </div>
              ))}
              {remindTimes.length < REMIND_MAX && (
                <button
                  type="button"
                  className="cc-btn self-start"
                  disabled={!enabled}
                  onClick={() => setRemindTimes((rows) => [...rows, nextRemind(rows)])}
                >
                  <PlusIcon />
                  {tr("cowork.folder.remind.add")}
                </button>
              )}
            </div>
          )}

          {express && (
            <details className="cc-card px-(--cc-space-16) py-(--cc-space-12)" open={Object.keys(defaults).length > 0}>
              <summary className="cc-disclosure">
                <ChevronRightIcon aria-hidden />
                {tr("cowork.folder.section.defaults")}
              </summary>
              <div className="mt-(--cc-space-12) grid grid-cols-1 gap-(--cc-space-16) md:grid-cols-2">
                <Field label={tr("cowork.folder.default.payment")} hidden={!showDefault.payment}>
                  <Select value={defaults.payment || ""} onChange={(v) => setDefault("payment", v)} options={[{ value: "credit", label: tr("Credit") }, { value: "cash", label: tr("Cash") }]} />
                </Field>
                <Field label={tr("cowork.folder.default.vat")} hidden={!showDefault.vat}>
                  <Select value={defaults.vat || ""} onChange={(v) => setDefault("vat", v)} options={[{ value: "claim", label: tr("cowork.folder.vat.claim") }, { value: "expense", label: tr("cowork.folder.vat.expense") }]} />
                </Field>
                <Field label={tr("cowork.folder.default.item")} hidden={!showDefault.item}>
                  <Select value={defaults.item || ""} onChange={(v) => setDefault("item", v)} options={[{ value: "__new__", label: tr("cowork.folder.item.new") }, { value: "ask", label: tr("cowork.folder.item.ask") }]} />
                </Field>
                <Field label={sales ? tr("cowork.folder.default.income") : tr("cowork.folder.default.account")} hidden={!showDefault.account}>
                  <Select value={accountKept(defaults.account || "") ? defaults.account || "" : ""} onChange={(v) => setDefault("account", v)} options={accountRows.map((r) => ({ value: r.code, label: `${r.code} · ${r.name}` }))} />
                </Field>
                <Field label={tr("cowork.folder.default.department")} hidden={!showDefault.department}>
                  <Select value={defaults.department || ""} onChange={(v) => setDefault("department", v)} options={(options?.departments || []).map((r) => ({ value: r.code, label: `${r.code} · ${r.name}` }))} />
                </Field>
              </div>
            </details>
          )}
        </div>
      </Modal>
      {menuView && menuAsk && connection && (
        <MenuDialog tr={tr} view={menuView} direction={direction} connection={connection} accountSet={accountSet}
          onDone={(ok) => { const resolve = menuAsk; setMenuAsk(null); setMenuView(null); resolve(ok); }} />
      )}
      {connectOpen && (
        <ConnectDialog
          tr={tr}
          kind={connectOpen}
          connection={connectOpen === "helper" ? connection : snapshot.connections[connectOpen] ?? null}
          onClose={() => {
            setConnectOpen(null);
            void refreshSnapshot();
            void loadOptions();
          }}
        />
      )}
    </>
  );
}

// ── MR.ERP 导入菜单与主档默认值：每个账套每类选一次（同「新建录入」提交前那个弹窗） ──────────────────

type MenuView = {
  known?: boolean; observed_at?: string; error_at?: string;
  kinds?: Record<string, { needed?: boolean; options: Array<{ idmenu: string; label?: string }> }>;
  defaults?: { known?: boolean; fields?: Record<string, { needed?: boolean; options: Array<{ code: string; name?: string }> }> };
};
const MENU_KINDS: Record<string, string[]> = { purchase: ["purchase", "purchase_expense"], sales: ["sales_credit", "sales_cash"] };
const DEFAULT_FIELDS: Record<string, string[]> = {
  sales: ["branch", "dept", "job", "store", "employee", "area", "delivery", "customer_type", "ar_account", "bank_account", "product_group", "unit"],
  purchase: ["branch", "dept", "job", "store", "employee", "supplier_type", "ap_account", "product_group", "unit", "expense_account"],
};
const menuNeeded = (v: MenuView | null, dir: string) => (MENU_KINDS[dir] || []).filter((k) => v?.kinds?.[k]?.needed);
const defaultNeeded = (v: MenuView | null, dir: string) => (DEFAULT_FIELDS[dir] || []).filter((f) => v?.defaults?.fields?.[f]?.needed);

async function mrerpMenu(connection: Connection, accountSet: string, fresh = false): Promise<MenuView | null> {
  const args = { name: connection.name, account_set_key: accountSet };
  const read = () => getMethod<MenuView>(CONN + "mrerp_menu", args).catch(() => null);
  let view = await read();
  if (!view || (view.known && view.defaults?.known && !fresh)) return view;
  const request = await postMethod<{ requested_at: string }>(CONN + "request_mrerp_menu", args).catch(() => null);
  if (!request) return view;
  const started = Date.now();
  while (Date.now() - started < 90000) {
    await wait(1500);
    view = await read();
    if (!view) return null;
    if ((view.observed_at || "") >= request.requested_at || (view.error_at || "") >= request.requested_at) return view;
  }
  return view;
}

async function mrerpMenuReady(
  connection: Connection, accountSet: string, direction: string,
  setView: (v: MenuView) => void, setAsk: (fn: (ok: boolean) => void) => void,
): Promise<boolean> {
  const any = (v: MenuView | null) => menuNeeded(v, direction).length || defaultNeeded(v, direction).length;
  let view = await mrerpMenu(connection, accountSet);
  if (!any(view)) return true;
  view = await mrerpMenu(connection, accountSet, true);
  if (!any(view) || !view) return true;
  return new Promise((resolve) => {
    setView(view!);
    setAsk(() => resolve);
  });
}

function MenuDialog({ tr, view, direction, connection, accountSet, onDone }: {
  tr: Tr; view: MenuView; direction: string; connection: Connection; accountSet: string; onDone: (ok: boolean) => void;
}) {
  const kinds = menuNeeded(view, direction);
  const fields = defaultNeeded(view, direction);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const all = [...kinds, ...fields.map((f) => `default_${f}`)];
  return (
    <Modal
      open
      onClose={() => onDone(false)}
      title={tr(fields.length ? "intake.mrerpDefault.title" : "intake.mrerpMenu.title")}
      footer={
        <>
          {error && <span className="me-auto text-danger text-body">{error}</span>}
          <button type="button" className="cc-btn" onClick={() => onDone(false)}>{t("cancel")}</button>
          <button
            type="button"
            className="cc-btn"
            data-primary=""
            disabled={busy || all.some((k) => !values[k])}
            onClick={async () => {
              // 存不进（选项刚在 MR.ERP 变了、没有权限）：原因写在弹窗里，按钮放开可再存（原先未接住，按钮一直转圈）
              setBusy(true);
              setError("");
              try {
                for (const kind of kinds) {
                  await postMethod(CONN + "choose_mrerp_menu", { name: connection.name, account_set_key: accountSet, doc_type: kind, idmenu: values[kind] });
                }
                for (const field of fields) {
                  await postMethod(CONN + "choose_mrerp_default", { name: connection.name, account_set_key: accountSet, field, code: values[`default_${field}`] });
                }
                onDone(true);
              } catch (e) {
                setError(shownError(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {tr("Save")}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-(--cc-space-16) pb-(--cc-space-8)">
        {kinds.map((kind) => (
          <Field key={kind} label={tr(`intake.mrerpMenu.${kind}`)}>
            <Select value={values[kind] || ""} onChange={(v) => setValues((s) => ({ ...s, [kind]: v }))}
              options={(view.kinds?.[kind]?.options || []).map((o) => ({ value: o.idmenu, label: o.label || o.idmenu }))} />
          </Field>
        ))}
        {fields.map((field) => (
          <Field key={field} label={tr(`intake.mrerpDefault.${field}`)}>
            <Select value={values[`default_${field}`] || ""} onChange={(v) => setValues((s) => ({ ...s, [`default_${field}`]: v }))}
              options={(view.defaults?.fields?.[field]?.options || []).map((o) => ({ value: o.code, label: [o.code, o.name].filter(Boolean).join(" · ") }))} />
          </Field>
        ))}
      </div>
    </Modal>
  );
}

// ── 连接：MR.ERP 填账号密码；Express / CC 桌面版是「下载 CC 桌面版」并等它连上（同 Cowork 的连接弹窗） ─────────────

function ConnectDialog({ tr, kind, connection, onClose }: { tr: Tr; kind: string; connection: Connection | null; onClose: () => void }) {
  if (kind === "mrerp") return <MrerpConnect tr={tr} connection={connection} onClose={onClose} />;
  return <HelperConnect tr={tr} connection={connection} adapter={kind === "helper" ? connection?.adapter ?? "express" : kind} onClose={onClose} />;
}

function MrerpConnect({ tr, connection, onClose }: { tr: Tr; connection: Connection | null; onClose: () => void }) {
  const requestKey = useMemo(key, []);
  const [url, setUrl] = useState(connection?.config?.system_url || "https://www.mrerp4sme.com");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal
      open
      onClose={onClose}
      title={`${tr("Connect")} MR.ERP`}
      footer={
        <>
          {error && <span className="me-auto text-danger text-body">{error}</span>}
          <button type="button" className="cc-btn" onClick={onClose}>{t("cancel")}</button>
          <button
            type="button"
            className="cc-btn"
            data-primary=""
            disabled={busy || !url || !user || !password}
            onClick={async () => {
              setBusy(true);
              try {
                await postMethod(CONN + "configure", {
                  adapter: "mrerp", request_key: requestKey, label: "MR.ERP", config: { system_url: url },
                  credentials: { username: user, password },
                  ...(connection ? { name: connection.name, expected_revision: connection.revision } : {}),
                });
                onClose();
              } catch (e) {
                setError(shownError(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {tr("Connect")}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-(--cc-space-16) pb-(--cc-space-8)">
        <Field label={tr("System URL")}><input className="cc-input" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <Field label={tr("Account")}><input className="cc-input" value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" /></Field>
        <Field label={tr("Password")}><input className="cc-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
      </div>
    </Modal>
  );
}

function HelperConnect({ tr, connection, adapter, onClose }: { tr: Tr; connection: Connection | null; adapter: string; onClose: () => void }) {
  const [online, setOnline] = useState(false);
  useEffect(() => {
    let current = connection;
    const check = async () => {
      if (!current) {
        const rows = await getMethod<Connection[]>(CONN + "records", { limit: 100 }).catch(() => []);
        current = mainOf(rows, adapter);
      }
      if (!current) return setOnline(false);
      const r = await getMethod<{ online?: boolean }>(COMPANION + "online", { connection: current.name }).catch(() => null);
      setOnline(Boolean(r?.online));
    };
    void check();
    const timer = window.setInterval(() => void check(), 3000);
    return () => window.clearInterval(timer);
  }, [connection, adapter]);
  const [missing, setMissing] = useState(false);
  return (
    <Modal open onClose={onClose} title={adapter === "express" ? `${tr("Connect")} Express` : tr("cowork.folder.connectHelper")}>
      <div className="flex flex-col items-start gap-(--cc-space-12) pb-(--cc-space-12)">
        <Chip tone={online ? "ok" : "wait"}>{online ? tr("connect.state.connected") : tr("intake.agent.state.missing")}</Chip>
        <button
          type="button"
          className="cc-btn"
          data-primary=""
          onClick={async () => {
            const info = await getMethod<{ configured?: boolean; url?: string }>(COMPANION + "installer").catch(() => null);
            if (!info?.configured || !info.url) return setMissing(true);
            window.open(info.url, "_blank", "noopener,noreferrer");
          }}
        >
          {tr("intake.target.download")}
        </button>
        <span className="text-ink-3 text-caption">{missing ? tr("intake.installerMissing") : tr("intake.agent.loginHint")}</span>
      </div>
    </Modal>
  );
}
