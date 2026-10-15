import * as React from "react";

// 手机 / 电脑的分界（同 Claude 侧栏）：手机外壳（MobileShell）、窄屏菜单、外壳变体（lib/variant.ts）都按这一条
export const MOBILE_BREAKPOINT = 768;
export const MOBILE_QUERY = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`;

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
