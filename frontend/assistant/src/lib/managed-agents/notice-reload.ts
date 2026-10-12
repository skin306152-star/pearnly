// 提醒挂到的对话不在侧栏已加载的那几页里（较早的对话）：重新拉一次列表，它就排到最上面（服务端把它的时间改成了现在）。
// 组件库没有只插一行的接口，只能整表重拉，所以要省着拉：
//   - 几条提醒合成一次（delay 内到的一起算）；
//   - 列表正在加载、正在加载更多，或新对话正在建（整表重拉会把刚建的新对话冲掉）时先不拉，过一会儿再看；
//   - 到时已经在列表里了（别的原因拉过了）就不拉。

type Item = { readonly id: string; readonly remoteId?: string | undefined; readonly status: string };
type Threads = {
  getState(): {
    readonly threadIds: readonly string[];
    readonly threadItems: readonly Item[];
    readonly isLoading: boolean;
    readonly isLoadingMore: boolean;
  };
  reload(): Promise<void>;
};

export function noticeReloader(threads: () => Threads, delay: number): { note: (session: string) => void; stop: () => void } {
  const waiting = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;

  const listed = (session: string) => {
    const { threadIds, threadItems } = threads().getState();
    return threadItems.some((item) => item.remoteId === session && threadIds.includes(item.id));
  };

  const settle = () => {
    timer = undefined;
    for (const session of waiting) if (listed(session)) waiting.delete(session);
    if (!waiting.size) return;
    const { isLoading, isLoadingMore, threadItems } = threads().getState();
    // 新对话发出第一句时：组件库先把它标成 regular，建好会话才有 remoteId
    const creating = threadItems.some((item) => item.status === "regular" && !item.remoteId);
    if (isLoading || isLoadingMore || creating) {
      timer = setTimeout(settle, delay);
      return;
    }
    waiting.clear();
    void threads().reload().catch(() => undefined);
  };

  return {
    note(session: string) {
      if (!session || listed(session)) return;
      waiting.add(session);
      timer ??= setTimeout(settle, delay);
    },
    stop() {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      waiting.clear();
    },
  };
}
