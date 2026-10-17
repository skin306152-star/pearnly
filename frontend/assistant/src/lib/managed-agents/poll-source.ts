// 会话的实时尾巴，接口形状仿 EventSource，session-controller 不用改流程。两路来源，按事件编号去重：
//
// - 推送（主）：服务端转发线程接引擎的事件流，经站点实时通道推过来（realtime.ts），产生即到。
// - 补拉（兜底）：按间隔拉「上次之后的新事件」。实时通道连着时只低频补拉（推送丢了、进程重启时补齐）；
//   断开时退回高频轮询。引擎的「列出事件」偶尔单次要 30 多秒，所以不能靠它出回复（2026-10-10 实测）。
// 页面在后台时停止补拉。
// 本站提醒（pearnly.notice）两路都会来，同样按编号去重；但它不在引擎的事件记录里，不能当补拉锚点。
//
// 回到前台、从后台缓存恢复（pageshow）、重新联网、实时通道断开后重连，这几个时刻马上补拉一次并顺带要一份服务端读回的
// 运行状态（onstatus）：在后台时漏掉的「这一轮结束」不靠补齐事件来猜（2026-10-13 iPhone：主屏幕图标打开、锁屏收到推送，
// 回来后一直显示在回复、发不了新消息）。一个时刻只读一次状态；不另开定时，取不到就跟着原有的补拉节奏再要。
//
// 推送与补拉之间的缺口（第 11 轮 F39）：实时通道断开又连上（断网时通道迟早断开），或补拉失败而通道当时也不在线，这段时间产生的
// 事件只在事件记录里，而通道一恢复新事件就推来了。先交推来的，顺序就永久错了（断网时发出的那句的回声随补拉晚到，排在已推来的回复
// 后面）。所以缺口一开始，推来的先压着（held）；等一次在缺口开始之后发出的补拉成功、这页事件交完，再按到达先后交压着的（已在这页
// 里的按编号去重）。不多发请求：缺口由原有的那次补拉（失败后的下一次、联网 / 重连时的那次）收口。
// 通道在线时补拉失败（网关 502、列事件接口一时出错）不开缺口：推来的走同一条活着的连接，不会漏推，照常当场交——推送是主路，
// 回复不能被补拉卡住（第 11 轮审查：以前补拉失败一次，推来的回复要压到下一次补拉成功，接口一直坏就一直不出）。通道其实已断、
// 只是还没报断开的，报断开后再连上照样开缺口。
// 服务端这次没读到引擎（列出事件超时，回 200 但标 partial、只带本站提醒）不算补齐：照常交提醒、算有回应，但不动锚点、不收口、
// 不交压着的，同补拉失败一样缺口开着时 SLOW_MS 后再取（第 11 轮合并审查：以前这种空页把缺口收了，压着的推送先于缺口里的引擎
// 事件交出，顺序永久错乱）。缺口开着而引擎一直读不到，回复就一直压着：宁可晚、不可乱。
// 尾巴建起来时实时通道不在线（还没连上、或看门重开 / 切回对话时恰好断网），之后第一次连上同样算重连、开缺口：这之前推不到的
// 事件只在事件记录里。

import { listEvents } from "./api";
import { type FeedEvent, isLocalEvent, type RelayEnvelope, type RunStatus } from "./events";
import { isConnected, onHeartbeat, subscribe } from "./realtime";

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
  /** 服务端读回的此刻运行状态；askedAt 是这次请求发出的时间（本页时钟），会话据此不让它盖过之后本页自己的操作。 */
  onstatus: ((run: RunStatus, askedAt: number) => void) | null = null;
  /** 收到服务端的任何消息：补拉有了回应（哪怕没有新事件）、推来事件、实时通道的心跳（会话据此分辨在跑还是断了）。 */
  onalive: (() => void) | null = null;
  /** 页面回到前台（同一刻补拉一次）：会话从这一刻重新计时，后台那段不算没消息。 */
  onwake: (() => void) | null = null;

  private readonly sessionId: string;
  private lastId: string | undefined;
  private seen = new Set<string>();
  private running = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  // 一次补拉进行中：期间要求的下一次（推送收尾、时刻）记下最早的那个，取完再排，不并行开第二条补拉
  private inFlight = false;
  private queued: number | null = null;
  // 下一次补拉顺带要运行状态（某个时刻要求的，取到为止）
  private wantStatus = false;
  // 实时通道断过（或尾巴建起来时就不在线，见构造）：再连上算重连
  private realtimeLost = false;
  // 缺口（见文件头）：开着时推来的先压着；gapSeq 每开一次加一，补拉发出时记下，回来时没再开过新的缺口才收口
  private gap = false;
  private gapSeq = 0;
  private held: FeedEvent[] = [];
  private readonly unsubscribe: () => void;
  private readonly offHeartbeat: () => void;
  private readonly onVisible = () => {
    if (document.visibilityState !== "visible") return;
    this.onwake?.();
    this.refresh();
  };
  private readonly onPageShow = (event: { persisted?: boolean }) => {
    if (event.persisted) this.refresh();
  };
  private readonly onOnline = () => this.refresh();

  constructor(sessionId: string) {
    this.sessionId = sessionId;
    // 建起来时通道不在线：这之前推不到的只在事件记录里，第一次连上也按重连开缺口（见文件头）。在 subscribe（会建连接）之前看
    this.realtimeLost = !isConnected();
    this.unsubscribe = subscribe(
      sessionId,
      (event) => this.pushed(event),
      (connected) => {
        // 断开了：马上转回高频补拉；断过又连上：补拉一次并核对状态（断线期间的推送没了）
        if (!connected) {
          this.realtimeLost = true;
          this.reschedule(0);
        } else if (this.realtimeLost) {
          this.realtimeLost = false;
          this.openGap();
          this.refresh();
        } else if (this.gap) {
          // 缺口已开着（补拉失败时还没连上）又连上：重开一次（在途那次补拉发出时推送还不通，它回来不算补齐），马上补拉收口
          this.openGap();
          this.refresh();
        }
      },
    );
    // 实时通道的心跳（服务端约每 25 秒一次）也算收到服务端的消息：补拉一次要很久时，靠它分辨「慢」和「断」
    this.offHeartbeat = onHeartbeat(() => {
      if (this.readyState !== PollingSource.CLOSED) this.onalive?.();
    });
    document.addEventListener("visibilitychange", this.onVisible);
    window.addEventListener("pageshow", this.onPageShow);
    window.addEventListener("online", this.onOnline);
    this.schedule(0);
  }

  close() {
    this.readyState = PollingSource.CLOSED;
    this.held = [];
    this.unsubscribe();
    this.offHeartbeat();
    document.removeEventListener("visibilitychange", this.onVisible);
    window.removeEventListener("pageshow", this.onPageShow);
    window.removeEventListener("online", this.onOnline);
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** 一个时刻（回到前台、页面恢复、联网、重连、点了停止）：马上补拉一次，顺带要一份服务端读回的运行状态。 */
  refresh() {
    if (this.readyState === PollingSource.CLOSED) return;
    this.wantStatus = true;
    this.reschedule(0);
  }

  private interval(): number {
    if (isConnected()) return this.running ? PUSHED_RUNNING_MS : PUSHED_IDLE_MS;
    return this.running ? FAST_MS : SLOW_MS;
  }

  private reschedule(delay: number) {
    if (this.inFlight) {
      this.queued = Math.min(this.queued ?? delay, delay);
      return;
    }
    if (this.timer) clearTimeout(this.timer);
    this.schedule(delay);
  }

  /** 缺口开始（实时通道重连；补拉失败且通道不在线）：之后推来的先压着，等下一次补拉补齐。通道在线时推来的不压。 */
  private openGap() {
    this.gap = true;
    this.gapSeq++;
  }

  /** 推送来的事件：照常交给会话，但不拿它当补拉的锚点（补拉锚点只用从事件记录里读到的编号）。缺口里推来的先压着（见文件头）。 */
  private pushed(event: FeedEvent) {
    if (this.readyState === PollingSource.CLOSED) return;
    if (this.gap) {
      this.held.push(event);
      this.onalive?.(); // 推来了就算还连着（压着不交，不是没收到）
    } else this.emit(event, false);
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
    this.timer = null;
    if (document.visibilityState === "hidden") return this.schedule(SLOW_MS);
    const withStatus = this.wantStatus;
    this.wantStatus = false;
    const askedAt = Date.now();
    const gapSeq = this.gapSeq;
    this.inFlight = true;
    let next: number;
    let failed = false;
    try {
      const page = await listEvents(this.sessionId, this.lastId, withStatus);
      if (this.readyState === PollingSource.CLOSED) return;
      this.onalive?.(); // 有回应就算连着，哪怕这页没有新事件
      if (this.readyState === PollingSource.CONNECTING) {
        this.readyState = PollingSource.OPEN;
        this.onopen?.();
      }
      // 服务端这次没读到引擎（partial）：这页只有本站提醒（不当锚点），不算补齐（见文件头）
      const partial = page.partial === true;
      for (const event of page.events) this.emit(event, !partial);
      // 缺口开始之后发出的这一页已补齐：按到达先后交压着的推送（这页里已有的按编号去掉），收口
      if (this.gap && gapSeq === this.gapSeq && !partial) {
        this.gap = false;
        const held = this.held;
        this.held = [];
        for (const event of held) this.emit(event, false);
      }
      // 先并完这页事件，再按状态校正（状态是服务端读完这页事件之后读的）。要了没取到（引擎一时读不到）就下一次再要。
      if (page.run) {
        this.running = page.run.status === "running" || page.run.status === "requires_action";
        this.onstatus?.(page.run, askedAt);
      } else if (withStatus) this.wantStatus = true;
      // 缺口没收口（没读到引擎）：同补拉失败，SLOW_MS 后再取，不等在线时的 8 / 30 秒
      next = partial && this.gap ? Math.min(SLOW_MS, this.interval()) : this.interval();
    } catch {
      if (this.readyState === PollingSource.CLOSED) return;
      if (withStatus) this.wantStatus = true;
      // 通道在线：推来的走活着的连接、不会漏，不压（见文件头）
      if (!isConnected()) this.openGap();
      // 同 EventSource 自动重连：先回到「连接中」，下一次取到就再报一次 onopen（会话据此补拉、收起「正在重新连接…」）。
      // 以前停在「已连上」，恢复后不再报 onopen，提示一直挂着、错过的文件卡片也不补（2026-10-10 测试）。
      this.readyState = PollingSource.CONNECTING;
      this.onerror?.();
      next = SLOW_MS;
      failed = true;
    } finally {
      this.inFlight = false;
    }
    // 期间要求的下一次（推送收尾、某个时刻）提前排上；刚失败的照旧退避，要的状态留到下一次
    const queued = this.queued;
    this.queued = null;
    this.schedule(failed || queued === null ? next : Math.min(queued, next));
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
