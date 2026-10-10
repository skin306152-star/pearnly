// 实时尾巴的第一版：按间隔拉「上次之后的新事件」，接口形状仿 EventSource，session-controller 不用改流程。
//
// 为什么先轮询：站点是 Frappe（gunicorn 同步进程），长连接转发 Managed Agents 的事件流会一直占着一个
// 进程，并发一上来就不够用。改用站点实时通道转发是 ADR-019 列明的待验证项；验证前用轮询，代价是
// 回复整段到达、不是逐字出现。会话在跑时拉得勤，空闲时放慢，页面在后台时停。

import { listEvents } from "./api";
import type { RelayEnvelope, StreamEvent } from "./events";

const FAST_MS = 1200;
const SLOW_MS = 6000;

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

  constructor(sessionId: string) {
    this.sessionId = sessionId;
    this.schedule(0);
  }

  close() {
    this.readyState = PollingSource.CLOSED;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
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
      for (const event of page.events) this.emit(event);
      this.schedule(this.running ? FAST_MS : SLOW_MS);
    } catch {
      if (this.readyState === PollingSource.CLOSED) return;
      this.onerror?.();
      this.schedule(SLOW_MS);
    }
  }

  private emit(event: StreamEvent) {
    const id = (event as { id?: string }).id;
    if (!id || this.seen.has(id)) return;
    this.seen.add(id);
    this.lastId = id;
    if (event.type === "session.status_running") this.running = true;
    if (event.type === "session.status_idle" || event.type === "session.status_terminated") this.running = false;
    const envelope: RelayEnvelope = { kind: "event", event };
    this.onmessage?.({ data: JSON.stringify(envelope) });
  }

  /** 本页刚发出消息 / 批准：马上拉一次，不等慢节奏。 */
  nudge() {
    this.running = true;
    if (this.timer) clearTimeout(this.timer);
    this.schedule(300);
  }
}
