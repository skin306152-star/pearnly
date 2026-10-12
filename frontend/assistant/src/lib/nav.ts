// 页面内的「后退 / 前进」（桌面端标题栏的两个箭头，同 Claude 桌面端）：记下看过的视图与对话，箭头在它们之间切换。
// 只在本页内存里，不进浏览器历史（对话页只有一个地址，浏览器后退会离开 CC）。

import { useAui, useAuiState } from "@assistant-ui/react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

export type View = "chat" | "tasks";
type Entry = { view: View; thread: string };

export const ViewContext = createContext<{ view: View; setView: (v: View) => void }>({ view: "chat", setView: () => {} });
export const useView = () => useContext(ViewContext);

export function useNavHistory(view: View, setView: (v: View) => void) {
  const aui = useAui();
  const thread = useAuiState((s) => s.threads.mainThreadId) ?? "";
  const stack = useRef<Entry[]>([]);
  const index = useRef(-1);
  const replaying = useRef(false);
  const [, bump] = useState(0);

  useEffect(() => {
    const current = stack.current[index.current];
    if (replaying.current) {
      replaying.current = false;
      return;
    }
    if (current && current.view === view && current.thread === thread) return;
    stack.current = [...stack.current.slice(0, index.current + 1), { view, thread }].slice(-50);
    index.current = stack.current.length - 1;
    bump((n) => n + 1);
  }, [view, thread]);

  const go = useCallback(
    (step: number) => {
      const target = stack.current[index.current + step];
      if (!target) return;
      index.current += step;
      replaying.current = true;
      setView(target.view);
      if (target.thread && target.thread !== thread) void aui.threads().switchToThread(target.thread);
      bump((n) => n + 1);
    },
    [aui, setView, thread],
  );

  return {
    canBack: index.current > 0,
    canForward: index.current < stack.current.length - 1,
    back: () => go(-1),
    forward: () => go(1),
  };
}
