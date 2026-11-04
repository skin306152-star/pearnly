// 代录的步骤行展开后的内容（方案 14.10）：同别的工具一样收进步骤组（steps.tsx 的 StepsGroup：「识别了文件 ×3（1 个失败）›」），点开一份一行。
// 一步此刻的状态：同一个 tool_use_id 下 processed_at 最新的那条（结果或 pearnly.progress）说了算（lib/managed-agents/progress.ts 的 stepProgress）。
//   operator_upload    一份资料一张上传卡片（W2 的 UploadCard：排队中 → 识别中 n/m 页 → 已识别 N 张 · N 张不在 10 月 / 失败）
//   operator_document  一张单一行（单号 · 对方），排着 / 被挡着的行尾说明、在做的行尾在动的点；录成写 Express 单号，没录成行尾危险色「失败」、
//                      下面一行原因（代码 → 文案）
//   operator_sources   这家这个月的资料：交单后是按组的张数；准备中点开照旧调 documents（服务端分页，「显示更多」），按组列出，
//                      拖到别的组改分组（共用拖动 components/ui/drag.tsx，照旧调站点 regroup，站点按公司记住）
// 样式只用 globals.css 的 .cc-step-* 与 .cc-op-*，不新造。

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ChevronRightIcon } from "lucide-react";
import { type FC, useMemo, useState } from "react";
import { UploadCard } from "@/components/run/upload-card";
import type { UploadState } from "@/components/run/types";
import { TypingDots } from "@/components/typing-dots";
import { DragArea, type DragData, DragList, type DragTexts, useDropZone, useSortableItem } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";
import { co, op, steps } from "@/i18n";
import { formatAmount } from "@/lib/companies";
import { reportCaught } from "@/lib/caught";
import type { ProgressEvent } from "@/lib/managed-agents/events";
import { progressText, stepProgress } from "@/lib/managed-agents/progress";
import { useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import {
  type DocGroup,
  type DocRef,
  DOC_GROUPS,
  type DocumentInput,
  type DocumentResult,
  docTitle,
  groupOf,
  operatorActions,
  reasonText,
  resultOf,
  type SourcesInput,
  type SourcesResult,
  type UploadInput,
  type UploadResult,
} from "./operator-shared";

type StepPart = { toolCallId: string; result?: unknown; resultAt?: string };

/** 这一步此刻的进度（比结果新才算）。 */
function useStepProgress(part: StepPart): ProgressEvent | null {
  const progress = useActiveSessionSnapshot().progress;
  return stepProgress(progress, part);
}

// ---------------------------------------------------------------- 上传
/** 一份资料此刻的卡片状态：进度比结果新（或没结果）按进度（排队中 / 识别中 n/m 页）；否则已识别 / 失败；什么都没有是排队中。 */
export function uploadStateOf(result: unknown, isError: boolean, progress: ProgressEvent | null): UploadState {
  if (progress) return progress.label === "reading_pages" ? { kind: "reading", pagesDone: progress.done, pageCount: progress.total } : { kind: "queued" };
  const read = resultOf<UploadResult>(result);
  if (!read) return { kind: "queued" };
  if (isError || read.state === "failed") return { kind: "failed", stage: "read", reason: reasonText(read.code) };
  const documents = read.documents ?? [];
  return { kind: "done", documents: documents.length, outside: documents.filter((d) => d.in_period === false).length };
}

export const OperatorUploadStep: ToolCallMessagePartComponent = (props) => {
  const input = (props.args ?? {}) as unknown as UploadInput;
  const progress = useStepProgress(props as unknown as StepPart);
  const state = uploadStateOf(props.result, !!props.isError, progress);
  return (
    <div className="aui-step" data-slot="cc-op-upload">
      <UploadCard name={input.filename ?? ""} {...(input.size ? { size: input.size } : {})} state={state} />
    </div>
  );
};

// ---------------------------------------------------------------- 单据
/** 一张单那一行的样子：进度比结果新（排着、被挡着）→ 排着；没结果 → 在做；有结论按 state。 */
export function documentRow(result: unknown, isError: boolean, progress: ProgressEvent | null):
  { tone: "queued" | "running" | "done" | "skipped" | "failed"; detail: string } {
  if (progress) return { tone: "queued", detail: progressText(progress) };
  const read = resultOf<DocumentResult>(result);
  if (!read) return { tone: "running", detail: "" };
  if (isError || read.state === "not_done" || read.result_unknown) {
    const reason = read.result_unknown ? reasonText("push.result_unknown") : reasonText(read.reason ?? read.code);
    return { tone: "failed", detail: [reason, read.voided_docnum ? op.voidedNo(read.voided_docnum) : ""].filter(Boolean).join(" · ") };
  }
  if (read.state === "skipped" || read.state === "cancelled") return { tone: "skipped", detail: op.doc.skipped! };
  return { tone: "done", detail: read.docnum ?? "" };
}

export const OperatorDocumentStep: ToolCallMessagePartComponent = (props) => {
  const doc = (props.args ?? {}) as unknown as DocumentInput;
  const progress = useStepProgress(props as unknown as StepPart);
  const row = documentRow(props.result, !!props.isError, progress);
  return (
    <div className="aui-step" data-slot="cc-op-row" data-tone={row.tone}>
      <div className="cc-step-row">
        <Marquee className="min-w-0 flex-1 truncate">
          {docTitle(doc, doc.doc_id)}
          {row.tone === "done" && row.detail && <span className="cc-step-target">{` · ${row.detail}`}</span>}
        </Marquee>
        {row.tone === "queued" && <span className="cc-step-target">{row.detail}</span>}
        {row.tone === "running" && <TypingDots size="sm" className="shrink-0" />}
        {row.tone === "skipped" && <span className="cc-step-target">{row.detail}</span>}
        {row.tone === "failed" && <span className="cc-step-failed">{steps.failed}</span>}
      </div>
      {row.tone === "failed" && row.detail && <div className="cc-step-detail px-(--cc-space-8) text-danger">{row.detail}</div>}
    </div>
  );
};

// ---------------------------------------------------------------- 资料
/** 一行摘要：资料 N 张 · 采购 n · 费用 m（空组不写）。counts：按组的张数（交单后的结果，或本页取到的）。 */
export function sourcesLine(total: number, counts: Partial<Record<string, number>>): string {
  const groups = DOC_GROUPS.filter((g) => (counts[g] ?? 0) > 0).map((g) => `${op.group[g]} ${counts[g]}`);
  return [op.sources(total), ...groups].join(" · ");
}

/** 按组排好（组的先后按 DOC_GROUPS，组内按日期、单号）；all = 空组也列出（拖动时当落点）。 */
export function groupDocs(docs: readonly DocRef[], all = false): Array<[DocGroup, DocRef[]]> {
  const byDate = (a: DocRef, b: DocRef) =>
    String(a.date ?? "").localeCompare(String(b.date ?? "")) || String(a.ref ?? a.doc_id).localeCompare(String(b.ref ?? b.doc_id));
  return DOC_GROUPS.map((group) => [group, docs.filter((doc) => groupOf(doc) === group).sort(byDate)] as [DocGroup, DocRef[]]).filter(
    ([, rows]) => all || rows.length > 0,
  );
}

// 只在入账的三组之间拖（站点按公司记住）
const ENTRY_GROUPS = new Set<string>(["purchase", "expense", "sales"]);

const docTexts = (): DragTexts => ({
  instructions: co.card.instructions,
  pickedUp: co.card.pickedUp,
  movedTo: co.card.movedTo,
  droppedAt: co.card.droppedAt,
  cancelled: co.card.cancelled,
  over: (name, group) => `${name} · ${group}`,
  droppedIn: (name, group) => `${name} · ${group}`,
});

type Listed = { docs: DocRef[]; next: number | null; total: number };

export const OperatorSourcesStep: ToolCallMessagePartComponent = ({ args, result }) => {
  const input = (args ?? {}) as unknown as SourcesInput;
  const done = resultOf<SourcesResult>(result);
  const [listed, setListed] = useState<Listed | null>(null);
  const [open, setOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const texts = useMemo(docTexts, []);
  const load = async (offset: number) => {
    try {
      const page = await operatorActions.documents(input.company, input.period, offset);
      setListed((before) => ({ docs: [...(offset ? (before?.docs ?? []) : []), ...page.documents], next: page.next, total: page.total ?? page.documents.length }));
    } catch (caughtError) {
      reportCaught(caughtError, "operator-sources");
    }
  };
  const toggle = () => {
    if (!open && !done && !listed) void load(0);
    setOpen((o) => !o);
  };
  const regroup = (docId: string, group: DocGroup) => {
    const before = listed;
    setListed((l) => (l ? { ...l, docs: l.docs.map((d) => (d.doc_id === docId ? { ...d, group } : d)) } : l));
    void operatorActions.regroup(input.company, docId, group).catch((caughtError: unknown) => {
      reportCaught(caughtError, "operator-regroup");
      setListed(before);
    });
  };
  const counts: Partial<Record<string, number>> = done?.groups
    ?? Object.fromEntries(groupDocs(listed?.docs ?? []).map(([g, rows]) => [g, rows.length]));
  const total = done?.total ?? listed?.total ?? 0;
  const line = done || listed ? sourcesLine(total, counts) : op.steps.operator_sources![1];
  return (
    <div className="aui-step" data-slot="cc-op-sources">
      <button type="button" className="cc-step-row cc-tap" aria-expanded={open} disabled={!!done} onClick={toggle}>
        <Marquee className="min-w-0 flex-1 truncate">{line}</Marquee>
        {!done && <ChevronRightIcon aria-hidden />}
      </button>
      {open && !done && listed && listed.docs.length > 0 && (
        <div className="cc-step-detail" data-slot="cc-op-sources-list">
          <DragArea
            texts={texts}
            accepts={(active, target) =>
              !!target.zone && target.list !== active.list && ENTRY_GROUPS.has(String(active.list)) && ENTRY_GROUPS.has(String(target.list))}
            onActive={(active) => setDragging(!!active)}
            onDrop={(active, target) => {
              if (target.list && target.list !== active.list) regroup(String(active.id), target.list as DocGroup);
            }}
          >
            {groupDocs(listed.docs, dragging).map(([group, rows]) => (
              <DocGroupList key={group} group={group} rows={rows} />
            ))}
          </DragArea>
          {listed.next !== null && (
            <button type="button" className="cc-show-more" onClick={() => void load(listed.next!)}>
              {co.showMore}
            </button>
          )}
        </div>
      )}
    </div>
  );
};

const DocGroupList: FC<{ group: DocGroup; rows: DocRef[] }> = ({ group, rows }) => {
  const zone = useDropZone({ id: `group:${group}`, kind: "doc-group", label: op.group[group]!, list: group }, true);
  return (
    <div ref={zone.ref} className="cc-op-group" data-group={group} data-drop-target={zone["data-drop-target"]} data-slot="cc-op-group">
      <div className="cc-op-group-head" data-group={group}>
        <Marquee className="min-w-0 flex-1 truncate">{op.group[group]}</Marquee>
        <span className="cc-op-count">{rows.length}</span>
      </div>
      <DragList id={group} items={rows.map((r) => r.doc_id)}>
        {rows.map((doc) => (
          <DocItem key={doc.doc_id} doc={doc} group={group} />
        ))}
      </DragList>
    </div>
  );
};

const DocItem: FC<{ doc: DocRef; group: DocGroup }> = ({ doc, group }) => {
  const title = docTitle(doc, doc.doc_id);
  const data: DragData = { id: doc.doc_id, kind: "doc", label: title, list: group };
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
