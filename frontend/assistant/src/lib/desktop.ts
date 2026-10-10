// 桌面端（companion/ 里的 Tomo 窗口）和网页一起用的三件事，浏览器里打开时也照常生效：
//
//   window.tomo.ask(text) — 桌面端快捷键呼出的输入条把文字交给这里：开一个新对话发出去（空文字只开新对话）
//   回复完成通知          — 窗口不在前台时回复完成，用网页原生通知提醒（桌面端已授权；浏览器只在用户授权过时弹）
//   整窗拖文件            — 文件拖到对话页任何地方都加进输入框，不会被浏览器当成网页打开
//   window.tomo.settings  — 桌面端托盘「连接与设置」/ Ctrl+, 打开设置

import { useAui } from "@assistant-ui/react";
import { useEffect, useRef } from "react";

type Message = { role?: string; content?: unknown };

declare global {
  interface Window {
    tomo?: { ask: (text: string) => void; settings: (section?: string) => void };
  }
}

/** 打开设置（section：general / connections / system）；设置窗口在 components/settings.tsx 里听这个事件。 */
export function openSettings(section = "") {
  window.dispatchEvent(new CustomEvent("tomo-settings", { detail: section }));
}

function lastReply(messages: readonly Message[]): string {
  const last = [...messages].reverse().find((m) => m.role === "assistant");
  const parts = Array.isArray(last?.content) ? (last.content as Array<{ type?: string; text?: string }>) : [];
  const text = parts.filter((p) => p.type === "text").map((p) => p.text ?? "").join(" ");
  const flat = text.replace(/[#*_`>|-]+/g, " ").replace(/\s+/g, " ").trim();
  return flat.length > 120 ? `${flat.slice(0, 119)}…` : flat;
}

export function useDesktopBridge(isRunning: boolean, messages: readonly Message[], title: string) {
  const aui = useAui();

  useEffect(() => {
    window.tomo = {
      ask(text: string) {
        const value = String(text ?? "").trim();
        aui.threads().switchToNewThread();
        if (!value) return;   // 只开新对话（桌面端菜单「新对话」）
        // 新对话切过去后下一帧才是当前对话，再把文字放进输入框发出
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            const composer = aui.thread().composer();
            composer.setText(value);
            composer.send();
          }),
        );
      },
      settings: (section?: string) => openSettings(section ?? ""),
    };
    return () => {
      delete window.tomo;
    };
  }, [aui]);

  const wasRunning = useRef(isRunning);
  useEffect(() => {
    const finished = wasRunning.current && !isRunning;
    wasRunning.current = isRunning;
    if (!finished || document.hasFocus() || typeof Notification === "undefined") return;
    if (Notification.permission !== "granted") return;
    const note = new Notification(title || "CC", { body: lastReply(messages), tag: "tomo-reply" });
    note.onclick = () => window.focus();
  }, [isRunning, messages, title]);

  useEffect(() => {
    const over = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    const drop = (e: DragEvent) => {
      if (!e.dataTransfer?.files.length) return;
      // 拖到输入框上的已由输入框自己收下（它会标记 defaultPrevented），这里只收其他地方的
      const handled = e.defaultPrevented;
      e.preventDefault();
      if (handled) return;
      for (const file of Array.from(e.dataTransfer.files)) void aui.thread().composer().addAttachment(file);
    };
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, [aui]);
}

// ---------------------------------------------------------------------------
// 桌面端的本机设置（设置里「此电脑」一栏）：Tomo 窗口经 QWebChannel 放进 window.tomoDesktop
// （companion/src/companion/tomo_bridge.py）；浏览器里没有，这一栏就不显示。

export type MemberStatus = "online" | "processing" | "attention" | "offline" | "connecting" | "paused";
export type DesktopMember = { id: string; name: string; kind: string; paused: boolean; status: MemberStatus; folders: string[] | null };
export type DesktopState = {
  version: string;
  site: string; // 这台电脑连的公司（站点域名），没连是空
  express_roots: string[];
  members: DesktopMember[];
  autostart: boolean;
  hotkey: boolean;
  update: { state: string; version: string };
};
export type DesktopResult = { ok: boolean; message?: string; cancelled?: boolean; roots?: string[]; other_site?: string };

type Signal<A extends unknown[]> = { connect(cb: (...args: A) => void): void; disconnect(cb: (...args: A) => void): void };
type Channel = {
  state(cb: (json: string) => void): void;
  call(method: string, args: string, cb: (json: string) => void): void;
  changed: Signal<[]>;
  done: Signal<[string, string]>;
};

declare global {
  interface Window {
    qt?: { webChannelTransport?: unknown };
    tomoDesktop?: Channel;
  }
}

export type Desktop = {
  state(): Promise<DesktopState>;
  call(method: string, args?: Record<string, unknown>): Promise<DesktopResult>;
  onChanged(cb: () => void): () => void;
};

function wrap(channel: Channel): Desktop {
  const lang = (document.documentElement.lang || "th").slice(0, 2);
  return {
    state: () => new Promise((resolve) => channel.state((json) => resolve(JSON.parse(json)))),
    call: (method, args = {}) =>
      new Promise((resolve) => {
        channel.call(method, JSON.stringify({ ...args, lang }), (json) => {
          const first = JSON.parse(json) as DesktopResult & { job?: string };
          if (!first.job) return resolve(first);
          // 慢的操作（配对、换 Express 文件夹、找 Express）：先回 job，做完发 done
          const done = (job: string, result: string) => {
            if (job !== first.job) return;
            channel.done.disconnect(done);
            resolve(JSON.parse(result));
          };
          channel.done.connect(done);
        });
      }),
    onChanged(cb) {
      channel.changed.connect(cb);
      return () => channel.changed.disconnect(cb);
    },
  };
}

let pending: Promise<Desktop | null> | null = null;

/** 在 Tomo 桌面端里返回本机设置的接口；浏览器里返回 null。 */
export function desktop(): Promise<Desktop | null> {
  if (!window.qt?.webChannelTransport) return Promise.resolve(null);
  pending ??= new Promise((resolve) => {
    if (window.tomoDesktop) return resolve(wrap(window.tomoDesktop));
    const ready = () => window.tomoDesktop && resolve(wrap(window.tomoDesktop));
    window.addEventListener("tomo-desktop", ready, { once: true });
    setTimeout(() => resolve(window.tomoDesktop ? wrap(window.tomoDesktop) : null), 5000);
  });
  return pending;
}

// ---------------------------------------------------------------------------
// 用登录的账号连上这台电脑（2026-10-10 起取代手抄配对码）：桌面端打开对话页时自动做一次，
// 本站按账号权限发一把只交给本机的密钥（pearnly_cowork.companion.api.register），桌面端拿它静默配对。
// 只在 Cowork 站点上做；已连着这家公司不重复做；已连着别家、或这条连接正被另一台电脑用着，
// 都不自动抢，等用户在设置里点。

export type Link =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "in_use"; generation: number }
  | { kind: "other_site"; site: string }
  | { kind: "not_admin" }
  | { kind: "no_connection" }
  | { kind: "failed"; message: string };

let link: Link = { kind: "idle" };
const listeners = new Set<(value: Link) => void>();

function setLink(value: Link) {
  link = value;
  for (const cb of listeners) cb(value);
}

export function currentLink(): Link {
  return link;
}

export function onLink(cb: (value: Link) => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

type Registered =
  | { status: "created"; token: string }
  | { status: "in_use"; generation: number }
  | { status: "no_connection" };

async function register(express: boolean, replace?: number): Promise<Registered | Link> {
  const res = await fetch("/api/method/pearnly_cowork.companion.api.register", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify({
      request_key: crypto.randomUUID(),
      express,
      ...(replace ? { replace: true, expected_generation: replace } : {}),
    }),
  });
  if (res.status === 403) return { kind: "not_admin" };
  const body = (await res.json().catch(() => null)) as { message?: Registered } | null;
  if (!res.ok || !body?.message) return { kind: "failed", message: "" };
  return body.message;
}

/** 连接这台电脑。replace：这条连接正被另一台电脑用着时，用户确认改用这台（带那把密钥的代数）；switchSite：断开原来连的别家公司。 */
export async function connectComputer(api: Desktop, options: { replace?: number; switchSite?: boolean } = {}) {
  if (link.kind === "connecting") return;
  setLink({ kind: "connecting" });
  try {
    const state = await api.state();
    let roots = state.express_roots ?? [];
    if (!roots.length) roots = (await api.call("detect")).roots ?? [];
    const found = await register(roots.length > 0, options.replace);
    if ("kind" in found) return setLink(found);
    if (found.status === "in_use") return setLink({ kind: "in_use", generation: found.generation });
    if (found.status === "no_connection") return setLink({ kind: "no_connection" });
    const paired = await api.call("pair", { code: `${found.token}@${location.host}`, switch: !!options.switchSite });
    if (paired.other_site) return setLink({ kind: "other_site", site: paired.other_site });
    setLink(paired.ok ? { kind: "idle" } : { kind: "failed", message: paired.message ?? "" });
  } catch {
    setLink({ kind: "failed", message: "" });
  }
}

/** 对话页打开时：Cowork 站点、在桌面端里、这台电脑还没连这家公司 → 自动连一次。 */
export function useAutoConnect(product: string | undefined) {
  useEffect(() => {
    if (product !== "cowork") return;
    let alive = true;
    void desktop().then(async (api) => {
      if (!api || !alive) return;
      const state = await api.state();
      if (!state || !("members" in state)) return;
      if (state.site && state.site !== location.host) return setLink({ kind: "other_site", site: state.site });
      if (!state.members.length) await connectComputer(api);
    });
    return () => {
      alive = false;
    };
  }, [product]);
}

// 对话文字大小（设置 → 常规），只存在本机浏览器里
export type TextSize = "small" | "medium" | "large";
const TEXT_SIZE_KEY = "tomo-text-size";

export function textSize(): TextSize {
  try {
    const value = localStorage.getItem(TEXT_SIZE_KEY);
    return value === "small" || value === "large" ? value : "medium";
  } catch {
    return "medium";
  }
}

export function setTextSize(size: TextSize) {
  document.documentElement.dataset.textSize = size;
  try {
    localStorage.setItem(TEXT_SIZE_KEY, size);
  } catch {
    // 无痕窗口等存不了：本次照样生效
  }
}

document.documentElement.dataset.textSize = textSize();
