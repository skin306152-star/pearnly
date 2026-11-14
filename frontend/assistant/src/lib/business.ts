// 本对话业务（输入框上方的业务菜单，引导式录入第 1 期；服务端 pearnly_core.assistant.guided / sessions.set_business）。
//   清单 GET businesses() → {chat: [...], operator: [...]}：这个产品 × 这个模式真能做的业务（产品 hooks 声明），只读一次
//   当前 会话摘要带 business（侧栏列表读到时记下，noteBusiness）；本页选了先按本页显示
//   改   POST business(session_id, business)；还没有会话的新建页先记在本页（draft），会话建好后补交（useBusinessCarry，from_start）
//   请交票据 选了业务，页面在对话里画一句「请上传…」（不进模型上下文；模型从每条消息的系统说明知道，contracts.business_note）：
//        选的时刻（business_at）之后用户还没交过文件（收款：还没说过话）才出，promptOf
import { useEffect } from "react";
import { create } from "zustand";
import { get, post } from "@/lib/frappe";
import { withReport } from "./caught";

export type Business = "purchase" | "sales" | "collection";
export type Offered = { chat: Business[]; operator: Business[] };


type State = { offered: Offered | null; bySession: Record<string, string>; atBySession: Record<string, number>; draft: string;
  draftAt: number };
export const useBusiness = create<State>(() => ({ offered: null, bySession: {}, atBySession: {}, draft: "", draftAt: 0 }));

/** 服务端的时刻（RFC3339，空 = 从会话开头算）→ 毫秒；读不出当 0（从开头算，宁可少出一句）。 */
const msOf = (value: string | undefined) => {
  const at = value ? new Date(value).getTime() : 0;
  return Number.isFinite(at) ? at : 0;
};

let loading: Promise<void> | null = null;
/** 读一次这个产品能做的业务（读不到当没有，不出菜单）。 */
export function loadOffered(): Promise<void> {
  loading ??= get<Offered>("businesses")
    .then((out) => useBusiness.setState({ offered: { chat: out?.chat ?? [], operator: out?.operator ?? [] } }))
    .catch(withReport(() => useBusiness.setState({ offered: { chat: [], operator: [] } }), "business-offered"));
  return loading;
}

/** 侧栏列表读到的会话业务。 */
export function noteBusiness(sessionId: string, business: string | undefined, at?: string | number) {
  if (!sessionId || business === undefined) return;
  const when = typeof at === "number" ? at : msOf(at);
  const state = useBusiness.getState();
  if ((state.bySession[sessionId] ?? "") === business && (at === undefined || state.atBySession[sessionId] === when)) return;
  useBusiness.setState((s) => ({ bySession: { ...s.bySession, [sessionId]: business },
    atBySession: at === undefined ? s.atBySession : { ...s.atBySession, [sessionId]: when } }));
}

/** 选 / 取消（"" = 取消）。有会话就交给服务端，没收下就撤回；没有会话记在本页。 */
export function chooseBusiness(sessionId: string | null, business: string, fromStart = false) {
  if (!sessionId) {
    useBusiness.setState({ draft: business, draftAt: Date.now() });
    return;
  }
  const state = useBusiness.getState();
  const before = state.bySession[sessionId] ?? "";
  const beforeAt = state.atBySession[sessionId] ?? 0;
  noteBusiness(sessionId, business, fromStart ? 0 : Date.now());
  void post<{ business?: string; business_at?: string }>("business", { session_id: sessionId, business, ...(fromStart ? { from_start: 1 } : {}) })
    .then((out) => noteBusiness(sessionId, out?.business ?? business, fromStart ? 0 : msOf(out?.business_at)))
    .catch(withReport(() => noteBusiness(sessionId, before, beforeAt), "business-set"));
}

/** 这段对话选业务的时刻（毫秒；0 = 从会话开头算）。 */
export const useBusinessAt = (sessionId: string | null): number =>
  useBusiness((s) => (sessionId ? (s.atBySession[sessionId] ?? 0) : s.draftAt));

type SeenMessage = { role: string; createdAt?: Date; attachments?: readonly unknown[] };

/** 对话里该不该画「请上传… / 请告诉我客户和收款金额」那一句（纯函数）：选了业务，且选的时刻之后用户还没交过文件
 *  （收款：还没说过话）、输入框里没挂着附件、正在发的那句也没带文件（收款：没在发）时出；回业务或 ""。 */
export function promptOf(business: string, at: number, messages: readonly SeenMessage[], composerFiles: number,
  pending: { text?: string; files?: readonly unknown[] } | null): string {
  if (!business) return "";
  const after = messages.filter((m) => m.role === "user" && (m.createdAt?.getTime() ?? 0) >= at);
  if (business === "collection") return after.length || pending ? "" : business;
  if (composerFiles || pending?.files?.length || after.some((m) => (m.attachments?.length ?? 0) > 0)) return "";
  return business;
}

/** 这段对话此刻的业务（新建页是本页记的那个）。 */
export const useCurrentBusiness = (sessionId: string | null): string =>
  useBusiness((s) => (sessionId ? (s.bySession[sessionId] ?? "") : s.draft));

/** 新建页选了业务、会话随第一句建好之后补交给服务端。 */
export function useBusinessCarry(sessionId: string | null) {
  useEffect(() => {
    const { draft, bySession } = useBusiness.getState();
    if (!sessionId || !draft || bySession[sessionId] !== undefined) return;
    useBusiness.setState({ draft: "", draftAt: 0 });
    chooseBusiness(sessionId, draft, true);
  }, [sessionId]);
}
