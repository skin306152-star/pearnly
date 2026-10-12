// 把 CC 装到这台设备上（用户 2026-10-12）：在哪儿打开网页，就引导装到哪儿。
//
//   Windows 电脑（Cowork）   → 下载 CC 桌面版（能写 Express、文件夹定时任务，功能最全）
//   安卓、Mac / Linux 的 Chrome、Edge，以及非 Cowork 的 Windows → 一键「安装 CC」（浏览器把网页装成应用）
//   iPhone / iPad             → 苹果不让网页一键安装，给「分享 → 添加到主屏幕」的两步指引
//   已在 CC 桌面版里、或已装成应用打开 → 什么都不显示
//
// 一键安装要有后台脚本（pearnly_core.assistant.api.service_worker），这里一并注册；它只管 /assistant 页面。

import { useEffect, useState } from "react";
import { isDesktopApp } from "@/lib/desktop";
import { getMethod } from "@/lib/frappe";

export type InstallTarget = "desktop" | "pwa" | "ios" | null;

type InstallPrompt = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };

const SERVICE_WORKER = "/api/method/pearnly_core.assistant.api.service_worker";
const DISMISS_KEY = "cc.install.dismissed";

let deferred: InstallPrompt | null = null;
let installed = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

function standalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

function isIOS(): boolean {
  const ua = navigator.userAgent;
  // iPadOS 13 起自称 Mac：有触屏的「Mac」就是 iPad
  return /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
}

function isWindows(): boolean {
  return /Windows/.test(navigator.userAgent);
}

if (typeof window !== "undefined" && !isDesktopApp()) {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault(); // 不让浏览器自己弹，由页面在合适的地方给按钮
    deferred = event as InstallPrompt;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    installed = true;
    deferred = null;
    notify();
  });
  if ("serviceWorker" in navigator) {
    void navigator.serviceWorker.register(SERVICE_WORKER, { scope: "/assistant" }).catch(() => undefined);
  }
}

/** 这台设备该怎么装（见文件头）；`product` 决定 Windows 上是不是给 CC 桌面版。 */
export function useInstallTarget(product: string | undefined): InstallTarget {
  const [, render] = useState(0);
  useEffect(() => {
    const listener = () => render((n) => n + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  if (isDesktopApp() || standalone() || installed) return null;
  if (isWindows() && product === "cowork") return "desktop";
  if (deferred) return "pwa";
  if (isIOS()) return "ios";
  return null;
}

/** 按设备装：下载桌面版安装包 / 弹浏览器的安装确认。iOS 由调用方显示指引。 */
export async function install(target: InstallTarget): Promise<void> {
  if (target === "desktop") {
    const info = await getMethod<{ url?: string }>("pearnly_cowork.companion.api.installer").catch(() => null);
    if (info?.url) window.open(info.url, "_blank", "noopener,noreferrer");
  } else if (target === "pwa" && deferred) {
    await deferred.prompt();
    const choice = await deferred.userChoice.catch(() => null);
    if (choice?.outcome === "accepted") {
      installed = true;
      deferred = null;
      notify();
    }
  }
}

/** 欢迎页那条提示关掉后这台设备不再出现（菜单里那一项一直在）。 */
export function installDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissInstall(): void {
  try {
    localStorage.setItem(DISMISS_KEY, "1");
  } catch {
    // 无痕窗口等存不了：本次关掉即可
  }
}
