// 月结台账（数字会计员第 1 期，服务端 companies.api.month）在页面这边用到的：形状、读取、按「公司|月份」缓存与实时重取，
// 以及右侧会话栏「本月」卡与公司页概览共用的纯函数（四段一行的短计数、异常按严重度分组、交给 CC 的预填）。
//
//   数据   服务端每次现算、不存；只给码与数（不带文案），页面按码翻译（i18n.ts 的 co.ledger）。形状见跨端契约
//          tests/fixtures/cc-month-contract.json（服务端测试断言它就是真实输出，页面测试拿同一份当假数据）。
//   四段   收单 intake / 录账 entry / 核对 verify / 报税 tax；状态 empty | open | attention | clear。核对一段只在
//          Express 公司、有录入权限时有（否则 null），报税一段要报税权限（否则 null）——是 null 的段不显示。
//   异常   服务端已排好（action → warn → info，同级按段、再按日期新 → 旧）；页面只按严重度切成三组，组内保持服务端顺序。
//   刷新   挂着（看得见）时跟着推送状态与定时任务的站点事件重取（PENDING_EVENTS），回到前台、重连时重取（lib/live.ts）；
//          看不见（会话栏关着、换了页面）不取。服务端每次整月现算，两次至少隔 REFRESH_MIN_MS（定时任务逐张推时事件很密），
//          空闲几乎不轮询（IDLE_MS）；有账套还在回读时每 REFRESH_MIN_MS 重取一次，最多 READING_POLL_MS（回读完成不发站点事件）。
//
// 金额是服务端的十进制字符串，只插千分位（formatAmount），不转浮点。

import { useCallback, useEffect, useRef, useState } from "react";
import { getMethod, shownError } from "@/lib/frappe";
import { lang } from "@/i18n";
import { isPeriod, PENDING_EVENTS, type PendingChoice, type PendingItem } from "@/lib/companies";
import { useLiveRefresh } from "@/lib/live";
import { onProjectChange } from "@/lib/managed-agents/realtime";

const API = "pearnly_cowork.companies.api.month";

// ---------------------------------------------------------------- 形状（companies/ledger.py 的 month、companies/exceptions.py）
export const STAGES = ["intake", "entry", "verify", "tax"] as const;
export type Stage = (typeof STAGES)[number];
export type StageState = "empty" | "open" | "attention" | "clear";
export const SEVERITIES = ["action", "warn", "info"] as const;
export type Severity = (typeof SEVERITIES)[number];
export type ExceptionAction = "choose" | "push" | "open" | "task" | "readback" | "draft" | "account_set";

export type ReadbackSet = {
  connection: string; key: string; label: string; status: "none" | "reading" | "ready" | "failed" | string;
  readback: string | null; requested_by?: string; requested_by_name?: string; requested_at?: string; ready_at?: string;
  counts?: Record<string, number> | null; newer?: string | null;
};
export type TaxDraftRow = { draft: string; branch?: string; updated_at?: string; tax_total?: string; issues?: number; stale?: boolean };
export type MonthStages = {
  intake: {
    received: number; recognized_only: number; no_date: number;
    tasks: { count: number; enabled: number; attention: number } | null;
    files: { failed: number; not_invoice: number; pos_layout_needed: number } | null;
    state: StageState;
  };
  /** auto：已推里自动推的张数（数字会计员第 3 期；旧服务端没有）。 */
  entry: { pushed: number; auto?: number; in_flight: number; waiting: number; not_pushed: number; state: StageState };
  verify: { sets: ReadbackSet[]; state: StageState } | null;
  tax: {
    vat: TaxDraftRow[]; wht: TaxDraftRow[];
    deadlines?: Record<string, { paper?: string; efiling?: string }>;
    state: StageState;
  } | null;
};

/** 一条异常：码、段、严重度、去处；单据层的（group 是原待处理粗分）带单据字段，汇总的带 count，其余按码带任务 / 账套 / 草稿。 */
export type MonthException = {
  code: string; stage: Stage; severity: Severity; action: ExceptionAction; group: string | null; document: string | null;
  invoice_no?: string; counterparty?: string; amount?: string | number | null; date?: string; filename?: string;
  reason_label?: string; detail?: string;
  choice?: PendingChoice | null; others?: string[]; count?: number;
  /** 同号的一组（duplicate_number）：每张的单号、对方、金额、日期（不在本月的被匹配那张也在里面）。 */
  documents?: Array<{ document: string; invoice_no?: string; counterparty?: string; amount?: string | number | null; date?: string; filename?: string }>;
  task?: string; title?: string;
  connection?: string; account_set_key?: string; label?: string; readback?: string | null;
  draft?: string; kind?: string; branch?: string; issue?: string; issue_label?: string;
};

/** 这家的自动推送档位（数字会计员第 3 期）：L0 只提议 / L1 按规则 / L2 整月；reason 是最近一次升降的原因码。
 *  不是 Express 公司为 null，旧服务端没有。 */
export type Automation = { tier: string; since?: string; reason?: string };

export type Month = {
  company: string; period: string; ended: boolean; generated_at?: string; truncated?: boolean;
  automation?: Automation | null;
  stages: MonthStages;
  exceptions: { items: MonthException[]; total: number; counts: Record<string, number>; severities?: Partial<Record<Severity, number>> };
};

export function loadMonth(company: string, period: string, limit?: number): Promise<Month> {
  return getMethod<Month>(API, { company, period, limit });
}

// ---------------------------------------------------------------- 纯函数
/** 显示哪几段：服务端给 null 的（不是 Express、没有录入 / 报税权限）不列。 */
export function shownStages(month: Pick<Month, "stages">): Stage[] {
  return STAGES.filter((stage) => !!month.stages[stage]);
}

/** 这个月有没有什么可显示的：每一段都是 empty（或没有）、也没有异常 → 会话栏里整张卡不显示。 */
export function emptyMonth(month: Month | null | undefined): boolean {
  if (!month) return true;
  if (month.exceptions?.items?.length) return false;
  return shownStages(month).every((stage) => month.stages[stage]!.state === "empty");
}

/** 按严重度分组（要处理 → 看一眼 → 知悉），组内保持服务端的顺序；空组不列。 */
export function groupExceptions(items: readonly MonthException[]): Array<[Severity, MonthException[]]> {
  return SEVERITIES.map((severity) => [severity, items.filter((item) => item.severity === severity)] as [Severity, MonthException[]])
    .filter(([, list]) => list.length > 0);
}

/** 一组的条数：服务端按全部算的（清单截断了也是全数），没有就数这一页。 */
export function severityCount(month: Pick<Month, "exceptions">, severity: Severity): number {
  return month.exceptions.severities?.[severity] ?? month.exceptions.items.filter((item) => item.severity === severity).length;
}

/** 卡头右侧的数：需处理与需留意的（知悉的不催）。 */
export function attentionCount(month: Pick<Month, "exceptions">): number {
  return severityCount(month, "action") + severityCount(month, "warn");
}

/** 列表里没列出的条数（服务端只回前 limit 条）。 */
export function unlisted(month: Pick<Month, "exceptions">): number {
  return Math.max(0, (month.exceptions.total ?? 0) - month.exceptions.items.length);
}

/** 单据层的异常换成待处理的一行（当场选、交给 CC 用同一套：pending.tsx 的 ChooseRow、handText）。 */
export function pendingOf(item: MonthException, company: string): PendingItem {
  return {
    document: item.document ?? "", company, reason_code: item.group ?? item.code, code: item.code, reason_label: item.reason_label ?? "",
    invoice_no: item.invoice_no, counterparty: item.counterparty, amount: item.amount, date: item.date, filename: item.filename,
    action: item.action === "choose" || item.action === "account_set" || item.action === "push" ? item.action : "open",
    choice: item.choice ?? null,
  };
}

/** 这条异常是不是「待选账套」、能当场换账套（服务端 action account_set、带推送去向，T1，第 6 批）。 */
export function accountSetException(item: MonthException): boolean {
  return item.action === "account_set" && !!item.document && !!item.choice?.connection;
}

/** 这条异常是不是 CC 准备好的提议、能当场「推送」（服务端 action push、带推送去向，数字会计员第 3 期）。 */
export function proposalException(item: MonthException): boolean {
  return item.action === "push" && !!item.document && !!item.choice?.connection;
}

/** 降过档时跟的短原因码（核不上 / 被改）；升档的原因（readback）与没有原因的不写。 */
export const DROP_REASONS = ["written_mismatch", "changed"] as const;
export function droppedReason(automation: Automation | null | undefined): string {
  const reason = automation?.reason ?? "";
  return (DROP_REASONS as readonly string[]).includes(reason) ? reason : "";
}

/** 这条异常是不是「当场选」（服务端 action choose、带推送去向）。 */
export function choosableException(item: MonthException): boolean {
  return item.action === "choose" && !!item.document && !!item.choice?.connection;
}

/** 一行的次要信息：单据（单号 · 对方，没有就单据编号）、任务名、账套名、草稿的分店。 */
export function exceptionSubject(item: MonthException, branchLabel: (branch: string) => string = (b) => b): string {
  if (item.document) return [item.invoice_no, item.counterparty].filter(Boolean).join(" · ") || item.document;
  if (item.task) return item.title || item.task;
  if (item.label || item.account_set_key) return item.label || item.account_set_key || "";
  if (item.draft) return [item.issue_label, item.branch ? branchLabel(item.branch) : ""].filter(Boolean).join(" · ");
  return "";
}

const LOCALE: Record<string, string> = { th: "th-TH-u-ca-buddhist", zh: "zh-CN", en: "en-US" };

/** 「15 ต.ค.」「10月15日」「Oct 15」：只有日期的（YYYY-MM-DD）按日期，带时间的按这台设备的时区；认不出原样。 */
export function dayShort(value: string | null | undefined): string {
  if (!value) return "";
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const date = day ? new Date(Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3]))) : new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { month: "short", day: "numeric", ...(day ? { timeZone: "UTC" } : {}) }).format(date);
}

/** 「10月15日 21:08」「Oct 15, 21:08」「15 ต.ค. 21:08」：带时间的按这台设备的时区写到时分（核对一段的回读时刻）；
 *  只有日期的同 dayShort；认不出原样。 */
export function dayTime(value: string | null | undefined): string {
  if (!value || /^\d{4}-\d{2}-\d{2}$/.test(value)) return dayShort(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(LOCALE[lang] ?? "en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

/** 有没有账套还在回读（最新那份或比完成的那份更新的一份）。 */
export function readingNow(month: Pick<Month, "stages"> | null | undefined): boolean {
  return !!month?.stages.verify?.sets.some((set) => set.status === "reading" || set.newer === "reading");
}

const pad = (n: number) => String(n).padStart(2, "0");
/** 这台设备的今天（YYYY-MM-DD）。 */
export const today = (now = new Date()) => `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

/** 报税一段写的截止日：ภ.พ.30 电子申报，有 ภ.ง.ด. 草稿时加上 ภ.ง.ด. 电子申报（更早），取最早、还没过的那个；
 *  都过了就不写截止日（测试窗口第 3 轮：也不写「逾期」——第 1 期不知道报没报）。 */
export function taxDue(tax: MonthStages["tax"], on = today()): string {
  if (!tax) return "";
  const dates = [tax.deadlines?.pp30?.efiling, tax.wht.length ? tax.deadlines?.pnd?.efiling : ""].filter((d): d is string => !!d).sort();
  return dates.find((d) => d >= on) ?? "";
}

// ---------------------------------------------------------------- 读取（按 公司|月份 缓存）
export const REFRESH_MIN_MS = 15_000;
export const IDLE_MS = 10 * 60_000;
export const READING_POLL_MS = 15 * 60_000;

const months = new Map<string, Month>();
// 当场就处理完的码（选完重推、推了提议）：交出去就从列表里拿掉
const DONE_ON_THE_SPOT = ["choice_needed", "cc_proposal"];
const keyOf = (company: string, period: string) => `${company}|${period}`;

export function forgetMonth(company?: string) {
  if (!company) return months.clear();
  for (const key of [...months.keys()]) if (key.startsWith(`${company}|`)) months.delete(key);
}

/** 这家这个月的台账：先给缓存、后台再取；挂着期间跟着推送 / 定时任务的站点事件、回到前台、重连重取。
 *  period 不是 YYYY-MM（对话没定月份）时不取，回 null。 */
export function useMonth(company: string, period: string) {
  const ok = !!company && isPeriod(period);
  const key = keyOf(company, period);
  const [state, setState] = useState<{ key: string; month: Month | null; error: string }>(() => ({ key, month: months.get(key) ?? null, error: "" }));
  const asked = useRef(0);
  const reload = useCallback(async () => {
    if (!ok) return;
    const mine = ++asked.current;
    try {
      const next = await loadMonth(company, period);
      if (mine !== asked.current) return;
      months.set(key, next);
      setState({ key, month: next, error: "" });
    } catch (e) {
      // 同一个错误不重设（role="alert" 不在每次自动重取失败时重新播报）
      const error = shownError(e);
      if (mine === asked.current)
        setState((before) => (before.key === key && before.error === error ? before : { key, month: before.key === key ? before.month : (months.get(key) ?? null), error }));
    }
  }, [ok, company, period, key]);
  useEffect(() => {
    setState({ key, month: months.get(key) ?? null, error: "" });
    void reload();
  }, [key, reload]);
  // 还在回读：有上限地定时重取（从这次看到「回读中」起最多 READING_POLL_MS）
  const shown = state.key === key ? state.month : null;
  const since = useRef(0);
  const reading = readingNow(shown);
  if (!reading) since.current = 0;
  else if (!since.current) since.current = Date.now();
  const busy = reading && Date.now() - since.current < READING_POLL_MS;
  useLiveRefresh(reload, { events: ok ? PENDING_EVENTS : [], busy, busyMs: REFRESH_MIN_MS, idleMs: IDLE_MS, minMs: REFRESH_MIN_MS });
  useEffect(
    () =>
      onProjectChange((change) => {
        if (change.op === "delete" && change.id === company) forgetMonth(company);
      }),
    [company],
  );
  /** 当场选完 / 推完一张：先从列表里拿掉（不等重取），再重取。 */
  const drop = useCallback(
    (document: string) => {
      setState((before) => {
        if (!before.month) return before;
        const items = before.month.exceptions.items.filter((item) => item.document !== document || !DONE_ON_THE_SPOT.includes(item.code));
        return { ...before, month: { ...before.month, exceptions: { ...before.month.exceptions, items } } };
      });
      void reload();
    },
    [reload],
  );
  const current = state.key === key;
  return {
    month: ok ? (current ? state.month : (months.get(key) ?? null)) : null,
    error: ok && current ? state.error : "",
    loading: ok && !(current ? state.month : months.get(key)) && !(current && state.error),
    reload,
    drop,
  };
}
