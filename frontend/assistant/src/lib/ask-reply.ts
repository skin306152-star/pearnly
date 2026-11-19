// 提问卡浮在输入框上方时，输入框里直接打字发出 = 交当前这题的「其他」（照 Claude 实测，审计 docs/work/2026-11-09-cc-vs-claude-ux-audit.md
// 第六节）：后面还没答的题记跳过，前面答过的照旧，卡片收成只读摘要，对话里不另出一条用户消息。
// 浮着的那张卡（components/tool-uis/ask-card.tsx 的 AskUserBody）在这里登记「用这句话作答」，输入框发送（runtime-provider.tsx 的 onNew）
// 先问这里：这段对话有浮着的提问卡就交给它，不当普通消息发出。只依赖 zustand：lib 不引组件。

import { create } from "zustand";

type Reply = { session: string; answer: (text: string) => void };

const useAskReply = create<{ reply: Reply | null }>(() => ({ reply: null }));

/** 浮着的提问卡登记（卡片收起时调回来的函数撤掉）。 */
export function offerAskReply(session: string, answer: (text: string) => void): () => void {
  const reply = { session, answer };
  useAskReply.setState({ reply });
  return () => {
    if (useAskReply.getState().reply === reply) useAskReply.setState({ reply: null });
  };
}

/** 输入框发出这句：这段对话有浮着的提问卡就用它作答、回 true（不再当消息发出）；没有回 false。 */
export function answerAskByText(session: string | null, text: string): boolean {
  const reply = useAskReply.getState().reply;
  if (!session || !reply || reply.session !== session || !text.trim()) return false;
  reply.answer(text.trim());
  return true;
}

/** 本页刚交的作答（按调用编号记）：浮层交了就收起，对话流里那张卡接着显示所选，不等服务端结果回来。 */
const useSentAnswers = create<{ sent: Record<string, unknown> }>(() => ({ sent: {} }));

export function noteSentAnswer(id: string, answer: unknown): void {
  useSentAnswers.setState((s) => ({ sent: { ...s.sent, [id]: answer } }));
}

export const useSentAnswer = (id: string | undefined): unknown => useSentAnswers((s) => (id ? s.sent[id] : undefined));
