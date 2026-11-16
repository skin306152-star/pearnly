// 引导卡的复核与结果（引导式分支 3 前端 F3 + N2 / N1b / N5；A2 线 G2）。两种来源同一套外观：
//   表单（收款这类，review(ctx, draft)）：lines 一行一项（标签左、值右），tables 一组一张（名字 + 金额），合计；带 step 的行可点「编辑」回那一步；
//   单据（采购 / 销售，documents 源）：每张一块票面（单号 · 对方 · 合计 + 标记），展开是表头八格与明细，能改的格就地改（失焦提交），
//        算出来的格只读（行上 editable 定，没带按 review.EDITABLE）；写不进账本的格（blocking）标红、这一张这次不写；
//   确定：consent=confirm 是「写入」（输入框上方那一枚已上线的词），consent=allow（N5，删除 / 结束类）是「拒绝 / 允许」，没有始终允许；
//   结果：表单一行（状态 · 单号 / 原因），带文件的可下载（N2），带追问的就地答（N1b）；单据每张一行（已入账带单号 / 进行中 / 已跳过 / 未完成）。
// 卡上的字：标签与说明由服务端翻好；状态与格名沿用已上线的词。只用标准组件与令牌。

import { ChevronDownIcon, ChevronRightIcon, DownloadIcon, PencilIcon } from "lucide-react";
import { type FC, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { co, guide, op, run as runWords, t } from "@/i18n";
import { fileUrl } from "@/lib/managed-agents/api";
import {
  canEdit, cancelGuide, cellValue, confirmGuide, type DocMark, type DocRow, editGuide, followGuide, type GuideDocResult, type GuideDocReview,
  type GuideResult, type GuideReview as FormReviewData, type GuideReviewTable, type GuideTableRow, type GuideUploads, type GuideView, HEAD_FIELDS, isDocResult, isDocReview,
  LINE_FIELDS, refusedText, reopenGuide, subsAnswered,
} from "@/lib/guide-run";
import { SubQuestionsField } from "./guide-inputs";

type Act = (call: () => Promise<GuideView>) => void;
type Props = { view: GuideView; sessionId: string | null; busy: boolean; failure: string; act: Act };

const Failure: FC<{ text?: string; slot?: string }> = ({ text, slot = "cc-ask-failure" }) =>
  text ? (
    <Alert variant="destructive" data-slot={slot}>
      <AlertDescription className="line-clamp-(--cc-alert-lines)">{text}</AlertDescription>
    </Alert>
  ) : null;

/** 底部按钮：确定（写入）或允许卡（拒绝 / 允许）。拒绝 = 取消这次引导。 */
const Decide: FC<Props & { ready: boolean; seen?: string }> = ({ view, busy, act, ready, seen }) => {
  const confirm = () => act(() => confirmGuide(view.run, view.version, seen));
  const cancel = () => act(() => cancelGuide(view.run));
  return (
    <div className="cc-card-actions" data-slot="cc-guide-decide" data-consent={view.consent ?? "confirm"}>
      {view.consent === "allow" ? (
        <>
          <Button type="button" variant="secondary" size="xs" disabled={busy} onClick={cancel}>{t("deny")}</Button>
          <Button type="button" size="xs" data-slot="cc-guide-confirm" disabled={busy || !ready} onClick={confirm}>{t("allow")}</Button>
        </>
      ) : (
        <>
          <Button type="button" variant="ghost" size="xs" data-slot="cc-guide-cancel" disabled={busy} onClick={cancel}>{t("cancel")}</Button>
          <Button type="button" size="xs" data-slot="cc-guide-confirm" disabled={busy || !ready} onClick={confirm}>{t("recordNow")}</Button>
        </>
      )}
    </div>
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
    <ItemGroup data-key={table.key} data-slot="cc-guide-table" className="cc-guide-body">
      <Item size="sm"><ItemContent><ItemTitle>{table.label}</ItemTitle></ItemContent></Item>
      {table.rows.map((row) => (
        <Collapsible key={row.code} defaultOpen={(row.issues ?? []).length > 0} data-slot="cc-guide-table-row" data-code={row.code}>
          <CollapsibleTrigger asChild>
            <Button type="button" variant="ghost" size="sm" className="w-full justify-start">
              <ChevronRightIcon />
              <span className="min-w-0 flex-1 truncate text-start">{[row.name || row.code, row.note].filter(Boolean).join(" · ")}</span>
              {(row.issues ?? []).length > 0 && <Badge variant="destructive">{row.issues!.length}</Badge>}
              {row.amount ? <span className="tabular-nums">{row.amount}</span> : null}
            </Button>
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

const FormReview: FC<Props & { review: FormReviewData }> = (props) => {
  const { view, busy, act, review } = props;
  const back = (step?: string) =>
    step ? (
      <Button type="button" variant="ghost" size="icon-xs" aria-label={t("edit")} disabled={busy} data-slot="cc-guide-back" data-step={step}
        onClick={() => act(() => reopenGuide(view.run, step, view.version))}>
        <PencilIcon />
      </Button>
    ) : null;
  return (
    <div data-slot="cc-guide-review" data-source="form" className="flex min-w-0 flex-col">
      <ItemGroup className="cc-guide-body">
        {(review.lines ?? []).map((line) => (
          <Item key={line.key} size="sm" data-key={line.key}>
            <ItemContent><ItemDescription>{line.label}</ItemDescription></ItemContent>
            <ItemActions><span className="text-end">{line.value}</span>{back(line.step)}</ItemActions>
          </Item>
        ))}
      </ItemGroup>
      {(review.tables ?? []).filter((table) => table.columns?.length).map((table) => <EditableTable key={table.key} {...props} table={table} />)}
      {(review.tables ?? []).filter((table) => !table.columns?.length).map((table) => (
        <ItemGroup key={table.key} data-key={table.key} className="cc-guide-body">
          <Item size="sm">
            <ItemContent><ItemTitle>{table.label}</ItemTitle></ItemContent>
            <ItemActions>{back(table.step)}</ItemActions>
          </Item>
          {table.rows.map((row) => (
            <Item key={row.code} size="sm" data-code={row.code}>
              <ItemContent>
                <ItemTitle>{row.name || row.code}</ItemTitle>
                {row.note ? <ItemDescription>{row.note}</ItemDescription> : null}
              </ItemContent>
              <ItemActions><span className="tabular-nums">{row.amount}</span></ItemActions>
            </Item>
          ))}
        </ItemGroup>
      ))}
      {review.total ? (
        <Item size="sm" data-slot="cc-guide-total" className="cc-guide-body">
          <ItemContent><ItemTitle>{guide.total}</ItemTitle></ItemContent>
          <ItemActions><span className="tabular-nums">{review.total}</span></ItemActions>
        </Item>
      ) : null}
      <Failure text={review.problem_text} slot="cc-guide-problem" />
      <Failure text={props.failure} />
      <Decide {...props} ready={review.ready} />
    </div>
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
const Cell: FC<{ row: DocRow; field: string; label: string; disabled: boolean; onCommit: (field: string, value: string) => void }> = ({
  row, field, label, disabled, onCommit,
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
        className={NUMERIC.has(column) ? "tabular-nums" : undefined}>
        {shown}
      </span>
    );
  }
  return (
    <Input data-slot="cc-guide-cell" data-field={field} aria-label={label} value={draft ?? shown} disabled={disabled} aria-invalid={wrong || undefined}
      inputMode={NUMERIC.has(column) ? "decimal" : undefined} className={NUMERIC.has(column) ? "cc-guide-amount w-(--cc-guide-amount-w) shrink-0" : undefined}
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
      <CollapsibleTrigger asChild>
        <Button type="button" variant="ghost" size="sm" className="w-full justify-start">
          {shown ? <ChevronDownIcon /> : <ChevronRightIcon />}
          <span className="min-w-0 flex-1 truncate text-start">{[face.doc_no, face.party_name].filter(Boolean).join(" · ")}</span>
          {row.marks.map((mark) => <Badge key={mark} variant="destructive" data-mark={mark}>{MARK_WORD[mark]?.() ?? mark}</Badge>)}
          <span className="tabular-nums">{face.total}</span>
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ItemGroup className="cc-guide-body">
          {HEAD_FIELDS.map((field) => (
            <Item key={field} size="sm" data-field={field}>
              <ItemContent><ItemDescription>{HEAD_LABEL[field]()}</ItemDescription></ItemContent>
              <ItemActions><Cell row={row} field={field} label={HEAD_LABEL[field]()} disabled={disabled} onCommit={commit} /></ItemActions>
            </Item>
          ))}
          {face.lines.length > 0 && (
            <Item size="sm" data-line="head">
              <ItemContent><ItemDescription>{LINE_LABEL.description()}</ItemDescription></ItemContent>
              <ItemActions data-slot="cc-guide-line-head">
                {(["qty", "unit_price", "amount"] as const).map((column) => <span key={column} className="cc-askq-desc">{LINE_LABEL[column]()}</span>)}
              </ItemActions>
            </Item>
          )}
          {face.lines.map((_line, index) => (
            <Item key={index} size="sm" data-line={index} className="flex-wrap">
              <ItemContent className="basis-full">
                <Cell row={row} field={`lines.${index}.description`} label={LINE_LABEL.description()} disabled={disabled} onCommit={commit} />
              </ItemContent>
              <ItemActions className="ms-auto">
                {(["qty", "unit_price", "amount"] as const).map((column) => (
                  <Cell key={column} row={row} field={`lines.${index}.${column}`} label={LINE_LABEL[column]()} disabled={disabled} onCommit={commit} />
                ))}
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      </CollapsibleContent>
    </Collapsible>
  );
};

const DocReview: FC<Props & { review: GuideDocReview }> = (props) => {
  const { view, busy, act, review } = props;
  const edit = (docId: string, field: string, value: string) => act(() => editGuide(view.run, docId, field, value, view.version));
  const refused = refusedText(view);
  return (
    <div data-slot="cc-guide-review" data-source="documents" className="flex min-w-0 flex-col">
      {review.rows.map((row) => (
        <DocBlock key={row.doc_id} row={row} open={review.rows.length === 1} disabled={busy} onCommit={edit} />
      ))}
      {review.held.length > 0 && (
        <Item size="sm" data-slot="cc-guide-held" className="cc-guide-body">
          <ItemContent><ItemDescription>{op.doc.skipped}</ItemDescription></ItemContent>
          <ItemActions><span className="tabular-nums">{review.held.length}</span></ItemActions>
        </Item>
      )}
      <Failure text={refused} slot="cc-guide-refused" />
      <Failure text={props.failure} />
      <Decide {...props} ready={review.ready} seen={review.seen} />
    </div>
  );
};

export const GuideReview: FC<Props> = (props) => {
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

/** 结果里的追问（N1b）：答了交 guide_follow，服务端重算结果。 */
const Follow: FC<Props & { result: GuideResult }> = ({ view, busy, act, result }) => {
  const [value, setValue] = useState<Record<string, unknown>>({});
  const subs = result.questions ?? [];
  return (
    <div data-slot="cc-guide-follow" className="flex min-w-0 flex-col">
      <SubQuestionsField questions={subs} value={value} disabled={busy} onChange={setValue} />
      <div className="cc-card-actions">
        <Button type="button" size="xs" disabled={busy || !subsAnswered(subs, value)} onClick={() => act(() => followGuide(view.run, value, view.version))}>
          {t("send")}
        </Button>
      </div>
    </div>
  );
};

const DocResultRows: FC<{ result: GuideDocResult }> = ({ result }) => (
  <ItemGroup data-slot="cc-guide-result" data-result={result.status} className="cc-guide-body">
    {result.rows.map((row) => (
      <Item key={row.doc_id} size="sm" data-doc={row.doc_id} data-status={row.status}>
        <ItemContent>
          <ItemTitle className="truncate">{row.doc_no || row.doc_id}</ItemTitle>
          {row.reason_text ? <ItemDescription>{row.reason_text}</ItemDescription> : null}
        </ItemContent>
        <ItemActions>
          {row.status === "queued" ? <Spinner /> : null}
          <span>{STATUS_WORD[row.status]?.() ?? row.status}</span>
          {row.docnum ? <span className="tabular-nums">{row.docnum}</span> : null}
        </ItemActions>
      </Item>
    ))}
  </ItemGroup>
);

export const GuideResultCard: FC<Props> = (props) => {
  const { view, sessionId } = props;
  const result = view.result!;
  if (isDocResult(result)) return <DocResultRows result={result} />;
  const form = result as GuideResult;
  return (
    <div data-slot="cc-guide-result" data-result={form.status} className="flex min-w-0 flex-col">
      <Item size="sm" className="cc-guide-body">
        <ItemContent>
          <ItemTitle>{STATUS_WORD[form.status]?.() ?? form.status}</ItemTitle>
          {form.reason_text ? <ItemDescription>{form.reason_text}</ItemDescription> : null}
        </ItemContent>
        <ItemActions>
          {form.wait ? <Spinner /> : null}
          <span className="tabular-nums">{[form.docnum, form.total].filter(Boolean).join(" · ")}</span>
        </ItemActions>
      </Item>
      {(form.files ?? []).length > 0 && sessionId && (
        <ItemGroup className="cc-guide-body" data-slot="cc-guide-files-out">
          {form.files!.map((file) => (
            <Item key={file.file} size="sm" data-slot="cc-guide-result-file" data-file-name={file.name}>
              <ItemContent><ItemTitle className="break-all">{file.name}</ItemTitle></ItemContent>
              <ItemActions>
                <Button asChild variant="ghost" size="icon-xs" aria-label={`${t("download")} ${file.name}`}>
                  <a href={fileUrl(sessionId, file.file)} download={file.name}><DownloadIcon /></a>
                </Button>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      )}
      {(form.questions ?? []).length > 0 && <Follow {...props} result={form} />}
      <Failure text={props.failure} />
    </div>
  );
};
