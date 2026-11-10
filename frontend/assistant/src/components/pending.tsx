// 待处理（补充乙）：要人处理的单。用户 11-04：左侧「待处理」入口和页面不要了，并进对话——
//   - 能归到公司和月份的单落在「这家公司 · 这个月」的对话末尾一个「需要处理」框（PendingInChat，thread.tsx 的 Tail 插槽）：
//     框是页面按服务端的待处理列表现画的，不写进会话（写进去会开一轮、要花钱）；服务端 pending_counts 的 months 指定了哪段对话
//     （本人这家这个月最近更新、没归档的那段）就只在那段出，还没有对话的侧栏有一行占位（projects.tsx），点了开新对话、框在新对话里；
//     旧服务端没给 months 时在这家这个月的任一段对话里出。「交给 CC」在这段对话的输入框里预填，不另开对话、不自动发送。
//   - 右侧会话栏与公司页的「待处理」卡照旧（直写右侧不动）：同一个列表组件，会话栏的只列对话月份以外的（otherMonths）。
//
//   数据：companies.api.pending（服务端从推送记录与录入状态现算，不新增存储）。按公司分组；归不上公司的放最后。
//   停在要人定的前置条件的（action choose 当场选、account_set 待选账套、mrerp MR.ERP 导入菜单 / 默认值；用户 11-05）：同一家的几张
//   一起判一次（共用写账前置判定 write_precheck check），出一张通用提问卡（PendingAsk，同 CC 的 ask_user：一题一页、预选项排第一带
//   「(推荐)」、「其他」可搜）；「提交」存下答案（answer），服务端把这些单重新排队写入；判定失败就地「交给 CC」。对话末尾那张单独一张、
//   带框与「需要您的输入」，在「需要处理」框上面；会话栏、公司页、月结台账里嵌着。
//   CC 准备好的提议（action push，数字会计员第 3 期：自动推前判成 A / B、这家的档位还只出提议）：行上不露「推送」，点开取
//   CC 备好的内容（assistant.pending_api.proposal_detail，只读：账套、每一项做法、日期、单号、对方、金额），下面「推送」按当场选
//   同一条路推（assistant.pending_api.answer 交空的选择，服务端照常重判、算人发起），没有确认步骤。
//   推送 / 作答回包停在要人选时不报错、直接重取；其余失败显示服务端给人看的话，没有就写通用的「推送失败」，不显示原因码。
//   其余一组一行「交给 CC」：在这家公司下开新对话（月份取单据月份），输入框预填「处理这几张：…」并带上单据编号，不自动发送。
//   带 url 的（ERP：写账本没成的采购 / 销售单，服务端 express/pending.py，action 只有 open）：点那一行在新窗口打开单据页，
//   在那里选一次、再写一次；不进「交给 CC」。这家的待处理变了服务端推 pearnly.project（part pending），同样重取。
//   处理完实时消失：推送状态的站点事件（cowork_intake_changed）来了重取，回到前台重取（lib/live.ts）。
//   金额是服务端的十进制字符串，只插千分位。样式只用 globals.css 的 .cc-* 与变量。

import { useAui, useAuiState } from "@assistant-ui/react";
import { ChevronDownIcon, InboxIcon, LoaderIcon } from "lucide-react";
import { type FC, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { focusComposer, useStartIn } from "@/components/assistant-ui/projects";
import { co, t } from "@/i18n";
import {
  cachedDetail,
  docLine,
  docList,
  formatAmount,
  isPeriod,
  listCompanies,
  loadPending,
  loadProposal,
  onPendingChange,
  openSitePage,
  PENDING_EVENTS,
  type PendingItem,
  type ProposalView,
  pushOutcome,
  pushProposal,
  refreshPendingCounts,
  usePendingCounts,
} from "@/lib/companies";
import { useLiveRefresh } from "@/lib/live";
import { filingOf, hintTitle, useProjects, useSection } from "@/lib/managed-agents/projects";
import { shownError } from "@/lib/frappe";
import { Marquee } from "@/components/ui/marquee";
import { reportCaught, withReport } from "../lib/caught";
import { AskCard } from "./tool-uis/ask-card";
import { askActions, type AskItem, type AskResult, itemQuestions, itemsOfCheck, keyAnswers } from "./tool-uis/ask-shared";
import { useAction } from "./tool-uis/operator-card";

const FIXED: () => Record<string, string> = () => ({ cash: co.payCash, credit: co.payCredit, claim: co.vatClaim, expense: co.vatExpense });

/** 这张单是不是「当场选」那一类（服务端 action choose，带推送去向）。 */
export function choosable(item: PendingItem): boolean {
  return item.action === "choose" && !!item.choice?.connection;
}

/** CC 准备好的提议、能当场「推送」的那一类（服务端 action push，带推送去向，数字会计员第 3 期）。 */
export function proposal(item: PendingItem): boolean {
  return item.action === "push" && !!item.choice?.connection;
}

/** MR.ERP 停在要选导入菜单 / 默认值、能当场选的那一类（服务端 action mrerp，带推送去向，2026-10-19）。 */
export function mrerpChoosable(item: PendingItem): boolean {
  return item.action === "mrerp" && !!item.choice?.connection;
}

/** 停在「待选账套」、能当场换账套的那一类（服务端 action account_set，带推送去向）。 */
export function accountSetChoosable(item: PendingItem): boolean {
  return item.action === "account_set" && !!item.choice?.connection;
}

/** 停在要人定的前置条件、页面当场出提问卡的那几类：当场选（choose）、待选账套（account_set）、MR.ERP 导入菜单 / 默认值（mrerp）。 */
export const asksFirst = (item: PendingItem) => choosable(item) || accountSetChoosable(item) || mrerpChoosable(item);

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
      .catch(withReport(() => [], "pending"))
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

/** 待处理单的列表状态（会话栏卡、公司页卡、对话末尾的框共用）：读取，站点事件来了、回到前台重取，作答成功的当场拿掉。 */
export function usePendingItems(company: string) {
  const [items, setItems] = useState<PendingItem[] | null>(null);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    try {
      setItems(await loadPending(company, 200));
      setError("");
    } catch (e) { reportCaught(e, "pending");
      setItems((before) => before ?? []);
      setError(shownError(e));
    }
  }, [company]);
  useEffect(() => {
    void reload();
  }, [reload]);
  useEffect(() => {
    setItems(null);
  }, [company]);
  useLiveRefresh(reload, { events: PENDING_EVENTS });
  useEffect(() => onPendingChange(() => void reload()), [reload]);
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

/** 待处理列表；company 给了只列这家（会话栏、公司页的卡片，没有待处理时整块不出现）。 */
export const PendingList: FC<{ company?: string; embedded?: boolean }> = ({ company = "", embedded = false }) => (
  <PendingItems company={company} embedded={embedded} {...usePendingItems(company)} />
);

/** 列表本身（数据由 usePendingItems 给；会话栏卡、对话末尾的框先按月份筛过再交进来）。ask：「交给 CC」换成调用方的做法
 *  （对话末尾的框：在这段对话的输入框里预填）。 */
export const PendingItems: FC<PendingState & { company: string; embedded?: boolean; ask?: (items: readonly PendingItem[]) => void }> = ({
  items,
  error,
  done,
  changed,
  company,
  embedded = false,
  ask,
}) => {
  const groups = useMemo(() => {
    const map = new Map<string, PendingItem[]>();
    for (const item of items ?? []) {
      const id = company || item.company || "";
      map.set(id, [...(map.get(id) ?? []), item]);
    }
    // 归不上公司的放最后
    return [...map.entries()].sort((a, b) => Number(!a[0]) - Number(!b[0]));
  }, [items, company]);
  const titles = useTitles(groups.map(([id]) => id).filter(Boolean));

  if (embedded && (!items || !items.length) && !error) return null;
  return (
    <div className="flex flex-col gap-(--cc-space-16)" data-slot="cc-pending">
      {error && <div role="alert" className="cc-alert">{error}</div>}
      {items === null ? (
        <div className="flex justify-center py-(--cc-space-24) text-ink-3">
          <LoaderIcon className="cc-spinner" />
        </div>
      ) : !items.length ? (
        <div className="cc-empty cc-enter">
          <InboxIcon />
        </div>
      ) : !groups.length ? (
        <div className="cc-empty-line">{t("noResults")}</div>
      ) : (
        groups.map(([id, rows]) => (
          <PendingGroup key={id || "none"} company={id} title={company ? "" : id ? (titles[id] ?? "") : co.other} items={rows} onDone={done} onChanged={changed} ask={ask} />
        ))
      )}
    </div>
  );
};

const PendingGroup: FC<{
  company: string; title: string; items: PendingItem[]; onDone: (document: string) => void; onChanged: () => void;
  ask?: (items: readonly PendingItem[]) => void;
}> = ({ company, title, items, onDone, onChanged, ask: askHere }) => {
  const askNew = useAskCC(company, title);
  const ask = askHere ?? askNew;
  // 归不上公司的没法判定：照旧一行一行、交给 CC
  const asks = company ? items.filter(asksFirst) : [];
  const hand = items.filter((item) => !item.url && !asks.includes(item) && !proposal(item));
  return (
    <section className="flex flex-col gap-(--cc-space-8)">
      {title && <h2 className="cc-section-label" data-flush="">{title}</h2>}
      {asks.length > 0 && (
        <PendingAsk key={asks.map((i) => i.document).join("\n")} company={company} items={asks} framed={false} onChanged={onChanged} onAsk={() => ask(asks)} />
      )}
      {items.filter((item) => !asks.includes(item)).map((item) =>
        proposal(item) ? (
          <ProposalRow key={item.document} item={item} onDone={onDone} onChanged={onChanged} />
        ) : (
          <PendingRow key={item.document} item={item} />
        ),
      )}
      {hand.length > 0 && (
        <button type="button" className="cc-card-btn self-start" data-slot="cc-hand-to-cc" onClick={() => ask(hand)}>
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
    <Marquee className="block truncate text-ink text-body">
      {[item.invoice_no, item.counterparty, formatAmount(item.amount), item.date].filter(Boolean).join(" · ") || item.document}
    </Marquee>
    <Marquee className="block truncate text-ink-3 text-caption">{item.reason_label}</Marquee>
  </span>
);

const PendingRow: FC<{ item: PendingItem }> = ({ item }) =>
  item.url ? (
    <button type="button" className="cc-list-item items-center text-start" data-slot="cc-pending-row" title={t("openInBrowser")}
      onClick={() => openSitePage(item.url ?? "")}>
      <Summary item={item} />
    </button>
  ) : (
    <div className="cc-list-item items-center" data-slot="cc-pending-row">
      <Summary item={item} />
    </div>
  );

/** 提议展开后的只读内容（一行一项：左类别名、右值）：推往的账套、CC 备好的每一项做法、日期、单号、对方、税前 / 税额 / 合计；
 *  没有值的不列。做法的值同当场选的标签规则（optionsOf）：现 / 赊、抵 / 记费用用页面的词，其余是服务端按主档写的「码 名称」。 */
export function proposalRows(view: ProposalView): Array<[string, string]> {
  const fixed = FIXED();
  const rows: Array<[string, string]> = [[co.proposal.accountSet, view.account_set.label || view.account_set.key]];
  for (const one of view.items) rows.push([co.kind[one.kind] ?? one.label ?? one.kind, fixed[one.value] ?? (one.value_label || one.value)]);
  rows.push(
    [co.proposal.date, view.date],
    [co.proposal.docNo, view.ref_no],
    [co.proposal.party, view.party],
    [co.proposal.beforeVat, formatAmount(view.before_vat)],
    [co.proposal.vat, formatAmount(view.vat)],
    [co.proposal.total, formatAmount(view.total)],
  );
  return rows.filter(([, value]) => !!value);
}

export const ProposalDetail: FC<{ view: ProposalView }> = ({ view }) => (
  <dl className="flex min-w-0 flex-col gap-(--cc-space-4)" data-slot="cc-proposal-detail">
    {proposalRows(view).map(([label, value], i) => (
      <div key={i} className="flex min-w-0 items-baseline justify-between gap-(--cc-space-12)">
        <dt className="shrink-0 text-ink-3">{label}</dt>
        <dd className="min-w-0 text-end text-ink wrap-break-word">{value}</dd>
      </div>
    ))}
  </dl>
);

/** CC 准备好的提议（数字会计员第 3 期）：行上不露「推送」，点开这一行先取 CC 备好的内容显示出来，下面才有「推送」（用户 10-19 定，
 *  同当场选那一行的展开）；推成了从列表去掉，停在要人选时列表重取（这张单换成当场选那一行），推不成（写不进）原因记在单上、
 *  列表重取，这次的话显示在这一行下面。 */
export const ProposalRow: FC<{ item: PendingItem; onDone: (document: string) => void; onChanged: () => void; head?: ReactNode }> = ({
  item,
  onDone,
  onChanged,
  head,
}) => {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<ProposalView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      setView(await loadProposal(item));
    } catch (e) { reportCaught(e, "pending");
      setError(shownError(e));
    }
  }, [item]);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !view) void load();
  };
  const push = async () => {
    setBusy(true);
    setError("");
    try {
      const outcome = pushOutcome(await pushProposal(item), co.ledger.code.push_failed!);
      if (outcome.kind === "done") onDone(item.document);
      else if (outcome.kind === "choose") onChanged();
      else {
        setError(outcome.text);
        onChanged();
      }
    } catch (e) { reportCaught(e, "pending");
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="cc-list-item flex-col" data-slot="cc-pending-proposal" data-variant={head ? "row" : undefined} data-open={open || undefined}>
      <button type="button" className={head ? "cc-pending-toggle cc-panel-row" : "cc-pending-toggle flex min-w-0 items-center gap-(--cc-space-8) text-start"}
        aria-expanded={open} onClick={toggle}>
        {head ?? <Summary item={item} />}
        <ChevronDownIcon className="cc-project-chevron shrink-0 text-ink-3" />
      </button>
      {open && <ProposalBody view={view} error={error} busy={busy} onPush={() => void push()} />}
      {error && <p role="alert" className="text-danger text-body">{error}</p>}
    </div>
  );
};

/** 展开后的那一块：内容还没取到时转圈（同当场选那一行），取到了是只读内容，下面靠右「推送」；取不到（出错）只显示错误。 */
export const ProposalBody: FC<{ view: ProposalView | null; error: string; busy: boolean; onPush: () => void }> = ({ view, error, busy, onPush }) => (
  <>
    {!view && !error && (
      <div className="flex justify-center py-(--cc-space-8) text-ink-3">
        <LoaderIcon className="cc-spinner" />
      </div>
    )}
    {view && (
      <>
        <ProposalDetail view={view} />
        <button type="button" className="cc-card-btn self-end" data-primary="" data-slot="cc-pending-push" disabled={busy} onClick={onPush}>
          {busy && <LoaderIcon className="cc-spinner" />}
          {co.pushNow}
        </button>
      </>
    )}
  </>
);

// ---------------------------------------------------------------- 停在要人定的单：一张提问卡
/** 停在要人定的前置条件的几张单（当场选、待选账套、MR.ERP 导入菜单 / 默认值）一起判一次（共用写账前置判定 write_precheck check），
 *  没定下的每项一题，出一张通用提问卡（tool-uis/ask-card.tsx，同 CC 的 ask_user：预选项排第一带「(推荐)」、默认选中，「其他」可搜）。
 *  「提交」= 存下答案（answer：照各自现有的记法记住，服务端把这些单重新排队写入），跳过的项不交、依赖它的单这次不写；列表重取。
 *  判出来没有要问的（公司规则已定）：一行「推送」（交空的答案，服务端照常重判重推）。判定失败：原因一行 +「交给 CC」。
 *  framed：对话末尾单独一张（带框与「需要您的输入」）；会话栏、公司页、月结台账里嵌着用时不带框。head：月结台账那一行的码名摘要。 */
export const PendingAsk: FC<{
  company: string;
  items: readonly PendingItem[];
  framed?: boolean;
  head?: ReactNode;
  onChanged: () => void;
  onAsk: () => void;
}> = ({ company, items, framed = true, head, onChanged, onAsk }) => {
  const docs = useMemo(() => items.map((item) => item.document), [items]);
  // 月结台账那一行（给了 head）：同以前的当场选，收着时只是一行，点开才判定（列表不逐行读账套）
  const [open, setOpen] = useState(!head);
  const [found, setFound] = useState<AskItem[] | null>(null);
  const [error, setError] = useState("");
  const [sent, setSent] = useState<AskResult | null>(null);
  const action = useAction();
  const ids = docs.join("\n");
  useEffect(() => {
    if (!open || !company || !ids) return;
    let alive = true;
    void askActions.check(company, ids.split("\n")).then(
      (reply) => alive && setFound(itemsOfCheck(reply)),
      (caughtError: unknown) => {
        reportCaught(caughtError, "pending-ask");
        if (alive) setError(shownError(caughtError));
      },
    );
    return () => {
      alive = false;
    };
  }, [open, company, ids]);
  const questions = useMemo(() => itemQuestions(found ?? [], company), [found, company]);
  const remember = (answers: ReadonlyArray<{ key: string; value: string }>, result: AskResult | null) =>
    void action.run(async () => {
      await askActions.remember(company, docs, answers);
      setSent(result);
      onChanged();
      void refreshPendingCounts();
    }, false);
  const content = error ? (
    <>
      <p role="alert" className="text-danger text-body">{error}</p>
      <button type="button" className="cc-card-btn self-start" data-slot="cc-hand-to-cc" onClick={onAsk}>
        {co.handToCC}
      </button>
    </>
  ) : found === null ? (
    head && open && !error && (
      <div className="flex justify-center py-(--cc-space-8) text-ink-3">
        <LoaderIcon className="cc-spinner" />
      </div>
    )
  ) : !questions.length ? (
    <>
      <button type="button" className="cc-card-btn self-start" data-primary="" data-slot="cc-pending-push" disabled={action.busy || !!sent}
        onClick={() => remember([], null)}>
        {co.pushNow}
      </button>
      {action.failure && <p role="alert" className="text-danger text-body">{action.failure}</p>}
    </>
  ) : (
    <AskCard
      questions={questions}
      phase={sent ? "answered" : "ask"}
      result={sent}
      busy={action.busy}
      failure={action.failure}
      framed={framed}
      slot="cc-pending-askq"
      onSubmit={(result) => remember(keyAnswers(found ?? [], questions, result), result)}
    />
  );
  if (!head) return <div className="flex flex-col gap-(--cc-space-8)" data-slot="cc-pending-ask">{content}</div>;
  return (
    <div className="cc-list-item flex-col" data-slot="cc-pending-ask" data-variant="row" data-open={open || undefined}>
      <button type="button" className="cc-pending-toggle cc-panel-row" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {head}
        <ChevronDownIcon className="cc-project-chevron shrink-0 text-ink-3" />
      </button>
      {open && <div className="flex flex-col gap-(--cc-space-8)">{content}</div>}
    </div>
  );
};

// ---------------------------------------------------------------- 对话末尾的「需要处理」框
/** 这段对话该不该出框、出哪家哪个月（文件头）：新对话按草稿的公司、月份，已有对话按它挂的公司、月份。 */
function useChatPending(): { company: string; month: string } | null {
  const kind = useSection("pending");
  const fresh = useAuiState((s) => s.threadListItem.status === "new");
  const remoteId = useAuiState((s) => s.threadListItem.remoteId) ?? "";
  const localId = useAuiState((s) => s.threadListItem.id);
  const draft = useProjects((s) => s.draft);
  useProjects((s) => s.filings);
  const months = usePendingCounts((s) => s.months);
  const counts = usePendingCounts((s) => s.companies);
  if (!kind) return null;
  const filing = fresh ? draft : filingOf(remoteId, localId);
  const company = filing.project;
  const month = filing.period;
  if (!company || !isPeriod(month)) return null;
  if (months === null) return counts[company] ? { company, month } : null;
  const row = months.find((m) => m.company === company && m.month === month);
  if (!row) return null;
  // 服务端指定了那段对话：只在那段出（新对话、别的对话不出）；还没有对话：新对话里出（侧栏占位行点进来的）
  if (row.conversation) return row.conversation === remoteId ? { company, month } : null;
  return fresh ? { company, month } : null;
}

// map: pending-in-chat
/** 「需要处理」框（同允许卡的一层框）：标题「N 张单要处理」，下面每张一行（当场选、推送、选账套、MR.ERP、打开单据页照旧），
 *  处理完的当场消失，都处理完整个框不出。放在对话消息最后（thread.tsx 的 Tail）。 */
export const PendingInChat: FC = () => {
  const target = useChatPending();
  if (!target) return null;
  return <ChatPendingBox key={`${target.company}:${target.month}`} company={target.company} month={target.month} />;
};

const ChatPendingBox: FC<{ company: string; month: string }> = ({ company, month }) => {
  const aui = useAui();
  const state = usePendingItems(company);
  const items = useMemo(() => (state.items === null ? null : state.items.filter((item) => pendingMonth(item) === month)), [state.items, month]);
  if (!items || !items.length) return null;
  // 「交给 CC」：在这段对话的输入框里预填，不另开对话、不自动发送
  const ask = (rows: readonly PendingItem[]) => {
    aui.thread().composer().setText(handText(rows));
    focusComposer();
  };
  // 停在要人定的前置条件的单：单独一张提问卡（同 CC 的 ask_user，带框与「需要您的输入」），在框的上面；其余照旧一行一行
  const asks = items.filter(asksFirst);
  const rows = items.filter((item) => !asksFirst(item));
  return (
    <>
      {asks.length > 0 && (
        <PendingAsk key={asks.map((i) => i.document).join("\n")} company={company} items={asks} onChanged={state.changed} onAsk={() => ask(asks)} />
      )}
      {rows.length > 0 && (
        <section className="cc-steps-card cc-ask" data-phase="ask" data-slot="cc-pending-in-chat" aria-label={co.pendingDocs(rows.length)}>
          <div className="cc-ask-q">{co.pendingDocs(rows.length)}</div>
          <div className="cc-pending-in-chat">
            <PendingItems {...state} items={rows} company={company} embedded ask={ask} />
          </div>
        </section>
      )}
    </>
  );
};
