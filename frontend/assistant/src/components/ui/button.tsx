// 组件库的按钮（shadcn）：只留在用的两种外观（outline、ghost）与四档尺寸（default、sm、icon、icon-sm），没人用的已删（标准化记录 B2）。
// 在用的几处输出保持原样，随调用方所在的批次换成标准类：设置页按钮 → .cc-btn（B4，V10）、会话行「···」→ .cc-row-btn（B6，V29）、
// 回复与代码块「复制」→ .cc-row-btn（B7，V30）；那之前这里的 transition-all、disabled:opacity-50、has-[>svg]:px-2.5 不改（一改就提前变画面）。
import type * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "focus-visible:shadow-focus aria-invalid:border-destructive inline-flex shrink-0 items-center justify-center gap-(--cc-space-8) rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-(--cc-icon-sm)",
  {
    variants: {
      variant: {
        outline:
          "bg-background hover:bg-accent hover:text-accent-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50 border shadow-xs",
        ghost:
          "hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50",
      },
      size: {
        default: "h-(--cc-control-md) px-(--cc-space-16) py-(--cc-space-8) has-[>svg]:px-(--cc-space-12)",
        sm: "h-(--cc-control) gap-(--cc-space-6) rounded-md px-(--cc-space-12) has-[>svg]:px-2.5",
        icon: "size-(--cc-control-md)",
        "icon-sm": "size-(--cc-control)",
      },
    },
    defaultVariants: {
      variant: "ghost",
      size: "default",
    },
  },
);

function Button({
  className,
  variant = "ghost",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot.Root : "button";

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
