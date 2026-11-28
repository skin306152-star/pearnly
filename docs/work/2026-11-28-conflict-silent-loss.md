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
| 1b | Cowork `push/automation/service.mismatch`（写入核不上降 L0 的钩子保存点） | `except Exception` 一律回到保存点、记日志：数据库冲突被吞掉 = 这次没降 L0 | `conflicts.is_conflict` 的往外抛，交回执的整笔重来（同 B ① 保存点类）。回读钩子 `_announce_ready`（只管升档）照旧吞掉：引导式回读不重来、保存前比对会被当成失败原文回给用户（code-review 11-28），下一次回读再评 |
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

补查（11-28 晚，第二次生产只读 `~/pearnly-devtools/prod-ro-1128-c1/`，主会话跑）：这 96 个 reference 在计费台账里**任何 type 的行都没有**（有 usage 的 0）。
- 结论改为：**已证实漏扣 96 笔**——10-01 压测那 98 次扣费在计费侧失败、没被重试，之后也没有补扣。
- 租户：T-2026-0287 81 笔（testcowork，我们自己的测试租户，现在仍在用）；T-2026-0299 10 笔、T-2026-0303 5 笔（10-01 用户同意临时建的压测租户 testcowork2 / testcowork3，现已拆）。三个都不是真实客户（依据：`docs/work/2026-10-01-cowork-direct-upload.md`、`2026-10-03-release-batch-1003.md`、`2026-09-29-cowork-recon-scanned-docs.md`）。
- 原因已在第 5 条修掉（③a：空提交挪到重试外、冲突按 `is_conflict` 重来、用尽留记录）。

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

### code-review（medium，范围 fdd454b77...HEAD）

| 发现 | 真问题 | 修了 | 耗时 |
|---|---|---|---|
| 6 | 3（闸门缓存按 id 跨扫描不清、回读钩子往外抛冲突会让引导式回读失败、重复请求路径少了角色权限检查） | 3 修；另 3 条不改：用尽日志在「已提交后冲突」时误记（扣费里提交是最后一句，现无此路径）；镜像卡拖动同空隙重号（原有问题，记已知）；拖卡先锁列表再锁卡的锁序（死锁由 conflicts.attempt 重来） | 约 15 分钟 |

## 第二段：作废入口（分支 pearnly/conflict-b3-void-entries，基于第一段；主会话 11-28 选 A）

起因：第一段给 `express.push.void` 改了加锁读，主会话要一条流程级并发测试。流程 `erp.express.void-while-sending` 在只有第一段的源码上 9/9 红：后台 `push.send` 锁着单据排写账件，作废等锁，拿到锁后用读视图里的旧副本 `doc.save()`。后台写 `ledger_job` 用的是不改修改时间的 `set_value`，保存前比对拦不住，保存把它盖回空；撤件时读到的是自己刚盖回的空值，留下一件排队中的写账件。电脑领走就把已作废的单写进 Express。

### 三层改法

| 层 | 改了什么 | 管哪些路径 |
|---|---|---|
| 撤件不变式 | `push.void` → `_cancel_unwritten`：按（来源 DocType、单号）在写账件表里加锁找出全部排队中 / 转人工的件，一律撤掉（「照 Express 原生作废原单」那件除外），不只认单据上的 `ledger_job` | 所有调作废的路径：页面、旧接口、采购「修改」作废原单、内部调用 |
| 入口先结束读视图 | `api_cancel_purchase`、`api_void_purchase`、`api_cancel_sale`、`api_void_stock_entry` 先 `frappe.db.commit()` 再 `conflicts.attempt`；`purchasing.access.get_document`、`sales.access.get_document`、`purchasing.service.set_status` 改成先 `FOR UPDATE` 再 `guard` / `get_doc`（出入库 `void_entry` 原本就先锁） | 页面上的四个作废入口 |
| 保存不盖回账本栏 | 新模块 `express.ledger_guard.keep_current`：采购 / 销售 / 出入库 / 盘点 / 收银日汇总五种单据的 `validate` 第一句，用加锁读把库里 `ledger_*` 现值放回文档对象；已提交的单的 `printed_total` 同理（只经 `printed.set_total` 改）。核过没有代码经文档对象改账本栏 | 所有整份保存这五种单据的路径（付款、附件、作废、修改、内部） |

另：领件兜底 `push.still_wanted` 改加锁读（领单那笔事务前面已有普通读）；`_send` 锁内复核的栏加上 `status`。

### 主会话问的几点

- **反向时序**（作废先完成、push.send 随后才排件）：
  - `_send` 开头就加锁读单据行，等作废提交后读到已作废，回 not_ready、不排件。
  - 准备途中提交过（主档落地）、放了锁时：写回前锁内复核（`_ledger_seen`）。作废会清账本栏，现在又加上了 `status`，不一致就放弃。
  - 领件那一刻（`cc_desktop.jobs._claim` → 产品 `still_wanted`）：单据已作废就把件转「已撤销」、不交电脑。原来是普通读，关隔离后看不到读视图之后提交的作废，现在改加锁读，是最后一道闸。
  - Cowork 推送：领件 `push.native.service.lease` 对推送记录加锁读、必须是待推；撤回、删票都锁这条记录，不变式成立。
- **LINE / CC 调作废的路径**：逐个核过，没有「先锁卡片再调单据作废」的入口。LINE 卡片的「取消」只取消卡片本身，不作废单据。别的事务里调作废的只有采购「修改」（提交事务里作废原单），入口层的 commit 管不到它，由撤件不变式和保存守卫兜住；流程有 `amend` 一格。
- **11-26 第一版的两个坑**在作废入口上专门验（实验栈格 `erp.void_with_children`：4 张单同时到作废 + 补开收据 + 付款）：作废那一路 10 + 3 轮不出 LinkValidationError，也不漏出死锁或保存前比对。另两路的结果只作记录：付款入口还会漏出 1213（属 ② 的 E1）；补开收据在实验栈出过一次 PDF 样式解析错（`xml.dom.SyntaxErr`），待查是不是实验栈环境问题。

### 同类排查：静默写（不改修改时间）× 旧副本整份保存

| 表 | 静默写的栏 | 整份保存的路径 | 判 | 处置 |
|---|---|---|---|---|
| 采购 / 销售 / 出入库 / 盘点 / 收银日汇总 | `ledger_*`（写账件、Express 单号、状态、原因、要问的项） | 付款、附件、作废、修改、过账等 | 真，管钱（盖空单号会重复写进 Express、盖掉件号会留下没撤的件） | 本段修：`ledger_guard` |
| 采购 / 销售 | `printed_total`（`printed.set_total`） | 同上（已提交的单） | 真，管钱 | 本段修：`ledger_guard` 对已提交的单 |
| ERP 物料 | `express_code` / `express_company`（写账回执认领码） | `serials.turn_on / turn_off`；主档同步 `masters._upsert` 正当地经保存改码 | 真（码被盖回会让下一张单按旧码或新建档写），不能一刀拦（有正当的保存路径改它） | 归 ④（写账回执与主档同步一起改） |
| ERP 公司 | `vat_registered`、`express_sync_state / note` | `cc_service._reactivate / _rename`；公司表单 | 真（增值税登记户被盖回会按错的税写），表单正当地改它 | 随各自改动；改到公司保存时一并核 |
| 充值申请 | 远端同步整行（`billing_remote`） | 审批 `approve_local / reject_local` 是加锁整份读再存 | 不涉及：审批读的是最新值；远端同步只镜像另一控制面的申请，不与本站审批并发 | — |
| 其余（Cowork 原件 `binding_records`、DMS 端点 `last_status`、推送日志删除标记、User `default_workspace`、MR.ERP 连接目录缓存） | — | — | 不管钱 | 进存量清单，随各自改动 |

### 验证（本机实验栈 maclab `pearnly-b3`）

| 格 | 修前（只有第一段的源码） | 修后 |
|---|---|---|
| 流程 `erp.express.void-while-sending`：采购、销售、修改作废原单 | 9/9 红（每张都留一件排队中的写账件） | 18/18（起始状态改成「停在要人选」之后连跑 3 遍） |
| 同上加库存单一格（入库单作废，`api_void_stock_entry`） | 12/12 红（四类每张都留一件没撤销的件） | 25/25 × 3 遍（含仓库前置判定） |
| `erp.stale_save_keeps_ledger`（回执记下单号、改了票面合计后，旧副本付款保存） | 0/3（单号被盖空、状态清掉） | 10/10 + 10/10（第二轮含票面合计） |
| `erp.void_with_children`（两个旧坑） | — | 23/23（10 + 3 + 10 轮） |

流程首跑碰到的环境坑，已在流程里处理：
- 实验站报的账套过期会显示「已断开」，后台不排件。流程有「竞争是否真实发生」的前置判定，这种情况判不通过。
- 每分钟的写账巡检会抢先把「排队中、还没有件」的单排上件。起始状态改成「停在已答过的人选」，巡检不碰。

### code-review（medium，第二段：79f6a2123...HEAD）

| 发现 | 真问题 | 修了 | 耗时 |
|---|---|---|---|
| 5 | 2（作废已写入的出入库单时撤多出来的件会把单据「已写入」清掉；保存守卫无法显式放行） | 2 修（`job_changed` 撤件分支对已写入 / 核不上的单不清账本栏；守卫认 `doc.flags.ledger_write`）；3 条不改：领件加锁读与作废锁序相反可能死锁（领单机器方法整笔重来接住）；先锁后核权限（没权限的请求随即回滚放锁）；库存格在没对上 Express 仓库码的数据上判不通过（new 数据集的仓已对好，流程如实报） | 约 10 分钟 |
