// 单行截断的文字（渐隐或省略号）：悬停 / 键盘焦点落到这一行、放不下时从开头匀速滚到末尾并停住（lib/hover-scroll.ts）。
// 外层就是原来那个截断的元素（类名照旧传进来，静止时的样子不变），里面一层 .cc-marquee-text 是滚动时平移的文字。
// 样式见 globals.css 的 .cc-marquee。
import type { ComponentPropsWithoutRef, FC } from "react";
import { useHoverScroll } from "@/lib/hover-scroll";
import { cn } from "@/lib/utils";

type Props = ComponentPropsWithoutRef<"button"> & { as?: "span" | "div" | "button" };

export const Marquee: FC<Props> = ({ as: Tag = "span", className, children, ...rest }) => {
  const ref = useHoverScroll<HTMLElement>();
  // 同一份属性给 span / div / button（as="button" 时外层就是那个按钮，例如公司页标题）
  return (
    <Tag ref={ref as never} className={cn("cc-marquee", className)} {...(rest as object)}>
      <span className="cc-marquee-text">{children}</span>
    </Tag>
  );
};
