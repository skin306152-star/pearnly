// 转圈太久、连点上报（乱用设计 docs/work/2026-11-09-cc-misuse-graceful-design.md 6.1，欠账 B40）：
// 11-08 事故里用户一直在转圈、连点按钮，站点错误日志里一条都没有。这里只「报」，不改界面：
//   - 页面上一个「在忙」的标记（.cc-spinner 转圈、.cc-spin 四点、骨架）挂着 60 秒、它所在那一块的字一直没变（进度或状态没动）；
//   - 同一张卡 5 秒内点了 3 次以上。
// 经统一出口 reportCaught 交给站点的 client_errors（服务端按内容去重、限量）；页面这边同一种每分钟最多 1 条。
// 只带哪一块（data-slot 名）、多久、几次，**不带票面、文件名、对话内容**。页面在后台时不计时（回到前台重新算）。

import { reportCaught } from "./caught";

export const STALL_MS = 60_000;
export const CLICK_WINDOW_MS = 5_000;
export const CLICK_TIMES = 3;
export const REPORT_GAP_MS = 60_000;
const SCAN_MS = 10_000;
const BUSY = '.cc-spinner, .cc-spin, [data-slot="skeleton"]';
const CARD = '.cc-card, [data-slot*="card"], [data-slot^="cc-op"], [data-slot^="cc-askq"]';

/** 一块的名字：最近的带 data-slot 的祖先（只是界面位置名，没有内容）。 */
export function placeOf(el: Element | null): string {
  const named = el?.closest("[data-slot]");
  return named?.getAttribute("data-slot")?.slice(0, 60) || "unknown";
}

/** 判定本身（不碰定时器和 DOM 事件，测试直接喂时刻）：转圈看「挂了多久、那一块的字变没变」，连点看「同一张卡 5 秒内几次」。 */
export class StallJudge {
  private busy = new Map<Element, { since: number; text: string }>();
  private clicks = new Map<Element, number[]>();
  private lastReport = new Map<string, number>();

  constructor(private readonly report: (kind: string, detail: string) => void) {}

  /** 扫一次此刻挂着的在忙标记；挂够 60 秒且那一块的字没变的报一次（同一个标记只报一次）。 */
  scan(now: number, marks: readonly { el: Element; text: string }[]): void {
    const alive = new Set<Element>();
    for (const { el, text } of marks) {
      alive.add(el);
      const seen = this.busy.get(el);
      if (!seen || seen.text !== text) {
        this.busy.set(el, { since: now, text });
        continue;
      }
      if (seen.since >= 0 && now - seen.since >= STALL_MS) {
        this.emit(now, "stall", `${placeOf(el)} ${Math.round((now - seen.since) / 1000)}s`);
        seen.since = -1;  // 这一个标记不再报
      }
    }
    for (const el of [...this.busy.keys()]) if (!alive.has(el)) this.busy.delete(el);
  }

  /** 页面到后台：计时作废，回来重新算（后台时浏览器不刷新界面，不算没进展）。 */
  reset(): void {
    this.busy.clear();
  }

  /** 点了一张卡。 */
  click(now: number, card: Element): void {
    const times = (this.clicks.get(card) ?? []).filter((t) => now - t < CLICK_WINDOW_MS);
    times.push(now);
    this.clicks.set(card, times);
    if (times.length >= CLICK_TIMES) {
      this.emit(now, "rapid_clicks", `${placeOf(card)} ${times.length}x`);
      this.clicks.delete(card);
    }
  }

  private emit(now: number, kind: string, detail: string): void {
    const last = this.lastReport.get(kind) ?? -Infinity;
    if (now - last < REPORT_GAP_MS) return;
    this.lastReport.set(kind, now);
    this.report(kind, detail);
  }
}

/** 一块在忙标记周围的字（它所在那一块的文字：步骤名、进度数变了就算有进展）。 */
function textNear(el: Element): string {
  const block = el.closest("[data-slot]") ?? el.parentElement;
  return (block?.textContent ?? "").slice(0, 500);
}

/** 页面挂一次：页面看得见、且有在忙标记时每 10 秒扫一次（没有标记就停，页面再有变动时看一眼要不要重起；空闲时不跑），
 *  听卡片上的点击。返回撤掉的函数。 */
export function watchStalls(): () => void {
  if (typeof document === "undefined" || typeof MutationObserver === "undefined") return () => undefined;
  const judge = new StallJudge((kind, detail) => reportCaught(new Error(`CC ${kind}: ${detail}`), kind));
  let timer = 0;
  const stop = () => {
    if (timer) window.clearInterval(timer);
    timer = 0;
  };
  const tick = () => {
    if (document.visibilityState !== "visible") {
      judge.reset();
      return stop();
    }
    const marks = [...document.querySelectorAll(BUSY)].map((el) => ({ el, text: textNear(el) }));
    if (!marks.length) {
      judge.reset();
      return stop();
    }
    judge.scan(Date.now(), marks);
  };
  const maybeStart = () => {
    if (timer || document.visibilityState !== "visible" || !document.querySelector(BUSY)) return;
    tick();
    timer = window.setInterval(tick, SCAN_MS);
  };
  const observer = new MutationObserver(maybeStart);
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener("visibilitychange", maybeStart);
  const onClick = (event: MouseEvent) => {
    const card = (event.target as Element | null)?.closest?.(CARD);
    if (card) judge.click(Date.now(), card);
  };
  document.addEventListener("click", onClick, true);
  maybeStart();
  return () => {
    stop();
    observer.disconnect();
    document.removeEventListener("visibilitychange", maybeStart);
    document.removeEventListener("click", onClick, true);
  };
}
