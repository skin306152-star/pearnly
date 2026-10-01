# 计费台账索引（2026-10-01）

分支 `codex/billing-ledger-index`，基线 main 35f418a8。

## 原因

- 部署后 128 张复压的慢查询（修复窗口统计，01:50:40–01:55:10 UTC）：计费库 `select name from tabPearnly Tenant where
  name=… for update` 149 次、合计 28.3 秒、平均 190ms、最长 722ms，占这段时间慢查询的 74%。识别结果要等扣费完才推给页面。
- 生产计费库 `tabPearnly Credit Transaction` 只有主键和 creation 索引（`payments.usage` 补丁 2026-09-20 记为已执行，
  `payg_month` 索引却不在，推测计费库迁移 / 恢复时执行记录跟过来而索引没有）。扣费在租户锁里有两次带锁读：
  `billing.charge` 按参考号查是否扣过、`usage.pages_used` 算本月已用页数；没有索引就扫全表并锁住所有租户的台账行，
  所有租户的扣费互相排队。

## 改动

- `payments.usage.ensure_indexes()`（幂等）：`payg_month`（tenant, type, billing_mode, billing_month）与
  `tenant_reference`（tenant, reference）。`execute()` 改调它；Credit Transaction 控制器 `on_doctype_update` 也调它
  （每次同步 DocType 都补上，不只靠一次性补丁）；新补丁 `patches.v0_0_2_ledger_indexes`。扣费口径、阶梯价、金额都不动。

## 生产（用户 2026-10-01 同意「按你的建议走」）

- 已在生产计费库补上两个索引（billing-web 容器里 `frappe.db.add_index`，0.19 秒；没有进数据库容器），`analyze table`
  更新统计。余额回读不变（testcowork 1185.0）。
- 执行计划：查是否扣过走 `tenant_reference` 扫 1 行（原全表 1349 行）；其他租户算本月页数走 `payg_month` 扫 1 行；
  testcowork 10 月 1119 行占全表大部分，优化器仍选全表（压测租户的特例）。

## 后续

- 大客户每月几万页时 `pages_used` 每次扣费要锁住当月全部台账行，会越来越慢；可改成钱包上的本月计数（需先定对账口径）。
- 下一轮 Cloud Run 压测时看 Tenant FOR UPDATE 的等待是否下降。
