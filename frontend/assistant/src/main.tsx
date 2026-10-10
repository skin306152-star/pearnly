import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Assistant } from "./assistant";
import "./globals.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <Assistant />
    </TooltipProvider>
  </StrictMode>,
);
