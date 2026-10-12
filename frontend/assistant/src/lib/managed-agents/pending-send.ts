// 发送后到真正的气泡出现之间，对话里先显示一个发送中的用户气泡（同 Claude：按下发送立刻出现）。两种等待：
//   带附件 — 组件库要等附件传完才把消息交给我们（2026-10-10 测试：回车后约 3 秒空白）；
//   新对话 — 第一条消息先要在服务端建好会话（2026-10-12 测试：约 1 秒空白）。
// 真正的气泡放进对话的同一刻清掉（session-controller 的 onShown）。只在本页，不进会话记录。

import { useSyncExternalStore } from "react";

export type PendingSend = { text: string; files: string[] } | null;

let pending: PendingSend = null;
let draft = "";
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/** 输入框里当前的文字（发送那一刻组件库先调附件的 send、后清空输入框，此时这里还是要发的文字）。 */
export function setDraft(text: string) {
  draft = text;
}

/** 附件还在上传时就发送了：先显示发送中的气泡。 */
export function beginPending(filename: string) {
  pending = { text: pending?.text ?? draft, files: [...(pending?.files ?? []), filename] };
  emit();
}

/** 新对话的第一条消息：建会话期间先显示发送中的气泡。 */
export function beginPendingText(text: string) {
  pending = { text, files: pending?.files ?? [] };
  emit();
}

export function clearPending() {
  if (!pending) return;
  pending = null;
  emit();
}

export function usePendingSend(): PendingSend {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => pending,
  );
}
