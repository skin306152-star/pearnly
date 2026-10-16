// 手机上长按弹菜单（侧栏对话行、公司组头共用）。
import { useRef } from "react";
import { isDragging } from "@/lib/layers";

const LONG_PRESS_MS = 500;
const LONG_PRESS_SLOP = 10;
const CONTEXT_MENU_WINDOW_MS = 1500;

/** 手机上长按一条对话或公司组头弹出它的菜单（同 Claude 手机端）：手机没有悬停，「…」按钮不出现。手指一动就不算长按（在滚列表）。
 *
 *  长按时系统也要出手：选中文字（蓝色选区手柄）、弹出长按菜单，安卓 Chrome 还会在底部弹「点按即可查看搜索结果」盖住菜单，
 *  误点一下就在 CC 里打开搜索页（2026-10-15 三星 A14 实测）。样式上这些行不可选字、不弹系统长按菜单（globals.css，只在触屏）；
 *  这里再拦掉手指刚按下后来的 contextmenu、长按成立时清掉已经出现的选区。鼠标右键（电脑）不受影响。 */
export function useLongPress(onLongPress: () => void) {
  const timer = useRef<number | null>(null);
  const origin = useRef({ x: 0, y: 0 });
  const fired = useRef(false);
  const touchedAt = useRef(-Infinity); // 最近一次手指按下（安卓有时先发 touchcancel 再发 contextmenu）
  const cancel = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };
  return {
    onTouchStart: (e: React.TouchEvent) => {
      const p = e.touches[0];
      fired.current = false;
      touchedAt.current = e.timeStamp;
      cancel();
      if (!p || e.touches.length > 1) return;
      origin.current = { x: p.clientX, y: p.clientY };
      timer.current = window.setTimeout(() => {
        timer.current = null;
        // 按住 250ms 后动了就是拖动（共用拖动 components/ui/drag.tsx 的 touch="move"）：不再出菜单
        if (isDragging()) return;
        fired.current = true;
        window.getSelection?.()?.removeAllRanges();
        navigator.vibrate?.(10);
        onLongPress();
      }, LONG_PRESS_MS);
    },
    onTouchMove: (e: React.TouchEvent) => {
      const p = e.touches[0];
      if (p && Math.hypot(p.clientX - origin.current.x, p.clientY - origin.current.y) > LONG_PRESS_SLOP) cancel();
    },
    onTouchEnd: (e: React.TouchEvent) => {
      cancel();
      if (fired.current) e.preventDefault(); // 长按松手不再算一次点击（不切到这个对话、不收起抽屉）
    },
    onTouchCancel: cancel,
    // 手指刚按下后来的 contextmenu 就是系统的长按菜单：不要它（鼠标右键前没有手指按下，照旧）
    onContextMenu: (e: React.MouseEvent) => {
      if (e.timeStamp - touchedAt.current < CONTEXT_MENU_WINDOW_MS) e.preventDefault();
    },
  };
}
