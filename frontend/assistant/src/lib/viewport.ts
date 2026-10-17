// 可见区域的高度与上沿（弹出键盘、iOS 地址栏伸缩时会变）：写进根元素的 --cc-vvh / --cc-vvtop，globals.css 的
// --cc-viewport-h 先用它、没有时退回 --cc-vh（100dvh；Chrome / 安卓 WebView 108 以前不认 dvh，退回 100vh）。
//
//   手机外壳 .cc-mshell 的高度、弹窗 .cc-modal 的位置与最大高度都按它算：键盘弹出时整页、弹窗都缩在键盘上方，
//   标题、关闭、保存一直在屏内（2026-10-15 三星 A14 / Chrome 106 实测弹窗超出屏幕、关不掉）。
//   安卓靠页面 meta 的 interactive-widget=resizes-content（旧版 Chrome 本来就这样）缩布局视口；iOS 不缩布局视口，只缩可见区域，
//   这里的上沿让 fixed 的外壳、弹窗贴住可见区域。不去 scrollTo 把页面拉回（iOS 上与手指来回打架，界面一直抖）。
//
// 键盘弹出时根元素带 data-cc-keyboard：输入框底下只留一小截（同 Claude 手机端：输入框贴着键盘），iOS 不再留
// env(safe-area-inset-bottom) 那一截（键盘盖住了主屏幕指示条）。按可见区域比这个宽度下见过的最高矮多少判断：iOS 整屏高不变、
// 只缩可见区域；安卓 resizes-content 时整屏也跟着变矮（第 10 轮 A14 实测按整屏比判不出来），所以比的是见过的最高，宽度变了
// （转屏）重新记。乘 scale：双指放大时可见区域也变矮，不算键盘。键盘收起的那一下发 KEYBOARD_CLOSED（安卓返回键收起键盘时
// 输入框仍聚焦，输入框据此收回一行，thread.tsx）。

// 可见区域比见过的最高矮过这么多像素算键盘弹出（地址栏伸缩只有几十像素）
const KEYBOARD_MIN = 120;
export const KEYBOARD_CLOSED = "cc:keyboard-closed";

export function trackViewport(root: HTMLElement = document.documentElement): () => void {
  const viewport = window.visualViewport;
  if (!viewport) return () => undefined;
  let width = 0;
  let tallest = 0;
  let open = false;
  const fit = () => {
    root.style.setProperty("--cc-vvh", `${viewport.height}px`);
    root.style.setProperty("--cc-vvtop", `${viewport.offsetTop}px`);
    const shown = viewport.height * viewport.scale;
    if (window.innerWidth !== width) {
      width = window.innerWidth;
      tallest = 0;
    }
    tallest = Math.max(tallest, window.innerHeight, shown);
    const next = tallest - shown > KEYBOARD_MIN;
    root.toggleAttribute("data-cc-keyboard", next);
    if (open && !next) window.dispatchEvent(new Event(KEYBOARD_CLOSED));
    open = next;
  };
  fit();
  viewport.addEventListener("resize", fit);
  viewport.addEventListener("scroll", fit);
  return () => {
    viewport.removeEventListener("resize", fit);
    viewport.removeEventListener("scroll", fit);
    root.removeAttribute("data-cc-keyboard");
  };
}
