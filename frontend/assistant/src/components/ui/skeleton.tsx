import { cn } from "@/lib/utils";

/** 加载占位的骨架：默认一条字高的条（--cc-skeleton-h），调用方按需改尺寸；明暗呼吸同运行中的脉冲点（animate-cc-pulse，V5）。
 *  block：整块铺满父元素、不带圆角（缩略图位这种已有外框的格子；外观由这里给，调用方不写圆角，卡片闸门 C3）。 */
function Skeleton({ className, block, ...props }: React.ComponentProps<"div"> & { block?: boolean }) {
  return (
    <div
      data-slot="skeleton"
      data-block={block || undefined}
      className={cn("bg-accent animate-cc-pulse", block ? "size-full" : "rounded-md h-(--cc-skeleton-h)", className)}
      {...props}
    />
  );
}

export { Skeleton };
