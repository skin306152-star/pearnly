// 等待中的三个点：同智能体小人胸前那三个点，黑色深 → 浅，依次一跳一跳。对话页所有「在忙」的地方都用它。

import { cn } from "@/lib/utils";

export function TypingDots({ size = "md", className, label }: { size?: "sm" | "md"; className?: string; label?: string }) {
  return (
    <span className={cn("typing-dots", size === "sm" && "typing-dots-sm", className)} role="status" aria-label={label}>
      <i />
      <i />
      <i />
    </span>
  );
}
