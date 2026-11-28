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
- **11-26 第一版的两个坑**在作废入口上专门验（实验栈格 `erp.void_with_children`：4 张单同时到作废 + 补开收据 + 付款）：作废那一路 10 + 3 轮不出 LinkValidationError，也不漏出死锁或保存前比对。另两路的结果只作记录：付款入口还会漏出 1213（属 ② 的 E1）。补开收据出过的 PDF 样式解析错（`xml.dom.SyntaxErr`，框架默认打印字体少一个引号）已查清，是测试替身的问题：cssutils「出错抛不抛」是进程级开关，生产每个请求、后台任务开头由 `pearnly_core.desk.print_css.lenient_css_parsing`（`before_request` / `before_job`，11-08 修的）设成不抛。实验脚本直接调函数、不经钩子，同一进程 6 路并发出凭证会复现（18 次 2 次）；补上这一步后 24 次全过。生产 ERP 网页是 gthread（每进程 4 线程），有这道钩子保护。实验脚本已补。

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

## ② ERP 同单写（分支 pearnly/conflict-b2-same-doc，11-28）

交接见 `~/pearnly-devtools/handoff-1128/并发冲突-后三批.md` 第四节。
- 已改：采购 / 销售付款、附件、删草稿、提交（网页入口拿到 post 锁后再 commit，「修改」作废原单按最新付款冲回）、旧付款接口、网页从 LINE 卡片提交：先 commit 再 `conflicts.attempt`。`intake._owned`、`collection._own` 先锁后读。
- E7 / E9 口径（主会话 11-28 同意）：全仓只有网页入口传 `amended_from`，LINE / CC 走不到「修改」。网页那格修前红修后绿；LINE / CC 那格写成不变式测试「内部调用撞并发付款时只报错重来，冲回金额恒等于实付合计」；另加守卫测试：以后新入口传 `amended_from` 而不走「拿锁后 commit」就失败。

### ② 实验栈结果（maclab `pearnly-b3`；修前 = ③b 472c5f398，修后 = 本分支）

| 格 | 修前 | 修后 |
|---|---|---|
| `erp.same_doc_writes`（同一张采购单 3 笔付款 + 补开凭证同时到，4 张一起） | 0/5（付款一路漏出 1213 死锁） | 10/10（每张已付 300、3 行付款） |
| `erp.amend_vs_payment_web`（网页「修改」作废原单与一笔付款同时到） | 0/5（旧副本冲回，原单保存撞 417 TimestampMismatch） | 10/10（作废、冲回金额 = 实付合计） |
| `erp.amend_vs_payment_internal`（内部调用、旧读视图，LINE / CC 那一类；不变式格） | 5/5 | 10/10（只许报错整笔回滚或冲回正确，从不作废了却冲少） |

守卫测试 `AmendOnlyThroughFreshEntry`：在只带 ③a 的源码上红，本分支绿。

### ② 第二轮（接手窗口，code-review 留下的 4 条与 E10 / E11）

逐条结论：
1. **删草稿 / 删附件在整笔重来时碰磁盘文件——真问题，已修（共用层）。** Frappe `File.on_trash` 当场删盘上那份（不等提交）。事务随后被回滚（重来、用尽回「请稍后重试」、任何后续报错）时 File 记录回来了、盘上已没有：附件打不开；原件层的登记文件里存着原件引用，重来那次读不到，存放处那份不再排删（漏删、长期占存储）。改在 `pearnly_core.originals.registered.RegisteredFile`（它在 MRO 里紧贴 Frappe `File`，Cowork 的文件守卫在外层照旧先核）：删除流程里 `delete_file_from_filesystem` 只登记「提交后再删」，提交那一刻还有别的 File 指着同一路径就只删缩略图；回滚清掉提交后回调，盘上不动。插入被回滚时 Frappe 清掉刚写的那份照旧当场删。三个产品所有删 File 的路径（移除附件、单据删除连带、LINE 收单作废、对账结束）都经这里。挂附件（`files.bind`）只改记录、不碰盘，重来幂等（同一文件按行去重）。
2. **E10 / E11 实验格——补了，并各查出一个真问题：**
   - E10 LINE 卡片：LINE 回调在分派前读过绑定、权限，`_owned(lock=True)` 锁到卡片后的 `exists` / `get_doc` 仍是旧视图。网页「提交」刚入账，LINE「取消」照旧状态走：卡片落成「已取消」、并把已挂到采购单上的原件删掉（单据在、原件没了）。改：`_owned(lock=True)` 加锁读整份；`handlers._save_card`（卡片保存 / 取消）锁卡片之前先 commit（到这里只读过绑定、权限，没有写），`conflicts.retried` 整笔重来，成功先 commit 再回 LINE。CC 对话的 `submit_chat` 在 CC 的事务里不能提交，靠加锁读整份。
   - E11 收款：版本号判断本来就拿加锁读的主表，改 × 放行两路都只有一路生效（修前修后都过）。**另查出管钱问题**：`release` 拿发票快照行锁之后，判余额与「别的收款占着的金额」（`reserved`）用的是普通读，读视图在拿锁之前（核权限、读这一笔）就建了；两笔草稿收同一张发票同时放行时，后到的那笔看不到前一笔刚放行的预留，**两笔都放行（同一张发票收两遍）**。改：`release` 里 `_checked(..., locked=True)` → 快照行与预留三张表都按加锁读（`collection.service._rows`、`snapshot.rows(locked=True)`）；建 / 改草稿、列未收清不加锁。网页 `api_collection_save` / `submit` / `withdraw` 先 commit 再 `conflicts.attempt`。
3. **交单 `start` 的月份仍取普通读——真问题，已修。** 改成一次加锁读整份任务（连单据行），月份、失效都按它判。失效判断里「这家公司有没有更新的任务」那次列表读仍是普通读：读视图里看不到并发新建的任务时按「没失效」交单，结果只是交给一个刚变旧的任务，不错账，不改。
4. **先锁后核权限的顺序——可接受，不改。** 同 ③b 作废入口：没权限的请求在锁上等一下、核权限失败整笔回滚放锁；报的错和单据存不存在无关，不泄露信息、不改数据。

第二次删已删的草稿：抛「找不到」（`DoesNotExistError`），什么都不动；沿用，不改文案。

两个旧坑（附件 LinkValidationError、子表间隙锁 QueryDeadlockError）逐入口专门验：格 `erp.b2_pitfalls`——采购 / 销售同一张单同时到 付款 ×2、挂附件、删附件、补开凭证、旧接口付款，草稿同时删两遍 + 挂附件；任何一路漏出这两种坑或 417 / 1213 原文都算不过；删掉的附件记录与盘上那份一致（共用同一份的除外）。

| 格 | 修前（③b 472c5f398） | 修后 |
|---|---|---|
| `erp.line_card_submit_vs_cancel`（E10：网页提交 × LINE 取消，两种先后各半） | 3/5（2 轮：卡片「已取消」、单据入了账、原件被删） | 10/10 |
| `erp.collection_edit_vs_release`（E11：网页改 × 放行，两种先后各半） | 5/5 | 10/10 |
| `erp.collection_double_release`（同一张发票两笔同时放行） | **0/5（每轮两笔都放行）** | 10/10（一笔放行、一笔回超收） |
| `erp.b2_pitfalls`（两个旧坑逐入口） | 0/5（旧接口付款漏出 417） | 10/10 |
| `erp.file_delete_rolled_back`（删 File 后回滚：记录与盘上那份都在；提交后两样都没了） | 0/5（回滚后记录在、盘上已删） | 5/5 |

修后 ERP 段全部 13 格各 5 轮全过（③ 的销售同号、资料清单、作废 × 子表、旧副本保存、看板，② 第一轮三格与本轮五格）：日志 `~/pearnly-devtools/handoff-1128/b3-logs/b2-full-after5.out`。
`--extended` 在列车头 8c9b0fab 上挑拣本分支后跑：新增 0、存量 275、误报 16。


收款发票子表 `ERP Collection Invoice.docnum` 加了索引（DocType `search_index`，原生迁移建）：放行的加锁读按单号锁这几张发票的行，没有索引时加锁读走全表、锁住整张子表（code-review 第 1 条）。实验栈 EXPLAIN 核过走 `docnum_index`；建索引后收款两格、旧坑格、LINE 卡片格再各 5 轮全过。
实验栈数据注意：前面挂修前源码跑「销售同号」格会再造出重号单（③a 的去重补丁这套库早已跑过），迁移在 `ERP Sales Document.request_id` 唯一索引处中止；照补丁 `v1_34_sales_request_id_unique` 再执行一次后迁移通过。线上迁移先跑补丁，不受影响。

#### 共用件影响清单（`check-impact.py --base 068ea0741`：ocr / 原件 14、transaction 6、另有 line-session、assistant 等）

| 入口 | 结果 | 依据 / 没测到的原因 |
|---|---|---|
| shared.conflict-catch | 通过 | 实验栈 ERP 段 13 格 × 10 轮、Cowork 段 4 格 × 3 轮；离线 test_conflict_silent_loss、test_conflict_catch_sites（候选离线） |
| erp.collection.server、erp.cc.collection | 通过 | 离线 test_erp_collection（新增放行按加锁读一条）、_cc、_guide、_line；实验栈收款两格 |
| erp.line.intake.submit、erp.line.card-result | 通过（实验栈 + 离线） | test_line_intake 149 条（新增保存 / 取消先提交、整笔重来、回复在提交后）；实验栈 `erp.line_card_submit_vs_cancel`。三星真机没测到（LINE 真机要排队，交列车 / 部署后实测） |
| erp.line.webhook-sim、erp.line.chat、erp.line.item-questions、erp.line.precheck-questions、erp.line.binding、cowork.line.chat | 没测到 | 这几条只经过改动的共用文件、本轮没改它们的逻辑；LINE 模拟 webhook 在回归实验室（今天归列车），真机要排队 |
| shared.originals.download、erp.stock-in.attach、erp.stock-out.attach、erp.stock-count.upload | 部分通过 | 删 File 的路径实验栈 `erp.file_delete_rolled_back`、`erp.b2_pitfalls`（删附件后记录与盘上一致）；离线 test_originals_registered。原件层存放处（甲骨文 / 谷歌）实机删除没测到：本机实验栈原件存本机 |
| erp.ocr、cowork.ocr、dms.ocr.id-card、shared.ocr.pipeline、erp.execution | 没测到 | 付费识别，不进默认回归；本轮只改删除时盘上那份的时机，不碰识别 |
| erp.cc.troubleshooting、cc.assistant、cc.operator.page、cc.operator.start-card、cc.desktop.requests | 离线通过 / 实机没测到 | test_express_operator_code_cc（交单按加锁读）、_tasks、_feed；CC 桌面版实机要 Windows 测试机 |
| erp.entry.choices | 没测到 | 只经共用文件命中，本轮没改 |

#### code-review（medium，068ea0741...HEAD）

| 发现 | 真问题 | 修了 | 耗时 |
|---|---|---|---|
| 5 | 5（1 条要紧、3 条小、1 条原有） | 4（索引、`_save_card` 顺序单测、登记与改动同一提交、保存点回滚不删缩略图）；原有的「失效判断里新任务列表仍是普通读」记录不改（不错账） | 约 15 分钟 |

全闸门、候选离线（c34722e4e：LANE EXIT 0）见交接。

## ④ 带外部读写的入口

分支 pearnly/conflict-b4-external（起点 24e3f1e3f）。逐条：现象、改法、修前 / 修后格结果。

### C1 应收快照同一张发票两行 + A2 #7 写后回执按旧 net 算（`collection/snapshot.py`）

- **现象（C1）**：同一件读好的 `express_ar_open`，网页 / LINE / CC 当场落（`snapshot.request`）与每分钟落（`snapshot.advance`）同时拿到「读好了」，各落一遍；同一本账套两份读回（先后两次按客户读、整本读 × 按客户读、MR.ERP 读回）同时 `replace`，两路的普通读都看不到对方刚插的行，各插一行。表上没有（book_key, docnum）唯一。两行时 `rows` 列两次，放行判余额的 `lock` 回 `{docnum: 行}` 只留其中一行：按哪一行判不确定（管钱）。另：`land` 当场删结果文件，所在事务之后回滚时请求行回来了、文件没了，下一次读到空内容记「结果坏了」。
- **现象（A2 #7）**：`apply_balances` 由写账回执调（CC 的事务里，读视图早就建了），普通读出行名与 `net`，按旧 `net` 算「已收」写回。
- **改法**：
  1. 唯一兜底：DocType 控制器 `on_doctype_update` 里 `frappe.db.add_unique(["book_key", "docnum"])`；补丁 `v1_35_receivable_snapshot_unique`（`[pre_model_sync]`）先合并已有重复——同一组（按库的排序规则，与唯一索引同口径）留读取时刻最新的一行、其余删，记一条错误日志带删了几行，再建索引；可重复执行。快照只是 Express 读回的缓存，收款记录不引用快照行。`replace` 插入撞唯一键时退回保存点、按单号加锁读出那一行照「旧的不盖新的」更新，Frappe 弹的「必须唯一」从 message_log 去掉。
  2. 排他取走：共用层 `cc_desktop.requests` **新增** `take(name)`（`drop` 不改）：加锁读请求行、仍是 done 才归本路，行在本事务里删、结果文件 `after_commit` 才删；后到的那路在这一行的锁上等前一路提交，读到已删回 None。`land` 用它；取不到时 `request` 回「正在读取」（另一路落的行这笔事务的普通读看不到，不能说「读好了」列成没有发票）。发起记录（缓存 `PENDING`）也改成提交后才清。
  3. 按账套串行到提交：`snapshot._hold`，不淘汰 Redis 锁（`pearnly_state.store().lock`），拿到后在 `after_commit` / `after_rollback` 放，同一笔事务再改同一本不再拿；`land`（取走之前）、`replace`、`apply_balances` 都拿。只在函数里拿、返回就放不够：锁放了事务还没提交，后到的那路看不到刚插的行。写快照的几处都拿，是为了避免「一笔事务先改了几行再去等锁、拿锁的那路又在等这几行」这种数据库看不出的互等（写后回执先 `apply_balances`、再 `_refresh` 按客户重读落库）。等 30 秒拿不到抛 `snapshot.Busy`：`request` 当成「正在读取」，`advance` 这一件下一分钟再来。`replace` 的现有行改加锁读；按客户读到、这位客户名下没有的单号，再按单号加锁读一次（同一张在别的客户名下：Express 改了客户或只差空白），改那一行而不是再插。
  4. `apply_balances`：先 `_hold`，再 `lock(book_key, docnums)` 一次加锁读出 name / net / read_at，按最新值改 / 删。`lock` 改用 `frappe.qb.get_query(..., for_update=True)`，按单号顺序走唯一索引；② 的 `release` 照旧调它锁行（返回值多了几列，调用方不用）。
  5. `advance` 每件包 `conflicts.retried`（每件自己提交；重来时请求行与文件都还在），用尽 / `Busy` 时这一件下一分钟再来，不挡后面几件。
- **基线**：`unlocked-overwrite-baseline.tsv` 删 `snapshot.replace`、`snapshot.apply_balances` 两行；`unlocked-overwrite-reviewed.tsv` 删 `apply_balances` 一行（原「net 不随并发改」的判断不成立）。`--extended` 新增 0。
- **离线**：test_erp_collection（新增 `SnapshotConcurrency` 7 条：同一件两路取只落一次、被别路取走时回正在读取、发起记录提交后才清、账套锁拿到提交 / 回滚才放且一笔事务只拿一次、别的客户名下的同一张改不插、撞唯一键改更新且旧的不盖新的、写后余额按加锁读的 net；`SnapshotUniquePatch` 1 条：每组留最新、第二次不改）、test_erp_collection_cc / _guide / _line、test_erp_mrerp_collection、test_cc_desktop_channel（新增 `take`：只取一次、回滚时文件还在、提交后才删）及其余 10 个用通道替身的测试文件全过；check-architecture、check-unbounded-queries、check-conflict-catch 通过。

| 格 | 修前（b4-before = 24e3f1e3f） | 修后 |
|---|---|---|
| `erp.snapshot_double_land`（(a) 同一件读回 `request` × `advance`；(b) 同一位客户两次读回同时 `replace`；两种各半） | 2/10（8 轮两行，两种都出现） | 10/10 |
| `erp.snapshot_balances_stale`（写后回执前另一路把 net 1000 → 1200 并提交） | 0/10（已收按旧 net 算成 300） | 10/10（已收 500） |

修后挂本分支 f4efcba24 迁移：补丁 `v1_35` 执行、`unique_book_key_docnum` 建好（实验栈当时没有重复行）。补丁另在实验栈上专门验：删掉索引、造一组 3 行同单号（读取时刻 01:00 / 03:00 / 空）加 1 行另一单号，执行补丁 → 留 03:00 那行和另一单号那行、删 2 行、错误日志「deleted 2 duplicate rows」、索引重建；再执行一次什么都不改、不再记日志。② 的收款两格（`erp.collection_double_release`、`erp.collection_edit_vs_release`，`release` 调改过的 `lock`）修后各 5 轮全过。

#### 共用件影响清单（`check-impact.py --base 24e3f1e3f`）

命中 cc-desktop（新增 `requests.take`，已有函数没改），15 个入口。只有 `erp.collection.server`、`erp.line.collection` 走到新函数；其余 13 个只因同一共用件命中，没走到改动的代码。新调用方 `collection/snapshot.py` 登记进 execution、transaction 两件的入口（`erp.collection.server`）。

| 入口 | 结果 | 依据 / 没测到的原因 |
|---|---|---|
| erp.collection.server | 通过 | test_erp_collection 91 条；实验栈本条两格 10/10、② 收款两格 5/5 |
| erp.line.collection | 离线通过 | test_erp_collection_line 70 条；LINE 真机没测到（要排队） |
| erp.cc.connect、erp.cc.write、erp.cc.masters、erp.cc.stock-void、cowork.cc.channel、cc.operator.page、erp.cc.companies、erp.cc.chat-push、cc.mrerp-login、erp.mrerp.direct、cc.desktop.bridge、cowork.cc-chat.push、cc.desktop.cloud-express | 没测到 | 只新增 `take`，这些入口不调它；`drop` / `result` 等原函数没改。test_cc_desktop_channel 与其余用通道替身的离线测试全过 |

#### 已知 / 没做

- `express/masters.py`、`express/ledger_check.py` 也是 `requests.result` + `requests.drop`（当场删文件），同样有「回滚后文件没了」的问题，不在本条范围，没改；要改直接换成 `take`。
- 唯一索引按库的排序规则比单号（只差大小写算同一张）；Express 单号实际是大写，没核过线上有没有只差大小写的两张。
- 线上各 ERP 站有没有重复行没查（生产只读要主窗口排）；补丁会合并，日志里有删了几行。

### A2 #1 / #3 无人值守推送判完之后人推了同一张（`folder_intake/push.push_one`、`push/automation/sweep`）；#2 泵登记误报

- **现象**：文件夹定时推送（`push_one` 开头普通读 `edit_count / last_push_status / last_push_record`）与自动推送扫描（`sweep.candidates` 一轮开头普通读出最多 1000 张）判「没人改过、没推过」之后，中间多次提交、取要选的项、做判定，再交 `application.submit`；推送准备 `prepare` 锁住票后只比 `record_version` 与连接版本，不再核「人推过没有」。这段时间里人在网页推了这张，定时任务接着推。
- **核实**：
  - 人在网页推送：`api.submit` → `prepare` → `ledger.reserve` 建推送记录（准备好时不投影），页面随即调 `companion.submission.confirm` 转排队，`push_state.mirror` 把推送状态、记录（与去向）`set_value(update_modified=False)` 抄到票上，**不升 `record_version`**（版本只在人改票时由 `storage.validate_document` 升，改单次数同时 +1）。所以只比版本挡不住「人推过」。
  - 请求号幂等挡不住：定时任务的键由（文件、票、版本、第几次）定，扫描的由（票、版本）定，网页是页面生成的键；`ledger.lookup_request` 只回同号的。
  - 推送账本按单据键挡住一部分：`reserve` 对（工作空间、客户、票、连接、账套）已有的记录加锁读，准备好 / 排队 / 在写 / 成功 / 结果不明的回原记录，**推往同一份账套不会建第二条**。挡不住的：人推往**另一份账套**（单据键不同，两份账套各写一张）；人那条停在待人处理 / 失败 / 撤回（定时任务照推，盖过人的处理）；人那条还在「准备」与「确认」两次请求之间时，定时任务拿到人那条、替人确认（人的确认随后版本不对、票上记失败）。
- **改法**：`prepare` / `submit` 加 `unattended`，只有 `push_one` 与 `sweep._push` 传 True（MR.ERP 定时任务不带 `automation`，所以不按 `automation` 判）。锁住票之后、请求号回放之后、版本比对之前，`_touched(doc)`（加锁读出的现值：改单次数不为 0，或已有推送状态 / 推送记录；推送原因不算，定时任务停下记过原因的票照常重推）为真就回 `{"status": "human"}`，不建推送记录、不记失败原因。放在回放之后：自己上一次同号的那条照旧回原记录；放在版本比对之前：人改过的票回 human，不再记成「记录已变」失败。`push_one` 收到回 `human`（同它开头的判断），扫描记为 `human` 一张。网页、CC、待处理卡片不传，行为不变。
- **#2 判误报**：`pump._push_step` / `_push` 的文件行只有泵写（`outcome_json` 全仓只有 `pump.py` 写），泵由 Redis 锁单跑（120 秒 > 每步 45 秒），每次写回前 `read(FILE, for_update=True)` 重读；锁过期两路并跑时同一张票的请求号相同，推送服务按请求号回原记录。对票的写都在 `push_one` → 推送服务里，由上面的重核兜住。
- **基线**：`unlocked-overwrite-baseline.tsv` 删 4 行（`pump._push`、`pump._push_step`、`push.push_one`、`sweep.run`）；静态检查仍会命中这几处（普通读出的值传给推送服务），`unlocked-overwrite-extended-reviewed.tsv` 登记 4 条（2 条「已修」写明重核在哪、2 条误报写明单写者与请求号回放）。`--extended` 新增 0。
- **离线**：test_cowork_push_fixed 新增 `UnattendedRecheck` 4 条（人推过 / 推成功 / 改过回 human、判的是加锁那一份、不建记录；没人动过照常往下、人发起的不看这几栏；同号回放照旧回原记录；`submit` 透传）；test_cowork_folder_intake 1 条（回 human 时不确认、不撤、不记失败，推送声明无人值守）；test_cowork_da_tiers 1 条（扫描回 human 不确认、不记失败，计数 `human`）；test_cowork_folder_follow_year 替身收 `unattended`。三条新测试在修前代码上都红。Cowork 推送相关（push_lifecycle、push_fixed、push_conflicts、push_native_contract、da_automation / tiers / verify / replay、folder_intake / follow_year / tasks_mrerp / task_batch / reminders / task_guide、scheduled_task_fixes、mrerp_operator_dispatch、write_precheck、assistant_tools、goods_posting、cowork_express_* 13 个）全过；check-architecture、check-unbounded-queries、check-unlocked-overwrite --extended 通过。test_cowork_folder_computers、test_cowork_mrerp_push 本机缺 frappe / requests，用实验镜像跑时导入的是镜像里的旧应用代码，修前修后同样失败（候选离线按 APP_MOUNTS 跑），没测到。
- **共用件**：改动的三个文件不是登记的共用件（`check-impact.py --base 51686e58b` 无命中）。

| 格 | 修前（b4-before = 24e3f1e3f） | 修后（2494899f8 + 09d92f459） |
|---|---|---|
| `cowork.folder_push_vs_human`（判完之后另一路把这张推往另一份账套并提交；文件夹定时推送、自动推送扫描各半） | 0/10（两条路各 5 轮都越过、接着准备第二条推送） | 12/12（两条路各 6 轮回 human，推送记录只有人那 1 条） |

实验站没有 Express 连接与目录：格里连接读取、请求号查找换成替身，越过重核后的第一步（`_document_block`）换成「会接着准备、建第二条推送记录」的标记；人推往另一份账套，是因为同一份账套的重复准备账本本来就挡住，测的是挡不住的那种。修后 Cowork 全部 5 格各 3 轮全过。

#### 已知 / 没做

- `push_one` 在推之前按跟随公司选的账套 `retarget`（加锁读比去向，但不看人是否刚推过）：人推往另一份账套后、定时任务回 human 之前，票上的去向已被改成任务那份（只影响显示与按公司归类，不建推送记录），没改。
- 顺手（76c687b83）：实验格收款两格 `_collection_seed` 改账套资料前记下原值，`_collection_cleanup` 还原（原来没有就删文件，同 `collection_lib.restore`）；实验栈上跑前跑后资料一致，原来没有资料时跑完文件已删。

### A2 #12 LINE 收单识别任务记结果撞冲突：识别已付费、结果丢了（`integrations/intake.run`；同类 `run_chat`）

- **现象**：识别任务开头普通 `get_doc` 判「还在排队」；`_fetch` 从 LINE 取件、交原件层上传后，`save` + `commit` 记到收单记录上；付费识别（扣费参考号固定 `erp.line_intake:<名>`）之后 `reload` → `save` 写成待提交 → 预选 → 提交 → 出题 → 发 LINE。整段不在 `conflicts.retried` 里，识别执行器对任何异常都整笔回滚、记 `workflow_error`、不重放（`execution/work.py`）。识别期间另一路改过这张卡（关快照隔离后 `reload` 读到的是本笔读视图里的旧修改时间），`save` 撞保存前比对：**识别已付费、结果丢了**，用户被告知重发，重发是新消息、新参考号，再扣一次；取件期间另一路改过，`_fetch` 的 `save` 撞上：File 与记录一起回滚，**存放处那份没人删**。
- **核实**：执行器检查点 `work.checkpoint(key, state, body)` 按行 `INSERT … ON DUPLICATE KEY UPDATE` 后当场 `frappe.db.commit()`；同一份 Work 重跑时 `run` 每次从库里重读（`context` 清缓存 → `_stages` → `stored_checkpoints`），能取回。`state` 是自由文本：`run` 只看 `started`（判结果不明）与 `done` / `external`（判已付费），`done_calls` 只数 `done`；Cowork 已有产品自己的 `terminal`。删除链：登记 File 删掉时 `RegisteredFile.on_trash` 排 `enqueue_after_commit` 的 `purge` 删存放处那份；盘上那份 `delete_file_from_filesystem` 在 `after_commit` 才删（② 11-28）；回滚两样都不动。本机存的新 File 回滚时由 Frappe `File.on_rollback` 删盘。
- **改法**（外部动作都只做一次，只有库内记结果那段重来）：
  1. 开头判状态改 `get_doc(..., for_update=True)`，判完 `commit` 放锁（下面去 LINE 取件、交识别不攥着这一行）。
  2. `_fetch`：取件、上传后先把 File 单独提交；记到记录上改为 `_keep_source`（按名字加锁读、仍在排队才写、提交），包 `conflicts.retried`。用尽：`_forget` 删掉刚上传的那条登记并提交（存放处 / 盘上那份按上面的链提交后删），抛 `Deferred("conflict")` 按队列稍后再来（还没付费，LINE 的内容还能再取）；这期间已不在排队：同样删掉，回「不在排队」。
  3. 识别 `_recognised`：本份 Work 有结果检查点（`erp.line_intake.result`，状态 `terminal`，不算进调用计数）就取回；没有才识别，成功先写检查点（自己提交）。
  4. 记结果 `_keep_result`（加锁读、仍在排队才写待提交 + 结果、拆出同一文件里的其余几张、每行预选、提交）包 `conflicts.retried`，重来不再调识别。用尽抛 `Deferred("conflict")`：重跑从检查点取回结果记上，不再识别、不再扣费（付过费的任务有 3 小时续跑宽限，过了按排队超时告诉本人）。识别期间这张已不在排队（取消 / 落定）：不改回、不出卡。
  5. 出题、发 LINE 照旧在记结果提交之后。
- **基线**：`unlocked-overwrite-baseline.tsv` 删 `integrations/intake.py run` 一行；`--extended` 新增 0。
- **离线**（bench 容器里跑 `test_line_intake.py`，154 条全过）：新增 `SaveConflict` 5 条——记结果撞一次保存前比对 → 待提交、识别 1 次、扣费参考号 1 次、发 LINE 1 次、两处读都加锁；记结果用尽 → `Deferred`、没发 LINE，重跑从检查点取回、识别仍 1 次；识别期间被取消 → 不改回、不出卡；记原件撞一次 → 取件 1 次、上传 1 次、记上那份；记原件用尽 → 刚上传的登记被删、`Deferred`、没识别没扣费。5 条在修前代码上全红（4 条抛 TimestampMismatchError，1 条把取消的改回待提交）。`Base` 补检查点替身（按 Work 存内存）；`Billing` 一条同一替身跑两张时清检查点（两份 Work）；一处 `get_doc` 替身收 `for_update`。test_cc_intake、test_operator_intake 在容器里过；tests/ 下 test_upload_limits、test_erp_recognised_numbers、test_snapshot_reset、test_erp_pending_guide、test_erp_guide_documents、test_erp_express_direct、test_erp_write_precheck、test_conflict_catch_sites、test_check_impact、test_check_unlocked_overwrite 过；check-architecture、check-unbounded-queries、check-conflict-catch 通过。
- **共用件**：没改共用件本身（`check-impact.py --base d86aca9b7`「命中的共用件：无」）；`intake.py` 新调用 `conflicts.retried`，登记进 transaction 件的入口（`erp.execution`）。只改到调用方的 8 个入口：

| 入口 | 结果 | 依据 / 没测到的原因 |
|---|---|---|
| erp.execution、erp.ocr | 实验栈通过（识别替身） | 本条格；付费识别与真 LINE 不在本机测（任务书禁止） |
| erp.line.chat、erp.line.item-questions、erp.line.precheck-questions、erp.line.binding、erp.line.intake.submit、erp.entry.choices | 没测到 | 真机 LINE 不在本窗口范围；离线 test_line_intake 154 条（含出题、出卡、卡片保存）过 |

| 格 | 修前（b4-before = 24e3f1e3f） | 修后（dbb30a529） |
|---|---|---|
| `erp.line_intake_save_conflict`（识别替身，扣费参考号计数；识别期间 / 从 LINE 取件期间另一路改这张卡并提交，两种各半） | 0/12（两种都抛 TimestampMismatchError：卡还在排队、识别已调 1 次、结果丢了、没发 LINE；取件那种记录上没有原件） | 12/12（待提交、结果在、识别 1 次、取件 1 次、发 LINE 1 次、只留记上的那份 File） |

| `erp.cc_intake_save_conflict`（CC 对话收单 `run_chat`，识别替身；识别期间另一路改这条并提交） | 0/12（抛 TimestampMismatchError：还在排队、识别已调 1 次、结果丢了） | 12/12（5c8de8667：待提交、结果在、识别 1 次、不发 LINE） |

修后（5c8de8667）两格同一次各 12 轮重跑全过（LINE 那格修前同样 0/12）。修后另跑 `erp.line_card_submit_vs_cancel`、`erp.file_delete_rolled_back` 各 3 轮全过。格里任务在 `work.context(<b3lab 编号>)` 里跑（检查点照真写、跑完删），LINE 绑定、取件、出题、发送换替身。

#### 已知 / 没做

- 存放处那份的删除、记结果用尽后的重跑只在离线验（见上）。
- 记结果用尽后靠队列重跑，重跑前这张一直是「排队中」；本机没造出连撞 4 次。实验栈原件层是本机存放（`store.where() == local`），本机存的新 File 回滚时 Frappe 自己删盘，修前在实验栈上也看不到残留。

#### 按类同改：CC 对话收单 `run_chat`（`erp.cc_intake`，主会话 11-28）

- **现象**：同 LINE：普通 `get_doc` 判状态 → 付费识别（参考号 `erp.cc_intake:<名>`）→ `reload` → `save`，不在 `retried` 里；识别期间另一路改过这条，`save` 撞保存前比对，识别已付费、结果丢了。④ 清单与静态检查都没列它。
- **改法**（5c8de8667，与 LINE 共用 `_checkpointed` / `_keep_result`）：开头加锁读判状态、随即提交放锁；识别结果进同一个结果检查点；记结果包 `retried`、重来不调识别；用尽抛 `Deferred("conflict")`，重跑从检查点取回；识别期间已不在排队不改回。原件在 `receive_chat` 收下时已存好，这里不取件。方向是猜的、按采购读出是本公司开的票时改按销售读：改方向的 `set_value` 先提交（记结果整笔重来不会把它回滚掉）。CC 对话不回 LINE，记录由 CC 读（`chat_records`），返回值照旧。
- **离线**：test_cc_intake 新增 `RunConflict` 3 条（撞一次 → 待提交、识别 1 次、两处读加锁；用尽 → `Deferred`、重跑从检查点取回、识别仍 1 次；识别期间被取消 → 不改回），修前代码上全红（2 条抛 TimestampMismatchError，1 条把取消的改回待提交）；`Base` 补检查点替身，一条同一替身跑两次的测试清检查点（两份 Work）。test_cc_intake 16 条、test_line_intake 154 条在容器里过；check-unlocked-overwrite --extended 新增 0、check-architecture、check-unbounded-queries、check-conflict-catch、test_snapshot_reset、test_conflict_catch_sites、test_erp_write_precheck 过；`check-impact.py --base d862e8bae` 命中共用件：无。

#### 同形状只读核（不改）

- **Cowork LINE 收单**（`line/intake._admit` → `ocr.execution.workflow.run`）：已是本条的形状。取件 + 存原件后先提交再交识别；识别结果由 `_freeze_result` 加锁读任务单写进 `result_json` 并提交（之后重跑看到 `result_json` 就不再识别，供应方调用另有检查点）；落票 `_persist` 加锁重读，撞冲突（`conflicts.is_conflict`，含保存前比对）回滚后 `Deferred("database_busy")` 稍后再来；LINE 通知（`cowork_ocr_recognized` → `announce`）在落票提交之后。剩一处小的：`run` 开头普通 `store.read` 后把 `phase` 写成 recognizing 并 `save`，这一步撞保存前比对不在接冲突的范围里，会记 `workflow_error`——在付费识别之前，不丢已付费的结果，用户收到「请重发」。没改。
- **DMS 身份证识别**（`intake/flow._recognize_image`）：没有「旧副本 save」：识别后 `commit`，再按会话加锁比较交换（`line_session.replace_session_snapshot`），草稿变了回 `draft_changed`（有意丢弃）；识别记录 `_record_ocr` 只插新行、写不上只记日志；扣费参考号按 LINE 消息固定（`idcard:line:<消息>`），LINE 重投不重复扣。识别结果没进检查点：比较交换那一步若撞 1213 会记 `workflow_error`、结果丢（低概率）；这段会话读改写已在基线里归 ④（`flow.py _recognize_image` 等，随 line_session 根函数改），不在本条。没改。

### A2 #16 / #17、巡检、B6：MR.ERP 导入直写记结论（ERP `mrerp/push.run`、`mrerp/choices.observe`、`mrerp/push.poll`；Cowork `push/mrerp/runner.run`）

- **现象（A2 #16，ERP `run`）**：领取（行锁读 + 提交）→ MR.ERP 导入（外部）→ 存选项 + 提交 → 普通读 `status` 判作废 → `_set` 结论 + 提交。记结论不在 `retried` 里：撞 1213 任务就失败，单据停在「正在导入」，15 分钟后被巡检转成「结果不明 ERR_IMPORT_INTERRUPTED」，实际已写进 MR.ERP 也要人工核。另：作废判断是普通读，与作废（`express.push.void`，锁着单据清账本栏）同时发生时，本事务读到作废前、`set_value` 等作废提交后照写「已写入」，作废了的单显示成干净的已写入。
- **核实（清单写「未核实实验栈能否不连 MR.ERP 跑 `run`」）**：能。导入在共用层 `pearnly_core.mrerp.push.execute.execute`，`run` 用到时才导入，换替身即可；账本判定 `handles`、准备 `prepare`、凭据 `connections.lease`、账号占用 `session_lock.operator_holds`、主档整读 `masters.after_write` 换替身后 `run` 在实验站上照真跑领取与记结论。另核：记结论这笔事务原来只锁单据行一处，正常路径下很难自然撞 1213（导入期间别人改单据，`set_value` 只是等锁、不报错）；会撞的是同一笔事务里还有别的锁（待处理通知、代录回执写的行）与另一路交叉的情形，实验格按这个造真死锁。
- **现象（A2 #17，`choices.observe`）**：普通读连接上整份 `menu_options / master_options` → 合并本账套 → `set_value` 整份。同一连接两本账套同时导入 / 读取，后写的盖掉先写那本刚存的选项缓存。
- **现象（巡检 `poll`）**：普通读列出「排队中、正在导入、超过 15 分钟」的单，按列出时的旧值 `_set` 成结果不明：此间导入刚写成「已写入」会被盖回「结果不明」。另一支 `dispatch` 也按旧值交。
- **现象（B6，Cowork `runner.run`）**：领取 → 提交 → MR.ERP 导入 → 记选项 → `ledger.ack` / `mark_unknown` → 提交。回执不在 `retried` 里：撞 1213 任务抛出，记录停在「已领」，写者锁等租约过期才放、过期后按结果不明转人工；已写进 MR.ERP 的要人工核，同连接后面的单排着。
- **改法**：
  1. ERP `run`：导入之后的「记结论」抽成 `_conclude`，包 `conflicts.retried`（重来不再导入）。先加锁读这张的 `status / ledger_state / ledger_reason`：作废了按原规则（导入成的转人工「作废后写进了」、单号留着；没导入成的清账本栏；结果不明照旧交人核）——作废会清账本栏，所以先于「还是不是本次领取」判；仍是本次领取（排队中 + 正在导入）照导入结果写；已被别的路径改过时：被巡检转成结果不明、导入其实写进了的，照实记已写入与单号（导入结果是事实，「结果不明」是猜测）；转到别的意外状态的转人工、单号留着（见下「记结论遇到意外状态的口径」）；没写进的以别人的结论为准、提交放锁。用尽照旧抛出（同修前：停在正在导入、由巡检转结果不明）。存选项那段也包 `retried`（缓存，用尽 / 出错照旧吞掉）。写进 MR.ERP 后的主档整读在提交之后、照旧。
  2. `choices.observe`：`_options(connection, lock=True)` 加锁读整份，共用层 `observe` 只改本账套那一格；这次什么都没读到时不写也不锁（同原来不写）。三个调用方（导入、读取任务、收款读回 / 收款导入、主档整读记负库存）都在自己的事务边界上，随后提交。Cowork 同类（`connections.observe_mrerp_menus / _masters`）原本就是加锁读（`state.read` 默认锁），不用改。
  3. `poll`：改结果不明那一步抽成 `_interrupted`，加锁按现值复核仍是已提交、MR.ERP、排队中 + 正在导入、发出时刻仍超过 15 分钟才改（被重新领走的发出时刻会变新，也不改）。`dispatch` 那一支核实后不改：它只排任务（`enqueue_after_commit`、按单据去重），任务开头 `_claim` 就在行锁里按现值复核（仍排队中、不在导入、已提交、账本仍是 MR.ERP），列出之后被转走的那一件领不到、什么都不改；在巡检里先锁一遍是重复的锁，还会在一轮里攥着至多 100 行到提交。与 Express 巡检（③b `send(..., poll=True)`）不同：Express 那条是在巡检进程里当场准备、排写账件。
  4. Cowork `run`：回执那段抽成 `_conclude`，包 `conflicts.retried`（导入结果在外层变量里，重来不再导入）；用尽时记一条带记录号与单号的错误日志、不抛（记录仍「已领」、写者锁留着，同修前由巡检在租约过期后转结果不明），不再接着排同连接下一条（锁没放）。
- **核实「ack 已提交后外层再撞冲突、同一 token 第二次 ack」（清单写待核）**：`_conclude` 的提交是最后一句，提交之后没有会被重来的语句，实际走不到第二次 ack；回执在保存点里（`ledger.atomic`），撞 1213 整笔回滚、保存前比对回到保存点后由 `retried` 整笔回滚，被回滚的那次什么都没落，重来是第一次回执。就算同一次尝试再收一次同样的回执：`_attempt` 核 fence / 领取人 / token（回执后不清 `lease_owner / active_attempt`，注释写明为了重复回执核对），成功的记录回 `replayed`，转人工的按回执摘要相同回 `replayed`，`mark_unknown` 在已不是「已领」时回 `replayed`——都回原记录、不报 stale。实验格每轮修后再收一次同样的回执，回 `replayed: True`。已是幂等，不用另改。
- **基线**：`unlocked-overwrite-baseline.tsv` 删 4 行（`mrerp/push.py run` × 2（$doctype、ERP MRERP Connection）、`mrerp/choices.py observe`、`mrerp/choices.py run_read`）；`unlocked-overwrite-reviewed.tsv` 删 `push/mrerp/runner.py run`（写单号那句挪进 `_conclude`，登记过期）。静态检查仍命中两处，`unlocked-overwrite-extended-reviewed.tsv` 登记 2 条：`choices.run_read`（传下去的是 MR.ERP 读回的值，`observe` 自己加锁读，「已修」）、`push.run` 领取那笔里的两处写（`_claim` 第一句就加锁读这张、锁到提交，`prepare` 的普通读在持锁之后才建读视图，「误报」）。`--extended` 新增 0。
- **共用件**：没改共用件本身；`mrerp/push.py`、`push/mrerp/runner.py` 新调 `conflicts.retried`，登记进 transaction 件的入口（`erp.mrerp.direct`、`cowork.mrerp.push`）。`check-impact.py --base 7290adf7c` 登记表完整、命中的共用件无。
- **离线**：test_erp_mrerp_direct 新增 `ConclusionConflict` 6 条（记结论撞一次 1213 → 已写入、单号在、导入 1 次、结论前加锁读；作废按加锁读的现值转人工；被巡检转走时没写进的不盖、写进的照实记；巡检列出后已写入 → 不动、仍超时的照旧转结果不明；巡检列出后被重新领走 → 不动；存选项加锁读到另一本那格、只并本账套、没读到不锁）。`Db` 替身加读视图（`stale`：普通读看旧行、加锁读看最新）。6 条在修前代码上全红（1 条抛 QueryDeadlockError，5 条断言不过）。test_cowork_mrerp_push 新增 `ConclusionConflict` 2 条（成功 / 结果不明 / 失败三种回执各撞一次 1213 → 导入 1 次、同一 token / fence 回执两次、接着排同连接；用尽 → 不抛、不接着排），修前 4 个子测试全抛 QueryDeadlockError。离线跑过：tests/ 下 test_erp_mrerp_direct 54、test_erp_mrerp_stock 12、test_erp_mrerp_collection 37、test_erp_cc_companies、test_erp_mrerp_onboarding、test_erp_write_precheck 34、test_ledger_instant_sync、test_erp_guide_documents、test_erp_express_direct 224、test_snapshot_reset、test_conflict_silent_loss、test_check_unlocked_overwrite、test_check_impact、test_conflict_catch_sites；Cowork 实验镜像（源码挂本工作树）里 test_cowork_mrerp_push 62、test_cowork_mrerp_operator 14、test_cowork_mrerp_menus 17、test_cowork_mrerp_cc_login 16、test_cowork_push_conflicts 9、test_cowork_push_lifecycle 31、test_cowork_push_native_contract 12、test_cowork_push_fixed 33、test_mrerp_ledger_check 9、test_mrerp_rules_golden 3、test_erp_mrerp_golden 5。check-architecture、check-unbounded-queries、check-conflict-catch、check-unlocked-overwrite --extended 通过。

| 格 | 修前（b4-before = 24e3f1e3f） | 修后 |
|---|---|---|
| `erp.mrerp_conclusion_conflict`（导入替身回已写入；导入期间另一路改单据；记结论写下单据行后与另一路交叉锁公司行，真 1213 回滚本连接） | 0/10（抛 QueryDeadlockError，单据停在「正在导入」、没有单号，导入已发 1 次） | 10/10（7480eab8e：已写入、单号在、导入 1 次；10 轮都是本连接被回滚，竞争真实） |
| `erp.mrerp_poll_stale`（巡检读视图建好后另一路把这张记成已写入并提交，再跑巡检） | 0/10（被盖成「结果不明 ERR_IMPORT_INTERRUPTED」，单号还在） | 10/10（已写入不动） |
| `cowork.mrerp_ack_conflict`（导入替身回已写入；另一路先改几张票、锁着推送记录再来锁这张票，回执那笔已锁连接与票、在等推送记录，真 1213 回滚回执那笔） | 0/10（抛 QueryDeadlockError，记录停在「已领」、两把写者锁仍被它持着、没接着排下一条） | 20/20（b351fa045，两次各 10 轮：已写入、单号在、写者锁放开、导入 1 次、接着排同连接；同一 token / fence 再收一次同样的回执回 `replayed: True`） |

实验格里 MR.ERP 一律替身（不连真 MR.ERP）：ERP 换 `handles` / `prepare` / `connections.lease` / `operator_holds` / 共用层导入 / 主档整读，`run` 本身照真跑领取与记结论；Cowork 换连接读取 / 凭据 / 账号占用 / 导入，领取换成照 `ledger.lease` 落行的替身（尝试、写者锁、记录改已领），回执走真的 `ledger.ack`。两处的 1213 都是格里造的交叉加锁（ERP 由替身在结论写下单据行后再锁公司行，代替同一笔事务里的后续写；Cowork 由另一路按「推送记录 → 票」与回执的「连接 → 票 → 写者锁 → 推送记录」交叉），另一路先多改几行使 InnoDB 回滚较轻的那笔；被回滚的是另一路的那几轮记作「竞争不真实」不算（这次 0 轮）。Cowork 修后第一次跑（挂回修后源码、迁移刚完）进程以 139 退出、没有输出，重跑两次各 10 轮全过，没查到原因（没复现）。修后另跑 ERP `erp.stale_save_keeps_ledger`、`erp.collection_double_release`、`erp.snapshot_balances_stale` 各 3 轮，Cowork `cowork.folder_push_vs_human`、`cowork.withdrawn_keeps_new_push` 各 10 轮，全过。跑完 ERP、Cowork 两套都挂在本工作树（与 HEAD 同一份应用代码）。

#### 已知 / 没做

- 收款导入 `collection/mrerp.run` 同形状，主会话 11-28 定按类同改，见下一节。
- 记结论 `retried` 用尽（连撞 4 次）时 ERP 照旧抛出、停在「正在导入」由巡检转结果不明；Cowork 记一条带单号的错误日志、等租约过期由巡检转结果不明。实际已写进 MR.ERP 的单号只在 ERP 任务的报错里 / Cowork 的日志里，没有自动对回。本机没造出连撞 4 次，只在离线验。
- 「已被别的路径改过、但导入写进了」只在现值是「结果不明」时照实记已写入：实际能走到的只有巡检转结果不明（任务最多 600 秒、巡检看 15 分钟，正常走不到）；作废先判（单测钉住：结果不明之后又作废 → 写进去后作废、转人工、单号留着）。
- ERP 记结论这一步在真实路径上自然撞 1213 的概率低（原来只锁单据一行）；修的主要收益是作废同时发生时按加锁读的现值判、巡检不再盖真结论、万一撞上不再留「正在导入」。

#### 按类同改：收款导入 MR.ERP（`collection/mrerp.run`、`poll`，主会话 11-28）

- **现象**：领取（行锁里从待写入改成写入中 + 提交）→ 导入（共用层 `mrerp.push.receipt.execute`）→ 存选项 → `push._set` 结论 + 提交，记结论不在 `retried` 里、不加锁复核：撞 1213 任务失败，这笔停在「写入中」，15 分钟后被巡检转成结果不明，已写进 MR.ERP 的要人工核。巡检同 `mrerp.push.poll`：按普通读列出时的旧值把超时的「写入中」改成结果不明，此间导入刚记成已写入会被盖回。另：写入中撤回会被拒（`withdraw` 抛忙），但巡检转成结果不明（失败）之后可以撤回；这时迟到的导入结论原来照写「已写入」，盖掉撤回（修前单测红）。
- **改法**（a1a52f5f4）：记结论抽成 `_conclude`，包 `conflicts.retried`（重来不再导入）。加锁读这笔的状态与原因：仍是本次领取（写入中 + 正在导入）照导入结果写；已撤回而导入写进了的，留在已撤回、原因记「写进去后撤回」、单号与写入时刻留着（同 `withdraw` 对已写入那笔的规则，收款页 / CC 照「去 MR.ERP 作废」提示）；被巡检转成结果不明而导入写进了的，照实记已写入；别的状态不盖（写进了的记一条错误日志带单号）；没写进的以别人的结论为准。写后按客户重读未收发票只在记下单号时排。存选项那段同样包 `retried`。巡检改结果不明前加锁按现值复核仍是写入中 + 正在导入且领取时刻仍超时；排任务那一支由 `_claim` 在行锁里复核，不改。
- **ERP 单据同一处收紧**（a1a52f5f4，主会话要求核「作废 / 撤回走现有规则」）：`mrerp.push._conclude` 作废先判，原本就走「写进去后作废」；已被别的路径改过时，原来写进了就记已写入，现改成只在现值是「结果不明」时记，别的状态不盖、记日志。ERP 单据没有「撤回」，只有作废。
- **离线**：test_erp_mrerp_collection 新增 `ConclusionConflict` 4 条（撞一次 1213 → 已写入、导入 1 次、结论前加锁读；结果不明 / 撤回 × 写进 / 没写进四种：结果不明 + 写进 → 已写入，撤回 + 写进 → 已撤回 + 写进去后撤回 + 单号，撤回 + 没写进 → 不动，结果不明 + 没写进 → 不动；转到别的状态 + 写进 → 不盖、记日志；巡检列出后已写入 → 不动、仍超时的照旧改），修前全红（7 处断言不过）。test_erp_mrerp_direct 新增 1 条（结果不明之后又作废 + 写进 → 写进去后作废、单号留着；没写进 → 清账本栏；转到要人选 + 写进 → 不盖、记一条日志），修前红（日志那段）。test_erp_mrerp_collection 41、test_erp_mrerp_direct 55、test_snapshot_reset、test_conflict_catch_sites 过；check-architecture、check-unbounded-queries、check-conflict-catch、check-unlocked-overwrite --extended（新增 0）过；`collection/mrerp.py` 新调 `conflicts.retried`，登记进 transaction 件的入口（`erp.collection.mrerp`），`check-impact.py --base ae2811033` 登记表完整。

| 格 | 修前（b4-before = 24e3f1e3f） | 修后（a1a52f5f4） |
|---|---|---|
| `erp.mrerp_collection_conclusion_conflict`（收款导入替身回已写入 + FR 单号；记结论写下收款行后与另一路交叉锁公司行，真 1213 回滚本连接） | 0/10（抛 QueryDeadlockError，这笔停在「写入中」、没有单号，导入已发 1 次） | 10/10（已写入、单号在、导入 1 次；10 轮都是本连接被回滚） |

同一次对比里 `erp.mrerp_conclusion_conflict`、`erp.mrerp_poll_stale` 修前仍各 0/10、修后（含 ERP 单据收紧）各 10/10。实验格换的替身：账本判定 `handles`（建草稿时照实验站原样、领取起按 MR.ERP）、准备、凭据、账号占用、共用层收款导入、写后按客户重读、告诉录入的人。撤回 / 结果不明那几种只在离线验（实验格没造巡检超时）。跑完 ERP 栈挂在本工作树（与 HEAD 同一份应用代码）。

#### 记结论遇到意外状态的口径（主会话 11-28 定，de4204022）

- **口径**：导入发出之后记结论时这张 / 这笔已不是本次领取——①现值是结果不明（巡检转的）、导入其实写进了 → 照实记已写入与单号；②作废（单据）/ 撤回（收款）而导入写进了 → 「写进去后作废 / 撤回」转人工、单号留着；③别的意外状态、而导入写进了或结果不明（可能写进了）→ 转人工、单号留在单据上、记一条错误日志，同一笔之后不再自动导入。确定没写进去的以别人的结论为准；已停在结果不明的不再改。a1a52f5f4 里③原来是「不盖、只记日志」，MR.ERP 里会多一张没人看得见的单，改掉。
- **改法**：③沿用现有「结果不明」那种转人工（不新造状态、不新造文案，页面 / CC 照现有「结果不明」那句）：ERP 单据记 `held` + `mrerp.result_unknown: ERR_MOVED_DURING_IMPORT`、`ledger_needed` 清空、写进了的 `ledger_docnum` 记 MR.ERP 单号；收款记 `failed` + 同一原因、`docnum` 记 FR 单号。错误日志带原状态与单号。收款撤回（`collection.mrerp.withdraw`）：失败却记着单号的同已写入一样按「写进去后撤回」，不当作没写过（原来撤回结果不明的那笔不留单号）。码常量 `mrerp.contracts.MOVED_DURING_IMPORT`（原因码后缀，不显示给用户）。
- **核实各入口不再导入**：ERP 单据结果不明时 `mrerp.push.send` 回 held（人点的「再写一次」`express.api` 与 CC 推送工具都经它，`api._mrerp_retryable`、`cc_tools.push_again` 也排除结果不明）；巡检只列排队中；选一次 `answer` 停在单据上的项已清空、认不得任何答案；`printed` 放行只认票面合计那几个原因；`precheck` 续推只认要人选；任务 `_claim` 只领排队中。收款：巡检只列待写入 / 写入中、`_claim` 只领待写入、失败的没有再放行的入口（退回草稿只在 Express 写账回执那条）。
- **Express 同形状只读核（没改，交主会话定）**：`express.push.job_changed`（电脑写账回执）写进去时按回执照写，单号总在单据上；作废了的转「写进去后作废」（同本条②）；断开时没写成的标已断开；写成了而单据已被改成别的状态时照记已写入 + 单号（不转人工）。状态已是已写入，`send` / 巡检 / 再写一次都不再交写账件，不会写第二张；和 MR.ERP 的③差在记「已写入」而不是「转人工」。另：ERP 单据这条回执不核「回执来自的写账件是不是单据上现在记的那件」（收款 `collection.push.job_changed` 核了），被换掉的旧件迟到的回执会盖到单据上。电脑写账的件按单据请求号去重、换件前撤旧件（领走的撤不了，按回执处理），没造出写两张的路径，没改。
- **离线**：test_erp_mrerp_direct 改 1 条、新增 1 条（写进了 / 结果不明 × 被改成要人选 → 结果不明 + 单号留着 + 一条日志；之后 send 回 held、选一次不收、巡检不排、再跑任务不导入）；test_erp_mrerp_collection 改 1 条（写进了 / 结果不明 × 被改回待写入 → 失败 + 结果不明 + FR 单号 + 一条日志；巡检不排、任务领不到；撤回照「写进去后撤回」、单号留着）。修前（b4-before）与 a1a52f5f4 的写法上都红。test_erp_mrerp_direct 56、test_erp_mrerp_collection 41、test_erp_express_direct 224、test_erp_mrerp_stock、test_erp_write_precheck、test_erp_mrerp_onboarding、test_erp_cc_companies、test_ledger_instant_sync、test_snapshot_reset、test_conflict_catch_sites 过；四个检查脚本过、`--extended` 新增 0、`check-impact.py --base 4084c210e` 登记表完整。
- **实验栈**：③走不到正常路径（实验格造的是本次领取仍在时的冲突），没加新格；挂 de4204022 后三格各 10 轮重跑全过（`erp.mrerp_conclusion_conflict`、`erp.mrerp_poll_stale`、`erp.mrerp_collection_conclusion_conflict`），ERP、Cowork 两套栈都挂回本工作树。

### B7 Cowork 对账出结果撞冲突：判失败、删源文件，重交要再付识别费（`recon_center/jobs._run`）

- **现象**：对账后台任务读两边文件时，识别按页存检查点、每次自己提交，识别任务名由对账号定（`document_ocr.reader`）。出结果那段（`create_from_documents` / `create_from_parsed` / `vat_check.run` 落任务与结果行）撞死锁或保存前比对时进 `except Exception`：回滚、记「系统繁忙」、对账判失败，`finally` 里 `_cleanup` 删掉两边源文件。用户只能重新上传 → 新对账号 → 新识别任务名 → 识别再付一次。
- **改法**：`except Exception` 回滚后先判 `conflicts.is_conflict(error)`：是冲突且参数里的挂起次数（`options.conflict_retries`）没到 `CONFLICT_RETRIES`（5）就抛 `_Conflicted`（`waiting.Pending` 的子类，`due` = 现在 + 5 秒），`parked` 置真挡住 `_cleanup`：源文件、进行中清单、页面「进行中」都留着。`run` 接到 `_Conflicted` 先把参数里的次数加一，再走现有 `waiting.park`（记下的参数就是放回队列用的参数，次数跟着走）；每分钟的 `waiting.tick` 到时按原参数放回队列，下一轮从头跑，识别按对账号找回已存的结果、不再付费，结果按对账号幂等（`TASKS.identity` / `vat_check_service.create` 同号回放），不会建两份。次数用尽照原来失败（记日志、「系统繁忙」、删源文件）。答预设问题要用的 `rerun.keep` 参数里不带这个次数。非冲突错误照旧当场失败。
- **核实**：挂起路径 `parked = True` 后 `finally` 不跑 `_cleanup`（与等识别的挂起同一条路）；`waiting.park` 存的 `arguments` 原样交给 `jobs.requeue` → `run(**arguments)`，`options` 里多一个键不改签名；已记下的旧等待没有这个键按 0 算。实际再跑的间隔由 tick 决定（最长约 1 分钟），5 次约 5 分钟内用尽。
- **离线**：新增 `tests/test_cowork_recon_result_conflict.py` 5 条（真 `jobs.run` / `_run`，识别、存储、状态缓存、名额、`waiting.park` 换替身）：出结果撞一次 QueryDeadlockError → 挂起、次数 1、源文件不删、不判失败、不记日志；按记下的参数再跑 → 出结果、源文件删、识别仍只付 2 次；次数用尽 → 照旧失败、删文件、记日志；连撞到上限每次加一、识别全程 2 次；数据问题当场失败；预设重跑参数不带次数。修前（b4-before）关键那条红（`0 != 1`，没挂起、判失败），另外 4 条因没有新常量 / 预设参数带次数报错或断言不过。已接进 `scripts/ci-candidate-checks.sh` 的离线清单。修后同镜像里 test_cowork_recon_waiting 21、test_cowork_recon_failure_text 3、test_cowork_recon_resume 13、test_cowork_recon_slots 6、test_cowork_recon_presets 41、test_cowork_recon_scanned_docs 125 过；check-architecture、check-unbounded-queries、check-conflict-catch、check-test-coverage、check-unlocked-overwrite --extended（新增 0）过。`jobs.py` 新调 `conflicts`，登记进 transaction 件的入口 `cowork.recon.upload`（并把 `jobs.py` 记进该入口的文件），`check-impact.py` 登记表完整。基线里没有这一处的行，未删行。

| 格 | 修前（b4-before = 24e3f1e3f） | 修后（本节提交） |
|---|---|---|
| `cowork.recon_result_conflict`（收入对账真跑 `jobs.run`；两份真私有 File；识别替身按「对账号 + 哪一份」记检查点；写结果行前另一路先改 8 张别的票、锁住结果行主键空隙、再去锁刚插入的任务行，结果行一插入就是真 1213，回滚出结果那笔） | 0/10（判「系统繁忙，对账未完成，请稍后重试。」，两份源文件当场删掉，识别已付 2 次） | 10/10（挂起、次数 1、源文件都在；按记下的参数再跑一轮出结果、结果行在、源文件这才删，识别全程 2 次；10 轮都是出结果那笔被回滚，竞争真实） |

实验格换的替身：识别读取（不调真识别、不花钱）、读出的总账 / 税报直接回合成表格、名额（slots）；记下等待用真的 `waiting.park`（队列 Redis），记下后格里马上取走（`waiting.drop`），不让实验站的 tick 把它放回真队列，再由格里按记下的参数自己跑下一轮（代替 tick → wake → 队列）。没在实验栈验 tick 真放回队列、long 进程接着跑这一段（`test_cowork_recon_waiting` 离线守着）；银行、销项核查两种对账走同一个 `except`，只在收入对账上造了冲突。跑完测试数据（任务、结果行、File、压舱行、等待记录）为 0。

### Express 写账回执只认单据现在那件（`express/push.job_changed`，主会话 11-28 定）

- **现象**：CC 桌面版写完 Express 回来的回执（`jobs.acknowledge` → `products.job_changed` → `push.job_changed`）不核「这是不是单据上现在记的那件写账件（`ledger_job`）」，回来哪件就把哪件的状态、件名、单号盖到单据上：被换掉的旧件迟到写成，单据记成「已写入 + 旧件」，现在那件仍排着、电脑照领照写，Express 里多一张；旧件别的回报（收回排队、转人工、撤掉）也把 `ledger_job` 改回旧件。现在那件写成时单据已被改成别的状态（失败、已断开、已手工处理、清空、已记着另一张单号），照记「已写入」。读单据是普通读（回报那笔事务前面已有普通读，关快照隔离后看旧值）。
- **改法**（e7424db7a）：
  - 回执开头加锁读单据（`status`、`ledger_state`、`ledger_job`、`ledger_docnum`、`ledger_reason`）。单据还没记上件的（刚交进队列那一次，`_send` 先 `enqueue` 后记件）照旧算现在那件。
  - 不是现在那件、或单据已因迟到写成转了人工的：没写成的回报不盖（现在那件为准）；写成的转人工（`_hold_written`）。
  - 现在那件写成、而单据已不在等它（`_awaited` 为假）：同样转人工。在等 = 排队中 / 写入中；停下且不是本条的原因（这件自己转人工后补报写成：结果不明 `LOST`、代录半截单号——共用通道只收这几种迟到的成功；单据有件时别的路径不会把它改成停下，`_send` 见有件就退出、答题与再写一次都先撤件）；已写入且单号相同（回执重放）。
  - 转人工沿用 MR.ERP 第三种（de4204022）的处理：`held` + 结果不明码，件名与单号记在单据上，记一条错误日志（`ERP Express write landed after the document moved on`，带原状态、原来那件、两边单号）。码是共用通道「结果不明」（`cc_desktop.jobs.LOST`）开头、小写后缀：`contracts.MOVED_DURING_WRITE = "cc_desktop.result_unknown:moved_during_write"`。不用 MR.ERP 那个 `mrerp.result_unknown: ERR_…`：它的文案写「去 MR.ERP 核对」，而大写带空格的码在 Express 单据页 / 待处理会原样露出；小写码不原样露出；原因怎么说、单据时间线记什么见下面「第二轮补充」，不新造状态。作废了的单照「写进去后作废」（`VOIDED_AFTER_WRITE`）；现在那件写成 × 作废照原路径不变（出入库单排原生作废、采购 / 销售转人工）。转人工时不挂对方档码、不排主档同步（写进去的是旧载荷，交人核）。没给 CC 对话补一句「到点结论」（只有「录进 / 没录进」两种说法，都不对），记为已知。
  - **两张单号**：不加字段。写账件表（`Pearnly CC Job`）里每件自己记着 `docnum`、`status`、来源单据；单据的 `ledger_job` 指着最后写成的那件、`ledger_docnum` 是它的单号；被领走的现在那件之后也写成时，回执照同一处再记一次（单据改指它与它的单号），旧件那张仍在旧件那一行与第一条错误日志里，第二条错误日志两张都带。单据页 Express 弹窗只显示一张；两张都记在单据时间线的原生评论里（第二轮补充）；写账件表只有系统管理员能看。
  - **领单兜底**（`still_wanted`）：加锁读多读 `ledger_job`，单据记着别的件时这件不交电脑（共用层照现有一支撤成 `voided`）。还没记上件的照旧要。
  - **已手工处理**（`api.mark_handled`，收银日汇总的出口）：原来对「不是 done 的件」一律撤，`done` 不是状态名，记着写成 / 已撤的件时撤件会被共用通道拒（`cc_desktop.job_busy`），按钮点了报错；改成只跳过 `success` / `cancelled`，电脑正在写的照旧拒。
- **核实：旧件被换掉时不会再被电脑领走写账**（依据都在修前代码里，本条另加 `still_wanted` 一道）：换件的路径都先撤旧件——再写一次 `api.py:429`、选对方档 `api.py:358`、已手工处理 `api.py:279`、代录再录 `push.py:385`、断开 `push.py:611`、作废 `push.py:648` 与 `_cancel_unwritten`（`push.py:666`，加锁按来源单据撤全部排队中 / 转人工的）；`jobs.cancel` 只撤排队中 / 转人工（`jobs.py:178`），领走的撤不了、换件那笔整笔失败；结果不明的「再写一次」是同一件重排（`api.py:426`，`jobs.retry`），不是换件。`_send` 锁里见到没撤销的件就接回它、不再排（`push.py:535–540`）。撤掉的件不会再被领：排头只取排队中，领单时加锁重读、不是排队中就跳过（`jobs.py:403`）；也收不进回执（`jobs.py:548–550`：只认本机领着的、本机钉着的结果不明、本机代录半截补报）。领单前 `still_wanted`（`push.py:714`）加锁读：作废的、单据记着别的件的都不交（`jobs.py:418` 撤成 `voided`）。所以按现有路径造不出「旧件被换掉后还写成」：迟到的回执只可能来自没被撤、仍被领着或钉着的件，本条是兜底，实验格靠直接改库造出这种状态。按 MR.ERP 账本改道时（`_send` 走 `mrerp_push.send`，不动 `ledger_job`）若 Express 那件正被领着，写成后现在落在「单据已不在等它」那支转人工——没在实验栈造过。
- **核实：转人工之后不会再写第二张**（逐个入口，同 MR.ERP 那条）：单据停在 `held` 且记着写成的件——`_send`（网页「再写一次」之后的再交、CC 推送工具、代录交单、答题后、提交后的后台一轮都经它）见有件回 `already_queued`；巡检 `poll` 只交没件的排队中 / 会自己好的原因，有件的只更新排队说明；「再写一次」`api.retry` 只认转人工的件（收银日汇总那支只认没件或已撤的件），回无权限；选一次 `api.answer` 有件且不是被拦的回无权限；单据页 `can_retry` / `can_answer` 为假；代录再录 `release_operator` 只认转人工的代录件；写账前判定 `precheck.document_view` 见件没撤回「已在写账件里」；`printed.set_total` 只放行票面合计那几个原因；`requeue_unmapped` 只放仓库没对上的。还排着的现在那件 / 收回的旧件，领单前被兜底撤掉；被领着的那件写成就照上面再记一次，没写成转回排队的同样领单前撤掉，转人工的不会被领、也没有入口重排（单据记着的不是它）。作废这张单：转人工的单还在打开状态里，作废撤掉没写的件，记着写成的件不清，单号留着（出入库单不排原生作废，交人在 Express 里处理）。
- **CC 桌面版约定**：没动（回执字段、取值、领单返回都不变），`tests/test_companion_contract.py` 142 条照过；旧版桌面版的回执走同一条。
- **离线**：test_erp_express_direct 新增 `StaleJobReceipt` 6 条（真 `jobs` 表、撤件与领单兜底接回真 `push`）：旧件写成晚到 × 新件排着 → 转人工、旧单号留着、`ledger_job` 指旧件、新件不动、一条日志，之后 `send` / `poll` / `release_operator` / `api.retry` / `api.answer` / 单据页按钮 / 写账前判定 / 领单兜底都不再写，领单撤掉新件后结论不变；新件被领着也写成 → 两张单号各在各件、单据记新件、两条日志；旧件的排队 / 转人工 / 撤掉回报不盖现在那件、旧件不再交电脑；现在那件写成 × 单据已失败 / 已断开 / 已手工处理 / 清空 / 已记另一张单号 → 转人工；排队中 / 写入中 / 结果不明 / 代录半截 → 照记已写入、刚交进队列那一次照旧记件；作废的单 × 旧件写成 → 写进去后作废。`PosDayExits` 加 1 处（记着写成 / 已撤的件时已手工处理不撤件）。修前（只补常量）4 条红（盖成已写入、`ledger_job` 被改回旧件）+ 已手工处理那处红，另 2 条守不变的行为修前也绿。test_erp_express_direct 230、test_cc_desktop_channel 60、test_companion_contract 142、test_erp_cc_chat_push 40、test_erp_collection 91、test_erp_mrerp_direct 56、test_erp_mrerp_collection 41、test_erp_mrerp_stock 12、test_erp_write_precheck 34、test_erp_cc_companies 46、test_express_operator_code_cc 51、test_ledger_instant_sync 14、test_erp_stock_entry_guide 26、test_opening_batch 23、test_conflict_silent_loss 18、test_snapshot_reset、test_conflict_catch_sites 过；test_erp_default_company_warehouse 本机缺 frappe，修前树上同样跑不起来，没测到。check-architecture、check-unbounded-queries、check-conflict-catch、check-unlocked-overwrite --extended（新增 0）、check-copy（新增 / 改动 0）过。基线里没有这一处的行，未删行。`check-impact.py --base 64b991eae` 命中共用件：无（改的是 ERP 自己的 `express/push.py`、`api.py`、`contracts.py`），影响入口 `erp.cc.write`、`erp.cc.stock-void`、`erp.mrerp.direct`、`erp.entry.choices` 只做了离线与实验格，没在测试机实测。

| 格 | 修前（b4-before = 24e3f1e3f） | 修后（e7424db7a） |
|---|---|---|
| `erp.stale_job_receipt`（每轮一张采购单、两件真写账件。A：旧件 J1 被领着，本连接是电脑回报 J1 写成那笔事务、读视图先建好，另一路把单据换成新件 J2 排上并提交，本连接记 J1 写成；B：J2 被领着，另一路把单据改成失败并提交，本连接记 J2 写成） | 0/10（A 记成已写入 + J1、J2 照样交电脑（兜底说要）；B 照记已写入） | 10/10（A 转人工、J1 单号留着、J2 仍排着但领单兜底不要、再交不排第三件；B 转人工、J2 单号记上；两张单号各在写账件表自己那一行） |

实验格换的替身：主档同步（`masters.queue / forget`）、挂对方档 / 存货码（只修前会走到）；回执直接调 `push.job_changed`（写账件行先改成 success + 单号，同 `jobs.acknowledge` 记成功后交回产品那一份），没走电脑的 `acknowledge` 网络入口；建单时把公司临时设成不接 Express（不让后台自动排件），跑完复原。巡检、再写一次、选一次只在离线验。跑完测试单据作废、写账件与错误日志清掉（核过为 0），ERP 栈挂在本工作树。

#### 第二轮补充（主会话 11-28）：时间线评论与「结果不明」落档（70aa79a2f）

- **时间线评论**：照「已在 Express 手工处理」那条（`api.mark_handled`：`frappe.get_doc(...).add_comment("Comment", _(键))`，谁、何时由 Frappe 原生时间线带出，四语按记的那一刻的用户语言走术语表）。转人工（`MOVED_DURING_WRITE`）时 `_hold_written` 记一条；作废的（写进去后作废）不另记。旧 / 现在按写账件建的先后（`Pearnly CC Job.creation`，单据上记着的那件 vs 这次写成的这件）：只有旧件写成 →「之前的写入已写进 Express：{旧单号}」；只有现在那件写成（单据被改过）→「单据变更后写进了 Express：{单号}」；单据上已记着另一张 →「两次写进了 Express：之前 {旧}，当前 {现}」。新增三条词条（`express.comment.writtenEarlier / writtenAfterChange / writtenTwice`，ERP 术语表四语，`i18n-build.py` 重新生成翻译包）。`check-copy.py --base 1fca58025 --report`：新增 3、改动 0、拦截 0、标黄 0（清单见本节末）。
- **「结果不明」一家落档**（口径：结果不明不得显示成失败；一家 = 共用通道收回后转人工 `cc_desktop.result_unknown`（`LOST`）与 `…:moved_during_write`，判断 `contracts.result_unknown`；词用共用层现成的 `guide.reason.last_write_result_unknown`「结果不明 / ไม่ทราบผล / 結果不明 / Result unknown」）：

| 入口 | 改前 | 改后 | 依据 |
|---|---|---|---|
| 单据页 Express 弹窗·原因栏 | 不显示（小写码一律隐去） | 结果不明 | `transactions.js:242` |
| 单据页 Express 弹窗·状态栏 | 失败 | 失败（未改，见下） | `p.ledger_word` |
| 列表 Express 列（采购 / 销售 / 出入库 / 收银小票） | 失败（写成的显示 Express 单号）；无原因悬浮 | 同左（未改，见下） | `purchase.js:69` 等 |
| 列表筛选 | 按 `ledger_state` 分档，`held` 一档叫「失败」 | 同左（未改，见下） | `purchase-fields.js:8` |
| 待处理（网页 / CC 右栏）原因 | 待处理 / 失败（`_BARE_CODE`） | 结果不明 | `pending.py:145` |
| 待处理 `reason_code` | manual（不是 failed） | 同左 | `pending.py:118` |
| LINE 回执 | 失败 · 待处理 | 失败 · 结果不明 | `line_cards.py:368` 经 `pending.reason_label` |
| CC 推送结果 / 推送工具（`_outcome`、`push_documents`） | 迟到写成的：failed；收回的：unconfirmed | 两种都 unconfirmed（不再推，带单号） | `cc_tools.py:176/261/336` |
| CC 列单 `result_unknown` | 只认收回的 | 两种都带 | `cc_tools.py` 列单 |
| CC 引导结果卡 | 迟到写成的：失败 | 留下 + 结果不明（unconfirmed → held） | `guide_documents.py:392` |
| 写账前判定（CC 引导 / 计划） | 迟到写成的：写不了·已在写账件里 | 结果不明那一档（同 MR.ERP 结果不明） | `precheck.py:382` |
| CC 核对工具 `erp_resolve_push_result` | 只收收回的（交原电脑核对） | 同左：迟到写成的回「没有要核对的」（单号已在单据上，交人在 Express 里处理） | `cc_tools.py` 核对 |

- **没改、要主会话 / 用户定**：单据页状态栏、列表 Express 列、列表筛选、LINE 回执开头的**状态词**仍是「失败」。状态词是用户 11-22 统一定的四个（录入中 / 成功 / 失败 / 已取消，网页列表、详情、Express 弹窗、LINE 回执同一张表，`contracts.LEDGER_WORDS` 与 `transactions.js` 契约测试比对），「结果不明」不在里面；MR.ERP 的结果不明今天也是状态「失败」+ 原因一句。要在这几处把状态词改成「结果不明」，是给全产品加第五个状态词、筛选加一档（新文案），超出本条授权，没做。收款（`collection.push`）的收回转人工没在本条核。
- **改了钉旧行为的断言**：test_erp_cc_companies `test_reason_labels_are_the_document_page_table` 原来断言 `cc_desktop.result_unknown` 说「失败 / 待处理」，按新口径改成「结果不明」（同时补 `…:moved_during_write`），其余小写码照旧。
- **离线**：test_erp_express_direct `StaleJobReceipt` 改 3 条、新增 1 条（时间线评论：旧件写成一条带旧单号；现在那件再写成第二条两张都带；被改过的单写成一条（单据原记另一张时两张都带）；作废的不记；「结果不明」一家的原因、写账前判定档、单据页映射与共用层四语词条在）；test_erp_cc_chat_push 新增 1 条（迟到写成的推送结论 unconfirmed、带单号、不再推、列单带 result_unknown）。在 1fca58025 上：评论 2 条、档位 1 条、推送 1 条红（另 1 条因没有 `result_unknown` 报错）。修后 test_erp_express_direct 231、test_erp_cc_chat_push 41、test_erp_cc_companies 46、test_erp_collection 93、test_erp_mrerp_direct 58、test_erp_mrerp_collection 43、test_erp_pending_guide 13、test_erp_guide_documents 9、test_erp_write_precheck 34、test_line_receipt 21、test_stop_exits 11、test_check_copy 77、test_cc_desktop_channel 60、test_companion_contract 142 等过；check-architecture、check-unbounded-queries、check-unlocked-overwrite --extended（新增 0）、check-conflict-catch、check-stop-exits、i18n-build --check 过；`node --check transactions.js` 过。
- **共用件**：`check-impact.py --base 1fca58025` 命中 `erp-transactions`（改了 `transactions.js` 单据页 Express 弹窗原因那一行）24 个入口：没在浏览器里逐个实测（只改了弹窗原因栏取词的一个分支，其余入口不经过它）——**没测到**，合入前要按闸门补。
- **实验格**：`erp.stale_job_receipt` 加断言「A 后时间线一条只带旧单号、B 后第二条两张都带」。

| 格 | 修前（1fca58025） | 修后（70aa79a2f） |
|---|---|---|
| `erp.stale_job_receipt`（同上；加时间线评论断言） | 0/10（转人工、单号、件表都对，单据时间线没有评论：两张单号只在写账件表与错误日志里） | 10/10（两段转人工、单号、件表同上；时间线两条评论，第二条两张单号都在）；测试单据、写账件、错误日志、评论清理后为 0 |

`check-copy.py --base 1fca58025 --report` 新增文案清单：

| 键 | 中文 | English | ไทย | 日本語 |
|---|---|---|---|---|
| express.comment.writtenEarlier | 之前的写入已写进 Express：{0} | Written to Express by an earlier entry: {0} | รายการก่อนหน้าบันทึกเข้า Express แล้ว: {0} | 以前の書き込みが Express に記録されました：{0} |
| express.comment.writtenAfterChange | 单据变更后写进了 Express：{0} | Written to Express after the document changed: {0} | บันทึกเข้า Express หลังเอกสารเปลี่ยนแปลง: {0} | 書類の変更後に Express に書き込まれました：{0} |
| express.comment.writtenTwice | 两次写进了 Express：之前 {0}，当前 {1} | Written to Express twice: earlier {0}, current {1} | บันทึกเข้า Express สองครั้ง: ก่อนหน้า {0} ปัจจุบัน {1} | Express に2回書き込まれました：以前 {0}、現在 {1} |

### ④ code-review（medium，24e3f1e3f...64b991eae）六条处理

修前对照树 `b4r-before`（= 3aa7c4838，本轮起点；24e3f1e3f 上还没有账套串行锁与这几处写法，不能当这几条的修前）。

1. **账套串行锁等不到被当成「请求无效」**（`collection/snapshot.py`，676c3c1d3）。现象：拿着某本账套快照串行锁的进程被杀，锁 120 秒才过期；`Busy` 继承 `ValidationError`，CC 交写后回执（`push.job_changed` → `apply_balances`）时机器通道 `machine._run` 把它映射成 400「请求无效」，CC 不退避重来，收款停在写入中；MR.ERP 读未收单落快照时当成读失败；列发票先 `land_ready`、再 `request`，各等 30 秒。改法：`Busy` 改继承 `conflicts.Exhausted`（机器通道照已有约定回 503「忙」，CC 下一轮再交同一份回执；网页 `conflicts.attempt` 回「请稍后重试」），共用层 `machine.py` / `conflicts.py` 没动，CC 桌面版约定不变（`test_companion_contract` 142 条照过）；`land_ready` 等不到回「正在读取」，`open_invoices` 这次不再进 `request` 等第二遍；MR.ERP `_read` 等不到回「正在读取」，`run_read` 把这一态只记 30 秒（过后再问就重读），不当失败。`check-impact --base 3aa7c4838` 命中共用件：无。
2. **「再写一次」普通读盖掉「正在导入」**（`mrerp/push.send`，520abd469）。现象：`send` 的读视图早于任务领取提交，读到「排队中 + 空原因」，把原因改回空、又排一次导入；导入回来 `_conclude` 判不是本次领取，转成结果不明。改法：`send` 开头加锁读现值再判再写（`answer` → `send` 同受益）。
3. **作废 × 写入核不上丢单号**（`mrerp/push._conclude`，1e2c15d60，原有问题）。现象：导入期间作废、导入成了但合计核不上（mismatch，原因空），落进「清账本栏」那支，MR.ERP 单号丢了。改法：作废分支用已算好的 `written`（含 mismatch），一并转「写进去后作废」、单号留着。收款一侧核不上仍记已写入、原因带码，不受影响。
4. **Cowork MR.ERP 选项缓存整份写回**（`connections/service.observe_mrerp_menus / observe_mrerp_masters`）：**不是真问题，没改**。两处都经 `_read` = `connections.state.read`，默认 `lock=True`：先 `get_value(for_update=True)`、再 `get_doc(for_update=True)`（锁定版 Frappe `document.py` 走 `SELECT … FOR UPDATE`，实验栈镜像核过），读到的是最新整份、合并本账套那一格后写回，与 ERP 侧 A2 #17 改后的写法一样。runner 的 `_remember_menus` 在导入之后的新事务里只锁这一行连接，没有第二把锁、也不整份 `save`，撞不出 1213 / 保存前比对；它本来就是只给选择入口用的缓存、出错回滚记日志，不包 `retried`。
5. **MR.ERP 读未收单落快照撞 1213 记失败**（`collection/mrerp._read`，69d60ebf7）。改法：落库那段（`snapshot.replace` + 提交）包 `conflicts.retried`，只重来落库，不再登录 MR.ERP 读一次；用尽照旧当读失败。`_replace` 两段加锁的顺序没改：按客户那段要先加锁读才知道这位客户名下有哪些单号，合成一次升序锁得先多锁整本账套的行（加锁读会锁住扫过的每一行），代价比偶发 1213 + 重来大；网页 / LINE / CC 当场落与每分钟落的路径原来就在 `retried` / `attempt` 里。
6. **LINE 收单取件后记原件抛别的错留孤儿**（`integrations/intake._fetch`，e9d49ea96）。改法：`_keep_source` 抛冲突以外的错（校验失败、1205 等锁超时）时先回滚、`_forget` 删掉已单独提交的 File 登记（提交后删盘上那份、排任务删存放处那份），再原样抛。

- **离线**（每条修前红、修后绿）：test_erp_collection +2（写后回执遇账套锁被占抛「用尽」而不是 `ValidationError`；列发票只等一次锁、两件读回都还在）；test_erp_mrerp_collection +2（落快照等不到锁 → 正在读取、只记 30 秒、不记日志；落快照撞一次 1213 → 只重来落库、MR.ERP 只读一次）；test_erp_mrerp_direct +2（旧读视图上的「再写一次」回已在排队、不再排导入、记成已写入；作废 × mismatch → 写进去后作废、单号留着）；test_line_intake +1（记原件抛校验错 → 删登记、原样抛；在实验栈镜像里跑）。另跑过：test_erp_collection 93、test_erp_collection_line 70、test_erp_collection_cc 42、test_erp_collection_guide 22、test_erp_mrerp_collection 43、test_erp_mrerp_direct 58、test_erp_mrerp_stock 12、test_erp_express_direct 230、test_cc_desktop_channel 60、test_companion_contract 142、test_erp_write_precheck 34、test_snapshot_reset、test_conflict_catch_sites、test_conflict_silent_loss、test_ledger_instant_sync、`test_line_intake` + `test_cc_intake` 171；check-architecture、check-unbounded-queries、check-conflict-catch、check-unlocked-overwrite --extended（新增 0）、check-copy（新增 / 改动 0）过。基线里没有这几处的行，未删行。

| 格 | 修前（b4r-before = 3aa7c4838） | 修后 |
|---|---|---|
| `erp.snapshot_busy_book`（本进程直接拿住这本账套的串行锁不放、等锁改 2 秒。一半轮次：CC 写后回执经真 `machine._run`，判回 503、锁放了再交一次记成已写入；另一半：整本与按客户两件读回都已读好时列发票，判只等一次锁、说正在读取、两件读回都还在） | 0/10（回执回 400「请求无效」） | 10/10（676c3c1d3） |
| `erp.mrerp_resend_while_importing`（任务领取提交「正在导入」后、导入替身停住，本连接以领取前的读视图调 `send`） | 0/10（`send` 回 sent、又排一次导入，导入回来转成结果不明 `ERR_MOVED_DURING_IMPORT`） | 10/10（520abd469：回已在排队、不排导入、记成已写入 + 单号） |

回归（挂 e9d49ea96，各 3 轮，全过）：`erp.snapshot_double_land`、`erp.snapshot_balances_stale`、`erp.snapshot_busy_book`、`erp.mrerp_conclusion_conflict`、`erp.mrerp_poll_stale`、`erp.mrerp_collection_conclusion_conflict`、`erp.mrerp_resend_while_importing`、`erp.collection_edit_vs_release`、`erp.collection_double_release`、`erp.line_intake_save_conflict`、`erp.cc_intake_save_conflict`、`erp.stale_job_receipt`；`cowork.mrerp_ack_conflict`、`cowork.recon_result_conflict`、`cowork.folder_push_vs_human`。没测到：MR.ERP 读未收单等锁 / 撞 1213（第 1、5 条那一支）只做了离线，实验站没有 MR.ERP 公司与连接；第 6 条只做了离线；CC 桌面版真机收到 503 后的退避没在真机上看。ERP、Cowork 两套栈都挂在本工作树。

#### code-review 小表

| 发现 | 真问题 | 修了 | 耗时 |
|---|---|---|---|
| 1 账套锁等不到当成请求无效 / 等两次 / MR.ERP 读当失败 | 是 | 是 | 约 17 分钟（17:46–18:03，6 条合计，含两个实验格对比与回归） |
| 2 再写一次普通读盖掉正在导入 | 是 | 是 | 同上 |
| 3 作废 × 写入核不上丢单号 | 是 | 是 | 同上 |
| 4 Cowork 选项缓存整份写回 | 否（已是加锁读） | 否 | 同上 |
| 5 落快照两段加锁、MR.ERP 读无重试 | 部分（无重试是真的；加锁顺序不改） | 加重试 | 同上 |
| 6 记原件抛别的错留孤儿 | 是 | 是 | 同上 |
