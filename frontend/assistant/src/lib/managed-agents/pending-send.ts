// 发送后到真正的气泡出现之间，对话里先显示一个发送中的用户气泡（同 Claude：按下发送立刻出现）。两种等待：
//   带附件 — 组件库要等附件传完才把消息交给我们（2026-10-10 测试：回车后约 3 秒空白）；
//   新对话 — 第一条消息先要在服务端建好会话（2026-10-12 测试：约 1 秒空白）。
// 真正的气泡放进对话后（session-controller 的 onShown 给出它的编号），要等对话里真画出了这条才撤：会话状态交给组件库要晚
// 一次提交，在那之前就撤，两个气泡都不在，看上去自己的消息闪没了（教程第 8 条 a）。只在本页，不进会话记录。

import { useSyncExternalStore } from "react";

export type PendingSend = { text: string; files: string[]; shownAs?: string } | null;

let pending: PendingSend = null;
let draft = "";
let sentAt = 0;        // 最近一次按下发送的时刻
let handoff = "";      // 接替临时气泡的正式气泡编号：它挂上时不再弹一次
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
  if (!pending) sentAt = Date.now();
  pending = { text: pending?.text ?? draft, files: [...(pending?.files ?? []), filename] };
  emit();
}

/** 新对话的第一条消息：建会话期间先显示发送中的气泡。 */
export function beginPendingText(text: string) {
  pending = { text, files: pending?.files ?? [] };
  sentAt = Date.now();
  emit();
}

export function lastSendAt(): number {
  return sentAt;
}

/** 正式气泡挂上时问一次：是不是接替了刚才的临时气泡（是就不再弹）。 */
export function takeHandoff(messageId: string): boolean {
  if (!handoff || handoff !== messageId) return false;
  handoff = "";
  return true;
}

/** 正式气泡已放进对话（编号 messageId）：临时气泡留到对话里画出这一条为止（pendingLanded）。 */
export function pendingShownAs(messageId: string) {
  if (!pending) return;
  pending = { ...pending, shownAs: messageId };
  handoff = messageId;
  emit();
}

/** 对话里已经画出了接替它的正式气泡：临时气泡不再显示（渲染时就判断，不等撤掉的那次更新）。 */
export function pendingLanded(send: PendingSend, renderedIds: readonly string[]): boolean {
  return !!send?.shownAs && renderedIds.includes(send.shownAs);
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
