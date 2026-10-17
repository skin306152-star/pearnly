// 设置（同 Claude：左下角菜单 →「设置」，或 Ctrl+,）。左边分栏，右边内容；选中 / 悬停用全站同一套中性灰（--muted），不用暖色：
//
//   常规      — 主题、对话字体、对话文字大小、对话记录宽度、动效、语言（同 Claude 设置的「外观」）、通知（浏览器 / 手机推送）
//   连接      — 只在浏览器里（Cowork 站点）出现：池里的电脑（一台一行）、在不在线；连接、文件夹只在电脑上的 CC 里改。
//               第 6 批（ADR-021）：登记人或管理员可在「···」移除一台电脑；管理员另有 Express 的「全所可用」开关，
//               关掉以后按成员名单（MR.ERP 没有这个开关）
//   此电脑    — 只在 CC 桌面端出现（lib/desktop.ts 的 desktop()）：
//     连接    — 这台电脑连的公司（登录的账号自动连上，不用配对码）、Express 数据文件夹（Express 授权留在这里）；
//               下面「电脑」同浏览器里的「连接」（池里每台电脑、移除、全所可用）
//
// 单据文件夹改在公司主档页按公司选（ADR-020，只在 CC 桌面版里经原生选择框）：桌面版声明了 company_folders 时这里不再有
// 「网页可读取的文件夹」，只在还有以前授权、现在没人用的文件夹（state().legacy_roots）时多一行「以前授权的文件夹」可移除；
// 1.6.0 以前的桌面版没有 company_folders，保留原来的文件夹块过渡。
//     系统    — 开机自动启动、CC 开着时保持电脑唤醒 / 用电池时也保持（同 Claude，companion keep_awake.py；旧版桌面端没有这两项就不显示）、
//               快捷提问、版本与更新、日志
//
// 桌面端托盘「连接与设置」直接打开「连接」一栏（window.tomo.settings）。

import { CheckIcon, FolderIcon, MonitorIcon, MoonIcon, MoreHorizontalIcon, PlugIcon, Settings2Icon, SunIcon, XIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { DropdownMenu } from "@/components/ui/menu";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  COMPANY_FOLDERS,
  connectComputer,
  currentLink,
  type Desktop,
  type DesktopState,
  desktop,
  type Link,
  onLink,
  setTextSize,
  type TextSize,
  textSize,
} from "@/lib/desktop";
import { useInstallGuide } from "@/components/install-prompt";
import { Shortcut } from "@/components/shortcut";
import { type ChatFont, type ChatWidth, type Motion, pref, setPref, type Theme } from "@/lib/appearance";
import { chooseLanguage, postMethod, shownError, siteFetch } from "@/lib/frappe";
import { giveFocusBack, opener } from "@/lib/focus-return";
import { useProfile } from "@/lib/profile";
import { syncPush, usePush } from "@/lib/push";
import { cn } from "@/lib/utils";
import { co, dayLabel, lang, LANGUAGES, st, t } from "../i18n";
import { Marquee } from "@/components/ui/marquee";

type Section = "general" | "connections" | "system";

// 设置关掉后焦点回到打开它的控件；那个控件不在了（菜单项随菜单卸下、窄屏藏起）或没记到（焦点在 BODY 时按 Ctrl+,、Safari
// 鼠标点按钮不拿焦点）就先回齿轮、齿轮不在再回用户菜单按钮（按这个先后，不按页面上的先后：用户菜单按钮排在齿轮前面）
const SETTINGS_BACK = ['[data-slot="cc-settings-button"]', '[data-slot="cc-user-menu"]'] as const;

// 连接状态色：取 globals.css 的 --cc-status-*（深浅色各一套）
const STATUS_COLOR: Record<string, string> = Object.fromEntries(
  ["online", "processing", "attention", "offline", "connecting", "paused"].map((k) => [k, `var(--cc-status-${k})`]),
);

export function Settings() {
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<Section>("general");
  // 谁打开的（齿轮、用户菜单里的「设置」、Ctrl+,、公司页的按钮）：关掉后焦点还给它（第 6 轮：没有 Trigger，组件库关掉时
  // 还焦点找不到去处，Esc 关掉后焦点落到 BODY）
  const back = useRef<HTMLElement | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const remember = () => {
    const from = opener();
    if (!content.current?.contains(from)) back.current = from; // 开着时再按 Ctrl+, 不算
  };
  const [api, setApi] = useState<Desktop | null>(null);

  useEffect(() => {
    void desktop().then((found) => {
      setApi(found);
      void found?.call("lang"); // 托盘与通知跟对话页同一种语言
    });
  }, []);

  useEffect(() => {
    const show = (event: Event) => {
      const wanted = String((event as CustomEvent).detail || "general") as Section;
      setSection(["general", "connections", "system"].includes(wanted) ? wanted : "general");
      remember();
      setOpen(true);
    };
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === ",") {
        event.preventDefault();
        remember();
        setOpen(true);
      }
    };
    window.addEventListener("tomo-settings", show);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("tomo-settings", show);
      window.removeEventListener("keydown", key);
    };
  }, []);

  // 浏览器里打开（没有桌面端）：Cowork 站点只给只读的「连接」看连着哪台电脑；改只在电脑上的 CC 里（用户 2026-10-10）
  const product = useProfile()?.product;
  const web = !api && product === "cowork";
  const current = !api && !(web && section === "connections") && section !== "general" ? "general" : section;
  const titles: Record<Section, string> = { general: st.general, connections: st.connections, system: st.system };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[70] bg-overlay data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          ref={content}
          aria-describedby={undefined}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            giveFocusBack(back.current, SETTINGS_BACK);
          }}
          className="cc-modal fixed left-1/2 z-[71] flex h-[min(680px,calc(var(--cc-viewport-h)*0.92))] w-[min(980px,95vw)] [--cc-modal-share:0.92] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-dialog border bg-surface text-ink shadow-pop outline-none md:flex-row"
        >
          <nav className="flex shrink-0 gap-1 overflow-x-auto border-b bg-app p-3 md:w-56 md:flex-col md:border-r md:border-b-0">
            <DialogPrimitive.Title className="hidden px-2.5 pt-1 pb-2 text-muted-foreground text-xs md:block">
              {st.settings}
            </DialogPrimitive.Title>
            <NavItem active={current === "general"} onClick={() => setSection("general")} icon={<Settings2Icon />}>
              {st.general}
            </NavItem>
            {web && (
              <NavItem active={current === "connections"} onClick={() => setSection("connections")} icon={<PlugIcon />}>
                {st.connections}
              </NavItem>
            )}
            {api && (
              <>
                <div className="hidden px-2.5 pt-4 pb-2 text-muted-foreground text-xs md:block">{st.thisComputer}</div>
                <NavItem active={current === "connections"} onClick={() => setSection("connections")} icon={<PlugIcon />}>
                  {st.connections}
                </NavItem>
                <NavItem active={current === "system"} onClick={() => setSection("system")} icon={<MonitorIcon />}>
                  {st.system}
                </NavItem>
              </>
            )}
          </nav>
          <main className="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-6 md:px-9 md:py-8">
            <h2 className="mb-6 font-medium text-xl">{titles[current]}</h2>
            {current === "general" && <General />}
            {current === "connections" && api && <Connections api={api} />}
            {current === "connections" && web && <WebConnections />}
            {current === "system" && api && <System api={api} />}
          </main>
          {/* 关闭同其他弹窗的 ×：图标按钮（电脑 32、图标 20；手机 44） */}
          <DialogPrimitive.Close aria-label={t("close")} className="cc-icon-btn absolute top-3.5 right-3.5">
            <XIcon />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function NavItem({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: ReactNode; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        // 同侧栏导航行：行高按变体（网页 32、桌面窗口 30、手机 44）、圆角 8
        "flex h-(--cc-row-h) shrink-0 items-center gap-2.5 whitespace-nowrap rounded-(--cc-radius-row) px-2.5 text-start text-sm [&_svg]:size-4",
        active ? "bg-(--cc-bg-selected) font-medium" : "hover:bg-hover",
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="mt-8 mb-1 font-medium text-(length:--cc-text-heading) first:mt-0">{children}</h3>;
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b py-4 last:border-b-0">
      <div className="min-w-0 text-sm">{label}</div>
      <div className="flex min-w-0 items-center gap-2">{children}</div>
    </div>
  );
}

// 分段选择（同 Claude 设置）：带图标的只显示图标（如主题），文字放进提示与读屏
function Segmented<T extends string>({ value, options, onChange }: { value: T; options: [T, string, ReactNode?][]; onChange: (v: T) => void }) {
  return (
    <div className="flex rounded-lg bg-muted p-0.5">
      {options.map(([key, label, icon]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          title={icon ? label : undefined}
          aria-label={icon ? label : undefined}
          aria-pressed={value === key}
          className={cn(
            // 选中的那一格带 shadow-sm（盖过 base 层的聚焦兜底），聚焦描边自己写
            // 文字格与图标格一样高 28（手机点按区 44）
            "cc-tap flex h-7 items-center justify-center rounded-md px-3 py-1 text-sm [&_svg]:size-4 focus-visible:shadow-focus",
            value === key ? "bg-surface shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {icon ?? label}
        </button>
      ))}
    </div>
  );
}

/** 开关（同 Claude 设置）：定时任务表单的「前一天提醒」也用这一个。 */
export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn("relative h-6 w-10 shrink-0 rounded-full transition-colors disabled:opacity-50", checked ? "bg-ink" : "bg-fill-muted")}
    >
      <span className={cn("absolute top-0.5 left-0.5 size-5 rounded-full bg-surface shadow-sm transition-transform", checked && "translate-x-4")} />
    </button>
  );
}

// ── 常规 ──

function General() {
  const [size, setSize] = useState<TextSize>(textSize());
  const [theme, setTheme] = useState<Theme>(pref("theme"));
  const [font, setFont] = useState<ChatFont>(pref("font"));
  const [width, setWidth] = useState<ChatWidth>(pref("width"));
  const [motion, setMotion] = useState<Motion>(pref("motion"));
  return (
    <>
      <Heading>{st.appearance}</Heading>
      <Row label={st.theme}>
        <Segmented
          value={theme}
          options={[["system", st.themeSystem, <MonitorIcon />], ["light", st.themeLight, <SunIcon />], ["dark", st.themeDark, <MoonIcon />]]}
          onChange={(value) => {
            setTheme(value);
            setPref("theme", value);
          }}
        />
      </Row>
      <Row label={st.chatFont}>
        <Segmented
          value={font}
          options={[["serif", st.fontSerif], ["sans", st.fontSans]]}
          onChange={(value) => {
            setFont(value);
            setPref("font", value);
          }}
        />
      </Row>
      <Row label={st.textSize}>
        <Segmented
          value={size}
          options={[["small", st.small], ["medium", st.medium], ["large", st.large]]}
          onChange={(value) => {
            setSize(value);
            setTextSize(value);
          }}
        />
      </Row>
      <Row label={st.chatWidth}>
        <Segmented
          value={width}
          options={[["narrow", st.narrow], ["medium", st.medium], ["wide", st.wide]]}
          onChange={(value) => {
            setWidth(value);
            setPref("width", value);
          }}
        />
      </Row>
      <Row label={st.motion}>
        <Segmented
          value={motion}
          options={[["system", st.motionSystem], ["reduce", st.motionReduce]]}
          onChange={(value) => {
            setMotion(value);
            setPref("motion", value);
          }}
        />
      </Row>
      <Row label={t("language")}>
        <Segmented value={lang} options={LANGUAGES.map((l) => [l.code, l.label] as [string, string])} onChange={(code) => void chooseLanguage(code)} />
      </Row>
      <Notifications />
    </>
  );
}

// 推送通知（lib/push.ts）：这一行一直在（CC 桌面端窗口除外：系统通知由桌面端自己弹）。能开就是开 / 关；开不了开关灰着，
// 下面一行说原因：iPhone / iPad 在浏览器页签里 → 先添加到主屏幕（点这句打开添加步骤）；拒绝过 → 只能去设置里改；
// 浏览器没有网页推送 → 此设备不支持。还没同步完（unknown）开关灰着、不给原因，打开设置时再同步一次。
export function Notifications() {
  const push = usePush();
  const showGuide = useInstallGuide();
  useEffect(() => void syncPush(), []);
  if (push.state === "desktop") return null;
  const usable = push.state === "on" || push.state === "off";
  const note = "text-muted-foreground text-xs";
  return (
    <Row
      label={
        <>
          {st.notifications}
          {usable && push.failure && <div className="text-danger text-xs">{push.failure}</div>}
          {push.state === "install" && (
            <button type="button" className={cn(note, "cc-tap flex items-center text-start underline underline-offset-2")} onClick={showGuide}>
              {st.notificationsAddToHome}
            </button>
          )}
          {push.state === "denied" && <div className={note}>{st.notificationsBlocked}</div>}
          {push.state === "unsupported" && <div className={note}>{st.notificationsUnsupported}</div>}
        </>
      }
    >
      {/* 打开要在点击里直接订阅（iPhone 的用户手势），不能先等别的 */}
      <Switch
        checked={push.state === "on"}
        disabled={!usable}
        label={st.notifications}
        onChange={(on) => void (on ? push.enable() : push.disable())}
      />
    </Row>
  );
}

// ── 此电脑：本机状态（开着就每 2 秒刷新一次，本机有变化也马上刷新） ──

function useDesktopState(api: Desktop) {
  const [state, setState] = useState<DesktopState | null>(null);
  const refresh = useCallback(() => void api.state().then((s) => s && "members" in s && setState(s)), [api]);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    const off = api.onChanged(refresh);
    return () => {
      clearInterval(timer);
      off();
    };
  }, [api, refresh]);
  return { state, refresh };
}

function useAction(api: Desktop, refresh: () => void) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const run = useCallback(
    async (name: string, method: string, args?: Record<string, unknown>) => {
      setBusy(name);
      setError("");
      const result = await api.call(method, args);
      setBusy("");
      if (!result.ok && !result.cancelled) setError(result.message || st.failed);
      refresh();
      return result;
    },
    [api, refresh],
  );
  return { busy, error, setError, run };
}

// 这台电脑只连一家公司（登录的账号，2026-10-10 起不用配对码）：公司一行、Express 数据文件夹（旧桌面版另有网页可读取的文件夹）。
// 公司下可能有 Express 和 MR.ERP 两个成员（旧配对留下的），合成一行；文件夹记在 Express 成员上，没有才记在 MR.ERP 上。

function useLink() {
  const [value, setValue] = useState<Link>(currentLink());
  useEffect(() => onLink(setValue), []);
  return value;
}

function Connections({ api }: { api: Desktop }) {
  const { state, refresh } = useDesktopState(api);
  const { busy, error, setError, run } = useAction(api, refresh);
  const linkState = useLink();
  const [scanned, setScanned] = useState(false);

  useEffect(() => {
    // 还没连也没找到 Express：后台找一次（读共享盘较慢）
    if (state && !scanned && !state.members.length && !state.express_roots.length) {
      setScanned(true);
      void run("scan", "detect");
    }
  }, [state, scanned, run]);

  if (!state) return null;
  const members = state.members;
  const main = members.find((m) => m.kind !== "folder") ?? members[0];
  const roots = state.express_roots;
  const express =
    busy === "scan" || busy === "roots"
      ? st.scanning
      : !roots.length
        ? st.expressMissing
        : roots.length > 1
          ? st.years(roots[0], roots.length)
          : roots[0];
  const kinds = [...new Set(members.map((m) => (m.kind === "folder" ? "MR.ERP" : "Express")))].join(" · ");
  const relink = !!main && (!!main.device_state || linkState.kind === "removed");
  // 文件夹随公司选（1.6.0 起）：设置里只剩以前授权、现在没人用的那几个可移除
  const companyFolders = !!state.features?.includes(COMPANY_FOLDERS);
  const legacy = state.legacy_roots ?? [];
  const connect = (options?: Parameters<typeof connectComputer>[1]) => {
    setError("");
    void connectComputer(api, options).then(refresh);
  };
  const pickRoots = async () => {
    const result = await run("roots", "pick_roots");
    if (result.ok && !members.length) connect(); // 找到 Express 就接着连上
  };
  // 需更新（旧版在已有新版电脑的池里登记，服务端回 update_needed）：桌面端在系统浏览器打开官网下载
  const download = () => void run("download", "download_update");

  return (
    <>
      <div data-marquee-host="" className="flex items-center gap-3 border-b py-4">
        <div className="min-w-0 flex-1">
          <Marquee as="div" className="truncate text-sm">{state.site || location.host}</Marquee>
          {kinds && <div className="text-muted-foreground text-xs">{kinds}</div>}
        </div>
        {/* 成员的密钥被服务端说不再用（device_state，多台电脑 ADR-021）：对话页已自动重新登记过一次；没成的在这里看到原因
            （被另一台电脑认领 = 现有「已在另一台电脑连接」）、点「重试」再登记。这台电脑被「电脑」列表移除过（登记回 removed）：
            写「电脑已移除」，点「连接」才作为新电脑回来（第 6 批审查第 2、8 条） */}
        {main && relink && (
          <LinkAction link={linkState} deviceState={main.device_state} onConnect={connect} onDownload={download} disabled={!!busy} />
        )}
        {main ? (
          <>
            {!relink && (
              <span className="flex items-center gap-1.5 text-sm" style={{ color: STATUS_COLOR[main.status] }}>
                <span className="size-2 rounded-full" style={{ background: STATUS_COLOR[main.status] }} />
                {st.status[main.status] ?? main.status}
              </span>
            )}
            <DropdownMenu.Root onOpenChange={(o) => o && setError("")}>
              <DropdownMenu.Trigger
                disabled={!!busy}
                aria-label={t("more")}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-muted data-[state=open]:bg-muted"
              >
                <MoreHorizontalIcon className="size-4" />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                {/* 同其他菜单（.cc-menu）；盖在设置弹窗（z 71）上面 */}
                <DropdownMenu.Content align="end" sideOffset={4} className="cc-menu z-[80] !min-w-44">
                  <DropdownMenu.Item
                    className="cc-menu-item"
                    onSelect={() => void Promise.all(members.map((m) => api.call("pause", { id: m.id, paused: !main.paused }))).then(refresh)}
                  >
                    {main.paused ? st.resume : st.pause}
                  </DropdownMenu.Item>
                  <DropdownMenu.Separator className="cc-menu-sep" />
                  <DropdownMenu.Item
                    className="cc-menu-item"
                    data-danger=""
                    onSelect={() => void Promise.all(members.map((m) => api.call("remove", { id: m.id }))).then(refresh)}
                  >
                    {st.disconnect}
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </>
        ) : (
          <LinkAction link={linkState} onConnect={connect} onDownload={download} disabled={!!busy} />
        )}
      </div>
      {main && linkState.kind === "other_site" && state.site !== location.host && (
        <div className="flex items-center justify-end border-b py-3">
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => connect({ switchSite: true })}>
            {st.switchTo(location.host)}
          </Button>
        </div>
      )}

      {(roots.length > 0 || !members.length || members.some((m) => m.kind !== "folder")) && (
        <Row label={st.expressFolder}>
          <Marquee className={cn("max-w-[22rem] truncate text-sm", roots.length ? "text-muted-foreground" : "text-danger")} title={express}>
            {express}
          </Marquee>
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void run("scan", "rescan")}>
            {st.findAgain}
          </Button>
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void pickRoots()}>
            {st.change}
          </Button>
        </Row>
      )}

      {companyFolders && legacy.length > 0 && (
        <>
          <div className="mt-8">
            <Heading>{st.earlierFolders}</Heading>
          </div>
          {legacy.map((root) => (
            <div data-marquee-host="" key={`${root.member}-${root.id}`} className="flex items-center gap-3 border-b py-3 text-sm last:border-b-0">
              <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
              <Marquee className="min-w-0 flex-1 truncate" title={root.path}>
                {root.path}
              </Marquee>
              <button
                type="button"
                aria-label={st.remove}
                title={st.remove}
                disabled={!!busy}
                onClick={() => void run("folder", "remove_folder", { id: root.member, path: root.path })}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
              >
                <XIcon className="size-4" />
              </button>
            </div>
          ))}
        </>
      )}

      {!companyFolders && main && main.folders !== null && (
        <>
          <div className="mt-8 flex items-center justify-between">
            <Heading>{st.folders}</Heading>
            <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void run("folder", "add_folder", { id: main.id })}>
              {st.addFolder}
            </Button>
          </div>
          {!main.folders.length && <p className="py-3 text-muted-foreground text-sm">{st.noFolders}</p>}
          {main.folders.map((path) => (
            <div data-marquee-host="" key={path} className="flex items-center gap-3 border-b py-3 text-sm last:border-b-0">
              <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
              <Marquee className="min-w-0 flex-1 truncate" title={path}>
                {path}
              </Marquee>
              <button
                type="button"
                aria-label={st.remove}
                title={st.remove}
                disabled={!!busy}
                onClick={() => void run("folder", "remove_folder", { id: main.id, path })}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
              >
                <XIcon className="size-4" />
              </button>
            </div>
          ))}
        </>
      )}
      {error && <p className="mt-3 text-danger text-sm">{error}</p>}

      <PoolComputers inDesktop />
    </>
  );
}

/** 还没连上时公司一行右边：正在连接 / 被另一台电脑用着（改用这台）/ 不是管理员 / 失败重试 / 电脑已移除 / 连接。
 *  deviceState：成员的密钥被服务端说不再用（claimed_elsewhere = 同一配对码被另一台电脑认领，写现有「已在另一台电脑连接」；
 *  superseded 写「未连接」），都给「重试」。 */
function LinkAction({ link, deviceState = "", onConnect, onDownload, disabled }: {
  link: Link; deviceState?: string; onConnect: (o?: { replace?: number; rejoin?: boolean }) => void; onDownload: () => void;
  disabled: boolean;
}) {
  const note = (text: string, tone = "text-muted-foreground") => <span className={cn("text-sm", tone)}>{text}</span>;
  if (link.kind === "connecting") return note(st.connecting);
  if (link.kind === "not_admin") return note(st.notAdmin);
  // 这台是旧版、池里已有新版认领过的电脑（服务端不让旧版顶掉新版）：更新 CC 桌面版
  if (link.kind === "update_needed")
    return (
      <>
        {note(st.needsUpdate, "text-danger")}
        <Button variant="outline" size="sm" disabled={disabled} onClick={onDownload}>
          {st.downloadNew}
        </Button>
      </>
    );
  // 这台电脑被「电脑」列表移除过：人点「连接」才作为新电脑回来（rejoin）
  if (link.kind === "removed")
    return (
      <>
        {note(co.folderGone, "text-danger")}
        <Button variant="outline" size="sm" disabled={disabled} onClick={() => onConnect({ rejoin: true })}>
          {st.connect}
        </Button>
      </>
    );
  if (link.kind === "in_use")
    return (
      <>
        {note(st.inUse)}
        <Button variant="outline" size="sm" disabled={disabled} onClick={() => onConnect({ replace: link.generation })}>
          {st.useHere}
        </Button>
      </>
    );
  return (
    <>
      {link.kind === "failed"
        ? note(link.message || st.linkFailed, "text-danger")
        : note(deviceState === "claimed_elsewhere" ? st.inUse : st.notConnected)}
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => onConnect()}>
        {link.kind === "failed" || deviceState ? st.retry : st.connect}
      </Button>
    </>
  );
}

// ── 浏览器里：只读的连接（连着哪台电脑、在不在线；pearnly_cowork.folder_intake.api.computers）。文件夹在公司主档页看 ──

// update_blocked：这台电脑的 CC 桌面版自动更新被 Windows 拦下（被拦的版本 + 时刻，UTC；服务端 connections/desktop_update.py）
// 第 6 批（ADR-021）：id = 电脑编号（「···」移除用）、can_remove = 本人是登记人或管理员（10-16 起没有「常驻」，第 56 条）
export type Computer = {
  adapter: string;
  device: string;
  online: boolean;
  version: string;
  folders: string[];
  update_blocked?: { version: string; at: string } | null;
  id?: string;
  connection?: string;
  can_remove?: boolean;
};

/** 「Windows 拦截了 v1.5.2 的自动更新」；带时刻时接上「· 昨天 18:18」（按这台设备的时区，同对话里的时间）。 */
export function blockedLine(version: string, at?: string): string {
  const text = st.update.blocked.replace("{ver}", version);
  const when = at ? new Date(at) : null;
  if (!when || Number.isNaN(when.getTime())) return text;
  return `${text} · ${dayLabel(when)} ${new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(when)}`;
}

async function getJson<T>(method: string): Promise<T | null> {
  const res = await siteFetch(`/api/method/${method}`, { cache: "no-store" }).catch(() => null);
  if (!res?.ok) return null;
  return ((await res.json()) as { message: T }).message;
}

function WebConnections() {
  return <PoolComputers />;
}

// 「全所可用」名单能勾的人：与服务端 `set_members` 核新加的人同一口径（启用、有录入 / 推送记录 / 管理角色），
// 只给管理员（`connections.api.member_choices`；第 6 批审查第 6 条：不再借团队成员接口）
const MEMBER_CHOICES = "pearnly_cowork.connections.api.member_choices";

const COMPUTERS = "pearnly_cowork.folder_intake.api.computers";
const REMOVE_COMPUTER = "pearnly_cowork.companion.api.remove_device";

/** 池里的电脑（一台一行，服务端 `folder_intake.rules.computers`）：在不在线、自动更新被拦；登记人或管理员「···」移除
 *  （`companion.api.remove_device`：吊销它的密钥、让它退役；它读的定时任务留着、标「电脑已移除」）。下面是管理员的「全所可用」。
 *  inDesktop：CC 桌面版「此电脑 → 连接」下面那一节（带「电脑」标题）；一台电脑都没有时整节不出现，不在桌面版里请人下载桌面版
 *  （第 6 批审查第 7 条）。浏览器的「连接」照旧写「还没有电脑连接」+「下载 CC 桌面版」。 */
export function PoolComputers({ inDesktop = false }: { inDesktop?: boolean }) {
  const [rows, setRows] = useState<Computer[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => void getJson<Computer[]>(COMPUTERS).then((r) => setRows(r ?? [])), []);
  useEffect(() => {
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [load]);
  const remove = async (row: Computer) => {
    if (!row.id) return;
    setBusy(row.id);
    setError("");
    try {
      await postMethod(REMOVE_COMPUTER, { device: row.id });
      setRows((before) => (before ?? []).filter((item) => item.id !== row.id));
      load();
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy("");
    }
  };
  if (!rows) return null;
  if (inDesktop && !rows.length) return <AllMembers heading />;
  return (
    <>
      {inDesktop && (
        <div className="mt-8">
          <Heading>{st.computers}</Heading>
        </div>
      )}
      {!rows.length ? (
        <Row label={<span className="text-muted-foreground">{st.noComputer}</span>}>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              void getJson<{ url?: string }>("pearnly_cowork.companion.api.installer").then(
                (info) => info?.url && window.open(info.url, "_blank", "noopener,noreferrer"),
              )
            }
          >
            {st.download}
          </Button>
        </Row>
      ) : (
        rows.map((row, i) => (
          <ComputerSection
            key={`${row.adapter}-${row.id ?? row.device}-${i}`}
            row={row}
            busy={!!row.id && busy === row.id}
            onRemove={row.can_remove && row.id ? () => void remove(row) : undefined}
          />
        ))
      )}
      {error && <p role="alert" className="mt-3 text-danger text-sm">{error}</p>}
      <AllMembers />
    </>
  );
}

/** 一台电脑：名字、ERP、在不在线；自动更新被 Windows 拦下时多一句（版本 + 时刻）。文件夹按公司在主档页看，这里不列。
 *  能移除时行尾「···」→「移除」（同侧栏行菜单，不另加确认）。 */
export function ComputerSection({ row, onRemove, busy = false }: { row: Computer; onRemove?: () => void; busy?: boolean }) {
  const status = row.online ? "online" : "offline";
  return (
    <section className="mb-8 last:mb-0">
      <div data-marquee-host="" className="flex items-center gap-3 border-b py-4">
        <div className="min-w-0 flex-1">
          <Marquee as="div" className="truncate text-sm">{row.device}</Marquee>
          <div className="text-muted-foreground text-xs">
            {row.adapter === "mrerp" ? "MR.ERP" : "Express"}
          </div>
          {row.update_blocked && (
            <div className="text-muted-foreground text-xs">{blockedLine(row.update_blocked.version, row.update_blocked.at)}</div>
          )}
        </div>
        <span className="flex items-center gap-1.5 text-sm" style={{ color: STATUS_COLOR[status] }}>
          <span className="size-2 rounded-full" style={{ background: STATUS_COLOR[status] }} />
          {st.status[status]}
        </span>
        {onRemove && (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger
              disabled={busy}
              aria-label={t("more")}
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-muted data-[state=open]:bg-muted"
            >
              <MoreHorizontalIcon className="size-4" />
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              {/* 同其他菜单（.cc-menu）；盖在设置弹窗（z 71）上面 */}
              <DropdownMenu.Content align="end" sideOffset={4} className="cc-menu z-[80] !min-w-44">
                <DropdownMenu.Item className="cc-menu-item" data-danger="" onSelect={onRemove}>
                  {st.remove}
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        )}
      </div>
    </section>
  );
}

type PoolConnection = { name: string; adapter: string; main?: boolean; manageable?: boolean; all_members?: boolean; users?: string[] };
type TeamMember = { email: string; display?: string; enabled?: boolean };

/** 「全所可用」（D2，ADR-021 第 31 条）：Express 主连接、只给管理员。打开 = 本所每个有录入权限的人都能用这个池（在自己的电脑上
 *  登录 CC 就加入）；关掉以后只有名单上的人能用，名单在下面逐人开关（`connections.api.set_members`，只改这两项、不升版本）。
 *  名单不能空：只剩一个人时那一个的开关不能关。MR.ERP 没有全所可用，不显示。 */
function AllMembers({ heading = false }: { heading?: boolean }) {
  const [pool, setPool] = useState<PoolConnection | null>(null);
  const [team, setTeam] = useState<TeamMember[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void getJson<PoolConnection[]>("pearnly_cowork.connections.api.records?limit=100").then((rows) =>
      setPool((rows ?? []).find((row) => row.adapter === "express" && row.main && row.manageable) ?? null),
    );
  }, []);
  useEffect(() => {
    if (!pool || pool.all_members) return;
    void getJson<TeamMember[]>(`${MEMBER_CHOICES}?name=${encodeURIComponent(pool.name)}`).then((found) => setTeam(found ?? []));
  }, [pool]);
  if (!pool) return null;
  const users = pool.users ?? [];
  const save = async (values: { all_members?: boolean; users?: string[] }) => {
    setBusy(true);
    setError("");
    try {
      const next = await postMethod<PoolConnection>("pearnly_cowork.connections.api.set_members", {
        name: pool.name,
        ...(values.all_members === undefined ? {} : { all_members: values.all_members ? 1 : 0 }),
        ...(values.users ? { users: JSON.stringify(values.users) } : {}),
      });
      setPool((before) => (before ? { ...before, all_members: next.all_members, users: next.users } : before));
    } catch (e) {
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  };
  const listed = [...new Set([...team.map((member) => member.email), ...users])];
  const name = (email: string) => team.find((member) => member.email === email)?.display || email;
  return (
    <div className="mt-8">
      {heading && <Heading>{st.computers}</Heading>}
      <Row label={st.allMembers}>
        <Switch checked={!!pool.all_members} disabled={busy} label={st.allMembers} onChange={(on) => void save({ all_members: on })} />
      </Row>
      {!pool.all_members && (
        <>
          <Heading>{st.members}</Heading>
          {listed.map((email) => {
            const on = users.includes(email);
            return (
              <Row key={email} label={<Marquee className="block truncate">{name(email)}</Marquee>}>
                <Switch
                  checked={on}
                  disabled={busy || (on && users.length === 1)}
                  label={name(email)}
                  onChange={(next) => void save({ users: next ? [...users, email] : users.filter((user) => user !== email) })}
                />
              </Row>
            );
          })}
        </>
      )}
      {error && <p role="alert" className="mt-3 text-danger text-sm">{error}</p>}
    </div>
  );
}

// ── 此电脑：系统 ──

function System({ api }: { api: Desktop }) {
  const { state, refresh } = useDesktopState(api);
  const { error, run } = useAction(api, refresh);
  if (!state) return null;
  return (
    <>
      <Row label={st.autostart}>
        <Switch checked={state.autostart} label={st.autostart} onChange={(on) => void run("autostart", "autostart", { enabled: on })} />
      </Row>
      {/* 方法名写成字面量：契约检查（tests/test_companion_contract.py）按 run("…", "方法") 核对桥里有没有 */}
      <KeepAwakeRows
        state={state}
        onChange={(method, on) =>
          void (method === "keep_awake"
            ? run("keep_awake", "keep_awake", { enabled: on })
            : run("keep_awake_on_battery", "keep_awake_on_battery", { enabled: on }))
        }
      />
      <Row label={st.quickAsk}>
        {state.hotkey ? (
          <Shortcut keys="Ctrl + Alt + Space" kbd />
        ) : (
          <span className="text-muted-foreground text-sm">{st.hotkeyBusy}</span>
        )}
      </Row>
      <VersionRow
        version={state.version}
        update={state.update}
        onUpdate={() => void run("update", "update")}
        onDownload={() => void run("download", "download_update")}
      />
      <Row label={st.logs}>
        <Button variant="outline" size="icon-sm" aria-label={st.logs} onClick={() => void run("logs", "logs")}>
          <FolderIcon className="size-4" />
        </Button>
      </Row>
      {error && <p className="mt-3 text-danger text-sm">{error}</p>}
    </>
  );
}

/** 「CC 开着时保持电脑唤醒」「用电池时也保持」（同 Claude 桌面版，用户 10-16；companion keep_awake.py，默认都开）。只在「此电脑 → 系统」
 *  （有桥才有），旧版桌面端的状态里没有这两项就不显示。 */
export function KeepAwakeRows({
  state,
  onChange,
}: {
  state: Pick<DesktopState, "keep_awake" | "keep_awake_on_battery">;
  onChange: (method: "keep_awake" | "keep_awake_on_battery", on: boolean) => void;
}) {
  if (state.keep_awake === undefined) return null;
  return (
    <>
      <Row label={st.keepAwake}>
        <Switch checked={state.keep_awake} label={st.keepAwake} onChange={(on) => onChange("keep_awake", on)} />
      </Row>
      <Row label={st.keepAwakeOnBattery}>
        <Switch
          checked={state.keep_awake_on_battery ?? true}
          label={st.keepAwakeOnBattery}
          onChange={(on) => onChange("keep_awake_on_battery", on)}
        />
      </Row>
    </>
  );
}

/**
 * 「版本」一行：本机版本 + 更新的真实状态（companion auto_update.state）。自动更新被 Windows 拦下（blocked）时一句话 +
 * 「下载新版」（桌面端在系统浏览器打开官网下载，不再重试安装）；其余照旧「检查更新 / 立即更新」。
 */
export function VersionRow({
  version,
  update,
  onUpdate,
  onDownload,
}: {
  version: string;
  update: { state: string; version: string };
  onUpdate: () => void;
  onDownload: () => void;
}) {
  const { state: updateState, version: next } = update;
  const blocked = updateState === "blocked";
  const waiting = ["found", "ready", "failed"].includes(updateState);
  const working = ["checking", "downloading", "installing"].includes(updateState);
  const updateText = blocked
    ? blockedLine(next)
    : updateState in st.update
      ? st.update[updateState as keyof typeof st.update].replace("{ver}", next)
      : "";
  return (
    <Row
      label={
        <span>
          {st.version} <span className="text-muted-foreground">v{version}</span>
        </span>
      }
    >
      {updateText && (
        <span className="flex items-center gap-1 text-muted-foreground text-sm">
          {updateState === "latest" && <CheckIcon className="size-3.5" />}
          {updateText}
        </span>
      )}
      {blocked ? (
        <Button variant="outline" size="sm" onClick={onDownload}>
          {st.downloadNew}
        </Button>
      ) : (
        !working && (
          <Button variant="outline" size="sm" onClick={onUpdate}>
            {waiting ? st.updateNow : st.checkUpdate}
          </Button>
        )
      )}
    </Row>
  );
}
