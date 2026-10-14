
// Wires Managed Agents into assistant-ui. Two layers:
//
//   useRemoteThreadListRuntime — the sidebar. Its adapter is the Managed
//     Agents session list (session-list-adapter.ts), so the thread list IS
//     the session list: no database, no id mapping (thread id = session id).
//
//   useSessionThreadRuntime — one per open thread, mounted by the runtime
//     hook. It reads the active session id off assistant-ui's own state,
//     grabs that session's controller (replay + live tail + actions), and
//     hands its folded event log to useExternalStoreRuntime. A brand-new
//     chat has no session yet: the composer still works, and the first send
//     creates the session through initialize().

import {
  AssistantRuntimeProvider,
  useAui,
  useAuiState,
  useExternalStoreRuntime,
  useRemoteThreadListRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { useEffect, useMemo, useSyncExternalStore } from "react";
import { noticesShown } from "./activity";
import { attachedFilesOf, createAttachmentAdapter } from "./attachments";
import { beginPendingText, clearPending, pendingShownAs } from "./pending-send";
import { sessionListAdapter, watchRunning } from "./session-list-adapter";
import {
  getSessionController,
  type ControllerSnapshot,
  type SessionController,
} from "./session-controller";
import { initialProjection, isBusy, withNotices } from "./reducer";

// ------------------------------------------------------------- state hookup
const EMPTY: ControllerSnapshot = {
  ...initialProjection(),
  isLoaded: true,
  isTailing: false,
  transportError: null,
  stalled: false,
};
const subscribeNothing = () => () => {};
const getEmpty = () => EMPTY;

function useControllerSnapshot(controller: SessionController | null): ControllerSnapshot {
  return useSyncExternalStore(
    controller ? controller.subscribe : subscribeNothing,
    controller ? controller.getSnapshot : getEmpty,
    getEmpty,
  );
}

function textOf(message: AppendMessage): string {
  return message.content
    .filter((p) => p.type === "text")
    .map((p) => (p as { text: string }).text)
    .join("\n")
    .trim();
}

// ------------------------------------------------------- per-thread runtime
function useSessionThreadRuntime() {
  const aui = useAui();
  // externalId is the session id for an initialized thread; a new chat has
  // neither until its first send creates the session.
  const sessionId = useAuiState((s) => s.threadListItem.externalId ?? s.threadListItem.remoteId);

  const controller = sessionId ? getSessionController(sessionId) : null;
  const snapshot = useControllerSnapshot(controller);

  // Attach while this thread is the one on screen: replay history and open
  // the live tail. The cleanup detaches when the thread goes to the
  // background (this hook instance stays MOUNTED for background threads, so
  // the gate is isMain, not unmount) — browsers cap concurrent connections
  // per origin, and a tail per visited session would exhaust the cap after
  // a few threads. The controller (and its folded state) stays warm in the
  // registry; attach() re-replays on return, so nothing emitted while
  // detached is lost.
  const isActive = useAuiState((s) => s.threads.mainThreadId === s.threadListItem.id);
  useEffect(() => {
    if (!controller || !isActive) return;
    controller.attach();
    return () => controller.detach();
  }, [controller, isActive]);

  // 当前对话里显示出了新的提醒（推送、补拉、整段回放带回来的都算）：交给 activity 告诉服务端看过了
  const lastNotice = snapshot.notices.at(-1)?.id;
  useEffect(() => {
    if (isActive && sessionId && lastNotice) noticesShown(sessionId, lastNotice);
  }, [isActive, sessionId, lastNotice]);

  /** Resolve the session id, creating the session for a brand-new chat. */
  const ensureSession = useMemo(
    () => async (): Promise<string> => {
      const { externalId, remoteId } = await aui.threadListItem().initialize();
      const id = externalId ?? remoteId;
      if (!id) throw new Error("session was not created");
      // Warm the controller so the tail is up before anything happens.
      getSessionController(id).attach();
      return id;
    },
    [aui],
  );

  const attachments = useMemo(() => createAttachmentAdapter(ensureSession), [ensureSession]);

  // 本站提醒卡片在这里才并进对话（reducer.withNotices）：发送、批准、回复完成通知都只看引擎消息。
  const messages = useMemo(() => withNotices(snapshot.messages, snapshot.notices), [snapshot.messages, snapshot.notices]);

  return useExternalStoreRuntime<ThreadMessageLike>({
    // Cast is safe: the reducer's message model is a structural subset of
    // ThreadMessageLike (see reducer.ts).
    messages: messages as readonly ThreadMessageLike[],
    convertMessage: (m) => m,
    isLoading: !snapshot.isLoaded,
    // Busy = the agent is working, or a gate/custom tool is waiting on us.
    isRunning: isBusy(snapshot),
    isDisabled: snapshot.isDead,
    adapters: { attachments },

    onNew: async (message: AppendMessage) => {
      // 按「还没有用户消息」判断首条：先加附件时会话在上传那一刻就建好了，按「有没有会话」判断会漏掉取标题，
      // 侧栏一直是「新对话」（2026-10-10 测试）。
      const isFirst = !snapshot.messages.some((m) => m.role === "user");
      const text = textOf(message);
      // 按下发送立刻出现气泡（新对话要先建会话，约 1 秒）；真正的气泡放进对话、并且画出来了才撤（thread.tsx 的
      // PendingSendBubble），不会两个都在，也不会两个都不在。发送请求结束时（至少一次往返，早已画出）兜底撤掉。
      beginPendingText(text);
      try {
        const id = await ensureSession();
        await getSessionController(id).sendMessage(text, attachedFilesOf(message.attachments), pendingShownAs);
      } finally {
        clearPending();
      }
      // 会话由我们自己建（initialize 在发送前），组件库「首轮结束后取标题」的钩子不会触发；
      // 这里自己取：服务端把首条消息概括成短标题后推过来（短消息原文即标题，随发送一起推），侧栏一收到就显示。
      if (isFirst) void aui.threadListItem().generateTitle();
    },

    // Stop button: a real interrupt, not just dropping the response.
    onCancel: async () => {
      if (controller) await controller.interrupt();
    },

    // The Allow/Deny click on a gated tool. approvalId is the tool_use
    // event id straight out of session.status_idle{requires_action}.
    onRespondToToolApproval: async ({ approvalId, approved, reason }) => {
      controller?.respondToApproval(approvalId, approved, reason);
    },
  });
}

// ---------------------------------------------------------------- provider
export function ManagedAgentsRuntimeProvider({ children }: { children: React.ReactNode }) {
  const runtime = useRemoteThreadListRuntime({
    adapter: sessionListAdapter,
    // Each mounted thread gets its own instance of this hook.
    runtimeHook: useSessionThreadRuntime,
  });
  // 侧栏标着在跑的其他对话：回到前台、恢复、联网、重连时向服务端核对（开着的那个由它的会话控制器核对）
  useEffect(() => watchRunning(), []);
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}

// Convenience for components that want the live session state (files
// panel, connection banner) without going through message rendering.
export function useActiveSessionSnapshot(): ControllerSnapshot & { sessionId: string | null } {
  const sessionId =
    useAuiState((s) => s.threadListItem.externalId ?? s.threadListItem.remoteId) ?? null;
  const controller = sessionId ? getSessionController(sessionId) : null;
  const snapshot = useControllerSnapshot(controller);
  return { ...snapshot, sessionId };
}
