// 上游：shadcn-ui/ui@8ab27715 apps/v4/registry/bases/radix/ui/button.tsx（MIT，见 components/NOTICE.md）。
// 属性名与取值照 shadcn（variant / size），外观落到 CC 现有的按钮类（docs/development/cc-ui-kit.md「按钮」）：
// 换掉手写的 <button className="cc-btn"> 后看起来不变。
import * as React from "react";
import { Slot } from "radix-ui";

import { cn } from "@/lib/utils";

type ButtonVariant = "default" | "outline" | "secondary" | "ghost" | "destructive" | "link";
type ButtonSize = "default" | "sm" | "lg" | "xs" | "icon" | "icon-sm" | "icon-xs" | "icon-lg";

// 尺寸决定用哪一个现有按钮类：文字按钮 32（.cc-btn）、列表小按钮（.cc-btn-sm）、卡片按钮 24 / 手机 44（.cc-card-btn）、
// 图标钮（.cc-icon-btn）、行尾小图标钮（.cc-row-btn）、卡片里的圆形图标钮（.cc-card-btn[data-icon]）
const SIZE_CLASS: Record<ButtonSize, string> = {
  default: "cc-btn",
  lg: "cc-btn",
  sm: "cc-btn-sm",
  xs: "cc-card-btn",
  icon: "cc-icon-btn",
  "icon-lg": "cc-icon-btn",
  "icon-sm": "cc-row-btn",
  "icon-xs": "cc-card-btn",
};

function buttonVariants({
  variant = "default",
  size = "default",
  className,
}: { variant?: ButtonVariant | null; size?: ButtonSize | null; className?: string } = {}) {
  const base = variant === "link" ? "cc-link-btn" : SIZE_CLASS[size ?? "default"];
  return cn("cc-ui-button", base, className);
}

// 语气写在 data-* 上（与现有类的写法一致：data-primary / data-danger / data-ghost / data-icon）
function toneProps(variant: ButtonVariant, size: ButtonSize) {
  return {
    "data-primary": variant === "default" && !size.startsWith("icon") ? "" : undefined,
    "data-danger": variant === "destructive" ? "" : undefined,
    "data-ghost": variant === "ghost" && !size.startsWith("icon") ? "" : undefined,
    "data-secondary": variant === "secondary" && size !== "xs" ? "" : undefined,
    "data-icon": size === "icon-xs" ? "" : undefined,
  };
}

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  type,
  ...props
}: React.ComponentProps<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  asChild?: boolean;
}) {
  const Comp = asChild ? Slot.Root : "button";

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      type={asChild ? type : (type ?? "button")}
      className={buttonVariants({ variant, size, className })}
      {...toneProps(variant, size)}
      {...props}
    />
  );
}

export { Button, buttonVariants, toneProps };
export type { ButtonSize, ButtonVariant };
