import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// globals.css 的 @theme 接过的实测字阶与字重（标准化记录 2.1 / 2.5）。tailwind-merge 不认识 text-caption 这类名字，会当成文字颜色：
// cn("text-ink-3", "text-caption") 就吞掉其中一个。在这里登记，cn() 才按字号 / 字重合并；与 @theme 两边一致由 tests/test_cc_ccs.py 核对。
export const TEXT_SIZES = ["caption", "footnote", "group", "panel", "body", "subhead", "prose", "title", "page"] as const;
export const FONT_WEIGHTS = ["normal", "medium", "semibold", "bold"] as const;
// @theme 接过的动画名（--animate-*）：不登记的话 cn("animate-cc-pulse", "animate-none") 两个都留着，调用方关不掉骨架的呼吸
export const ANIMATIONS = ["cc-pulse"] as const;

const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: [...TEXT_SIZES],
      "font-weight": [...FONT_WEIGHTS],
      animate: [...ANIMATIONS],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
