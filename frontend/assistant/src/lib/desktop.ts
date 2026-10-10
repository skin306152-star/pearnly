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
    const note = new Notification(title || "Tomo", { body: lastReply(messages), tag: "tomo-reply" });
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
  express_roots: string[];
  members: DesktopMember[];
  autostart: boolean;
  hotkey: boolean;
  update: { state: string; version: string };
};
export type DesktopResult = { ok: boolean; message?: string; cancelled?: boolean; roots?: string[] };

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
