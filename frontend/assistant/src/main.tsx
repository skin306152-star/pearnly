import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Assistant } from "./assistant";
import "./globals.css";
import { pref } from "@/lib/appearance";

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
const applyScheme = () => {
  // 设置里可以固定浅色 / 深色（同 Claude），默认跟随系统
  const theme = pref("theme");
  document.documentElement.classList.toggle("dark", theme === "dark" || (theme === "system" && scheme.matches));
  themeColor();
};
applyScheme();
scheme.addEventListener("change", applyScheme);
window.addEventListener("cc-theme", applyScheme);
new MutationObserver(themeColor).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["data-open"], childList: true });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <Assistant />
    </TooltipProvider>
  </StrictMode>,
);
