// 单行放不下时末尾渐隐（用户 2026-10-16：「一行放不下时，侧栏末尾是渐隐，右侧栏是省略号，统一渐隐」）：CC 前端所有单行截断的
// 文字一律末尾渐隐、不用省略号；只有真的放不下才渐隐，放得下时末尾不淡（按内容收缩宽度的元素也一样）。
//
//   判定 — 整页共用一个 ResizeObserver（不给每一行各开一个）：观察截断的那个框（宽度变了）和里面那层文字 .cc-marquee-text
//          （文字变了、字体载入后变宽），放不下就给框设 data-overflow，CSS 用 mask 把末尾 --cc-marquee-end（默认
//          --cc-row-fade，24px）渐隐（globals.css 的 .cc-marquee）。先读完所有框再一起写，写的只是属性（只影响绘制），
//          不会引起观察器反复触发
//   放不下 — 文字宽 > 框的内容宽（框宽减左右内边距）超过 0.5px。行内元素（clientWidth 为 0，本来就不截断）不算
//   悬停滚动 — lib/hover-scroll.ts 用同一个量法：放得下的不滚；放不下的滚到最后一个字越过末尾渐隐
//
// 用法：components/ui/marquee.tsx 的 <Marquee> 自动挂上；表格这类一次成百上千格的，调 watchOverflow(容器, 量哪些框) 只观察容器。
const TEXT = ":scope > .cc-marquee-text";

/** 文字比框的内容宽多出多少像素（≤ 0.5 就是放得下）。框里有 .cc-marquee-text 时量它（不受平移、缩放影响），没有就比 scrollWidth。 */
export function overflowOf(box: HTMLElement): number {
  if (!box.isConnected || !box.clientWidth) return 0;
  const text = box.querySelector<HTMLElement>(TEXT);
  if (!text) return box.scrollWidth - box.clientWidth;
  const style = getComputedStyle(box);
  const padding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
  // 拖动浮层等被缩放时，量到的宽同比放大：按框的缩放比换回版面宽
  const scale = box.offsetWidth ? box.getBoundingClientRect().width / box.offsetWidth || 1 : 1;
  return text.getBoundingClientRect().width / scale + padding - box.clientWidth;
}

/** 放不下：多出超过 0.5px（取整误差以内算放得下，末尾不淡）。 */
export const overflows = (over: number) => over > 0.5;

/** 量一批框并设 / 去 data-overflow：先全部读、再全部写（只在变了时写）。 */
export function markOverflow(boxes: Iterable<HTMLElement>) {
  const list = [...new Set(boxes)];
  const states = list.map((box) => overflows(overflowOf(box)));
  list.forEach((box, i) => {
    if (box.hasAttribute("data-overflow") !== states[i]) box.toggleAttribute("data-overflow", states[i]);
  });
}

// 整页一个观察器：被观察的元素 → 它变了要重量哪些框
const targets = new Map<Element, () => Iterable<HTMLElement>>();
let observer: ResizeObserver | null = null;

function shared(): ResizeObserver | null {
  if (observer || typeof ResizeObserver === "undefined") return observer;
  observer = new ResizeObserver((entries) => {
    const boxes: HTMLElement[] = [];
    for (const entry of entries) {
      const boxesOf = targets.get(entry.target);
      if (boxesOf) boxes.push(...boxesOf());
    }
    markOverflow(boxes);
  });
  return observer;
}

/** 观察这些元素，任何一个尺寸变了就重量 boxesOf() 给的框；返回卸下的函数。 */
export function watchOverflow(watch: readonly Element[], boxesOf: () => Iterable<HTMLElement>): () => void {
  const ro = shared();
  if (!ro) {
    markOverflow(boxesOf()); // 没有 ResizeObserver（极老的浏览器）：只在挂上时量一次
    return () => {};
  }
  for (const el of watch) {
    targets.set(el, boxesOf);
    ro.observe(el);
  }
  return () => {
    for (const el of watch) {
      if (targets.get(el) !== boxesOf) continue;
      targets.delete(el);
      ro.unobserve(el);
    }
  };
}

/** 一个截断的框（里面一层 .cc-marquee-text）：观察框与文字两层。 */
export function attachOverflowFade(box: HTMLElement): () => void {
  const text = box.querySelector<HTMLElement>(TEXT);
  return watchOverflow(text ? [box, text] : [box], () => [box]);
}
