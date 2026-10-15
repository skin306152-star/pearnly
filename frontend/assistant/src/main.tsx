import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ErrorBoundary } from "@/components/error-boundary";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Assistant } from "./assistant";
import "./globals.css";
import { colorSchemeOf, pref, type Theme } from "@/lib/appearance";
import { installBackKey } from "@/lib/back-stack";
import { installVariant } from "@/lib/variant";
import { trackViewport } from "@/lib/viewport";

// 外壳变体（网页 / 桌面窗口 / 手机，lib/variant.ts）：先于首帧写好，侧栏底色与行高不跳
installVariant();

// 深浅色默认跟随系统（同 Claude）：系统改了也即时跟着变；设置里也可以固定。样式变量在 globals.css 的 :root / .dark。
const scheme = window.matchMedia("(prefers-color-scheme: dark)");
// 手机浏览器状态栏的颜色（<meta name="theme-color">）取当前底色；抽屉打开时取抽屉的颜色（assistant.tsx 的 MobileShell）
function themeColor() {
  const drawer = document.querySelector('.cc-mshell[data-open="true"]');
  const value = getComputedStyle(document.documentElement).getPropertyValue(drawer ? "--cc-bg-drawer" : "--cc-bg-app").trim();
  if (!value) return;
  // 站点页面按深浅色各放了一个（首屏用）；这里统一改成当前值
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    if (meta.getAttribute("content") !== value) meta.setAttribute("content", value);
  }
}
// 页面的深浅（<meta name="color-scheme">，lib/appearance.ts 的 colorSchemeOf）：设置里固定了就只报那一种，
// 浏览器自己画的部分（安卓系统导航条、滚动条、表单控件）跟着页面走（测试窗口第 2 轮：深色下导航条仍是白的）
function colorScheme(theme: Theme) {
  let meta = document.querySelector('meta[name="color-scheme"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.setAttribute("name", "color-scheme");
    document.head.appendChild(meta);
  }
  const value = colorSchemeOf(theme);
  if (meta.getAttribute("content") !== value) meta.setAttribute("content", value);
}
const applyScheme = () => {
  // 设置里可以固定浅色 / 深色（同 Claude），默认跟随系统
  const theme = pref("theme");
  document.documentElement.classList.toggle("dark", theme === "dark" || (theme === "system" && scheme.matches));
  colorScheme(theme);
  themeColor();
};
applyScheme();
scheme.addEventListener("change", applyScheme);
window.addEventListener("cc-theme", applyScheme);
new MutationObserver(themeColor).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["data-open"], childList: true });

// 可见区域的高度（键盘弹出时变矮）：手机外壳与弹窗按它定高度（lib/viewport.ts）
trackViewport();
// 手机返回键先关最上面一层弹层（lib/back-stack.ts）
installBackKey();

// 最外层错误边界：哪里抛错都不整页变白（components/error-boundary.tsx）
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <TooltipProvider>
        <Assistant />
      </TooltipProvider>
    </ErrorBoundary>
  </StrictMode>,
);
