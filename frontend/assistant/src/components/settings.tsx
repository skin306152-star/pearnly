// 设置（同 Claude：左下角菜单 →「设置」，或 Ctrl+,）。左边分栏，右边内容；选中 / 悬停用全站同一套中性灰（--muted），不用暖色：
//
//   常规      — 主题、对话字体、对话文字大小、对话记录宽度、动效、语言（同 Claude 设置的「外观」）
//   连接      — 只在浏览器里（Cowork 站点）出现：只读，连着哪台电脑、在不在线、网页可读取的文件夹；改只在电脑上的 CC 里
//   此电脑    — 只在 CC 桌面端出现（lib/desktop.ts 的 desktop()）：
//     连接    — 这台电脑连的公司（登录的账号自动连上，不用配对码）、Express 数据文件夹、网页可读取的文件夹
//     系统    — 开机自动启动、快捷提问、版本与更新、日志
//
// 桌面端托盘「连接与设置」直接打开「连接」一栏（window.tomo.settings）。

import { CheckIcon, FolderIcon, MonitorIcon, MoonIcon, MoreHorizontalIcon, PlugIcon, Settings2Icon, SunIcon, XIcon } from "lucide-react";
import { Dialog as DialogPrimitive, DropdownMenu } from "radix-ui";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
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
import { type ChatFont, type ChatWidth, type Motion, pref, setPref, type Theme } from "@/lib/appearance";
import { useProfile } from "@/lib/profile";
import { cn } from "@/lib/utils";
import { lang, LANGUAGES, st, t } from "../i18n";

type Section = "general" | "connections" | "system";

// 连接状态色：取 globals.css 的 --cc-status-*（深浅色各一套）
const STATUS_COLOR: Record<string, string> = Object.fromEntries(
  ["online", "processing", "attention", "offline", "connecting", "paused"].map((k) => [k, `var(--cc-status-${k})`]),
);

async function saveLanguage(code: string) {
  await fetch("/api/method/pearnly_core.account.preferences.set_language", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify({ language: code }),
  });
  location.reload();
}

export function Settings() {
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<Section>("general");
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
      setOpen(true);
    };
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === ",") {
        event.preventDefault();
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
          aria-describedby={undefined}
          className="fixed top-1/2 left-1/2 z-[71] flex h-[min(680px,92dvh)] w-[min(980px,95vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border bg-surface text-ink shadow-2xl outline-none md:flex-row"
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
          <DialogPrimitive.Close
            aria-label={t("close")}
            className="absolute top-3.5 right-3.5 flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
          >
            <XIcon className="size-4" />
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
        "flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-start text-sm [&_svg]:size-4",
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
            "flex items-center rounded-md px-3 py-1 text-sm [&_svg]:size-4",
            value === key ? "bg-surface shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {icon ?? label}
        </button>
      ))}
    </div>
  );
}

function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn("relative h-6 w-10 rounded-full transition-colors", checked ? "bg-ink" : "bg-line-strong")}
    >
      <span className={cn("absolute top-0.5 left-0.5 size-5 rounded-full bg-surface shadow transition-transform", checked && "translate-x-4")} />
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
        <Segmented value={lang} options={LANGUAGES.map((l) => [l.code, l.label] as [string, string])} onChange={(code) => void saveLanguage(code)} />
      </Row>
    </>
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

// 这台电脑只连一家公司（登录的账号，2026-10-10 起不用配对码）：公司一行、Express 数据文件夹、网页可读取的文件夹。
// 公司下可能有 Express 和 MR.ERP 两个成员（旧配对留下的），合成一行；文件夹记在 Express 成员上，没有才记在 MR.ERP 上。

const menuItem =
  "flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none hover:bg-muted focus:bg-muted";

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
  const connect = (options?: Parameters<typeof connectComputer>[1]) => {
    setError("");
    void connectComputer(api, options).then(refresh);
  };
  const pickRoots = async () => {
    const result = await run("roots", "pick_roots");
    if (result.ok && !members.length) connect(); // 找到 Express 就接着连上
  };

  return (
    <>
      <div className="flex items-center gap-3 border-b py-4">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm">{state.site || location.host}</div>
          {kinds && <div className="text-muted-foreground text-xs">{kinds}</div>}
        </div>
        {main ? (
          <>
            <span className="flex items-center gap-1.5 text-sm" style={{ color: STATUS_COLOR[main.status] }}>
              <span className="size-2 rounded-full" style={{ background: STATUS_COLOR[main.status] }} />
              {st.status[main.status] ?? main.status}
            </span>
            <DropdownMenu.Root onOpenChange={(o) => o && setError("")}>
              <DropdownMenu.Trigger
                disabled={!!busy}
                aria-label={t("more")}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-muted data-[state=open]:bg-muted"
              >
                <MoreHorizontalIcon className="size-4" />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content align="end" sideOffset={4} className="z-[80] min-w-44 rounded-xl border bg-surface p-1.5 shadow-lg">
                  <DropdownMenu.Item
                    className={menuItem}
                    onSelect={() => void Promise.all(members.map((m) => api.call("pause", { id: m.id, paused: !main.paused }))).then(refresh)}
                  >
                    {main.paused ? st.resume : st.pause}
                  </DropdownMenu.Item>
                  <DropdownMenu.Separator className="my-1 h-px bg-border" />
                  <DropdownMenu.Item
                    className={cn(menuItem, "text-danger")}
                    onSelect={() => void Promise.all(members.map((m) => api.call("remove", { id: m.id }))).then(refresh)}
                  >
                    {st.disconnect}
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </>
        ) : (
          <LinkAction link={linkState} onConnect={connect} disabled={!!busy} />
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
          <span className={cn("max-w-[22rem] truncate text-sm", roots.length ? "text-muted-foreground" : "text-danger")} title={express}>
            {express}
          </span>
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void run("scan", "rescan")}>
            {st.findAgain}
          </Button>
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void pickRoots()}>
            {st.change}
          </Button>
        </Row>
      )}

      {main && main.folders !== null && (
        <>
          <div className="mt-8 flex items-center justify-between">
            <Heading>{st.folders}</Heading>
            <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void run("folder", "add_folder", { id: main.id })}>
              {st.addFolder}
            </Button>
          </div>
          {!main.folders.length && <p className="py-3 text-muted-foreground text-sm">{st.noFolders}</p>}
          {main.folders.map((path) => (
            <div key={path} className="flex items-center gap-3 border-b py-3 text-sm last:border-b-0">
              <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate" title={path}>
                {path}
              </span>
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
    </>
  );
}

/** 还没连上时公司一行右边：正在连接 / 被另一台电脑用着（改用这台）/ 不是管理员 / 失败重试 / 连接。 */
function LinkAction({ link, onConnect, disabled }: { link: Link; onConnect: (o?: { replace?: number }) => void; disabled: boolean }) {
  const note = (text: string, tone = "text-muted-foreground") => <span className={cn("text-sm", tone)}>{text}</span>;
  if (link.kind === "connecting") return note(st.connecting);
  if (link.kind === "not_admin") return note(st.notAdmin);
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
      {link.kind === "failed" ? note(link.message || st.linkFailed, "text-danger") : note(st.notConnected)}
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => onConnect()}>
        {link.kind === "failed" ? st.retry : st.connect}
      </Button>
    </>
  );
}

// ── 浏览器里：只读的连接（连着哪台电脑、在不在线、网页可读取的文件夹；pearnly_cowork.folder_intake.api.computers） ──

type Computer = { adapter: string; device: string; online: boolean; version: string; folders: string[] };

async function getJson<T>(method: string): Promise<T | null> {
  const res = await fetch(`/api/method/${method}`, { credentials: "same-origin", cache: "no-store" }).catch(() => null);
  if (!res?.ok) return null;
  return ((await res.json()) as { message: T }).message;
}

function WebConnections() {
  const [rows, setRows] = useState<Computer[] | null>(null);
  useEffect(() => {
    const load = () => void getJson<Computer[]>("pearnly_cowork.folder_intake.api.computers").then((r) => setRows(r ?? []));
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, []);
  if (!rows) return null;
  if (!rows.length)
    return (
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
    );
  return (
    <>
      {rows.map((row, i) => {
        const status = row.online ? "online" : "offline";
        return (
          <section key={`${row.adapter}-${row.device}-${i}`} className="mb-8 last:mb-0">
            <div className="flex items-center gap-3 border-b py-4">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{row.device}</div>
                <div className="text-muted-foreground text-xs">{row.adapter === "mrerp" ? "MR.ERP" : "Express"}</div>
              </div>
              <span className="flex items-center gap-1.5 text-sm" style={{ color: STATUS_COLOR[status] }}>
                <span className="size-2 rounded-full" style={{ background: STATUS_COLOR[status] }} />
                {st.status[status]}
              </span>
            </div>
            <Heading>{st.folders}</Heading>
            {!row.folders.length && <p className="py-3 text-muted-foreground text-sm">{st.noFolders}</p>}
            {row.folders.map((path) => (
              <div key={path} className="flex items-center gap-3 border-b py-3 text-sm last:border-b-0">
                <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate" title={path}>
                  {path}
                </span>
              </div>
            ))}
          </section>
        );
      })}
    </>
  );
}

// ── 此电脑：系统 ──

function System({ api }: { api: Desktop }) {
  const { state, refresh } = useDesktopState(api);
  const { error, run } = useAction(api, refresh);
  if (!state) return null;
  const { state: updateState, version } = state.update;
  const waiting = ["found", "ready", "failed"].includes(updateState);
  const working = ["checking", "downloading", "installing"].includes(updateState);
  const updateText =
    updateState in st.update ? st.update[updateState as keyof typeof st.update].replace("{ver}", version) : "";
  return (
    <>
      <Row label={st.autostart}>
        <Switch checked={state.autostart} label={st.autostart} onChange={(on) => void run("autostart", "autostart", { enabled: on })} />
      </Row>
      {/* 常驻模式：这台电脑当「CC 的电脑」——不睡眠、开机锁屏、不弹询问框（companion always_on.py）；打开时一并开机自启 */}
      {state.always_on !== undefined && (
        <Row label={st.alwaysOn}>
          <Switch checked={state.always_on} label={st.alwaysOn} onChange={(on) => void run("always_on", "always_on", { enabled: on })} />
        </Row>
      )}
      <Row label={st.quickAsk}>
        {state.hotkey ? (
          <kbd className="rounded-md border bg-app px-2 py-0.5 font-sans text-muted-foreground text-xs">Ctrl + Alt + Space</kbd>
        ) : (
          <span className="text-muted-foreground text-sm">{st.hotkeyBusy}</span>
        )}
      </Row>
      <Row
        label={
          <span>
            {st.version} <span className="text-muted-foreground">v{state.version}</span>
          </span>
        }
      >
        {updateText && (
          <span className="flex items-center gap-1 text-muted-foreground text-sm">
            {updateState === "latest" && <CheckIcon className="size-3.5" />}
            {updateText}
          </span>
        )}
        {!working && (
          <Button variant="outline" size="sm" onClick={() => void run("update", "update")}>
            {waiting ? st.updateNow : st.checkUpdate}
          </Button>
        )}
      </Row>
      <Row label={st.logs}>
        <Button variant="outline" size="sm" onClick={() => void run("logs", "logs")}>
          <FolderIcon className="size-4" />
        </Button>
      </Row>
      {error && <p className="mt-3 text-danger text-sm">{error}</p>}
    </>
  );
}
