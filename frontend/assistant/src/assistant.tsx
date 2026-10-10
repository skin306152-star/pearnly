// 整个对话页：外层 assistant-ui，底下是 Managed Agents（ADR-019）。
//
//   ManagedAgentsRuntimeProvider — 会话列表 = 本站该用户的会话；当前会话的事件记录 = 对话内容
//   PearnlySidebar               — 会话列表（新建 / 切换 / 归档 / 删除）
//   Thread                       — 对话，带工具卡片与「允许 / 拒绝」
//
// 基于 Anthropic 官方示例 claude-quickstarts/managed-agents/assistant-ui（MIT），见 NOTICE.md。

import { AuiProvider, Tools, useAui } from "@assistant-ui/react";
import { MessageSquareIcon } from "lucide-react";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { ManagedAgentsToolFallback, toolkit } from "@/components/tool-uis";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { ManagedAgentsRuntimeProvider, useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import { t } from "./i18n";

// 空白对话只显示标题，不加说明文字。
function Welcome() {
  return (
    <div className="aui-thread-welcome-root mb-6 flex flex-col items-center px-4 text-center">
      <h1 className="font-semibold text-2xl">{t("title")}</h1>
    </div>
  );
}

function SessionStatusBar() {
  const { sessionId, isTailing, isLoaded, transportError, lastError } = useActiveSessionSnapshot();
  if (!sessionId) return null;
  return (
    <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-col gap-1 px-4 pt-2">
      {transportError && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-red-700 text-xs">
          {transportError}
        </div>
      )}
      {lastError && lastError.retryStatus === "retrying" && (
        <div className="rounded-md bg-muted px-3 py-1.5 text-muted-foreground text-xs">
          {lastError.message}
        </div>
      )}
      {isLoaded && !isTailing && !transportError && (
        <div className="px-1 text-[11px] text-muted-foreground">{t("reconnecting")}</div>
      )}
    </div>
  );
}

function PearnlySidebar() {
  return (
    <Sidebar>
      <SidebarHeader className="mb-2 border-b">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <div>
                <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                  <MessageSquareIcon className="size-4" />
                </div>
                <span className="font-semibold">{t("title")}</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent className="px-2">
        <ThreadList />
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  );
}

function Workspace() {
  const aui = useAui({ tools: Tools({ toolkit }) });
  return (
    <AuiProvider value={aui}>
      <SidebarProvider>
        <div className="flex h-dvh w-full">
          <PearnlySidebar />
          <SidebarInset>
            <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
              <SidebarTrigger />
              <span className="font-medium text-sm">{t("title")}</span>
            </header>
            <div className="flex flex-1 flex-col overflow-hidden" style={{ ["--thread-max-width" as string]: "44rem" }}>
              <SessionStatusBar />
              <div className="min-h-0 flex-1">
                <Thread components={{ Welcome, ToolFallback: ManagedAgentsToolFallback }} />
              </div>
            </div>
          </SidebarInset>
        </div>
      </SidebarProvider>
    </AuiProvider>
  );
}

export function Assistant() {
  return (
    <ManagedAgentsRuntimeProvider>
      <Workspace />
    </ManagedAgentsRuntimeProvider>
  );
}
