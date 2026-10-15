// 月结台账（数字会计员第 1 期，服务端 companies.api.month）在页面这边用到的：形状、读取、按「公司|月份」缓存与实时重取，
// 以及右侧会话栏「本月」卡与公司页概览共用的纯函数（四段一行的短计数、异常按严重度分组、交给 CC 的预填）。
//
//   数据   服务端每次现算、不存；只给码与数（不带文案），页面按码翻译（i18n.ts 的 co.ledger）。形状见跨端契约
//          tests/fixtures/cc-month-contract.json（服务端测试断言它就是真实输出，页面测试拿同一份当假数据）。
//   四段   收单 intake / 录账 entry / 核对 verify / 报税 tax；状态 empty | open | attention | clear。核对一段只在
//          Express 公司、有录入权限时有（否则 null），报税一段要报税权限（否则 null）——是 null 的段不显示。
//   异常   服务端已排好（action → warn → info，同级按段、再按日期新 → 旧）；页面只按严重度切成三组，组内保持服务端顺序。
//   刷新   挂着（看得见）时跟着推送状态与定时任务的站点事件重取（PENDING_EVENTS），回到前台、重连时重取（lib/live.ts）；
//          看不见（会话栏关着、换了页面）不取。
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
export type ExceptionAction = "choose" | "open" | "task" | "readback" | "draft";

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
  entry: { pushed: number; in_flight: number; waiting: number; not_pushed: number; state: StageState };
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
  invoice_no?: string; counterparty?: string; amount?: string | number | null; date?: string; reason_label?: string; detail?: string;
  choice?: PendingChoice | null; others?: string[]; count?: number;
  task?: string; title?: string;
  connection?: string; account_set_key?: string; label?: string; readback?: string | null;
  draft?: string; kind?: string; branch?: string; issue?: string;
};

export type Month = {
  company: string; period: string; ended: boolean; generated_at?: string; truncated?: boolean;
  stages: MonthStages;
  exceptions: { items: MonthException[]; total: number; counts: Record<string, number> };
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

/** 卡头右侧的数：要处理与要看一眼的（知悉的不催）。 */
export function attentionCount(items: readonly MonthException[]): number {
  return items.filter((item) => item.severity !== "info").length;
}

/** 单据层的异常换成待处理的一行（当场选、交给 CC 用同一套：pending.tsx 的 ChooseRow、handText）。 */
export function pendingOf(item: MonthException, company: string): PendingItem {
  return {
    document: item.document ?? "", company, reason_code: item.group ?? item.code, code: item.code, reason_label: item.reason_label ?? "",
    invoice_no: item.invoice_no, counterparty: item.counterparty, amount: item.amount, date: item.date,
    action: item.action === "choose" ? "choose" : "open", choice: item.choice ?? null,
  };
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
  if (item.draft) return item.branch ? branchLabel(item.branch) : "";
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

/** 核对一段最近一次完成的回读时间（多份账套取最新的）；没有完成的回空。 */
export function latestReadback(sets: readonly ReadbackSet[]): string {
  return sets.filter((set) => set.status === "ready" && set.ready_at).map((set) => set.ready_at!).sort().at(-1) ?? "";
}

// ---------------------------------------------------------------- 读取（按 公司|月份 缓存）
const months = new Map<string, Month>();
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
      if (mine === asked.current) setState((before) => ({ key, month: before.key === key ? before.month : (months.get(key) ?? null), error: shownError(e) }));
    }
  }, [ok, company, period, key]);
  useEffect(() => {
    setState({ key, month: months.get(key) ?? null, error: "" });
    void reload();
  }, [key, reload]);
  useLiveRefresh(reload, { events: ok ? PENDING_EVENTS : [] });
  useEffect(
    () =>
      onProjectChange((change) => {
        if (change.op === "delete" && change.id === company) forgetMonth(company);
      }),
    [company],
  );
  /** 当场选完一张：先从列表里拿掉（不等重取），再重取。 */
  const drop = useCallback(
    (document: string) => {
      setState((before) => {
        if (!before.month) return before;
        const items = before.month.exceptions.items.filter((item) => item.document !== document || item.code !== "choice_needed");
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
