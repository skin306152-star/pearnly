// 引导卡的复核与结果（引导式分支 3 前端 F3 + N2 / N1b / N5；A2 线 G2）。两种来源同一套外观：
//   表单（收款这类，review(ctx, draft)）：lines 一行一项（标签左、值右），tables 一组一张（名字 + 金额），合计；带 step 的行可点「编辑」回那一步；
//   单据（采购 / 销售，documents 源）：每张一块票面（单号 · 对方 · 合计 + 标记），展开是表头八格与明细，能改的格就地改（失焦提交），
//        算出来的格只读（行上 editable 定，没带按 review.EDITABLE）；写不进账本的格（blocking）标红、这一张这次不写；
//   确定：consent=confirm 是「写入」（输入框上方那一枚已上线的词），consent=allow（N5，删除 / 结束类）是「拒绝 / 允许」，没有始终允许；
//   结果：表单一行（状态 · 单号 / 原因），带文件的可下载（N2），带追问的就地答（N1b）；单据每张一行（已入账带单号 / 进行中 / 已跳过 / 未完成）。
// 卡上的字：标签与说明由服务端翻好；状态与格名沿用已上线的词。只用标准组件与令牌。

import { ChevronDownIcon, ChevronRightIcon, DownloadIcon } from "lucide-react";
import { type FC, type ReactNode, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { ask, co, guide, op, run as runWords, t } from "@/i18n";
import { businessName } from "@/lib/business";
import { fileUrl } from "@/lib/managed-agents/api";
import {
  canEdit, cancelGuide, cellValue, confirmGuide, type DocMark, type DocRow, editGuide, followGuide, type GuideDocResult, type GuideDocReview,
  type GuideResult, type GuideReview as FormReviewData, type GuideReviewTable, type GuideTableRow, type GuideUploads, type GuideView, HEAD_FIELDS, isDocResult, isDocReview,
  LINE_FIELDS, refusedText, reopenGuide, subsAnswered,
} from "@/lib/guide-run";
import { ChangeList } from "./approval-bar";
import { CardFrame, type FrameButton } from "./card-frame";
import { operatorTitle } from "./guide-operator";
import { SubQuestionsField } from "./guide-inputs";

/** 卡外框上给流程脚本认的属性（同运行卡）。 */
export const runAttrs = (view: GuideView) => ({ "data-run": view.run, "data-status": view.status, "data-business": view.business });

type Act = (call: () => Promise<GuideView>) => void;
type Props = { view: GuideView; sessionId: string | null; busy: boolean; failure: string; act: Act };

/** 脚上的按钮（卡片骨架 card-frame.tsx）：次按钮照线上允许卡叫「拒绝」（灰底，在主按钮左边；= 取消这次引导），主按钮确定是「写入」、
 *  允许卡式确定（N5）是「允许」（主会话 11-16 定）。 */
function decideButtons({ view, act }: Props, ready: boolean, seen?: string): FrameButton[] {
  const confirm = () => act(() => confirmGuide(view.run, view.version, seen));
  const cancel = () => act(() => cancelGuide(view.run));
  const allow = view.consent === "allow";
  return [
    { slot: "cc-guide-cancel", label: t("deny"), onClick: cancel },
    { slot: "cc-guide-confirm", label: allow ? t("allow") : t("recordNow"), primary: true, disabled: !ready, onClick: confirm },
  ];
}

/** 复核里能回到某一步的一行：整行是按钮（同「已定 n 项」那一行的写法：ghost 小按钮通栏），右边一个箭头。 */
const StepRow: FC<{ label: string; value?: string; step?: string; busy: boolean; onBack: (step: string) => void; strong?: boolean }> = ({
  label, value, step, busy, onBack, strong,
}) => {
  const body = (
    <>
      <span className={strong ? "min-w-0 flex-1 text-start" : "min-w-0 flex-1 text-start text-(--cc-ink-3)"}>{label}</span>
      {value ? <span className="text-end">{value}</span> : null}
    </>
  );
  if (!step) {
    return <div className="cc-op-line" data-slot="cc-guide-line">{body}</div>;
  }
  return (
    <Button type="button" variant="ghost" size="sm" className="w-full justify-start" data-slot="cc-guide-back" data-step={step} disabled={busy}
      aria-label={`${t("edit")} ${label}`} onClick={() => onBack(step)}>
      {body}
      <ChevronRightIcon />
    </Button>
  );
};

// ── 表单复核里逐行可改的表（P3 预扣税待确认行，11-16）：每行一块（名称 · 说明 · 金额 + 问题句），下面按 columns 一格一行；
//    editable 里的格就地改（选项用原生下拉、其余一格输入，失焦 / 回车 / 选中即交 guide_edit），其余只读；被拒时在那一格下显示译文。──

const TableCell: FC<{
  row: GuideTableRow; column: { key: string; label: string }; disabled: boolean; refused: string;
  onCommit: (field: string, value: string) => void;
}> = ({ row, column, disabled, refused, onCommit }) => {
  const shown = row.cells?.[column.key] ?? "";
  const edit = row.editable?.[column.key];
  const [draft, setDraft] = useState<string | null>(null);
  const commit = (value: string) => {
    if (value !== shown) onCommit(column.key, value);
    setDraft(null);
  };
  let body;
  if (!edit) {
    body = <span data-slot="cc-guide-cell" data-field={column.key} data-readonly>{shown}</span>;
  } else if (edit.input === "choice") {
    body = (
      <NativeSelect data-slot="cc-guide-cell" data-field={column.key} aria-label={column.label} value={shown} disabled={disabled}
        onChange={(event) => commit(event.currentTarget.value)}>
        {(edit.options ?? []).map((option) => <NativeSelectOption key={option.code} value={option.code}>{option.name}</NativeSelectOption>)}
      </NativeSelect>
    );
  } else {
    body = (
      <Input data-slot="cc-guide-cell" data-field={column.key} aria-label={column.label} value={draft ?? shown} disabled={disabled}
        type={edit.input === "date" ? "date" : "text"} inputMode={edit.input === "number" ? "decimal" : undefined} aria-invalid={!!refused || undefined}
        className={edit.input === "number" ? "cc-guide-amount w-(--cc-guide-amount-w) shrink-0" : undefined}
        onChange={(event) => setDraft(event.currentTarget.value)} onBlur={() => draft !== null && commit(draft)}
        onKeyDown={(event) => { if (event.key === "Enter" && draft !== null) { event.preventDefault(); commit(draft); } }} />
    );
  }
  return (
    <Item size="sm" data-field={column.key}>
      <ItemContent>
        <ItemDescription>{column.label}</ItemDescription>
        {refused ? <ItemDescription data-slot="cc-guide-cell-refused">{refused}</ItemDescription> : null}
      </ItemContent>
      <ItemActions>{body}</ItemActions>
    </Item>
  );
};

const EditableTable: FC<Props & { table: GuideReviewTable }> = ({ view, busy, act, table }) => {
  const [last, setLast] = useState<{ code: string; field: string } | null>(null);
  const columns = table.columns ?? [];
  const edit = (code: string, field: string, value: string) => {
    setLast({ code, field });
    act(() => editGuide(view.run, code, field, value, view.version));
  };
  return (
    <ItemGroup data-key={table.key} data-slot="cc-guide-table">
      <Item size="sm"><ItemContent><ItemTitle>{table.label}</ItemTitle></ItemContent></Item>
      {table.rows.map((row) => (
        <Collapsible key={row.code} defaultOpen={(row.issues ?? []).length > 0} data-slot="cc-guide-table-row" data-code={row.code}>
          <CollapsibleTrigger className="cc-guide-doc-head" data-slot="cc-guide-doc-head">
              <ChevronRightIcon />
              <span className="flex min-w-0 flex-1 flex-col items-start gap-(--cc-space-4)">
                <span className="wrap-break-word text-start">{[row.name || row.code, row.note].filter(Boolean).join(" · ")}</span>
                <span className="flex w-full flex-wrap items-center gap-(--cc-space-6)">
                  {(row.issues ?? []).length > 0 && <Badge variant="destructive">{row.issues!.length}</Badge>}
                  {row.amount ? <span className="ms-auto tabular-nums">{row.amount}</span> : null}
                </span>
              </span>
          </CollapsibleTrigger>
          <CollapsibleContent>
            {(row.issues ?? []).map((issue) => <p key={issue} className="cc-askq-desc" data-slot="cc-guide-issue">{issue}</p>)}
            {columns.map((column) => (
              <TableCell key={column.key} row={row} column={column} disabled={busy} onCommit={(field, value) => edit(row.code, field, value)}
                refused={last?.code === row.code && last.field === column.key && view.outcome === "refused" ? refusedText(view, column.key) : ""} />
            ))}
          </CollapsibleContent>
        </Collapsible>
      ))}
    </ItemGroup>
  );
};

// ── 表单复核 ──────────────────────────────────────────────────────────────────────────────────────────

const FormReview: FC<Props & { review: FormReviewData; head?: ReactNode }> = (props) => {
  const { view, busy, act, review, head } = props;
  const back = (step: string) => act(() => reopenGuide(view.run, step, view.version));
  return (
    <CardFrame title={view.consent_title || businessName(view.business)} note={view.consent_note} slot="cc-guide-run" attrs={runAttrs(view)}
      buttons={decideButtons(props, review.ready)}
      failure={props.failure} busy={busy}>
      {head}
      {view.consent === "allow" ? (
        // 允许式确定（删除 / 结束类）：照线上允许卡——标题栏 + 带框的内容块（ChangeList）+ 拒绝 / 允许
        <ChangeList preview={[...(review.lines ?? []).map((line) => ({ field: line.label, before: "", after: line.value })),
          ...(review.tables ?? []).flatMap((table) => table.rows.map((row) => ({ field: row.name || row.code, before: "", after: row.amount ?? "" })))]} />
      ) : null}
      <div data-slot="cc-guide-review" data-source="form" className="cc-op-before-body" hidden={view.consent === "allow"}>
        {(review.lines ?? []).map((line) => (
          <StepRow key={line.key} label={line.label} value={line.value} step={line.step} busy={busy} onBack={back} />
        ))}
        {(review.tables ?? []).filter((table) => table.columns?.length).map((table) => <EditableTable key={table.key} {...props} table={table} />)}
        {(review.tables ?? []).filter((table) => !table.columns?.length).map((table) => (
          <div key={table.key} data-key={table.key} className="flex min-w-0 flex-col">
            <StepRow label={table.label} step={table.step} busy={busy} onBack={back} strong />
            {table.rows.map((row) => (
              <div key={row.code} data-code={row.code} className="cc-op-line">
                <span className="min-w-0 flex-1">{[row.name || row.code, row.note].filter(Boolean).join(" · ")}</span>
                <span className="tabular-nums">{row.amount}</span>
              </div>
            ))}
          </div>
        ))}
        {review.total ? (
          <div className="cc-op-line" data-slot="cc-guide-total">
            <span className="min-w-0 flex-1 font-medium">{guide.total}</span>
            <span className="tabular-nums font-medium">{review.total}</span>
          </div>
        ) : null}
        {review.problem_text ? <Alert variant="destructive" data-slot="cc-guide-problem"><AlertDescription>{review.problem_text}</AlertDescription></Alert> : null}
      </div>
    </CardFrame>
  );
};

// ── 单据复核 ──────────────────────────────────────────────────────────────────────────────────────────

/** 标记码 → 行上最短的词（沿用已上线的词）。 */
const MARK_WORD: Record<DocMark, () => string> = {
  number: () => co.proposal.docNo, duplicate: () => co.ledger.code.duplicate_number ?? co.proposal.docNo, tax_id: () => guide.taxId,
  amount: () => guide.amount, date: () => co.proposal.date,
};
const HEAD_LABEL: Record<(typeof HEAD_FIELDS)[number], () => string> = {
  doc_no: () => co.proposal.docNo, party_name: () => co.proposal.party, party_tax: () => guide.taxId, party_branch: () => co.branchShort,
  date: () => co.proposal.date, subtotal: () => co.proposal.beforeVat, vat: () => co.proposal.vat, total: () => co.proposal.total,
};
const LINE_LABEL: Record<(typeof LINE_FIELDS)[number], () => string> = {
  description: () => guide.description, qty: () => guide.qty, unit_price: () => guide.unitPrice, amount: () => guide.amount,
};
const NUMERIC = new Set(["subtotal", "vat", "total", "qty", "unit_price", "amount"]);

/** 一格：能改的是输入框（失焦或回车时值变了才交），不能改的是只读字；有标记的标红，写不进账本的同样标红。 */
const Cell: FC<{ row: DocRow; field: string; label: string; disabled: boolean; onCommit: (field: string, value: string) => void; fill?: boolean }> = ({
  row, field, label, disabled, onCommit, fill,
}) => {
  const shown = cellValue(row.face, field);
  const [draft, setDraft] = useState<string | null>(null);
  const column = field.split(".").pop() ?? field;
  const wrong = !!row.cells[field] || row.blocking.includes(field);
  const commit = () => {
    if (draft !== null && draft !== shown) onCommit(field, draft);
    setDraft(null);
  };
  if (!canEdit(row, field)) {
    return (
      <span data-slot="cc-guide-cell" data-field={field} data-readonly aria-invalid={wrong || undefined}
        className={NUMERIC.has(column) ? (fill ? "tabular-nums text-end" : "tabular-nums") : undefined}>
        {shown}
      </span>
    );
  }
  return (
    <Input data-slot="cc-guide-cell" data-field={field} aria-label={label} value={draft ?? shown} disabled={disabled} aria-invalid={wrong || undefined}
      inputMode={NUMERIC.has(column) ? "decimal" : undefined}
      className={fill ? "cc-guide-amount" : NUMERIC.has(column) ? "cc-guide-amount w-(--cc-guide-amount-w) shrink-0" : undefined}
      onChange={(event) => setDraft(event.currentTarget.value)} onBlur={commit}
      onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commit(); } }} />
  );
};

const DocBlock: FC<{ row: DocRow; open: boolean; disabled: boolean; onCommit: (docId: string, field: string, value: string) => void }> = ({
  row, open, disabled, onCommit,
}) => {
  const [shown, setShown] = useState(open || row.blocking.length > 0);
  const face = row.face;
  const commit = (field: string, value: string) => onCommit(row.doc_id, field, value);
  return (
    <Collapsible open={shown} onOpenChange={setShown} data-slot="cc-guide-doc" data-doc={row.doc_id} data-blocking={row.blocking.length > 0 || undefined}>
      {/* 行头两行（验收 C5：手机泰 / 英 / 日下对方名被挤没、金额越出卡外）：第一行单号 · 对方完整换行，第二行标记与合计 */}
      <CollapsibleTrigger className="cc-guide-doc-head" data-slot="cc-guide-doc-head">
          {shown ? <ChevronDownIcon /> : <ChevronRightIcon />}
          <span className="flex min-w-0 flex-1 flex-col items-start gap-(--cc-space-4)">
            <span className="wrap-break-word text-start">{[face.doc_no, face.party_name].filter(Boolean).join(" · ")}</span>
            <span className="flex w-full flex-wrap items-center gap-(--cc-space-6)">
              {row.marks.map((mark) => <Badge key={mark} variant="destructive" data-mark={mark}>{MARK_WORD[mark]?.() ?? mark}</Badge>)}
              <span className="ms-auto tabular-nums">{face.total}</span>
            </span>
          </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ItemGroup>
          {HEAD_FIELDS.map((field) => (
            <Item key={field} size="sm" data-field={field}>
              <ItemContent><ItemDescription>{HEAD_LABEL[field]()}</ItemDescription></ItemContent>
              <ItemActions><Cell row={row} field={field} label={HEAD_LABEL[field]()} disabled={disabled} onCommit={commit} /></ItemActions>
            </Item>
          ))}
          {/* 明细一行一块：说明占整行，下面数量 / 单价 / 金额三格等宽一排，每格上面写自己的列名（验收 D5：列名与格子不同列） */}
          {face.lines.map((_line, index) => (
            <div key={index} data-line={index} className="flex min-w-0 flex-col gap-(--cc-space-6)" data-slot="cc-guide-line">
              <Cell row={row} field={`lines.${index}.description`} label={LINE_LABEL.description()} disabled={disabled} onCommit={commit} />
              <div className="grid grid-cols-3 gap-(--cc-space-8)">
                {(["qty", "unit_price", "amount"] as const).map((column) => (
                  <label key={column} className="flex min-w-0 flex-col gap-(--cc-space-4)">
                    <span className="cc-askq-desc">{LINE_LABEL[column]()}</span>
                    <Cell row={row} field={`lines.${index}.${column}`} label={LINE_LABEL[column]()} disabled={disabled} onCommit={commit} fill />
                  </label>
                ))}
              </div>
            </div>
          ))}
        </ItemGroup>
      </CollapsibleContent>
    </Collapsible>
  );
};

const DocReview: FC<Props & { review: GuideDocReview; head?: ReactNode }> = (props) => {
  const { view, busy, act, review, head } = props;
  const edit = (docId: string, field: string, value: string) => act(() => editGuide(view.run, docId, field, value, view.version));
  return (
    <CardFrame title={view.consent_title || operatorTitle(view) || businessName(view.business)} slot="cc-guide-run" attrs={runAttrs(view)}
      buttons={decideButtons(props, review.ready, review.seen)} failure={refusedText(view) || props.failure} busy={busy}>
      {head}
      <div data-slot="cc-guide-review" data-source="documents" className="cc-op-before-body">
        {review.rows.map((row) => (
          <DocBlock key={row.doc_id} row={row} open={review.rows.length === 1} disabled={busy} onCommit={edit} />
        ))}
        {review.held.length > 0 && (
          <div className="cc-op-line" data-slot="cc-guide-held">
            <span className="min-w-0 flex-1">{op.doc.skipped}</span>
            <span className="tabular-nums">{review.held.length}</span>
          </div>
        )}
      </div>
    </CardFrame>
  );
};

export const GuideReview: FC<Props & { head?: ReactNode }> = (props) => {
  const review = props.view.review!;
  return isDocReview(review) ? <DocReview {...props} review={review} /> : <FormReview {...props} review={review} />;
};

// ── 识别中（documents ②）─────────────────────────────────────────────────────────────────────────────

/** 交了文件、还在识别：一行真数（已识别 / 共几份），识别不了的那份写原因。 */
export const GuideUploadsLine: FC<{ uploads: GuideUploads }> = ({ uploads }) => {
  const total = uploads.files.length;
  const done = uploads.files.filter((file) => file.state !== "reading").length;
  const failed = uploads.files.filter((file) => file.state === "failed");
  return (
    <div data-slot="cc-guide-uploads" className="cc-guide-body flex min-w-0 flex-col gap-(--cc-space-4)">
      <div className="flex items-center gap-(--cc-space-8)">
        {uploads.wait ? <Spinner /> : null}
        <span>{uploads.wait ? runWords.reading : runWords.read(done - failed.length)}</span>
        <span className="tabular-nums">{done}/{total}</span>
      </div>
      {failed.map((file) => (
        <p key={file.batch} className="cc-askq-desc" data-batch={file.batch}>{file.reason_text || op.doc.notDone}</p>
      ))}
    </div>
  );
};

// ── 结果 ──────────────────────────────────────────────────────────────────────────────────────────────

const STATUS_WORD: Record<string, () => string> = {
  entered: () => op.doc.saved, queued: () => op.doc.entering, confirmed: () => op.doc.entering, held: () => op.doc.skipped,
  failed: () => op.doc.notDone, withdrawn: () => op.doc.skipped,
};

const DocResultRows: FC<{ result: GuideDocResult }> = ({ result }) => (
  <div data-slot="cc-guide-result" data-result={result.status} className="cc-op-before-body">
    {result.rows.map((row) => (
      <div key={row.doc_id} className="cc-op-line" data-doc={row.doc_id} data-status={row.status}>
        <span className="min-w-0 flex-1 wrap-break-word">
          {row.doc_no || row.doc_id}
          {row.reason_text ? <span className="block text-(--cc-ink-3)">{row.reason_text}</span> : null}
        </span>
        {row.status === "queued" ? <Spinner /> : null}
        <span>{STATUS_WORD[row.status]?.() ?? ""}</span>
        {row.docnum ? <span className="tabular-nums">{row.docnum}</span> : null}
      </div>
    ))}
  </div>
);

/** 结果卡（同一骨架）：表单一行状态（· 单号 / 合计 · 原因）、文件下载（N2）、追问（N1b，脚上「提交」交 guide_follow）；单据每张一行。 */
export const GuideResultCard: FC<Props & { head?: ReactNode }> = (props) => {
  const { view, sessionId, busy, act, head } = props;
  const result = view.result!;
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const title = operatorTitle(view) ?? businessName(view.business);
  if (isDocResult(result)) {
    return (
      <CardFrame title={title} slot="cc-guide-run" attrs={runAttrs(view)} phase="answered" failure={props.failure}>
        {head}
        <DocResultRows result={result} />
      </CardFrame>
    );
  }
  const form = result as GuideResult;
  const subs = form.questions ?? [];
  const buttons: FrameButton[] = subs.length
    ? [{ slot: "cc-guide-follow-submit", label: ask.submit, primary: true, disabled: !subsAnswered(subs, answers),
      onClick: () => act(() => followGuide(view.run, answers, view.version)) }]
    : [];
  return (
    <CardFrame title={title} slot="cc-guide-run" attrs={runAttrs(view)} phase={subs.length ? "ask" : "answered"} buttons={buttons}
      failure={props.failure} busy={busy}>
      {head}
      <div data-slot="cc-guide-result" data-result={form.status} className="cc-op-before-body">
        <div className="cc-op-line">
          <span className="min-w-0 flex-1 wrap-break-word">
            {STATUS_WORD[form.status]?.() ?? ""}
            {form.reason_text ? <span className="block text-(--cc-ink-3)">{form.reason_text}</span> : null}
          </span>
          {form.wait ? <Spinner /> : null}
          <span className="tabular-nums">{[form.docnum, form.total].filter(Boolean).join(" · ")}</span>
        </div>
        {sessionId && (form.files ?? []).map((file) => (
          <div key={file.file} className="cc-op-line" data-slot="cc-guide-result-file" data-file-name={file.name}>
            <span className="min-w-0 flex-1 wrap-break-word">{file.name}</span>
            <Button asChild variant="secondary" size="xs" aria-label={`${t("download")} ${file.name}`}>
              <a href={fileUrl(sessionId, file.file)} download={file.name}><DownloadIcon />{t("download")}</a>
            </Button>
          </div>
        ))}
      </div>
      {subs.length > 0 && (
        <div data-slot="cc-guide-follow"><SubQuestionsField questions={subs} value={answers} disabled={busy} onChange={setAnswers} /></div>
      )}
    </CardFrame>
  );
};
