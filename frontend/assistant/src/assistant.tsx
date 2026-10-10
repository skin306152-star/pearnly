// 整个对话页：外层 assistant-ui，底下是 Managed Agents（ADR-019）。布局按 claude.ai：
//
//   左侧导航（可收起）— 搜索、新建、按日期分组的历史、底部当前用户
//   右侧              — 空对话时是产品形象 + 问候 + 输入框；有内容时是对话
//
// 站点只属于一个产品：形象与工具都按本站产品来（lib/profile.ts）。
// 基于 Anthropic 官方示例 claude-quickstarts/managed-agents/assistant-ui（MIT），见 NOTICE.md。

import { AuiProvider, Tools, useAui } from "@assistant-ui/react";
import { Thread } from "@/components/assistant-ui/thread";
import { Mascot } from "@/components/mascot";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { ManagedAgentsToolFallback, toolkit } from "@/components/tool-uis";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { ManagedAgentsRuntimeProvider, useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import { brandIcon, PEARNLY_ICON, useProfile, type Profile } from "@/lib/profile";
import { greeting, lang, t } from "./i18n";

function firstName(profile: Profile | null): string {
  return (profile?.full_name || "").trim().split(/\s+/)[0] ?? "";
}

function Welcome() {
  const profile = useProfile();
  const icon = brandIcon(profile?.product);
  const name = firstName(profile);
  return (
    <div className="aui-thread-welcome-root mb-8 flex items-center justify-center gap-3 px-4 text-center">
      {icon && <Mascot src={icon} className="pearnly-mascot-host h-20 shrink-0 sm:h-24" />}
      <h1 className="font-serif text-3xl text-foreground sm:text-4xl">
        {name ? `${greeting()}${lang === "zh" ? "，" : ", "}${name}` : greeting()}
      </h1>
    </div>
  );
}

function SessionStatusBar() {
  const { sessionId, isTailing, isLoaded, transportError, lastError } = useActiveSessionSnapshot();
  if (!sessionId) return null;
  const message = transportError || (lastError?.retryStatus === "retrying" ? lastError.message : "");
  if (!message && !(isLoaded && !isTailing)) return null;
  return (
    <div className="mx-auto w-full max-w-(--thread-max-width) px-4 pt-2 text-muted-foreground text-xs">
      {message || t("reconnecting")}
    </div>
  );
}

function UserFooter() {
  const profile = useProfile();
  if (!profile) return null;
  const initials = (profile.full_name || profile.user).trim().slice(0, 2).toUpperCase();
  return (
    <div className="flex items-center gap-2.5 px-1 py-1">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted font-medium text-xs">{initials}</div>
      <div className="min-w-0 truncate text-sm">
        {profile.full_name || profile.user}
        <span className="text-muted-foreground"> · {profile.product_label}</span>
      </div>
    </div>
  );
}

function AppSidebar() {
  return (
    <Sidebar>
      <SidebarHeader className="flex-row items-center justify-between px-3 pt-3">
        <img src={PEARNLY_ICON} alt="Pearnly" className="size-8 rounded-lg" />
        <SidebarTrigger aria-label={t("sidebar")} />
      </SidebarHeader>
      <SidebarContent className="px-2">
        <ThreadList />
      </SidebarContent>
      <SidebarFooter className="border-t">
        <UserFooter />
      </SidebarFooter>
    </Sidebar>
  );
}

// 侧栏收起时，左上角只留一个展开按钮（同 claude.ai）。
function CollapsedTrigger() {
  const { open, isMobile, openMobile } = useSidebar();
  if (isMobile ? openMobile : open) return null;
  return (
    <div className="absolute top-3 left-3 z-10">
      <SidebarTrigger aria-label={t("sidebar")} />
    </div>
  );
}

function Workspace() {
  const aui = useAui({ tools: Tools({ toolkit }) });
  return (
    <AuiProvider value={aui}>
      <SidebarProvider>
        <div className="flex h-dvh w-full">
          <AppSidebar />
          <SidebarInset className="relative">
            <CollapsedTrigger />
            <div className="flex flex-1 flex-col overflow-hidden" style={{ ["--thread-max-width" as string]: "48rem" }}>
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
