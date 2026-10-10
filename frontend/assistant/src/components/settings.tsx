// 设置（同 Claude：左下角菜单 →「设置」，或 Ctrl+,）。左边分栏，右边内容；选中 / 悬停用全站同一套中性灰（--muted），不用暖色：
//
//   常规      — 对话文字大小、语言
//   此电脑    — 只在 Tomo 桌面端出现（lib/desktop.ts 的 desktop()）：
//     连接    — Express 账套、成员（配对码、暂停、移除）、每个成员网页可选的文件夹（原小助手主窗口）
//     系统    — 开机自动启动、快捷提问、版本与更新、日志
//
// 桌面端托盘「连接与设置」直接打开「连接」一栏（window.tomo.settings）。

import { CheckIcon, FolderIcon, MonitorIcon, MoreHorizontalIcon, PlugIcon, Settings2Icon, XIcon } from "lucide-react";
import { Dialog as DialogPrimitive, DropdownMenu } from "radix-ui";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  type Desktop,
  type DesktopMember,
  type DesktopState,
  desktop,
  setTextSize,
  type TextSize,
  textSize,
} from "@/lib/desktop";
import { cn } from "@/lib/utils";
import { lang, LANGUAGES, st, t } from "../i18n";

type Section = "general" | "connections" | "system";

const STATUS_COLOR: Record<string, string> = {
  online: "#27ae60",
  processing: "#b7950b",
  attention: "#d35400",
  offline: "#c0392b",
  connecting: "#9a9893",
  paused: "#9a9893",
};

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

  const current = !api && section !== "general" ? "general" : section;
  const titles: Record<Section, string> = { general: st.general, connections: st.connections, system: st.system };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[70] bg-black/35" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed top-1/2 left-1/2 z-[71] flex h-[min(680px,92dvh)] w-[min(980px,95vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border bg-white text-[#1a1a18] shadow-2xl outline-none md:flex-row"
        >
          <nav className="flex shrink-0 gap-1 overflow-x-auto border-b bg-[#fafafa] p-3 md:w-56 md:flex-col md:border-r md:border-b-0">
            <DialogPrimitive.Title className="hidden px-2.5 pt-1 pb-2 text-muted-foreground text-xs md:block">
              {st.settings}
            </DialogPrimitive.Title>
            <NavItem active={current === "general"} onClick={() => setSection("general")} icon={<Settings2Icon />}>
              {st.general}
            </NavItem>
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
        active ? "bg-muted font-medium" : "hover:bg-[#f5f5f5]",
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="mt-8 mb-1 font-medium text-[15px] first:mt-0">{children}</h3>;
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b py-4 last:border-b-0">
      <div className="min-w-0 text-sm">{label}</div>
      <div className="flex min-w-0 items-center gap-2">{children}</div>
    </div>
  );
}

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="flex rounded-lg bg-muted p-0.5">
      {options.map(([key, label]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          className={cn("rounded-md px-3 py-1 text-sm", value === key ? "bg-white shadow-sm" : "text-muted-foreground hover:text-foreground")}
        >
          {label}
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
      className={cn("relative h-6 w-10 rounded-full transition-colors", checked ? "bg-[#1a1a18]" : "bg-[#d4d4d4]")}
    >
      <span className={cn("absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform", checked && "translate-x-4")} />
    </button>
  );
}

// ── 常规 ──

function General() {
  const [size, setSize] = useState<TextSize>(textSize());
  return (
    <>
      <Heading>{st.appearance}</Heading>
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

function Connections({ api }: { api: Desktop }) {
  const { state, refresh } = useDesktopState(api);
  const { busy, error, setError, run } = useAction(api, refresh);
  const [pairing, setPairing] = useState<{ id: string | null } | null>(null);
  const [scanned, setScanned] = useState(false);

  useEffect(() => {
    // 还没有成员也没找到 Express：后台找一次（读共享盘较慢）
    if (state && !scanned && !state.members.length && !state.express_roots.length) {
      setScanned(true);
      void run("scan", "detect");
    }
  }, [state, scanned, run]);

  if (!state) return null;
  const roots = state.express_roots;
  const express =
    busy === "scan" || busy === "roots"
      ? st.scanning
      : !roots.length
        ? st.expressMissing
        : roots.length > 1
          ? st.years(roots[0], roots.length)
          : roots[0];

  return (
    <>
      <Row label={st.express}>
        <span className={cn("max-w-[22rem] truncate text-sm", roots.length ? "text-muted-foreground" : "text-[#c0392b]")} title={express}>
          {express}
        </span>
        <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void run("roots", "pick_roots")}>
          {st.change}
        </Button>
      </Row>

      <div className="mt-6 flex items-center justify-between">
        <Heading>{st.members}</Heading>
        <Button variant="outline" size="sm" disabled={!!busy} onClick={() => setPairing({ id: null })}>
          {st.addMember}
        </Button>
      </div>
      {pairing && (
        <PairForm
          api={api}
          editing={pairing.id ? state.members.find((m) => m.id === pairing.id)?.name : undefined}
          memberId={pairing.id}
          onClose={() => setPairing(null)}
          refresh={refresh}
        />
      )}
      {error && <p className="mt-3 text-[#c0392b] text-sm">{error}</p>}
      {!state.members.length && !pairing && <p className="mt-3 text-muted-foreground text-sm">{st.noMembers}</p>}
      <div className="mt-3 flex flex-col gap-3">
        {state.members.map((member) => (
          <MemberCard
            key={member.id}
            member={member}
            disabled={!!busy}
            onChangeCode={() => setPairing({ id: member.id })}
            onPause={() => void run("pause", "pause", { id: member.id, paused: !member.paused })}
            onRemove={() => void run("remove", "remove", { id: member.id })}
            onAddFolder={() => void run("folder", "add_folder", { id: member.id })}
            onRemoveFolder={(path) => void run("folder", "remove_folder", { id: member.id, path })}
            onClearError={() => setError("")}
          />
        ))}
      </div>
    </>
  );
}

function PairForm({
  api,
  memberId,
  editing,
  onClose,
  refresh,
}: {
  api: Desktop;
  memberId: string | null;
  editing?: string;
  onClose: () => void;
  refresh: () => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const submit = async () => {
    if (!code.trim() || busy) return;
    setBusy(true);
    setMessage(st.pairing);
    const result = await api.call("pair", { code, id: memberId ?? "" });
    setBusy(false);
    refresh();
    if (result.ok) onClose();
    else setMessage(result.message || st.failed);
  };
  return (
    <div className="mt-3 rounded-xl border p-4">
      <div className="mb-2 text-sm">{editing ? `${st.changeCode.replace("…", "")} · ${editing}` : st.code}</div>
      <div className="flex flex-wrap gap-2">
        <input
          autoFocus
          value={code}
          disabled={busy}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
          placeholder={st.codeHint}
          spellCheck={false}
          className="h-9 min-w-0 flex-1 rounded-lg border bg-white px-3 font-mono text-sm outline-none focus:border-[#a8a59c]"
        />
        <Button variant="ghost" size="sm" className="h-9" disabled={busy} onClick={onClose}>
          {st.cancel}
        </Button>
        <Button size="sm" className="h-9" disabled={busy || !code.trim()} onClick={() => void submit()}>
          {editing ? st.save : st.pair}
        </Button>
      </div>
      {message && <p className={cn("mt-2 text-sm", busy ? "text-muted-foreground" : "text-[#c0392b]")}>{message}</p>}
    </div>
  );
}

const menuItem =
  "flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none hover:bg-muted focus:bg-muted";

function MemberCard({
  member,
  disabled,
  onChangeCode,
  onPause,
  onRemove,
  onAddFolder,
  onRemoveFolder,
  onClearError,
}: {
  member: DesktopMember;
  disabled: boolean;
  onChangeCode: () => void;
  onPause: () => void;
  onRemove: () => void;
  onAddFolder: () => void;
  onRemoveFolder: (path: string) => void;
  onClearError: () => void;
}) {
  return (
    <div className="rounded-xl border p-4">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1 truncate font-medium text-sm">{member.name}</div>
        <span className="flex items-center gap-1.5 text-sm" style={{ color: STATUS_COLOR[member.status] }}>
          <span className="size-2 rounded-full" style={{ background: STATUS_COLOR[member.status] }} />
          {st.status[member.status] ?? member.status}
        </span>
        <DropdownMenu.Root onOpenChange={(o) => o && onClearError()}>
          <DropdownMenu.Trigger
            disabled={disabled}
            aria-label={t("more")}
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-muted data-[state=open]:bg-muted"
          >
            <MoreHorizontalIcon className="size-4" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content align="end" sideOffset={4} className="z-[80] min-w-44 rounded-xl border bg-white p-1.5 shadow-lg">
              <DropdownMenu.Item className={menuItem} onSelect={onChangeCode}>
                {st.changeCode}
              </DropdownMenu.Item>
              <DropdownMenu.Item className={menuItem} onSelect={onPause}>
                {member.paused ? st.resume : st.pause}
              </DropdownMenu.Item>
              <DropdownMenu.Separator className="my-1 h-px bg-border" />
              <DropdownMenu.Item className={cn(menuItem, "text-[#c0392b]")} onSelect={onRemove}>
                {st.remove}
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
      {member.folders && (
        <div className="mt-3 border-t pt-3">
          <div className="mb-1.5 text-muted-foreground text-xs">{st.folders}</div>
          {member.folders.map((path) => (
            <div key={path} className="group flex items-center gap-2 rounded-md py-1 text-sm">
              <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate" title={path}>
                {path}
              </span>
              <button
                type="button"
                aria-label={st.remove}
                title={st.remove}
                disabled={disabled}
                onClick={() => onRemoveFolder(path)}
                className="flex size-6 items-center justify-center rounded text-muted-foreground opacity-60 hover:bg-muted hover:opacity-100"
              >
                <XIcon className="size-3.5" />
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={disabled}
            onClick={onAddFolder}
            className="mt-1 rounded-md px-1 py-0.5 text-muted-foreground text-sm hover:text-foreground"
          >
            {st.addFolder}
          </button>
        </div>
      )}
    </div>
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
      <Row label={st.quickAsk}>
        {state.hotkey ? (
          <kbd className="rounded-md border bg-[#fafafa] px-2 py-0.5 font-sans text-muted-foreground text-xs">Ctrl + Alt + Space</kbd>
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
      {error && <p className="mt-3 text-[#c0392b] text-sm">{error}</p>}
    </>
  );
}
