// 新对话的第一条消息：先在服务端建会话（约 1–4 秒）再发出。这期间还没有会话控制器，对话状态是空的，组件库会以为没在跑：
// 发送键还是「发送」、还能再发一条（可能并发再建一个会话），状态行却已经在说「思考中」（2026-10-15 生产 F4）。
// 每段对话（useSessionThreadRuntime 的一个实例）各有一个：建会话并发出期间算在跑；期间点停止先记下，发出后打断这段新会话
// （用户那条消息照常发出、留在对话里，回复被打断）。已有会话的对话（含先加附件、上传时已建好会话的）不经这里。

export class NewChatSend {
  private sending = false;
  private stopAsked = false;
  private readonly listeners = new Set<() => void>();

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** 正在建会话并发出（组件库的 isRunning 并上这一项）。 */
  isSending = () => this.sending;

  private set(sending: boolean) {
    if (this.sending === sending) return;
    this.sending = sending;
    for (const listener of this.listeners) listener();
  }

  /** 建会话 → 发出；期间点过停止就在发出后打断。失败照常抛出（发送键恢复可发送）。 */
  async run(
    ensure: () => Promise<string>,
    send: (sessionId: string) => Promise<void>,
    interrupt: (sessionId: string) => Promise<void>,
  ): Promise<void> {
    this.stopAsked = false;
    this.set(true);
    try {
      const id = await ensure();
      await send(id);
      if (this.stopAsked) await interrupt(id);
    } finally {
      this.stopAsked = false;
      this.set(false);
    }
  }

  /** 停止键：正在建会话并发出时记下（回 true，调用方不再自己打断），否则回 false。 */
  cancel(): boolean {
    if (!this.sending) return false;
    this.stopAsked = true;
    return true;
  }
}
