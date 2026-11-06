// 等待回复的标识（同 Claude）：四个小橘点依次亮起，旁边可带一行灰色状态字（例如「8秒 · 思考中」）。
// 对话页所有「在忙」的地方都用它；样式在 globals.css 的 .typing-dots / .cc-spin。

import { cn } from "@/lib/utils";

export function TypingDots({ size = "md", className, label, showLabel = false }: {
  size?: "sm" | "md"; className?: string; label?: string; showLabel?: boolean;
}) {
  return (
    <span className={cn("typing-dots", size === "sm" && "typing-dots-sm", className)} role="status" aria-label={label}>
      <span className="cc-spin" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </span>
      {showLabel && label && <span className="typing-dots-label">{label}</span>}
    </span>
  );
}
