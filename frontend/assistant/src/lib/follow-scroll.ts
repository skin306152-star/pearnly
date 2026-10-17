// 对话滚动照 Claude（用户 10-17 给的三张手机截图；电脑网页同样）：回复在出时不跟到底，下面还有内容就在输入框上方出 ↓，
// 点了才滚到最新（thread.tsx 的 ThreadScrollToBottom）。
//
// 发出一条时组件库的对话视口用「这一轮从顶上开始」（turnAnchor="top"）：把自己的消息推到顶上，下面留出空白给回复；
// 回复超出一屏后它不跟（autoScroll 关着），画面停在这一轮开头，这正是要的样子，这里不再补跟随。
//
// 这里只管打开一段对话（settle）停在哪儿，规则一条：
//   - 没有在跑的一轮（已结束的对话）：停在最新一条（最底下）；
//   - 有在跑的一轮（组件库给这一轮的提问标了 data-aui-top-anchor-user）：停在这一轮开头，提问顶在上面——与新对话第一句、
//     电脑发出后组件库给的落点同一套算法（turnStart，提问太高时只留最后 6em）。
// 打开后内容还会变高——消息用 content-visibility 先按估计高度排、滚到哪儿才量出真实高度，正文排版、文件列表取回后的结果
// 文件卡、缩略图随后才到——所以打开后内容一变高就把位置重新对回去（最底下 / 这一轮开头），直到内容静下来 SETTLE_QUIET_MS
// （最多 SETTLE_MAX_MS），或用户一碰（滚轮、按下手指 / 鼠标、按键）就交还，之后不再替他滚，也不跟随（出 ↓）。
// 第 11 轮 C19：组件库只在这一轮第一次出现时按当时的版面滚一次（一次性的平滑滚动），之后上面的内容变高它不管；浏览器的
// 滚动锚定也补不回来（回复根元素 -mb-7.5 pb-7.5 盖住下一条的顶上 30px，提问顶在上面时锚点选在上一轮回复里，它往下长就
// 把提问推出屏幕）。A14 打开正在回复的长对话时提问因此被推到屏幕下方 967px、画面停在上一轮回复中间。
// 只看本页的滚动与尺寸，不发请求、不开定时。

/** 这一轮开头的滚动位置（同组件库 computeTopAnchorTargetScrollTop 的默认夹取 10em / 6em）：提问顶在上面；
 *  提问高过 10em 时只留它最后 6em 在上面，下面留地方给回复。offset = 提问在滚动内容里的上沿。 */
export function turnStart(offset: number, height: number, em: number): number {
  return offset + Math.max(0, height - (height <= 10 * em ? height : 6 * em));
}

export const SETTLE_QUIET_MS = 1500;
export const SETTLE_MAX_MS = 10_000;

type Viewport = Pick<
  HTMLElement,
  | "addEventListener"
  | "removeEventListener"
  | "scrollTop"
  | "scrollHeight"
  | "clientHeight"
  | "scrollTo"
  | "querySelector"
  | "getBoundingClientRect"
>;
type Box = Pick<HTMLElement, "getBoundingClientRect" | "offsetHeight">;
type Observe = (target: unknown, onResize: () => void) => () => void;

/** 组件库标的这一轮的提问与它的回复：这一轮在跑时标上（打开一段已结束的对话时没有；跑完留到换对话、重新载入才清）。 */
const TURN_ANCHOR = "[data-aui-top-anchor-user]";
const TURN_TARGET = "[data-aui-top-anchor-target]";

function emOf(el: unknown): number {
  if (typeof getComputedStyle !== "function") return 16;
  return Number.parseFloat(getComputedStyle(el as Element).fontSize) || 16;
}

function observeSize(target: unknown, onResize: () => void): () => void {
  if (typeof ResizeObserver === "undefined") return () => {};
  const observer = new ResizeObserver(() => onResize());
  observer.observe(target as Element);
  return () => observer.disconnect();
}

export class OpenSettler {
  // 打开对话后对位置：从哪一刻起、内容最近一次变高在哪一刻（-∞ = 不在对）
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

  /** 打开了一段对话（换到另一段、或这段的消息刚取回）：停到该停的位置，内容还在长就接着对回去（见文件头）。 */
  settle() {
    this.settleFrom = this.settleLast = this.now();
    this.place();
  }

  /** 正在打开后的对位置：用户没碰过、内容最近还在变、没超过上限。 */
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
    this.place();
  }

  /** 该停的位置：有在跑的一轮 → 这一轮开头；否则最底下（超出能滚的范围时浏览器自己收在最底下）。 */
  private target(): number {
    const v = this.viewport;
    const anchor = v.querySelector(TURN_ANCHOR) as Box | null;
    if (!anchor) return v.scrollHeight - v.clientHeight;
    const offset = anchor.getBoundingClientRect().top - v.getBoundingClientRect().top + v.scrollTop;
    return turnStart(offset, anchor.offsetHeight, emOf(v.querySelector(TURN_TARGET) ?? anchor));
  }

  private place() {
    const v = this.viewport;
    const top = Math.min(this.target(), v.scrollHeight - v.clientHeight);
    if (Math.abs(v.scrollTop - top) <= 1) return;
    v.scrollTo({ top, behavior: "instant" });
  }
}
