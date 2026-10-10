// 整个对话页：外层 assistant-ui，底下是 Managed Agents（ADR-019）。布局按 claude.ai：
//
//   左侧导航（可收起）— 搜索、新建、按日期分组的历史、底部当前用户
//   右侧              — 空对话时是产品形象 + 问候 + 输入框；有内容时是对话
//
// 站点只属于一个产品：工具按本站产品来；欢迎处的形象是智能体自己的（lib/profile.ts）。
// 基于 Anthropic 官方示例 claude-quickstarts/managed-agents/assistant-ui（MIT），见 NOTICE.md。

import { AuiProvider, Tools, useAui, useAuiState } from "@assistant-ui/react";
import {
  CheckIcon,
  ChevronRightIcon,
  ChevronsUpDownIcon,
  CircleHelpIcon,
  ExternalLinkIcon,
  FilesIcon,
  GlobeIcon,
  InfoIcon,
  LogOutIcon,
  SettingsIcon,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Thread } from "@/components/assistant-ui/thread";
import { Mascot } from "@/components/mascot";
import { Settings } from "@/components/settings";
import { ResultsContext, ResultsPanel, useResultFiles } from "@/components/results-panel";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { ManagedAgentsToolFallback, toolkit } from "@/components/tool-uis";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { ManagedAgentsRuntimeProvider, useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import { openSettings, useDesktopBridge } from "@/lib/desktop";
import { isBusy } from "@/lib/managed-agents/reducer";
import { assistantMascot, useProfile, type Profile } from "@/lib/profile";
import { greeting, HELP_URL, lang, LANGUAGES, siteUrl, t } from "./i18n";

function firstName(profile: Profile | null): string {
  return (profile?.full_name || "").trim().split(/\s+/)[0] ?? "";
}

function Welcome() {
  const profile = useProfile();
  const icon = assistantMascot();
  const name = firstName(profile);
  return (
    <div className="aui-thread-welcome-root -mb-2 flex items-center justify-center gap-3 px-4 text-center">
      {/* 形象图上方留了约 27% 空白（挥手的余量）；负外边距把它扣掉，文字就对准小人本身居中 */}
      {icon && <Mascot src={icon} className="pearnly-mascot-host -mt-5.5 h-20 shrink-0 sm:-mt-6.5 sm:h-24" />}
      <h1 className="text-3xl text-foreground sm:text-4xl">
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

async function signOut() {
  // 走本站原生的统一登出（pearnly_core.identity.sign_out：结束站点会话并在身份服务登出），退出后回到智能体入口。
  const res = await fetch("/api/method/pearnly_core.identity.sign_out?to=assistant", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
  });
  const body = (await res.json().catch(() => null)) as { message?: { redirect_url?: string } } | null;
  location.href = body?.message?.redirect_url || "/";
}

async function setLanguage(code: string) {
  await fetch("/api/method/pearnly_core.account.preferences.set_language", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify({ language: code }),
  });
  location.reload();
}

// 用户菜单（同 claude.ai）：账号、语言、帮助、了解更多、退出登录。外链都是宣传页上已有的页面。
const menuItem =
  "flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none hover:bg-muted focus:bg-muted data-[state=open]:bg-muted";
const menuContent = "z-[60] min-w-56 rounded-xl border bg-white p-1.5 shadow-lg";

function MenuLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <DropdownMenu.Item asChild className={menuItem}>
      <a href={href} target="_blank" rel="noopener">
        <span className="flex-1">{children}</span>
        <ExternalLinkIcon className="size-3.5 text-muted-foreground" />
      </a>
    </DropdownMenu.Item>
  );
}

function UserFooter() {
  const profile = useProfile();
  if (!profile) return null;
  const initials = (profile.full_name || profile.user).trim().slice(0, 2).toUpperCase();
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="flex w-full items-center gap-2.5 rounded-md px-1 py-1 text-start outline-none hover:bg-muted data-[state=open]:bg-muted">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted font-medium text-xs">{initials}</div>
        <div className="min-w-0 flex-1 truncate text-sm">
          {profile.full_name || profile.user}
          <span className="text-muted-foreground"> · {profile.product_label}</span>
        </div>
        <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="top" align="start" sideOffset={6} className={menuContent}>
          <div className="truncate px-2.5 py-1.5 text-muted-foreground text-sm">{profile.user}</div>
          <DropdownMenu.Item onSelect={() => openSettings()} className={menuItem}>
            <SettingsIcon className="size-4" />
            <span className="flex-1">{t("settings")}</span>
            <span className="text-muted-foreground text-xs">Ctrl ,</span>
          </DropdownMenu.Item>
          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger className={menuItem}>
              <GlobeIcon className="size-4" />
              <span className="flex-1">{t("language")}</span>
              <ChevronRightIcon className="size-4 text-muted-foreground" />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent sideOffset={8} className={menuContent}>
                {LANGUAGES.map((l) => (
                  <DropdownMenu.Item key={l.code} className={menuItem} onSelect={() => void setLanguage(l.code)}>
                    <span className="flex-1">{l.label}</span>
                    {l.code === lang && <CheckIcon className="size-4" />}
                  </DropdownMenu.Item>
                ))}
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>
          <DropdownMenu.Item asChild className={menuItem}>
            <a href={HELP_URL} target="_blank" rel="noopener">
              <CircleHelpIcon className="size-4" />
              <span className="flex-1">{t("help")}</span>
              <ExternalLinkIcon className="size-3.5 text-muted-foreground" />
            </a>
          </DropdownMenu.Item>
          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger className={menuItem}>
              <InfoIcon className="size-4" />
              <span className="flex-1">{t("learnMore")}</span>
              <ChevronRightIcon className="size-4 text-muted-foreground" />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent sideOffset={8} className={menuContent}>
                <MenuLink href={siteUrl("/about/")}>{t("about")}</MenuLink>
                <DropdownMenu.Separator className="my-1 h-px bg-border" />
                <MenuLink href={siteUrl("/privacy/")}>{t("privacy")}</MenuLink>
                <MenuLink href={siteUrl("/terms/")}>{t("terms")}</MenuLink>
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item onSelect={() => void signOut()} className={menuItem}>
            <LogOutIcon className="size-4" />
            {t("logout")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function AppSidebar() {
  return (
    <Sidebar>
      {/* 侧栏顶部不放图标（用户 2026-10-10）：搜索框直接在最上面，收起按钮放在它同一行 */}
      <SidebarContent className="px-2 pt-2">
        <ThreadList trailing={<SidebarTrigger aria-label={t("sidebar")} />} />
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

// 右侧结果面板：新结果出现时自动打开最新的一个；对话里的文件卡片、右上角按钮随时打开（同 claude.ai 的 Artifacts）。
function useResultsPanel() {
  const { sessionId, isRunning } = useActiveSessionSnapshot();
  const { files, fresh, clearFresh } = useResultFiles(sessionId, isRunning);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    setOpen(false);
    setSelected(null);
  }, [sessionId]);
  useEffect(() => {
    if (fresh) {
      setSelected(null);
      setOpen(true);
      clearFresh();
    }
  }, [fresh, clearFresh]);
  const openFile = useCallback((id: string) => {
    setSelected(id);
    setOpen(true);
  }, []);
  return { sessionId, files, selected, setSelected, openFile, open: open && files.length > 0, setOpen };
}

// 桌面端快捷提问、回复完成通知、整窗拖文件（lib/desktop.ts）
function DesktopBridge() {
  const snapshot = useActiveSessionSnapshot();
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  useDesktopBridge(isBusy(snapshot), snapshot.messages, title);
  return null;
}

function Workspace() {
  const aui = useAui({ tools: Tools({ toolkit }) });
  const results = useResultsPanel();
  const context = useMemo(
    () => ({ sessionId: results.sessionId, files: results.files, openFile: results.openFile }),
    [results.sessionId, results.files, results.openFile],
  );
  return (
    <AuiProvider value={aui}>
      <ResultsContext.Provider value={context}>
      <DesktopBridge />
      <Settings />
      <SidebarProvider>
        <div className="flex h-dvh w-full">
          <AppSidebar />
          <SidebarInset className="relative flex-row">
            <CollapsedTrigger />
            {results.files.length > 0 && !results.open && (
              <button
                type="button"
                onClick={() => results.setOpen(true)}
                title={t("files")}
                aria-label={t("files")}
                className="absolute top-3 right-3 z-10 flex h-8 items-center gap-1.5 rounded-md px-2 text-[#5b5950] text-sm hover:bg-muted"
              >
                <FilesIcon className="size-4" />
                {results.files.length}
              </button>
            )}
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden" style={{ ["--thread-max-width" as string]: "48rem" }}>
              <SessionStatusBar />
              <div className="min-h-0 flex-1">
                <Thread components={{ Welcome, ToolFallback: ManagedAgentsToolFallback }} />
              </div>
            </div>
            {results.open && results.sessionId && (
              <ResultsPanel
                sessionId={results.sessionId}
                files={results.files}
                selected={results.selected}
                onSelect={results.setSelected}
                onClose={() => results.setOpen(false)}
                className="max-md:fixed max-md:inset-0 max-md:z-50 md:w-[45%]"
              />
            )}
          </SidebarInset>
        </div>
      </SidebarProvider>
      </ResultsContext.Provider>
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
