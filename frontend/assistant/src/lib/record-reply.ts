// 「录吧」快捷回复什么时候出现（总表二 11-10「去掉开始按钮」：卡片不放开始，开始由用户一句话下令；输入框上方给一个「录吧」，
// 等同打字，手机一点即可）。
//
// 看这段对话的写入计划事件（A 线 assistant/write_plan.py 的 pearnly.write_plan，推送与补拉都带；归并层按计划记最新状态，
// reducer 的 writePlans）：有一份在等用户点头（awaiting），而且 CC 这一轮已说完（没在跑），就出现。计划开始了（started）、
// 被新的一份替掉（replaced）、没有计划都不出现；用户改了票、CC 重新算出新的一份 awaiting 时又出现。放不放行仍由服务端按用户
// 消息序号判，这里只管出不出现（主会话 11-11 按 A 线实际交付定）。

/** 有一份计划在等用户点头、CC 没在跑。 */
export function offersRecord(plans: ReadonlyMap<string, string>, running: boolean): boolean {
  if (running) return false;
  for (const status of plans.values()) if (status === "awaiting") return true;
  return false;
}
