# ERP 写账准备并发：重复写账与字段被盖（管钱缺陷，2026-11-16）

分支 `pearnly/erp-push-send-lock`（基于 origin/main da962350b）。主会话 11-16 定为管钱缺陷、优先排车，修法审定「可以做」。

## 现象与根因

验收首跑：收银日汇总 PDS-2026-00798 写成了 HS691116-001，单据上 `ledger_book` 却被盖空、已答过的「是不是增值税登记户」又挂回 `ledger_needed`。
`express.push._send` 开头无锁读单据、判「还没排件」，再做耗时的准备（主档落地途中会提交，「不带着事务等」），最后写回 / 排件。两次准备交错时：

1. 晚落库的那次把答题后写下的账套、要问的项盖回旧的「停下」（写账件照样写成，回执只改 `ledger_state`）；
2. 请求号按 `#(此刻件数+1)` 现算：先排件的提交后，后到的数到 1 件、编出 `#2`，唯一索引挡不住，**排出第二件、同一张单写两次账**。

并发入口：提交后的后台 `queue`、每分钟 `push.poll`、答题后 `express.api.answer`、收银日汇总合成后的同步交、代录交单、对话直写、待处理重推、改票面合计后重推。
采购 / 销售 / 出入库 / 盘点 / 收银日汇总都经 `_send`。线上发布标记 release-20261115-1126 同样写法（按件数编号自 10-23 f77e8096b 起）。
生产与实验栈都是 MariaDB 11.8（快照隔离默认开）：同一事务里读过这行再晚写回会被数据库拦下（1020），但准备途中一提交就不再护着，所以线上仍会发生。

## 修法（`express/push.py`）

- 开头行锁读（`frappe.db.get_value(..., for_update=True)`，原生 SELECT … FOR UPDATE）：同一张单一次只进一次准备，poll、答题、后台、代录都过这把锁。
- 写回前再核：准备完后锁内重读账本栏（`ledger_state / ledger_job / ledger_reason / ledger_book / ledger_needed`），与开始时不一样就放弃这次结论（回滚），
  按单据此刻的样子回 `already_queued` / `held`，以后到的为准。
- 排件前锁内再核：这张单已有没撤销的写账件（作废件除外）就不排、接回它。所有清掉 `ledger_job` 重交的路径（再写一次、改选对方档、代录再录、作废）都先撤旧件，
  所以一张单同时最多一件没撤销的写账件。请求号编号算法不变（锁内、已确认没有未撤销件时，件数只在显式撤旧件再交时增加），与线上已排的件兼容。
- 不在电脑端按单号挡重复（用户口径：推送不查重不拦，要再写一次就照写）。

## 按类核对

- 收款（`collection/push.send`）：请求号按记录号 + 退回次数固定，不按件数现算，并发撞唯一索引，不会排出第二件。
- ERP 的 MR.ERP 推送（`mrerp/push`）：认领时行锁（`_claim` for_update）+ 导入中标记 + 后台任务按单去重，已防。
- Cowork 推送（`push/application/service.prepare`）：开头锁识别记录（for_update），核调用方带来的记录版本号，按调用方请求号预留推送记录（同号回原记录），已防。

## 证据（本机实验栈 pearnly-lab-co8，镜像 = 线上 02eebaa4，MariaDB 11.8.9）

流程 `erp.express.send-concurrency`（两条线程各自连库；慢线程准备途中照主档落地提交一次再睡 3 秒）：
- 修前（push.py 退回 main）：场景一（poll 与答题同时）单据被盖回 `held / choice_needed:account`、要问的项挂回，而写账件已排上；
  场景二（两次交单交错）排出两件：`erp:purchase:PUR-…` 与 `…#2`，都 queued。
- 修后：两个场景都只有一件写账件，单据 `queued`、不再挂要问的项、`ledger_job` 指向那一件；慢的那次回 `already_queued`。
- 结果文件：主会话共享笔记「写账并发-流程证据-11-16」。

单测：`tests/test_erp_express_direct.py::test_two_preparations_of_one_document_never_queue_a_second_job_or_write_back_a_stale_stop`
（开头行锁读；锁里见没撤销的件就接回不排；准备期间账本栏变了就放弃、不写回；撤旧件后再交编 #2）。

## 线上存量

生产只读统计脚本 `~/pearnly-devtools/prod-ro-1116/dup-jobs-check.{sh,py}`（主会话审后排执行）：列各站点同一张单下没撤销的写账件 ≥2 件的单。
