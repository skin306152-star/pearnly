
import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/attachment";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { FileTypeIcon, fileTypeLabel, useResults } from "@/components/results-panel";
import { StepRow, StepsGroup } from "@/components/tool-uis/steps";
import { TypingDots } from "@/components/typing-dots";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { steps, t } from "@/i18n";
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
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  LoaderIcon,
  SquareIcon,
} from "lucide-react";
import { fileUrl } from "@/lib/managed-agents/api";
import { lastSendAt, setDraft, takeHandoff, usePendingSend } from "@/lib/managed-agents/pending-send";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type FC,
  type PropsWithChildren,
} from "react";

// 触屏设备（手机、平板）：没有悬停；打开页面不自动聚焦输入框
const TOUCH = typeof window !== "undefined" && window.matchMedia("(hover: none)").matches;

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

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-app @container flex h-full flex-col font-sans text-ink"
      style={{
        ["--thread-max-width" as string]: "var(--cc-thread-w)",
      }}
    >
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-auto [scrollbar-gutter:stable] scroll-smooth"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-4 pt-4",
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

/** 附件还在上传时就发送：先显示发送中的用户气泡（附件名旁转圈），真正的气泡出来就换掉（lib/managed-agents/pending-send.ts）。 */
const PendingSendBubble: FC = () => {
  const pending = usePendingSend();
  if (!pending) return null;
  return (
    <div data-role="user" className="grid grid-cols-[minmax(20%,1fr)_auto] content-start gap-y-2 px-2 [&>*]:col-start-2">
      <div className="flex flex-wrap justify-end gap-2">
        {pending.files.map((name, i) => (
          <span key={i} className="cc-card flex items-center gap-1.5 px-2.5 py-1.5 text-sm">
            <LoaderIcon className="size-3.5 animate-spin text-ink-3" />
            {name}
          </span>
        ))}
      </div>
      {pending.text && (
        <div className="cc-bubble-user" data-enter="" data-sending="">{pending.text}</div>
      )}
      <div className="col-span-full col-start-1 pt-4">
        <ThinkingMark />
      </div>
    </div>
  );
};

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

  if (role === "user") return <UserMessage />;
  return <AssistantMessageComponent />;
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
  // 新对话问「今天能帮你做什么」，对话里就是「回复…」（同 Claude）；打开已有对话、消息还在加载时也已经是「回复…」
  const hasMessages = useAuiState((s) => s.thread.messages.length > 0 || !!s.threadListItem.remoteId);
  const placeholder = hasMessages ? t("reply") : t("placeholder");
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="cc-composer"
        >
          <ComposerAttachments />
          <DraftWatcher />
          <ComposerPrimitive.Input
            placeholder={placeholder}
            className="aui-composer-input block max-h-72 min-h-6 w-full resize-none bg-transparent px-1 font-sans text-base outline-none"
            rows={1}
            autoFocus={!TOUCH /* 手机上打开不弹键盘（同 Claude 手机 app），点输入框才弹 */}
            enterKeyHint="send"
            aria-label={placeholder}
          />
          <ComposerAction />
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  );
};

const ComposerAction: FC = () => {
  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-between">
      <AuiIf condition={(s) => s.thread.capabilities.attachments}>
        <ComposerAddAttachment />
      </AuiIf>
      <div className="ms-auto flex items-center gap-1.5">
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
      <ErrorPrimitive.Root className="aui-message-error-root border-danger bg-danger-soft text-danger mt-2 rounded-md border p-3 text-sm">
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
        className="cc-reply px-2 text-base leading-[1.7rem] wrap-break-word"
      >
        <MessagePrimitive.GroupedParts groupBy={groupByWithVisibleGates}>
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
              case "indicator":
                return <ThinkingMark />;
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessageError />
        <MessageFiles />
      </div>

      <div
        data-slot="aui_assistant-message-footer"
        className={cn("ms-2 flex items-center", ACTION_BAR_HEIGHT)}
      >
        <AssistantActionBar />
      </div>
    </MessagePrimitive.Root>
  );
};

/** 等回复（同 Claude）：小橘点在动，旁边「8秒 · 思考中」，秒数从出现时开始走。 */
const ThinkingMark: FC = () => {
  // 从按下发送那一刻算（发送中气泡下的与回复里的是同一段等待，不归零）；刷新后接着看的从出现时算
  const [start] = useState(() => (Date.now() - lastSendAt() < 10 * 60 * 1000 ? lastSendAt() : Date.now()));
  const [now, setNow] = useState(start);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.floor((now - start) / 1000);
  return (
    <span className="inline-flex items-center font-sans">
      <TypingDots label={steps.thinking} showLabel={false} />
      <span className="typing-dots-label ms-2.5">{`${steps.seconds(seconds)} · ${steps.thinkingNow}`}</span>
    </span>
  );
};

/** 这条回复期间产出的文件：显示成卡片，点一下在右侧打开（同 claude.ai）。按文件时间落在本条与下一条消息之间判定。 */
const MessageFiles: FC = () => {
  const { sessionId, files, openFile } = useResults();
  const id = useAuiState((s) => s.message.id);
  const messages = useAuiState((s) => s.thread.messages);
  const mine = useMemo(() => {
    const index = messages.findIndex((m) => m.id === id);
    if (index < 0) return [];
    const from = messages[index]!.createdAt.getTime();
    const until = messages[index + 1]?.createdAt.getTime() ?? Number.POSITIVE_INFINITY;
    return files.filter((f) => {
      const at = new Date(f.created_at).getTime();
      return at >= from && at < until;
    });
  }, [files, messages, id]);
  if (!sessionId || !mine.length) return null;
  return (
    <div className="mt-3 flex flex-col gap-2">
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
            className="cc-icon-btn"
          >
            <DownloadIcon className="size-4" />
          </a>
        </div>
      ))}
    </div>
  );
};

// 回复下的操作（同 Claude）：只放引擎真能做到的「复制」——会话记录改不了，重试 / 编辑做不到原样替换，就不摆个点了没反应的按钮。
// 电脑上最后一条一直显示、其余悬停时显示；手机没有悬停，每条都显示（同 Claude 手机端）。

const AssistantActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide={TOUCH ? "never" : "not-last"}
      className="aui-assistant-action-bar-root -ms-1 flex gap-0.5 text-ink-3"
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
  const [enter] = useState(() => isLast && !takeHandoff() && Date.now() - lastSendAt() < 4000);
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
