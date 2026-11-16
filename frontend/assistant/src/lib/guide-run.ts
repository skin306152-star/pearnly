// 按表单的引导（引导式 P0 接线，服务端 pearnly_core/assistant/guide/run.py）：开一次、按 run 取卡、作答、确定、取消、搜索选项。
// 卡的内容（题目、选项、复核、结果）都由服务端出好（标题已按本页语言翻好），页面只画、只把点选的值按键交回。
import { get, post } from "@/lib/frappe";

export type GuideOption = { code: string; name?: string; note?: string; amount?: string; max?: string; kind?: string };
export type GuideQuestion = {
  key: string; input: string; rule: string; tier: string; title: string; required: boolean;
  options?: GuideOption[]; more?: boolean; search?: boolean; facts?: string[]; wait?: boolean; preselected?: unknown; value?: unknown;
};
export type GuideDecided = { key: string; title: string; value: unknown; value_label?: string; source?: string };
export type GuideReview = {
  version: number; ready: boolean; problem?: string; problem_text?: string; total?: string;
  lines?: { key: string; label: string; value: string; step?: string }[];
  tables?: { key: string; label: string; step?: string; rows: { code: string; name?: string; note?: string; amount?: string }[] }[];
};
export type GuideResult = { status: string; docnum?: string; reason_text?: string; total?: string; customer?: string; wait?: boolean };
export type GuideView = {
  run: string; business: string; status: string; version: number; stale?: boolean; reason?: string; outcome?: string;
  refused?: Record<string, string>;
  card?: { questions: GuideQuestion[]; decided: GuideDecided[]; ready: boolean; answers?: Record<string, unknown>;
    returned?: Record<string, { reason: string; text?: string }> };
  review?: GuideReview;
  result?: GuideResult;
};

export const startGuide = (sessionId: string, business: string) =>
  post<GuideView>("guide_start", { session_id: sessionId, business });
export const viewGuide = (run: string) => get<GuideView>("guide_view", { run_name: run });
export const answerGuide = (run: string, answers: Record<string, unknown>, version: number) =>
  post<GuideView>("guide_answer", { run_name: run, answers: JSON.stringify(answers), version });
export const confirmGuide = (run: string, version: number) => post<GuideView>("guide_confirm", { run_name: run, version });
export const cancelGuide = (run: string) => post<GuideView>("guide_cancel", { run_name: run });
export const searchGuide = (run: string, key: string, txt: string) =>
  get<{ options: GuideOption[]; more: boolean }>("guide_search", { run_name: run, key, txt });
