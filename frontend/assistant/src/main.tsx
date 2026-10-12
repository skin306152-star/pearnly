import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Assistant } from "./assistant";
import "./globals.css";

// 深浅色跟随系统（同 Claude）：系统改了也即时跟着变。样式变量在 globals.css 的 :root / .dark。
const scheme = window.matchMedia("(prefers-color-scheme: dark)");
const applyScheme = () => document.documentElement.classList.toggle("dark", scheme.matches);
applyScheme();
scheme.addEventListener("change", applyScheme);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <Assistant />
    </TooltipProvider>
  </StrictMode>,
);
