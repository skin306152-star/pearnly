// 把 CC 装到这台设备上（用户 2026-10-12）：在哪儿打开网页，就按那台设备的说法给一个按钮。
//
//   Windows 电脑（Cowork）   → 「下载 CC 桌面版」（能写 Express、文件夹定时任务，功能最全）
//   安卓、Linux、非 Cowork 的 Windows（Chrome、Edge）→ 一键「安装 CC」（浏览器把网页装成应用）
//   Mac                      → 「添加到程序坞」：Chrome、Edge 一键；Safari 网页调不起来，给「文件 → 添加到程序坞」
//   iPhone / iPad            → 「添加到主屏幕」：苹果不让网页调起（网页弹的分享面板里没有这一项，2026-10-12 实测
//                               Safari、Chrome 都是），按所用浏览器给它自己菜单里的准确步骤
//   LINE 里打开              → 「在浏览器中打开」：LINE 内置浏览器装不了，一点跳到手机默认浏览器
//   已在 CC 桌面版里、已装成应用打开、浏览器说已装 → 什么都不显示
//
// 一键安装要有后台脚本（pearnly_core.assistant.api.service_worker），这里一并注册；它只管 /assistant 页面。

import { useEffect, useState } from "react";
import { isDesktopApp } from "@/lib/desktop";
import { getMethod } from "@/lib/frappe";

export type InstallTarget = "desktop" | "pwa" | "mac" | "ios" | "browser" | null;

/** 浏览器调不起安装时给哪套步骤（见 install-prompt.tsx）。 */
export type InstallGuide = "safari-menu" | "share" | "chrome-ios" | "mac-safari";

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

function isMac(): boolean {
  return /Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints <= 1;
}

function isSafari(): boolean {
  const ua = navigator.userAgent;
  return /Safari\//.test(ua) && !/Chrome|Chromium|CriOS|FxiOS|EdgiOS|Edg\/|Firefox|OPR|OPT\//.test(ua);
}

function safariVersion(): number {
  return Number(/Version\/(\d+)/.exec(navigator.userAgent)?.[1] ?? 0);
}

/** LINE 内置浏览器：装不了，但认 openExternalBrowser=1 跳到默认浏览器。 */
function inLine(): boolean {
  return / Line\//i.test(navigator.userAgent);
}

/** 浏览器调不起时的步骤：iPhone 上 Safari 26 起分享收进地址栏旁的菜单，Chrome 的分享在地址栏右边。 */
export function installGuide(): InstallGuide {
  const ua = navigator.userAgent;
  if (isMac()) return "mac-safari";
  if (/CriOS/.test(ua)) return "chrome-ios";
  if (isSafari() && /iPhone|iPod/.test(ua) && safariVersion() >= 26) return "safari-menu";
  return "share";
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
  if (inLine()) return "browser";
  // Safari 17（macOS 14）起才有「添加到程序坞」
  if (isMac()) return deferred || (isSafari() && safariVersion() >= 17) ? "mac" : null;
  if (deferred) return "pwa";
  if (isIOS()) return "ios";
  return null;
}

/** 浏览器自己能装吗（能就一键，不能由调用方显示步骤）。 */
export function canPrompt(): boolean {
  return deferred !== null;
}

/** 按设备装：下载桌面版安装包 / 弹浏览器的安装确认 / 跳出 LINE。iPhone、Mac Safari 由调用方显示步骤。 */
export async function install(target: InstallTarget): Promise<void> {
  if (target === "desktop") {
    const info = await getMethod<{ url?: string }>("pearnly_cowork.companion.api.installer").catch(() => null);
    if (info?.url) window.open(info.url, "_blank", "noopener,noreferrer");
  } else if (target === "browser") {
    const url = new URL(location.href);
    url.searchParams.set("openExternalBrowser", "1");
    location.href = url.toString();
  } else if ((target === "pwa" || target === "mac") && deferred) {
    // 浏览器的安装确认每次页面只能弹一次，再调会直接失败：弹过就收起按钮（取消了下次打开页面浏览器会再给）
    const prompt = deferred;
    deferred = null;
    await prompt.prompt().catch(() => undefined);
    const choice = await prompt.userChoice.catch(() => null);
    if (choice?.outcome === "accepted") installed = true;
    notify();
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
