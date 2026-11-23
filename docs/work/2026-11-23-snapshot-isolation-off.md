# MariaDB 关快照隔离（并发取号 1020，11-23）

- 分支 `pearnly/snapshot-isolation-off`，基线 `origin/main` = `7d935d206`。评估全文在共享笔记「2026-11-23-并发取号1020-评估.md」。
- 主会话 11-23 定：按配置层施工；先补机器检查；infrastructure 里所有 MariaDB 加参数；生产**不做** `SET GLOBAL`，随部署重建生效。

## 现象与根因

- 回归实验栈：30 张 LINE 卡片同一瞬间「保存」，29 张建采购单取号（`tabSeries`）报 1020「Record has changed since last read」。
- 线上（主会话只读查询，11-23）：全部站点 `innodb_snapshot_isolation=1`、REPEATABLE-READ。近 14 天类死锁报错：
  - erpcowork 7 条（1213 ×2、1020 ×2 等）；ris 2 条；testcowork 13 条；testerp 8 条。
  - 其中 `assistant.titles.summarize` 15 条都在 11-10 16:41 / 16:44：11-09 `ef1e718d3`（标题任务先结束读快照再加锁、撞上整笔重来）已修，
    11-10 晚随 `release-20261110-1259` 上线，此后 0 条。同类，关快照隔离后这一路也不会再出。
- 根因：MariaDB 11.8 默认 `innodb_snapshot_isolation=ON`。事务读过之后（建立了读视图），再加锁读 / 改到别人在这之后提交过的行就报 1020。
  Frappe 16.33 把它当死锁抛 `QueryDeadlockError`，取号（`naming.getseries`：`FOR UPDATE` 再 `UPDATE`）没有重试；
  后台任务的自动重试只接 `InternalError`，接不到包装后的 `QueryDeadlockError`（读源码）。

ON / OFF 实测（独立临时 MariaDB 11.8.9，两个连接，A 先读一次）：

| 情形 | ON | OFF |
|---|---|---|
| 取号：B 先取号提交，A 加锁取号 | 1020 | 成功，读到最新号 |
| 取号：B 持锁，A 排队等到锁 | 1020 | 成功，读到最新号 |
| 按条件批量改（同 User.on_trash），B 只改了扫描范围里不相关的一行 | 1020 | 成功 |
| 没加锁读出、再写回算好的值 | 1020 | 写成，覆盖别人刚写的（丢更新） |

## 改动

1. **所有 MariaDB 加 `--innodb-snapshot-isolation=OFF`**：生产 `infrastructure/production/compose.yml`；本地底座 `00-base.yml`；
   回归实验室 `regression-lab.yml`；`unified-release-lab.yml`（候选车道的 dms-flow / dms-snapshot 实验室继承它）；`erp-acceptance.yml`（自己覆盖了
   command）；`cowork-backend-lab`、`dms-line-lab`、`erp-pos-lab`、`erp-pos-provision-lab`、`auth-preview`、`line-login-lab`。
   maclab、testenv、stocktake-acceptance 继承生产那份 command，不用另加。`tests/test_mariadb_snapshot_isolation.py` 守：新加的 MariaDB
   没带这个参数（或自己写了 command 却漏了）就失败。生产不做 `SET GLOBAL`，下次部署重建 mariadb 容器时生效。
2. **机器检查 `scripts/check-unlocked-overwrite.py`**：同一个函数里，没加锁读出某张表，再把由读出的值算出来的结果写回**同一张表**
   （`set_value` / `db_set` / 带参数的 `UPDATE`）。没标注的就失败；确认「后写的为准」是本意的在那一行标 `# overwrite-ok: 原因`。
   排除 patches、tests；`doc.save()` 不算（Frappe 保存前用加锁读比对修改时间）。启发式，跨函数传值、动态表名靠评审。
3. **逐处确认**（全仓 22 处）：
   - 改成加锁读（7 个函数）：
     - `erp_portal/inventory/count._refresh_totals`：两人同时录同一份盘点，各自按旧快照的盘点数重算差异会互相覆盖 → 行加锁读当前值。
     - `cowork/history/push_state.withdraw_automation`：原因码在原值上追加 → 加锁读。
     - `cowork/assistant/into_document.merge`：原件的绑定列表在原值上替换 → 加锁读。
     - `erp_portal/express/operator.apply_intake_answers` / `settle_payment`：收单记录的选择 JSON 在原值上改 → 加锁读（写回前重读）。
     - `cowork` / `erp_portal` 的 `work/service.move_list / sort_list / move` 与列尾位置：排到 DocType 元数据行锁（互斥）之后
       的位置读原来是普通读，拿的是事务开头的快照，看不到前一个人刚提交的位置 → 一律加锁读。
   - 登记确认（15 处，`scripts/unlocked-overwrite-reviewed.tsv`，不为登记去改业务文件）：值来自外部（身份服务、MR.ERP、Express 读回、
     邮件队列）、按现算整值重写且幂等（税额、日汇总、对账状态）、已经用带条件的 UPDATE 抢到（工具执行记录）、单一执行者（导出任务）、
     迁移（计费流水分类）。代码改了对不上的条目报「登记过期」。
   - 钱的路径（钱包、套餐扣费）本来就是先加锁读再算，OFF 下读的是最新已提交值，不变。
4. **关掉之后露出来的下一处：`erp_portal/products/line_memory.learn`**（实验栈 30 张同一瞬间保存，关掉后 29 张撞
   `ERP Line Item Memory` 主键 1062）：同一对方同一品名的几张单都看到「还没有」、都去插同一个名字。改成先插、撞重复回到保存点改更新
   （插入等前一个提交后才报重复，此时那一行已在）。`tests/test_erp_line_item_choice.py` 加一条。
5. 已有的 68 处 1020 / 1213 捕获与 46 处 `transaction.conflicts.retried` 照旧保留，接真正的死锁（1213）；不再新加各入口自己的重试。

## 验证（分支提交，本机，11-23）

- 实验室（回归实验栈 erp-new，同一份代码只切开关；脚本 `~/pearnly-devtools/snapiso-1123/`）：

| 场景 | ON | OFF |
|---|---|---|
| conc30：30 张 LINE 卡片同一瞬间保存（N=30，STAGGER=0） | 1 成 / 29 张 1020（`tabSeries`） | 30/30，Error Log 0，写账任务 30 张排上 |
| 并发删 12 个用户（User.on_trash），另一线程同时改别人的 ToDo | 12 张全 1020（`tabToDo`） | 12/12 |

  第一次 OFF（还没修 `line_memory.learn`）是 1 成 / 29 张 1062 撞 `ERP Line Item Memory` 主键，修后得上表。
- 闸门（`gate.sh`）289 项通过、0 失败；`check-unlocked-overwrite.py` 通过；架构检查通过。
- 候选离线（生产镜像 `fdf20d98`，挂本分支源码）：只有 `scripts/test-dms-line-runtime.py` 的
  `test_second_file_uses_updated_form_and_retry_does_not_duplicate` 一条失败；同一镜像挂 `origin/main` `7d935d206` 同样失败（3 次稳定），
  与本分支无关（本分支没改 DMS）。
- 受影响流程（实验栈 OFF，挂本分支，`--jobs 3`）：29/30 通过。`erp.cc.operator.intake.new-item [new / desktop]` 不通过
  （开始卡没问到放进来的那张）；同一实验栈挂 `7d935d206` 的产品代码，ON、OFF 都同样不通过 → 原有问题，不是本分支带来的。

## 影响清单（`check-impact.py`）

| 入口 | 结果 |
|---|---|
| ERP / Cowork 看板（`erp.work.board`、`cowork.work.board`） | 通过：流程 4 格 × 2 产品 |
| ERP 盘点（`erp.stocktake.new`、`erp.stocktake.scan`、`erp.cc.stocktake`） | 通过：流程 `erp.stocktake.new` / `scan` 各 4 格；离线 `test_erp_cc_stocktake` 等 |
| ERP LINE 卡片保存 / 物料行记忆（`erp.line.card-lines`、conc30） | 通过：流程 4 格；conc30 30/30 |
| 代录收单（`erp.cc.operator.intake`、`.sales`、`cc.operator`、`cc.sidebar-modes`） | 通过；`.new-item` 见上（原有问题） |
| Cowork CC 补页并单（`cowork.cc.troubleshooting`） | 离线：`test_cowork_into_document` 带加锁断言；没测到真引擎（实验栈假智能体不调工具） |
| Cowork 自动推撤回（`withdraw_automation`） | 离线：`test_cowork_da_tiers` 带加锁断言 |
| 生产 MariaDB | 没测到：生产不做 `SET GLOBAL`，下次部署重建 mariadb 容器生效；上线后用 `~/pearnly-devtools/snapiso-1123/snapiso-ro.sh` 回读 |
