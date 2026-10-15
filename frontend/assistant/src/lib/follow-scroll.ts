// 回复在出时跟到底，用户往上翻就不动（教程第 12 条；同 Claude）。
//
// 组件库的对话视口用「这一轮从顶上开始」（turnAnchor="top"）：发出后把自己的消息推到顶上，下面留出空白给回复，回复没超出
// 一屏时它管位置。它不跟随超出一屏的回复，判断「在底部」也只容 1 像素（手机惯性滚动几乎停不准）。这里补上超出之后的跟随：
//   - 用户往上翻（滚轮向上、手指往下拖、按上翻键、拖滚动条向上）立刻停止跟随，不等滚动事件（内容同时在长，单看滚动位置分不清）；
//   - 回到离底部 NEAR_BOTTOM_PX 以内、且离上次往上翻已过 COOLDOWN_MS（惯性还在走的那一下不算），接着跟；
//   - 只在这一轮在跑时跟；新的一轮（用户又发了一条）从跟随开始；
//   - 组件库还在给这一轮留空白（顶部锚定的占位还有高度）时不插手，免得抢它把消息推到顶上的那一下。
//
// 打开一段对话（settle）停在最新一条：组件库只在消息第一次出现时滚到底一次，「这一轮从顶上开始」时它不再跟着内容长
// （autoScroll 关着），而打开后内容还会变高——消息用 content-visibility 先按估计高度排、滚到哪儿才量出真实高度，
// 正文排版、文件卡片、缩略图随后才到——于是停在中间（2026-10-15 三星 A14 实测 F11f）。打开后内容一变高就贴回最底下，
// 直到内容静下来 SETTLE_QUIET_MS（最多 SETTLE_MAX_MS），或用户一碰（滚轮、按下手指 / 鼠标、按键）就交还：
// 往上翻照旧立刻停、不拉回。
// 只看本页的滚动与尺寸，不发请求、不开定时。

export const NEAR_BOTTOM_PX = 48;
export const COOLDOWN_MS = 400;
export const SETTLE_QUIET_MS = 1500;
export const SETTLE_MAX_MS = 10_000;
const DRAG_PX = 4;
const UP_KEYS = new Set(["PageUp", "ArrowUp", "Home"]);

type Viewport = Pick<
  HTMLElement,
  "addEventListener" | "removeEventListener" | "scrollTop" | "scrollHeight" | "clientHeight" | "scrollTo" | "querySelector"
>;
type Observe = (target: unknown, onResize: () => void) => () => void;

function observeSize(target: unknown, onResize: () => void): () => void {
  if (typeof ResizeObserver === "undefined") return () => {};
  const observer = new ResizeObserver(() => onResize());
  observer.observe(target as Element);
  return () => observer.disconnect();
}

function editable(target: unknown): boolean {
  const el = target as { tagName?: string; isContentEditable?: boolean } | null;
  return !!el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || !!el.isContentEditable);
}

export class StreamFollower {
  following = true;
  private pausedAt = Number.NEGATIVE_INFINITY;
  // 打开对话后贴底：从哪一刻起、内容最近一次变高在哪一刻（-∞ = 不在贴底）
  private settleFrom = Number.NEGATIVE_INFINITY;
  private settleLast = Number.NEGATIVE_INFINITY;
  private touchY: number | null = null;
  private dragging = false;
  private lastTop: number;
  private readonly off: Array<() => void> = [];

  constructor(
    private readonly viewport: Viewport,
    content: unknown,
    private readonly live: () => boolean,
    private readonly now: () => number = () => Date.now(),
    observe: Observe = observeSize,
  ) {
    this.lastTop = viewport.scrollTop;
    // 只读事件（passive）：不拦浏览器自己的滚动
    const on = <E>(type: string, handle: (event: E) => void) => {
      const listener = handle as unknown as EventListener;
      viewport.addEventListener(type, listener, { passive: true });
      this.off.push(() => viewport.removeEventListener(type, listener));
    };
    on("wheel", (e: { deltaY: number }) => {
      this.unsettle();
      if (e.deltaY < 0) this.pause();
    });
    on("touchstart", (e: { touches: ArrayLike<{ clientY: number }> }) => {
      this.unsettle();
      this.touchY = e.touches[0]?.clientY ?? null;
    });
    on("touchmove", (e: { touches: ArrayLike<{ clientY: number }> }) => {
      const y = e.touches[0]?.clientY;
      if (y === undefined) return;
      // 手指往下拖 = 内容往上翻
      if (this.touchY !== null && y - this.touchY > DRAG_PX) this.pause();
      this.touchY = y;
    });
    const release = () => {
      this.touchY = null;
      this.dragging = false;
    };
    on("touchend", release);
    on("touchcancel", release);
    on("keydown", (e: { key: string; target: unknown }) => {
      this.unsettle();
      if (UP_KEYS.has(e.key) && !editable(e.target)) this.pause();
    });
    // 按在视口自己身上（不是里面的内容）= 在拖滚动条
    on("pointerdown", (e: { target: unknown }) => {
      this.unsettle();
      this.dragging = e.target === viewport;
    });
    on("pointerup", release);
    on("scroll", () => this.onScroll());
    this.off.push(observe(content, () => this.onResize()));
  }

  dispose() {
    this.off.splice(0).forEach((off) => off());
  }

  /** 新的一轮：从跟随开始。 */
  resume() {
    this.following = true;
    this.pausedAt = Number.NEGATIVE_INFINITY;
  }

  /** 打开了一段对话（换到另一段、或这段的消息刚取回）：贴到最底下，内容还在长就接着贴（见文件头）。 */
  settle() {
    this.resume();
    this.settleFrom = this.settleLast = this.now();
    this.toBottom();
  }

  /** 正在打开后的贴底：用户没碰过、内容最近还在变、没超过上限。 */
  get settling(): boolean {
    const now = this.now();
    return this.following && now - this.settleLast < SETTLE_QUIET_MS && now - this.settleFrom < SETTLE_MAX_MS;
  }

  private unsettle() {
    this.settleFrom = this.settleLast = Number.NEGATIVE_INFINITY;
  }

  private pause() {
    this.following = false;
    this.pausedAt = this.now();
    this.unsettle();
  }

  private distance(): number {
    const v = this.viewport;
    return v.scrollHeight - v.scrollTop - v.clientHeight;
  }

  /** 回到底部附近、冷却已过、手没按着：接着跟。 */
  private maybeResume() {
    if (this.following || this.touchY !== null || this.dragging) return;
    if (this.distance() <= NEAR_BOTTOM_PX && this.now() - this.pausedAt >= COOLDOWN_MS) this.following = true;
  }

  private onScroll() {
    const top = this.viewport.scrollTop;
    if (this.dragging && top < this.lastTop) this.pause();
    this.lastTop = top;
    this.maybeResume();
  }

  private onResize() {
    const settling = this.settling;
    if (!this.live() && !settling) return;
    if (settling) this.settleLast = this.now();
    this.maybeResume();
    if (!this.following) return;
    this.toBottom();
  }

  private toBottom() {
    // 组件库正把这一轮推到顶上（占位还有高度）：位置归它管
    const reserve = this.viewport.querySelector<HTMLElement>("[data-aui-top-anchor-reserve]");
    if ((reserve?.offsetHeight ?? 0) > 0) return;
    if (this.distance() <= 1) return;
    this.viewport.scrollTo({ top: this.viewport.scrollHeight, behavior: "instant" });
    this.lastTop = this.viewport.scrollTop;
  }
}
