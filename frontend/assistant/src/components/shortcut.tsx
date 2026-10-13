// 键盘快捷键提示（菜单项右边的「Ctrl ,」、设置里快捷提问的组合键）：整页都经这里显示，不在别处直接写按键文字。
// 触屏设备（lib/touch.ts）上没有这些键，不显示；CC 桌面端窗口和电脑浏览器照常显示。

import { isDesktopApp } from "@/lib/desktop";
import { touchOnly } from "@/lib/touch";

export function showShortcuts(): boolean {
  return isDesktopApp() || !touchOnly();
}

/** 菜单项右边的灰字；kbd：设置里那一格（带边框）。 */
export function Shortcut({ keys, kbd }: { keys: string; kbd?: boolean }) {
  if (!showShortcuts()) return null;
  if (kbd) return <kbd className="rounded-md border bg-app px-2 py-0.5 font-sans text-muted-foreground text-xs">{keys}</kbd>;
  return <span className="text-ink-3 text-xs">{keys}</span>;
}
