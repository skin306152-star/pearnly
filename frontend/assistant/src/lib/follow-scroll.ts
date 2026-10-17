// 对话滚动照 Claude（用户 10-17 给的三张手机截图；电脑网页同样）：回复在出时不跟到底，下面还有内容就在输入框上方出 ↓，
// 点了才滚到最新（thread.tsx 的 ThreadScrollToBottom）。
//
// 发出一条时组件库的对话视口用「这一轮从顶上开始」（turnAnchor="top"）：把自己的消息推到顶上，下面留出空白给回复；
// 回复超出一屏后它不跟（autoScroll 关着），画面停在这一轮开头，这正是要的样子，这里不再补跟随。
//
// 这里只管打开一段对话（settle）停在哪儿，规则一条：
//   - 没有在跑的一轮（已结束的对话）：停在最新一条（最底下）；
//   - 有在跑的一轮：停在这一轮开头，提问顶在上面——与新对话第一句、电脑发出后组件库给的落点同一套算法（turnStart，提问太高
//     时只留最后 6em）。
// 「有没有在跑的一轮、它的提问是哪条」按会话控制器的状态（runningTurn：在跑、最后一条是回复、前一条是提问），不看组件库标的
// data-aui-top-anchor-user：那个标记跑完也留着（换回一段已结束的对话时还在），第 0 条提问（新对话第一句）又从来不标。
// 打开后内容还会变高——消息用 content-visibility 先按估计高度排、滚到哪儿才量出真实高度，正文排版、文件列表取回后的结果
// 文件卡、缩略图随后才到——所以打开后内容一变高就把位置重新对回去（最底下 / 这一轮开头），直到内容静下来 SETTLE_QUIET_MS
// （最多 SETTLE_MAX_MS），或用户一碰（滚轮、手指滑动、按下手指 / 鼠标、按键；在输入框里点、打字、发送不算，但在输入框上滚轮、
// 手指滑动照样算：那是在翻对话）就交还，之后不再替他滚，
// 也不跟随（出 ↓）。还在对位置时本页或别处发出了新的一轮，就按新这一轮对（它的提问顶在上面，与发出后的落点一致）。
// 停在这一轮开头以后这一轮跑完了也不改去最底下（不跟随）；只有打开时的状态是离开前留下的旧状态（离开期间这一轮结束了，
// 回来时还显示在跑）才不算数：等这段对话按服务端核对完（synced），用户还没碰过就按核对后的状态重新对一次。
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

/** 打开对话停在哪儿要用的状态：turn = 这一轮在跑时它的提问的消息编号（没有在跑的一轮为 null）；synced = 这段对话的状态已按
 *  服务端核对过（会话控制器的 isSynced；离开再回来、核对完之前是离开时的旧状态）。 */
export type OpenState = { turn: string | null; synced: boolean };

/** 在跑的这一轮的提问：在跑（含等「允许」）时，最后一条是提问（回复还没出字）就是它，最后一条是回复就取它前一条提问
 *  （同组件库认这一轮的条件，只是第 0 条也算；组件库在回复出字前自己补一条空回复，这里的消息里没有）。 */
export function runningTurn(busy: boolean, messages: ReadonlyArray<{ id: string; role: string }>): string | null {
  if (!busy) return null;
  const last = messages.at(-1);
  if (last?.role === "user") return last.id;
  const question = messages.at(-2);
  return last?.role === "assistant" && question?.role === "user" ? question.id : null;
}

/** 输入框（.aui-composer-root）里的点按、打字、发送：不算碰了对话（不交还），见 OpenSettler 的构造。 */
const COMPOSER = ".aui-composer-root";
/** 手指挪了多远算在滑（不是点）：点按时手指的微小抖动不算。 */
const SWIPE_PX = 10;

const NOT_RUNNING: OpenState = { turn: null, synced: true };

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
  // 这次打开以后用户碰过没有（还没打开过也算碰过：没有要重新对的）
  private touched = true;
  // 打开时（或上次重新对时）状态核对过没有：没核对过，核对完要按新状态重新对一次
  private synced = true;
  // 已按核对过的状态停在哪一轮开头：这一轮跑完了也接着停在这儿，不改去最底下（不跟随）
  private held: string | null = null;
  private readonly off: Array<() => void> = [];

  constructor(
    private readonly viewport: Viewport,
    content: unknown,
    private readonly state: () => OpenState = () => NOT_RUNNING,
    private readonly now: () => number = () => Date.now(),
    observe: Observe = observeSize,
  ) {
    // 只读事件（passive）：不拦浏览器自己的滚动；用户一碰就交还。在输入框里点、打字、发送、停止键、Esc 不算（审查第 3 条：
    // 打开后马上发出，上一轮晚到的结果文件卡会把新这一轮的提问推下去——还在对位置就按新这一轮对回去）；↓ 等输入框外的按钮照样算
    const release = (e: Event) => {
      if ((e.target as Element | null)?.closest?.(COMPOSER)) return;
      this.unsettle();
    };
    for (const type of ["touchstart", "keydown", "pointerdown"]) {
      viewport.addEventListener(type, release, { passive: true });
      this.off.push(() => viewport.removeEventListener(type, release));
    }
    // 滚轮、手指滑动是在翻对话，不看落在哪儿（第 11 轮合并审查：在输入框上方滚轮或手指往上翻也走了输入框的例外，回复还在出的
    // 10 秒里被一次次拉回这一轮开头）。手指要真挪动（超过 SWIPE_PX）才算：在输入框里点按时的微小抖动不算
    const scrolled = () => this.unsettle();
    let from: { x: number; y: number } | null = null;
    const point = (e: Event) => {
      const touch = (e as TouchEvent).touches?.[0];
      return touch ? { x: touch.clientX ?? 0, y: touch.clientY ?? 0 } : null;
    };
    const down = (e: Event) => {
      from = point(e);
    };
    const swiped = (e: Event) => {
      const at = point(e);
      if (!from || !at) return;
      if (Math.abs(at.x - from.x) > SWIPE_PX || Math.abs(at.y - from.y) > SWIPE_PX) this.unsettle();
    };
    const listen = (type: string, fn: (e: Event) => void) => {
      viewport.addEventListener(type, fn, { passive: true });
      this.off.push(() => viewport.removeEventListener(type, fn));
    };
    listen("wheel", scrolled);
    listen("touchstart", down);
    listen("touchmove", swiped);
    this.off.push(observe(content, () => this.onResize()));
  }

  dispose() {
    this.off.splice(0).forEach((off) => off());
  }

  /** 打开了一段对话（换到另一段、或这段的消息刚取回）：停到该停的位置，内容还在长就接着对回去（见文件头）。 */
  settle() {
    this.touched = false;
    this.synced = this.state().synced;
    this.restart();
  }

  /** 状态变了（这一轮开始 / 结束、核对完）：核对完而用户还没碰过，按核对后的状态重新对一次（打开时是旧状态）；
   *  还在对位置就按新状态对一次（例如这一轮刚标出来）。 */
  recheck() {
    const { synced } = this.state();
    const confirmed = synced && !this.synced;
    this.synced = synced;
    if (confirmed && !this.touched) this.restart();
    else if (this.settling) this.place();
  }

  /** 正在打开后的对位置：用户没碰过、内容最近还在变、没超过上限。 */
  get settling(): boolean {
    const now = this.now();
    return now - this.settleLast < SETTLE_QUIET_MS && now - this.settleFrom < SETTLE_MAX_MS;
  }

  private restart() {
    this.settleFrom = this.settleLast = this.now();
    this.held = null;
    this.place();
  }

  private unsettle() {
    this.touched = true;
    this.settleFrom = this.settleLast = Number.NEGATIVE_INFINITY;
  }

  private onResize() {
    if (!this.settling) return;
    this.settleLast = this.now();
    this.place();
  }

  /** 该停的位置：有在跑的一轮（或已停在某一轮开头）→ 这一轮开头；否则最底下（超出能滚的范围时浏览器自己收在最底下）。
   *  这一轮的提问还没画出来时先到最底下，画出来内容变高时再对过去。 */
  private target(): number {
    const v = this.viewport;
    const { turn, synced } = this.state();
    if (turn && synced) this.held = turn;
    const id = turn ?? this.held;
    const question = id ? (v.querySelector(`[data-message-id=${JSON.stringify(id)}]`) as Box | null) : null;
    if (!question) return v.scrollHeight - v.clientHeight;
    const offset = question.getBoundingClientRect().top - v.getBoundingClientRect().top + v.scrollTop;
    return turnStart(offset, question.offsetHeight, emOf(question));
  }

  private place() {
    const v = this.viewport;
    const top = Math.min(this.target(), v.scrollHeight - v.clientHeight);
    if (Math.abs(v.scrollTop - top) <= 1) return;
    v.scrollTo({ top, behavior: "instant" });
  }
}
