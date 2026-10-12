// 会话的实时尾巴，接口形状仿 EventSource，session-controller 不用改流程。两路来源，按事件编号去重：
//
// - 推送（主）：服务端转发线程接引擎的事件流，经站点实时通道推过来（realtime.ts），产生即到。
// - 补拉（兜底）：按间隔拉「上次之后的新事件」。实时通道连着时只低频补拉（推送丢了、进程重启时补齐）；
//   断开时退回高频轮询。引擎的「列出事件」偶尔单次要 30 多秒，所以不能靠它出回复（2026-10-10 实测）。
// 页面在后台时停止补拉。
// 本站提醒（pearnly.notice）两路都会来，同样按编号去重；但它不在引擎的事件记录里，不能当补拉锚点。

import { listEvents } from "./api";
import { type FeedEvent, isLocalEvent, type RelayEnvelope } from "./events";
import { isConnected, subscribe } from "./realtime";

const FAST_MS = 1200;
const SLOW_MS = 6000;
// 实时通道连着时的补拉间隔
const PUSHED_RUNNING_MS = 8000;
const PUSHED_IDLE_MS = 30000;
// 一轮结束后很快补拉一次，对齐推送可能漏掉的事件
const SETTLE_MS = 1500;

export class PollingSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readyState = PollingSource.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  private readonly sessionId: string;
  private lastId: string | undefined;
  private seen = new Set<string>();
  private running = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly unsubscribe: () => void;

  constructor(sessionId: string) {
    this.sessionId = sessionId;
    this.unsubscribe = subscribe(
      sessionId,
      (event) => this.pushed(event),
      (connected) => {
        // 断开了：马上转回高频补拉；连上了：按低频节奏走。
        if (!connected) this.reschedule(0);
      },
    );
    this.schedule(0);
  }

  close() {
    this.readyState = PollingSource.CLOSED;
    this.unsubscribe();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private interval(): number {
    if (isConnected()) return this.running ? PUSHED_RUNNING_MS : PUSHED_IDLE_MS;
    return this.running ? FAST_MS : SLOW_MS;
  }

  private reschedule(delay: number) {
    if (this.timer) clearTimeout(this.timer);
    this.schedule(delay);
  }

  /** 推送来的事件：照常交给会话，但不拿它当补拉的锚点（补拉锚点只用从事件记录里读到的编号）。 */
  private pushed(event: FeedEvent) {
    if (this.readyState === PollingSource.CLOSED) return;
    this.emit(event, false);
    const kind = event.type as string;
    const stop = (event as { stop_reason?: { type?: string } }).stop_reason?.type;
    if ((kind === "session.status_idle" && stop !== "requires_action") || kind === "session.status_terminated") {
      this.reschedule(SETTLE_MS);
    }
  }

  private schedule(delay: number) {
    if (this.readyState === PollingSource.CLOSED) return;
    this.timer = setTimeout(() => void this.tick(), delay);
  }

  private async tick() {
    if (document.visibilityState === "hidden") return this.schedule(SLOW_MS);
    try {
      const page = await listEvents(this.sessionId, this.lastId);
      if (this.readyState === PollingSource.CLOSED) return;
      if (this.readyState === PollingSource.CONNECTING) {
        this.readyState = PollingSource.OPEN;
        this.onopen?.();
      }
      for (const event of page.events) this.emit(event, true);
      this.schedule(this.interval());
    } catch {
      if (this.readyState === PollingSource.CLOSED) return;
      // 同 EventSource 自动重连：先回到「连接中」，下一次取到就再报一次 onopen（会话据此补拉、收起「正在重新连接…」）。
      // 以前停在「已连上」，恢复后不再报 onopen，提示一直挂着、错过的文件卡片也不补（2026-10-10 测试）。
      this.readyState = PollingSource.CONNECTING;
      this.onerror?.();
      this.schedule(SLOW_MS);
    }
  }

  private emit(event: FeedEvent, anchor: boolean) {
    const id = (event as { id?: string }).id;
    const kind = event.type as string;
    if (kind === "event_start" || kind === "event_delta") {
      // 逐字预览只在推送里有、没有自己的编号，也不入事件记录：直接交给会话，由正式事件到达时收口。
      this.onmessage?.({ data: JSON.stringify({ kind: "event", event } satisfies RelayEnvelope) });
      return;
    }
    if (!id || this.seen.has(id)) return;
    this.seen.add(id);
    // 锚点只用引擎事件：本站编号服务端在事件记录里找不到，每轮都会退回整段重放
    if (anchor && !isLocalEvent(event)) this.lastId = id;
    if (event.type === "session.status_running") this.running = true;
    if (event.type === "session.status_idle" || event.type === "session.status_terminated") this.running = false;
    const envelope: RelayEnvelope = { kind: "event", event };
    this.onmessage?.({ data: JSON.stringify(envelope) });
  }

  /** 本页刚发出消息 / 批准：回复走推送；没连上实时通道时马上补拉一次，不等慢节奏。 */
  nudge() {
    this.running = true;
    this.reschedule(isConnected() ? PUSHED_RUNNING_MS : 300);
  }
}
