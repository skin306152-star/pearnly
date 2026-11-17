# B04 代录任务失效（2026-11-17）

分支 `pearnly/operator-task-expiry`，基线 origin/main `803f0da84`。来源：11-14 CC 前端欠账总表 B04（主会话 11-17 派，「可以做」）；
规则出自 docs/work/2026-11-09-cc-misuse-graceful-design.md 2.3 与用户 11-13「卡片有效期照 DMS」。旧分支 `pearnly/cc-card-expiry`
（ff592e52e / caf75dc65）只当参考，按现在的代码重做，没合。

## 规则
代录任务还有没处理的单，且 7 天没有任何动作；或同一公司后来开的代录任务已包含它没处理的票 → 已失效。收尾了的（完成 / 停止）不失效。
判定在读的时候现算（`express_operator.tasks.expired`），不加定时任务、不改 `status`（开始前那张卡只认 `preparing`，guided-erp 11-17 确认）。

## 改动
- `tasks.read` 回 `expired`；作答 / 一次作答 / 继续遇已失效回 `{…, "expired": true}`，不交单（状态，不是报错）。
- 交单（`api.hand_in` → `start`，写入计划的「写入」走这条）遇已失效报「This operator task has expired. Start a new one.」（新词条四语），
  一张都不交；写入计划本身不改（guided-erp 的文件）。
- 卡片执行记录：`contracts.TOOL_RUN_STATES` 加 `expired`；读对话事件时（`sessions._with_tool_runs`）这段对话挂着的代录任务已失效，
  还在等的卡片按 `expired` 给页面（`tasks.expired_sessions`，只读现算，不写库）。

## 合入顺序（必须）
现在 main 上的前端不认识 `expired`，会把卡片当成「已允许 / 已答」。前端在 `pearnly/cc-guided-ui-review`（guided-frontend，
3d4684c25 起）已认 `expired`（一行「已失效」，词条 guide.expired），并让不认识的状态一律收起。**本分支要等那条进 main 后再合，或同一批合。**

## 文案
新增 1 条（pearnly_core「This operator task has expired. Start a new one.」/ 此实操任务已失效，请新建一个。），标黄 1 处 R-SENT（两句），无拦截。

## 验证
单测：test_express_operator_tasks（7 天失效且卡片不交单、新任务含同票使旧任务失效、已完成不失效、按对话列出已失效）、
test_express_operator_code_cc（已失效任务交单被拒、一张不交）、test_assistant（等待中的卡片在任务失效时读成 expired，未失效照旧）。
全闸门、候选离线、流程见主会话汇报。
