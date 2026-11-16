// 整个对话页：外层 assistant-ui，底下是 Managed Agents（ADR-019）。版式照 Claude 客户端（用户 2026-10-12）：
//
//   电脑 / 网页  — 左侧栏（标志或标题栏、搜索、新建、定时任务、最近对话、左下角账号）+ 右侧对话
//   桌面端窗口   — 没有系统标题栏：侧栏顶上画菜单 / 侧栏开关 / 后退 / 前进，右上角画最小化 / 最大化 / 关闭
//   手机         — 左上角圆形菜单按钮；抽屉里大号「CC」、搜索、定时任务、最近对话，底部头像与「新建会话」
//
// 颜色、尺寸、动效只引用 globals.css 的变量与 .cc-* 样式类，这里不写数值。「定时任务」只在有这个功能的产品（Cowork）出现。
// 公司（ADR-020，共用层 projects() 的 kind 为 company 时：Cowork 公司主档、ERP 公司）：侧栏按公司分组（组头「···」有这家的改名、
// 删除等）；对话顶栏「公司名 ›」与右侧会话栏（components/session-panel.tsx）；输入框下巴的公司（点了弹选择公司窗口，
// components/company-picker.tsx）、月份、分店（ComposerChin）。用户 11-07：侧栏「公司」、公司列表页、公司页删了。待处理（用户 11-04：左侧不要，并进对话）落在「公司 · 本月」对话末尾的「需要处理」框
// （thread.tsx 的 Tail 插槽，components/pending.tsx 的 PendingInChat），还没有对话的侧栏一行占位（projects.tsx）。
// 哪些分区出现只看产品声明的 sections（useSection），不按产品名分支。DMS 与今天一样。
// 实操（CC 在 Express 窗口里逐张填写，Cowork 与 ERP 都有；设计稿 https://claude.ai/artifact/SgtxECheQE4e6gtF1G4WKW）：侧栏最上面那一行
// （☰、侧栏开关、←、→）右端两个图标切换「直写 / 实操」，侧栏收起时跟着那一行留在顶栏；手机在抽屉顶部；Ctrl / ⌘ + Shift + 1 / 2。
// 只有一套对话框架（方案 cc-unified-plan 14）：代录任务就是一段会话，同一个 thread、runtime、新建页、侧栏（按模式筛，lib/managed-agents/
// list-mode.ts）、活动状态与停止键；代录的状态由服务端的代录事件源翻成统一对话事件，卡片与步骤在 components/tool-uis/operator-*。
// 代录模式没有右侧栏（用户 11-04：代录右侧不要）。执行电脑上读桥（components/operator/bridge.ts）。两种模式各记各的最后位置（lib/nav.ts）。
// 基于 Anthropic 官方示例 claude-quickstarts/managed-agents/assistant-ui（MIT），见 NOTICE.md。

import { AuiProvider, Tools, useAui, useAuiState } from "@assistant-ui/react";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  Building2Icon,
  CheckIcon,
  CircleHelpIcon,
  ClockIcon,
  CopyIcon,
  ExternalLinkIcon,
  FileUpIcon,
  FilesIcon,
  GlobeIcon,
  InfoIcon,
  LoaderIcon,
  LogOutIcon,
  MenuIcon,
  MessageCirclePlusIcon,
  MinusIcon,
  MoreHorizontalIcon,
  PanelLeftIcon,
  PanelRightIcon,
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
import { Marquee } from "@/components/ui/marquee";
import { Skeleton } from "@/components/ui/skeleton";
import { DropdownMenu, SubMenu, menuOffset } from "@/components/ui/menu";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { ModeSwitch, useOperatorBridge } from "@/components/operator";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList, ThreadListItemRename, ThreadListNew, ThreadListSearch, toggleStar } from "@/components/assistant-ui/thread-list";
import {
  ComposerChin,
  ConversationCompany,
  MobileCompanyItem,
  MobileChatTitle,
  MobileFilingItems,
  WelcomeFiling,
} from "@/components/assistant-ui/projects";
import { CompanyPickerDialog } from "@/components/company-picker";
import { PendingInChat } from "@/components/pending";
import { Settings } from "@/components/settings";
import { ResultsContext, ResultsPanel, useResultFiles } from "@/components/results-panel";
import { type PanelMode, SessionPanel, useFiledCompany } from "@/components/session-panel";
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
import { clearDrafts } from "@/lib/managed-agents/drafts";
import { ManagedAgentsRuntimeProvider, useActiveSessionSnapshot } from "@/lib/managed-agents/runtime-provider";
import { connectionDetail, showsReconnecting } from "@/lib/managed-agents/turn";
import { openSettings, openSite, useAutoConnect, useConnectedSites, useDesktopBridge, useFramelessWindow, windowAction } from "@/lib/desktop";
import { chooseLanguage, siteFetch } from "@/lib/frappe";
import { isBusy } from "@/lib/managed-agents/reducer";
import type { SessionFile } from "@/lib/managed-agents/api";
import { onNotice, setActiveSession, setChatView } from "@/lib/managed-agents/activity";
import { noticeReloader } from "@/lib/managed-agents/notice-reload";
import { listen } from "@/lib/managed-agents/realtime";
import { modeForKey, noteViewMode, openOperator, switchMode, useAddressTracksConversation, useCurrentMode, useDeepLinks, useNavHistory, useThreadExit, useView, ViewContext, type Mode, type View } from "@/lib/nav";
import { OPEN_LAYERS } from "@/lib/layers";
import { useBackLayer } from "@/lib/back-stack";
import { useSidebarKeys } from "@/lib/sidebar-keys";
import { APP_NAME, assistantLogo, assistantMascot, loadProfile, useProfile, type Profile } from "@/lib/profile";
import { forgetDevice, syncPush, usePush } from "@/lib/push";
import { Mascot } from "@/components/mascot";
import { setDraft, useProjects, useSection } from "@/lib/managed-agents/projects";
import { useCompanyNav, watchPendingCounts } from "@/lib/companies";
import { dockFits, loadPanelPrefs, setPanelDocked, setPanelOpen, setPanelOverlay, usePanel } from "@/lib/company-panel";
import { cssPx } from "@/lib/css-var";
import { syncPrefs } from "@/lib/prefs-sync";
import { DeleteChatsHost, deleteChat } from "@/components/delete-chats";
import { co, greeting, HELP_URL, lang, LANGUAGES, op, siteUrl, t } from "./i18n";
import { withReport } from "./lib/caught";

type Nav = ReturnType<typeof useNavHistory>;

// 提醒挂到侧栏没加载到的对话时，几条提醒合成一次重拉列表
const NOTICE_RELOAD_MS = 1500;

function firstName(profile: Profile | null): string {
  return (profile?.full_name || "").trim().split(/\s+/)[0] ?? "";
}

/** 有定时任务这个功能的产品（产品声明的分区 tasks，首屏 describe 就带回来，同 Pearnly 那边：目前只有 Cowork）。 */
function hasScheduledTasks(profile: Profile | null): boolean {
  return !!profile?.sections?.includes("tasks");
}

/** 有代录 Express 的产品（站点声明了 pearnly_express_operator：Cowork 与 ERP）。 */
function hasOperator(profile: Profile | null): boolean {
  return profile?.product === "cowork" || profile?.product === "erp";
}

// ── 代录（两种模式）─────────────────────────────────────────────────────────────────────────────────

/** 这个产品有代录（左上角切换、执行电脑上读桥）。 */
const OperatorContext = createContext(false);

function useMode() {
  const { view, setView } = useView();
  const mode = useCurrentMode();
  const change = useCallback((next: Mode) => switchMode(next, view, setView), [view, setView]);
  return { mode, change };
}

/** 左上角「直写 | 实操」两个图标（没有实操的产品不显示）。 */
function Modes({ className }: { className?: string }) {
  const operator = useContext(OperatorContext);
  const { mode, change } = useMode();
  if (!operator) return null;
  return <ModeSwitch value={mode} onChange={change} className={className} />;
}

/** Ctrl / ⌘ + Shift + 1 / 2 切模式；代录模式下 Ctrl / ⌘ + N 新建任务（看着公司、待处理页时也是）。弹窗、菜单开着时不管。 */
function useModeKeys(enabled: boolean) {
  const aui = useAui();
  const { mode: current, change } = useMode();
  const { view, setView } = useView();
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.repeat || document.querySelector(OPEN_LAYERS)) return;
      const mode = modeForKey(event);
      if (mode) {
        event.preventDefault();
        change(mode);
        return;
      }
      if (current === "operator" && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.code === "KeyN") {
        event.preventDefault();
        newInOperator(aui, view, setView);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [aui, enabled, change, current, view, setView]);
}

/** 代录模式下「新建」：同直写开一段新对话（新建页就是直写的新建页，发出去建的是代录会话），主栏留在代录。 */
function newInOperator(aui: ReturnType<typeof useAui>, view: View, setView: (v: View) => void) {
  openOperator(view, setView);
  setDraft({});
  void aui.threads().switchToNewThread();
}


// 空对话：胖橘猫 + 衬线问候（用户 2026-10-12：问候语旁用胖橘猫；电脑上在左，手机上在上）
function Welcome() {
  const profile = useProfile();
  const name = firstName(profile);
  const mascot = assistantMascot();
  const hello = name ? `${greeting()}${lang === "zh" ? "，" : lang === "ja" ? "、" : ", "}${name}` : greeting();
  return (
    <div className="flex flex-col items-center gap-(--cc-welcome-gap)">
      {/* 在哪儿打开就引导装到哪儿（电脑下载桌面版、手机装成应用），关掉后不再出现 */}
      <InstallBanner />
      <div className="aui-thread-welcome-root cc-enter -mb-(--cc-space-8) flex flex-col items-center justify-center gap-(--cc-space-8) px-(--cc-space-16) text-center md:flex-row md:gap-(--cc-space-12)">
        {/* 胖橘右上方举着泡泡：图的上沿就是泡泡顶，文字对着整只猫居中 */}
        {/* 问候语换了（名字取回、换了时段）再播一次小动画（上一次没播完就不打断） */}
        {mascot && <Mascot src={mascot} play={hello} className="pearnly-mascot-host h-(--cc-mascot-h) shrink-0 md:h-(--cc-mascot-h-wide)" />}
        <h1 className="cc-greeting text-3xl">{hello}</h1>
      </div>
      {/* 手机：公司、月份、分店在问候下面（同线上旧版的公司条）；电脑在输入框下巴里 */}
      <WelcomeFiling />
    </div>
  );
}

/** 打开已有对话、消息还没取回时。手机同 Claude 手机端：形象居中轻轻浮动，下面一行「正在加载消息」；
 *  电脑同 Claude 网页（11-09 对照审计 #12，截图 docs/work/2026-11-09-cc-vs-claude-ux-audit/claude-08）：按消息的位置画灰色骨架——
 *  右边一条提问气泡、下面三段回复的细条，内容到了原地换掉。两种都画，按外壳变体只显示一种（globals.css .cc-loading）。 */
function ThreadLoading() {
  const mascot = assistantMascot();
  return (
    <div className="cc-loading" role="status">
      <div className="cc-loading-mascot">
        {mascot && <Mascot src={mascot} className="pearnly-mascot-host" />}
        {t("loadingMessages")}
      </div>
      <div className="cc-loading-skeleton" aria-hidden="true">
        <Skeleton className="cc-loading-ask h-(--cc-control-lg) rounded-(--cc-radius-lg)" />
        {LOADING_LINES.map((lines, i) => (
          <div key={i} className="cc-loading-para">
            {lines.map((w, j) => (
              <Skeleton key={j} className="cc-loading-line h-(--cc-space-8) rounded-full" data-w={w} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 骨架三段回复每行的长短（占栏宽的百分比档，globals.css 的 .cc-loading-line[data-w]），照 Claude 那张截图的样子。 */
const LOADING_LINES = [["96", "100", "92", "58"], ["100", "94", "41"], ["97", "90", "66"]] as const;

function SessionStatusBar() {
  const snapshot = useActiveSessionSnapshot();
  // 原始报错（多是英文）只进控制台排查，界面只说本地化的「正在重新连接…」（turn.ts 的 showsReconnecting）
  const detail = connectionDetail(snapshot);
  useEffect(() => {
    if (detail) console.debug("[cc] connection:", detail);
  }, [detail]);
  if (!snapshot.sessionId || !showsReconnecting(snapshot)) return null;
  return (
    <div data-slot="cc-status" className="cc-status mx-auto w-full max-w-(--cc-thread-w) px-(--cc-space-16) pt-(--cc-space-8) text-ink-3 text-caption">
      {t("reconnecting")}
    </div>
  );
}

async function signOut() {
  // 先让本站不再往这台设备推这个人的提醒（lib/push.ts；最多等 3 秒，不挡退出）
  await forgetDevice().catch(withReport(() => undefined, "assistant"));
  // 这台设备上本人的输入框草稿（drafts.ts）一并清掉
  clearDrafts();
  // 走本站原生的统一登出（pearnly_core.identity.sign_out：结束站点会话并在身份服务登出），退出后回到智能体入口。
  const res = await siteFetch("/api/method/pearnly_core.identity.sign_out?to=assistant", {
    method: "POST",
    headers: { "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
  });
  const body = (await res.json().catch(() => null)) as { message?: { redirect_url?: string } } | null;
  // map: logout
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
  // CC 窗口里：这台电脑连着两个以上站点（Cowork + ERP）时，在这里直接切换（当前站点打勾），不用退出再登录
  const sites = useConnectedSites();
  if (!profile) return null;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>{children}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="top" align="start" sideOffset={menuOffset()} className="cc-menu">
          <Marquee as="div" className="cc-menu-label truncate">{profile.user}</Marquee>
          {sites.length > 1 && (
            <>
              {sites.map((site) => (
                <DropdownMenu.Item key={site} className="cc-menu-item" data-slot="cc-site" onSelect={() => openSite(site)}>
                  <Marquee className="min-w-0 flex-1 truncate">{site}</Marquee>
                  {site === location.host && <CheckIcon />}
                </DropdownMenu.Item>
              ))}
              <DropdownMenu.Separator className="cc-menu-sep" />
            </>
          )}
          <DropdownMenu.Item onSelect={() => openSettings()} className="cc-menu-item">
            <SettingsIcon />
            <span className="flex-1">{t("settings")}</span>
            <Shortcut keys="Ctrl ," />
          </DropdownMenu.Item>
          <SubMenu icon={<GlobeIcon />} label={t("language")}>
            {LANGUAGES.map((l) => (
              <DropdownMenu.Item key={l.code} className="cc-menu-item" onSelect={() => void chooseLanguage(l.code)}>
                <span className="flex-1">{l.label}</span>
                {l.code === lang && <CheckIcon />}
              </DropdownMenu.Item>
            ))}
          </SubMenu>
          <DropdownMenu.Item asChild className="cc-menu-item">
            <a href={HELP_URL} target="_blank" rel="noopener">
              <CircleHelpIcon />
              <span className="flex-1">{t("help")}</span>
              <ExternalLinkIcon className="text-ink-3" />
            </a>
          </DropdownMenu.Item>
          <SubMenu icon={<InfoIcon />} label={t("learnMore")}>
            <MenuLink href={siteUrl("/about/")}>{t("about")}</MenuLink>
            <DropdownMenu.Separator className="cc-menu-sep" />
            <MenuLink href={siteUrl("/privacy/")}>{t("privacy")}</MenuLink>
            <MenuLink href={siteUrl("/terms/")}>{t("terms")}</MenuLink>
          </SubMenu>
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

// 侧栏底部托盘（设计文档 3.1、4.2，同 Claude Code）：高 48；头像 + 名字 · 产品（点开用户菜单，高 32 同托盘按钮），右边 32 的「设置」
function UserFooter() {
  const profile = useProfile();
  if (!profile) return null;
  return (
    <>
      <UserMenu>
        <button type="button" className="cc-nav-item h-(--cc-tray-btn) min-w-0 flex-1" data-slot="cc-user-menu">
          <span className="cc-nav-icon">
            <span className="cc-avatar size-(--cc-avatar-sm)">{initials(profile)}</span>
          </span>
          <Marquee className="cc-nav-label min-w-0 flex-1 truncate">
            {profile.full_name || profile.user}
            <span className="text-ink-3"> · {profile.product_label}</span>
          </Marquee>
        </button>
      </UserMenu>
      <button type="button" className="cc-icon-btn" data-slot="cc-settings-button" onClick={() => openSettings()} aria-label={t("settings")} title={t("settings")}>
        <SettingsIcon />
      </button>
    </>
  );
}

// ── 桌面端标题栏（同 Claude 桌面端）────────────────────────────────────────────────────────────────

// 菜单「☰」：同 Claude 桌面端的应用菜单，只放 CC 已有的动作
function AppMenu() {
  const aui = useAui();
  const { view, setView } = useView();
  const operator = !!useContext(OperatorContext);
  const { mode, change } = useMode();
  const zoom = (step: number) => void windowAction("zoom", { step });
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="cc-icon-btn" aria-label={t("menu")} title={t("menu")}>
        <MenuIcon />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={menuOffset()} className="cc-menu">
          {/* 两种模式同一个「新建会话」（代录模式下主栏留在代录） */}
          <DropdownMenu.Item
            className="cc-menu-item"
            onSelect={() => {
              if (mode === "operator") return newInOperator(aui, view, setView);
              setView("chat"); setDraft({}); aui.threads().switchToNewThread();
            }}
          >
            <span className="flex-1">{t("newChatPill")}</span>
            <Shortcut keys="Ctrl N" />
          </DropdownMenu.Item>
          {operator && (
            <>
              <DropdownMenu.Item className="cc-menu-item" data-slot="cc-mode-chat" onSelect={() => change("chat")}>
                <span className="flex-1">{op.direct}</span>
                <Shortcut keys="Ctrl Shift 1" />
              </DropdownMenu.Item>
              <DropdownMenu.Item className="cc-menu-item" data-slot="cc-mode-operator" onSelect={() => change("operator")}>
                <span className="flex-1">{op.handsOn}</span>
                <Shortcut keys="Ctrl Shift 2" />
              </DropdownMenu.Item>
            </>
          )}
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
// navToggle：放在对话区顶栏（侧栏收起时）的那一份，侧栏开关标成顶栏导航钮；侧栏标题栏里那一份标成侧栏的落点（删完对话一行不剩、
// 侧栏展开时焦点掉了的落点，lib/sidebar-focus.ts）
function TitleBarLeft({ nav, navToggle = false }: { nav: Nav; navToggle?: boolean }) {
  const { toggleSidebar } = useSidebar();
  return (
    <div className="flex items-center gap-(--cc-titlebar-gap)">
      <AppMenu />
      <button type="button" className="cc-icon-btn" data-slot={navToggle ? "cc-nav-toggle" : undefined} data-cc-nav-fallback={navToggle ? undefined : ""} onClick={toggleSidebar} aria-label={t("sidebar")} title={t("sidebar")}>
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
        {maximized ? <CopyIcon className="size-(--cc-icon-xs) scale-x-(--cc-mirror)" /> : <SquareIcon className="size-(--cc-icon-xs)" />}
      </button>
      <button type="button" className="cc-winbtn" data-close="" onClick={() => void windowAction("close")} aria-label={t("closeWindow")} title={t("closeWindow")}>
        <XIcon />
      </button>
    </div>
  );
}

// ── 侧栏 ───────────────────────────────────────────────────────────────────────────────────────

// 顶部导航（设计文档 3.3、4.2）：两种模式共用一个框架（主会话 11-03，照 Claude 桌面版）——先是两边共有的：新建（按模式：新对话 /
// 新建任务）；再是模式自己的：定时任务（只对话模式）。没有「公司」（用户 11-07：账套已在连接里接好，对话里点「公司」弹窗选，
// 公司页上的改名、删除等搬到组头与公司卡的「···」）。共有的在上，切模式时一格都不挪。没有「待处理」（用户 11-04：并进对话）。
// 只有当前页选中：只看某家的定时任务与主档页都不算，顶部那一行不选中。键盘上与组标题、
// 对话行是同一组 roving 焦点（data-roving）。手机抽屉没有「新建」这一行（底部胶囊），其余同一顺序。onPick：手机上点了收起抽屉
function SidebarNav({ mobile = false, onPick }: { mobile?: boolean; onPick?: () => void }) {
  const items = (
    <>
      {!mobile && <NewNav />}
      <ScheduledNav onPick={onPick} />
    </>
  );
  return mobile ? items : <div className="flex flex-col gap-(--cc-row-gap)">{items}</div>;
}

/** 「新建」：两种模式同一个（直写的新对话）；代录模式下主栏留在代录，新建页就是直写的新建页。 */
function NewNav() {
  const { view, setView } = useView();
  const mode = useCurrentMode();
  return <ThreadListNew onClick={() => (mode === "operator" ? openOperator(view, setView) : setView("chat"))} />;
}

/** 侧栏「定时任务」（只对话模式、只在声明了 tasks 的产品）：在共有入口下面，代录模式下不出现也不挤动上面几行。 */
function ScheduledNav({ onPick }: { onPick?: () => void }) {
  const profile = useProfile();
  const mode = useCurrentMode();
  const { view, setView } = useView();
  const taskCompany = useCompanyNav((s) => s.taskCompany);
  if (!hasScheduledTasks(profile) || mode !== "chat") return null;
  return (
    <button
      type="button"
      data-roving=""
      className="cc-nav-item"
      data-slot="cc-nav-tasks"
      title={t("scheduledTasks")}
      data-active={(view === "tasks" && !taskCompany) || undefined}
      onClick={() => {
        useCompanyNav.setState({ taskCompany: "", taskFocus: "" });
        setView("tasks");
        onPick?.();
      }}
    >
      <span className="cc-nav-icon"><ClockIcon /></span>
      <span className="cc-nav-label">{t("scheduledTasks")}</span>
    </button>
  );
}

/** 点了某个对话：回到对话视图（从「定时任务」切回来）。电脑上 Cowork 带 Shift / ⌘ / Ctrl 是多选（不打开，thread-list.tsx）；
 *  ERP / DMS、手机没有多选，组件库照样切换对话，视图也跟着回到对话。 */
function useBackToChatOnPick(onNavigate?: () => void) {
  const { setView } = useView();
  const mode = useCurrentMode();
  const { isMobile } = useSidebar();
  const multi = !!useProjects((s) => s.kind) && !isMobile;
  return (e: React.MouseEvent) => {
    if (multi && (e.shiftKey || e.metaKey || e.ctrlKey)) return;
    if ((e.target as HTMLElement).closest('[data-slot="aui_thread-list-item-trigger"]')) {
      // 代录模式同一个侧栏（只列代录会话）：点了留在代录
      setView(mode === "operator" ? "operator" : "chat");
      onNavigate?.();
    }
  };
}

function DesktopSidebar({ nav, window: win, chrome, onResize }: { nav: Nav; window: ReturnType<typeof useMaximized>; chrome: boolean; onResize: (width: number | null) => void }) {
  const [search, setSearch] = useState("");
  const pick = useBackToChatOnPick();
  const companies = !!useProjects((s) => s.kind);
  const keys = useSidebarKeys(companies);
  const { toggleSidebar } = useSidebar();
  const logo = assistantLogo();
  return (
    <>
      <SidebarHeader className="pt-0 pb-(--cc-sidebar-head-pb)">
        {chrome ? (
          <>
            <div className="cc-titlebar -mx-(--cc-space-2)" {...dragHandlers(win.toggle)}>
              <TitleBarLeft nav={nav} />
              {/* 直写 / 实操：→ 右边、靠这一行右端 */}
              <Modes className="ms-auto" />
            </div>
          </>
        ) : (
          // 标题区 44（设计文档 3.1）：「图标 + CC」在左、24 的收起钮在右（用户 2026-10-12：放右边更顺手）；收起后只在左上角
          // 留展开按钮（MainTopBar）
          <div className="flex h-(--cc-sidebar-head-h) items-center justify-between ps-(--cc-space-6) pe-(--cc-space-4)">
            <span className="flex min-w-0 items-center gap-(--cc-space-8)">
              <span className="flex shrink-0 items-center gap-(--cc-space-8) font-semibold text-ink text-lg">
                {logo && <img src={logo} alt="" className="cc-logo size-(--cc-logo)" />}
                {APP_NAME}
              </span>
            </span>
            <span className="flex items-center gap-(--cc-space-4)">
              <Modes />
              <button type="button" className="cc-row-btn" data-slot="cc-sidebar-toggle" data-cc-nav-fallback="" onClick={toggleSidebar} aria-label={t("sidebar")} title={t("sidebar")}>
                <PanelLeftIcon />
              </button>
            </span>
          </div>
        )}
        <ThreadListSearch value={search} onValueChange={setSearch} />
      </SidebarHeader>
      <SidebarContent ref={keys} className="px-(--cc-sidebar-pad)" onClickCapture={pick} data-cc-view={useView().view}>
        <SidebarNav />
        <ThreadList search={search} />
      </SidebarContent>
      <SidebarFooter className="h-(--cc-sidebar-foot-h) flex-row items-center gap-(--cc-space-4) border-t border-line py-0">
        <UserFooter />
      </SidebarFooter>
      <SplitHandle size={sidebarSize} edge="end" onChange={onResize} panel={(handle) => handle.closest<HTMLElement>('[data-slot="sidebar-container"]')} />
    </>
  );
}

// 手机抽屉（同 Claude 手机端）：大号「CC」+ 圆形搜索；待处理、公司、定时任务（只对话模式）；对话 / 任务列表；底部头像与「新建」胶囊
// （按模式：新建会话 / 新建任务）
function MobileDrawer() {
  const { setOpenMobile } = useSidebar();
  const aui = useAui();
  const profile = useProfile();
  const { view, setView } = useView();
  const operator = useCurrentMode() === "operator";
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  // 在抽屉里打字（改名、搜索）时键盘弹起：底部头像与「新建会话」那一条收起，不挡住正在改的那几行
  const [typing, setTyping] = useState(false);
  const close = () => setOpenMobile(false);
  const pick = useBackToChatOnPick(close);
  return (
    <div
      className="relative flex h-full flex-col pt-[env(safe-area-inset-top)]"
      onFocusCapture={(e) => setTyping(isTextField(e.target))}
      onBlurCapture={() => setTyping(false)}
    >
      <div className="flex items-center justify-between px-(--cc-mdrawer-px) pt-(--cc-space-4) pb-(--cc-space-12)">
        <span className="cc-drawer-title">{APP_NAME}</span>
        <span className="flex items-center gap-(--cc-space-8)">
          <Modes />
          <button type="button" className="cc-round-btn" data-cc-nav-fallback="" onClick={() => setSearching((v) => !v)} aria-label={t("search")}>
            <SearchIcon />
          </button>
        </span>
      </div>
      {searching && (
        <div className="cc-enter px-(--cc-mdrawer-px) pb-(--cc-space-8)">
          <ThreadListSearch value={search} onValueChange={setSearch} autoFocus />
        </div>
      )}
      <div className="cc-drawer flex-1 overflow-y-auto px-(--cc-mdrawer-list-px) pb-(--cc-mdrawer-tail)" onClickCapture={pick}>
        <SidebarNav mobile onPick={close} />
        <ThreadList search={search} />
      </div>
      <div
        data-slot="cc-drawer-foot"
        hidden={typing}
        className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between bg-linear-to-t from-drawer via-drawer to-transparent px-(--cc-mdrawer-tray-px) pt-(--cc-mdrawer-tray-pt) pb-[max(var(--cc-mdrawer-bottom),env(safe-area-inset-bottom))]">
        {profile && (
          <UserMenu>
            <button type="button" className="cc-avatar pointer-events-auto size-(--cc-mavatar) text-body" aria-label={profile.full_name || profile.user}>
              {initials(profile)}
            </button>
          </UserMenu>
        )}
        <button
          type="button"
          className="cc-pill pointer-events-auto"
          onClick={() => {
            if (operator) newInOperator(aui, view, setView);
            else {
              setView("chat");
              setDraft({});
              aui.threads().switchToNewThread();
            }
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

/** 会弹出键盘的输入框（文字输入框、多行输入、可编辑区）。 */
function isTextField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || target instanceof HTMLTextAreaElement) return true;
  return target instanceof HTMLInputElement && !["button", "checkbox", "radio", "range", "submit", "reset", "file", "color"].includes(target.type);
}

/** 手机顶栏打开抽屉的图标（同 Claude 手机端）：三条线、最下面一条短。 */
function DrawerIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" className="stroke-(length:--cc-icon-stroke)" aria-hidden>
      <path d="M4 7h16M4 12h16M4 17h10" />
    </svg>
  );
}

// 对话区顶部。
// - 手机（同 Claude 手机端对话页）：左边菜单，中间两行标题（对话名、公司名；8.webp），右边胶囊（新建对话、「…」对话选项），都浮在内容上。
// - 电脑（设计文档 3.9 / 4.3，高 32）：对话有内容时左上角「公司名 ›」+ 对话名（点了就地改名）+「···」（置顶、改名、删除）；
//   右上 26 的按钮：本对话的文件 N（有文件才出现）、右侧会话栏开关（挂了公司的 Cowork 对话才有）。
// - 桌面端：以上放进自画的标题栏，右边是窗口按钮。
function MainTopBar({ nav, window: win, chrome, tools }: { nav: Nav; window: ReturnType<typeof useMaximized>; chrome: boolean; tools: ReactNode }) {
  const { open, isMobile, setOpenMobile, toggleSidebar } = useSidebar();
  const { view, setView } = useView();
  const aui = useAui();
  const hasMessages = useAuiState((s) => s.thread.messages.length > 0);
  // 代录模式的主栏就是对话页（代录任务是一段会话），顶栏同对话页
  const inChat = (view === "chat" || view === "operator") && hasMessages;
  if (isMobile) {
    // 同 Claude 手机端的对话页：没有横条，按钮浮在内容上，顶上一层上实下透的渐变；对话里中间是两行标题（不可点）；抽屉打开时照样在
    // （随页面推开、被压淡）
    return (
      <div className="cc-mtopbar">
        <button type="button" className="cc-round-btn" data-slot="cc-nav-toggle" onClick={() => setOpenMobile(true)} aria-label={t("menu")}>
          <DrawerIcon />
        </button>
        {inChat ? <MobileChatTitle /> : <span />}
        {inChat ? <MobileChatPill onNew={() => { setView("chat"); setDraft({}); aui.threads().switchToNewThread(); }} /> : <span className="cc-mtopbar-slot" />}
      </div>
    );
  }
  if (chrome) {
    return (
      <div className="cc-titlebar shrink-0 justify-between pe-0" {...dragHandlers(win.toggle)}>
        <div className="flex min-w-0 items-center gap-(--cc-topbar-gap)">
          {!open && <TitleBarLeft nav={nav} navToggle />}
          {/* 侧栏收起：直写 / 实操跟着那一行留在顶栏 */}
          {!open && <Modes />}
          {inChat && <ConversationTitle />}
        </div>
        <div className="flex items-center gap-(--cc-topbar-gap)">
          {tools}
          <WindowButtons maximized={win.maximized} toggle={win.toggle} />
        </div>
      </div>
    );
  }
  if (open && !inChat && !tools) return null;
  return (
    <div className="cc-topbar">
      {!open && (
        <button type="button" className="cc-icon-btn" data-slot="cc-nav-toggle" onClick={toggleSidebar} aria-label={t("sidebar")} title={t("sidebar")}>
          <PanelLeftIcon />
        </button>
      )}
      {!open && <Modes />}
      {inChat && <ConversationTitle />}
      <div className="ms-auto flex items-center gap-(--cc-topbar-gap)">{tools}</div>
    </div>
  );
}

// 手机对话页右上角的胶囊（同 Claude 手机端）：新建对话 + 「…」（公司名 = 打开右侧会话栏、置顶、改名、处理月份、分店、删除）
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
          <DropdownMenu.Content align="end" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-sm)">
            {/* 挂在公司下时最上面是公司名：点了全屏打开右侧会话栏 */}
            <MobileCompanyItem />
            {/* 取消置顶回到它那一组的最上面（starChat）；失败写进侧栏出错行 */}
            <DropdownMenu.Item className="cc-menu-item" onSelect={() => toggleStar(aui.threadListItem(), !starred)}>
              {starred ? <PinOffIcon /> : <PinIcon />}
              {starred ? t("unstar") : t("star")}
            </DropdownMenu.Item>
            <DropdownMenu.Item className="cc-menu-item" onSelect={rename}>
              <PencilIcon />
              {t("rename")}
            </DropdownMenu.Item>
            <MobileFilingItems />
            <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={() => deleteChat(aui.threadListItem())}>
              <TrashIcon />
              {t("delete")}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}

// 顶部（设计文档 3.9）：「公司名 ›」（挂在公司下时，点了打开右侧会话栏的公司卡）+ 对话名（点了就地改名）+「···」。
// 月份、分店胶囊在输入框下巴里。
function ConversationTitle() {
  return (
    <div className="flex min-w-0 items-center gap-(--cc-space-2)">
      <ConversationCompany />
      <ConversationName />
    </div>
  );
}

function ConversationName() {
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const [renaming, setRenaming] = useState(false);
  if (renaming) return <ThreadListItemRename onDone={() => setRenaming(false)} className="cc-title-edit" />;
  return (
    <>
      <button type="button" className="cc-title-btn" title={t("rename")} onClick={() => setRenaming(true)} onDoubleClick={(e) => e.stopPropagation()}>
        <Marquee className="truncate">{title || t("untitled")}</Marquee>
      </button>
      <ConversationMenu onRename={() => setRenaming(true)} />
    </>
  );
}

// 对话名旁的「···」（同 Claude Code）：置顶、改名（就地改）、删除（对话不能换公司，用户 10-17 删了「移到 ▸」）
function ConversationMenu({ onRename }: { onRename: () => void }) {
  const aui = useAui();
  const custom = useAuiState((s) => s.threadListItem.custom);
  const starred = !!custom?.starred;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="cc-row-btn" aria-label={t("conversationMenu")} title={t("conversationMenu")} onDoubleClick={(e) => e.stopPropagation()}>
        <MoreHorizontalIcon />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={menuOffset()} className="cc-menu min-w-(--cc-menu-w-sm)">
          {/* 取消置顶回到它那一组的最上面（starChat）；失败写进侧栏出错行 */}
          <DropdownMenu.Item className="cc-menu-item" onSelect={() => toggleStar(aui.threadListItem(), !starred)}>
            {starred ? <PinOffIcon /> : <PinIcon />}
            {starred ? t("unstar") : t("star")}
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" onSelect={onRename}>
            <PencilIcon />
            {t("rename")}
          </DropdownMenu.Item>
          <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={() => deleteChat(aui.threadListItem())}>
            <TrashIcon />
            {t("delete")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

// 右侧结果面板：新结果出现时自动打开最新的一个；对话里的文件卡片、右上角按钮随时打开（同 claude.ai 的 Artifacts）。
// 消息气泡里用户发的附件点开也在这里预览（同 Claude）：附件不在结果文件里，面板临时多列点开的那一个（右上角「文件 N」仍只数结果）。
function useResultsPanel() {
  const { sessionId, isRunning } = useActiveSessionSnapshot();
  const { files: outputs, uploads, fresh, clearFresh } = useResultFiles(sessionId, isRunning);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [upload, setUpload] = useState<SessionFile | null>(null);
  const files = useMemo(
    () => (upload && !outputs.some((f) => f.id === upload.id) ? [...outputs, upload] : outputs),
    [outputs, upload],
  );
  useEffect(() => {
    setOpen(false);
    setSelected(null);
    setUpload(null);
  }, [sessionId]);
  useEffect(() => {
    if (fresh) {
      setSelected(null);
      setUpload(null);
      setOpen(true);
      clearFresh();
    }
  }, [fresh, clearFresh]);
  const openFile = useCallback((id: string) => {
    setSelected(id);
    setOpen(true);
  }, []);
  // 右上角「文件 N」：只列结果文件，打开最新的一个
  const showResults = useCallback(() => {
    setUpload(null);
    setSelected(null);
    setOpen(true);
  }, []);
  const openUpload = useCallback((file: SessionFile) => {
    setUpload(file);
    setSelected(file.id);
    setOpen(true);
  }, []);
  return { sessionId, files, outputs, uploads, selected, setSelected, openFile, openUpload, showResults, open: open && files.length > 0, setOpen };
}

// 桌面端快捷提问、回复完成与提醒通知、整窗拖文件、登录后自动连接这台电脑（lib/desktop.ts）；推送通知（lib/push.ts）
function DesktopBridge() {
  const snapshot = useActiveSessionSnapshot();
  const title = useAuiState((s) => s.threadListItem.title) ?? "";
  const push = usePush();
  const { view } = useView();
  // 挂在服务端等识别时模型空闲（只显示发送），但这一轮没完：不当成「回复完了」去发桌面通知
  useDesktopBridge(isBusy(snapshot) || snapshot.waitingIds.length > 0, snapshot.messages, title, push.state === "on", snapshot.sessionId);
  useAutoConnect(useProfile()?.product);
  // 一打开就接上实时通道：侧栏才看得到后台对话在跑、跑完（lib/managed-agents/activity.ts）
  useEffect(() => listen(), []);
  useEffect(() => void syncPush(), []);
  // 待处理按公司、按「公司 · 月份」的数（公司列表上的数、侧栏的占位行、对话末尾的「需要处理」框）：只在声明了待处理的产品读
  // （推送状态的站点事件来了、回到前台再读）
  const pending = useSection("pending");
  useEffect(() => (pending ? watchPendingCounts() : undefined), [pending]);
  useEffect(() => setActiveSession(snapshot.sessionId), [snapshot.sessionId]);
  // 在定时任务页时当前对话不算「正看着」：推来的提醒留圆点、不报看过；回到对话页才算
  useEffect(() => setChatView(view === "chat" || view === "operator"), [view]);
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

// 深链接：/assistant?c=<对话>、?view=tasks，推送通知与桌面端托盘通知点开（lib/nav.ts）；之后地址跟着正在看的对话（批 A ③：
// 刷新回到原对话。排在深链接之后：打开页面时它先读 ?c=）
function DeepLinks() {
  const { view, setView } = useView();
  useDeepLinks(setView, () => loadProfile().then(hasScheduledTasks));
  useAddressTracksConversation(view);
  return null;
}

/** 主区那一行放不放得下「对话最窄 + 右侧会话栏」（放不下时会话栏浮在对话上）。
 *  el 是那一行的节点（回调 ref 存进 state）：手机与电脑外壳互换时这一行会卸下重建，跟着新节点重新量，不盯着已脱离页面的旧节点。 */
function useDockFits(el: HTMLDivElement | null) {
  const [fits, setFits] = useState(true);
  // 画第一帧前就量好：窄窗口记着「开着」时不先按并排画一帧再收起
  useLayoutEffect(() => {
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const style = getComputedStyle(el);
      const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      setFits(dockFits(el.clientWidth, cssPx(style.getPropertyValue("--cc-chat-min-w"), root), cssPx(style.getPropertyValue("--cc-panel-w"), root)));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [el]);
  return fits;
}

function Layout({ results, onSidebarResize }: { results: ReturnType<typeof useResultsPanel>; onSidebarResize: (width: number | null) => void }) {
  const { view, setView } = useView();
  const nav = useNavHistory(view, setView);
  // 换模式时正在看的对话不属于新模式就去新建页（lib/nav.ts 的 leaveForeignThread）
  useThreadExit();
  const operator = useContext(OperatorContext);
  useModeKeys(operator);
  useOperatorBridge(operator);
  const chrome = useFramelessWindow();
  const win = useMaximized(chrome);
  const { isMobile, openMobile, setOpenMobile } = useSidebar();
  // 右侧只有一个停靠位（设计文档 4.4）：文件预览开着时是预览，否则挂了公司的 Cowork 对话里是会话栏（ERP / DMS、没挂公司的没有）。
  // 宽屏并排（开关记在本机）；对话区放不下时浮在对话上、手机盖满屏（点了才开，换对话收起）
  const profile = useProfile();
  useEffect(() => loadPanelPrefs(profile?.user ?? ""), [profile?.user]);
  // 个人排列（侧栏手排顺序、分组和排序、会话栏卡片顺序与开合）按人存在服务端：知道是谁了就取，本机记下的先顶着（lib/prefs-sync.ts）
  useEffect(() => void syncPrefs(profile?.user ?? ""), [profile?.user]);
  const [area, setArea] = useState<HTMLDivElement | null>(null);
  const fits = useDockFits(area);
  const filing = useFiledCompany();
  // 已建好的对话（有服务端编号）：不看消息条数，换对话时线程加载期间消息为 0，会话栏不跟着卸下再挂上
  const saved = useAuiState((s) => !!s.threadListItem.remoteId);
  const session = useAuiState((s) => s.threadListItem.remoteId) ?? "";
  const panelOpen = usePanel((s) => s.open);
  const overlay = usePanel((s) => s.overlay);
  const mode: PanelMode = isMobile ? "phone" : fits ? "docked" : "floating";
  useEffect(() => setPanelDocked(mode === "docked"), [mode]);
  const hasPanel = view === "chat" && saved && !!filing.project;
  const panelShown = hasPanel && !(view === "chat" && results.open) && (mode === "docked" ? panelOpen : overlay);
  useEffect(() => setPanelOverlay(false), [results.sessionId, view]);
  // 点了「公司名 ›」（或手机菜单里的公司名）：预览开着时换回会话栏
  const focusAt = usePanel((s) => s.focus?.at ?? 0);
  const { setOpen: setPreviewOpen } = results;
  useEffect(() => {
    if (focusAt) setPreviewOpen(false);
  }, [focusAt, setPreviewOpen]);
  const closePanel = useCallback(() => (mode === "docked" ? setPanelOpen(false) : setPanelOverlay(false)), [mode]);
  const togglePanel = () => {
    const preview = view === "chat" && results.open;
    if (preview) results.setOpen(false);
    const next = preview || !panelShown;
    if (mode === "docked") setPanelOpen(next);
    else setPanelOverlay(next);
  };
  // 手机返回键（lib/back-stack.ts）：先关抽屉、盖满屏的结果面板与会话栏；组件库的弹窗与菜单不用登记
  useBackLayer(isMobile && openMobile, () => setOpenMobile(false));
  useBackLayer(isMobile && view === "chat" && results.open, () => results.setOpen(false));
  useBackLayer(panelShown && mode !== "docked", () => setPanelOverlay(false));
  // 本对话产出的文件（同 claude.ai 右上角）：面板关着时显示，点开右侧面板。手机上不放（同 Claude 手机端，对话里的文件卡片点开即可）
  const filesButton =
    view === "chat" && results.outputs.length > 0 && !results.open ? (
      <button
        type="button"
        onClick={results.showResults}
        title={t("files")}
        aria-label={t("files")}
        className="cc-bar-btn"
      >
        <FilesIcon />
        {results.outputs.length}
      </button>
    ) : null;
  // 右侧会话栏开关（手机从胶囊菜单里的公司名打开）
  const panelButton = hasPanel ? (
    <button
      type="button"
      data-slot="cc-panel-toggle"
      onClick={togglePanel}
      aria-pressed={panelShown}
      title={t(panelShown ? "hidePanel" : "showPanel")}
      aria-label={t(panelShown ? "hidePanel" : "showPanel")}
      className="cc-bar-btn"
    >
      <PanelRightIcon />
    </button>
  ) : null;
  const tools = isMobile || view === "operator" ? null : filesButton || panelButton ? (
    <>
      {filesButton}
      {panelButton}
    </>
  ) : null;
  // 对话页本身（两种模式同一个：代录任务就是一段会话，方案 cc-unified-plan 14）
  const chatPage = (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <SessionStatusBar />
      <div className="cc-thread-slot min-h-0 flex-1">
        {/* 对话末尾的「需要处理」框（Tail）：待处理并进对话（用户 11-04） */}
        <Thread components={{ Welcome, Loading: ThreadLoading, ToolFallback: ManagedAgentsToolFallback, Chin: ComposerChin, Tail: PendingInChat }} />
      </div>
    </div>
  );
  // 代录（view = operator）是同一个对话页、没有右侧栏（用户 11-04：代录右侧不要；右侧栏只跟着 view = chat）
  const main = (
    <>
      <MainTopBar nav={nav} window={win} chrome={chrome} tools={tools} />
      <div ref={setArea} className="relative flex min-h-0 flex-1 flex-row">
        {view === "tasks" ? <ScheduledTasks /> : chatPage}
        {view === "chat" && results.open && results.sessionId && (
          <ResultsPanel
            sessionId={results.sessionId}
            files={results.files}
            selected={results.selected}
            onSelect={results.setSelected}
            onClose={() => results.setOpen(false)}
          />
        )}
        {panelShown && <SessionPanel session={session} filing={filing} mode={mode} onClose={closePanel} />}
      </div>
    </>
  );
  if (isMobile) return <MobileShell open={openMobile} onOpenChange={setOpenMobile} drawer={<MobileDrawer />} main={main} />;
  return (
    <div className="flex h-(--cc-vh) w-full bg-app">
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

// 侧栏收起 / 展开记在本机（设计文档 3.1）：刷新后照旧；收起时左上角只留展开按钮（MainTopBar）。手机抽屉不记
const SIDEBAR_OPEN_KEY = "cc.sidebar.open";

function useSidebarOpen() {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_OPEN_KEY) !== "0";
    } catch {
      return true;
    }
  });
  const change = useCallback((next: boolean) => {
    setOpen(next);
    try {
      localStorage.setItem(SIDEBAR_OPEN_KEY, next ? "1" : "0");
    } catch {
      // 无痕窗口等存不了：本次照样收起
    }
  }, []);
  return [open, change] as const;
}

function Workspace() {
  const aui = useAui({ tools: Tools({ toolkit }) });
  const results = useResultsPanel();
  const context = useMemo(
    () => ({
      sessionId: results.sessionId,
      files: results.outputs,
      uploads: results.uploads,
      openFile: results.openFile,
      openUpload: results.openUpload,
    }),
    [results.sessionId, results.outputs, results.uploads, results.openFile, results.openUpload],
  );
  const [view, setViewState] = useState<View>("chat");
  // 进了模式自己的视图就是那个模式（lib/nav.ts 的 noteViewMode）；公司、待处理两种模式共有，不换模式
  const setView = useCallback((next: View) => {
    noteViewMode(next);
    setViewState(next);
  }, []);
  const viewContext = useMemo(() => ({ view, setView }), [view, setView]);
  const profile = useProfile();
  const operator = hasOperator(profile);
  const sidebarWidth = useStoredSize(sidebarSize.key, sidebarSize.cssVar, sidebarSize.unit);
  const [sidebarOpen, setSidebarOpen] = useSidebarOpen();
  return (
    <AuiProvider value={aui}>
      <ViewContext.Provider value={viewContext}>
        <OperatorContext.Provider value={operator}>
        <ResultsContext.Provider value={context}>
          <DesktopBridge />
          <DeepLinks />
          {/* 设置在 InstallProvider 里：「通知」一行在 iPhone 浏览器里要打开「添加到主屏幕」的步骤 */}
          <InstallProvider>
            <Settings />
            <CompanyPickerDialog />
            <SidebarProvider style={sidebarWidth.style} open={sidebarOpen} onOpenChange={setSidebarOpen}>
              <Layout results={results} onSidebarResize={sidebarWidth.update} />
            </SidebarProvider>
          </InstallProvider>
        </ResultsContext.Provider>
        </OperatorContext.Provider>
      </ViewContext.Provider>
    </AuiProvider>
  );
}

export function Assistant() {
  return (
    <ManagedAgentsRuntimeProvider>
      <Workspace />
      <DeleteChatsHost />
    </ManagedAgentsRuntimeProvider>
  );
}
