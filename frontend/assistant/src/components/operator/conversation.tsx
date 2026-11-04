// 代录任务会话里的几块（评估 docs/work/2026-11-04-cc-code-style.md 第 2.1、6 节；用户 11-04：「代录右侧不要」，原右栏五块并进对话）：
//   资料步骤行   「资料 22 张 · 采购 20 · 费用 2 ›」（同工具步骤的摘要行 .cc-steps-summary），点开是按组的列表；准备中拖到别的组改分组
//                （共用拖动 components/ui/drag.tsx，站点按公司记住）
//   「开始前」框  准备中对话最后一个框（同允许卡的一层框 .cc-steps-card.cc-ask）：第一行「写入 {公司} · {账套} · {电脑} 的 Express」，
//                Express 登录没设置的一行〔设置〕，上次推送结果不明的单（已入账 / 没有入账），停下的单（重试 / 跳过），要人定的按类一次问完
//                （下拉），最下面「开始」：一直能点，还差什么写在它上面
//   单据行        开始以后每张单一行（单号 · 对方、Express 单号或原因、状态）；停下等人的那张行下就地有重试 / 跳过 / 已入账 / 没有入账；
//                整批停下的一行原因〔设置〕/〔重试〕；停了还有没录的〔开始〕
//   报告          做完时一张文件卡片（同回复下的输出文件卡片 .cc-file-card），点了下载 CSV（浏览器里生成）
//   输入框下面那一行  公司、月份（准备中可改）、电脑（几台时可选）、这次写入的账套
// 执行中、出错、停止都用直写那一套（thread.tsx 的 RunLine、.cc-alert、输入框里的 ■），这里不另做。样式只用 globals.css 的 .cc-* 与变量。

import { BookOpenIcon, Building2Icon, CheckIcon, ChevronDownIcon, ChevronRightIcon, CircleAlertIcon, CircleIcon, FileSpreadsheetIcon, MinusIcon, MonitorIcon } from "lucide-react";
import { type FC, useEffect, useMemo, useState } from "react";
import { PeriodChip } from "@/components/period-chip";
import { DragArea, type DragData, DragList, type DragTexts, useDropZone, useSortableItem } from "@/components/ui/drag";
import { DropdownMenu, menuOffset } from "@/components/ui/menu";
import { Marquee } from "@/components/ui/marquee";
import { co, op, t } from "@/i18n";
import { formatAmount } from "@/lib/companies";
import { LOGIN_REASONS, useOperator } from "./controller";
import {
  batchNeedsHuman,
  docRow,
  type DocTone,
  docTitle,
  finishLine,
  groupDocuments,
  groupOf,
  isFinal,
  kindTitle,
  needsYou,
  optionLabel,
  questionGroups,
  reasonText,
  reportCsv,
  RESULT_UNKNOWN,
  stoppedDocs,
  unknownDocs,
} from "./model";
import type { CompanyOption, DocGroup, DocumentOption, OperatorComputer } from "./types";

// ---------------------------------------------------------------- 资料步骤行
/** 摘要那一行的字：资料 N 张 · 采购 n · 费用 m（空组不写）。 */
export function sourcesLine(docs: readonly DocumentOption[]): string {
  return [op.sources(docs.length), ...groupDocuments(docs).map(([group, rows]) => `${op.group[group]} ${rows.length}`)].join(" · ");
}

/** 准备中这家这个月的资料：一行摘要，点开按组列出（能拖到别的组改分组），还有没取的「显示更多」。 */
export const SourcesStep: FC = () => {
  const o = useOperator();
  const [open, setOpen] = useState(false);
  const docs = o.data.docs;
  if (!o.draft || !docs) return null;
  return (
    <div className="cc-steps" data-slot="cc-op-sources">
      <button type="button" className="cc-steps-summary" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Marquee className="min-w-0 truncate">{sourcesLine(docs)}</Marquee>
        <ChevronRightIcon aria-hidden />
      </button>
      {open && docs.length > 0 && (
        <div className="cc-steps-card" data-slot="cc-op-sources-list">
          <DraftGroups docs={docs} />
          {o.data.next !== null && (
            <button type="button" className="cc-show-more" disabled={o.busy} onClick={o.loadMore}>
              {co.showMore}
            </button>
          )}
        </div>
      )}
    </div>
  );
};

/** 拖动的读屏播报（同会话栏卡片那一套，拖进别的组时念组名）。 */
const docTexts = (): DragTexts => ({
  instructions: co.card.instructions,
  pickedUp: co.card.pickedUp,
  movedTo: co.card.movedTo,
  droppedAt: co.card.droppedAt,
  cancelled: co.card.cancelled,
  over: (name, group) => `${name} · ${group}`,
  droppedIn: (name, group) => `${name} · ${group}`,
});

// 第 0 期只在入账的三组之间拖（站点按公司记住）；收付款、不入账随第 1 期
const ENTRY_GROUPS = new Set<string>(["purchase", "expense", "sales"]);

/** 按组列出，拖到别的组改分组（落点是整组，自己那一组不算）。 */
const DraftGroups: FC<{ docs: readonly DocumentOption[] }> = ({ docs }) => {
  const o = useOperator();
  const [dragging, setDragging] = useState(false);
  const groups = groupDocuments(docs, dragging);
  const texts = useMemo(docTexts, []);
  return (
    <DragArea
      texts={texts}
      accepts={(active, target) =>
        !!target.zone && target.list !== active.list && ENTRY_GROUPS.has(String(active.list)) && ENTRY_GROUPS.has(String(target.list))}
      onActive={(active) => setDragging(!!active)}
      onDrop={(active, target) => {
        if (target.list && target.list !== active.list) o.regroup(active.id, target.list as DocGroup);
      }}
    >
      {groups.map(([group, rows]) => (
        <DocGroupList key={group} group={group} rows={rows} />
      ))}
    </DragArea>
  );
};

const DocGroupList: FC<{ group: DocGroup; rows: DocumentOption[] }> = ({ group, rows }) => {
  const zone = useDropZone({ id: `group:${group}`, kind: "doc-group", label: op.group[group]!, list: group }, true);
  return (
    <div ref={zone.ref} className="cc-op-group" data-group={group} data-drop-target={zone["data-drop-target"]} data-slot="cc-op-group">
      <div className="cc-op-group-head" data-group={group}>
        <Marquee className="min-w-0 flex-1 truncate">{op.group[group]}</Marquee>
        <span className="cc-op-count">{rows.length}</span>
      </div>
      <DragList id={group} items={rows.map((r) => r.id)}>
        {rows.map((doc) => (
          <DocItem key={doc.id} doc={doc} group={group} />
        ))}
      </DragList>
    </div>
  );
};

const DocItem: FC<{ doc: DocumentOption; group: DocGroup }> = ({ doc, group }) => {
  const title = docTitle(doc, doc.id);
  const data: DragData = { id: doc.id, kind: "doc", label: title, list: group };
  const sortable = useSortableItem(data);
  return (
    <div {...sortable.node} className="cc-op-doc" data-slot="cc-op-doc">
      <span {...sortable.handle} className="cc-op-doc-handle">
        <Marquee className="min-w-0 flex-1 truncate">{title}</Marquee>
        {doc.total && <span className="cc-op-count">{formatAmount(doc.total)}</span>}
      </span>
    </div>
  );
};

// ---------------------------------------------------------------- 写入目标
/** 电脑名：本机带「本机」（名字本来就是「本机」的不重复）。 */
export const pcName = (pc: Pick<OperatorComputer, "name" | "local">) =>
  pc.local && pc.name !== op.thisComputer ? `${pc.name} · ${op.thisComputer}` : pc.name;

/** 这次写进哪份账套（给人看的名字）：开始以后是任务快照里的那一份，开始前是站点预检给的那一份（同 start 实际用的）。 */
export function useAccountSet(): string {
  const o = useOperator();
  if (o.draft) return o.target ? o.target.account_set_title || o.target.account_set : "";
  return o.snapshot?.account_set || (o.target ? o.target.account_set_title || o.target.account_set : "");
}

/** 「开始前」框第一行：写入 {公司} · {账套} · {电脑} 的 Express（不知道的那一项不写）。 */
export function writeLine(company: string, accountSet: string, computer: string): string {
  return op.writeTo([company, accountSet, computer].filter(Boolean).join(" · "));
}

// ---------------------------------------------------------------- 「开始前」框
type ItemAction = { slot: string; label: string; run: () => void };
type DocItemRow = { key: string; title: string; detail: string; actions: ItemAction[] };

/** 上次推送结果不明的单（准备中）：站点给的那一句 +「已入账」「没有入账」（标了再预检一次）。 */
function useUnknownRows(): DocItemRow[] {
  const o = useOperator();
  if (!o.draft) return [];
  return unknownDocs(o.data.prechecks ?? [], o.draft.skipped).map((row) => ({
    key: row.doc_id,
    title: docTitle(o.data.docs?.find((d) => d.id === row.doc_id) ?? {}, row.doc_id),
    detail: row.message,
    actions: [
      { slot: "cc-op-entered", label: op.doc.saved!, run: () => o.resolve(row.doc_id, true) },
      { slot: "cc-op-not-entered", label: op.notEntered, run: () => o.resolve(row.doc_id, false) },
    ],
  }));
}

/** 预检里停下的单（等识别、挡下的）：原因 + 重试（再预检）/ 跳过。 */
function useStoppedRows(): DocItemRow[] {
  const o = useOperator();
  if (!o.draft) return [];
  return stoppedDocs(o.data.prechecks ?? [], o.draft.skipped).map((row) => ({
    key: row.doc_id,
    title: docTitle(o.data.docs?.find((d) => d.id === row.doc_id) ?? {}, row.doc_id),
    detail: row.reason,
    actions: [
      { slot: "cc-op-retry", label: op.retry, run: () => o.retry(row.doc_id) },
      { slot: "cc-op-skip", label: op.skip, run: () => o.skip(row.doc_id) },
    ],
  }));
}

const Actions: FC<{ actions: readonly ItemAction[] }> = ({ actions }) => {
  const o = useOperator();
  if (!actions.length) return null;
  return (
    <div className="cc-card-actions">
      {actions.map((action) => (
        <button key={action.slot} type="button" className="cc-card-btn" data-slot={action.slot} disabled={o.busy} onClick={action.run}>
          {action.label}
        </button>
      ))}
    </div>
  );
};

const HandleRow: FC<{ row: DocItemRow; slot: string }> = ({ row, slot }) => (
  <div className="cc-op-handle" data-slot={slot}>
    <Marquee className="block min-w-0 truncate text-ink">{row.title}</Marquee>
    {row.detail && <span className="text-ink-3 text-caption">{row.detail}</span>}
    <Actions actions={row.actions} />
  </div>
);

/** 准备中对话最后一个框（文件头）。资料、预检还没取到时不出。 */
export const BeforeBox: FC = () => {
  const o = useOperator();
  const unknown = useUnknownRows();
  const stopped = useStoppedRows();
  const accountSet = useAccountSet();
  if (!o.draft || !o.data.docs || !o.data.prechecks) return null;
  const d = o.draft;
  const pc = o.computer;
  const groupOfDoc = (id: string) => groupOf(o.data.docs?.find((doc) => doc.id === id) ?? {});
  return (
    <section className="cc-steps-card cc-ask" data-phase="ask" data-slot="cc-op-before" aria-label={op.beforeStart}>
      <div className="cc-ask-q" data-slot="cc-op-target">{writeLine(d.company_title || d.company, accountSet, pc ? pcName(pc) : "")}</div>
      <div className="cc-op-before-body">
        {pc && pc.login === false && (
          <div className="cc-op-line" data-slot="cc-op-login">
            <Marquee className="min-w-0 flex-1 truncate">{op.reason.login_missing}</Marquee>
            {pc.local && (
              <button type="button" className="cc-card-btn" data-slot="cc-op-authorize" disabled={o.busy || !pc.online} onClick={o.authorize}>
                {op.setUp}
              </button>
            )}
          </div>
        )}
        {unknown.map((row) => (
          <HandleRow key={row.key} row={row} slot="cc-op-unknown" />
        ))}
        {stopped.map((row) => (
          <HandleRow key={row.key} row={row} slot="cc-op-handle" />
        ))}
        {questionGroups(o.questions).map(([kind, list]) => (
          <div key={kind} className="cc-op-ask-group" data-kind={kind} data-slot="cc-op-ask-group">
            <div className="cc-op-ask-kind">{kindTitle(kind, list.flatMap((q) => q.doc_ids.map(groupOfDoc)))}</div>
            {list.map((q) => (
              <label key={q.key} className="cc-op-ask" data-slot="cc-op-ask">
                <Marquee className="min-w-0 flex-1 truncate">{q.label}</Marquee>
                <select
                  className="cc-input cc-op-ask-select"
                  aria-label={`${kindTitle(kind)} · ${q.label}`}
                  value={d.answers[q.key] ?? ""}
                  onChange={(e) => o.answer(q.key, e.target.value)}
                >
                  <option value="" disabled>—</option>
                  {q.options.map((option) => (
                    <option key={option.code} value={option.code}>
                      {optionLabel(kind, option)}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        ))}
      </div>
      <div className="cc-ask-foot">
        {o.notice && (
          <div role="alert" className="cc-alert" data-size="sm" data-slot="cc-op-start-notice">
            {o.notice}
          </div>
        )}
        <div className="cc-card-actions">
          <button type="button" className="cc-card-btn" data-primary="" data-slot="cc-op-start" disabled={o.busy} onClick={o.start}>
            {op.start}
          </button>
        </div>
      </div>
    </section>
  );
};

// ---------------------------------------------------------------- 开始以后：单据行
const ROW_ICON: Record<DocTone, FC<{ className?: string }>> = {
  done: CheckIcon,
  running: CircleIcon,
  waiting: CircleIcon,
  attention: CircleAlertIcon,
  skipped: MinusIcon,
};

/** 停下等人的那一张能做的：推送结果不明的标已入账 / 没有入账；其余重试（交单时就没收的不能）/ 跳过（这台电脑上的、或没收的）。 */
function docActions(o: ReturnType<typeof useOperator>, doc: NonNullable<ReturnType<typeof useOperator>["snapshot"]>["docs"][number]): ItemAction[] {
  const s = o.snapshot!;
  if (!needsYou(doc)) return [];
  if (doc.reason === RESULT_UNKNOWN && s.site) {
    return [
      { slot: "cc-op-entered", label: op.doc.saved!, run: () => o.resolve(doc.doc_id, true) },
      { slot: "cc-op-not-entered", label: op.notEntered, run: () => o.resolve(doc.doc_id, false) },
    ];
  }
  const actions: ItemAction[] = [];
  if (!doc.refused) actions.push({ slot: "cc-op-retry", label: op.retry, run: () => o.retry(doc.doc_id) });
  if (!s.remote || doc.refused) actions.push({ slot: "cc-op-skip", label: op.skip, run: () => o.skip(doc.doc_id) });
  return actions;
}

/** 开始以后：一行概况、每张单一行（要人处理的行下就地有按钮）、整批停下的那一行、完成行、续做的「开始」、报告卡片。 */
export const TaskDocs: FC<{ meta: string }> = ({ meta }) => {
  const o = useOperator();
  const s = o.snapshot;
  if (!s) return null;
  const final = isFinal(s.status);
  const batchStop = batchNeedsHuman(s) && !s.remote;
  const login = LOGIN_REASONS.has(s.reason);
  const resumable = !s.remote && (s.status === "ready" || s.status === "stopped") && s.docs.some((d) => d.status === "pending");
  return (
    <>
      {meta && <div className="cc-op-meta">{meta}</div>}
      {s.docs.map((doc) => {
        const row = docRow(doc);
        const Icon = ROW_ICON[row.tone];
        const actions = docActions(o, doc);
        const detail = doc.reason === RESULT_UNKNOWN && s.site ? [co.ledger.code.result_unknown, ...doc.voided.map((no) => op.voidedNo(no))].join(" · ") : row.detail;
        return (
          <div key={doc.doc_id} className="cc-op-row" data-tone={row.tone} data-slot="cc-op-row">
            <Icon className="cc-op-row-icon" aria-hidden />
            <span className="flex min-w-0 flex-col">
              <Marquee className="truncate text-ink">{docTitle(doc.summary ?? {}, doc.doc_id)}</Marquee>
              {detail && <Marquee className="truncate text-ink-3 text-caption">{detail}</Marquee>}
            </span>
            <span className="cc-op-row-state">{row.state}</span>
            {actions.length > 0 && (
              <div className="cc-op-row-actions">
                <Actions actions={actions} />
              </div>
            )}
          </div>
        );
      })}
      {batchStop && (
        <div className="cc-op-handle" data-slot="cc-op-batch-stop">
          <span className="text-ink">{reasonText(s.reason)}</span>
          <Actions actions={[{ slot: login ? "cc-op-authorize" : "cc-op-retry", label: login ? op.setUp : op.retry, run: login ? o.authorize : o.resume }]} />
        </div>
      )}
      {final && <div className="cc-op-finish" data-slot="cc-op-finish">{finishLine(s)}</div>}
      {resumable && (
        <div className="cc-card-actions" data-slot="cc-op-start-row">
          <button type="button" className="cc-card-btn" data-primary="" data-slot="cc-op-start" disabled={o.busy} onClick={o.start}>
            {op.start}
          </button>
        </div>
      )}
      {final && <ReportCard />}
    </>
  );
};

// ---------------------------------------------------------------- 报告
/** 报告文件名：按任务标题（公司 月份）。 */
export const reportName = (company: string, period: string) => `${[company, period].filter(Boolean).join(" ").replace(/[\\/:*?"<>|]+/g, " ").trim() || "report"}.csv`;

/** 下载：每张单一行的 CSV（Excel 直接打开）。 */
export function downloadReport(name: string, csv: string) {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** 做完时对话里一张文件卡片（同回复下的输出文件卡片），点了下载。 */
const ReportCard: FC = () => {
  const o = useOperator();
  const s = o.snapshot;
  if (!s) return null;
  const name = reportName(s.company_title || s.company, s.period ?? "");
  return (
    <button
      type="button"
      className="cc-file-card cc-card-hover items-center gap-(--cc-space-12) px-(--cc-space-12) py-(--cc-file-card-py) text-start"
      data-slot="cc-op-export"
      title={t("download")}
      onClick={() => downloadReport(name, reportCsv(s))}
    >
      <span className="cc-file-card-icon">
        <FileSpreadsheetIcon />
      </span>
      <span className="min-w-0">
        <Marquee className="block truncate font-medium text-ink text-body">{name}</Marquee>
        <span className="block text-ink-3 text-caption">CSV</span>
      </span>
    </button>
  );
};

// ---------------------------------------------------------------- 输入框下面那一行
/** 选公司（同对话页下巴的公司条）：输入就搜，服务端分页取前 20 家。 */
export const CompanyChip: FC<{ value: CompanyOption | null; onChange: (company: CompanyOption) => void }> = ({ value, onChange }) => {
  const o = useOperator();
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<CompanyOption[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const timer = window.setTimeout(
      () => void o.source.companies(search.trim(), 0, 20).then((found) => alive && setRows(found), () => alive && setRows([])),
      250,
    );
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [o.source, search, open]);
  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button type="button" className="cc-chin-btn" data-slot="cc-chin-company" data-chosen={value ? "" : undefined}>
          <Building2Icon />
          <Marquee className="min-w-0 truncate">{value?.title ?? co.company}</Marquee>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="bottom" align="start" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-lg) max-w-(--cc-menu-w-max)">
          <div className="px-(--cc-space-4) pb-(--cc-space-4)">
            <input
              className="cc-input w-full"
              value={search}
              aria-label={t("search")}
              placeholder={t("search")}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key !== "Escape" && e.key !== "ArrowDown" && e.stopPropagation()}
            />
          </div>
          {(rows ?? []).map((row) => (
            <DropdownMenu.Item key={row.id} className="cc-menu-item" onSelect={() => onChange(row)}>
              <Marquee className="min-w-0 flex-1 truncate">{row.title}</Marquee>
              {row.id === value?.id && <CheckIcon />}
            </DropdownMenu.Item>
          ))}
          {rows && !rows.length && <div className="cc-menu-label">{t("noResults")}</div>}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

/** 执行的电脑：名字 + 在线点；准备中有几台时点了换。 */
const ComputerChip: FC = () => {
  const o = useOperator();
  const [batchPcs, setBatchPcs] = useState<OperatorComputer[] | null>(null);
  const company = o.draft ? "" : (o.snapshot?.company ?? "");
  useEffect(() => {
    setBatchPcs(null);
    if (!company) return;
    let alive = true;
    void o.source.computers(company).then((found) => alive && setBatchPcs(found), () => alive && setBatchPcs([]));
    return () => {
      alive = false;
    };
  }, [o.source, company]);
  const computers = o.draft ? (o.data.computers ?? []) : (batchPcs ?? []);
  const pc = o.draft ? o.computer : (computers.find((c) => c.id === (o.snapshot?.computer ?? "")) ?? computers[0]);
  if (!pc) return null;
  const face = (
    <>
      <MonitorIcon />
      <Marquee className="min-w-0 truncate">{pcName(pc)}</Marquee>
      <span className="cc-status-line" data-status={pc.online ? "online" : "offline"} role="img" aria-label={pc.online ? op.online : op.offline}>
        <span className="cc-status-dot" />
      </span>
    </>
  );
  if (!o.draft || computers.length < 2) {
    return (
      <span className="cc-chin-btn" data-chosen="" data-slot="cc-op-computer">
        {face}
      </span>
    );
  }
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className="cc-chin-btn" data-chosen="" data-slot="cc-op-computer">
          {face}
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="top" align="start" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-lg) max-w-(--cc-menu-w-max)">
          {computers.map((c) => (
            <DropdownMenu.Item key={c.id} className="cc-menu-item" disabled={!c.online} onSelect={() => o.chooseComputer(c.id)}>
              <Marquee className="min-w-0 flex-1 truncate">{pcName(c)}</Marquee>
              {c.id === pc.id && <CheckIcon />}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

/** 这次写入的账套（只显示）。 */
const AccountSetChip: FC = () => {
  const name = useAccountSet();
  if (!name) return null;
  return (
    <span className="cc-chin-btn" data-chosen="" data-slot="cc-op-account-set" title={name}>
      <BookOpenIcon />
      <Marquee className="min-w-0 truncate">{name}</Marquee>
    </span>
  );
};

/** 任务会话的下巴：准备中能改公司、月份（交单后就不能改了）；电脑、账套一直在。 */
export const TaskChin: FC = () => {
  const o = useOperator();
  const d = o.draft;
  return (
    <>
      {d && (
        <>
          <CompanyChip value={{ id: d.company, title: d.company_title || d.company }} onChange={(c) => c.id !== d.company && o.retarget({ company: c.id, company_title: c.title })} />
          <PeriodChip look="cc-chin-btn" side="top" value={d.period} onChange={(p) => p && p !== d.period && o.retarget({ period: p })} allowNone={false} />
        </>
      )}
      <ComputerChip />
      <AccountSetChip />
    </>
  );
};
