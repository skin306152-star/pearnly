// 待处理（补充乙）：要人处理的单统一一个入口——侧栏「待处理 N」打开的视图（?view=pending，可带 &company=），
// 右侧会话栏与公司页的「待处理」卡也是同一个组件（只列这家；会话栏的只列对话月份以外的，otherMonths）。
//
//   数据：companies.api.pending（服务端从推送记录与录入状态现算，不新增存储）。按公司分组；归不上公司的放最后。
//   能当场选的（action choose：推送停在「要人选一次」，服务端只给推送去向与类别）：点开那一行才取要选的项与可选值
//   （push.application.api.choices，同复核页、CC 的判定；列表不逐行读账套主档）。建议项排第一、预选，要点「确认」才作答：
//   assistant.pending_api.answer 记下选择并按 CC 推送同一条路重推（只记不推不会进账）。可选值没读到的那张就地「交给 CC」。
//   其余一组一行「交给 CC」：在这家公司下开新对话（月份取单据月份），输入框预填「处理这几张：…」并带上单据编号，不自动发送。
//   处理完实时消失：推送状态的站点事件（cowork_intake_changed）来了重取，回到前台重取（lib/live.ts）。
//   金额是服务端的十进制字符串，只插千分位。样式只用 globals.css 的 .cc-* 与变量。

import { ChevronDownIcon, InboxIcon, LoaderIcon, SearchIcon, SparklesIcon, XIcon } from "lucide-react";
import { type FC, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { co, t } from "@/i18n";
import {
  answerChoices,
  answered,
  cachedDetail,
  type ChoiceNeeded,
  type ChoicesView,
  docLine,
  docList,
  formatAmount,
  isPeriod,
  listCompanies,
  loadChoices,
  loadPending,
  PENDING_EVENTS,
  type PendingItem,
  refreshPendingCounts,
  useCompanyNav,
} from "@/lib/companies";
import { useLiveRefresh } from "@/lib/live";
import { hintTitle, useProjects } from "@/lib/managed-agents/projects";
import { shownError } from "@/lib/frappe";

const choiceKey = (need: Pick<ChoiceNeeded, "kind" | "subject">) => `${need.kind}\n${need.subject}`;
const FIXED: () => Record<string, string> = () => ({ cash: co.payCash, credit: co.payCredit, claim: co.vatClaim, expense: co.vatExpense });

/** 一项要选的可选值：项自带的（对方档、付款、抵进项）或这份账套的科目表 / 存货表；建议的排第一（服务端的顺序）。 */
export function optionsOf(view: Pick<ChoicesView, "options">, need: ChoiceNeeded): Array<{ value: string; label: string }> {
  const fixed = FIXED();
  const pool = need.options?.length
    ? need.options
    : need.kind === "item"
      ? (view.options?.products ?? [])
      : ["account", "receipt"].includes(need.kind)
        ? (view.options?.accounts ?? [])
        : [];
  const rows = pool.map((o) => ({
    value: o.code,
    label: fixed[o.code] ?? [o.code, o.name ?? "", "branch" in o && o.branch ? `(${o.branch})` : ""].filter(Boolean).join(" "),
  }));
  const first = [...(need.suggested ?? []).map((s) => s.code), need.current ?? ""].filter(Boolean);
  const ahead = first.flatMap((code) => rows.filter((r) => r.value === code));
  return [...new Set([...ahead, ...rows])];
}

/** 打开时预填什么：已定的（current）、或服务端建议的第一项（在可选值里的）；都没有就空着，必须人选——
 *  不拿列表第一项充数（2026-10-15 验收 F18：没有建议时预选了科目表第一项「11-01-01-00 เงินสด」，直接确认会记错科目）。 */
export function initialChoice(view: Pick<ChoicesView, "options">, need: ChoiceNeeded): string {
  const values = new Set(optionsOf(view, need).map((o) => o.value));
  const first = [need.current ?? "", ...(need.suggested ?? []).map((s) => s.code)].find((code) => code && values.has(code));
  return first ?? "";
}

/** 这张单展开后能不能当场选完（每一项都有可选值、没在等电脑读科目表）。 */
export function choosableView(view: ChoicesView | null | undefined): boolean {
  return !!view && !view.wait && view.needed.length > 0 && view.needed.every((need) => optionsOf(view, need).length > 0);
}

/** 这张单是不是「当场选」那一类（服务端 action choose，带推送去向）。 */
export function choosable(item: PendingItem): boolean {
  return item.action === "choose" && !!item.choice?.connection;
}

/** 「交给 CC」预填的话（不自动发送）：一张「处理这张：INV-001 · 对方 · 1,070.00 · 2026-09-05」，几张时每张一行。
 *  只写单号、对方、金额、日期，不写内部记录名（测试窗口第 3 轮）；CC 按单号找（工具说明里讲了）。 */
export function handText(items: readonly PendingItem[]): string {
  const lines = items.map(docLine).filter(Boolean);
  return lines.length === 1 ? co.handToCCOne(lines[0]!) : co.handToCCText(docList(lines));
}

/** 这几张单的月份（取最多的那个月；没有日期的不算）。 */
export function monthOfItems(items: readonly PendingItem[]): string {
  const counts = new Map<string, number>();
  for (const item of items) {
    const month = String(item.date ?? "").slice(0, 7);
    if (isPeriod(month)) counts.set(month, (counts.get(month) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || b[0].localeCompare(a[0]))[0]?.[0] ?? "";
}

/** 一张单记在哪个月：服务端的 month（同月结台账：单据日期，没有的按上传月份）；旧服务端没给时取单据日期的月份。 */
export const pendingMonth = (item: Pick<PendingItem, "month" | "date">) => item.month ?? String(item.date ?? "").slice(0, 7);

/** 会话栏「待处理」卡只列对话月份以外的单（开发窗口 10-16 定）：对话那个月的只在「本月」卡的异常清单里，两张卡不重复列；
 *  对话没有月份时全列。公司页、待处理页不用它，照旧全列。 */
export function otherMonths(items: readonly PendingItem[], period: string): PendingItem[] {
  return isPeriod(period) ? items.filter((item) => pendingMonth(item) !== period) : [...items];
}

/** 待处理页的搜索：单号、对方、单据编号、原因、金额、日期里有这几个字（不分大小写）。 */
export function matchesPending(item: PendingItem, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return [item.invoice_no, item.counterparty, item.document, item.reason_label, item.amount, item.date]
    .map((part) => String(part ?? ""))
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

/** 公司编号 → 名称：侧栏里有的直接用，其余取全所公司第一页，再没有的逐家读一次。 */
function useTitles(ids: readonly string[]): Record<string, string> {
  const projects = useProjects((s) => s.projects);
  const [found, setFound] = useState<Record<string, string>>({});
  const key = ids.join(",");
  useEffect(() => {
    const known = new Map(projects.map((p) => [p.id, p.title]));
    const missing = ids.filter((id) => !known.has(id) && !found[id]);
    if (!missing.length) return;
    let alive = true;
    void listCompanies("", 0, 50)
      .catch(() => [])
      .then(async (rows) => {
        const out: Record<string, string> = {};
        for (const row of rows) out[row.id] = row.title;
        for (const id of missing.filter((m) => !out[m])) {
          const detail = await cachedDetail(id);
          if (detail) out[id] = detail.company.title;
        }
        if (alive) setFound((before) => ({ ...before, ...out }));
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, projects]);
  return { ...found, ...Object.fromEntries(projects.map((p) => [p.id, p.title])) };
}

/** 待处理单的列表状态（列表与会话栏卡共用）：读取，站点事件来了、回到前台重取，作答成功的当场拿掉。 */
export function usePendingItems(company: string) {
  const [items, setItems] = useState<PendingItem[] | null>(null);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    try {
      setItems(await loadPending(company));
      setError("");
    } catch (e) {
      setItems((before) => before ?? []);
      setError(shownError(e));
    }
  }, [company]);
  useEffect(() => {
    setItems(null);
    void reload();
  }, [reload]);
  useLiveRefresh(reload, { events: PENDING_EVENTS });
  const done = useCallback((document: string) => {
    setItems((before) => (before ?? []).filter((i) => i.document !== document));
    void refreshPendingCounts();
  }, []);
  const changed = useCallback(() => {
    void reload();
    void refreshPendingCounts();
  }, [reload]);
  return { items, error, done, changed };
}

type PendingState = ReturnType<typeof usePendingItems>;

/** 待处理列表；company 给了只列这家（会话栏、公司页的卡片，没有待处理时整块不出现）；search：待处理页的搜索。 */
export const PendingList: FC<{ company?: string; embedded?: boolean; search?: string }> = ({ company = "", embedded = false, search = "" }) => (
  <PendingItems company={company} embedded={embedded} search={search} {...usePendingItems(company)} />
);

/** 列表本身（数据由 usePendingItems 给；会话栏卡先按月份筛过再交进来）。 */
export const PendingItems: FC<PendingState & { company: string; embedded?: boolean; search?: string }> = ({
  items,
  error,
  done,
  changed,
  company,
  embedded = false,
  search = "",
}) => {
  const groups = useMemo(() => {
    const map = new Map<string, PendingItem[]>();
    for (const item of (items ?? []).filter((row) => matchesPending(row, search))) {
      const id = company || item.company || "";
      map.set(id, [...(map.get(id) ?? []), item]);
    }
    // 归不上公司的放最后
    return [...map.entries()].sort((a, b) => Number(!a[0]) - Number(!b[0]));
  }, [items, company, search]);
  const titles = useTitles(groups.map(([id]) => id).filter(Boolean));

  if (embedded && (!items || !items.length) && !error) return null;
  return (
    <div className="flex flex-col gap-4" data-slot="cc-pending">
      {error && <div role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-danger text-sm">{error}</div>}
      {items === null ? (
        <div className="flex justify-center py-6 text-ink-3">
          <LoaderIcon className="size-4 animate-spin" />
        </div>
      ) : !items.length ? (
        <div className="cc-card cc-enter flex flex-col items-center gap-3 px-6 py-12 text-ink-3">
          <InboxIcon className="size-8" />
        </div>
      ) : !groups.length ? (
        <div className="py-6 text-center text-ink-3 text-sm">{t("noResults")}</div>
      ) : (
        groups.map(([id, rows]) => (
          <PendingGroup key={id || "none"} company={id} title={company ? "" : id ? (titles[id] ?? "") : co.other} items={rows} onDone={done} onChanged={changed} />
        ))
      )}
    </div>
  );
};

const PendingGroup: FC<{
  company: string; title: string; items: PendingItem[]; onDone: (document: string) => void; onChanged: () => void;
}> = ({ company, title, items, onDone, onChanged }) => {
  const ask = useAskCC(company, title);
  const hand = items.filter((item) => !choosable(item));
  return (
    <section className="flex flex-col gap-2">
      {title && <h2 className="cc-section-label !px-0 !pt-0">{title}</h2>}
      {items.map((item) =>
        choosable(item) ? (
          <ChooseRow key={item.document} item={item} onDone={onDone} onChanged={onChanged} onAsk={() => ask([item])} />
        ) : (
          <PendingRow key={item.document} item={item} />
        ),
      )}
      {hand.length > 0 && (
        <button type="button" className="cc-btn self-start" data-slot="cc-hand-to-cc" onClick={() => ask(hand)}>
          <SparklesIcon className="size-4" />
          {co.handToCC}
        </button>
      )}
    </section>
  );
};

/** 交给 CC：在这家公司下开新对话（月份取单据月份），预填「处理这几张：…」，不自动发送（会话栏「本月」卡同一个）。 */
export function useAskCC(company: string, title: string) {
  const start = useStartIn();
  const withPeriod = useProjects((s) => s.period);
  return (items: readonly PendingItem[]) => {
    hintTitle(company, title); // 侧栏还没有这家时，新对话先按这个名称搭出这一组
    start(company ? { project: company, period: withPeriod ? monthOfItems(items) : "" } : {}, handText(items), false);
  };
}

/** 一张单的摘要：单号 · 对方 · 金额 · 日期，下面一行原因。 */
const Summary: FC<{ item: PendingItem }> = ({ item }) => (
  <span className="min-w-0 flex-1">
    <span className="block truncate text-ink text-sm">
      {[item.invoice_no, item.counterparty, formatAmount(item.amount), item.date].filter(Boolean).join(" · ") || item.document}
    </span>
    <span className="block truncate text-ink-3 text-xs">{item.reason_label}</span>
  </span>
);

const PendingRow: FC<{ item: PendingItem }> = ({ item }) => (
  <div className="cc-list-item items-center" data-slot="cc-pending-row">
    <Summary item={item} />
  </div>
);

/** 能当场选的单：点开才取要选的项（每项一个下拉，建议项第一、预选），点「确认」记下并重推。
 *  head：会话栏「本月」卡的一行 26 高（码名 · 单据 · 金额，components/month-ledger.tsx），换掉默认的两行摘要；展开后同一套。 */
export const ChooseRow: FC<{ item: PendingItem; onDone: (document: string) => void; onChanged: () => void; onAsk: () => void; head?: ReactNode }> = ({
  item,
  onDone,
  onChanged,
  onAsk,
  head,
}) => {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<ChoicesView | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    setView(null);
    try {
      const next = await loadChoices(item);
      setView(next);
      setValues(Object.fromEntries(next.needed.map((need) => [choiceKey(need), initialChoice(next, need)])));
    } catch (e) {
      setError(shownError(e));
    }
  }, [item]);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !view) void load();
  };
  const confirm = async () => {
    if (!view) return;
    setBusy(true);
    setError("");
    try {
      const result = await answerChoices(item, view, values);
      if (answered(result)) onDone(item.document);
      else if (result.status === "needs_choice") await load();
      else {
        setError(result.reason ?? "");
        onChanged();
      }
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  };
  const kinds = (item.choice?.kinds ?? []).map((kind) => co.kind[kind] ?? kind).join(" · ");
  return (
    <div className="cc-list-item flex-col" data-slot="cc-pending-choose" data-variant={head ? "row" : undefined} data-open={open || undefined}>
      <button type="button" className={head ? "cc-pending-toggle cc-panel-row" : "cc-pending-toggle flex min-w-0 items-center gap-2 text-start"}
        aria-expanded={open} onClick={toggle}>
        {head ?? (
          <span className="min-w-0 flex-1">
            <span className="block truncate text-ink text-sm">
              {[item.invoice_no, item.counterparty, formatAmount(item.amount), item.date].filter(Boolean).join(" · ") || item.document}
            </span>
            <span className="block truncate text-ink-3 text-xs">{kinds || item.reason_label}</span>
          </span>
        )}
        <ChevronDownIcon className="cc-project-chevron shrink-0 text-ink-3" />
      </button>
      {open && !view && !error && (
        <div className="flex justify-center py-2 text-ink-3">
          <LoaderIcon className="size-4 animate-spin" />
        </div>
      )}
      {open && view && choosableView(view) && (
        <>
          {view.needed.map((need) => (
            <label key={choiceKey(need)} className="cc-field">
              <span className="truncate text-xs">{[co.kind[need.kind] ?? need.kind, need.label].filter(Boolean).join(" · ")}</span>
              <select
                className="cc-input"
                value={values[choiceKey(need)] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [choiceKey(need)]: e.target.value }))}
              >
                {!values[choiceKey(need)] && <option value="" disabled>—</option>}
                {optionsOf(view, need).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
          ))}
          <button type="button" className="cc-btn self-end" data-primary=""
            disabled={busy || view.needed.some((need) => !values[choiceKey(need)])} onClick={() => void confirm()}>
            {busy && <LoaderIcon className="size-4 animate-spin" />}
            {co.confirm}
          </button>
        </>
      )}
      {open && view && !choosableView(view) && (
        // 科目表还在读、可选值没报上来：这张交给 CC（它会等电脑读完再问）
        <>
          {view.wait?.message && <p className="text-ink-3 text-sm">{view.wait.message}</p>}
          <button type="button" className="cc-btn self-start" onClick={onAsk}>
            <SparklesIcon className="size-4" />
            {co.handToCC}
          </button>
        </>
      )}
      {error && <p role="alert" className="text-danger text-sm">{error}</p>}
    </div>
  );
};

/** 待处理视图（?view=pending）：列表页样式（设计文档 3.14 / 4.5：标题、搜索、行同侧栏行）；从公司组头或公司页来的只看这家。 */
export const PendingView: FC = () => {
  const company = useCompanyNav((s) => s.pendingCompany);
  const title = useTitles(company ? [company] : [])[company] ?? "";
  const [search, setSearch] = useState("");
  return (
    <div data-slot="cc-scroll" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="cc-page">
        <div className="cc-page-head">
          <h1 className="cc-page-title">{co.pending}</h1>
        </div>
        <label className="cc-page-search">
          <SearchIcon />
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} aria-label={t("search")} placeholder={t("search")} />
        </label>
        {company && (
          <div className="mb-4">
            <span className="cc-chip">
              {title}
              <button type="button" className="-me-1 flex items-center" aria-label={t("close")} onClick={() => useCompanyNav.setState({ pendingCompany: "" })}>
                <XIcon className="size-3.5" />
              </button>
            </span>
          </div>
        )}
        <PendingList company={company} search={search} />
      </div>
    </div>
  );
};
