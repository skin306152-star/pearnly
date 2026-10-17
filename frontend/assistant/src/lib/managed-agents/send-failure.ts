// 发送没成功：新对话建会话被拒（本站点没设置 CC、没有权限……）、断网、服务端出错，或已有对话发出被拒。
// 组件库按下发送就清空了输入框，交给我们的发送失败了它不放回，Promise 也没人接（2026-10-16 测试窗口：没配 CC 的站点发第一条，
// 气泡和「思考中」闪一下就没了，打的字也没了，只有控制台一条未捕获的错误）。这里：原因写在输入框上方（服务端原文，没有原文
// 用通用的一句，lib/frappe.ts 的 shownError），打的字与附件放回输入框。原因按对话记，同一段对话再发送时清掉。只在本页。
// 附件没传上（发送时重传一次也没传上，attachments.ts 的 filesToSend）同样算没发出去。
// 停止没成功（回复中途断网后点停止，2026-10-17 第 9 轮第 16 条：控制台未捕获的 Failed to fetch，页面一直「思考中」）也写在
// 这里：只说原因，字不放回（消息早已发出）；停止键还在，再点就再试；回复结束（不在跑了）就清掉。
// 原因按对话分开记，发送没成功与停止没成功各记各的（第 10 轮审查：原来全页一个位置，A 对话停止没成功会盖掉 B 对话还没处理的
// 发送失败原因，点停止也会先清掉本对话的发送失败原因）；两样都有时显示停止那一行（较新）。
// 只在确实没发出时放回：发出请求的回应没回来（断网）而服务端已收下（回声已到）的，会话控制器当作发出了、不抛，这里不放回；
// 新对话发出后补的打断没成功同停止键，只写停止的原因（第 10 轮 E30：放回后再按发送会重复一条）。
// 回声比失败晚到（第 11 轮 F39：请求断在路上，服务端几秒后才收下）：放回时看不出发没发出，先放回不丢字；之后这条的回声到了
// （会话控制器的 onDelivered），sendLanded 把放回的字和附件撤掉（用户没改过的才撤）、原因清掉，不会再发第二遍。

import { useSyncExternalStore } from "react";
import type { CompleteAttachment, CreateAttachment } from "@assistant-ui/react";
import { shownError } from "@/lib/frappe";
import { isUploaded } from "./attachments";

/** 输入框里要用到的几项（组件库 ThreadComposerRuntime 的子集）。 */
export type ComposerLike = {
  getState(): { text: string; attachments?: readonly { id: string }[] };
  setText(text: string): void;
  addAttachment(attachment: CreateAttachment | File): Promise<void>;
  clearAttachments?(): Promise<void> | void;
};

/** 各对话的出错原因：send 发送没成功，stop 停止没成功。 */
const failures = new Map<string, { send?: string; stop?: string }>();
/** 各对话最近一次放回输入框的：哪个输入框、放回的字、附件编号（回声晚到时撤掉，sendLanded）。 */
const restored = new Map<string, { composer: ComposerLike; text: string; attachments: string[] }>();
const listeners = new Set<() => void>();

function note(thread: string, kind: "send" | "stop", message: string | undefined) {
  const next = { ...failures.get(thread) };
  if (message) next[kind] = message;
  else delete next[kind];
  if (next.send || next.stop) failures.set(thread, next);
  else failures.delete(thread);
}

function emit() {
  for (const listener of listeners) listener();
}

/** 发送没成功：记下原因；restore 时把这条消息的字和附件放回输入框（没发出去才放回）。 */
export function sendFailed(
  thread: string,
  error: unknown,
  restore?: { composer: ComposerLike | null; text: string; attachments?: readonly CompleteAttachment[] },
) {
  note(thread, "send", shownError(error));
  emit();
  if (!restore?.composer) return;
  const { composer, text, attachments = [] } = restore;
  restored.set(thread, { composer, text, attachments: attachments.map((a) => a.id) });
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
  clearStopFailure(thread);
  try {
    await run();
    return true;
  } catch (error) {
    note(thread, "stop", shownError(error));
    emit();
    return false;
  }
}

/** 这一轮已不在跑（或又点了停止）：停止没成功的原因不用再留着（发送没成功的留到再发送）。 */
export function clearStopFailure(thread: string) {
  if (!failures.get(thread)?.stop) return;
  note(thread, "stop", undefined);
  emit();
}

/** 放回输入框的那一条服务端其实收下了（回声晚到，会话控制器的 onDelivered；text 是用户打的字）：放回的字还原样在就撤掉（之后
 *  又打的字留着），放回的附件原样都在就一起撤掉，发送没成功的原因清掉。用户已改过、或这段对话没放回过这一条：不动输入框。 */
export function sendLanded(thread: string, text: string) {
  const back = restored.get(thread);
  if (!back || back.text !== text) return;
  restored.delete(thread);
  const { composer, attachments } = back;
  const now = composer.getState();
  if (text) {
    if (now.text === text) composer.setText("");
    else if (now.text.startsWith(`${text}\n`)) composer.setText(now.text.slice(text.length + 1));
  }
  const ids = (now.attachments ?? []).map((a) => a.id);
  if (attachments.length && ids.length === attachments.length && ids.every((id, i) => id === attachments[i])) {
    void Promise.resolve(composer.clearAttachments?.()).catch((err) => console.debug("[cc] attachments not cleared:", err));
  }
  if (failures.get(thread)?.send) {
    note(thread, "send", undefined);
    emit();
  }
}

/** 再发送（或别处要清）时清掉这段对话的出错原因（发送、停止两样都清）。 */
export function clearSendFailure(thread: string) {
  restored.delete(thread); // 再发送了：放回的已经用掉，晚到的回声不再去动输入框
  if (!failures.has(thread)) return;
  failures.delete(thread);
  emit();
}

/** 这段对话此刻要显示的出错原因（停止没成功的优先，没有是 ""）。 */
export function sendFailureOf(thread: string): string {
  const failure = failures.get(thread);
  return failure?.stop ?? failure?.send ?? "";
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
