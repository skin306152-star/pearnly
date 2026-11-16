// 快捷键表与按键判断的纯函数（components/app-shortcuts.tsx 用；不碰页面，测试直接调）。依据见那个文件开头。

export type Row = { label: () => string };

/** 表里按说明文字搜（不分大小写）；空的显示全部。 */
export function filterRows<T extends Row>(rows: readonly T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  return q ? rows.filter((row) => row.label().toLowerCase().includes(q)) : [...rows];
}

/** 这条消息里的字（用户消息的文本段连起来）。 */
export function textOf(message: { content?: readonly { type: string; text?: string }[] } | undefined): string {
  return (message?.content ?? []).filter((part) => part.type === "text").map((part) => part.text ?? "").join("\n").trim();
}

/** Ctrl/⌘ + 键（不带 Alt）：按下的是哪一个；不是这几个回 null。 */
export function chord(event: Pick<KeyboardEvent, "ctrlKey" | "metaKey" | "altKey" | "shiftKey" | "code">): "search" | "new" | "attach" | "table" | null {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return null;
  if (event.code === "KeyK" && !event.shiftKey) return "search";
  if (event.code === "KeyO" && event.shiftKey) return "new";
  if (event.code === "KeyU" && !event.shiftKey) return "attach";
  if (event.code === "Slash" && !event.shiftKey) return "table";
  return null;
}
