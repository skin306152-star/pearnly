// 组件库的提示框（shadcn）。延时只在 globals.css（--cc-tooltip-delay），提示框用到时读（标准化记录 2.6）；每个 Tooltip 自带一层 Provider，
// 外面不用再包（main.tsx、侧栏原来各包了一层，延时由离它最近的这一层决定，外面那几层从来不起作用）。层级 --cc-z-tooltip：盖过菜单（V7）；
// 字号实测 caption 12/17、进出场同菜单与弹层（.cc-pop：快档 120ms、主缓动，V18；原来是 tw-animate 默认的 150ms 淡入放大加滑入）。
import type * as React from "react";
import { Tooltip as TooltipPrimitive } from "radix-ui";

import { fixedMs } from "@/lib/css-var";
import { cn } from "@/lib/utils";

/** 提示框延时的回退值（ms）：样式表还没生效时用，与 globals.css 的 --cc-tooltip-delay 同值（测试核对）。 */
export const TOOLTIP_DELAY = 0;

/** 提示框延时（ms）：globals.css 的 --cc-tooltip-delay，用时读、读到就记住。 */
export const tooltipDelay = () => fixedMs("--cc-tooltip-delay", TOOLTIP_DELAY);

function TooltipProvider({
  delayDuration,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Provider>) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delayDuration={delayDuration ?? tooltipDelay()}
      {...props}
    />
  );
}

function Tooltip({
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  return (
    <TooltipProvider>
      <TooltipPrimitive.Root data-slot="tooltip" {...props} />
    </TooltipProvider>
  );
}

function TooltipTrigger({
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Trigger>) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />;
}

function TooltipContent({
  className,
  sideOffset = 0,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        className={cn(
          "cc-pop bg-tooltip text-tooltip-ink z-(--cc-z-tooltip) w-fit origin-(--radix-tooltip-content-transform-origin) rounded-md px-(--cc-space-12) py-(--cc-space-6) text-caption text-balance",
          className,
        )}
        {...props}
      >
        {children}
        {/* 小箭头：组件库模板带的方块转 45° 露出一角，往上收进提示框（尺寸、角度在 globals.css 的 --cc-tooltip-arrow*） */}
        <TooltipPrimitive.Arrow
          data-slot="tooltip-arrow"
          className="bg-tooltip fill-tooltip z-(--cc-z-tooltip) size-(--cc-tooltip-arrow) translate-y-[calc(100%/-2-var(--cc-space-2))] rotate-(--cc-tooltip-arrow-turn) rounded-xs"
        />
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
