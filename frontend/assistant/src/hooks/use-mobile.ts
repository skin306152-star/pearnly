import * as React from "react";

// 手机 / 电脑的分界（同 Claude 侧栏）：手机外壳（MobileShell）、窄屏菜单、外壳变体（lib/variant.ts）都按这一条。
// 断点只有一条、这里是它的来源：globals.css @theme 的 --breakpoint-md: 768px（Tailwind 的 max-md: / md: 生成的就是同一段
// (width < 768px) / (width >= 768px)）与 globals.css 的 @media 都写同一段文本，tests/test_cc_ccs.py 核对三处一致。
// 写 width < 768px 而不是 max-width: 767px：浏览器缩放出 767.5 这类小数宽度时，变体与 max-md: 的类才判在同一边（V37）。
export const MOBILE_BREAKPOINT = 768;
export const MOBILE_QUERY = `(width < ${MOBILE_BREAKPOINT}px)`;

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(
    undefined,
  );

  React.useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    // 用 mql.matches 而不是 innerWidth：浏览器缩放出小数宽度时与 lib/variant.ts 判得一样
    const onChange = () => {
      setIsMobile(mql.matches);
    };
    mql.addEventListener("change", onChange);
    setIsMobile(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return !!isMobile;
}
