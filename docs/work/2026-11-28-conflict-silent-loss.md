# B ③ 关快照隔离后的静默丢更新与管钱底线（11-28）

- 分支 `pearnly/conflict-b3-silent-loss`，基线 `origin/pearnly/conflict-retry-common` = fdd454b77（B ①，列车合进 main 后变基到 main）。
- 施工清单（主会话 11-28 审定「可以」）：共享笔记「并发冲突-③施工清单.md」；全仓闸门命中清单：「并发冲突-③闸门命中清单.md」。
- 前因：[B ①](2026-11-26-conflict-retry-common.md)；线上 MariaDB 11-26 起 `innodb_snapshot_isolation=OFF`。

## 判据（统一一套）

关隔离后只有**加锁读**拿最新值，普通读停在事务第一次读时的读视图。`doc.save()` 有 Frappe 保存前比对兜底（报 417，B ① 起整笔重来），不算静默。
静默的是：`set_value` / `db_set` / SQL `UPDATE`、插入、删除，或「要不要写」的判断，用了普通读出来的旧值。改法：

- 函数在自己的事务边界上：先加锁再做任何普通读，或先 `commit` 结束读视图；
- 函数在调用方的事务里被调（钩子、内部组合）：写回要用的那几列直接加锁读（`get_value(..., for_update=True)` / `SELECT … FOR UPDATE`）。
  不用 `get_doc(for_update=True)` 整份重读（11-26 实测会出 LinkValidationError、多锁子表）。

## 改了什么（9 处 + 闸门）

| # | 位置 | 原来（原文核过） | 现在 |
|---|---|---|---|
| 1 | Cowork `companies/automation.on_readback` / `on_mismatch`（自动推送档位） | 先普通读旧档位算出 tier，`_set` 再锁行写回：与「核不上降 L0」并发会把 L0 撤销 | `state(company, lock=True)` 加锁读档位行再算；`_set` 查行号也加锁读（别人刚建的行看得到） |
| 1b | Cowork `push/automation/service.mismatch`、`readback/service._announce_ready`（两条钩子的保存点） | `except Exception` 一律回到保存点、记日志：数据库冲突被吞掉 = 这次没降 L0 | `conflicts.is_conflict` 的往外抛，交外层整笔重来（同 B ① 保存点类） |
| 2 | Core `express_operator/listed.remember` | `tasks._own` 锁行名、普通 `get_doc` 取快照，合并后 `set_value` 写回：丢另一路刚记的单 | 快照与公司、状态三列加锁读；新增 `tasks._own_name`（`_own` 不变） |
| 3 | Cowork / ERP `work/service.move`、`move_list`（看板位置） | 不是丢更新（邻卡位置是加锁读，卡片 `save` 有比对）；同一空隙同时拖卡算出同一个位置（原来就有） | 拖卡在目标列表行上加锁排队；`_past_taken` 加锁读出两边之间别人刚放进的，落到它们后面 |
| 4 | 控制面 `onboarding/welcome.queue_for_request`（欢迎信） | 加锁 SELECT 只取 name，普通 `get_doc` 判「排过没有」：会再排一封带初始密码的信 | 加锁的那条 SELECT 直接带出 `welcome_email_queue` 判断 |
| 5 | 控制面 `billing_api.service_charge` + `payments/transactions.retry_service`（扣费） | 入口的空提交在重试包装里，一提交就标「已提交」，之后撞 1213 不重试 → 产品侧只记「没记上」，ERP 不补扣 = 漏收费；且只认死锁 | 空提交挪到包装外；`retry_service` 按 `conflicts.is_conflict` 判（含保存前比对）；用尽整笔回滚、记一条带 reference 的 `billing charge conflict exhausted`（补扣核对用）再抛 |
| 7 | ERP 销售单 `request_id`；采购 / 销售 `entry.post` 查重 | 销售没有唯一索引；拿到 Redis 锁后普通读查重，看不到另一路刚提交的同号单 → 两张同号 | 销售 `request_id` 唯一（DocType + `pre_model_sync` 补丁 `v1_34_sales_request_id_unique`：空串改 NULL，万一重号后来的改「原号#单号」并记日志，不删单）；查重改加锁读，权限按加锁读到的公司 / 制单人判（不再普通 `get_doc`） |
| 8 | Cowork `history/push_state.withdrawn`（W6） | 普通读「还指着这条」再清空：会清掉别人刚重推的新记录状态 | 加锁读（同文件 `withdraw_automation`） |
| 9 | Cowork `history/removal._drop_related`（W5） | `_pushing` 加锁读出记录，删时又普通 `get_all` 找一遍：读视图之后新建的删不到，留孤儿 | 用 `_pushing` 加锁读出的名单删 |
| 10 | 闸门 `scripts/check-unlocked-overwrite.py` | 只认同一函数里「同表普通读 → 写回」 | 加三类：借函数读、跨函数传值、判断用旧值（见脚本说明）；按 import 解析别的模块的表名常量；只当主键用的名字不算值；扫描 13 秒 |

第 6 条（线上 10 月 98 条扣费冲突是否漏扣）：只读脚本 `~/pearnly-devtools/prod-ro-1128-billing/`，主会话审后 11-28 跑（一次 SSH，exit 0），输出 `check.out`。

### 第 6 条结果（11-28 生产只读，事实与盲区分开写）

事实（原文核过）：
- billing.internal 扣费相关冲突 98 条，**全部在 2026-10-01**，分三阵：14:41（28 条）、16:26（29 条）、21:27（41 条）。
- 全是开着快照隔离时的 1020（不是 1213）：77 条在 `tabPearnly Wallet`（`wallet_of` 的 `SELECT … FOR UPDATE`），21 条在 `tabSeries`。
- 98 条里能摘到的 reference：97 条 `cowork.invoice:*`、1 条 `cowork.recon_document:*`，共 96 个不同 reference。分属 3 个租户：T-2026-0287（83 条）、T-2026-0299（10 条）、T-2026-0303（5 条）。没有 ERP、DMS 的 reference。
- 台账 9 月起每天的 usage 行里，**10-01 一行也没有**（9-30 有 Cowork 3、ERP 2，下一条是 10-03 的 DMS）。
- ERP 7 站「ERP OCR charge pending」0 条；Cowork 3 站已存结果、未扣上的识别单 0 张；DMS 2 站扣费相关 Error Log 0 条。所以要回台账核的产品侧 reference 为 0。

一、10-01 是谁在跑、当时有没有被重试接住：
- 时间与形状对得上 10-01 的生产压测：`billing.py` 的 `_store_wallet` 注释写着「2026-10-01 三租户半量压测：一家 256 张发票」；当天 3 个租户、三阵集中扣费、全部是 Cowork 发票识别。
- **当时没有被计费服务的重试接住**：
  - 每条都进了 Error Log，说明那次请求是失败返回的。
  - 原因正是第 5 条：`service_charge` 开头的空提交把「已提交」标上，`retry_service` 撞 1020 / 1213 都直接抛出，不重试。
  - 产品侧 Cowork 会把这张标 `billing_pending`，稍后用同一 reference 再扣。
- 这 96 个 reference 后来有没有被 Cowork 补扣上，**从现有数据核不出来**：
  - 台账里 10-01 没有任何 usage 行；
  - 现存 3 个 Cowork 站点里也没有未扣的单；
  - 这 3 个租户很可能是压测用后已拆掉的站点。
  - 这批 reference 本身没回台账查过：脚本只查了产品侧报来的 reference，这一项要下次连生产时补。

二、「0」的盲区：
- billing.internal 上 10-01 的 Error Log 到今天（58 天）还在，说明这个站点 10-01 以后的 Error Log 没被清过。计费侧「请求打到计费服务、失败返回」这一类，10-01 起是完整的：ERP、DMS 一条也没有。
- ERP / DMS / Cowork 各产品站的 Error Log 保留期**没有读**。Frappe 默认按 Log Settings 清旧 Error Log，默认 30 天，没核实各站实际值。
  - 所以：**ERP 侧 10 月产品日志里「没记上」的 0，无法从日志证明**——10-29 以前的可能已被清掉。
  - 只能旁证：计费侧 Error Log 没有 ERP reference；台账 10 月 ERP 只有 10-27 的 2 行，用量本身很少。
- 计费服务连不上的失败（网络、超时）不会进计费侧日志，只在产品站日志里，同样受保留期限制。
- 结论：**不能写成已证实无漏扣**。能确认的只有：10-01 那 98 次扣费在计费侧失败、没被重试，补扣与否无法核实；计费侧 10-01 起没有别的扣费冲突。

## 验证

### 本机实验栈两连接实证（maclab `pearnly-b3`，MariaDB 11.8.9，`innodb_snapshot_isolation=0`、REPEATABLE-READ）

脚本 `scripts/conflict-silent-loss-lab-verify.py`：本连接先建读视图，另一个线程 / 连接按产品代码改完提交，本连接再走同一个函数。

| 格 | 修前（挂 fdd454b77 源码，10 轮） | 修后（挂本分支，10 轮 × 4 遍） |
|---|---|---|
| ERP 销售同一 request_id | 0/10（每轮建出 2 张） | 40/40 |
| 代录资料清单两路同时记 | 0/10（只剩 B，A 丢了） | 40/40 |
| ERP 看板同一空隙同时拖 | 0/10（两张都是 1536） | 40/40 |
| 扣费撞一次冲突 | 0/10（抛错、台账 0 行 = 漏扣） | 40/40（重试、只 1 行、余额只减一次） |
| 扣费一直冲突用尽 | 0/10（没留记录） | 40/40（没扣、余额不变、1 条带 reference 的记录） |
| 用尽后同一 reference 补扣 | 10/10 | 40/40（只 1 行） |
| 欢迎信两路同时排 | 0/10（排了 2 封） | 40/40 |
| Cowork 看板同一空隙同时拖 | 0/10（两张都是 1536） | 30/30 |
| Cowork 档位：回读与核不上同时到 | 0/10（最后是 L2，降 L0 被撤销） | 30/30（L0） |
| Cowork 撤回旧推送、另一路刚重推 | 0/10（新记录的状态被清空） | 30/30 |
| Cowork 删票、另一路刚记了推送记录 | 0/10（留下 1 条孤儿） | 30/30 |

### 离线

- `tests/test_conflict_silent_loss.py` 10 项：修前源码 6 项不过（扣费 4、查重、补丁登记），修后全过。
- `tests/test_check_unlocked_overwrite.py` 新增 `Extended` 7 项（三类正反例）。
- 闸门在修前源码上命中 292 处、修后 285 处；③ 修的档位、资料清单、撤回推送、删票四处修前都在、修后消失。
- 替身跟上：`tests/test_welcome_email.py`（加锁 SELECT 带出排队号）、`tests/test_express_operator_tasks.py`（按名字取）。

### 共用件影响清单（`check-impact.py --base fdd454b77`）

命中的共用件：无（`transaction` 本身没改）；新调用方登记进 `shared.conflict-catch`。要实测的入口与结果见下方「影响清单实测」。

## 待主会话定 / 已知

- 闸门 285 处存量的处置（逐处修还是登记基线），已发主会话。
- 不在 ③：`tasks._own` 根函数与其余代录入口（②/④）；ERP `post` 拿锁后其余普通读（与隔离开关无关，② E7/E9 一起定）。
