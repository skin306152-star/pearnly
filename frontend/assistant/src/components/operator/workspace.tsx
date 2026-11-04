// 「实操」工作区的主栏与顶栏（照 Claude Code 会话；用户 11-04：「代录右侧不要」，原右栏五块并进这一条会话，conversation.tsx）：
//   新建    大输入框居中（占位「这次要处理哪些单据？」），下面选公司、月份（同对话页的下巴）。选了公司就建一个准备中的任务
//           （月份默认上个月）；不必先说一句话。
//   任务    一条会话，从上到下：新建时带的那句话（气泡）→ 这个任务的对话（对话框甲，task-thread.tsx），上传卡片按上传那一刻插在
//           消息之间（uploads.ts，W2 的 UploadCard）→ 准备中：资料步骤行、「开始前」框；开始以后：一行概况、每张单一行（要人处理的
//           行下就地有按钮）、完成行、要中途选的单的选项卡片、报告卡片 → 工作状态行（同对话的 RunLine）、出错框（同对话的 .cc-alert）。
//           底部同一个输入框：打的字交给这个任务的对话；放的资料（「＋」、拖进输入框、拖进会话任何地方）一份一张上传卡片；批次在这台
//           电脑上录着时发送键换成 ■（同对话），Esc 同效。输入框下面那一行：公司、月份（准备中可改）、电脑、这次写入的账套。
//   顶栏    任务标题。
// 输入框就是对话页那一个（thread.tsx 的 Composer：聚焦、长高、附件卡片、发送、停止、手机胶囊都一样），套在实操自己的运行时里
// （附件按发送时交，同对话）。不做步骤向导、统计卡片、实时画面、CC 光标、接管提示条。样式只用 globals.css 的 .cc-* 与 .cc-op-*。

import {
  type AppendMessage,
  AssistantRuntimeProvider,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
  type ThreadMessageLike,
  useAui,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { createContext, type DragEvent, type FC, type ReactNode, useContext, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Composer, ThreadMessages } from "@/components/assistant-ui/thread";
import { PeriodChip } from "@/components/period-chip";
import { UploadCard } from "@/components/run/upload-card";
import { Marquee } from "@/components/ui/marquee";
import { dayLabel, lang, op, t } from "@/i18n";
import { lastMonth } from "@/lib/companies";
import { ACCEPT } from "@/lib/managed-agents/attachments";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { SessionIdOverride } from "@/lib/managed-agents/runtime-provider";
import { withReport } from "@/lib/caught";
import { type Operator, useOperator } from "./controller";
import { BeforeBox, CompanyChip, SourcesStep, TaskChin, TaskDocs } from "./conversation";
import { groupOf } from "./model";
import { titleOf } from "./operator-sidebar";
import { useOperatorStore } from "./store";
import { TaskChoices, TaskRunIndicator, type TaskThread, useTaskThread } from "./task-thread";
import type { CompanyOption } from "./types";
import { retryUpload, type UploadItem, useTaskUploads } from "./uploads";

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

// ── 输入框（对话页那一个，套在实操自己的运行时里）────────────────────────────────────────────────────
/** 实操的输入框发出：说的那句话与附件（附件按发送时交，同对话）。 */
type Send = (text: string, files: readonly File[]) => void;
type ComposerTarget = { send: Send; canSend: boolean };
const ComposerTargetContext = createContext<{ current: ComposerTarget }>({ current: { send: () => undefined, canSend: false } });

/** 附件只留在本页（文件对象），发送时交给实操（一份一张上传卡片）。 */
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

/** 输入框（对话页那一个）：占位换成实操的问句，不写对话的发送草稿；chin 是实操自己的下巴；stop：批次或这段对话在跑时的 ■。 */
const OperatorComposer: FC<{ chin?: FC; stop?: { running: boolean; onStop: () => void } }> = ({ chin, stop }) => (
  <Composer placeholder={op.placeholder} draft={false} chin={chin} stop={stop} />
);

/** 拖进会话任何地方的文件进输入框（同直写：整窗都接，lib/desktop.ts；这里先收下，免得进了对话页的输入框）。 */
const DropArea: FC<{ className: string; children: ReactNode }> = ({ className, children }) => {
  const aui = useAui();
  const over = (e: DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) e.preventDefault();
  };
  const drop = (e: DragEvent) => {
    if (e.defaultPrevented || !e.dataTransfer.files.length) return;
    e.preventDefault();
    for (const file of Array.from(e.dataTransfer.files)) void aui.thread().composer().addAttachment(file).catch(withReport(undefined, "operator-drop"));
  };
  return (
    <div className={className} data-slot="cc-op-work" onDragOver={over} onDrop={drop}>
      {children}
    </div>
  );
};

// ── 主栏 ────────────────────────────────────────────────────────────────────
export const OperatorMain: FC = () => {
  const o = useOperator();
  // Ctrl / ⌘ + N 新建任务在外壳里按模式接（assistant.tsx 的 useModeKeys：看着公司页时也要能新建）
  // 新建页：选了公司才发得出（一选就建任务、换到任务页）；任务页：一直能发
  const target = useRef<ComposerTarget>({ send: () => undefined, canSend: false });
  const thread = useTaskThread(o.source, o.id ? o.task : null);
  target.current = { send: sender(o, thread), canSend: !!o.id };
  return (
    <ComposerTargetContext.Provider value={target}>
      <OperatorComposerScope canSend={!!o.id} thread={thread}>
        <DropArea className="cc-op-work">
          <div className="cc-op-main">{o.id ? <TaskLog thread={thread} /> : <NewTask />}</div>
        </DropArea>
      </OperatorComposerScope>
    </ComposerTargetContext.Provider>
  );
};

/** 输入框发出交给谁：打的字 = 这个任务的对话（对话框甲，准备中也交）；资料 = 一份一张上传卡片，进这家这个月。 */
function sender(o: Operator, thread: TaskThread): Send {
  return (text, files) => {
    if (files.length) o.intake(files);
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

/** 新建：大输入框居中，下面选公司、月份。选了公司就建一个准备中的任务；输入框里打的字、放的资料留着，到了任务页再发。 */
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

/** 「14:25」「昨天 14:25」。 */
const when = (seconds: number) => {
  if (!seconds) return "";
  const at = new Date(seconds * 1000);
  const time = new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(at);
  const day = dayLabel(at);
  return day === t("today") ? time : `${day} ${time}`;
};

/** 消息之间插上传卡片：每条消息前画上一条消息之后、这条之前传的；最后一条之后传的画在消息后面。 */
function uploadSlots(uploads: readonly UploadItem[], messages: readonly ThreadMessageLike[]) {
  const times = messages.map((m) => (m.createdAt instanceof Date ? m.createdAt.getTime() : 0));
  const index = new Map(messages.map((m, i) => [String(m.id ?? ""), i]));
  const before = (message: { id: string; createdAt?: Date }) => {
    const at = index.get(message.id);
    if (at === undefined) return [];
    const from = at > 0 ? times[at - 1]! : -Infinity;
    const until = times[at]!;
    return uploads.filter((u) => u.at > from && u.at <= until);
  };
  const last = times.length ? times[times.length - 1]! : -Infinity;
  return { before, after: uploads.filter((u) => u.at > last) };
}

const Uploads: FC<{ items: readonly UploadItem[] }> = ({ items }) => {
  const o = useOperator();
  if (!items.length) return null;
  return (
    <div className="cc-op-uploads" data-slot="cc-op-uploads">
      {items.map((item) => (
        <UploadCard
          key={item.key}
          name={item.name}
          size={item.size}
          state={item.state}
          {...(item.local && item.state.kind === "failed" && item.state.stage === "upload" ? { onRetry: () => retryUpload(o.source, item.local!) } : {})}
        />
      ))}
    </div>
  );
};

/** 任务的会话（文件头）。 */
const TaskLog: FC<{ thread: TaskThread }> = ({ thread }) => {
  const o = useOperator();
  const prompt = useOperatorStore((s) => s.preps[o.id]?.prompt ?? "");
  const end = useRef<HTMLDivElement>(null);
  const at = o.draft ?? o.task;
  const uploads = useTaskUploads(o.source, at ? { name: o.id, period: at.period } : null, o.refresh);
  const messages = thread.snapshot.messages as readonly ThreadMessageLike[];
  const slots = uploadSlots(uploads, messages);
  let meta = "";
  if (o.snapshot && !o.draft) {
    const s = o.snapshot;
    meta = [when(s.created ?? 0), s.remote ? "" : op.thisComputer, op.docCount(s.total)].filter(Boolean).join(" · ");
  } else if (o.draft) {
    const docs = (o.data.docs ?? []).filter((doc) => groupOf(doc) !== "skip");
    meta = [when(o.draft.created), o.data.docs ? op.docCount(docs.length) : "", op.state.preparing].filter(Boolean).join(" · ");
  }
  const rows = o.snapshot?.docs.length ?? 0;
  // 进度往下走：跟到最后一行（同对话里回复往下长）
  useLayoutEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [rows, uploads.length, o.snapshot?.status]);
  // 交出去以后停在「未完成」的单（站点任务记录）：其中要人选的在这里出选项卡片（TaskChoices 自己判哪些要选）
  const waiting = o.draft ? [] : (o.task?.documents ?? []).filter((d) => d.state === "not_done").map((d) => d.doc_id);
  const s = o.snapshot;
  const batchRunning = !o.draft && !!s && !s.remote && (s.status === "running" || s.status === "paused");
  // 这段对话在跑时 ■ 停的是这一轮；批次在这台电脑上录着时停这一批（同对话的停止键与 Esc）
  const stop = thread.running
    ? { running: true, onStop: () => void getSessionController(thread.sessionId).interrupt().catch(withReport(undefined, "operator-thread-stop")) }
    : { running: batchRunning, onStop: o.stop };
  return (
    <>
      <div className="cc-op-log" data-slot="cc-scroll">
        <div className="cc-op-log-inner" data-slot="cc-op-log">
          {prompt && (
            <div className="cc-op-you">
              <div className="cc-bubble-user">{prompt}</div>
            </div>
          )}
          <ThreadMessages before={(message) => <Uploads items={slots.before(message)} />} />
          <Uploads items={slots.after} />
          {o.draft && meta && <div className="cc-op-meta">{meta}</div>}
          <SourcesStep />
          <BeforeBox />
          {!o.draft && <TaskDocs meta={meta} />}
          {waiting.length > 0 && <TaskChoices waiting={waiting} />}
          <TaskRunIndicator chatBusy={thread.running} />
          {o.error && <div role="alert" className="cc-alert" data-slot="cc-op-error">{o.error}</div>}
          <div ref={end} />
        </div>
      </div>
      <div className="cc-op-foot">
        <OperatorComposer chin={TaskChin} stop={stop} />
      </div>
    </>
  );
};
