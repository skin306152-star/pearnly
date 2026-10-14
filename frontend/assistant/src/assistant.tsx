// 整个对话页：外层 assistant-ui，底下是 Managed Agents（ADR-019）。版式照 Claude 客户端（用户 2026-10-12）：
//
//   电脑 / 网页  — 左侧栏（标志或标题栏、搜索、新建、定时任务、最近对话、左下角账号）+ 右侧对话
//   桌面端窗口   — 没有系统标题栏：侧栏顶上画菜单 / 侧栏开关 / 后退 / 前进，右上角画最小化 / 最大化 / 关闭
//   手机         — 左上角圆形菜单按钮；抽屉里大号「CC」、搜索、定时任务、最近对话，底部头像与「新建会话」
//
// 颜色、尺寸、动效只引用 globals.css 的变量与 .cc-* 样式类，这里不写数值。「定时任务」只在有这个功能的产品（Cowork）出现。
// 公司主档（ADR-020，只在 Cowork：共用层 projects() 的 kind 为 company 时）：侧栏「公司」「待处理 N」两行、按公司分组，
// 视图多「公司页」「主档页」「待处理」；对话顶栏「公司 ›」与月份分店胶囊；新对话输入框上方的公司条。ERP / DMS 与今天一样。
// 基于 Anthropic 官方示例 claude-quickstarts/managed-agents/assistant-ui（MIT），见 NOTICE.md。

import { AuiProvider, Tools, useAui, useAuiState } from "@assistant-ui/react";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  Building2Icon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ChevronsUpDownIcon,
  CircleHelpIcon,
  ClockIcon,
  CopyIcon,
  ExternalLinkIcon,
  FileUpIcon,
  FilesIcon,
  GlobeIcon,
  InboxIcon,
  InfoIcon,
  LoaderIcon,
  LogOutIcon,
  MenuIcon,
  MessageCirclePlusIcon,
  MinusIcon,
  MoreHorizontalIcon,
  PanelLeftIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  SquareIcon,
  PinIcon,
  PinOffIcon,
  TrashIcon,
  XIcon,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList, ThreadListItemRename, ThreadListNew, ThreadListSearch } from "@/components/assistant-ui/thread-list";
import {
  ComposerCompany,
  ConversationCompany,
  ConversationFiling,
  MobileFilingItems,
  MoveToMenu,
} from "@/components/assistant-ui/projects";
import { AddCompanyDialog } from "@/components/add-company";
import { Companies } from "@/components/companies";
import { CompanyPage } from "@/components/company-page";
import { PendingView } from "@/components/pending";
import { Settings } from "@/components/settings";
import { ResultsContext, ResultsPanel, useResultFiles } from "@/components/results-panel";
import { InstallBanner, InstallMenuItem, InstallProvider } from "@/components/install-prompt";
import { Shortcut } from "@/components/shortcut";
import { MobileShell } from "@/components/mobile-shell";
import { SplitHandle, sidebarSize, useStoredSize } from "@/components/split-handle";
import { ScheduledTasks } from "@/components/scheduled-tasks";
import { ManagedAgentsToolFallback, toolkit } from "@/components/tool-uis";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarProvider,
  useSidebar,
} from "@/components/ui/sidebar";
import { ManagedAgentsRuntimeProvider, useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import { openSettings, useAutoConnect, useDesktopBridge, useFramelessWindow, windowAction } from "@/lib/desktop";
import { chooseLanguage, siteFetch } from "@/lib/frappe";
import { isBusy } from "@/lib/managed-agents/reducer";
import { onNotice, setActiveSession, setChatView } from "@/lib/managed-agents/activity";
import { noticeReloader } from "@/lib/managed-agents/notice-reload";
import { listen } from "@/lib/managed-agents/realtime";
import { useDeepLinks, useNavHistory, useView, ViewContext, type View } from "@/lib/nav";
import { APP_NAME, assistantLogo, assistantMascot, loadProfile, useProfile, type Profile } from "@/lib/profile";
import { forgetDevice, syncPush, usePush } from "@/lib/push";
import { Mascot } from "@/components/mascot";
import { setDraft, useProjects } from "@/lib/managed-agents/projects";
import { usePendingCounts, useCompanyNav, watchPendingCounts, watchTaskOverview } from "@/lib/companies";
import { co, greeting, HELP_URL, lang, LANGUAGES, siteUrl, t } from "./i18n";

type Nav = ReturnType<typeof useNavHistory>;

// 提醒挂到侧栏没加载到的对话时，几条提醒合成一次重拉列表
const NOTICE_RELOAD_MS = 1500;

function firstName(profile: Profile | null): string {
  return (profile?.full_name || "").trim().split(/\s+/)[0] ?? "";
}

/** 有定时任务这个功能的产品（同 Pearnly 那边：目前只有 Cowork）。 */
function hasScheduledTasks(profile: Profile | null): boolean {
  return profile?.product === "cowork";
}

// 空对话：胖橘猫 + 衬线问候（用户 2026-10-12：问候语旁用胖橘猫；电脑上在左，手机上在上）
function Welcome() {
  const profile = useProfile();
  const name = firstName(profile);
  const mascot = assistantMascot();
  return (
    <div className="flex flex-col items-center gap-5">
      {/* 在哪儿打开就引导装到哪儿（电脑下载桌面版、手机装成应用），关掉后不再出现 */}
      <InstallBanner />
      <div className="aui-thread-welcome-root cc-enter -mb-2 flex flex-col items-center justify-center gap-2 px-4 text-center md:flex-row md:gap-3">
        {/* 胖橘右上方举着泡泡：图的上沿就是泡泡顶，文字对着整只猫居中 */}
        {mascot && <Mascot src={mascot} className="pearnly-mascot-host h-24 shrink-0 sm:h-28" />}
        <h1 className="cc-greeting text-3xl md:text-4xl">
          {name ? `${greeting()}${lang === "zh" ? "，" : ", "}${name}` : greeting()}
        </h1>
      </div>
      {/* 公司条（Cowork 至少有一家公司时）：这段新对话挂在哪家、处理哪个月 */}
      <ComposerCompany />
    </div>
  );
}

/** 打开已有对话、消息还没取回时（同 Claude 手机端：形象居中轻轻浮动，下面一行「正在加载消息」） */
function ThreadLoading() {
  const mascot = assistantMascot();
  return (
    <div className="cc-loading" role="status">
      {mascot && <Mascot src={mascot} className="pearnly-mascot-host" />}
      {t("loadingMessages")}
    </div>
  );
}

function SessionStatusBar() {
  const { sessionId, isTailing, isLoaded, transportError, lastError } = useActiveSessionSnapshot();
  if (!sessionId) return null;
  const message = transportError || (lastError?.retryStatus === "retrying" ? lastError.message : "");
  if (!message && !(isLoaded && !isTailing)) return null;
  return (
    <div data-slot="cc-status" className="mx-auto w-full max-w-(--cc-thread-w) px-4 pt-2 text-ink-3 text-xs">
      {message || t("reconnecting")}
    </div>
  );
}

async function signOut() {
  // 先让本站不再往这台设备推这个人的提醒（lib/push.ts；最多等 3 秒，不挡退出）
  await forgetDevice().catch(() => undefined);
  // 走本站原生的统一登出（pearnly_core.identity.sign_out：结束站点会话并在身份服务登出），退出后回到智能体入口。
  const res = await siteFetch("/api/method/pearnly_core.identity.sign_out?to=assistant", {
    method: "POST",
    headers: { "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
  });
  const body = (await res.json().catch(() => null)) as { message?: { redirect_url?: string } } | null;
  location.href = body?.message?.redirect_url || "/";
}

function MenuLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <DropdownMenu.Item asChild className="cc-menu-item">
      <a href={href} target="_blank" rel="noopener">
        <span className="flex-1">{children}</span>
        <ExternalLinkIcon className="text-ink-3" />
      </a>
    </DropdownMenu.Item>
  );
}

function initials(profile: Profile) {
  return (profile.full_name || profile.user).trim().slice(0, 2).toUpperCase();
}

// 用户菜单（同 Claude）：账号、设置、语言、帮助、了解更多、退出登录。外链都是宣传页上已有的页面。
function UserMenu({ children }: { children: ReactNode }) {
  const profile = useProfile();
  if (!profile) return null;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>{children}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="top" align="start" sideOffset={6} className="cc-menu">
          <div className="truncate px-2.5 py-1.5 text-ink-3 text-sm">{profile.user}</div>
          <DropdownMenu.Item onSelect={() => openSettings()} className="cc-menu-item">
            <SettingsIcon />
            <span className="flex-1">{t("settings")}</span>
            <Shortcut keys="Ctrl ," />
          </DropdownMenu.Item>
          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger className="cc-menu-item">
              <GlobeIcon />
              <span className="flex-1">{t("language")}</span>
              <ChevronRightIcon className="text-ink-3" />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent sideOffset={8} className="cc-menu">
                {LANGUAGES.map((l) => (
                  <DropdownMenu.Item key={l.code} className="cc-menu-item" onSelect={() => void chooseLanguage(l.code)}>
                    <span className="flex-1">{l.label}</span>
                    {l.code === lang && <CheckIcon />}
                  </DropdownMenu.Item>
                ))}
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>
          <DropdownMenu.Item asChild className="cc-menu-item">
            <a href={HELP_URL} target="_blank" rel="noopener">
              <CircleHelpIcon />
              <span className="flex-1">{t("help")}</span>
              <ExternalLinkIcon className="text-ink-3" />
            </a>
          </DropdownMenu.Item>
          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger className="cc-menu-item">
              <InfoIcon />
              <span className="flex-1">{t("learnMore")}</span>
              <ChevronRightIcon className="text-ink-3" />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent sideOffset={8} className="cc-menu">
                <MenuLink href={siteUrl("/about/")}>{t("about")}</MenuLink>
                <DropdownMenu.Separator className="cc-menu-sep" />
                <MenuLink href={siteUrl("/privacy/")}>{t("privacy")}</MenuLink>
                <MenuLink href={siteUrl("/terms/")}>{t("terms")}</MenuLink>
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>
          <DropdownMenu.Separator className="cc-menu-sep" />
          <InstallMenuItem />
          <DropdownMenu.Item onSelect={() => void signOut()} className="cc-menu-item">
            <LogOutIcon />
            {t("logout")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

// 侧栏左下角：头像 + 名字 · 产品（同 Claude）
function UserFooter() {
  const profile = useProfile();
  if (!profile) return null;
  return (
    <UserMenu>
      <button type="button" className="cc-nav-item h-11">
        <span className="cc-avatar">{initials(profile)}</span>
        <span className="cc-nav-label min-w-0 flex-1 truncate">
          {profile.full_name || profile.user}
          <span className="text-ink-3"> · {profile.product_label}</span>
        </span>
        <ChevronsUpDownIcon className="cc-nav-label text-ink-3" />
      </button>
    </UserMenu>
  );
}

// ── 桌面端标题栏（同 Claude 桌面端）────────────────────────────────────────────────────────────────

// 菜单「☰」：同 Claude 桌面端的应用菜单，只放 CC 已有的动作
function AppMenu() {
  const aui = useAui();
  const { setView } = useView();
  const zoom = (step: number) => void windowAction("zoom", { step });
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="cc-icon-btn" aria-label={t("menu")} title={t("menu")}>
        <MenuIcon />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={4} className="cc-menu">
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => { setView("chat"); setDraft({}); aui.threads().switchToNewThread(); }}>
            <span className="flex-1">{t("newChatPill")}</span>
            <Shortcut keys="Ctrl N" />
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => openSettings()}>
            <span className="flex-1">{t("settings")}</span>
            <Shortcut keys="Ctrl ," />
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="cc-menu-sep" />
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => location.reload()}>
            <span className="flex-1">{t("reload")}</span>
            <Shortcut keys="Ctrl R" />
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => zoom(1)}>
            <span className="flex-1">{t("zoomIn")}</span>
            <Shortcut keys="Ctrl =" />
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => zoom(-1)}>
            <span className="flex-1">{t("zoomOut")}</span>
            <Shortcut keys="Ctrl -" />
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => zoom(0)}>
            <span className="flex-1">{t("actualSize")}</span>
            <Shortcut keys="Ctrl 0" />
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="cc-menu-sep" />
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => void windowAction("close")}>
            <span className="flex-1">{t("closeWindow")}</span>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/** 标题栏空白处：按住拖动窗口、双击最大化（同系统标题栏）。 */
function dragHandlers(onToggle: () => void) {
  return {
    onMouseDown: (e: React.MouseEvent) => {
      if (e.button !== 0 || e.detail > 1 || (e.target as HTMLElement).closest("button, a, input, [role=menu]")) return;
      void windowAction("move");
    },
    onDoubleClick: (e: React.MouseEvent) => {
      if ((e.target as HTMLElement).closest("button, a, input")) return;
      onToggle();
    },
  };
}

function useMaximized(enabled: boolean) {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const sync = () => void windowAction("state").then((r) => setMaximized(Boolean(r.maximized)));
    sync();
    window.addEventListener("resize", sync);
    return () => window.removeEventListener("resize", sync);
  }, [enabled]);
  const toggle = useCallback(() => void windowAction("maximize").then((r) => setMaximized(Boolean(r.maximized))), []);
  return { maximized, toggle };
}

// 标题栏左半：菜单 / 侧栏开关 / 后退 / 前进（侧栏开着时在侧栏顶上，收起时在对话区左上角）
function TitleBarLeft({ nav }: { nav: Nav }) {
  const { toggleSidebar } = useSidebar();
  return (
    <div className="flex items-center gap-0.5">
      <AppMenu />
      <button type="button" className="cc-icon-btn" onClick={toggleSidebar} aria-label={t("sidebar")} title={t("sidebar")}>
        <PanelLeftIcon />
      </button>
      <button type="button" className="cc-icon-btn" onClick={nav.back} disabled={!nav.canBack} aria-label={t("back")} title={t("back")}>
        <ArrowLeftIcon />
      </button>
      <button type="button" className="cc-icon-btn" onClick={nav.forward} disabled={!nav.canForward} aria-label={t("forward")} title={t("forward")}>
        <ArrowRightIcon />
      </button>
    </div>
  );
}

function WindowButtons({ maximized, toggle }: { maximized: boolean; toggle: () => void }) {
  return (
    <div className="flex items-stretch self-stretch">
      <button type="button" className="cc-winbtn" onClick={() => void windowAction("minimize")} aria-label={t("minimize")} title={t("minimize")}>
        <MinusIcon />
      </button>
      <button type="button" className="cc-winbtn" onClick={toggle} aria-label={t(maximized ? "restore" : "maximize")} title={t(maximized ? "restore" : "maximize")}>
        {maximized ? <CopyIcon className="!size-3.5 -scale-x-100" /> : <SquareIcon className="!size-3.5" />}
      </button>
      <button type="button" className="cc-winbtn" data-close="" onClick={() => void windowAction("close")} aria-label={t("closeWindow")} title={t("closeWindow")}>
        <XIcon />
      </button>
    </div>
  );
}

// ── 侧栏 ───────────────────────────────────────────────────────────────────────────────────────

function SidebarNav() {
  const profile = useProfile();
  const { view, setView } = useView();
  return (
    <div className="flex flex-col gap-0.5">
      <ThreadListNew onClick={() => setView("chat")} />
      {hasScheduledTasks(profile) && (
        <button
          type="button"
          className="cc-nav-item"
          title={t("scheduledTasks")}
          data-active={view === "tasks" || undefined}
          onClick={() => {
            useCompanyNav.setState({ taskCompany: "", taskFocus: "" });
            setView("tasks");
          }}
        >
          <span className="cc-nav-icon"><ClockIcon /></span>
          <span className="cc-nav-label">{t("scheduledTasks")}</span>
        </button>
      )}
      <CompanyNav />
    </div>
  );
}

/** 侧栏「公司」与「待处理 N」（只在有公司分组的产品；N 为 0 时不显示待处理）。onPick：手机上点了收起抽屉。 */
function CompanyNav({ onPick }: { onPick?: () => void }) {
  const kind = useProjects((s) => s.kind);
  const total = usePendingCounts((s) => s.total);
  const { view, setView } = useView();
  if (!kind) return null;
  const go = (next: View) => {
    if (next === "pending") useCompanyNav.setState({ pendingCompany: "" });
    setView(next);
    onPick?.();
  };
  // 「待处理 N」紧跟在「定时任务」下面（补充乙），然后是「公司」
  return (
    <>
      {total > 0 && (
        <button type="button" className="cc-nav-item" title={co.pending} data-active={view === "pending" || undefined} onClick={() => go("pending")}>
          <span className="cc-nav-icon"><InboxIcon /></span>
          <span className="cc-nav-label flex-1">{co.pending}</span>
          <span className="cc-count">{total}</span>
        </button>
      )}
      <button
        type="button"
        className="cc-nav-item"
        title={co.companies}
        data-active={view === "companies" || view === "company" || undefined}
        onClick={() => go("companies")}
      >
        <span className="cc-nav-icon"><Building2Icon /></span>
        <span className="cc-nav-label">{co.companies}</span>
      </button>
    </>
  );
}

/** 点了某个对话：回到对话视图（从「定时任务」切回来）。 */
function useBackToChatOnPick(onNavigate?: () => void) {
  const { setView } = useView();
  return (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('[data-slot="aui_thread-list-item-trigger"]')) {
      setView("chat");
      onNavigate?.();
    }
  };
}

function DesktopSidebar({ nav, window: win, chrome, onResize }: { nav: Nav; window: ReturnType<typeof useMaximized>; chrome: boolean; onResize: (width: number | null) => void }) {
  const [search, setSearch] = useState("");
  const pick = useBackToChatOnPick();
  const { toggleSidebar } = useSidebar();
  const logo = assistantLogo();
  return (
    <>
      <SidebarHeader className="gap-2 px-2 pt-0 pb-1">
        {chrome ? (
          <div className="cc-titlebar -mx-0.5" {...dragHandlers(win.toggle)}>
            <TitleBarLeft nav={nav} />
          </div>
        ) : (
          // 「图标 + CC」在左、收起按钮在右（用户 2026-10-12：放右边更顺手）；收起后只在左上角留展开按钮（MainTopBar）
          <div className="flex h-12 items-center justify-between ps-1.5">
            <span className="flex items-center gap-2 font-semibold text-ink text-lg">
              {logo && <img src={logo} alt="" className="cc-logo size-6" />}
              {APP_NAME}
            </span>
            <button type="button" className="cc-icon-btn" onClick={toggleSidebar} aria-label={t("sidebar")} title={t("sidebar")}>
              <PanelLeftIcon />
            </button>
          </div>
        )}
        <ThreadListSearch value={search} onValueChange={setSearch} />
      </SidebarHeader>
      <SidebarContent className="px-2" onClickCapture={pick} data-cc-view={useView().view}>
        <SidebarNav />
        <ThreadList search={search} />
      </SidebarContent>
      <SidebarFooter className="border-t border-line px-2">
        <UserFooter />
      </SidebarFooter>
      <SplitHandle size={sidebarSize} edge="end" onChange={onResize} panel={(handle) => handle.closest<HTMLElement>('[data-slot="sidebar-container"]')} />
    </>
  );
}

// 手机抽屉（同 Claude 手机端）：大号「CC」+ 圆形搜索、定时任务、最近对话；底部头像与「新建会话」胶囊
function MobileDrawer() {
  const { setOpenMobile } = useSidebar();
  const aui = useAui();
  const profile = useProfile();
  const { setView } = useView();
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  const close = () => setOpenMobile(false);
  const pick = useBackToChatOnPick(close);
  return (
    <div className="relative flex h-full flex-col pt-[env(safe-area-inset-top)]">
      <div className="flex items-center justify-between px-4 pt-1 pb-3">
        <span className="cc-drawer-title">{APP_NAME}</span>
        <button type="button" className="cc-round-btn" onClick={() => setSearching((v) => !v)} aria-label={t("search")}>
          <SearchIcon />
        </button>
      </div>
      {searching && (
        <div className="cc-enter px-4 pb-2">
          <ThreadListSearch value={search} onValueChange={setSearch} autoFocus />
        </div>
      )}
      <div className="cc-drawer flex-1 overflow-y-auto px-3 pb-28" onClickCapture={pick}>
        {hasScheduledTasks(profile) && (
          <button
            type="button"
            className="cc-nav-item"
            onClick={() => {
              useCompanyNav.setState({ taskCompany: "", taskFocus: "" });
              setView("tasks");
              close();
            }}
          >
            <span className="cc-nav-icon"><ClockIcon /></span>
            {t("scheduledTasks")}
          </button>
        )}
        <CompanyNav onPick={close} />
        <ThreadList search={search} />
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between bg-linear-to-t from-drawer via-drawer to-transparent px-5 pt-6 pb-[max(1.25rem,env(safe-area-inset-bottom))]">
        {profile && (
          <UserMenu>
            <button type="button" className="cc-avatar pointer-events-auto size-12 text-sm" aria-label={profile.full_name || profile.user}>
              {initials(profile)}
            </button>
          </UserMenu>
        )}
        <button
          type="button"
          className="cc-pill pointer-events-auto"
          onClick={() => {
            setView("chat");
            setDraft({});
            aui.threads().switchToNewThread();
            close();
          }}
        >
          <PlusIcon />
          {t("newChatPill")}
        </button>
      </div>
    </div>
  );
}

/** 手机顶栏打开抽屉的图标（同 Claude 手机端）：三条线、最下面一条短。 */
function DrawerIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden>
      <path d="M4 7h16M4 12h16M4 17h10" />
    </svg>
  );
}

// 对话区顶部。
// - 手机（同 Claude 手机端对话页）：左边菜单，右边胶囊（新建对话、「…」对话选项），都浮在内容上。
// - 电脑（同 claude.ai）：对话有内容时左上角是对话名，点开加星标 / 改名 / 删除；右边是本对话的文件。
// - 桌面端：以上放进自画的标题栏，右边是窗口按钮。
function MainTopBar({ nav, window: win, chrome, files }: { nav: Nav; window: ReturnType<typeof useMaximized>; chrome: boolean; files: ReactNode }) {
  const { open, isMobile, setOpenMobile, toggleSidebar } = useSidebar();
  const { view, setView } = useView();
  const aui = useAui();
  const hasMessages = useAuiState((s) => s.thread.messages.length > 0);
  const inChat = view === "chat" && hasMessages;
  if (isMobile) {
    // 同 Claude 手机端的对话页：没有标题、没有横条，按钮浮在内容上，顶上一层上实下透的渐变；抽屉打开时照样在（随页面推开、被压淡）
    return (
      <div className="cc-mtopbar">
        <button type="button" className="cc-round-btn" onClick={() => setOpenMobile(true)} aria-label={t("menu")}>
          <DrawerIcon />
        </button>
        <span />
        {inChat ? <MobileChatPill onNew={() => { setView("chat"); setDraft({}); aui.threads().switchToNewThread(); }} /> : <span className="cc-mtopbar-slot" />}
      </div>
    );
  }
  if (chrome) {
    return (
      <div className="cc-titlebar shrink-0 justify-between pe-0" {...dragHandlers(win.toggle)}>
        <div className="flex min-w-0 items-center gap-1">
          {!open && <TitleBarLeft nav={nav} />}
          {inChat && <ConversationTitle />}
        </div>
        <div className="flex items-center gap-1">
          {files}
          <WindowButtons maximized={win.maximized} toggle={win.toggle} />
        </div>
      </div>
    );
  }
  if (open && !inChat && !files) return null;
  return (
    <div className="cc-topbar">
      {!open && (
        <button type="button" className="cc-icon-btn" onClick={toggleSidebar} aria-label={t("sidebar")} title={t("sidebar")}>
          <PanelLeftIcon />
        </button>
      )}
      {inChat && <ConversationTitle />}
      <div className="ms-auto flex items-center">{files}</div>
    </div>
  );
}

// 手机对话页右上角的胶囊（同 Claude 手机端）：新建对话 + 「…」（置顶、改名、移到…、处理月份、删除）
function MobileChatPill({ onNew }: { onNew: () => void }) {
  const aui = useAui();
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const custom = useAuiState((s) => s.threadListItem.custom);
  const starred = !!custom?.starred;
  const rename = () => {
    const next = window.prompt(t("rename"), title)?.trim();
    if (next && next !== title) void aui.threadListItem().rename(next);
  };
  return (
    <div className="cc-mpill">
      <button type="button" aria-label={t("newChatPill")} onClick={onNew}>
        <MessageCirclePlusIcon />
      </button>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button type="button" aria-label={t("conversationMenu")}>
            <MoreHorizontalIcon />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={6} className="cc-menu !min-w-40">
            {/* 展开写法：组件库整份替换 custom，以后加的键不能被冲掉 */}
            <DropdownMenu.Item className="cc-menu-item" onSelect={() => void aui.threadListItem().updateCustom({ ...custom, starred: !starred })}>
              {starred ? <PinOffIcon /> : <PinIcon />}
              {starred ? t("unstar") : t("star")}
            </DropdownMenu.Item>
            <DropdownMenu.Item className="cc-menu-item" onSelect={rename}>
              <PencilIcon />
              {t("rename")}
            </DropdownMenu.Item>
            <MobileFilingItems />
            <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={() => void aui.threadListItem().delete()}>
              <TrashIcon />
              {t("delete")}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}

// 顶部：「公司 ›」+ 对话名 + 月份分店胶囊（挂在公司下时）
function ConversationTitle() {
  return (
    <div className="flex min-w-0 items-center gap-1">
      <ConversationCompany />
      <ConversationMenu />
      <ConversationFiling />
    </div>
  );
}

// 顶部对话名（同 claude.ai）：点开置顶、改名（就地改）、移到 ▸、删除
function ConversationMenu() {
  const aui = useAui();
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const custom = useAuiState((s) => s.threadListItem.custom);
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const starred = !!custom?.starred;
  const [renaming, setRenaming] = useState(false);
  if (renaming) return <ThreadListItemRename onDone={() => setRenaming(false)} className="w-72" />;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className="cc-title-btn" aria-label={t("conversationMenu")} onDoubleClick={(e) => e.stopPropagation()}>
          <span className="truncate">{title || t("untitled")}</span>
          <ChevronDownIcon />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={4} className="cc-menu !min-w-40">
          {/* 展开写法：组件库整份替换 custom，以后加的键不能被冲掉 */}
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => void aui.threadListItem().updateCustom({ ...custom, starred: !starred })}>
            {starred ? <PinOffIcon /> : <PinIcon />}
            {starred ? t("unstar") : t("star")}
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => setRenaming(true)}>
            <PencilIcon />
            {t("rename")}
          </DropdownMenu.Item>
          <MoveToMenu sessionId={remoteId} localId={localId} />
          <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={() => void aui.threadListItem().delete()}>
            <TrashIcon />
            {t("delete")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
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

// 桌面端快捷提问、回复完成与提醒通知、整窗拖文件、登录后自动连接这台电脑（lib/desktop.ts）；推送通知（lib/push.ts）
function DesktopBridge() {
  const snapshot = useActiveSessionSnapshot();
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const push = usePush();
  const { view } = useView();
  useDesktopBridge(isBusy(snapshot), snapshot.messages, title, push.state === "on", snapshot.sessionId);
  useAutoConnect(useProfile()?.product);
  // 一打开就接上实时通道：侧栏才看得到后台对话在跑、跑完（lib/managed-agents/activity.ts）
  useEffect(() => listen(), []);
  useEffect(() => void syncPush(), []);
  // 侧栏「待处理 N」与公司组头的数：只在有公司分组的产品读（推送状态的站点事件来了、回到前台再读）
  const companyKind = useProjects((s) => s.kind);
  useEffect(() => (companyKind ? watchPendingCounts() : undefined), [companyKind]);
  // 公司组里「定时任务 N」与定时任务总览的分组：一次取全部（不逐家读详情），定时任务事件来了、回到前台再读
  useEffect(() => (companyKind ? watchTaskOverview() : undefined), [companyKind]);
  useEffect(() => setActiveSession(snapshot.sessionId), [snapshot.sessionId]);
  // 在定时任务页时当前对话不算「正看着」：推来的提醒留圆点、不报看过；回到对话页才算
  useEffect(() => setChatView(view === "chat"), [view]);
  useNoticeReload();
  // 浏览器标签页标题跟着对话名（同 Claude）
  useEffect(() => {
    document.title = title && snapshot.messages.length ? `${title} - ${APP_NAME}` : APP_NAME;
  }, [title, snapshot.messages.length]);
  return null;
}

// 提醒挂到侧栏没加载到的对话时重拉列表（lib/managed-agents/notice-reload.ts：合并、避开加载中与正在建的新对话）
function useNoticeReload() {
  const aui = useAui();
  useEffect(() => {
    const reloader = noticeReloader(() => aui.threads(), NOTICE_RELOAD_MS);
    const off = onNotice((notice) => reloader.note(notice.session));
    return () => {
      off();
      reloader.stop();
    };
  }, [aui]);
}

// 深链接：/assistant?c=<对话>、?view=tasks，推送通知与桌面端托盘通知点开（lib/nav.ts）
function DeepLinks() {
  const { setView } = useView();
  useDeepLinks(setView, () => loadProfile().then(hasScheduledTasks));
  return null;
}

function Layout({ results, onSidebarResize }: { results: ReturnType<typeof useResultsPanel>; onSidebarResize: (width: number | null) => void }) {
  const { view, setView } = useView();
  const nav = useNavHistory(view, setView);
  const chrome = useFramelessWindow();
  const win = useMaximized(chrome);
  const { isMobile, openMobile, setOpenMobile } = useSidebar();
  // 本对话产出的文件（同 claude.ai 右上角）：面板关着时显示，点开右侧面板。手机上不放（同 Claude 手机端，对话里的文件卡片点开即可）
  const filesButton =
    view === "chat" && results.files.length > 0 && !results.open ? (
      <button
        type="button"
        onClick={() => results.setOpen(true)}
        title={t("files")}
        aria-label={t("files")}
        className="cc-icon-btn w-auto gap-1.5 px-2 text-sm"
      >
        <FilesIcon />
        {results.files.length}
      </button>
    ) : null;
  const main = (
    <>
      <MainTopBar nav={nav} window={win} chrome={chrome} files={isMobile ? null : filesButton} />
      <div className="relative flex min-h-0 flex-1 flex-row">
        {view === "tasks" ? (
          <ScheduledTasks />
        ) : view === "companies" ? (
          <Companies />
        ) : view === "company" ? (
          <CompanyPage />
        ) : view === "pending" ? (
          <PendingView />
        ) : (
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <SessionStatusBar />
            <div className="min-h-0 flex-1">
              <Thread components={{ Welcome, Loading: ThreadLoading, ToolFallback: ManagedAgentsToolFallback }} />
            </div>
          </div>
        )}
        {view === "chat" && results.open && results.sessionId && (
          <ResultsPanel
            sessionId={results.sessionId}
            files={results.files}
            selected={results.selected}
            onSelect={results.setSelected}
            onClose={() => results.setOpen(false)}
            className="max-md:fixed max-md:inset-0 max-md:z-50 max-md:w-full"
          />
        )}
      </div>
    </>
  );
  if (isMobile) return <MobileShell open={openMobile} onOpenChange={setOpenMobile} drawer={<MobileDrawer />} main={main} />;
  return (
    <div className="flex h-dvh w-full bg-app">
      <div className="cc-dropzone" aria-hidden>
        <div className="cc-dropzone-box">
          <FileUpIcon />
          {t("dropFiles")}
        </div>
      </div>
      {/* 收起后整条侧栏消失，只在左上角留展开按钮（同 claude.ai 与 Claude 桌面端） */}
      <Sidebar>
        <DesktopSidebar nav={nav} window={win} chrome={chrome} onResize={onSidebarResize} />
      </Sidebar>
      {/* min-w-0：右侧结果面板里的宽表格在面板内横向滚动，不把整页撑宽、把「下载」「关闭」挤出窗口 */}
      <SidebarInset className="relative min-w-0 flex-col bg-app">{main}</SidebarInset>
    </div>
  );
}

function Workspace() {
  const aui = useAui({ tools: Tools({ toolkit }) });
  const results = useResultsPanel();
  const context = useMemo(
    () => ({ sessionId: results.sessionId, files: results.files, openFile: results.openFile }),
    [results.sessionId, results.files, results.openFile],
  );
  const [view, setView] = useState<View>("chat");
  const viewContext = useMemo(() => ({ view, setView }), [view]);
  const sidebarWidth = useStoredSize(sidebarSize.key, sidebarSize.cssVar, sidebarSize.unit);
  return (
    <AuiProvider value={aui}>
      <ViewContext.Provider value={viewContext}>
        <ResultsContext.Provider value={context}>
          <DesktopBridge />
          <DeepLinks />
          {/* 设置在 InstallProvider 里：「通知」一行在 iPhone 浏览器里要打开「添加到主屏幕」的步骤 */}
          <InstallProvider>
            <Settings />
            <AddCompanyDialog />
            <SidebarProvider style={sidebarWidth.style}>
              <Layout results={results} onSidebarResize={sidebarWidth.update} />
            </SidebarProvider>
          </InstallProvider>
        </ResultsContext.Provider>
      </ViewContext.Provider>
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
