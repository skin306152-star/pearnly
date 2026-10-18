import { t } from "@/i18n";
import { useCallback, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";

// 三栏都能拖（同 claude.ai）：左侧栏右边、右侧结果面板左边各有一条分隔条。按住左右拖动调宽度，双击或回车恢复默认，
// 方向键微调。宽度记在本机；默认值和最窄 / 最宽都在 globals.css（--cc-sidebar-w / --cc-sidebar-min-w / --cc-sidebar-max-w，
// --cc-results-share / --cc-results-min-w / --cc-chat-min-w），这里只在拖动时把新值写回同一个变量。

const STEP_PX = 16;

function read(key: string): number | null {
  try {
    const value = Number(localStorage.getItem(key));
    return value > 0 ? value : null;
  } catch {
    return null;
  }
}

function write(key: string, value: number | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(Math.round(value * 10000) / 10000));
  } catch {
    // 无痕窗口等存不了：本次照样生效，只是下次回到默认
  }
}

/** 记在本机的一个尺寸（null = 用 CSS 里的默认值），以及把它写到某个 CSS 变量上的样式。 */
export function useStoredSize(key: string, cssVar: string, unit: "" | "px") {
  const [size, setSize] = useState<number | null>(() => read(key));
  const update = useCallback(
    (next: number | null) => {
      setSize(next);
      write(key, next);
    },
    [key],
  );
  const style = (size === null ? {} : { [cssVar]: `${size}${unit}` }) as CSSProperties;
  return { size, update, style };
}

function px(el: Element, name: string): number {
  const value = getComputedStyle(el).getPropertyValue(name).trim();
  const size = parseFloat(value) || 0;
  if (value.endsWith("rem")) return size * parseFloat(getComputedStyle(document.documentElement).fontSize);
  if (value.endsWith("vw")) return (size * window.innerWidth) / 100;
  return size;
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max));

/** 右侧结果面板：按占整个内容区的比例（窗口变大变小仍成比例），两边都不窄于各自的最小宽度。 */
export const resultsSize = {
  key: "cc.results.share",
  cssVar: "--cc-results-share",
  unit: "" as const,
  fromPointer(panel: HTMLElement, x: number) {
    const area = panel.parentElement!.getBoundingClientRect();
    return this.limit(panel, (area.right - x) / area.width);
  },
  step(panel: HTMLElement, towardStart: boolean) {
    const area = panel.parentElement!.getBoundingClientRect().width || 1;
    const now = panel.getBoundingClientRect().width / area;
    return this.limit(panel, now + (towardStart ? STEP_PX : -STEP_PX) / area);
  },
  limit(panel: HTMLElement, share: number) {
    const area = panel.parentElement!.getBoundingClientRect().width || 1;
    return clamp(share, px(panel, "--cc-results-min-w") / area, 1 - px(panel, "--cc-chat-min-w") / area);
  },
};

/** 左侧栏：按像素宽，夹在最窄与最宽之间。 */
export const sidebarSize = {
  key: "cc.sidebar.width",
  cssVar: "--cc-sidebar-w",
  unit: "px" as const,
  fromPointer(panel: HTMLElement, x: number) {
    return this.limit(panel, x - panel.getBoundingClientRect().left);
  },
  step(panel: HTMLElement, towardStart: boolean) {
    return this.limit(panel, panel.getBoundingClientRect().width + (towardStart ? -STEP_PX : STEP_PX));
  },
  limit(panel: HTMLElement, width: number) {
    return clamp(width, px(panel, "--cc-sidebar-min-w"), px(panel, "--cc-sidebar-max-w"));
  },
};

type Size = typeof resultsSize | typeof sidebarSize;

/**
 * 分隔条。`edge` 是它贴在所属那一栏的哪一边（结果面板贴左边 start，侧栏贴右边 end）；
 * `panel` 找到被调宽度的那一栏（默认是父元素）。
 */
export function SplitHandle({
  size,
  edge,
  onChange,
  panel = (handle) => handle.parentElement,
}: {
  size: Size;
  edge: "start" | "end";
  onChange: (value: number | null) => void;
  panel?: (handle: HTMLElement) => HTMLElement | null;
}) {
  const [dragging, setDragging] = useState(false);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    const handle = event.currentTarget;
    const target = panel(handle);
    if (event.button !== 0 || !target) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    setDragging(true);
    document.documentElement.setAttribute("data-cc-resizing", "");
    const move = (e: globalThis.PointerEvent) => onChange(size.fromPointer(target, e.clientX));
    const end = () => {
      setDragging(false);
      document.documentElement.removeAttribute("data-cc-resizing");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = panel(event.currentTarget);
    if (!target) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      onChange(size.step(target, event.key === "ArrowLeft"));
    } else if (event.key === "Enter") {
      event.preventDefault();
      onChange(null);
    }
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={t("resizePanel")}
      tabIndex={0}
      data-edge={edge}
      data-dragging={dragging ? "" : undefined}
      className="cc-split max-md:hidden"
      onPointerDown={onPointerDown}
      onDoubleClick={() => onChange(null)}
      onKeyDown={onKeyDown}
    />
  );
}
