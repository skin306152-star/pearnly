# 代录交单遇到「等电脑」挂起排队（A 写账主线）

状态：本次范围完成，待合入。分支 `pearnly/cc-operator-wait-masters`，基线 origin/main 81ec70ae，工作树 `.claude/worktrees/a-line-wait`。
来源：线上 11-10 testcowork 代录任务 t4n5b25940（只读调查，主会话派）。

## 现象与根因

用户点「开始」时，这份账套的科目表还没被电脑读到、也没有可用电脑。Cowork 交单回 `waiting_for_cc`（原因 reading），共用层
`tasks.attach` 只认已交 / 排队，其余一律记「未完成」，任务当场收尾成「需要你处理」；之后没有任何东西再交这张单（没推出去，产品
查状态时还会说「已取消」）。ERP 同一处：写账准备停在「过一会儿自己会好」的原因（`RETRY_REASONS`），任务同样记未完成；而 ERP
每分钟那一轮（`push.poll`）会把这张归代录的单**当直写推出去**（没有写账件，`operator_owned` 认不出它归代录）——串了模式。

## 修法

- 共用层 `express_operator.tasks`：`WAITING_FOR_CC` 交单回执 → 行记「排队」、原因 `waiting_for_cc`，任务等着；`_conclude` 先把这些行
  再交一次（交上转普通排队、还等继续等、别的停下记未完成）；`waiting_documents()` / `wake_waiting()` 叫醒任务（排 `feed.refresh`）。
  `feed`：这类行的步骤进度标签 `waiting_for_cc`，前端 `op.reason.waiting_for_cc`（四语，复用已有「等待 CC 桌面版」说法）。
- Cowork：电脑报来账套主档（`connections/collector`）后叫醒。交单本身不变（`_push_one` 原来就回 `waiting_for_cc`）。
- ERP：`express/operator.submit` 把 held + `RETRY_REASONS` 交回 `waiting_for_cc`；`push.poll` 跳过 `operator.waiting_targets()`（不按直写
  推），每轮末尾叫醒。

开始卡把预检里「等待」的单也交，这本身不变（主会话 11-10：交上去会排队等，不判失败）。

## 验证

| 版本 | 场景 | 结果 |
|---|---|---|
| 旧代码 | test_express_operator_tasks（等电脑挂起、再交、交上后收尾）、test_erp_express_direct（submit 映射、poll 不按直写推）、test_express_operator_feed（步骤标签）、test_cc_operator（前端说法） | 红 |
| fb426b9d7 | 上述 + 受影响单测 | 过 |

| 0b985d0bc | gate（development.yml 全部步骤） | 214 步全过 |
| 0b985d0bc | 候选离线（生产镜像 fb1037ad） | 107 组，LANE EXIT 0 |
| 0b985d0bc | 受影响流程（parity-lab，`--changed`） | 7 条流程 × 两套数据，20/20 过 |
