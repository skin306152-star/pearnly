# 开始前卡片：出题、记答案、判定、写账同一口径（11-17 回归 erp.cc.operator.intake.sales）

## 现象与根因
main 803f0da84 上，ERP 代录放销售票，开始卡第二题显示「其他 · revenue」，答了以后写入计划仍判 undecided_items、开不了始（847fc020c 上过）。
第 1 期在共用写账规则里加了只在引导口径才问的题（`express_rules.push.choices.resolve(guided=True)`：销项收入科目 A14、明细对不上 A7 等）。
引导口径由「此刻是否在 CC 对话里」（`assistant.guided.active()` 读 `frappe.local.pearnly_assistant`）决定：
- 出题 `express_operator.api.precheck` 带开始卡的任务号、进任务的对话 → 引导口径，出了收入科目题；
- 记答案 `express_operator.api.remember` 不收任务号、不进对话 → 网页口径重判没有这一题，`express.precheck.save` 查不到就**静默跳过**；
- 写入计划在对话工具里判 → 引导口径，仍要这一题 → undecided_items。
「其他」：对话页类名表没有 `account`（以及另外 7 类），落到「其他」。

## 逐入口表（四步：出题 / 记答案 / 开始前判定 / 写账）
| 入口 | 出题 | 记答案 | 开始前判定 | 写账 | 结论 |
|---|---|---|---|---|---|
| ERP 代录开始卡（含收单记录 I:） | 页面 precheck，带任务 → 对话 | 页面 remember，**不在对话** | 写入计划（工具）→ 对话 | 首交在写入计划（工具）里；**重交（继续 / 中途作答 / 收尾）在页面或后台、不在对话** | 不一致 → 本批修（remember 与重交都进任务对话） |
| Cowork 代录开始卡 | 同上（共用接口） | 同上；Cowork 记答案按类直接记、不重判，答案不丢 | 对话 | 重交同 ERP（共用 tasks） | 记答案无损；重交同批修 |
| 代录中途选择框（operator_choose） | 电脑回报的 needed（交单时准备，在首交 / 重交的口径里） | task_answer → 重交 | — | 重交 | 重交进对话后两步一致 |
| 直写对话写入计划 + 引导卡 pearnly_guide | 工具 → 对话 | 确认经 toolrun.decide → 进对话 | 工具 → 对话 | 工具 → 对话 | 一致 |
| 直写对话 pearnly_precheck_answer | 工具 | 工具 | 工具 | 工具 | 一致 |
| 网页待处理（pending） | 页面 check | 页面 answer | 页面 | 页面 / 每分钟重试 | 都是网页口径，一致 |
| LINE | 网页口径 | 网页口径 | 网页口径 | 网页口径 | 一致 |
| 定时任务 / 文件夹 | 后台 | — | 后台 | 后台 | 一致 |
| 收款引导运行卡（guide.run） | 产品函数，不走共用写账判定 | 同 | 同 | 同 | 不涉及 |
| ERP 直写在对话里推送、停下等电脑后由每分钟重试（push.poll）再推 | 对话 | 对话 | 对话 | **后台重试按网页口径重新准备** | 不一致风险：另报主会话（要在单据上记下「从哪段对话推的」，不在本批） |

## 本批改动
- `assistant.guided.within(session_id)`：这个范围里按这段对话做（同工具调用）；`express_operator.api.precheck / remember` 共用
  `_in_task_conversation`，`tasks._resubmit` 用 `guided.within(任务对话)`。开始卡记答案带上任务号（`operator-start.tsx`）。
- `express.precheck.save`：卡上的题这里重判没有时，整批拒、回「没有记下：<题>」，不再静默跳过（调用方不会当成功）。
- 对话页题类名：`account` 按分组叫费用 / 收入 / 存货科目；补齐 cash_account、unit、new_item、new_party、cost_basis、detail、
  operator_form 四语（沿用术语表 guide.q.* 的叫法）。
- 守门：`tests/test_cc_card_same_basis.py`（服务端每一类题四语都有名字；开始卡出题、记答案、重交都在对话里，新页面接口调判定 / 记答案
  必须收任务并进对话）。
- 流程覆盖：erp.cc.operator.intake / .sales 的 covers 加共用写账规则、引导开关、ERP 准备、开始卡；共用件 express-rules、assistant
  的入口加 `cc.operator.start-card`。漏跑原因：这两条流程原来只登记 4 个文件，第 1 期改的 choices.py / prepare.py / guided.py 不在里面。

## 先红后绿
- `tests/test_express_operator_code_cc.py::test_the_start_card_answers_are_saved_in_the_same_task_conversation`：改前 TypeError（remember 不收 task）。
- `tests/test_express_operator_tasks.py`（重交在任务对话里）：改前 1 failure，改后过。
- `tests/test_erp_write_precheck.py::test_an_answer_to_a_question_this_judgment_does_not_ask_is_refused_not_skipped`：改前不拒。
- `tests/test_cc_card_same_basis.py`：旧类名表上 1 failure。
- 流程 erp.cc.operator.intake.sales：发现窗口 local_3709c091 复验（红证据在共享笔记「销售放票undecided-证据-11-17/」）。
