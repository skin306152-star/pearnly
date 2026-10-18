
import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/attachment";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { FileTypeIcon, fileTypeLabel, useResults } from "@/components/results-panel";
import { LoneStep, runningStepLabel, StepRow, StepsGroup } from "@/components/tool-uis/steps";
import { ApprovalSheet } from "@/components/tool-uis/approval-sheet";
import { TypingDots } from "@/components/typing-dots";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
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
import { useSendFailure } from "@/lib/managed-agents/send-failure";
import { stopRun } from "@/lib/managed-agents/stop-run";
import { useActiveSessionSnapshot, useOpenState } from "@/lib/managed-agents/runtime-provider";
import { statusText, ThinkingClock, turnActivity, turnStartedAt } from "@/lib/managed-agents/turn";
import { OpenSettler, type OpenState } from "@/lib/follow-scroll";
import { holdImeEnter } from "@/lib/ime";
import { KEYBOARD_CLOSED } from "@/lib/viewport";
import { useToolLabels } from "@/lib/tool-labels";
import { approvalCard, type CardPart } from "@/lib/approval-card";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type FC,
  type KeyboardEvent,
  type PropsWithChildren,
} from "react";
import { Marquee } from "@/components/ui/marquee";

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
  const { Welcome, Loading } = useContext(ThreadComponentsContext);
  const settle = useSettleOnOpen();

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-app @container flex h-full flex-col font-sans text-ink"
    >
      <ThreadPrimitive.Viewport
        ref={settle.viewport}
        turnAnchor="top"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-auto [scrollbar-gutter:stable] scroll-smooth"
      >
        <div
          ref={settle.content}
          className={cn(
            // 栏宽 768（设计文档 3.10）：内容区左右各留一份 --cc-thread-gutter，正文与输入框正好 --cc-thread-w
            "mx-auto flex w-full max-w-[calc(var(--cc-thread-w)+var(--cc-thread-gutter)*2)] flex-1 flex-col px-(--cc-thread-gutter) pt-(--cc-space-16)",
            (isEmpty || isLoading) && "justify-center",
          )}
        >
          {isLoading && Loading && <Loading />}
          {isEmpty && (
            // 手机上问候在屏幕中间、输入框贴底（同 Claude 手机端）；电脑上两者一起居中
            <div className="max-md:flex max-md:flex-1 max-md:items-center max-md:justify-center">
              {Welcome && (
                <AuiIf condition={isNewChatView}>
                  <Welcome />
                </AuiIf>
              )}
            </div>
          )}

          <div
            data-slot="aui_message-group"
            className="mb-(--cc-thread-tail) flex flex-col gap-y-(--cc-turn-gap) empty:hidden"
          >
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
            <PendingSendBubble />
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer flex flex-col gap-(--cc-space-16) overflow-visible bg-linear-to-b from-transparent via-app/85 to-app pt-(--cc-space-16) pb-(--cc-space-16) md:pb-(--cc-space-24)",
              !isEmpty &&
                "sticky bottom-0 mt-auto rounded-t-xl",
            )}
          >
            <ThreadScrollToBottom />
            <SendFailure />
            <ApprovalSheet />
            <Composer />
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

/** 按下发送到真正的气泡画出来之前，先显示用户气泡（附件名旁一块骨架在闪，不用转圈：ccs 卡片规矩；lib/managed-agents/pending-send.ts）。
 *  对话里画出了接替它的那一条才撤（同一次提交里换过去），中间不会两个都不在。 */
const PendingSendBubble: FC = () => {
  const pending = useShownPending();
  if (!pending) return null;
  // 用户气泡同 Claude：发出去就是平常的样子，不加「发送中」的扫光；工作状态行在 CC 这一侧（左边），
  // 和正式回复下面的状态行同一列（px-2），不跟着用户气泡靠右（用户 10-16：「发送时我方不应该出现气泡，cc 那边正常出现」）
  return (
    <div>
      <div data-role="user" className="cc-user-row">
        <div className="flex flex-wrap justify-end gap-2">
          {pending.files.map((name, i) => (
            <span key={i} className="cc-card flex items-center gap-1.5 px-2.5 py-1.5 text-body">
              <Skeleton aria-hidden className="size-3.5 shrink-0 rounded-xs bg-subtle" />
              {name}
            </span>
          ))}
        </div>
        {pending.text && <div className="cc-bubble-user" data-enter="">{pending.text}</div>}
      </div>
      <RunIndicator className="px-(--cc-space-8) pt-(--cc-run-gap)" />
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

/** 打开一段对话：已结束的停在最新一条，正在回复的停在这一轮开头（提问顶在上面），之后这一轮跑完也不改（lib/follow-scroll.ts）。
 *  回复在出时不跟到底，下面有内容就出 ↓（同 Claude，三端一样）。 */
function useSettleOnOpen() {
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null);
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  // 打开的是哪段对话（消息取回来了才算打开）：换了一段、或这段的消息刚取回，就停到该停的位置（follow-scroll 的 settle）
  const opened = useAuiState((s) => (s.thread.messages.length > 0 ? s.threadListItem.id : ""));
  const openedNow = useRef(opened);
  openedNow.current = opened;
  // 本页刚发出（发送中的气泡还在）：新对话第一条落进来不算打开，位置交给组件库的「这一轮顶到上面」
  const sending = useRef(false);
  sending.current = usePendingSend() !== null;
  // 有没有在跑的一轮、状态核对过没有（会话控制器的快照，不看组件库的标记，见 follow-scroll.ts 文件头）
  const { turn, synced } = useOpenState();
  const openState = useRef<OpenState>({ turn, synced });
  openState.current = { turn, synced };
  const settler = useRef<OpenSettler | null>(null);
  useEffect(() => {
    if (!viewport || !content) return;
    const created = new OpenSettler(viewport, content, () => openState.current);
    settler.current = created;
    if (openedNow.current && !sending.current) created.settle();
    return () => {
      created.dispose();
      if (settler.current === created) settler.current = null;
    };
  }, [viewport, content]);
  useEffect(() => {
    if (opened && !sending.current) settler.current?.settle();
  }, [opened]);
  // 这一轮开始 / 结束、状态核对完：打开时的旧状态核对完要重新对一次，还在对位置就按新状态对
  useEffect(() => {
    settler.current?.recheck();
  }, [turn, synced]);
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
    <MessagePrimitive.Root data-slot="cc-notice" data-role="notice" className="flex flex-col items-start gap-(--cc-space-8) px-(--cc-space-8)">
      <div role="note" aria-label={t("notice")} className="cc-file-card w-full flex-col gap-(--cc-file-card-py) px-(--cc-space-12) py-(--cc-file-card-py)">
        <div className="flex gap-(--cc-space-12)">
          <span className="cc-file-card-icon">
            <BellIcon />
          </span>
          <div className="min-w-0 flex-1">
            {title && <div className="font-medium text-ink text-body">{title}</div>}
            <div className="wrap-break-word whitespace-pre-line text-ink-2 text-body">{text}</div>
            <div className="mt-(--cc-space-4) text-ink-3 text-caption">{noticeTime(createdAt)}</div>
          </div>
        </div>
        {/* 「打开通知」在卡片底部靠右（卡片按钮，同允许卡）；打开了按钮就没了，没打开原因写在按钮上方一行 */}
        {push.state === "off" && (
          <div className="cc-ask-foot">
            {push.failure && (
              <div role="alert" className={ERROR_BOX}>
                <p className="line-clamp-(--cc-alert-lines)">{push.failure}</p>
              </div>
            )}
            <div className="cc-card-actions">
              <button type="button" className="cc-card-btn" onClick={() => void push.enable()}>
                {st.turnOnNotifications}
              </button>
            </div>
          </div>
        )}
      </div>
    </MessagePrimitive.Root>
  );
};

/** 下面还有内容时在输入框上方居中出 ↓，点了平滑滚到最新（同 Claude，13.webp / 12.webp）。
 *  40 的圆、箭头 20，离输入框 12（量 Claude 手机截图约 39pt 圆、约 12pt 间距），样子是共用的 .cc-scroll-btn（V70）；在底部时组件库把它禁用，
 *  这里藏起来（visibility: hidden：读屏读不到、Tab 走不到）。读屏名写在 aria-label 上（第 11 轮 C21：只靠按钮里的隐藏文字时属性是空的）。 */
const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom asChild behavior="smooth">
      <TooltipIconButton
        tooltip={t("scrollToBottom")}
        aria-label={t("scrollToBottom")}
        className="aui-thread-scroll-to-bottom cc-scroll-btn"
      >
        <ArrowDownIcon />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const Composer: FC = () => {
  const { Chin } = useContext(ThreadComponentsContext);
  // 新对话问「今天能帮你做什么」，对话里就是「回复…」（同 Claude）；打开已有对话、消息还在加载时也已经是「回复…」
  const hasMessages = useAuiState((s) => s.thread.messages.length > 0 || !!s.threadListItem.remoteId);
  const placeholder = hasMessages ? t("reply") : t("placeholder");
  // 没在输入、也没有字和附件时标 data-collapsed：手机上收成一行胶囊（「＋」、占位字，同 Claude 手机端），点进去展开成
  // 两行（输入在上、按钮在下）；电脑不按它变（globals.css 只在手机外壳里用）
  const [typing, setTyping] = useState(false);
  const empty = useAuiState((s) => s.composer.isEmpty);
  // 安卓返回键收起键盘时输入框仍聚焦：键盘收起、输入框是空的就让它失焦，收回一行（同点了空白处）
  const emptyNow = useRef(empty);
  emptyNow.current = empty;
  useEffect(() => {
    const closed = () => {
      const el = document.activeElement;
      if (emptyNow.current && el instanceof HTMLTextAreaElement && el.closest('[data-slot="aui_composer-shell"]')) el.blur();
    };
    window.addEventListener(KEYBOARD_CLOSED, closed);
    return () => window.removeEventListener(KEYBOARD_CLOSED, closed);
  }, []);
  // 手机（触屏或手机外壳）发出去就收起键盘：这一轮顶到上面、下面留空（同 Claude 手机 app，11.webp）；电脑保持焦点接着打字
  const phone = useIsMobile();
  const dropKeyboard = TOUCH || phone;
  const input = useRef<HTMLTextAreaElement>(null);
  const sent = () => {
    if (dropKeyboard && !emptyNow.current) input.current?.blur();
  };
  // 在跑时输入框里按 Esc 停止（同停止键，stop-run.ts）；选字中的 Esc 归输入法
  const thread = useAuiState((s) => s.threadListItem.id);
  const running = useAuiState((s) => s.thread.isRunning);
  const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    holdImeEnter(event); // Safari 选字结束的回车（keyCode 229）不发送，lib/ime.ts
    if (event.key !== "Escape" || !running || event.nativeEvent.isComposing || event.defaultPrevented) return;
    event.preventDefault();
    void stopRun(thread);
  };
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col" onSubmit={sent}>
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="cc-composer cc-composer-inline"
          data-collapsed={!typing && empty ? "" : undefined}
        >
          <ComposerAttachments />
          <DraftWatcher />
          <ComposerPrimitive.Input
            ref={input}
            placeholder={placeholder}
            className="aui-composer-input block max-h-(--cc-composer-input-max) min-h-(--cc-control-xs) w-full resize-none bg-transparent font-sans outline-none"
            rows={1}
            autoFocus={!TOUCH /* 手机上打开不弹键盘（同 Claude 手机 app），点输入框才弹 */}
            unstable_focusOnRunStart={!dropKeyboard /* 手机上开跑、点 ↓ 都不把焦点拉回输入框 */}
            unstable_focusOnScrollToBottom={!dropKeyboard}
            enterKeyHint="send"
            onKeyDown={keyDown}
            onFocus={() => setTyping(true)}
            onBlur={() => setTyping(false)}
            aria-label={placeholder}
          />
          <ComposerAction onSend={sent} />
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

const ComposerAction: FC<{ onSend: () => void }> = ({ onSend }) => {
  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-between">
      <AuiIf condition={(s) => s.thread.capabilities.attachments}>
        <ComposerAddAttachment />
      </AuiIf>
      <div className="aui-composer-send-slot ms-auto flex items-center gap-(--cc-space-6)">
        <AuiIf condition={(s) => !s.thread.isRunning}>
          <ComposerPrimitive.Send asChild>
            <button type="button" className="aui-composer-send cc-send" aria-label={t("send")} title={t("send")} onClick={onSend}>
              <ArrowUpIcon className="aui-composer-send-icon" />
            </button>
          </ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={(s) => s.thread.isRunning}>
          <StopButton />
        </AuiIf>
      </div>
    </div>
  );
};

/** 停止键：真正的打断，消息留在对话里、字不放回输入框（不经组件库的 cancelRun，stop-run.ts）。 */
const StopButton: FC = () => {
  const thread = useAuiState((s) => s.threadListItem.id);
  return (
    <button type="button" className="aui-composer-cancel cc-send" data-stop="" aria-label={t("stop")} title={t("stop")} onClick={() => void stopRun(thread)}>
      <SquareIcon className="aui-composer-cancel-icon fill-current" />
    </button>
  );
};

// 对话里出错那一行（回复出错、发送没成功、通知没打开）同一个样子：统一的出错框 .cc-alert（危险浅底、危险线、内边距 12、圆角 8，V12），最多两行
const ERROR_BOX = "cc-alert";

// 和回复里别的块一样只用上外边距（.cc-reply 的 --cc-md-block，V32），不另写 mt
const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className={cn("aui-message-error-root", ERROR_BOX)}>
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-(--cc-alert-lines)" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

/** 这段对话上次发送没成功（新对话建会话被拒——例如本站点没设置 CC——、没有权限、断网、服务端出错）：原因写在输入框上方，
 *  同对话里出错那一行；打的字与附件已放回输入框，再发送时清掉（lib/managed-agents/send-failure.ts）。 */
const SendFailure: FC = () => {
  const thread = useAuiState((s) => s.threadListItem.id);
  const message = useSendFailure(thread);
  if (!message) return null;
  return (
    <div role="alert" data-slot="cc-send-failure" className={ERROR_BOX}>
      <p className="line-clamp-(--cc-alert-lines)">{message}</p>
    </div>
  );
};

// 思考与工具调用连续的一段收成一组「步骤」（同 claude.ai）。允许 / 拒绝卡不收进组（lib/approval-card.ts）：等回答的收在折叠里
// 没人看得见，会话就卡住了；点完的照 Claude 留在对话流原处（当场、切回、重开对话同一个样子）。
const groupByBase = groupPartByType({
  reasoning: ["group-chainOfThought"],
  "tool-call": ["group-chainOfThought"],
  "standalone-tool-call": [],
});
function useGroupByWithVisibleGates(): typeof groupByBase {
  const labels = useToolLabels();
  return useMemo<typeof groupByBase>(
    () => (part, context) =>
      part.type === "tool-call" && approvalCard(part as unknown as CardPart, labels) ? [] : groupByBase(part, context),
    [labels],
  );
}

/** 这条回复要不要等工具名字（lib/tool-labels.ts）取回再画：有本站工具已有结果、本页没有点击记录的那一步（重开对话时），是不是
 *  点完的卡片要靠它认；没取回前先画会先进折叠的步骤组、取回后再跳出来（第 10 轮审查）。取失败也算取回（按普通步骤画）。 */
function useWaitForLabels(): boolean {
  const ready = useToolLabels().ready === true;
  return useAuiState(
    (s) =>
      !ready &&
      s.message.parts.some((p) => {
        const part = p as { type: string; result?: unknown; approval?: unknown; toolKind?: string };
        return part.type === "tool-call" && part.toolKind === "custom" && part.result !== undefined && !part.approval;
      }),
  );
}

const AssistantMessage: FC = () => {
  const { ToolFallback: ToolFallbackComponent = StepRow } = useContext(ThreadComponentsContext);
  const groupByWithVisibleGates = useGroupByWithVisibleGates();
  const waitForLabels = useWaitForLabels();

  // 操作行留在消息自己的绘制区里（content-visibility 会裁掉区外的），再用负外边距把它占的高让出来（--cc-reply-actions-h）
  const ACTION_BAR_HEIGHT = "min-h-(--cc-reply-actions-h) pt-(--cc-space-6)";

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="group/message relative -mb-(--cc-reply-actions-h) pb-(--cc-reply-actions-h) [contain-intrinsic-size:auto_var(--cc-msg-intrinsic)] [content-visibility:auto]"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="cc-reply px-(--cc-space-8) wrap-break-word"
      >
        {/* 「在做什么」统一由对话底部的工作状态行说（RunIndicator），回复里不再另补占位 */}
        {!waitForLabels && (
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
                  // 不进组的只有允许 / 拒绝卡：同组里的步骤一样放进带环的卡片（只一层框），不单独浮在正文里；手机上还在等回答的不画
                  return (
                    part.toolUI ?? (
                      <LoneStep part={part as unknown as CardPart}>
                        <ToolFallbackComponent {...part} />
                      </LoneStep>
                    )
                  );
                case "data":
                  return part.dataRendererUI;
                default:
                  return null;
              }
            }}
          </MessagePrimitive.GroupedParts>
        )}
        <MessageError />
        <MessageFiles />
      </div>
      {/* 工作状态行挂在这一轮的回复里（还没有回复时是组件库补的占位）：组件库把这一轮推到顶上时在回复后面留空白，
          放在回复外面就被挤到屏幕最下面去了 */}
      <LastReplyIndicator />

      <div
        data-slot="aui_assistant-message-footer"
        className={cn("ms-(--cc-space-8) flex items-center", ACTION_BAR_HEIGHT)}
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
  return sending ? null : <RunIndicator className="px-(--cc-space-8) pt-(--cc-run-gap-reply)" />;
};

// 「思考了几秒」跨组件记：发送中气泡下的、组件库占位里的、正式回复里的是同一行，换位置时不归零
const thinking = new ThinkingClock();

/** 工作状态行（同 Claude，lib/managed-agents/turn.ts）：发出即出现在用户消息下面，橘色小点在动，旁边「计时 · 在做什么」，
 *  一直在，直到这一轮真正结束；电脑上等用户点「允许」时换成橙色小点 +「需要您的输入」（照 Claude，在卡片下方），点完换回来；
 *  手机上等回答时由输入框上方的浮层代替（approval-sheet.tsx），这一行不显示。计时从按下发送那一刻起本地每秒走；
 *  想完开始出正文时说「思考了 M秒」；很久收不到服务端消息时改说「连接中断，正在重连」。 */
const RunIndicator: FC<{ className?: string }> = ({ className }) => {
  const pending = usePendingSend();
  const snapshot = useActiveSessionSnapshot();
  const labels = useToolLabels();
  const phone = useIsMobile();
  const sending = !!pending; // 临时气泡还在（正式气泡还没画出来）：这一轮刚开始
  const activity = turnActivity(snapshot, sending);
  const waiting = activity.kind === "input";
  const visible = activity.kind !== "hidden" && !waiting;
  const now = useClock(visible);
  const start = turnStartedAt(snapshot.messages, lastSendAt(), sending, now) ?? now;
  // 等用户回答时同以前收起状态行一样记（不计进「思考了几秒」）
  const thought = thinking.note(waiting ? "hidden" : activity.kind, start, Date.now());
  if (waiting) {
    if (phone) return null;
    return (
      <div data-slot="cc-run-indicator" data-kind="input" className={cn("flex min-w-0 items-center font-sans", className)}>
        <span className="cc-needs-input-dot" aria-hidden="true" />
        <span className="cc-needs-input">{t("needsInput")}</span>
      </div>
    );
  }
  if (!visible) return null;
  const step = activity.kind === "tool" ? runningStepLabel(activity.toolName, activity.args, labels) : "";
  const text = statusText(activity, Math.floor((now - start) / 1000), thought, step);
  // 读屏只报在做什么，不每秒报一次计时
  const label = activity.kind === "tool" ? step : activity.kind === "reconnecting" ? text : steps.thinking;
  return (
    <div data-slot="cc-run-indicator" data-kind={activity.kind} className={cn("flex min-w-0 items-center font-sans", className)}>
      <TypingDots label={label} showLabel={false} />
      <Marquee className="typing-dots-label ms-(--cc-dots-gap) min-w-0 truncate" aria-hidden="true">{text}</Marquee>
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
    <div className="flex flex-col gap-(--cc-space-8)">
      {mine.map((file) => (
        // 卡片的内边距在「打开」按钮里：除了右边的下载，整张卡都能点（手机点按区，按钮排查 H11）
        <div
          key={file.id}
          className="cc-file-card cc-card-hover items-center pe-(--cc-space-12)"
        >
          <button type="button" onClick={() => openFile(file.id)} className="flex min-w-0 flex-1 items-center gap-(--cc-space-12) self-stretch rounded-md px-(--cc-space-12) py-(--cc-file-card-py) text-left">
            <span className="cc-file-card-icon">
              <FileTypeIcon filename={file.filename} />
            </span>
            <span className="min-w-0">
              <Marquee className="block truncate font-medium text-ink text-body">{file.filename}</Marquee>
              <span className="block text-ink-3 text-caption">{fileTypeLabel(file.filename)}</span>
            </span>
          </button>
          <a
            href={fileUrl(sessionId, file.id)}
            title={t("download")}
            aria-label={t("download")}
            className="cc-icon-btn cc-tap"
          >
            <DownloadIcon className="size-(--cc-icon-sm)" />
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
      className="aui-assistant-action-bar-root cc-reply-actions -ms-(--cc-space-4) flex gap-(--cc-space-2) text-ink-3"
    >
      <CopyAction />
    </ActionBarPrimitive.Root>
  );
};

const CopyAction: FC = () => (
  <ActionBarPrimitive.Copy asChild>
    <TooltipIconButton tooltip={t("copy")} className="aui-button-icon cc-row-btn cc-tap active:scale-(--cc-press-scale)">
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
      className="cc-user-row [contain-intrinsic-size:auto_var(--cc-msg-intrinsic)] [content-visibility:auto]"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content cc-bubble-user peer empty:hidden" data-enter={useEnterOnce() || undefined}>
          <MessagePrimitive.Parts />
        </div>
      </div>

      {/* 你发的消息悬停时在气泡下方右侧出现「复制」（同 claude.ai），位置先留好、出现时不跳；触屏上不放（data-cc-touch：手机变体，或粗指针且不是桌面窗口，V3），长按选字即可 */}
      <div className="-mt-(--cc-space-4) -me-(--cc-space-4) flex min-h-(--cc-control-sm) justify-end in-data-[cc-touch]:hidden">
        <ActionBarPrimitive.Root autohide="always" className="flex text-ink-3">
          <CopyAction />
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
};
