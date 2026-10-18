// 手机上长按弹菜单（侧栏对话行、公司组头共用）。
import { useCallback, useRef, useState } from "react";
import { watchDragMoved } from "@/lib/layers";

/** 长按出菜单的时长。共用拖动（components/ui/drag.tsx）按住 250ms 就拿起，拿起后手指不动、按到这个时间才出菜单。 */
export const LONG_PRESS_MS = 500;
/** 按住时手指离按下处动出这么多就不算长按（在滚列表）。共用拖动拿起后「算在拖」用同一个距离（菜单不出 = 在拖）。 */
export const LONG_PRESS_SLOP = 10;
const CONTEXT_MENU_WINDOW_MS = 1500;

/** 下一帧开头再调 run（浏览器每帧先把排着的触摸事件发给页面，再调动画帧回调）；回一个作废它的函数。没有 requestAnimationFrame
 *  （服务端渲染、测试）时排到下一个任务。 */
function nextFrame(run: () => void): () => void {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(run);
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(run, 0);
  return () => clearTimeout(id);
}

/** 手机上长按一条对话或公司组头弹出它的菜单（同 Claude 手机端）：手机没有悬停，「…」按钮不出现。手指一动就不算长按（在滚列表）。
 *
 *  能拖的行与组头按住 250ms 就拿起（共用拖动）：拿起后手指动了就是在拖，菜单不出；一直不动到 LONG_PRESS_MS 菜单照旧出来，
 *  这时手指再动（真的拖起来了）就调 onDrag 收起菜单，接着拖（第 6 轮测试机：原来按到 500ms 就归菜单，慢慢开始拖的人一直进菜单）。
 *  「拿起后动了」由共用拖动在收到那一下 touchmove 时当场告诉这里（lib/layers.ts 的 watchDragMoved）：还没出菜单就作废，已经出来
 *  就收起；不在到点时回头问。
 *
 *  到点不当场出菜单，等到下一帧开头（第 13 轮测试机：按住 0.35 秒就开始拖，8 次有 3 次菜单照样在约 0.57 秒出来、挂 0.2 秒才收起）。
 *  浏览器把 touchmove 攒到下一帧开头才发给页面（手指离开它自己的防抖范围以前的几下干脆不发），到点的计时器会先于一下「到点前就
 *  已经动出去」的 touchmove 跑：那时问到的是还没动，菜单出来，紧跟着的这一下 touchmove 又把它收起。现在到点只是排到下一帧开头，
 *  那时这一下已经发进来、作废了长按，菜单不出；这一帧里没人作废才出。按住不动的菜单最多晚一帧出来；到点后、这一帧之前松手或系统
 *  收回触摸，照旧出菜单（触摸事件按先后发，松手之前的移动都已经发进来了，同原来到点就出）。
 *
 *  长按时系统也要出手：选中文字（蓝色选区手柄）、弹出长按菜单，安卓 Chrome 还会在底部弹「点按即可查看搜索结果」盖住菜单，
 *  误点一下就在 CC 里打开搜索页（2026-10-15 三星 A14 实测）。样式上这些行不可选字、不弹系统长按菜单（globals.css，只在触屏）；
 *  这里再拦掉手指刚按下后来的 contextmenu、长按成立时清掉已经出现的选区。鼠标右键（电脑）不受影响。 */
export function useLongPress(onLongPress: () => void, onDrag?: () => void) {
  const timer = useRef<number | null>(null);
  const frame = useRef<(() => void) | null>(null); // 到点了、等下一帧开头：作废它的函数
  const origin = useRef({ x: 0, y: 0 });
  const fired = useRef(false);
  const touchedAt = useRef(-Infinity); // 最近一次手指按下（安卓有时先发 touchcancel 再发 contextmenu）
  const owner = useRef(null); // 挂在共用拖动上的是哪一个（watchDragMoved 只摘自己的）
  const cancel = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    frame.current?.();
    frame.current = null;
  };
  const open = () => {
    frame.current = null;
    fired.current = true;
    window.getSelection?.()?.removeAllRanges();
    navigator.vibrate?.(10);
    onLongPress();
  };
  // 手指离开（松手、系统收回触摸）：已经到点、还在等下一帧的，照到点就出菜单；不再听共用拖动
  const release = () => {
    const due = frame.current !== null;
    cancel();
    if (due) open();
    watchDragMoved(owner, null);
  };
  return {
    onTouchStart: (e: React.TouchEvent) => {
      const p = e.touches[0];
      fired.current = false;
      touchedAt.current = e.timeStamp;
      cancel();
      if (!p || e.touches.length > 1) return;
      origin.current = { x: p.clientX, y: p.clientY };
      // 拿起后手指真的动了（共用拖动认定在拖）：还没出菜单就作废；菜单已经出来了就收起，接着拖
      watchDragMoved(owner, () => {
        if (!fired.current) return cancel();
        fired.current = false;
        onDrag?.();
      });
      timer.current = window.setTimeout(() => {
        timer.current = null;
        frame.current = nextFrame(open);
      }, LONG_PRESS_MS);
    },
    onTouchMove: (e: React.TouchEvent) => {
      const p = e.touches[0];
      if (p && Math.hypot(p.clientX - origin.current.x, p.clientY - origin.current.y) > LONG_PRESS_SLOP) cancel();
    },
    onTouchEnd: (e: React.TouchEvent) => {
      release();
      if (fired.current) e.preventDefault(); // 长按松手不再算一次点击（不切到这个对话、不收起抽屉）
    },
    onTouchCancel: release,
    // 手指刚按下后来的 contextmenu 就是系统的长按菜单：不要它（鼠标右键前没有手指按下，照旧）
    onContextMenu: (e: React.MouseEvent) => {
      if (e.timeStamp - touchedAt.current < CONTEXT_MENU_WINDOW_MS) e.preventDefault();
    },
  };
}

/** 长按出的菜单（侧栏对话行、公司组头共用）：open / setOpen 给菜单（Radix 的 open / onOpenChange），press 给行（同 useLongPress），
 *  modal 给菜单的 Root。
 *
 *  长按（触屏）开的菜单不按模态开（modal=false）：模态菜单打开时组件库给整页挂上禁止滚动的样式表、body 设 pointer-events: none、
 *  其余节点都标 aria-hidden，各引起一次整页样式重算，正叠在拿起后的那一帧上（审查：手机按住不动到 500ms 出菜单那一帧，6 倍节流
 *  98–130ms，其中这几样约 20–45ms）。点菜单外照样只关菜单、不落到下面（components/ui/menu.tsx 吞掉紧跟着的那一次 click），
 *  电脑上（「···」、右键、键盘）照旧是模态（点菜单外由整页不接指针挡住，同 Claude）。modal 只在打开时定，关的那一下不变
 *  （变了组件库会把正在收起的菜单换成另一种重挂一遍）。 */
export function useLongPressMenu(): {
  open: boolean;
  setOpen: (open: boolean) => void;
  modal: boolean;
  press: ReturnType<typeof useLongPress>;
} {
  const [menu, setMenu] = useState({ open: false, modal: true });
  const setOpen = useCallback((open: boolean) => setMenu((now) => (open ? { open: true, modal: true } : { ...now, open: false })), []);
  const press = useLongPress(
    () => setMenu({ open: true, modal: false }),
    () => setMenu((now) => ({ ...now, open: false })),
  );
  return { open: menu.open, setOpen, modal: menu.modal, press };
}
