import { t } from "@/i18n";
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { isDragging } from "@/lib/layers";

// 手机（同 Claude 手机端）：抽屉在底下不动，当前页面是盖在上面的一张卡片。按住页面任意处左右拖，卡片跟着手指走，
// 一离开原位就是圆角细边，越往右越淡（深色模式越暗）；松手按甩的方向（甩得快）或停的位置（过半）决定停在打开还是收起。
// 打开后点露出的那截或往左拖收回。拖动进度写在 --cc-mprogress（0 收起 ~ 1 打开），样式全在 globals.css 的 .cc-mshell。

const LOCK = 8; // 手指移动多少像素后判定是横拖（开关抽屉）还是竖滚（滚动内容）
const FLICK = 0.35; // 松手时每毫秒多少像素算「甩」
const SAMPLE_MS = 100; // 用最近这么久的轨迹算松手速度

type Gesture = {
  x0: number;
  y0: number;
  base: number; // 按下时：0 收起，1 打开
  locked: boolean;
  progress: number;
  samples: Array<{ x: number; at: number }>;
};

/** 按下处在一块还能往左滚的横向滚动区里（宽表格、代码块）：右拖先滚它，不开抽屉。 */
function scrollsBack(target: EventTarget | null, shell: HTMLElement): boolean {
  for (let el = target instanceof Element ? target : null; el && el !== shell; el = el.parentElement) {
    if (el.scrollLeft > 0 && el.scrollWidth > el.clientWidth) return true;
  }
  return false;
}

function typing(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest("input, textarea, select, [contenteditable='true']");
}

export function MobileShell({
  open,
  onOpenChange,
  drawer,
  main,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  drawer: ReactNode;
  main: ReactNode;
}) {
  const shellRef = useRef<HTMLDivElement>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const changeRef = useRef(onOpenChange);
  changeRef.current = onOpenChange;

  // 外壳在时整页不能被拉动：根元素标 data-cc-mshell（globals.css；不用 html:has(.cc-mshell)，根元素做 :has() 的锚点会让
  // 页面上每次插入节点、改属性都整页重算样式）。画第一帧前标上
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.ccMshell = "";
    return () => {
      delete root.dataset.ccMshell;
    };
  }, []);

  // 弹出键盘时整页跟着可见区域走（同 Claude 手机端：输入框顶在键盘上方）：外壳是 fixed，高度与上沿取根元素的
  // --cc-viewport-h / --cc-vvtop（lib/viewport.ts 按可见区域写，弹窗也用同一份）。整页本身不让拉动（globals.css 的
  // html[data-cc-mshell]），只有对话、抽屉里的内容区会滚。
  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;
    let g: Gesture | null = null;
    const drawerWidth = () => shell.querySelector<HTMLElement>(".cc-mdrawer")?.getBoundingClientRect().width || 1;
    const finish = () => {
      shell.removeAttribute("data-dragging");
      shell.style.removeProperty("--cc-mprogress");
      g = null;
    };

    const onStart = (e: TouchEvent) => {
      const p = e.touches[0];
      if (e.touches.length !== 1 || !p || (!openRef.current && typing(e.target))) {
        if (g?.locked) finish();
        g = null;
        return;
      }
      g = { x0: p.clientX, y0: p.clientY, base: openRef.current ? 1 : 0, locked: false, progress: openRef.current ? 1 : 0,
            samples: [{ x: p.clientX, at: e.timeStamp }] };
      if (g.base === 0 && scrollsBack(e.target, shell)) g = null;
    };

    const onMove = (e: TouchEvent) => {
      const p = e.touches[0];
      if (!g || !p) return;
      // 抽屉里正在拖对话、公司组（共用拖动）：手指横着偏一点也不推抽屉
      if (isDragging() && !g.locked) {
        g = null;
        return;
      }
      const dx = p.clientX - g.x0;
      const dy = p.clientY - g.y0;
      if (!g.locked) {
        if (Math.abs(dx) < LOCK && Math.abs(dy) < LOCK) return;
        // 竖着滚、收起时往左拖、打开时往右拖：都不归抽屉管
        if (Math.abs(dx) <= Math.abs(dy) || (g.base === 0 ? dx < 0 : dx > 0)) {
          g = null;
          return;
        }
        g.locked = true;
        g.x0 = p.clientX; // 从判定那一刻起算，卡片不会先跳一下
        shell.setAttribute("data-dragging", "");
      }
      e.preventDefault();
      g.progress = Math.min(1, Math.max(0, g.base + (p.clientX - g.x0) / drawerWidth()));
      shell.style.setProperty("--cc-mprogress", String(g.progress));
      g.samples.push({ x: p.clientX, at: e.timeStamp });
      while (g.samples.length > 2 && e.timeStamp - g.samples[0]!.at > SAMPLE_MS) g.samples.shift();
    };

    const onEnd = () => {
      if (!g?.locked) {
        g = null;
        return;
      }
      const first = g.samples[0]!;
      const last = g.samples[g.samples.length - 1]!;
      const velocity = last.at > first.at ? (last.x - first.x) / (last.at - first.at) : 0;
      const next = Math.abs(velocity) > FLICK ? velocity > 0 : g.progress > 0.5;
      // 先把目标状态写到元素上再撤掉拖动进度：动画从手指松开的位置接着走，不会先往反方向闪一下
      shell.dataset.open = String(next);
      finish();
      if (next !== openRef.current) changeRef.current(next);
    };

    shell.addEventListener("touchstart", onStart, { passive: true });
    shell.addEventListener("touchmove", onMove, { passive: false });
    shell.addEventListener("touchend", onEnd);
    shell.addEventListener("touchcancel", onEnd);
    return () => {
      shell.removeEventListener("touchstart", onStart);
      shell.removeEventListener("touchmove", onMove);
      shell.removeEventListener("touchend", onEnd);
      shell.removeEventListener("touchcancel", onEnd);
    };
  }, []);

  return (
    <div ref={shellRef} className="cc-mshell" data-open={open}>
      <aside className="cc-mdrawer" aria-hidden={!open} inert={!open || undefined}>
        {drawer}
      </aside>
      <div className="cc-mmain">
        <div className="flex h-full min-w-0 flex-col" inert={open || undefined}>{main}</div>
        <button
          type="button"
          className="cc-mscrim"
          tabIndex={open ? 0 : -1}
          aria-hidden={!open}
          aria-label={t("close")}
          onClick={() => onOpenChange(false)}
        />
      </div>
    </div>
  );
}
