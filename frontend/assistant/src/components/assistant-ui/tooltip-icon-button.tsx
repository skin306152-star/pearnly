
// 带组件库提示框的图标按钮（回复与代码块的「复制」、滚到底、附件移除）。只是一个普通 <button>，外观全由调用方给的
// 标准类决定（.cc-row-btn、.cc-scroll-btn …），不再套 shadcn 的 Button（标准化记录 B7，V30）。读屏名是按钮里的隐藏文字，
// 调用方给的其余属性（读屏名、点击……）原样展开到按钮上。
import { type ComponentPropsWithRef, forwardRef } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export type TooltipIconButtonProps = ComponentPropsWithRef<"button"> & {
  tooltip: string;
  side?: "top" | "bottom" | "left" | "right";
};

export const TooltipIconButton = forwardRef<
  HTMLButtonElement,
  TooltipIconButtonProps
>(({ children, tooltip, side = "bottom", type = "button", ...rest }, ref) => {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type={type} {...rest} ref={ref}>
          {children}
          <span className="aui-sr-only sr-only">{tooltip}</span>
        </button>
      </TooltipTrigger>
      <TooltipContent side={side}>{tooltip}</TooltipContent>
    </Tooltip>
  );
});

TooltipIconButton.displayName = "TooltipIconButton";
