// 本对话业务（输入框上方的业务菜单，引导式录入第 1 期；服务端 pearnly_core.assistant.guided / sessions.set_business）。
//   清单 GET businesses() → {chat: [...], operator: [...]}：这个产品 × 这个模式真能做的业务（产品 hooks 声明），只读一次
//   当前 会话摘要带 business（侧栏列表读到时记下，noteBusiness）；本页选了先按本页显示
//   改   POST business(session_id, business)；还没有会话的新建页先记在本页（draft），会话建好后补交（useBusinessCarry）
import { useEffect } from "react";
import { create } from "zustand";
import { get, post } from "@/lib/frappe";
import { withReport } from "./caught";

export type Business = "purchase" | "sales" | "collection";
export type Offered = { chat: Business[]; operator: Business[] };


type State = { offered: Offered | null; bySession: Record<string, string>; draft: string };
export const useBusiness = create<State>(() => ({ offered: null, bySession: {}, draft: "" }));

let loading: Promise<void> | null = null;
/** 读一次这个产品能做的业务（读不到当没有，不出菜单）。 */
export function loadOffered(): Promise<void> {
  loading ??= get<Offered>("businesses")
    .then((out) => useBusiness.setState({ offered: { chat: out?.chat ?? [], operator: out?.operator ?? [] } }))
    .catch(withReport(() => useBusiness.setState({ offered: { chat: [], operator: [] } }), "business-offered"));
  return loading;
}

/** 侧栏列表读到的会话业务。 */
export function noteBusiness(sessionId: string, business: string | undefined) {
  if (!sessionId || business === undefined) return;
  if ((useBusiness.getState().bySession[sessionId] ?? "") === business) return;
  useBusiness.setState((s) => ({ bySession: { ...s.bySession, [sessionId]: business } }));
}

/** 选 / 取消（"" = 取消）。有会话就交给服务端，没收下就撤回；没有会话记在本页。 */
export function chooseBusiness(sessionId: string | null, business: string) {
  if (!sessionId) {
    useBusiness.setState({ draft: business });
    return;
  }
  const before = useBusiness.getState().bySession[sessionId] ?? "";
  noteBusiness(sessionId, business);
  void post("business", { session_id: sessionId, business }).catch(
    withReport(() => noteBusiness(sessionId, before), "business-set"),
  );
}

/** 这段对话此刻的业务（新建页是本页记的那个）。 */
export const useCurrentBusiness = (sessionId: string | null): string =>
  useBusiness((s) => (sessionId ? (s.bySession[sessionId] ?? "") : s.draft));

/** 新建页选了业务、会话随第一句建好之后补交给服务端。 */
export function useBusinessCarry(sessionId: string | null) {
  useEffect(() => {
    const { draft, bySession } = useBusiness.getState();
    if (!sessionId || !draft || bySession[sessionId] !== undefined) return;
    useBusiness.setState({ draft: "" });
    chooseBusiness(sessionId, draft);
  }, [sessionId]);
}
