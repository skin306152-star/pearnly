// 触屏设备（手机、平板：主要的输入方式不能悬停）。整页只在这里判断，别处不再各写一套：
//
//   components/assistant-ui/thread.tsx — 打开页面不自动聚焦输入框（不弹键盘）、消息下的操作按钮一直显示
//   components/shortcut.tsx            — 不显示键盘快捷键提示（菜单里的「Ctrl ,」等）；CC 桌面端窗口一律显示

export function touchOnly(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(hover: none)").matches;
}
