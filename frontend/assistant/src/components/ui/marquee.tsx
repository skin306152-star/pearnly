// 单行截断的文字：放不下时末尾渐隐（lib/overflow-fade.ts，整页一个观察器判定、设 data-overflow；用户 10-16「统一渐隐」，不用省略号）；
// 悬停 / 键盘焦点落到这一行、放不下时从开头匀速滚到末尾并停住（lib/hover-scroll.ts）。
// 外层就是原来那个截断的元素（类名照旧传进来），里面一层 .cc-marquee-text 是文字（量宽、滚动时平移的就是它）。
// 样式见 globals.css 的 .cc-marquee。
import { memo, useEffect, type ComponentPropsWithoutRef } from "react";
import { useHoverScroll } from "@/lib/hover-scroll";
import { attachOverflowFade } from "@/lib/overflow-fade";
import { cn } from "@/lib/utils";

type Props = ComponentPropsWithoutRef<"button"> & { as?: "span" | "div" | "button" };

// 记住（memo）：侧栏组头、行随拖动重画时，名字没变就不重画（测试窗口第 10 轮 B 段拿起那一刻的长帧）
export const Marquee = memo(function Marquee({ as: Tag = "span", className, children, ...rest }: Props) {
  const ref = useHoverScroll<HTMLElement>();
  useEffect(() => (ref.current ? attachOverflowFade(ref.current) : undefined), [ref]);
  // 同一份属性给 span / div / button（as="button" 时外层就是那个按钮）
  return (
    <Tag ref={ref as never} className={cn("cc-marquee", className)} {...(rest as object)}>
      <span className="cc-marquee-text">{children}</span>
    </Tag>
  );
});
