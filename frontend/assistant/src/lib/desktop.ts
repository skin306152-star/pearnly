// 桌面端（companion/ 里的 Tomo 窗口）和网页一起用的三件事，浏览器里打开时也照常生效：
//
//   window.tomo.ask(text) — 桌面端快捷键呼出的输入条把文字交给这里：开一个新对话发出去
//   回复完成通知          — 窗口不在前台时回复完成，用网页原生通知提醒（桌面端已授权；浏览器只在用户授权过时弹）
//   整窗拖文件            — 文件拖到对话页任何地方都加进输入框，不会被浏览器当成网页打开

import { useAui } from "@assistant-ui/react";
import { useEffect, useRef } from "react";

type Message = { role?: string; content?: unknown };

declare global {
  interface Window {
    tomo?: { ask: (text: string) => void };
  }
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
        if (!value) return;
        aui.threads().switchToNewThread();
        // 新对话切过去后下一帧才是当前对话，再把文字放进输入框发出
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            const composer = aui.thread().composer();
            composer.setText(value);
            composer.send();
          }),
        );
      },
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
