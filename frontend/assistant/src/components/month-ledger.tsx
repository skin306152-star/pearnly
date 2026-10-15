// 月结台账（数字会计员第 1 期，lib/month.ts）：右侧会话栏「本月」卡（对话挂的公司与月份）与公司页概览第一节（带月份胶囊，
// 默认上个月）共用同一套。
//
//   四段   收单 / 录账 / 核对 / 报税，一行一段：状态点（empty 空心、open 进行中、attention 要处理、clear 已完成）+ 段名 +
//          右侧 12px 淡色短计数；服务端给 null 的段（不是 Express、没有录入 / 报税权限）不列。
//   异常   按严重度分组（要处理 → 看一眼 → 知悉，组内服务端顺序），每组先列 8 条、其余「显示更多」。一条一行 26：码名、
//          单据 / 对方（或任务、账套、草稿分店）、金额（汇总的写件数），行尾是去处：
//            choice_needed  当场选（pending.tsx 的 ChooseRow，F18 规则：只预选已定的或服务端建议的，没有就空着必须人选）
//            task           到定时任务页并定位到那个任务
//            其余           交给 CC：在这家开新对话、预填不自动发送（单据同待处理的「交给 CC」，回读 / 草稿各有一句）
//          知悉的整行淡色（不催）。
//   空态   会话栏：没取到、取不到月份、整个月什么都没有时整张卡不显示；出错时显示原因。公司页：一直显示（有月份胶囊），
//          取的时候转圈。
//
// 文案走 i18n.ts 的 co.ledger（服务端只给码）；样式只用 globals.css 的 .cc-panel* / .cc-month-* 与变量。

import { ChevronRightIcon, LoaderIcon, SparklesIcon } from "lucide-react";
import { type FC, useState } from "react";
import { useStartIn } from "@/components/assistant-ui/projects";
import { PanelCard } from "@/components/company-cards";
import { ChooseRow, useAskCC } from "@/components/pending";
import { PeriodChip } from "@/components/period-chip";
import { co } from "@/i18n";
import { branchShort, formatAmount, lastMonth, monthLabel, type PendingItem, refreshPendingCounts, useCompanyNav } from "@/lib/companies";
import { useProjects } from "@/lib/managed-agents/projects";
import {
  attentionCount,
  choosableException,
  dayShort,
  emptyMonth,
  exceptionSubject,
  groupExceptions,
  latestReadback,
  type Month,
  type MonthException,
  pendingOf,
  type Severity,
  type Stage,
  shownStages,
  useMonth,
} from "@/lib/month";
import { useView } from "@/lib/nav";

/** 每组先列几条（其余「显示更多」）。 */
export const GROUP_SHOWN = 8;

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
    return total ? co.ledger.entered(e.pushed, total) : "";
  }
  if (stage === "verify") {
    const sets = s.verify?.sets ?? [];
    if (sets.some((set) => set.status === "reading" || set.newer === "reading")) return co.ledger.reading;
    const day = latestReadback(sets);
    return day ? co.ledger.readBack(dayShort(day)) : "";
  }
  const due = s.tax?.deadlines?.pp30?.efiling;
  return due ? co.ledger.due(dayShort(due)) : "";
}

/** 码名（认不出的码用服务端的原因文字，再没有写码本身）。 */
export const codeName = (item: Pick<MonthException, "code" | "reason_label">) => co.ledger.code[item.code] ?? (item.reason_label || item.code);

// ---------------------------------------------------------------- 纯展示（测试直接渲染）
const StageRow: FC<{ stage: Stage; month: Month }> = ({ stage, month }) => {
  const state = month.stages[stage]!.state;
  const word = co.ledger.state[state] ?? state;
  return (
    <div className="cc-panel-row" data-slot="cc-month-stage" data-stage={stage} data-state={state}>
      <span className="cc-month-dot" data-state={state} role="img" aria-label={word} title={word} />
      <span className="min-w-0 flex-1 truncate">{co.ledger.stage[stage]}</span>
      <span className="cc-panel-extra">{stageSummary(stage, month) || word}</span>
    </div>
  );
};

/** 一条异常的一行内容：码名、单据 / 对方、金额（汇总的写件数）。 */
const ItemHead: FC<{ item: MonthException }> = ({ item }) => {
  const extra = formatAmount(item.amount) || (item.count ? String(item.count) : "");
  return (
    <>
      <span className="cc-month-code">{codeName(item)}</span>
      <span className="cc-month-sub">{exceptionSubject(item, branchShort)}</span>
      {extra && <span className="cc-panel-extra tabular-nums">{extra}</span>}
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
  if (choosableException(item)) {
    return <ChooseRow item={pendingOf(item, company)} head={<ItemHead item={item} />} onDone={onDone} onChanged={onChanged} onAsk={() => onAct(item)} />;
  }
  const task = item.action === "task" && !!item.task;
  const label = task ? co.ledger.openTask : co.handToCC;
  return (
    <button type="button" className="cc-panel-row" data-slot="cc-month-item" data-code={item.code} data-severity={item.severity} title={label}
      onClick={() => onAct(item)}>
      <ItemHead item={item} />
      {task ? <ChevronRightIcon aria-hidden /> : <SparklesIcon aria-hidden />}
    </button>
  );
};

const itemKey = (item: MonthException, i: number) =>
  [item.code, item.document ?? item.task ?? item.draft ?? item.account_set_key ?? "", item.issue ?? "", i].join(":");

/** 台账的内容：四段，再是按严重度分组的异常。 */
export const LedgerBody: FC<{ month: Month; company: string } & LedgerHandlers> = ({ month, company, ...handlers }) => {
  const [more, setMore] = useState<Severity[]>([]);
  return (
    <>
      <div className="flex flex-col" data-slot="cc-month-stages">
        {shownStages(month).map((stage) => (
          <StageRow key={stage} stage={stage} month={month} />
        ))}
      </div>
      {groupExceptions(month.exceptions.items).map(([severity, items]) => {
        const all = more.includes(severity);
        return (
          <div key={severity} className="flex flex-col" data-slot="cc-month-group" data-severity={severity}>
            <div className="cc-subheading flex items-center gap-1.5">
              <span>{co.ledger.severity[severity]}</span>
              <span className="tabular-nums">{items.length}</span>
            </div>
            {(all ? items : items.slice(0, GROUP_SHOWN)).map((item, i) => (
              <ExceptionRow key={itemKey(item, i)} item={item} company={company} {...handlers} />
            ))}
            {!all && items.length > GROUP_SHOWN && (
              <button type="button" className="cc-show-more" onClick={() => setMore((before) => [...before, severity])}>
                {co.showMore}
              </button>
            )}
          </div>
        );
      })}
    </>
  );
};

// ---------------------------------------------------------------- 去处
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
    if (item.document) {
      // 重复单号：同号的其他几张一起交过去
      const others = (item.others ?? []).map((document): PendingItem => ({ document, company, reason_code: "", reason_label: "", action: "open" }));
      ask([pendingOf(item, company), ...others]);
      return;
    }
    const filing = { project: company, period: withPeriod ? period : "", scope: "" };
    const month = monthLabel(period);
    if (item.draft) start(filing, co.draftText(item.kind ?? "", month), false);
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
  return (
    <PanelCard id="month" title={co.thisMonth} summary={month ? attentionCount(month.exceptions.items) : undefined} slot="cc-panel-month">
      {error && <p role="alert" className="px-1 text-danger text-sm">{error}</p>}
      {month && <LedgerBody month={month} company={company} {...handlers} />}
    </PanelCard>
  );
};

// ---------------------------------------------------------------- 公司页概览第一节
/** 公司页的月结台账：卡头月份胶囊（最近 12 个月，默认上个月，不列「不指定」）；一直显示，取的时候转圈。 */
export const MonthSection: FC<{ company: string }> = ({ company }) => {
  const [period, setPeriod] = useState(() => lastMonth());
  const { month, error, loading, reload, drop } = useMonth(company, period);
  const handlers = useLedgerActions(company, period, drop, reload);
  return (
    <PanelCard
      id="month"
      title={co.ledger.title}
      summary={month ? attentionCount(month.exceptions.items) : undefined}
      slot="cc-company-month"
      action={<PeriodChip value={period} onChange={setPeriod} allowNone={false} look="cc-chin-btn" />}
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
