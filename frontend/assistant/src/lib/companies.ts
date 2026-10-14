// 公司主档（Cowork companies 域，ADR-020）在对话页这边用到的全部：接口、当前看的是哪家、月份与分店的显示、待处理。
//
//   接口      — pearnly_cowork.companies.api.*（读用 GET、写只收 POST，权限都在服务端判）；名称、税号一律由服务端从账套目录算，
//               页面只交「哪条连接的哪份账套」与幂等的 request_key
//   当前公司  — 公司页 / 主档页 / 定时任务页 / 待处理页看的是哪家（useCompanyNav；深链接 ?company=、?view=tasks&company=）
//   月份分店  — 处理月份 YYYY-MM（公历存，泰文按佛历显示）；分店号 5 位，00000 是总公司（显示走翻译）
//   待处理    — 侧栏「待处理 N」与公司组头的数（pending_counts，一次 GROUP 查询），推送状态的站点事件来了重取，回到前台重取
//
// 金额是服务端给的十进制字符串（Decimal），页面只插千分位、不转成浮点。

import { useEffect, useState } from "react";
import { create } from "zustand";
import { co, lang } from "@/i18n";
import { getMethod, postMethod } from "@/lib/frappe";
import { onConnectionChange, onSiteEvent } from "@/lib/managed-agents/realtime";

const API = "pearnly_cowork.companies.api.";

// ---------------------------------------------------------------- 形状（规格 §4.2 与补充甲、乙、丙）

/** 全所公司页的一行。 */
export type CompanyRow = {
  id: string; title: string; legal_name?: string; tax_id?: string; branch?: string; adapter?: string;
  years?: string[]; mine_count?: number; mine_last_active?: string | null;
};

/** 添加公司时的候选（按 identity 合并；company 是已建的编号）。 */
export type Candidate = {
  identity: string; title: string; legal_name?: string; tax_id?: string; branch?: string; adapter?: string;
  years?: string[]; connection: string; account_set_key: string; company: string | null;
  branches?: Array<{ branch: string; label?: string }>;
};

export type AccountSet = {
  connection: string; adapter?: string; key: string; label: string; year_label?: string;
  period_start?: string; period_end?: string; writable?: boolean; branch?: string; branch_label?: string;
};
/** 一家公司（法人）下的分店：分店号、显示名、各自的账套（服务端从目录现算，新年度在前）。 */
export type Branch = { branch: string; label?: string; account_sets: AccountSet[] };
export type FolderState = "ok" | "waiting" | "offline" | "missing" | "folder_missing";
export type CompanyFolder = {
  reader: string; device: string; label: string; state: FolderState; month_folder?: string | null;
  month_candidates?: string[]; mine?: boolean; root_id?: string; folder?: string;
};
export type Note = { id: string; text: string; origin?: "person" | "cc"; updated_by?: string; updated_at?: string };
/** 记住的选择（express / pos / income / vat / history）；history = 从 Express 历史学到的「供应商 → 科目」，只读可删。 */
export type Choice = {
  key: string; source: string; kind: string; subject_label: string; value: string; value_label?: string; editable?: boolean;
  year_label?: string; set_by?: string; set_at?: string; count?: number; last_date?: string;
  supplier_name?: string; account_label?: string;
};
export type TaskRow = { name: string; title: string; enabled?: boolean | number; account_label?: string; folder_label?: string };
export type Draft = { name: string; kind: string; period: string; updated_at?: string; branch?: string; company_branch?: string };
export type Detail = {
  company: {
    id: string; title: string; legal_name?: string; tax_id?: string; branch?: string; adapter?: string;
    created_by?: string; created_at?: string; combined_vat?: boolean | number;
  };
  account_sets: AccountSet[];
  branches?: Branch[];
  period_set?: { key: string; label: string } | null;
  folders: CompanyFolder[];
  notes: Note[];
  choices: Choice[];
  tasks?: TaskRow[] | null;
  drafts: Draft[] | null;
};

/** 待处理的一张单（补充乙）：action choose = 能当场选（带要选的项与可选值）；open = 交给 CC。 */
export type ChoiceNeeded = {
  kind: string; subject: string; label?: string; current?: string; suggested?: string;
  options?: Array<{ code: string; name?: string; branch?: string }>;
};
export type PendingItem = {
  document: string; company: string | null; reason_code: string; reason_label: string;
  invoice_no?: string; counterparty?: string; amount?: string | number | null; date?: string; action: "choose" | "open";
  choices?: {
    connection: string; account_set_key: string; needed: ChoiceNeeded[];
    options?: { accounts?: Array<{ code: string; name?: string }>; products?: Array<{ code: string; name?: string }> };
  } | null;
};

// ---------------------------------------------------------------- 接口
export const requestKey = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function listCompanies(search = "", start = 0, limit = 50): Promise<CompanyRow[]> {
  return getMethod<CompanyRow[]>(API + "companies", { search: search || undefined, start, limit }).then((rows) => rows ?? []);
}

export function listCandidates(search = ""): Promise<Candidate[]> {
  return getMethod<Candidate[]>(API + "candidates", { search: search || undefined }).then((rows) => rows ?? []);
}

/** 从账套目录建公司：只交连接与账套键，名称、税号由服务端读目录算；已有就回现有的那家。 */
export function createCompany(candidate: Pick<Candidate, "connection" | "account_set_key">, key = requestKey()): Promise<{ id: string; title?: string }> {
  return postMethod(API + "create", { connection: candidate.connection, account_set_key: candidate.account_set_key, request_key: key });
}

export function loadDetail(company: string, period = ""): Promise<Detail> {
  return getMethod<Detail>(API + "detail", { company, period: period || undefined });
}

export const renameCompany = (company: string, title: string) => postMethod(API + "rename", { company, title });
export const deleteCompany = (company: string) => postMethod(API + "delete", { company });
/** 知识库一条：不给 note 是新增，给了是修改。 */
export const saveNote = (company: string, text: string, note?: string) =>
  postMethod<Note>(API + "note_save", { company, text, ...(note ? { note } : {}) });
export const deleteNote = (company: string, note: string) => postMethod(API + "note_delete", { company, note });
/** CC 桌面版 pick_folder 给的 binding 原样交给服务端（页面不拼路径）。 */
export const bindFolder = (company: string, binding: unknown) => postMethod(API + "folder_bind", { company, binding: JSON.stringify(binding) });
export const unbindFolder = (company: string, reader: string) => postMethod(API + "folder_unbind", { company, reader });
export const forgetChoice = (company: string, key: string) => postMethod(API + "choice_forget", { company, key });
export const setChoice = (company: string, key: string, value: string) => postMethod(API + "choice_set", { company, key, value });

/** 待处理的单（company 不给 = 全部有权看的）。 */
export function loadPending(company = "", limit = 200): Promise<PendingItem[]> {
  return getMethod<PendingItem[] | { items?: PendingItem[] }>(API + "pending", { company: company || undefined, limit }).then((out) =>
    Array.isArray(out) ? out : (out?.items ?? []),
  );
}

/** 当场选：复用推送的「记下选择」（push.application.api.save_choices），不另写一套。 */
export function answerChoices(item: PendingItem, values: Record<string, string>): Promise<unknown> {
  const view = item.choices;
  if (!view) return Promise.reject(new Error("no choices"));
  const items = view.needed
    .filter((need) => values[`${need.kind}\n${need.subject}`])
    .map((need) => ({ kind: need.kind, subject: need.subject, value: values[`${need.kind}\n${need.subject}`], label: need.label ?? "" }));
  return postMethod("pearnly_cowork.push.application.api.save_choices", {
    history: item.document, connection: view.connection, account_set_key: view.account_set_key, items: JSON.stringify(items),
  });
}

/** 账套目录还没读到时「刷新账套」：本人能用的每条连接各请电脑读一次目录（connections.request_refresh，同网页）。 */
export async function refreshCatalogs(): Promise<void> {
  const rows = (await getMethod<Array<{ name: string; enabled?: number | boolean }>>("pearnly_cowork.connections.api.records", { limit: 100 })) ?? [];
  await Promise.all(
    rows.filter((row) => row.enabled).map((row) =>
      postMethod("pearnly_cowork.connections.api.request_refresh", { name: row.name, request_key: requestKey() }).catch(() => undefined),
    ),
  );
}

// ---------------------------------------------------------------- 当前看的是哪家
/** 定时任务表单的预填（主档页「＋」）：覆盖本月的账套；本机已绑定文件夹时连同文件夹（reader|root_id|folder）。 */
export type TaskSeed = { adapter: string; connection: string; account_set_key: string; folder?: string };
type CompanyNav = {
  company: string; // 主档页看的是哪家
  taskCompany: string; // 定时任务页只看这家（""= 全部）
  taskFocus: string; // 定时任务页定位到哪个任务
  taskSeed: TaskSeed | null; // 打开定时任务页时接着开新建表单并预填
  pendingCompany: string; // 待处理只看这家
};
export const useCompanyNav = create<CompanyNav>(() => ({ company: "", taskCompany: "", taskFocus: "", taskSeed: null, pendingCompany: "" }));

// ---------------------------------------------------------------- 月份与分店
const MONTH = /^(\d{4})-(0[1-9]|1[0-2])$/;

/** 公历 YYYY-MM（服务端同一个正则）。 */
export function isPeriod(value: string): boolean {
  return MONTH.test(value);
}

const pad = (n: number) => String(n).padStart(2, "0");
export const periodOf = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}`;

/** 上个月（按这台设备的日期；新对话默认处理上个月）。 */
export function lastMonth(now = new Date()): string {
  return periodOf(new Date(now.getFullYear(), now.getMonth() - 1, 1));
}

/** 胶囊里列的月份：本月往前共 12 个月，新的在前。 */
export function recentMonths(now = new Date(), count = 12): string[] {
  return Array.from({ length: count }, (_, i) => periodOf(new Date(now.getFullYear(), now.getMonth() - i, 1)));
}

const LOCALE: Record<string, string> = { th: "th-TH-u-ca-buddhist", zh: "zh-CN", en: "en-US" };
const at = (period: string) => {
  const m = MONTH.exec(period);
  return m ? new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 15)) : null;
};

/** 「ก.ย. 2569」「2026年9月」「Sep 2026」；空 = 不指定。 */
export function monthLabel(period: string): string {
  const date = at(period);
  if (!date) return co.noPeriod;
  return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { month: "short", year: "numeric", timeZone: "UTC" }).format(date);
}

/** 侧栏对话行后面的月份短写：今年只写月（「ก.ย.」「9月」「Sep」），别的年带上年。 */
export function monthShort(period: string, now = new Date()): string {
  const date = at(period);
  if (!date) return "";
  if (date.getUTCFullYear() !== now.getFullYear()) return monthLabel(period);
  return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { month: "short", timeZone: "UTC" }).format(date);
}

export const HQ = "00000";

/** 分店短写：总公司 → 「总公司」，其余 → 「分店 00001」（走翻译）；空 = 不指定。 */
export function branchShort(scope: string): string {
  if (!scope) return "";
  return scope === HQ ? co.headOffice : `${co.branchShort} ${scope}`;
}

/** 一家公司的分店（服务端 branches；旧服务端没有就按账套上的分店号现分）。 */
export function branchesOf(detail: Pick<Detail, "branches" | "account_sets"> | null | undefined): Branch[] {
  if (!detail) return [];
  if (Array.isArray(detail.branches)) return detail.branches;
  const map = new Map<string, Branch>();
  for (const set of detail.account_sets ?? []) {
    if (!set.branch) continue;
    const row = map.get(set.branch) ?? { branch: set.branch, label: set.branch_label, account_sets: [] };
    row.account_sets.push(set);
    map.set(set.branch, row);
  }
  return [...map.values()];
}

/** 千分位、保留服务端给的小数（不经浮点）。 */
export function formatAmount(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const text = String(value).trim();
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text);
  if (!m) return text;
  const whole = m[2]!.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const decimals = (m[3] ?? "").padEnd(2, "0");
  return `${m[1]}${whole}.${decimals}`;
}

/** 显示时间（存 UTC，按这台设备的时区显示）。 */
export function formatWhen(value: string | undefined | null): string {
  if (!value) return "";
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value.replace(" ", "T")}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

// ---------------------------------------------------------------- 公司详情缓存（顶栏分店胶囊要知道这家有几个分店）
const detailCache = new Map<string, Promise<Detail | null>>();

export function cachedDetail(company: string): Promise<Detail | null> {
  if (!company) return Promise.resolve(null);
  let found = detailCache.get(company);
  if (!found) {
    found = loadDetail(company).catch(() => null);
    detailCache.set(company, found);
  }
  return found;
}

export function dropCachedDetail(company?: string) {
  if (company) detailCache.delete(company);
  else detailCache.clear();
}

/** 这家公司的分店（多于一个时页面才出分店胶囊）。 */
export function useBranches(company: string): Branch[] {
  const [branches, setBranches] = useState<Branch[]>([]);
  useEffect(() => {
    let alive = true;
    setBranches([]);
    void cachedDetail(company).then((detail) => alive && setBranches(branchesOf(detail)));
    return () => {
      alive = false;
    };
  }, [company]);
  return branches;
}

// ---------------------------------------------------------------- 待处理计数
type Counts = { loaded: boolean; total: number; companies: Record<string, number> };
export const usePendingCounts = create<Counts>(() => ({ loaded: false, total: 0, companies: {} }));

// 推送状态变了（录入列表同一个事件，history/push_state.py）、定时任务有变化（folder_intake/live.py）：重取一次计数
export const PENDING_EVENTS = ["cowork_intake_changed", "cowork_folder_changed"] as const;

type CountsReply = { total?: number; companies?: Record<string, number>; by_company?: Record<string, number> } | Array<{ company: string | null; count: number }>;

export function parseCounts(out: CountsReply | null | undefined): { total: number; companies: Record<string, number> } {
  if (Array.isArray(out)) {
    const companies: Record<string, number> = {};
    let total = 0;
    for (const row of out) {
      total += Number(row.count) || 0;
      if (row.company) companies[row.company] = (companies[row.company] ?? 0) + (Number(row.count) || 0);
    }
    return { total, companies };
  }
  const companies = { ...(out?.companies ?? out?.by_company ?? {}) };
  const total = Number(out?.total) || Object.values(companies).reduce((a, b) => a + (Number(b) || 0), 0);
  return { total, companies };
}

let counting: Promise<void> | null = null;
export function refreshPendingCounts(): Promise<void> {
  counting ??= getMethod<CountsReply>(API + "pending_counts")
    .then((out) => usePendingCounts.setState({ loaded: true, ...parseCounts(out) }))
    .catch(() => undefined)
    .finally(() => {
      counting = null;
    });
  return counting;
}

/** 侧栏挂上后：读一次计数；推送状态的站点事件来了（合成一次）、回到前台、实时通道重连时再读。 */
export function watchPendingCounts(): () => void {
  void refreshPendingCounts();
  let timer = 0;
  const soon = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => void refreshPendingCounts(), 1500);
  };
  const offs = PENDING_EVENTS.map((name) => onSiteEvent(name, soon));
  const offConnection = onConnectionChange((connected) => connected && soon());
  const onVisible = () => document.visibilityState === "visible" && soon();
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.clearTimeout(timer);
    offs.forEach((off) => off());
    offConnection();
    document.removeEventListener("visibilitychange", onVisible);
  };
}

// ---------------------------------------------------------------- 全所有没有公司（新对话的公司条只在至少有一家时出现）
let anyCompany: Promise<boolean> | null = null;

export function hasAnyCompany(): Promise<boolean> {
  anyCompany ??= listCompanies("", 0, 1).then((rows) => rows.length > 0, () => false);
  return anyCompany;
}

/** 建了或删了公司：下次重新问。 */
export function forgetAnyCompany() {
  anyCompany = null;
}
