// 引导卡（工具 pearnly_guide，引导式录入第 1 期；服务端 pearnly_core.assistant.api.guide_card → guide_tool.stored_card）。
//   页面拿到的调用参数只有模型给的单据；卡由服务端出卡前存在执行记录里，按本人、本对话、这一次调用取。没有回空卡。
import { get } from "@/lib/frappe";

export type StoredGuideCard = { card?: { questions?: unknown[]; decided?: unknown[] }; company_title?: string };

export const fetchGuideCard = (sessionId: string, toolUseId: string) =>
  get<StoredGuideCard>("guide_card", { session_id: sessionId, tool_use_id: toolUseId });
