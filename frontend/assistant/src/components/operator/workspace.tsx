// 「实操」工作区的主栏与顶栏（照 Claude Code 会话，设计稿 Main / NewTask / BeforeEntry / Done / Narrow / Phone）：
//   新建    大输入框居中（占位「这次要处理哪些单据？」），下面选公司、月份（同对话页的下巴）。选了公司就建一个准备中的任务
//           （月份默认上个月；资料、开始前确认、执行的电脑、「开始」在右侧栏）；不必先说一句话。
//   任务    你说的话一个气泡 + 一行「时刻 · 电脑 · N 张」+ 每张单一行（单号 · 对方、状态、Express 单号、金额和税是否一致）；
//           做完最后一行「完成 {n} 张，{m} 张需要处理 · 用时 {t}」。底部同一个输入框：打的字交给这个任务的对话（对话框甲，
//           task-thread.tsx：回复画在单据行下面；录到中途要人选的单在这里出选项卡片），放的资料进资料区（准备中下巴里还能改公司、月份）。
//   顶栏    任务标题；「停止」（这台电脑上进行中）。「开始」在右侧栏报告下面（panel.tsx）。主栏顶栏止于右侧栏的分隔线；右侧栏开着
//           （并排）时开关在它右上角，收着、窄屏、手机时在主栏顶栏右端（同 Claude）。
// 输入框就是对话页那一个（thread.tsx 的 Composer：聚焦、长高、附件卡片、发送、手机胶囊都一样），套在实操自己的运行时里
// （useOperatorComposer：附件按发送时交，同对话）。右侧栏并排时宽度可拖（左边的分隔条，split-handle.tsx，记在本机）。
// 不做步骤向导、统计卡片、实时画面、CC 光标、接管提示条。样式只用 globals.css 的 .cc-* 与 .cc-op-*。

import { useSidebar } from "@/components/ui/sidebar";
import {
  type AppendMessage,
  AssistantRuntimeProvider,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
  type ThreadMessageLike,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { Building2Icon, CheckIcon, ChevronDownIcon, CircleAlertIcon, CircleIcon, MinusIcon, PanelRightIcon } from "lucide-react";
import { createContext, type FC, type ReactNode, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Composer, ThreadMessages } from "@/components/assistant-ui/thread";
import { PeriodChip } from "@/components/period-chip";
import { operatorPanelSize, useStoredSize } from "@/components/split-handle";
import { DropdownMenu, menuOffset } from "@/components/ui/menu";
import { Marquee } from "@/components/ui/marquee";
import { co, dayLabel, lang, op, t } from "@/i18n";
import { lastMonth } from "@/lib/companies";
import { fixedPx } from "@/lib/css-var";
import { ACCEPT } from "@/lib/managed-agents/attachments";
import { SessionIdOverride } from "@/lib/managed-agents/runtime-provider";
import { type Operator, useOperator, useOperatorOrNull } from "./controller";
import { docRow, type DocTone, docTitle, finishLine, groupOf, isFinal, stoppedDocs, unknownDocs } from "./model";
import { OperatorPanel, type PanelMode, useHandleItems } from "./panel";
import { setDrawer, setPanel, useOperatorStore } from "./store";
import { titleOf } from "./operator-sidebar";
import { TaskChoices, TaskRunIndicator, type TaskThread, useTaskThread } from "./task-thread";
import type { CompanyOption } from "./types";
import { withReport } from "@/lib/caught";

// ── 右侧栏怎么放 ──────────────────────────────────────────────────────────────
/** 窄屏的分界（globals.css 的 --cc-op-narrow，用时读；回退同值）。 */
export const NARROW_PX = 900;

/** 窗口窄于 --cc-op-narrow：右侧栏改成从右边滑出的抽屉。 */
export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const check = () => setNarrow(window.innerWidth < fixedPx("--cc-op-narrow", NARROW_PX));
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  return narrow;
}

/** 右侧栏的样子与开没开：手机盖满屏、窄屏抽屉（点了才开）、宽屏并排（开关记在本机）。 */
export function usePanelPlace(): { mode: PanelMode; open: boolean; toggle: () => void; close: () => void } {
  const { isMobile } = useSidebar();
  const narrow = useNarrow();
  const docked = useOperatorStore((s) => s.panel);
  const drawer = useOperatorStore((s) => s.drawer);
  const mode: PanelMode = isMobile ? "phone" : narrow ? "floating" : "docked";
  const open = mode === "docked" ? docked : drawer;
  const set = (next: boolean) => (mode === "docked" ? setPanel(next) : setDrawer(next));
  return { mode, open, toggle: () => set(!open), close: () => set(false) };
}

/** 任务标题：改过的名字，否则公司 · 月份。 */
export function useTaskTitle(): string {
  const o = useOperator();
  if (o.task) return titleOf(o.task);
  if (o.snapshot) return titleOf({ title: "", company: o.snapshot.company, company_title: o.snapshot.company_title ?? "", period: o.snapshot.period ?? "" });
  return "";
}

// ── 顶栏 ────────────────────────────────────────────────────────────────────
export const OperatorTitle: FC = () => {
  const title = useTaskTitle();
  if (!title) return null;
  return (
    <h1 className="cc-op-title" data-slot="cc-op-title">
      <Marquee className="truncate">{title}</Marquee>
    </h1>
  );
};

/** 右侧栏并排开着（它从窗口顶到底一整列，开关与桌面窗口的窗口按钮在它右上角）。 */
export function useDockOpen(): boolean {
  const o = useOperatorOrNull();
  const panel = usePanelPlace();
  return !!o?.id && panel.mode === "docked" && panel.open;
}

/** 右侧栏开关（同对话页的「显示 / 隐藏右侧栏」）。 */
export const PanelToggle: FC = () => {
  const panel = usePanelPlace();
  return (
    <button
      type="button"
      data-slot="cc-panel-toggle"
      onClick={panel.toggle}
      aria-pressed={panel.open}
      title={t(panel.open ? "hidePanel" : "showPanel")}
      aria-label={t(panel.open ? "hidePanel" : "showPanel")}
      className="cc-bar-btn"
    >
      <PanelRightIcon />
    </button>
  );
};

/** 主栏顶栏右侧：停止（这台电脑上进行中）；右侧栏没有并排开着时还有右侧栏开关。「开始」在右侧栏报告下面。 */
export const OperatorTools: FC = () => {
  const o = useOperator();
  const docked = useDockOpen();
  if (!o.id) return null;
  const s = o.snapshot;
  const running = !!s && !s.remote && (s.status === "running" || s.status === "paused");
  return (
    <>
      {running && (
        <button type="button" className="cc-btn" data-slot="cc-op-stop" disabled={o.busy} onClick={o.stop}>
          {op.stop}
        </button>
      )}
      {!docked && <PanelToggle />}
    </>
  );
};

/** 右侧栏（外壳放在主栏那一列的右边，与它同高）：并排时最上面一行是 top（外壳给；没给时只有右侧栏开关），宽度可拖（记在本机）；
 *  窄屏抽屉、手机全屏盖在整列上。 */
export const OperatorDock: FC<{ top?: (toggle: ReactNode) => ReactNode }> = ({ top }) => {
  const o = useOperator();
  const panel = usePanelPlace();
  const title = useTaskTitle();
  const width = useStoredSize(operatorPanelSize.key, operatorPanelSize.cssVar, operatorPanelSize.unit);
  if (!o.id || !panel.open) return null;
  const bar = top ? top(<PanelToggle />) : (
    <div className="cc-topbar cc-op-panel-bar">
      <PanelToggle />
    </div>
  );
  return <OperatorPanel mode={panel.mode} title={title} onClose={panel.close} top={bar} width={width} />;
};

// ── 输入框（对话页那一个，套在实操自己的运行时里）────────────────────────────────────────────────────
/** 实操的输入框发出：说的那句话与附件（附件按发送时交，同对话）。 */
type Send = (text: string, files: readonly File[]) => void;
type ComposerTarget = { send: Send; canSend: boolean };
const ComposerTargetContext = createContext<{ current: ComposerTarget }>({ current: { send: () => undefined, canSend: false } });

/** 附件只留在本页（文件对象），发送时交给实操（站点的资料上传）。 */
function localAttachments(files: Map<string, File>): AttachmentAdapter {
  return {
    accept: ACCEPT,
    async add({ file }): Promise<PendingAttachment> {
      const id = `op-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      files.set(id, file);
      const type = file.type.startsWith("image/") ? ("image" as const) : ("document" as const);
      return { id, type, name: file.name, contentType: file.type, file, status: { type: "requires-action", reason: "composer-send" } };
    },
    async remove(attachment) {
      files.delete(attachment.id);
    },
    async send(attachment): Promise<CompleteAttachment> {
      return { ...attachment, status: { type: "complete" }, content: [] };
    },
  };
}

/** 实操主栏的运行时（新建页与任务页共用一份：选了公司换到任务页时输入框里的字、附件还在）。消息是这个任务的对话（新建页没有）。 */
const OperatorComposerScope: FC<{ canSend: boolean; thread: TaskThread; children: ReactNode }> = ({ canSend, thread, children }) => {
  const target = useContext(ComposerTargetContext);
  const files = useMemo(() => new Map<string, File>(), []);
  const adapter = useMemo(() => localAttachments(files), [files]);
  // 消息同对话页（runtime-provider.tsx）：会话控制器折好的是 ThreadMessageLike，原样交给组件库转换
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: thread.snapshot.messages as readonly ThreadMessageLike[],
    convertMessage: (m) => m,
    isRunning: thread.running,
    isSendDisabled: !canSend,
    onNew: async (message: AppendMessage) => {
      const text = message.content.map((part) => (part.type === "text" ? part.text : "")).join("\n").trim();
      const picked = (message.attachments ?? []).flatMap((a) => {
        const file = files.get(a.id) ?? a.file;
        files.delete(a.id);
        return file ? [file] : [];
      });
      target.current.send(text, picked);
    },
    adapters: { attachments: adapter },
  });
  // 回复下面的工作状态行认这段对话（不是组件库当前打开的那段，那是对话页的）
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <SessionIdOverride.Provider value={thread.sessionId}>{children}</SessionIdOverride.Provider>
    </AssistantRuntimeProvider>
  );
};

/** 输入框（对话页那一个）：占位换成实操的问句，不写对话的发送草稿；chin 是实操自己的下巴。 */
const OperatorComposer: FC<{ chin?: FC }> = ({ chin }) => <Composer placeholder={op.placeholder} draft={false} chin={chin} />;

// ── 主栏 ────────────────────────────────────────────────────────────────────
export const OperatorMain: FC = () => {
  const o = useOperator();
  const panel = usePanelPlace();
  // Ctrl / ⌘ + N 新建任务在外壳里按模式接（assistant.tsx 的 useModeKeys：看着公司、待处理页时也要能新建）
  // 新建页：选了公司才发得出（一选就建任务、换到任务页）；任务页：一直能发
  const target = useRef<ComposerTarget>({ send: () => undefined, canSend: false });
  const thread = useTaskThread(o.source, o.id ? o.task : null);
  target.current = { send: sender(o, thread), canSend: !!o.id };
  return (
    <ComposerTargetContext.Provider value={target}>
      <OperatorComposerScope canSend={!!o.id} thread={thread}>
        <div className="cc-op-work" data-slot="cc-op-work">
          <div className="cc-op-main">{o.id ? <TaskLog chatBusy={thread.running} onAttention={panel.mode === "docked" ? undefined : panel.toggle} /> : <NewTask />}</div>
        </div>
      </OperatorComposerScope>
    </ComposerTargetContext.Provider>
  );
};

/** 输入框发出交给谁：打的字 = 这个任务的对话（对话框甲）；资料 = 资料区（准备中照准备的那一套，开始以后放进这家这个月）。 */
function sender(o: Operator, thread: TaskThread): Send {
  return (text, files) => {
    if (files.length) {
      if (o.draft) o.intake(files);
      else if (o.task) void o.source.intake(o.task.company, o.task.period, files).catch(withReport(undefined, "operator-intake"));
    }
    if (text) void thread.send(text).catch(withReport(undefined, "operator-thread-send"));
  };
}

/** 新建页的下巴：选公司、月份（先选月份也行，默认上个月）。 */
const NewTaskContext = createContext<{ period: string; setPeriod: (period: string) => void; pick: (company: CompanyOption) => void }>({
  period: "",
  setPeriod: () => undefined,
  pick: () => undefined,
});

const NewChin: FC = () => {
  const { period, setPeriod, pick } = useContext(NewTaskContext);
  return (
    <>
      <CompanyChip value={null} onChange={pick} />
      <PeriodChip look="cc-chin-btn" side="bottom" value={period} onChange={setPeriod} allowNone={false} />
    </>
  );
};

/** 新建：大输入框居中，下面选公司、月份。选了公司就建一个准备中的任务（执行的电脑在右侧栏「电脑与 Express」里选）；输入框里
 *  打的字、放的资料留着，到了任务页再发。 */
const NewTask: FC = () => {
  const o = useOperator();
  const [period, setPeriod] = useState(lastMonth());
  const create = o.create;
  const chin = useMemo(
    () => ({ period, setPeriod, pick: (company: CompanyOption) => create({ company: company.id, company_title: company.title, period }) }),
    [period, create],
  );
  return (
    <div className="cc-op-new" data-slot="cc-op-new">
      {o.error && <div role="alert" className="cc-alert">{o.error}</div>}
      <NewTaskContext.Provider value={chin}>
        <OperatorComposer chin={NewChin} />
      </NewTaskContext.Provider>
    </div>
  );
};

/** 准备中的任务：下巴里还能改公司、月份（交单后就不能改了）。 */
const DraftChin: FC = () => {
  const o = useOperator();
  if (!o.draft) return null;
  const d = o.draft;
  return (
    <>
      <CompanyChip value={{ id: d.company, title: d.company_title || d.company }} onChange={(c) => c.id !== d.company && o.retarget({ company: c.id, company_title: c.title })} />
      <PeriodChip look="cc-chin-btn" side="bottom" value={d.period} onChange={(p) => p && p !== d.period && o.retarget({ period: p })} allowNone={false} />
    </>
  );
};

const ROW_ICON: Record<DocTone, FC<{ className?: string }>> = {
  done: CheckIcon,
  running: CircleIcon,
  waiting: CircleIcon,
  attention: CircleAlertIcon,
  skipped: MinusIcon,
};

type LogRow = { key: string; tone: DocTone; title: string; detail: string; state: string };

const Row: FC<{ row: LogRow }> = ({ row }) => {
  const Icon = ROW_ICON[row.tone];
  return (
    <div className="cc-op-row" data-tone={row.tone} data-slot="cc-op-row">
      <Icon className="cc-op-row-icon" aria-hidden />
      <span className="flex min-w-0 flex-col">
        <Marquee className="truncate text-ink">{row.title}</Marquee>
        {row.detail && <Marquee className="truncate text-ink-3 text-caption">{row.detail}</Marquee>}
      </span>
      <span className="cc-op-row-state">{row.state}</span>
    </div>
  );
};

/** 「14:25」「昨天 14:25」。 */
const when = (seconds: number) => {
  if (!seconds) return "";
  const at = new Date(seconds * 1000);
  const time = new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(at);
  const day = dayLabel(at);
  return day === t("today") ? time : `${day} ${time}`;
};

/** 任务的记录：你说的话、一行概况、每张单一行、完成行、对话与工作状态行（状态只在这里说，task-thread.tsx）；底部输入框。 */
const TaskLog: FC<{ chatBusy: boolean; onAttention?: () => void }> = ({ chatBusy, onAttention }) => {
  const o = useOperator();
  const prompt = useOperatorStore((s) => s.preps[o.id]?.prompt ?? "");
  const handle = useHandleItems();
  const end = useRef<HTMLDivElement>(null);
  let rows: LogRow[] = [];
  let meta = "";
  let finish = "";
  if (o.draft) {
    const docs = (o.data.docs ?? []).filter((doc) => groupOf(doc) !== "skip");
    const prechecks = o.data.prechecks ?? [];
    const stopped = new Map([...stoppedDocs(prechecks), ...unknownDocs(prechecks).map((row) => ({ doc_id: row.doc_id, reason: row.message }))].map((row) => [row.doc_id, row.reason]));
    rows = docs.map((doc) => {
      const skipped = o.draft!.skipped.includes(doc.id);
      const reason = stopped.get(doc.id);
      return {
        key: doc.id,
        title: docTitle(doc, doc.id),
        tone: skipped ? "skipped" : reason ? "attention" : "waiting",
        detail: skipped ? "" : (reason ?? ""),
        state: skipped ? op.doc.skipped! : reason ? op.doc.notDone! : op.doc.waiting!,
      };
    });
    meta = [when(o.draft.created), o.computer?.name, o.data.docs ? op.docCount(docs.length) : "", op.state.preparing].filter(Boolean).join(" · ");
  } else if (o.snapshot) {
    const s = o.snapshot;
    rows = s.docs.map((doc) => ({ key: doc.doc_id, title: docTitle(doc.summary ?? {}, doc.doc_id), ...docRow(doc) }));
    meta = [when(s.created ?? 0), s.remote ? "" : op.thisComputer, op.docCount(s.total)].filter(Boolean).join(" · ");
    if (isFinal(s.status)) finish = finishLine(s);
  }
  // 进度往下走：跟到最后一行（同对话里回复往下长）
  useLayoutEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [rows.length, finish]);
  const confirm = o.draft ? o.questions.filter((q) => !o.draft!.answers[q.key]).length + unknownDocs(o.data.prechecks ?? [], o.draft.skipped).length : 0;
  // 交出去以后停在「未完成」的单（站点任务记录）：其中要人选的在这里出选项卡片（TaskChoices 自己判哪些要选）
  const waiting = o.draft ? [] : (o.task?.documents ?? []).filter((d) => d.state === "not_done").map((d) => d.doc_id);
  const attention = confirm + handle.length;
  return (
    <>
      <div className="cc-op-log" data-slot="cc-scroll">
        <div className="cc-op-log-inner" data-slot="cc-op-log">
          {o.error && <div role="alert" className="cc-alert">{o.error}</div>}
          {prompt && (
            <div className="cc-op-you">
              <div className="cc-bubble-user">{prompt}</div>
            </div>
          )}
          {meta && <div className="cc-op-meta">{meta}</div>}
          {rows.map((row) => (
            <Row key={row.key} row={row} />
          ))}
          {finish && <div className="cc-op-finish" data-slot="cc-op-finish">{finish}</div>}
          {waiting.length > 0 && <TaskChoices waiting={waiting} />}
          <ThreadMessages />
          <TaskRunIndicator chatBusy={chatBusy} />
          {onAttention && attention > 0 && (
            <button type="button" className="cc-op-attention" data-slot="cc-op-attention" onClick={onAttention}>
              <Marquee className="min-w-0 flex-1 truncate">{op.state.attention}</Marquee>
              <span className="cc-op-section-summary">{attention}</span>
            </button>
          )}
          <div ref={end} />
        </div>
      </div>
      <div className="cc-op-foot">
        <OperatorComposer chin={o.draft ? DraftChin : undefined} />
      </div>
    </>
  );
};

/** 选公司（同对话页下巴的公司条）：输入就搜，服务端分页取前 20 家。 */
const CompanyChip: FC<{ value: CompanyOption | null; onChange: (company: CompanyOption) => void }> = ({ value, onChange }) => {
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
