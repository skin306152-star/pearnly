// 带附件发送时，组件库要等附件传完才把消息交给我们（runtime-provider 的 onNew），这段时间输入框已清空、
// 对话里却什么都没有，像是消息丢了（2026-10-10 测试：回车后约 3 秒空白）。这里记下「正在发送」的文字与附件名，
// 对话里先显示一个发送中的用户气泡；onNew 把真正的气泡放进对话的同一刻清掉。只在本页，不进会话记录。

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
