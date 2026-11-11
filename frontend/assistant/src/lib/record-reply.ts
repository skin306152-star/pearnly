// 「录吧」快捷回复什么时候出现（总表二 11-10「去掉开始按钮」：卡片不放开始，开始由用户一句话下令；输入框上方给一个「录吧」，
// 等同打字，手机一点即可）。
//
// 只看对话里已有的内容，不另开接口：CC 调了写入计划（A 线 pearnly_write_plan），计划里有要写的（status = ready），
// 而且这是对话目前最后一轮、已经说完（复述完在等用户点头）。用户发了任何一句（包括点「录吧」）以后最后一条就是用户的，
// 它自然消失；没有要写的（nothing_to_write 等）、工具报错时不出现。放不放行仍由服务端按用户消息序号判，这里只管出不出现。

import { reportCaught } from "@/lib/caught";

/** A 线的写入计划工具（评估「一句话下令」第 2 节）。 */
export const WRITE_PLAN_TOOL = "pearnly_write_plan";

type Part = { type?: string; toolName?: string; result?: unknown; isError?: boolean };
type Message = { role: string; content: ReadonlyArray<unknown> };

export function offersRecord(messages: ReadonlyArray<Message>, running: boolean): boolean {
  if (running) return false;
  // 本站提醒卡片（system，reducer.withNotices）可能排在回复后面，它们不是一轮对话
  const last = messages.findLast((m) => m.role !== "system");
  if (!last || last.role !== "assistant") return false;
  // 同一轮里改过再算的（用户说了修改，CC 重新调一次计划）按最后一次
  const plan = (last.content as ReadonlyArray<Part>).findLast((p) => p.type === "tool-call" && p.toolName === WRITE_PLAN_TOOL);
  if (!plan || plan.isError || plan.result === undefined) return false;
  return statusOf(plan.result) === "ready";
}

/** 工具结果的正文是一段 JSON 文字（或带 text 的对象，同 tool-uis/operator-shared 的 resultOf）；读不出就当没有状态。 */
function statusOf(result: unknown): string {
  if (typeof result === "object" && result !== null && "status" in result) return String((result as { status: unknown }).status);
  const text = typeof result === "object" && result !== null && "text" in result ? String((result as { text: unknown }).text) : String(result);
  if (!/^\s*\{/.test(text)) return "";
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed && typeof parsed === "object" && "status" in parsed ? String((parsed as { status: unknown }).status) : "";
  } catch (caughtError) {
    reportCaught(caughtError, "record-reply");
    return "";
  }
}
