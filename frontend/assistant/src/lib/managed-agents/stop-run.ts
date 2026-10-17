// 停止键与输入框里的 Esc（打断这一轮）：各段对话的运行时（runtime-provider.tsx）把自己的「停止」登记在这里，输入框按对话编号调。
//
// 不经组件库的 cancelRun（第 11 轮 F39）：它是给本地运行时用的——回复还没出时点停止，它把最后那条用户消息从自己的消息仓库里
// 删掉、字放回输入框。我们的对话以服务端为准（外部数据，组件库没有 setMessages 可回写），那条消息服务端多半已经收下：字放回
// 输入框再按发送就重复一条；被它删掉的条目我们的数据里还在，两边从此对不上，之后再点停止时组件库按编号取那条就抛
// 「Entry not available in the store」。所以运行时不给组件库 onCancel（没有取消能力，组件库自己的停止、Esc 都不动作），
// 停止只走这里：消息留在对话里、不放回输入框；真没发出去的由发送失败那一路放回（send-failure.ts）。

const stoppers = new Map<string, () => Promise<void>>();

/** 运行时登记这段对话的停止（对话编号 = 组件库的 threadListItem.id）；返回取消登记。 */
export function registerStop(thread: string, stop: () => Promise<void>): () => void {
  stoppers.set(thread, stop);
  return () => {
    if (stoppers.get(thread) === stop) stoppers.delete(thread);
  };
}

/** 停止这段对话正在跑的这一轮（停止键、Esc）。不抛出：没成功的原因由登记的停止自己写（attemptStop）。 */
export async function stopRun(thread: string): Promise<void> {
  await stoppers.get(thread)?.();
}
