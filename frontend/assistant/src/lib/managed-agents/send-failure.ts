// 发送没成功：新对话建会话被拒（本站点没设置 CC、没有权限……）、断网、服务端出错，或已有对话发出被拒。
// 组件库按下发送就清空了输入框，交给我们的发送失败了它不放回，Promise 也没人接（2026-10-16 测试窗口：没配 CC 的站点发第一条，
// 气泡和「思考中」闪一下就没了，打的字也没了，只有控制台一条未捕获的错误）。这里：原因写在输入框上方（服务端原文，没有原文
// 用通用的一句，lib/frappe.ts 的 shownError），打的字与附件放回输入框。原因按对话记，同一段对话再发送时清掉。只在本页。
// 附件没传上（发送时重传一次也没传上，attachments.ts 的 filesToSend）同样算没发出去。
// 停止没成功（回复中途断网后点停止，2026-10-17 第 9 轮第 16 条：控制台未捕获的 Failed to fetch，页面一直「思考中」）也写在
// 这里：只说原因，字不放回（消息早已发出）；停止键还在，再点就再试；回复结束（不在跑了）就清掉。

import { useSyncExternalStore } from "react";
import type { CompleteAttachment, CreateAttachment } from "@assistant-ui/react";
import { shownError } from "@/lib/frappe";
import { isUploaded } from "./attachments";

/** 输入框里要用到的几项（组件库 ThreadComposerRuntime 的子集）。 */
export type ComposerLike = {
  getState(): { text: string };
  setText(text: string): void;
  addAttachment(attachment: CreateAttachment | File): Promise<void>;
};

let failure: { thread: string; message: string; stop?: boolean } | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/** 发送没成功：记下原因；restore 时把这条消息的字和附件放回输入框（没发出去才放回）。 */
export function sendFailed(
  thread: string,
  error: unknown,
  restore?: { composer: ComposerLike | null; text: string; attachments?: readonly CompleteAttachment[] },
) {
  failure = { thread, message: shownError(error) };
  emit();
  if (!restore?.composer) return;
  const { composer, text, attachments = [] } = restore;
  // 等的这一会儿又打了字：原来那条放在前面，新打的接在后面，两段都不丢
  const typed = composer.getState().text;
  if (text) composer.setText(typed.trim() ? `${text}\n${typed}` : text);
  // 已传进会话的：原样放回，编号不变，再发送时照样带上。没传上的（新对话没设置 CC 时选的文件，上传前建会话就被拒了；
  // 发送时重传也没传上的）：把原文件放回去重新传，上传中、出错照常显示在那一项上，不当成传好了的
  for (const a of attachments) {
    const back = isUploaded(a.id) || !a.file
      ? composer.addAttachment({ id: a.id, type: a.type, name: a.name, contentType: a.contentType, content: a.content })
      : composer.addAttachment(a.file);
    void back.catch((err) => console.debug("[cc] attachment not restored:", err));
  }
}

/** 发出一条（run 里建会话、发出；真发出去了调 sent()）。没成功：记原因、没发出去的字和附件放回输入框，回 false。
 *  从不抛出：组件库调发送不等也不接错（ExternalStoreThreadRuntimeCore.append 由输入框的 handleSend 直接调用），抛了就是未捕获的错误。
 *  已发出、只是之后的打断没成功：消息已在对话里，只说原因、不放回。 */
export async function attemptSend(
  thread: string,
  restore: { composer: ComposerLike | null; text: string; attachments?: readonly CompleteAttachment[] },
  run: (sent: () => void) => Promise<void>,
): Promise<boolean> {
  clearSendFailure(thread);
  let delivered = false;
  try {
    await run(() => {
      delivered = true;
    });
    return true;
  } catch (error) {
    sendFailed(thread, error, delivered ? undefined : restore);
    return false;
  }
}

/** 停止键（打断这一轮）。没成功：原因写在输入框上方，不放回任何东西、不抛出（组件库调停止不等也不接错）；再点再试。 */
export async function attemptStop(thread: string, run: () => Promise<void>): Promise<boolean> {
  clearSendFailure(thread);
  try {
    await run();
    return true;
  } catch (error) {
    failure = { thread, message: shownError(error), stop: true };
    emit();
    return false;
  }
}

/** 这一轮已不在跑：停止没成功的原因不用再留着（发送没成功的留到再发送）。 */
export function clearStopFailure(thread: string) {
  if (failure?.thread === thread && failure.stop) clearSendFailure(thread);
}

/** 再发送（或别处要清）时清掉这段对话的出错原因。 */
export function clearSendFailure(thread: string) {
  if (failure?.thread !== thread) return;
  failure = null;
  emit();
}

/** 这段对话上次发送没成功的原因（没有是 ""）。 */
export function sendFailureOf(thread: string): string {
  return failure?.thread === thread ? failure.message : "";
}

export function useSendFailure(thread: string): string {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => sendFailureOf(thread),
  );
}
