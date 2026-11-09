// 回复中先打下一句、排队发（批 A ⑤，对照文档第 2 项，11-09 实测 Claude：回复中回车 = 排队，那句先显示在输入框上方、可撤回，
// 本轮完了自动发出；以前 CC 回车只换行）。
//
// 用组件库自带的队列（createMessageQueue，外部数据运行时的 queue 选项）：接上以后输入框的每次发送都先进队列——空闲时马上发
// （同以前），在忙（新对话建会话并发出、回复中、等允许 / 等工具，同发送键变停止键的那个「忙」）时收下排着，忙转闲时放下一条。
// 排着的只在本页：没发出去之前刷新就没了（同 Claude 实测：排队那句不进对话记录）。
// 一条发出去了没开跑就失败（断网、被拒）：不等「忙转闲」，失败的当场放下一条，不卡住后面排着的；失败本身照旧写在输入框上方、
// 字放回输入框（send-failure.ts）。

import { type AppendMessage, createMessageQueue } from "@assistant-ui/react";
import { reportCaught } from "../caught";

export type SendQueue = ReturnType<typeof createSendQueue>;

export function createSendQueue(run: (message: AppendMessage) => Promise<unknown>) {
  let busy = false; // 这段对话在忙（由外面告诉）
  let starting = false; // 正在发出一条（还没开跑或刚开跑）
  const queue = createMessageQueue({
    run: (message) => {
      starting = true;
      void run(message)
        .catch((caughtError: unknown) => reportCaught(caughtError, "send-queue"))
        .finally(() => {
          starting = false;
          // 发出后已经在忙：等忙转闲再放下一条；没忙起来（失败、或这一轮已经结束）：当场放下一条
          if (!busy) queue.notifyIdle();
        });
    },
  });
  return {
    adapter: queue.adapter,
    subscribe: queue.subscribe,
    /** 这段对话忙不忙（同发送键变停止键的那个判断）：转忙时队列开始收着，转闲时放下一条。 */
    setBusy(next: boolean) {
      if (next === busy) return;
      busy = next;
      if (next) queue.notifyBusy();
      else if (!starting) queue.notifyIdle();
    },
  };
}
