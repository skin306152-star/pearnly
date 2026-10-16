// 月结台账（数字会计员第 1 期，lib/month.ts）：右侧会话栏「本月」卡（对话挂的公司与月份）与公司页会话栏的「月结」卡（带月份
// 胶囊，默认上个月）共用同一套。卡头右侧一句状态见 monthSummary。
//
//   四段   收单 / 录账 / 核对 / 报税，一行一段：状态点（empty 空心、open 进行中、attention 有待办、clear 已完成）+ 段名 +
//          右侧 12px 淡色短计数；服务端给 null 的段（不是 Express、没有录入 / 报税权限）不列。核对一段写谁、何时回读的
//          （用户 10-15 定：能用这条连接的人都看得到）；多本账套时每本一行缩进子行。单据超过上限只算了一部分时卡底写一行。
//   异常   按严重度分组（需处理 → 需留意 → 知悉，组内服务端顺序；组标题的数是服务端按全部算的），需处理 / 需留意先列 8 条、
//          知悉先列 3 条，其余「显示更多」；清单被截断时卡底写还有几条。一条一行 26：码名、单据 / 对方（或任务、账套、草稿的
//          问题与分店）、金额（汇总的写「N 张」，草稿问题写「N 项」），行尾是去处：
//            choice_needed  当场选（pending.tsx 的 ChooseRow，F18 规则：只预选已定的或服务端建议的，没有就空着必须人选）
//            task           到定时任务页并定位到那个任务
//            其余           交给 CC：在这家开新对话、预填不自动发送（单据同待处理的「交给 CC」，重复单号、回读、草稿各有一句；
//                           单据写单号 · 对方 · 金额 · 日期，不写内部记录名）。同号的几张服务端合成一条，行写「单号 · N 张」
//          知悉的整行淡色（不催）。
//   自动推送（数字会计员第 3 期第 12 条）：卡头下一行「自动推送 · 只提议 / 按规则 / 整月」，降过档时跟一个短原因（核不上 /
//          被改，按码翻译）；不是 Express 公司（服务端给 null）不显示。录账一段写「已录 N/总数（自动 M）」。CC 准备好的提议
//          （cc_proposal）行尾「推送」（待处理的 ProposalRow），所在组标题旁「全部推送」（一次最多 20 张，多的再点）；
//          写入与预期不符、CC 判断推送的交给 CC（预填不发送）。没有确认步骤，不加说明文字。
//   空态   会话栏：没取到、取不到月份、整个月什么都没有时整张卡不显示；出错时显示原因；卡头是当月写「本月」，否则写月份。
//          公司页：一直显示，月份胶囊与「在这家新建对话」的同一个（页面选「不指定」时台账看上个月），取的时候转圈。
//
// 文案走 i18n.ts 的 co.ledger（服务端只给码）；样式只用 globals.css 的 .cc-panel* / .cc-month-* 与变量。

import { ChevronRightIcon, LoaderIcon, SparklesIcon } from "lucide-react";
import { type FC, useState } from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { PanelCard } from "@/components/company-cards";
import { AccountSetRow, ChooseRow, ProposalRow, useAskCC } from "@/components/pending";
import { PeriodChip } from "@/components/period-chip";
import { co } from "@/i18n";
import { branchShort, docLine, docList, formatAmount, isPeriod, lastMonth, monthLabel, periodOf, pushProposals, refreshPendingCounts, useCompanyNav } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { useProjects } from "@/lib/managed-agents/projects";
import {
  accountSetException,
  type Automation,
  choosableException,
  dayShort,
  droppedReason,
  dayTime,
  emptyMonth,
  exceptionSubject,
  groupExceptions,
  type Month,
  type MonthException,
  pendingOf,
  proposalException,
  type ReadbackSet,
  type Severity,
  severityCount,
  type Stage,
  shownStages,
  taxDue,
  unlisted,
  useMonth,
} from "@/lib/month";
import { useView } from "@/lib/nav";
import { Marquee } from "@/components/ui/marquee";

/** 每组先列几条（其余「显示更多」）；知悉的不催，先列得少。 */
export const GROUP_SHOWN = 8;
export const GROUP_SHOWN_BY: Record<Severity, number> = { action: GROUP_SHOWN, warn: GROUP_SHOWN, info: 3 };

/** 申报草稿的表名（泰国税务局的正式名，三种语言一样）。 */
export const FORM_NAME: Record<string, string> = { vat_return: "ภ.พ.30", withholding: "ภ.ง.ด." };

/** 一本账套的回读：还在读、谁何时读的、失败、还没读。 */
export function setSummary(set: ReadbackSet): string {
  if (set.status === "reading" || set.newer === "reading") return co.ledger.reading;
  if (set.status === "ready") return co.ledger.readBack(dayTime(set.ready_at), set.requested_by_name || set.requested_by || "");
  if (set.status === "failed") return co.ledger.readFailed;
  return co.ledger.code.readback_needed;
}

/** 一段的短计数（右侧淡色）；没有可写的回空，行上改写状态词。 */
export function stageSummary(stage: Stage, month: Pick<Month, "stages">): string {
  const s = month.stages;
  if (stage === "intake") {
    const n = s.intake.received + s.intake.no_date;
    return n ? co.ledger.docs(n) : "";
  }
  if (stage === "entry") {
    const e = s.entry;
    const total = e.pushed + e.in_flight + e.waiting + e.not_pushed;
    // 收单的张数里还有只识别、不录的：写出来，两段的数才对得上
    // 已推里有自动推的（数字会计员第 3 期）：「已录 2/7（自动 1）」
    const entered = total ? co.ledger.entered(e.pushed, total) + (e.auto ? co.ledger.auto(e.auto) : "") : "";
    const parts = [entered, s.intake.recognized_only ? co.ledger.recognizedOnly(s.intake.recognized_only) : ""];
    return parts.filter(Boolean).join(" · ");
  }
  if (stage === "verify") {
    // 一本：谁、何时回读的；多本：读了几本（每本一行缩进子行）；还没读的不写数、写状态词
    const sets = s.verify?.sets ?? [];
    if (sets.length === 1) return sets[0].status === "none" ? "" : setSummary(sets[0]);
    if (sets.some((set) => set.status === "reading" || set.newer === "reading")) return co.ledger.reading;
    return sets.length ? co.ledger.readCount(sets.filter((set) => set.status === "ready").length, sets.length) : "";
  }
  const due = taxDue(s.tax);
  return due ? co.ledger.due(dayShort(due)) : "";
}

/** 卡头右侧的一句（开发窗口 10-16 定）：有需处理的写「需处理 N」，否则有需留意的写「需留意 N」；都没有时写这个月走到哪：
 *  每一段都完成写「已完成」，都还没有写「暂无」，否则写第一段没完成的「段名 · 状态」。 */
export function monthSummary(month: Month): string {
  const action = severityCount(month, "action");
  if (action) return co.card.toDo(action);
  const warn = severityCount(month, "warn");
  if (warn) return co.card.toCheck(warn);
  const stages = shownStages(month);
  if (!stages.length) return "";
  const states = stages.map((stage) => month.stages[stage]!.state);
  if (states.every((state) => state === "clear")) return co.ledger.state.clear ?? "";
  if (states.every((state) => state === "empty")) return co.ledger.state.empty ?? "";
  const at = stages.find((stage) => month.stages[stage]!.state !== "clear")!;
  const state = month.stages[at]!.state;
  return `${co.ledger.stage[at]} · ${co.ledger.state[state] ?? state}`;
}

/** 自动推送一行的右侧：档位名，降过档时跟短原因（「只提议 · 核不上」）。 */
export function automationSummary(automation: Automation): string {
  const reason = droppedReason(automation);
  return [co.ledger.tier[automation.tier] ?? automation.tier, reason ? co.ledger.dropped[reason] : ""].filter(Boolean).join(" · ");
}

/** 码名（认不出的码用服务端的原因文字，再没有写码本身）。 */
export const codeName = (item: Pick<MonthException, "code" | "reason_label">) => co.ledger.code[item.code] ?? (item.reason_label || item.code);

// ---------------------------------------------------------------- 纯展示（测试直接渲染）
const StageRow: FC<{ stage: Stage; month: Month }> = ({ stage, month }) => {
  const state = month.stages[stage]!.state;
  const word = co.ledger.state[state] ?? state;
  const sets = stage === "verify" ? (month.stages.verify?.sets ?? []) : [];
  return (
    <>
      <div className="cc-panel-row" data-slot="cc-month-stage" data-stage={stage} data-state={state}>
        <span className="cc-month-dot" data-state={state} role="img" aria-label={word} title={word} />
        <Marquee className="min-w-0 flex-1 truncate">{co.ledger.stage[stage]}</Marquee>
        <Marquee className="cc-panel-extra">{stageSummary(stage, month) || word}</Marquee>
      </div>
      {sets.length > 1 &&
        sets.map((set) => (
          <div key={`${set.connection}|${set.key}`} className="cc-panel-row cc-panel-indent" data-slot="cc-month-set">
            <Marquee className="cc-month-sub">{set.label || set.key}</Marquee>
            <Marquee className="cc-panel-extra">{setSummary(set)}</Marquee>
          </div>
        ))}
    </>
  );
};

/** 同号的一组（服务端合成一条，带每张的单号等）。 */
export const sameNumberGroup = (item: MonthException) => item.code === "duplicate_number" && (item.documents?.length ?? 0) > 1;

/** 一条异常的一行内容：码名、单据 / 对方、金额（汇总的写「N 张」，草稿问题写「N 项」）；同号的一组写「单号 · N 张」。 */
const ItemHead: FC<{ item: MonthException }> = ({ item }) => {
  const group = sameNumberGroup(item);
  const extra = group ? "" : formatAmount(item.amount) || (item.count ? (item.code === "tax_issue" ? co.ledger.issues(item.count) : co.ledger.docs(item.count)) : "");
  return (
    <>
      <Marquee className="cc-month-code">{codeName(item)}</Marquee>
      <Marquee className="cc-month-sub">{group ? co.ledger.sameNumber(item.invoice_no || "", item.documents!.length) : exceptionSubject(item, branchShort)}</Marquee>
      {extra && <Marquee className="cc-panel-extra tabular-nums">{extra}</Marquee>}
    </>
  );
};

export type LedgerHandlers = {
  /** 去处：到任务页 / 交给 CC（预填、不自动发送）。 */
  onAct: (item: MonthException) => void;
  /** 当场选完一张（已交出去）。 */
  onDone: (document: string) => void;
  /** 当场选没成（原因记在单上）：重取。 */
  onChanged: () => void;
};

const ExceptionRow: FC<{ item: MonthException; company: string } & LedgerHandlers> = ({ item, company, onAct, onDone, onChanged }) => {
  if (proposalException(item)) {
    return <ProposalRow item={pendingOf(item, company)} head={<ItemHead item={item} />} onDone={onDone} onChanged={onChanged} />;
  }
  if (choosableException(item)) {
    return <ChooseRow item={pendingOf(item, company)} head={<ItemHead item={item} />} onDone={onDone} onChanged={onChanged} onAsk={() => onAct(item)} />;
  }
  if (accountSetException(item)) {
    return <AccountSetRow item={pendingOf(item, company)} head={<ItemHead item={item} />} onDone={onDone} onChanged={onChanged} onAsk={() => onAct(item)} />;
  }
  const task = item.action === "task" && !!item.task;
  const label = task ? co.ledger.openTask : co.handToCC;
  return (
    <button type="button" className="cc-panel-row" data-slot="cc-month-item" data-code={item.code} data-severity={item.severity} title={label}
      onClick={() => onAct(item)}>
      <ItemHead item={item} />
      {task ? <ChevronRightIcon aria-hidden /> : <SparklesIcon aria-hidden />}
      <span className="sr-only">{label}</span>
    </button>
  );
};

const itemKey = (item: MonthException, i: number) =>
  [item.code, item.document ?? item.task ?? item.draft ?? item.account_set_key ?? "", item.issue ?? "", i].join(":");

/** 组标题：严重度与条数；组里有 CC 准备好的提议时右侧「全部推送」——这家这个月的提议一次推出去（服务端一次最多 20 张，
 *  多的再点），推完重取；推不成的原因写在标题下面。 */
const GroupHead: FC<{ month: Month; severity: Severity; proposals: boolean; onChanged: () => void }> = ({ month, severity, proposals, onChanged }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pushAll = async () => {
    setBusy(true);
    setError("");
    try {
      await pushProposals(month.company, month.period);
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy(false);
      onChanged();
    }
  };
  const heading = (
    <div className="cc-subheading flex items-center gap-1.5" role="heading" aria-level={3}>
      <span>{co.ledger.severity[severity]}</span>
      <span className="tabular-nums">{severityCount(month, severity)}</span>
    </div>
  );
  if (!proposals) return heading;
  return (
    <>
      <div className="flex min-w-0 items-center justify-between gap-2" data-slot="cc-month-group-head">
        {heading}
        <button type="button" className="cc-btn-sm" data-slot="cc-month-push-all" disabled={busy} onClick={() => void pushAll()}>
          {busy && <LoaderIcon className="animate-spin" />}
          {co.pushAll}
        </button>
      </div>
      {error && <p role="alert" className="px-1 text-danger text-sm">{error}</p>}
    </>
  );
};

/** 台账的内容：自动推送档位，四段，再是按严重度分组的异常。 */
export const LedgerBody: FC<{ month: Month; company: string } & LedgerHandlers> = ({ month, company, ...handlers }) => {
  const [more, setMore] = useState<Severity[]>([]);
  return (
    <>
      {month.automation && (
        <div className="cc-panel-row" data-slot="cc-month-automation" data-tier={month.automation.tier}>
          <Marquee className="min-w-0 flex-1 truncate">{co.ledger.automation}</Marquee>
          <Marquee className="cc-panel-extra">{automationSummary(month.automation)}</Marquee>
        </div>
      )}
      <div className="flex flex-col" data-slot="cc-month-stages">
        {shownStages(month).map((stage) => (
          <StageRow key={stage} stage={stage} month={month} />
        ))}
      </div>
      {groupExceptions(month.exceptions.items).map(([severity, items]) => {
        const all = more.includes(severity);
        const shown = GROUP_SHOWN_BY[severity];
        return (
          <div key={severity} className="flex flex-col" data-slot="cc-month-group" data-severity={severity}>
            <GroupHead month={month} severity={severity} proposals={items.some(proposalException)} onChanged={handlers.onChanged} />
            {(all ? items : items.slice(0, shown)).map((item, i) => (
              <ExceptionRow key={itemKey(item, i)} item={item} company={company} {...handlers} />
            ))}
            {!all && items.length > shown && (
              <button type="button" className="cc-show-more" onClick={() => setMore((before) => [...before, severity])}>
                {co.showMore}
              </button>
            )}
          </div>
        );
      })}
      {unlisted(month) > 0 && (
        <div className="cc-panel-row" data-slot="cc-month-note">
          <Marquee className="cc-month-sub">{co.ledger.moreItems(unlisted(month))}</Marquee>
        </div>
      )}
      {month.truncated && (
        <div className="cc-panel-row" data-slot="cc-month-note">
          <Marquee className="cc-month-sub">{co.ledger.partial}</Marquee>
        </div>
      )}
    </>
  );
};

// ---------------------------------------------------------------- 去处
/** 重复单号只是知悉（推送不查重不拦）：同号的几张一起交过去，说明是核对同号，不是「处理」。每张一行：单号 · 对方 · 金额 ·
 *  日期，不写内部记录名（测试窗口第 3 轮）；旧服务端没给 documents 时只写这一张。 */
export function duplicatePrefill(item: MonthException): string {
  const lines = (item.documents?.length ? item.documents : [item]).map(docLine).filter(Boolean);
  return co.ledger.duplicateText(docList(lines));
}

/** 点了一条异常：任务 → 任务页定位；单据 → 交给 CC（同待处理）；回读 / 草稿 → 在这家开新对话、预填一句（都不自动发送）。 */
function useLedgerActions(company: string, period: string, drop: (document: string) => void, reload: () => Promise<void>): LedgerHandlers {
  const start = useStartIn();
  const { setView } = useView();
  const withPeriod = useProjects((s) => s.period);
  const title = useProjects((s) => s.projects.find((p) => p.id === company)?.title ?? "");
  const ask = useAskCC(company, title);
  const onAct = (item: MonthException) => {
    if (item.action === "task" && item.task) {
      useCompanyNav.setState({ taskCompany: company, taskFocus: item.task, taskSeed: null });
      setView("tasks");
      return;
    }
    const filing = { project: company, period: withPeriod ? period : "", scope: "" };
    if (item.code === "duplicate_number" && item.document) {
      start(filing, duplicatePrefill(item), false);
      return;
    }
    if (item.document) {
      ask([pendingOf(item, company)]);
      return;
    }
    const month = monthLabel(period);
    if (item.draft) start(filing, co.draftText(FORM_NAME[item.kind ?? ""] ?? item.kind ?? "", month), false);
    else if (item.action === "readback") start(filing, co.ledger.readbackText(item.label || item.account_set_key || "", month), false);
    else start(filing, "", false);
  };
  return {
    onAct,
    onDone: (document) => {
      drop(document);
      void refreshPendingCounts();
    },
    onChanged: () => {
      void reload();
      void refreshPendingCounts();
    },
  };
}

// ---------------------------------------------------------------- 会话栏「本月」卡
/** 对话挂的公司与月份；没有月份、取不到、整个月什么都没有时不显示（出错时显示原因）。 */
export const MonthCard: FC<{ company: string; period: string }> = ({ company, period }) => {
  const { month, error, reload, drop } = useMonth(company, period);
  const handlers = useLedgerActions(company, period, drop, reload);
  if (!month && !error) return null;
  if (month && emptyMonth(month) && !error) return null;
  const title = period === periodOf(new Date()) ? co.thisMonth : monthLabel(period);
  return (
    <PanelCard id="month" title={title} summary={month ? monthSummary(month) : undefined} slot="cc-panel-month">
      {error && <p role="alert" className="px-1 text-danger text-sm">{error}</p>}
      {month && <LedgerBody month={month} company={company} {...handlers} />}
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 公司页概览第一节
/** 公司页的月结台账（在公司页的会话栏里）：卡头月份胶囊（最近 12 个月，不列「不指定」）与「在这家新建对话」共用页面的
 *  月份（lib/company-panel 的 usePagePeriod），页面是「不指定」时看上个月；一直显示，取的时候转圈。 */
export const MonthSection: FC<{ company: string; period: string; onPeriod: (p: string) => void }> = ({ company, period: chosen, onPeriod }) => {
  const period = isPeriod(chosen) ? chosen : lastMonth();
  const { month, error, loading, reload, drop } = useMonth(company, period);
  const handlers = useLedgerActions(company, period, drop, reload);
  return (
    <PanelCard
      id="month"
      title={co.ledger.title}
      summary={month ? monthSummary(month) : undefined}
      slot="cc-company-month"
      extra={<PeriodChip value={period} onChange={onPeriod} allowNone={false} look="cc-chin-btn" />}
    >
      {loading && (
        <div className="flex justify-center py-2 text-ink-3">
          <LoaderIcon className="size-4 animate-spin" />
        </div>
      )}
      {error && <p role="alert" className="px-1 text-danger text-sm">{error}</p>}
      {month && <LedgerBody month={month} company={company} {...handlers} />}
    </PanelCard>
  );
};
