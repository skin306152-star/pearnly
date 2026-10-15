
import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/attachment";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { FileTypeIcon, fileTypeLabel, useResults } from "@/components/results-panel";
import { runningStepLabel, StepRow, StepsGroup } from "@/components/tool-uis/steps";
import { TypingDots } from "@/components/typing-dots";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { usePush } from "@/lib/push";
import { touchOnly } from "@/lib/touch";
import { dayLabel, lang, st, steps, t } from "@/i18n";
import {
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  ComposerPrimitive,
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartComponent,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  BellIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  SquareIcon,
} from "lucide-react";
import { fileUrl } from "@/lib/managed-agents/api";
import { clearPending, lastSendAt, pendingLanded, setDraft, takeHandoff, usePendingSend } from "@/lib/managed-agents/pending-send";
import { useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import { statusText, ThinkingClock, turnActivity, turnStartedAt } from "@/lib/managed-agents/turn";
import { StreamFollower } from "@/lib/follow-scroll";
import { holdImeEnter } from "@/lib/ime";
import { useToolLabels } from "@/lib/tool-labels";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type FC,
  type PropsWithChildren,
} from "react";

// 触屏设备（手机、平板，lib/touch.ts）：打开页面不自动聚焦输入框
const TOUCH = touchOnly();

export type ThreadGroupPart = MessagePrimitive.GroupedParts.GroupPart;

/**
 * Optional component overrides for the thread. `AssistantMessage` and
 * `Welcome` replace whole sections; the remaining slots override how the
 * assistant message renders tool calls and part groups. Tool UIs registered
 * by name (toolkit `render`, `useAssistantDataUI`) take precedence over
 * `ToolFallback`.
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  /** 打开已有对话、消息还没取回时居中显示（同 Claude：形象 + 「正在加载消息」） */
  Loading?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
  /** 输入框下面的「下巴」（设计文档 3.12）：产品自己的按钮（Cowork 的公司、月份、分店）；不给或给了但没内容时不占位 */
  Chin?: ComponentType | undefined;
};

export type ThreadProps = {
  components?: ThreadComponents | undefined;
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext =
  createContext<ThreadComponents>(EMPTY_COMPONENTS);

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);

export const Thread: FC<ThreadProps> = ({ components = EMPTY_COMPONENTS }) => {
  const pending = usePendingSend();
  const isEmpty = useAuiState(isNewChatView) && !pending;
  const isLoading =
    useAuiState((s) => s.thread.messages.length === 0 && s.thread.isLoading && !s.threads.isLoading) && !pending;

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot isEmpty={isEmpty} isLoading={isLoading} />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{ isEmpty: boolean; isLoading: boolean }> = ({ isEmpty, isLoading }) => {
  const { Welcome = ThreadWelcome, Loading } = useContext(ThreadComponentsContext);
  const follow = useFollowStream();

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-app @container flex h-full flex-col font-sans text-ink"
      style={{
        ["--thread-max-width" as string]: "var(--cc-thread-w)",
      }}
    >
      <ThreadPrimitive.Viewport
        ref={follow.viewport}
        turnAnchor="top"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-auto [scrollbar-gutter:stable] scroll-smooth"
      >
        <div
          ref={follow.content}
          className={cn(
            // 栏宽 768（设计文档 3.10）：内容区左右各留 1rem，正文与输入框正好 --thread-max-width
            "mx-auto flex w-full max-w-[calc(var(--thread-max-width)+2rem)] flex-1 flex-col px-4 pt-4",
            (isEmpty || isLoading) && "justify-center",
          )}
        >
          {isLoading && Loading && <Loading />}
          {isEmpty && (
            // 手机上问候在屏幕中间、输入框贴底（同 Claude 手机端）；电脑上两者一起居中
            <div className="max-md:flex max-md:flex-1 max-md:items-center max-md:justify-center">
              <AuiIf condition={isNewChatView}>
                <Welcome />
              </AuiIf>
            </div>
          )}

          <div
            data-slot="aui_message-group"
            className="mb-14 flex flex-col gap-y-6 empty:hidden"
          >
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
            <PendingSendBubble />
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer flex flex-col gap-4 overflow-visible bg-linear-to-b from-transparent via-app/85 to-app pt-4 pb-4 md:pb-6",
              !isEmpty &&
                "sticky bottom-0 mt-auto rounded-t-xl",
            )}
          >
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            <Composer />
            <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

/** 按下发送到真正的气泡画出来之前，先显示发送中的用户气泡（附件名旁一块骨架在闪，不用转圈：ccs 卡片规矩；lib/managed-agents/pending-send.ts）。
 *  对话里画出了接替它的那一条才撤（同一次提交里换过去），中间不会两个都不在。 */
const PendingSendBubble: FC = () => {
  const pending = useShownPending();
  if (!pending) return null;
  return (
    <div data-role="user" className="grid grid-cols-[minmax(20%,1fr)_auto] content-start gap-y-2 px-2 [&>*]:col-start-2">
      <div className="flex flex-wrap justify-end gap-2">
        {pending.files.map((name, i) => (
          <span key={i} className="cc-card flex items-center gap-1.5 px-2.5 py-1.5 text-sm">
            <Skeleton aria-hidden className="size-3.5 shrink-0 rounded-xs bg-subtle" />
            {name}
          </span>
        ))}
      </div>
      {pending.text && (
        <div className="cc-bubble-user" data-enter="" data-sending="">{pending.text}</div>
      )}
      <RunIndicator className="col-span-full col-start-1 pt-4" />
    </div>
  );
};

/** 还在显示的临时气泡（对话里还没画出接替它的那一条）；画出来了就撤掉。 */
function useShownPending() {
  const pending = usePendingSend();
  const landed = useAuiState((s) => pendingLanded(pending, s.thread.messages.map((m) => m.id)));
  useEffect(() => {
    if (landed) clearPending();
  }, [landed]);
  return landed ? null : pending;
}

/** 回复在出时跟到底、用户往上翻就不动（lib/follow-scroll.ts）。新的一轮（又发了一条）从跟随开始。 */
function useFollowStream() {
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null);
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  const live = useRef(false);
  live.current = useAuiState((s) => s.thread.isRunning);
  const turn = useAuiState((s) => {
    for (let i = s.thread.messages.length - 1; i >= 0; i--) if (s.thread.messages[i]!.role === "user") return s.thread.messages[i]!.id;
    return undefined;
  });
  // 打开的是哪段对话（消息取回来了才算打开）：换了一段、或这段的消息刚取回，就停到最新一条（follow-scroll 的 settle）
  const opened = useAuiState((s) => (s.thread.messages.length > 0 ? s.threadListItem.id : ""));
  const openedNow = useRef(opened);
  openedNow.current = opened;
  const follower = useRef<StreamFollower | null>(null);
  useEffect(() => {
    if (!viewport || !content) return;
    const created = new StreamFollower(viewport, content, () => live.current);
    follower.current = created;
    if (openedNow.current) created.settle();
    return () => {
      created.dispose();
      if (follower.current === created) follower.current = null;
    };
  }, [viewport, content]);
  useEffect(() => follower.current?.resume(), [turn]);
  useEffect(() => {
    if (opened) follower.current?.settle();
  }, [opened]);
  return { viewport: setViewport, content: setContent };
}

/** 发送那一刻组件库先调附件的 send、后清空输入框：随时记下输入框的文字，发送中的气泡才有字。 */
const DraftWatcher: FC = () => {
  const text = useAuiState((s) => s.composer.text);
  useEffect(() => setDraft(text), [text]);
  return null;
};

const ThreadMessage: FC = () => {
  const { AssistantMessage: AssistantMessageComponent = AssistantMessage } =
    useContext(ThreadComponentsContext);
  const role = useAuiState((s) => s.message.role);
  const notice = useAuiState((s) => isNoticeMessage(s.message));

  if (role === "user") return <UserMessage />;
  if (notice) return <NoticeMessage />;
  return <AssistantMessageComponent />;
};

// ── 提醒卡片 ───────────────────────────────────────────────────────────────────────────────────────

type MessageLike = { metadata?: { custom?: Record<string, unknown> } };

/** 本站提醒并进对话的那条（lib/managed-agents/reducer.ts 的 withNotices，system 消息）。 */
const isNoticeMessage = (m: MessageLike) => Boolean(m.metadata?.custom?.notice);

const noticeTime = (at: Date) =>
  `${dayLabel(at)} ${new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(at)}`;

/** 定时任务提醒、完成简报等（同结果文件卡的卡片样式）：铃铛、标题、正文、时间；没有复制栏、不加说明文字。
 *  这台浏览器还没开推送通知时卡片下方给「打开通知」：按提醒这类事件挂，不按哪个产品的工具。 */
const NoticeMessage: FC = () => {
  const title = useAuiState((s) => {
    const notice = s.message.metadata?.custom?.notice as { title?: unknown } | undefined;
    return typeof notice?.title === "string" ? notice.title : "";
  });
  const text = useAuiState((s) => {
    const part = s.message.content[0];
    return part?.type === "text" ? part.text : "";
  });
  const createdAt = useAuiState((s) => s.message.createdAt);
  const push = usePush();
  return (
    <MessagePrimitive.Root data-slot="cc-notice" data-role="notice" className="flex flex-col items-start gap-2 px-2">
      <div role="note" aria-label={t("notice")} className="cc-card flex w-full max-w-md gap-3 px-3 py-2.5 font-sans">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-sm bg-subtle text-ink-2">
          <BellIcon className="size-4.5" />
        </span>
        <div className="min-w-0 flex-1">
          {title && <div className="font-medium text-ink text-sm">{title}</div>}
          <div className="wrap-break-word whitespace-pre-line text-ink-2 text-sm">{text}</div>
          <div className="mt-1 text-ink-3 text-xs">{noticeTime(createdAt)}</div>
        </div>
      </div>
      {push.state === "off" && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button type="button" className="cc-btn cc-tap" onClick={() => void push.enable()}>
            {st.turnOnNotifications}
          </button>
          {push.failure && <span className="text-danger text-sm">{push.failure}</span>}
        </div>
      )}
    </MessagePrimitive.Root>
  );
};

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <TooltipIconButton
        tooltip={t("scrollToBottom")}
        variant="outline"
        className="aui-thread-scroll-to-bottom border-line bg-surface hover:bg-hover absolute -top-12 z-10 self-center rounded-full p-4 shadow-[var(--cc-shadow-composer)] disabled:invisible"
      >
        <ArrowDownIcon />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-6 flex flex-col items-center px-4 text-center">
      <h1 className="aui-thread-welcome-message-inner fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-2xl font-semibold duration-200">
        How can I help you today?
      </h1>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-wrap items-center justify-center gap-2 px-4">
      <ThreadPrimitive.Suggestions>
        {() => <ThreadSuggestionItem />}
      </ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <SuggestionPrimitive.Trigger send asChild>
        <Button
          variant="ghost"
          className="aui-thread-welcome-suggestion text-ink-2 hover:bg-hover border-line bg-surface h-auto gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-normal whitespace-nowrap transition-colors"
        >
          <SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1" />
          <SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 empty:hidden" />
        </Button>
      </SuggestionPrimitive.Trigger>
    </div>
  );
};

const Composer: FC = () => {
  const { Chin } = useContext(ThreadComponentsContext);
  // 新对话问「今天能帮你做什么」，对话里就是「回复…」（同 Claude）；打开已有对话、消息还在加载时也已经是「回复…」
  const hasMessages = useAuiState((s) => s.thread.messages.length > 0 || !!s.threadListItem.remoteId);
  const placeholder = hasMessages ? t("reply") : t("placeholder");
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="cc-composer cc-composer-inline"
        >
          <ComposerAttachments />
          <DraftWatcher />
          <ComposerPrimitive.Input
            placeholder={placeholder}
            className="aui-composer-input block max-h-72 min-h-6 w-full resize-none bg-transparent font-sans outline-none"
            rows={1}
            autoFocus={!TOUCH /* 手机上打开不弹键盘（同 Claude 手机 app），点输入框才弹 */}
            enterKeyHint="send"
            onKeyDown={holdImeEnter /* Safari 选字结束的回车（keyCode 229）不发送，lib/ime.ts */}
            aria-label={placeholder}
          />
          <ComposerAction />
        </div>
      </ComposerPrimitive.AttachmentDropzone>
      {Chin && (
        <div className="cc-composer-chin" data-slot="cc-composer-chin">
          <Chin />
        </div>
      )}
    </ComposerPrimitive.Root>
  );
};

const ComposerAction: FC = () => {
  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-between">
      <AuiIf condition={(s) => s.thread.capabilities.attachments}>
        <ComposerAddAttachment />
      </AuiIf>
      <div className="aui-composer-send-slot ms-auto flex items-center gap-1.5">
        <AuiIf condition={(s) => !s.thread.isRunning}>
          <ComposerPrimitive.Send asChild>
            <button type="button" className="aui-composer-send cc-send" aria-label={t("send")} title={t("send")}>
              <ArrowUpIcon className="aui-composer-send-icon" />
            </button>
          </ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={(s) => s.thread.isRunning}>
          <ComposerPrimitive.Cancel asChild>
            <button type="button" className="aui-composer-cancel cc-send" aria-label={t("stop")} title={t("stop")}>
              <SquareIcon className="aui-composer-cancel-icon !size-3.5 fill-current" />
            </button>
          </ComposerPrimitive.Cancel>
        </AuiIf>
      </div>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-danger-line bg-danger-soft text-danger mt-2 rounded-md border p-3 text-sm">
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

// 思考与工具调用连续的一段收成一组「步骤」（同 claude.ai）。停下来等批准的那一步不收进组：
// 收在折叠里的「允许 / 拒绝」没人看得见，会话就卡住了。
const groupByBase = groupPartByType({
  reasoning: ["group-chainOfThought"],
  "tool-call": ["group-chainOfThought"],
  "standalone-tool-call": [],
});
const groupByWithVisibleGates: typeof groupByBase = (part, context) => {
  if (part.type === "tool-call" && part.approval && part.approval.approved === undefined) {
    return [];
  }
  return groupByBase(part, context);
};

const AssistantMessage: FC = () => {
  const { ToolFallback: ToolFallbackComponent = StepRow } = useContext(ThreadComponentsContext);

  const ACTION_BAR_PT = "pt-1.5";
  // Keep the action bar inside the contained root's paint box, then cancel its reserved space in flow.
  const ACTION_BAR_HEIGHT = `min-h-7.5 ${ACTION_BAR_PT}`;

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="group/message relative -mb-7.5 pb-7.5 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="cc-reply px-2 wrap-break-word"
      >
        {/* 「在做什么」统一由对话底部的工作状态行说（RunIndicator），回复里不再另补占位 */}
        <MessagePrimitive.GroupedParts groupBy={groupByWithVisibleGates} indicator="never">
          {({ part, children }) => {
            switch (part.type) {
              case "group-chainOfThought":
                return <StepsGroup group={part}>{children}</StepsGroup>;
              case "text":
                return <MarkdownText />;
              case "reasoning":
                return null; // 引擎不给思考内容；进行中由步骤标题显示「正在思考…」
              case "tool-call":
                return part.toolUI ?? <ToolFallbackComponent {...part} />;
              case "data":
                return part.dataRendererUI;
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessageError />
        <MessageFiles />
      </div>
      {/* 工作状态行挂在这一轮的回复里（还没有回复时是组件库补的占位）：组件库把这一轮推到顶上时在回复后面留空白，
          放在回复外面就被挤到屏幕最下面去了 */}
      <LastReplyIndicator />

      <div
        data-slot="aui_assistant-message-footer"
        className={cn("ms-2 flex items-center", ACTION_BAR_HEIGHT)}
      >
        <AssistantActionBar />
      </div>
    </MessagePrimitive.Root>
  );
};

/** 最后一条回复下面的工作状态行（不是最后一条的回复已是历史）。 */
const LastReplyIndicator: FC = () => {
  const isLast = useAuiState((s) => s.message.isLast);
  return isLast ? <LastReplyRunIndicator /> : null;
};

const LastReplyRunIndicator: FC = () => {
  // 又发了一条、临时气泡还在：状态行在临时气泡下面，上一轮的回复下面不再放一个
  const sending = !!useShownPending();
  return sending ? null : <RunIndicator className="px-2 pt-2" />;
};

// 「思考了几秒」跨组件记：发送中气泡下的、组件库占位里的、正式回复里的是同一行，换位置时不归零
const thinking = new ThinkingClock();

/** 工作状态行（同 Claude，lib/managed-agents/turn.ts）：发出即出现在用户消息下面，橘色小点在动，旁边「计时 · 在做什么」，
 *  一直在，直到这一轮真正结束；等用户点「允许」时收起，点完再出来。计时从按下发送那一刻起本地每秒走；
 *  想完开始出正文时说「思考了 M秒」；很久收不到服务端消息时改说「连接中断，正在重连」。 */
const RunIndicator: FC<{ className?: string }> = ({ className }) => {
  const pending = usePendingSend();
  const snapshot = useActiveSessionSnapshot();
  const labels = useToolLabels();
  const sending = !!pending; // 临时气泡还在（正式气泡还没画出来）：这一轮刚开始
  const activity = turnActivity(snapshot, sending);
  const visible = activity.kind !== "hidden";
  const now = useClock(visible);
  const start = turnStartedAt(snapshot.messages, lastSendAt(), sending, now) ?? now;
  const thought = thinking.note(activity.kind, start, Date.now());
  if (!visible) return null;
  const step = activity.kind === "tool" ? runningStepLabel(activity.toolName, activity.args, labels) : "";
  const text = statusText(activity, Math.floor((now - start) / 1000), thought, step);
  // 读屏只报在做什么，不每秒报一次计时
  const label = activity.kind === "tool" ? step : activity.kind === "reconnecting" ? text : steps.thinking;
  return (
    <div data-slot="cc-run-indicator" data-kind={activity.kind} className={cn("flex min-w-0 items-center font-sans", className)}>
      <TypingDots label={label} showLabel={false} />
      <span className="typing-dots-label ms-2.5 min-w-0 truncate" aria-hidden="true">{text}</span>
    </div>
  );
};

/** 显示时每秒走一次的本页时钟。 */
function useClock(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  return running ? now : Date.now();
}

/** 这条回复期间产出的文件：显示成卡片，点一下在右侧打开（同 claude.ai）。按文件时间落在本条与下一条消息之间判定；
 *  「下一条」跳过提醒卡片（它可能排在一轮回复后面，但时间更早）。 */
const MessageFiles: FC = () => {
  const { sessionId, files, openFile } = useResults();
  const id = useAuiState((s) => s.message.id);
  const messages = useAuiState((s) => s.thread.messages);
  const mine = useMemo(() => {
    const index = messages.findIndex((m) => m.id === id);
    if (index < 0) return [];
    const from = messages[index]!.createdAt.getTime();
    const next = messages.slice(index + 1).find((m) => !isNoticeMessage(m));
    const until = next?.createdAt.getTime() ?? Number.POSITIVE_INFINITY;
    return files.filter((f) => {
      const at = new Date(f.created_at).getTime();
      return at >= from && at < until;
    });
  }, [files, messages, id]);
  if (!sessionId || !mine.length) return null;
  return (
    <div className="flex flex-col gap-2">
      {mine.map((file) => (
        <div
          key={file.id}
          className="cc-card cc-card-hover flex max-w-md items-center gap-3 px-3 py-2.5 font-sans"
        >
          <button type="button" onClick={() => openFile(file.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-sm bg-subtle text-ink-2">
              <FileTypeIcon filename={file.filename} className="size-4.5" />
            </span>
            <span className="min-w-0">
              <span className="block truncate font-medium text-ink text-sm">{file.filename}</span>
              <span className="block text-ink-3 text-xs">{fileTypeLabel(file.filename)}</span>
            </span>
          </button>
          <a
            href={fileUrl(sessionId, file.id)}
            title={t("download")}
            aria-label={t("download")}
            className="cc-icon-btn cc-tap"
          >
            <DownloadIcon className="size-4" />
          </a>
        </div>
      ))}
    </div>
  );
};

// 回复下的操作（同 Claude）：只放引擎真能做到的「复制」——会话记录改不了，重试 / 编辑做不到原样替换，就不摆个点了没反应的按钮。
// 电脑上悬停这条回复时淡入（设计文档 3.10，.cc-reply-actions）；手机、平板没有悬停，每条都显示（同 Claude 手机端）。

const AssistantActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="never"
      className="aui-assistant-action-bar-root cc-reply-actions -ms-1 flex gap-0.5 text-ink-3"
    >
      <CopyAction />
    </ActionBarPrimitive.Root>
  );
};

const CopyAction: FC = () => (
  <ActionBarPrimitive.Copy asChild>
    <TooltipIconButton tooltip={t("copy")}>
      <AuiIf condition={(s) => s.message.isCopied}>
        <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
      </AuiIf>
      <AuiIf condition={(s) => !s.message.isCopied}>
        <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
      </AuiIf>
    </TooltipIconButton>
  </ActionBarPrimitive.Copy>
);

/** 新发出的那条气泡弹一下；接替「发送中」临时气泡的正式气泡、翻历史时出现的旧气泡不弹（只看挂上那一刻）。 */
function useEnterOnce(): boolean {
  const isLast = useAuiState((s) => s.message.isLast);
  const id = useAuiState((s) => s.message.id);
  const [enter] = useState(() => isLast && !takeHandoff(id) && Date.now() - lastSendAt() < 4000);
  return enter;
}

const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="grid auto-rows-auto grid-cols-[minmax(20%,1fr)_auto] content-start gap-y-2 px-2 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content cc-bubble-user peer empty:hidden" data-enter={useEnterOnce() || undefined}>
          <MessagePrimitive.Parts />
        </div>
      </div>

      {/* 你发的消息悬停时在气泡下方右侧出现「复制」（同 claude.ai），位置先留好、出现时不跳；手机上长按选字即可 */}
      <div className="-mt-1 -me-1 flex min-h-7 justify-end pointer-coarse:hidden">
        <ActionBarPrimitive.Root autohide="always" className="flex text-ink-3">
          <CopyAction />
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
};
