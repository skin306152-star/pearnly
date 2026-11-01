// 「实操」的右侧栏（照 Claude 右栏：整张贴右边、左边 1px 线、可折叠分区、分区之间 1px 线；样式 globals.css 的 .cc-op-panel / .cc-op-section*，
// 与对话页的会话栏 .cc-panel 分开）：
//   ① 资料        上传框（拖放 / 选文件 / 手机拍照），已收的单按组（采购、销售、费用、收付款、不入账）列出与数量；准备中可把一张拖到
//                 别的组改分组（共用拖动 components/ui/drag.tsx，站点按公司记住）
//   ② 开始前确认  要人定的按类一次问完（商品、供应商 / 客户、费用科目、付款方式、税率与票面不符……），答完才能「开始」
//   ③ 需要处理    没做完的单（原因 + 已作废单号）：重试、跳过；整个任务停下等人的（Express 登录、选公司……）也在这里
//   ④ 电脑与 Express  哪台电脑、在线、Express 登录 已设置 / 未设置（设置 / 更换）
//   ⑤ 报告        导出报告
//   「开始」      在报告下面靠右（内容变长跟着往下走）；一直能点：还不能开始时点了说还差什么（开始前确认还有几项、没有要处理的单、
//                 电脑离线、Express 登录未设置），并打开对应的分区
// 处理完的分区自动收起、标 ✓；点过的按点的（同步到服务端偏好）。宽屏与主栏并排、从窗口顶到底一整列（开关在它右上角，记在本机），
// 左边的分隔条拖宽（同左侧栏、文件预览，split-handle.tsx，宽度记在本机；滚动在里面那一层，分隔条不跟着滚走）；窄屏
// （< --cc-op-narrow）从右边整高滑出、点外面关，手机盖满屏。

import { CameraIcon, CheckIcon, ChevronRightIcon, XIcon } from "lucide-react";
import { type CSSProperties, type FC, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { operatorPanelSize, SplitHandle } from "@/components/split-handle";
import { DragArea, type DragData, DragList, type DragTexts, useDropZone, useSortableItem } from "@/components/ui/drag";
import { Marquee } from "@/components/ui/marquee";
import { co, op, t } from "@/i18n";
import { formatAmount } from "@/lib/companies";
import { ACCEPT } from "@/lib/managed-agents/attachments";
import { useOperator } from "./controller";
import {
  allAnswered,
  batchNeedsHuman,
  docTitle,
  groupDocuments,
  groupOf,
  isFinal,
  kindTitle,
  needsYou,
  notDoneDetail,
  optionLabel,
  questionGroups,
  reasonText,
  reportCsv,
  RESULT_UNKNOWN,
  stoppedDocs,
  unknownDocs,
} from "./model";
import { setFold, useOperatorStore } from "./store";
import type { DocGroup, DocumentOption, OperatorComputer } from "./types";

export type PanelMode = "docked" | "floating" | "phone";
export const SECTIONS = ["files", "confirm", "handle", "computer", "report"] as const;
export type SectionId = (typeof SECTIONS)[number];

/** 一个分区：标题 + 右侧一句（数量或 ✓）是折叠开关；done = 处理完了，没点过时收着、标 ✓。shut = 没点过时收着（不标 ✓）。 */
export const Section: FC<{ task: string; id: SectionId; done: boolean; summary?: ReactNode; children?: ReactNode; shut?: boolean }> = ({
  task,
  id,
  done,
  summary,
  children,
  shut = false,
}) => {
  const chosen = useOperatorStore((s) => s.folds[`${task}:${id}`]);
  const open = chosen ?? !(done || shut);
  return (
    <section className="cc-op-section" data-section={id} data-done={done || undefined} data-slot="cc-op-section">
      <button type="button" className="cc-op-section-toggle" aria-expanded={open} onClick={() => setFold(`${task}:${id}`, !open)}>
        <ChevronRightIcon className="cc-op-section-chevron" aria-hidden />
        <Marquee className="min-w-0 flex-1 truncate text-start">{op.section[id]}</Marquee>
        {done ? (
          <CheckIcon className="cc-op-section-check" role="img" aria-label={op.state.done} />
        ) : (
          summary !== undefined && summary !== "" && <span className="cc-op-section-summary">{summary}</span>
        )}
      </button>
      {open && children && <div className="cc-op-section-body">{children}</div>}
    </section>
  );
};

/** top：并排时右侧栏最上面那一行（右上角的右侧栏开关；桌面窗口里还有窗口按钮），与主栏顶栏同高，右侧栏从窗口顶到底一整列。
 *  width：并排时的宽度（split-handle.tsx 的 useStoredSize：拖左边的分隔条改，记在本机）。 */
export const OperatorPanel: FC<{
  mode: PanelMode;
  title: string;
  onClose: () => void;
  top?: ReactNode;
  width?: { style: CSSProperties; update: (value: number | null) => void };
}> = ({ mode, title, onClose, top, width }) => {
  const o = useOperator();
  const task = o.id;
  const box = useRef<HTMLElement>(null);
  // 抽屉、全屏：Esc 关（菜单、弹窗开着时它们先关）
  useEffect(() => {
    if (mode === "docked") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || document.querySelector('[role="dialog"], [role="menu"]')) return;
      e.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mode, onClose]);

  return (
    <>
      {mode === "floating" && <div className="cc-op-scrim" data-slot="cc-op-scrim" aria-hidden onClick={onClose} />}
      <aside ref={box} className="cc-op-panel" data-mode={mode} data-slot="cc-op-panel" aria-label={title} style={mode === "docked" ? width?.style : undefined}>
        {mode === "docked" && width && <SplitHandle size={operatorPanelSize} edge="start" onChange={width.update} />}
        {mode === "docked" && top}
        {mode !== "docked" && (
          <div className="cc-op-panel-top">
            <Marquee className="min-w-0 flex-1 truncate">{title}</Marquee>
            <button type="button" className="cc-icon-btn" aria-label={t("close")} title={t("close")} onClick={onClose}>
              <XIcon />
            </button>
          </div>
        )}
        <div className="cc-op-panel-scroll" data-slot="cc-op-panel-scroll">
          <FilesSection task={task} />
          <ConfirmSection task={task} />
          <HandleSection task={task} />
          <ComputerSection task={task} />
          <ReportSection task={task} />
          <StartRow />
        </div>
      </aside>
    </>
  );
};

// ---------------------------------------------------------------- ① 资料
const FilesSection: FC<{ task: string }> = ({ task }) => {
  const o = useOperator();
  if (o.draft) {
    const docs = o.data.docs;
    return (
      <Section task={task} id="files" done={false} summary={docs ? docs.length : undefined}>
        <Dropzone onFiles={o.intake} busy={o.busy} />
        {docs && <DraftGroups docs={docs} />}
        {o.data.next !== null && (
          <button type="button" className="cc-show-more" disabled={o.busy} onClick={o.loadMore}>
            {co.showMore}
          </button>
        )}
      </Section>
    );
  }
  const docs = o.snapshot?.docs ?? [];
  const groups = groupDocuments(
    docs.map((d) => ({ id: d.doc_id, group: d.summary?.group ?? "purchase", ref: d.summary?.ref, party: d.summary?.party, date: d.summary?.date })),
  );
  return (
    <Section task={task} id="files" done={!!o.snapshot} summary={docs.length}>
      {groups.map(([group, rows]) => (
        <GroupHead key={group} group={group} count={rows.length} />
      ))}
    </Section>
  );
};

const GroupHead: FC<{ group: DocGroup; count: number }> = ({ group, count }) => (
  <div className="cc-op-group-head" data-group={group}>
    <Marquee className="min-w-0 flex-1 truncate">{op.group[group]}</Marquee>
    <span className="cc-op-section-summary">{count}</span>
  </div>
);

/** 上传框：拖放、点了选文件；手机上另有「拍照」（同一个框，进同一条路）。 */
export const Dropzone: FC<{ onFiles: (files: readonly File[]) => void; busy: boolean }> = ({ onFiles, busy }) => {
  const picker = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const take = (list: FileList | null, input: HTMLInputElement | null) => {
    const files = list ? Array.from(list) : [];
    if (input) input.value = "";
    if (files.length) onFiles(files);
  };
  return (
    <div
      className="cc-op-drop"
      data-over={over || undefined}
      data-slot="cc-op-drop"
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files, null);
      }}
    >
      <button type="button" className="cc-op-drop-pick" disabled={busy} onClick={() => picker.current?.click()}>
        <span>{op.drop}</span>
        <span className="text-ink-3 text-caption">{op.dropTypes}</span>
      </button>
      <button type="button" className="cc-btn cc-op-camera" disabled={busy} onClick={() => camera.current?.click()}>
        <CameraIcon />
        {op.takePhoto}
      </button>
      <input ref={picker} type="file" multiple accept={ACCEPT} hidden onChange={(e) => take(e.target.files, e.target)} />
      <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={(e) => take(e.target.files, e.target)} />
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

/** 准备中：按组列出，拖到别的组改分组（落点是整组，自己那一组不算）。 */
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
      <GroupHead group={group} count={rows.length} />
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
        {doc.total && <span className="cc-op-section-summary">{formatAmount(doc.total)}</span>}
      </span>
    </div>
  );
};

// ---------------------------------------------------------------- ② 开始前确认
type UnknownItem = { key: string; title: string; message: string; entered: () => void; notEntered: () => void };

/** 上次推送结果不明的单（准备中）：站点给的那一句 +「已入账」「没有入账」（标了再预检一次）。 */
export function useUnknownItems(): UnknownItem[] {
  const o = useOperator();
  if (!o.draft) return [];
  return unknownDocs(o.data.prechecks ?? [], o.draft.skipped).map((row) => ({
    key: row.doc_id,
    title: docTitle(o.data.docs?.find((d) => d.id === row.doc_id) ?? {}, row.doc_id),
    message: row.message,
    entered: () => o.resolve(row.doc_id, true),
    notEntered: () => o.resolve(row.doc_id, false),
  }));
}

const ConfirmSection: FC<{ task: string }> = ({ task }) => {
  const o = useOperator();
  const unknown = useUnknownItems();
  if (!o.draft) return <Section task={task} id="confirm" done={!!o.snapshot} />;
  const { questions } = o;
  const loaded = !!o.data.prechecks;
  const answered = questions.filter((q) => o.draft!.answers[q.key]).length;
  const total = questions.length + unknown.length;
  const done = loaded && allAnswered(questions, o.draft.answers) && !unknown.length;
  const groupOfDoc = (id: string) => groupOf(o.data.docs?.find((d) => d.id === id) ?? {});
  return (
    <Section task={task} id="confirm" done={done} summary={loaded && total ? `${answered} / ${total}` : undefined}>
      {unknown.map((item) => (
        <div key={item.key} className="cc-op-handle" data-slot="cc-op-unknown">
          <Marquee className="block min-w-0 truncate text-ink">{item.title}</Marquee>
          <span className="text-ink-3 text-caption">{item.message}</span>
          <div className="cc-card-actions">
            <button type="button" className="cc-card-btn" data-slot="cc-op-entered" disabled={o.busy} onClick={item.entered}>
              {op.doc.saved}
            </button>
            <button type="button" className="cc-card-btn" data-slot="cc-op-not-entered" disabled={o.busy} onClick={item.notEntered}>
              {op.notEntered}
            </button>
          </div>
        </div>
      ))}
      {questionGroups(questions).map(([kind, list]) => (
        <div key={kind} className="cc-op-ask-group" data-kind={kind} data-slot="cc-op-ask-group">
          <div className="cc-op-ask-kind">{kindTitle(kind, list.flatMap((q) => q.doc_ids.map(groupOfDoc)))}</div>
          {list.map((q) => (
            <label key={q.key} className="cc-op-ask" data-slot="cc-op-ask">
              <Marquee className="min-w-0 flex-1 truncate">{q.label}</Marquee>
              <select
                className="cc-input cc-op-ask-select"
                aria-label={`${kindTitle(kind)} · ${q.label}`}
                value={o.draft!.answers[q.key] ?? ""}
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
    </Section>
  );
};

// ---------------------------------------------------------------- ③ 需要处理
type HandleItem = {
  key: string; title: string; detail: string; retry?: () => void; retryLabel?: string; skip?: () => void;
  entered?: () => void; notEntered?: () => void;
};

export function useHandleItems(): HandleItem[] {
  const o = useOperator();
  if (o.draft) {
    return stoppedDocs(o.data.prechecks ?? [], o.draft.skipped).map((row) => {
      const doc = o.data.docs?.find((d) => d.id === row.doc_id);
      return { key: row.doc_id, title: docTitle(doc ?? {}, row.doc_id), detail: row.reason, retry: () => o.retry(row.doc_id), skip: () => o.skip(row.doc_id) };
    });
  }
  const s = o.snapshot;
  if (!s) return [];
  const items: HandleItem[] = [];
  if (batchNeedsHuman(s) && !s.remote) {
    const login = s.reason === "login_failed" || s.reason === "login_missing";
    items.push({ key: "batch", title: reasonText(s.reason), detail: "", retry: login ? o.authorize : o.start, retryLabel: login ? op.setUp : undefined });
  }
  for (const doc of s.docs.filter(needsYou)) {
    const local = !s.remote || doc.refused;
    if (doc.reason === RESULT_UNKNOWN && s.site) {
      // 推送结果不明（CC 重启打断的那张）：同开始前确认，在 Express 里核对后标「已入账」「没有入账」（10-29 测试机）
      items.push({ key: doc.doc_id, title: docTitle(doc.summary ?? {}, doc.doc_id),
        detail: [co.ledger.code.result_unknown, ...doc.voided.map((no) => op.voidedNo(no))].join(" · "),
        entered: () => o.resolve(doc.doc_id, true), notEntered: () => o.resolve(doc.doc_id, false) });
      continue;
    }
    items.push({
      key: doc.doc_id,
      title: docTitle(doc.summary ?? {}, doc.doc_id),
      detail: notDoneDetail(doc),
      // 这台电脑上已不在跑的任务（CC 重启后恢复出来的）桥上没有录单线，重试只会报错：不给
      retry: !s.remote && !doc.refused && s.live !== false ? () => o.retry(doc.doc_id) : undefined,
      skip: local ? () => o.skip(doc.doc_id) : undefined,
    });
  }
  return items;
}

const HandleSection: FC<{ task: string }> = ({ task }) => {
  const o = useOperator();
  const items = useHandleItems();
  return (
    <Section task={task} id="handle" done={!items.length && (!!o.snapshot || !!o.data.prechecks)} summary={items.length || undefined}>
      {items.map((item) => (
        <div key={item.key} className="cc-op-handle" data-slot="cc-op-handle">
          <Marquee className="block min-w-0 truncate text-ink">{item.title}</Marquee>
          {item.detail && <span className="text-ink-3 text-caption">{item.detail}</span>}
          <div className="cc-card-actions">
            {item.retry && (
              <button type="button" className="cc-card-btn" data-slot="cc-op-retry" disabled={o.busy} onClick={item.retry}>
                {item.retryLabel ?? op.retry}
              </button>
            )}
            {item.skip && (
              <button type="button" className="cc-card-btn" data-slot="cc-op-skip" disabled={o.busy} onClick={item.skip}>
                {op.skip}
              </button>
            )}
            {item.entered && (
              <button type="button" className="cc-card-btn" data-slot="cc-op-entered" disabled={o.busy} onClick={item.entered}>
                {op.doc.saved}
              </button>
            )}
            {item.notEntered && (
              <button type="button" className="cc-card-btn" data-slot="cc-op-not-entered" disabled={o.busy} onClick={item.notEntered}>
                {op.notEntered}
              </button>
            )}
          </div>
        </div>
      ))}
    </Section>
  );
};

// ---------------------------------------------------------------- ④ 电脑与 Express
/** 电脑名：本机带「本机」（名字本来就是「本机」的不重复）。 */
export const pcName = (pc: Pick<OperatorComputer, "name" | "local">) =>
  pc.local && pc.name !== op.thisComputer ? `${pc.name} · ${op.thisComputer}` : pc.name;

const ComputerSection: FC<{ task: string }> = ({ task }) => {
  const o = useOperator();
  const [batchPcs, setBatchPcs] = useState<OperatorComputer[] | null>(null);
  const company = o.snapshot?.company ?? "";
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
  const done = !!pc && pc.online && pc.login !== false;
  return (
    <Section task={task} id="computer" done={done && !o.draft} summary={pc?.name}>
      {o.draft && computers.length > 1 ? (
        <label className="cc-op-line">
          <Marquee className="min-w-0 flex-1 truncate">{op.computer}</Marquee>
          <select className="cc-input cc-op-ask-select" value={pc?.id ?? ""} onChange={(e) => o.chooseComputer(e.target.value)}>
            {computers.map((c) => (
              <option key={c.id} value={c.id} disabled={!c.online}>
                {pcName(c)}
              </option>
            ))}
          </select>
        </label>
      ) : (
        pc && (
          <div className="cc-op-line" data-slot="cc-op-computer">
            <Marquee className="min-w-0 flex-1 truncate">{pcName(pc)}</Marquee>
            <span className="cc-status-line" data-status={pc.online ? "online" : "offline"}>
              <span className="cc-status-dot" />
              {pc.online ? op.online : op.offline}
            </span>
          </div>
        )
      )}
      {pc && pc.login !== null && (
        <div className="cc-op-line" data-slot="cc-op-login">
          <Marquee className="min-w-0 flex-1 truncate">{op.expressLogin}</Marquee>
          <span className="text-ink-3">{pc.login ? op.loginSet : op.loginUnset}</span>
          {pc.local && (
            <button type="button" className="cc-card-btn" data-slot="cc-op-authorize" disabled={o.busy || !pc.online} onClick={o.authorize}>
              {pc.login ? op.change : op.setUp}
            </button>
          )}
        </div>
      )}
    </Section>
  );
};

// ---------------------------------------------------------------- ⑤ 报告
/** 导出：每张单一行的 CSV（Excel 直接打开），文件名按任务标题。 */
export function downloadReport(name: string, csv: string) {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name.replace(/[\\/:*?"<>|]+/g, " ").trim() || "report"}.csv`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

const ReportSection: FC<{ task: string }> = ({ task }) => {
  const o = useOperator();
  const s = o.snapshot;
  const final = !!s && isFinal(s.status);
  return (
    <Section task={task} id="report" done={false} shut={!final}>
      {s && (
        <div className="cc-op-line">
          <button
            type="button"
            className="cc-btn"
            data-slot="cc-op-export"
            onClick={() => downloadReport([s.company_title || s.company, s.period].filter(Boolean).join(" "), reportCsv(s))}
          >
            {op.exportReport}
          </button>
        </div>
      )}
    </Section>
  );
};

// ---------------------------------------------------------------- 开始
/** 「开始」：报告下面靠右（内容变长跟着往下走）。准备中、或这台电脑上停下还有没做的单（续做）时有；一直能点，还不能开始时点了在
 *  它上面说还差什么（controller.tsx 的 startBlocker），并打开那一个分区。 */
const StartRow: FC = () => {
  const o = useOperator();
  const s = o.snapshot;
  const resumable = !!s && !s.remote && (s.status === "ready" || s.status === "stopped") && s.docs.some((d) => d.status === "pending");
  if (!o.draft && !resumable) return null;
  return (
    <div className="cc-op-start" data-slot="cc-op-start-row">
      {o.notice && (
        <div role="alert" className="cc-alert" data-size="sm" data-slot="cc-op-start-notice">
          {o.notice}
        </div>
      )}
      <button type="button" className="cc-btn" data-primary="" data-slot="cc-op-start" onClick={o.start}>
        {op.start}
      </button>
    </div>
  );
};
