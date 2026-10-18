import { cn } from "@/lib/utils";

/** 加载占位的骨架：默认一条字高的条（--cc-skeleton-h），调用方按需改尺寸。 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("bg-accent animate-pulse rounded-md h-(--cc-skeleton-h)", className)}
      {...props}
    />
  );
}

export { Skeleton };
