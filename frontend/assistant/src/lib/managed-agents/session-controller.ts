
// One instance per Managed Agents session, held in a registry so switching
// threads in the sidebar keeps every conversation's state warm. It owns the
// three moving parts of a live thread:
//
//   1. replay:  pearnly_core.assistant.api.events, folded through the reducer
//   2. tail:    a PollingSource (EventSource-shaped poller, see poll-source.ts),
//               kept open across turns and across the approval pause
//   3. actions: send a message, allow/deny (batched), answer a custom tool,
//               interrupt
//
// React subscribes with useSyncExternalStore; nothing here touches React.
//
// 在回复 / 等允许 / 已结束以服务端读回的运行状态为准（RunStatus，reducer.applyRunStatus）：每次整段重放（含重进对话）
// 都带一份，回到前台、页面恢复、联网、实时通道重连、点停止时由尾巴补拉一次带一份。不从「没收到某条事件」推断；
// 本页乐观标的「在回复」也由它清掉，卡住的「在回复」不会一直挡着发新消息。
//
// 在跑还是断了（教程第 8 条 g）：这一轮在跑时，服务端的任何消息（推来的事件、补拉的回应、实时通道的心跳）都算「还连着」。
// 超过 STALL_MS 一条也没有，就标 stalled（状态行改成「连接中断，正在重连」），并重开尾巴：走原有的连上即补拉、带状态
// 校正那一套，不另加轮询；之后收到任何消息就清掉。页面在后台时不计（回到前台重新计时，尾巴同时补拉一次）。

import { shownError, transientError } from "@/lib/frappe";
import { noteRun } from "./activity";
import { confirmTools, interruptSession, listEvents, runTool, sendMessage as postMessage } from "./api";
import { type FeedEvent, messageText, type RelayEnvelope, type RunStatus, type StreamEvent, toolRunEvent } from "./events";
import { PollingSource } from "./poll-source";
import {
  applyEvent,
  applyRunStatus,
  evictOptimisticMessage,
  initialProjection,
  isBusy,
  markResuming,
  restoreGates,
  clearSending,
  gateCustomTool,
  type Projection,
} from "./reducer";

export type ControllerSnapshot = Projection & {
  /** History fetched at least once (spinner while false). */
  isLoaded: boolean;
  /** The tail is connected. False while replaying or reconnecting. */
  isTailing: boolean;
  /** Last transport-level problem (relay drop, failed action), for a banner. */
  transportError: string | null;
  /** 这一轮在跑，却超过 STALL_MS 收不到服务端任何消息（见文件头）。 */
  stalled: boolean;
  /** 接上（attach）以后整段重放成功过一次：此刻的消息与在不在跑是按服务端核对过的。离开这段对话（detach）时置否——
   *  回来到核对完之前，显示的是离开时的旧状态（例如离开期间这一轮已结束，这里还是「在跑」）。打开对话停在哪儿要分清这两种
   *  （lib/follow-scroll.ts）。 */
  isSynced: boolean;
};

/** 在跑时多久收不到服务端任何消息算断了。依据：实时通道连着时，在跑的会话每 8 秒补拉一次（poll-source 的
 *  PUSHED_RUNNING_MS），通道自己的心跳每 25 秒一次（socket.io 默认 pingInterval）；没连上时补拉 1.2 秒一次、失败后 6 秒一次。
 *  引擎「列出事件」偶尔单次 30 多秒（工具长时间执行时更常见），这期间心跳照到。取比心跳间隔多 5 秒：通道正常时不会误报，
 *  断网后半分钟内说清楚。 */
export const STALL_MS = 30_000;

type Listener = () => void;

export class SessionController {
  readonly sessionId: string;
  private state: ControllerSnapshot;
  private listeners = new Set<Listener>();
  private source: PollingSource | null = null;
  private replayed = false;
  private disposed = false;
  // While folding stored history, a requires_action lands *before* the event
  // that answered it (both are in the same list). Answering custom tools
  // mid-fold would double-post; hold until the whole log is folded.
  private replaying = false;
  // A single in-flight replay. onerror can fire in bursts and attach() may
  // overlap it; concurrent folds would let one finish and clear `replaying`
  // while another is still mid-fold. Everyone shares the running one.
  private replayInFlight: Promise<void> | null = null;
  // A replay requested WHILE one is in flight can't just reuse it: the in-
  // flight fetch may predate the newer disconnect and miss events emitted
  // during it. Note the request and run one more pass when it finishes.
  private rerunNeeded = false;
  // Every time the tail (re)connects there may be a gap: events emitted
  // while no stream was attached exist only in the persisted log, and a
  // fresh live event folding before that backfill replays would misorder
  // the transcript permanently (dedupe bakes it in) — or stamp a
  // requires_action gate before its tool_use exists, wedging the approval.
  // So from each tail onopen until a replay STARTED at-or-after it
  // succeeds (`backfilling`), and before the very first successful load
  // (`!replayed`), live events are buffered here and flushed after the
  // history folds. A FAILED replay keeps buffering and retries below.
  private pendingTail: FeedEvent[] = [];
  private backfilling = false;
  private replayRetryTimer: ReturnType<typeof setTimeout> | null = null;
  private replayRetryDelay = 2000;
  // The tail reconnect delay after a hard close, with backoff.
  private reopenTimer: ReturnType<typeof setTimeout> | null = null;
  private reopenDelay = 1000;
  // Counter for locally synthesized (optimistic) event ids; every synthetic
  // event needs a fresh id or the reducer's dedupe swallows it.
  private optimistic = 0;
  // 本页最近一次自己改运行状态（发消息、点允许 / 拒绝、答完工具）的时刻（本页时钟）：在它之前发出的状态请求读到的是
  // 这之前的服务端，不能拿来盖掉这次操作（例如刚发出时还读到「空闲」）。
  private localChangeAt = 0;
  // 本页自己的改动还没被服务端收下（消息、允许 / 拒绝的请求还在路上）：这期间读回的状态是收下之前的服务端
  // （例如发出后马上回到前台，读到上一轮结束时的「空闲」），一律不用；收下之后的时刻再读。
  private unacked = 0;
  // 并完一批事件再答自定义工具：同一页里后面就有这次调用的答复时不再白跑一次（见 fold）
  private answerQueued = false;
  // 正在发出的那条消息的请求：点停止要等它有了结果再打断（服务端还没收下这条就打断，打断落空、回复照样出来）
  private sendInFlight: Promise<unknown> | null = null;
  // 发出请求断在路上（一时的失败）、已撤回并放回输入框的消息：完整的字（按服务端存的口径去掉首尾空白，contracts.message_text
  // 的 strip；只带附件时拼出的字以空行开头，回声里没有）→ 用户打的字。之后它的回声到了（服务端其实收下了），告诉页面撤掉放回的（onDelivered）
  private retracted = new Map<string, string>();
  private delivered = new Set<(text: string) => void>();

  // Confirmation batching: clicks within a short window ride one request,
  // because racing two sends can be rejected mid-resume by the API.
  private confirmQueue: Array<{ tool_use_id: string; result: "allow" | "deny"; deny_message?: string }> = [];
  private confirmTimer: ReturnType<typeof setTimeout> | null = null;

  // 在跑还是断了（见文件头）：最近一次收到服务端消息的时刻（本页时钟）与看门的定时器（只在这一轮在跑时有，不发请求）
  private lastContact = Date.now();
  private stallTimer: ReturnType<typeof setTimeout> | null = null;
  // 看门重开尾巴的次数：之前那条尾巴卡住的重放回来时据此作废
  private tailGeneration = 0;
  /** 测试里调短。 */
  static stallMs = STALL_MS;

  constructor(sessionId: string) {
    this.sessionId = sessionId;
    this.state = { ...initialProjection(), isLoaded: false, isTailing: false, transportError: null, stalled: false, isSynced: false };
  }

  // ------------------------------------------------------------ subscription
  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = (): ControllerSnapshot => this.state;

  private set(patch: Partial<ControllerSnapshot>) {
    this.state = { ...this.state, ...patch };
    for (const l of this.listeners) l();
  }

  /** Merge a new projection (from a fold, a rollback, or an optimistic
   *  patch) into the snapshot and notify. The one place the post-update
   *  hooks live, so they can't silently diverge between callers. */
  private mergeProjection(next: Partial<ControllerSnapshot>) {
    this.state = { ...this.state, ...next };
    for (const l of this.listeners) l();
    // A session that just died (terminated/deleted) has nothing more to
    // stream; drop the tail so EventSource doesn't keep reconnecting to a
    // route that now 404s.
    if (this.state.isDead && this.source) this.detach();
    this.watchStall();
  }

  // ------------------------------------------------------------ 在跑还是断了
  /** 收到了服务端的消息（事件、补拉回应、心跳），或重新开始计时（发出、点完允许、回到前台、接上尾巴）。 */
  private touch() {
    this.lastContact = Date.now();
    if (this.state.stalled) this.set({ stalled: false });
    this.watchStall();
  }

  /** 这一轮在跑且尾巴开着才看门；不在跑了就收起「连接中断」。 */
  private watchStall() {
    const watching = !this.disposed && !!this.source && isBusy(this.state);
    if (!watching) {
      if (this.stallTimer) clearTimeout(this.stallTimer);
      this.stallTimer = null;
      if (this.state.stalled) this.set({ stalled: false });
      return;
    }
    if (this.stallTimer) return;
    const wait = Math.max(0, this.lastContact + SessionController.stallMs - Date.now());
    this.stallTimer = setTimeout(() => {
      this.stallTimer = null;
      this.checkStall();
    }, wait);
  }

  private checkStall() {
    if (this.disposed || !this.source || !isBusy(this.state)) return this.watchStall();
    // 页面在后台：浏览器停了补拉，不算断；回到前台时尾巴补拉一次并重新计时（onwake）
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      this.lastContact = Date.now();
      return this.watchStall();
    }
    if (Date.now() - this.lastContact < SessionController.stallMs) return this.watchStall();
    if (!this.state.stalled) this.set({ stalled: true });
    // 重开尾巴：卡住的那次请求作废，新尾巴连上即补拉、带回服务端状态（onopen → replay）。再过 STALL_MS 还没消息就再来一次。
    this.reconnect();
    this.watchStall();
  }

  /** 关掉当前尾巴马上重开（不等退避）；新尾巴的第一次回应才算收到消息。卡住的补拉、重放一并作废（tailGeneration）：
   *  新尾巴连上照常从事件记录补齐、带回服务端状态。 */
  private reconnect() {
    if (this.reopenTimer) {
      clearTimeout(this.reopenTimer);
      this.reopenTimer = null;
    }
    if (this.replayRetryTimer) {
      clearTimeout(this.replayRetryTimer);
      this.replayRetryTimer = null;
    }
    this.source?.close();
    this.source = null;
    this.tailGeneration++;
    this.replayInFlight = null;
    this.rerunNeeded = false;
    this.replaying = false;
    this.backfilling = false;
    this.pendingTail = [];
    this.lastContact = Date.now();
    this.openTail(false);
  }

  private fold(event: FeedEvent) {
    const before = this.state;
    const next = applyEvent(before, event);
    if (next === before) return;
    this.mergeProjection(next);
    if (event.type === "user.message" && this.retracted.size && next.echoedOptimistic === before.echoedOptimistic) this.landedLate(event);
    // Live: answer a custom tool as soon as the session parks on it — after
    // the rest of this batch folds (a poll page delivers its events in one
    // synchronous loop, and the answer is often later in the same page).
    // During replay the answer is usually already later in the log, so
    // wait for the fold to finish (replay() calls this once at the end).
    if (!this.replaying) this.queueAnswers();
  }

  /** 一条没对上本页气泡的用户消息：是不是之前撤回、放回输入框的那一条（服务端其实收下了）。是就告诉页面撤掉放回的。 */
  private landedLate(event: Extract<FeedEvent, { type: "user.message" }>) {
    const full = event.content.map((block) => (block.type === "text" ? block.text : "")).join("").trim();
    const typed = this.retracted.get(full);
    if (typed === undefined) return;
    this.retracted.delete(full);
    for (const listener of this.delivered) listener(typed);
  }

  /** 撤回、放回输入框的那一条后来到了服务端（回声晚到）：listener 收到用户当时打的字。返回取消订阅。 */
  onDelivered = (listener: (text: string) => void): (() => void) => {
    this.delivered.add(listener);
    return () => {
      this.delivered.delete(listener);
    };
  };

  private queueAnswers() {
    if (this.answerQueued) return;
    this.answerQueued = true;
    queueMicrotask(() => {
      this.answerQueued = false;
      if (!this.disposed && !this.replaying) this.answerPendingCustomTools();
    });
  }

  /** 服务端读回的此刻运行状态。askedAt：这次请求发出的时刻（本页时钟）；在它之后本页自己改过运行状态就不用这份。 */
  private applyRun(run: RunStatus, askedAt: number) {
    if (this.disposed || askedAt < this.localChangeAt || this.unacked > 0) return;
    const next = applyRunStatus(this.state, run);
    if (next === this.state) return; // 这期间到过更新的状态事件：以事件为准
    this.mergeProjection(next);
    noteRun(this.sessionId, run); // 侧栏同一个对话的状态点跟着改
    if (!this.replaying) this.queueAnswers();
  }

  // -------------------------------------------------------------- lifecycle
  /** Attach: open the tail. The history replay is driven by the tail's own
   *  lifecycle — onopen backfills (anchored to the connect moment, so it
   *  also covers everything missed while detached), onerror replays while
   *  disconnected — so an eager replay here would just fetch the whole log
   *  a second time. Idempotent, and a no-op while a reopen is backing off
   *  (the runtime hook re-renders and calls this on every state change;
   *  without the reopenTimer check a failing stream route would reconnect
   *  in a tight loop). */
  attach(): void {
    if (this.disposed || this.source || this.reopenTimer) return;
    if (this.state.isDead) return; // nothing left to stream or replay
    this.openTail();
  }

  /** Detach: close the tail but keep folded state for a quick return. Also
   *  cancels the pending reopen and replay-retry timers, or a backgrounded
   *  thread would quietly keep reconnecting and refetching in the dark. */
  detach(): void {
    if (this.reopenTimer) {
      clearTimeout(this.reopenTimer);
      this.reopenTimer = null;
    }
    if (this.replayRetryTimer) {
      clearTimeout(this.replayRetryTimer);
      this.replayRetryTimer = null;
    }
    this.source?.close();
    this.source = null;
    // No tail: nothing left to buffer for, and everything buffered is in
    // the persisted log — the reattach backfill refetches it.
    this.backfilling = false;
    this.pendingTail = [];
    this.set({ isTailing: false, isSynced: false });
    this.watchStall();
  }

  dispose(): void {
    this.disposed = true;
    this.detach();
    if (this.confirmTimer) clearTimeout(this.confirmTimer);
    if (this.reopenTimer) clearTimeout(this.reopenTimer);
    if (this.stallTimer) clearTimeout(this.stallTimer);
    if (this.answerRetryTimer) clearTimeout(this.answerRetryTimer);
    if (this.replayRetryTimer) clearTimeout(this.replayRetryTimer);
    this.listeners.clear();
    this.delivered.clear();
  }

  private openTail(restart = true) {
    const source = new PollingSource(this.sessionId);
    this.source = source;
    // 服务端的任何消息都算还连着；回到前台重新计时（同一刻尾巴补拉一次）
    source.onalive = () => {
      if (this.source === source) this.touch();
    };
    source.onwake = () => {
      if (this.source === source) this.touch();
    };
    // 接上尾巴（打开、切回对话）从此刻计时；看门时的重开不算，要等到真收到消息
    if (restart) this.touch();
    // 尾巴在某个时刻（回到前台、恢复、联网、重连、点停止）带回的状态。整段重放还没并完时不用：那次重放自己带一份，
    // 而这页事件此刻还压在缓冲里，先用状态会被随后并进来的事件顺序搅乱。
    source.onstatus = (run, askedAt) => {
      if (this.source !== source || !this.replayed || this.backfilling) return;
      this.applyRun(run, askedAt);
    };
    source.onopen = () => {
      this.reopenDelay = 1000; // healthy again: reset backoff
      // The stream only delivers events emitted after it attached; anything
      // from before this moment must come from the log. Buffer live events
      // until that backfill lands (this also covers EventSource's silent
      // auto-reconnects, which reuse the same source object).
      this.backfilling = true;
      void this.replay();
      this.set({ isTailing: true, transportError: null });
    };
    source.onmessage = (msg) => {
      if (this.source === source) this.touch();
      let envelope: RelayEnvelope;
      try {
        envelope = JSON.parse(msg.data) as RelayEnvelope;
      } catch {
        return;
      }
      if (envelope.kind !== "event") {
        this.set({ transportError: envelope.message });
        return;
      }
      // Hold live events until the pending backfill has folded history, so
      // the two sources can't interleave out of order (see pendingTail).
      if (!this.replayed || this.backfilling) this.pendingTail.push(envelope.event);
      else this.fold(envelope.event);
    };
    source.onerror = () => {
      if (this.source !== source) return; // 已被重开替下的旧尾巴
      this.set({ isTailing: false });
      if (this.disposed) return;
      // A dead session's endpoints 404 forever: reconnecting or replaying
      // would just hammer them. The projection already says so; stop here.
      if (this.state.isDead) {
        if (this.source === source) this.source = null;
        source.close();
        return;
      }
      // Anything emitted while disconnected is in the persisted log; re-fold
      // it (dedupe by event id makes the overlap harmless).
      void this.replay();
      // A transient hiccup: EventSource reconnects by itself. But a non-200
      // or non-SSE response (our route's JSON 502/404) hard-closes it with no
      // auto-reconnect; if we don't reopen, the live tail is dead for good.
      if (source.readyState === PollingSource.CLOSED && this.source === source) {
        this.source = null;
        source.close();
        this.scheduleReopen();
      }
    };
  }

  private scheduleReopen() {
    if (this.reopenTimer || this.disposed || this.state.isDead) return;
    const delay = this.reopenDelay;
    this.reopenDelay = Math.min(delay * 2, 30_000);
    this.reopenTimer = setTimeout(() => {
      this.reopenTimer = null;
      if (this.disposed || this.source || this.state.isDead) return;
      this.openTail(); // onopen (or onerror) drives the replay
    }, delay);
  }

  /** Fold the persisted event log. Safe to call any time: concurrent calls
   *  never fold twice at once. A call that arrives during a fetch queues one
   *  more pass afterward, because that fetch's snapshot may predate a newer
   *  disconnect and miss events emitted during it. */
  replay(): Promise<void> {
    if (this.replayInFlight) {
      this.rerunNeeded = true;
      return this.replayInFlight;
    }
    // 看门重开尾巴（reconnect）后，之前卡住的那次重放作废：它回来时什么都不做，也不占着「正在重放」
    const generation = this.tailGeneration;
    const run: Promise<void> = (async () => {
      do {
        this.rerunNeeded = false;
        await this.doReplay(generation);
      } while (this.rerunNeeded && !this.disposed && generation === this.tailGeneration);
    })().finally(() => {
      if (this.replayInFlight === run) this.replayInFlight = null;
    });
    this.replayInFlight = run;
    return run;
  }

  private async doReplay(generation = this.tailGeneration): Promise<void> {
    const stale = () => generation !== this.tailGeneration;
    this.replaying = true;
    try {
      // 整段重放（打开、重进对话、重连）都带一份服务端读回的运行状态：并完历史就按它定在回复 / 等允许 / 已结束，
      // 漏掉的「结束」、去重定住的旧状态、本页乐观标的「在回复」都由它校正（只并没见过的事件做不到）。
      const askedAt = Date.now();
      const body = await listEvents(this.sessionId, undefined, true);
      if (stale()) return;
      for (const event of body.events) this.fold(event);
      if (body.run) this.applyRun(body.run, askedAt);
      this.replayed = true;
      this.replayRetryDelay = 2000; // loaded: reset the retry backoff
      // History is in: fold whatever the tail buffered during the fetch, in
      // arrival order, on top of it, and stop buffering — unless another
      // replay was requested while this one ran (rerunNeeded): that request
      // may cover events this fetch predates, so hold until its pass lands.
      if (!this.rerunNeeded) {
        this.backfilling = false;
        const buffered = this.pendingTail;
        this.pendingTail = [];
        for (const event of buffered) this.fold(event);
      }
      // 运行状态（applyRun）已在上面并进去，核对完的标记最后才置：读到「核对过」时在不在跑一定是核对后的
      this.set({ isLoaded: true, transportError: null, isSynced: this.source !== null });
    } catch (err) {
      if (stale()) return;
      this.set({ isLoaded: true, transportError: describe(err) });
      // The buffer stays intact (folding it over a projection that's still
      // missing its backfill would bake in the misorder the buffer exists
      // to prevent). Nothing else retries a failed replay while the tail is
      // healthy, so do it here — with the same backoff as the tail reopen,
      // and only while attached (detach cancels the timer; attach retries).
      if ((!this.replayed || this.backfilling) && !this.disposed && !this.state.isDead && !this.replayRetryTimer && this.source) {
        const delay = this.replayRetryDelay;
        this.replayRetryDelay = Math.min(delay * 2, 30_000);
        this.replayRetryTimer = setTimeout(() => {
          this.replayRetryTimer = null;
          if ((!this.replayed || this.backfilling) && !this.disposed && !this.state.isDead) void this.replay();
        }, delay);
      }
    } finally {
      // The fold settled one way or the other: whatever is *still* pending
      // really is owed (e.g. the tab closed before we answered a chart).
      if (!stale()) {
        this.replaying = false;
        this.answerPendingCustomTools();
      }
    }
  }

  // --------------------------------------------------------------- actions
  // onShown：真正的气泡放进对话的那一刻，带上它的编号（页面等它真画出来再撤掉发送前先显示的临时气泡，pending-send.ts）
  async sendMessage(
    text: string,
    files: Array<{ filename: string; mountPath: string }>,
    onShown?: (messageId: string) => void,
  ) {
    // Make sure the tail is up before the turn starts producing events.
    this.attach();
    // And that stored history is fully folded first: an optimistic bubble
    // sitting at the end while old messages fold in beneath it could be
    // mistaken for one of their echoes. That means both the first load AND
    // any reconnect backfill still in flight (`backfilling`) — folding the
    // bubble over a projection missing its gap misorders it permanently.
    // Wait on the current pass (usually already finished) — and if it
    // FAILED (doReplay resolves either way), refuse the send rather than
    // fold the bubble into a projection with a hole under it.
    if (!this.replayed || this.backfilling) {
      await (this.replayInFlight ?? this.replay());
      if (!this.replayed || this.backfilling) {
        throw new Error("The conversation history hasn't loaded yet; try again in a moment.");
      }
    }

    // Show the bubble and the "working" state now; the echoed user.message
    // replaces the optimistic bubble (same id, see reducer), and the real
    // status events take over the running flag.
    const n = ++this.optimistic;
    const optimisticId = `optimistic-${n}`;
    const now = new Date().toISOString();
    this.fold({
      type: "user.message",
      id: optimisticId,
      // Same builder the server uses, so the echo's text is byte-identical
      // and the reducer reconciles it in place instead of adding a bubble.
      content: [{ type: "text", text: messageText(text, files) }],
      processed_at: now,
    } as unknown as StreamEvent);
    this.fold({
      type: "session.status_running",
      id: `optimistic-run-${n}`,
      processed_at: now,
    } as unknown as StreamEvent);
    this.localChangeAt = Date.now();
    this.touch(); // 从发出计时：空闲时补拉得慢，不能把那段间隔算成断了
    onShown?.(optimisticId);

    this.unacked++;
    const full = messageText(text, files);
    const post = postMessage(this.sessionId, full);
    this.sendInFlight = post;
    try {
      await post;
    } catch (err) {
      // 回声已经到了：服务端早已收下（回复可能都在出了），没回来的只是这次请求的回应（中途断网）。这条算发出了：
      // 不撤气泡、不报发送失败，输入框也不放回（第 10 轮 E30：断网点停止约 2.4 秒后那句话被放回输入框，再按发送就重复一条）
      if (!this.state.echoedOptimistic.has(optimisticId)) {
        // Nothing was sent: pull the bubble back out of the transcript so it
        // never reads as delivered. (Replay can't do this; it only adds.)
        this.rollbackSend(optimisticId);
        // 一时的失败（断网、网关）看不出服务端收没收下：先当没发出（放回输入框，不丢字），回声若晚到再告诉页面撤掉放回的
        if (transientError(err)) this.retracted.set(full.trim(), text);
        throw err;
      }
    } finally {
      this.unacked--;
      if (this.sendInFlight === post) this.sendInFlight = null;
    }
    // 收下之后才要的状态才算数（applyRun 比的就是这个时刻）
    this.localChangeAt = Date.now();
    this.source?.nudge();
  }

  private rollbackSend(optimisticId: string) {
    this.mergeProjection(evictOptimisticMessage(this.state, optimisticId));
  }

  /** The Allow/Deny click. Optimistically settles the gate, then batches. */
  respondToApproval(toolUseId: string, approved: boolean, reason?: string) {
    // Already queued for this batch: a double-click must not send the
    // same confirmation twice.
    if (this.confirmQueue.some((c) => c.tool_use_id === toolUseId)) return;

    // Optimistic: fold a synthetic confirmation so the buttons disappear at
    // once. Its id is unique per click (a retry after a restored gate must
    // fold again; a reused id would already be in `seen` and dedupe out).
    // The real echoed user.tool_confirmation re-confirms the same state.
    this.fold({
      type: "user.tool_confirmation",
      id: `optimistic-confirm-${++this.optimistic}`,
      tool_use_id: toolUseId,
      result: approved ? "allow" : "deny",
      ...(reason ? { deny_message: reason } : {}),
      processed_at: new Date().toISOString(),
    } as unknown as StreamEvent);

    this.unacked++; // 答复送达前读回的状态不用（flushConfirmations 收尾时减回）
    this.confirmQueue.push({
      tool_use_id: toolUseId,
      result: approved ? "allow" : "deny",
      ...(!approved && reason ? { deny_message: reason } : {}),
    });
    if (this.confirmTimer) clearTimeout(this.confirmTimer);
    this.confirmTimer = setTimeout(() => void this.flushConfirmations(), 250);

    // That was the last open gate: the session is about to resume. Keep the
    // composer busy until the real status_running lands, or the window
    // between click and echo briefly accepts a message mid-resume (and the
    // action bar flashes). A failed POST undoes this via restoreGates.
    this.localChangeAt = Date.now();
    this.touch(); // 等用户点的那段时间不算没消息
    const next = markResuming(this.state, toolUseId);
    if (next !== this.state) this.mergeProjection(next);
  }

  private async flushConfirmations() {
    const confirmations = this.confirmQueue;
    this.confirmQueue = [];
    this.confirmTimer = null;
    if (confirmations.length === 0) return;
    try {
      const reply = await confirmTools(this.sessionId, confirmations);
      this.localChangeAt = Date.now(); // 同 sendMessage：服务端收下答复之前要的状态不算
      // 送到了：点完那一行不再淡着（以后到的结果若与本页点的不同，reducer 照结果改）
      this.mergeProjection(clearSending(this.state, confirmations.map((c) => c.tool_use_id)));
      // 本站拦下的调用此刻在本站的状态（服务端不再等的不转给引擎，第 11 轮 A4）：照它当场定下卡片——别的设备上已停止的旧卡片
      // 写「已取消」、不放回按钮；执行完的写「允许」。同推来的执行记录（同一个编号，先到后到都只折一次）
      for (const [id, status] of Object.entries(reply?.runs ?? {})) {
        if (status !== "awaiting_approval") this.fold(toolRunEvent(id, status));
      }
      this.source?.nudge();
    } catch (err) {
      // The server never took our answer, so the session is still parked.
      // Undo the optimistic settle by putting those gates back (a replay
      // can't: the parked status_idle is already in `seen` and dedupes out).
      // 卡片照原样回来，按钮上方写一行原因（同对话里发不出去那一行：服务端原文，没有就用通用的一句），再点重试
      // 只写卡片上的原因，不设 transportError：确认没送到不等于连接断了，顶上不再同时出现「正在重新连接…」（第 10 轮审查）；
      // 真断了由推送 / 补拉自己报。服务端早已不在等的门放不回来，也不再标「发送中」
      const ids = confirmations.map((c) => c.tool_use_id);
      this.mergeProjection(clearSending(restoreGates(this.state, ids, shownError(err)), ids));
    } finally {
      this.unacked -= confirmations.length;
    }
  }

  /** Custom tools are Pearnly business tools: the server executes them under
   *  the signed-in user (pearnly_core.assistant.tools); we only nudge it the
   *  moment the reducer flags one pending. A
   *  failed answer must be retried: the session emits nothing while it waits
   *  on us, so no later fold would ever bring us back here. */
  private answering = new Set<string>();
  private answerAttempts = new Map<string, number>();
  // Earliest wall-clock time each id may be tried again. This is what makes
  // the backoff real: replay's finally also calls us on every replay, and
  // without this gate a burst of reconnect-driven replays would burn every
  // attempt in about a second and then give up for good.
  private retryAfter = new Map<string, number>();
  private answerRetryTimer: ReturnType<typeof setTimeout> | null = null;
  private answerPendingCustomTools() {
    const now = Date.now();
    for (const id of this.state.pendingCustomTools) {
      if (this.answering.has(id) || this.state.answeredCustomTools.has(id)) continue;
      if ((this.retryAfter.get(id) ?? 0) > now) continue; // still backing off
      const attempt = (this.answerAttempts.get(id) ?? 0) + 1;
      if (attempt > 5) continue; // gave up; the banner tells the user
      this.answerAttempts.set(id, attempt);
      this.answering.add(id);
      void runTool(this.sessionId, id)
        .then((reply) => {
          if (reply.status === "awaiting_approval") {
            // Held for the user's Allow/Deny: show the gate with what it changes, don't treat it as answered.
            this.answerAttempts.delete(id);
            this.retryAfter.delete(id);
            this.mergeProjection(gateCustomTool(this.state, id, { ask: reply.ask, preview: reply.preview }));
            return;
          }
          this.source?.nudge();
          this.answerAttempts.delete(id);
          this.retryAfter.delete(id);
          // Fold our own answer so a reattach never re-answers it — unless
          // the real answer already folded (its result is the record; our
          // empty one would blank the card).
          if (!this.state.answeredCustomTools.has(id)) {
            this.fold({
              type: "user.custom_tool_result",
              id: `optimistic-result-${id}`,
              custom_tool_use_id: id,
              content: [],
              is_error: false,
              processed_at: new Date().toISOString(),
            } as unknown as StreamEvent);
          }
          // Answering the last owed tool resumes the session, exactly like
          // the last Allow/Deny click: keep the composer busy until the
          // real status_running lands (see respondToApproval). Only while the
          // session is still parked on it: a reply that comes back after the
          // turn already moved on or ended must not re-mark it running
          // (2026-10-13 iPhone: back from the background, one poll page held
          // the whole rest of the turn; this late reply left it "replying").
          const next = markResuming(this.state, id);
          if (next !== this.state) {
            this.localChangeAt = Date.now();
            this.mergeProjection(next);
          }
        })
        .catch((err) => {
          this.set({ transportError: describe(err) });
          // Back off, then try again: the session is parked until we succeed.
          const delay = Math.min(1500 * 2 ** (attempt - 1), 15_000);
          this.retryAfter.set(id, Date.now() + delay);
          if (!this.disposed && !this.answerRetryTimer) {
            this.answerRetryTimer = setTimeout(() => {
              this.answerRetryTimer = null;
              this.answerPendingCustomTools();
            }, delay);
          }
        })
        .finally(() => this.answering.delete(id));
    }
  }

  /** 停止按钮。无论打断成没成，都向服务端核对一次此刻的状态：本页卡在「在回复」而服务端早已空闲时，点一下停止就恢复发送。
   *  刚发出的那条还在路上：等它有了结果再打断（先到的打断落了空，服务端随后收下这条、回复照样出来）；它没发出去也照常核对状态。 */
  async interrupt(): Promise<void> {
    try {
      await this.sendInFlight?.catch(() => undefined);
      await interruptSession(this.sessionId);
    } finally {
      this.refreshStatus();
    }
  }

  /** 向服务端要一份此刻的运行状态：尾巴开着就补拉一次带上（一次请求），没开着就整段重放（也带）。 */
  refreshStatus(): void {
    if (this.disposed || this.state.isDead) return;
    if (this.source) this.source.refresh();
    else void this.replay();
  }

  get busy(): boolean {
    return isBusy(this.state);
  }
}

// ------------------------------------------------------------------ registry
// Controllers outlive the component that mounts them: switching threads and
// coming back is instant, and an approval waiting in a background thread is
// still there when you return.

const registry = new Map<string, SessionController>();

export function getSessionController(sessionId: string): SessionController {
  let controller = registry.get(sessionId);
  if (!controller) {
    controller = new SessionController(sessionId);
    registry.set(sessionId, controller);
  }
  return controller;
}

export function dropSessionController(sessionId: string): void {
  const controller = registry.get(sessionId);
  controller?.dispose();
  registry.delete(sessionId);
}

// ---------------------------------------------------------------- helpers
function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
