// 键盘快捷键提示（菜单项右边的「Ctrl ,」、设置里快捷提问的组合键）：整页都经这里显示，不在别处直接写按键文字。
// 触屏设备（lib/touch.ts）上没有这些键，不显示；CC 桌面端窗口和电脑浏览器照常显示。
//
// 源码里一律按 Windows / Linux 的写法给（Ctrl、Alt）；苹果设备（Mac、接键盘的 iPad）上显示成 ⌘、⌥（测试窗口第 7 轮：Mac 上用户
// 菜单的设置写成「Ctrl ,」）。这些快捷键的处理本来就两边都认（ctrlKey || metaKey，如设置的 Ctrl / ⌘ + ,）。

import { isDesktopApp } from "@/lib/desktop";
import { touchOnly } from "@/lib/touch";

export function showShortcuts(): boolean {
  return isDesktopApp() || !touchOnly();
}

/** 苹果设备（键盘上是 ⌘ 而不是 Ctrl）：Mac、iPhone、iPad（iPadOS 13 起自称 Mac）。 */
export function appleKeys(): boolean {
  if (typeof navigator === "undefined") return false;
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || "";
  return /Mac|iPhone|iPad|iPod/i.test(platform);
}

const APPLE: Record<string, string> = { Ctrl: "⌘", Alt: "⌥" };

/** 按这台设备显示的按键文字：苹果设备上 Ctrl → ⌘、Alt → ⌥，其余原样。 */
export function keysFor(keys: string, apple = appleKeys()): string {
  return apple ? keys.replace(/\w+/g, (key) => APPLE[key] ?? key) : keys;
}

/** 菜单项右边的灰字；kbd：设置里那一格（带边框）。 */
export function Shortcut({ keys, kbd }: { keys: string; kbd?: boolean }) {
  if (!showShortcuts()) return null;
  const shown = keysFor(keys);
  if (kbd) return <kbd className="rounded-md border bg-app px-2 py-0.5 font-sans text-muted-foreground text-xs">{shown}</kbd>;
  return <span className="text-ink-3 text-xs">{shown}</span>;
}
