// 等待回复：三个 CC 橘的点波浪起伏，旁边可带一行发光的状态字（「正在思考…」，同 Claude）。对话页所有「在忙」的地方都用它。
// 样式在 globals.css 的 .typing-dots*。

import { cn } from "@/lib/utils";

export function TypingDots({ size = "md", className, label, showLabel = false }: {
  size?: "sm" | "md"; className?: string; label?: string; showLabel?: boolean;
}) {
  return (
    <span className={cn("typing-dots", size === "sm" && "typing-dots-sm", className)} role="status" aria-label={label}>
      <span className="typing-dots-marks" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {showLabel && label && <span className="typing-dots-label">{label}</span>}
    </span>
  );
}
