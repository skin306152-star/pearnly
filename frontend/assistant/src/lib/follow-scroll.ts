// 对话滚动照 Claude（用户 10-17 给的三张手机截图；电脑网页同样）：回复在出时不跟到底，下面还有内容就在输入框上方出 ↓，
// 点了才滚到最新（thread.tsx 的 ThreadScrollToBottom）。
//
// 发出一条时组件库的对话视口用「这一轮从顶上开始」（turnAnchor="top"）：把自己的消息推到顶上，下面留出空白给回复；
// 回复超出一屏后它不跟（autoScroll 关着），画面停在这一轮开头，这正是要的样子，这里不再补跟随。
//
// 这里只管打开一段对话（settle）停在最新一条：组件库只在消息第一次出现时滚到底一次，而打开后内容还会变高——消息用
// content-visibility 先按估计高度排、滚到哪儿才量出真实高度，正文排版、文件卡片、缩略图随后才到——于是停在中间
// （2026-10-15 三星 A14 实测 F11f）。打开后内容一变高就贴回最底下，直到内容静下来 SETTLE_QUIET_MS（最多 SETTLE_MAX_MS），
// 或用户一碰（滚轮、按下手指 / 鼠标、按键）就交还，之后不再替他滚。
// 只看本页的滚动与尺寸，不发请求、不开定时。

export const SETTLE_QUIET_MS = 1500;
export const SETTLE_MAX_MS = 10_000;

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

export class OpenSettler {
  // 打开对话后贴底：从哪一刻起、内容最近一次变高在哪一刻（-∞ = 不在贴底）
  private settleFrom = Number.NEGATIVE_INFINITY;
  private settleLast = Number.NEGATIVE_INFINITY;
  private readonly off: Array<() => void> = [];

  constructor(
    private readonly viewport: Viewport,
    content: unknown,
    private readonly now: () => number = () => Date.now(),
    observe: Observe = observeSize,
  ) {
    // 只读事件（passive）：不拦浏览器自己的滚动；用户一碰就交还
    const release = () => this.unsettle();
    for (const type of ["wheel", "touchstart", "keydown", "pointerdown"]) {
      viewport.addEventListener(type, release, { passive: true });
      this.off.push(() => viewport.removeEventListener(type, release));
    }
    this.off.push(observe(content, () => this.onResize()));
  }

  dispose() {
    this.off.splice(0).forEach((off) => off());
  }

  /** 打开了一段对话（换到另一段、或这段的消息刚取回）：贴到最底下，内容还在长就接着贴（见文件头）。 */
  settle() {
    this.settleFrom = this.settleLast = this.now();
    this.toBottom();
  }

  /** 正在打开后的贴底：用户没碰过、内容最近还在变、没超过上限。 */
  get settling(): boolean {
    const now = this.now();
    return now - this.settleLast < SETTLE_QUIET_MS && now - this.settleFrom < SETTLE_MAX_MS;
  }

  private unsettle() {
    this.settleFrom = this.settleLast = Number.NEGATIVE_INFINITY;
  }

  private onResize() {
    if (!this.settling) return;
    this.settleLast = this.now();
    this.toBottom();
  }

  private toBottom() {
    // 组件库正把这一轮推到顶上（占位还有高度）：位置归它管
    const reserve = this.viewport.querySelector<HTMLElement>("[data-aui-top-anchor-reserve]");
    if ((reserve?.offsetHeight ?? 0) > 0) return;
    const v = this.viewport;
    if (v.scrollHeight - v.scrollTop - v.clientHeight <= 1) return;
    v.scrollTo({ top: v.scrollHeight, behavior: "instant" });
  }
}
