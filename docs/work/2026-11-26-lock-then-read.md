# 关快照隔离后的并发冲突：加锁后读最新值 + 保存前比对同样重来（11-26）

- 分支 `pearnly/isolation-timestamp-retry`，基线 `origin/main` = `f18fa1ec8`。主会话 local_93ac5c59 11-26 派（合入列车窗口施工），用户口径：「虽然没有真实用户，但是并发还是为未来考虑，建议处理好」。
- 起因：train-1125 清卷全量里 `erp.purchase.voucher-concurrent`（8 张采购单 × 补开收据 / 预扣税凭证同时出、外加重复点，共 18 个请求）偶发一个 417 `TimestampMismatchError`。
- 前因：11-23 `4380c28ab` 给所有 MariaDB 加 `--innodb-snapshot-isolation=OFF`（[记录](2026-11-23-snapshot-isolation-off.md)），线上还没部署（生产仍是隔离 ON）。

## 证据：是关隔离带出的（同一回归实验室、同一份数据，每轮 2 格）

| 源码 | 隔离 | 结果 |
|---|---|---|
| 线上版 `d8510e61a`（实验室按它的定义重建 db，默认 ON） | ON | 10/10 过 |
| main `f18fa1ec8` | OFF | 3/10 过（7 格 417 TimestampMismatchError） |
| train-1125 `59b87b82e` | OFF | 3/10 过 |
| main `f18fa1ec8` 代码 + 覆盖文件把 db 改回 ON | ON | 10/10 过 |

11-20 以前各次全量这条都过，11-25 起不过，与 `4380c28ab` 进 main 吻合。

## 机制

`purchasing/output._attach_voucher` 在 `conflicts.retried` 里：`access.get_document(lock=True)` 先 `guard.require`（读了角色——普通读，建立读视图），
再 `select … for update`，再 `frappe.get_doc` **普通读**。

- ON：那次加锁读碰到读视图之后被别人改过的行，报 1020 → `retried` 整笔回滚重来 → 过。
- OFF：加锁读通过、拿到最新行；但随后普通读仍读读视图里的旧副本 → `doc.save()` 里 Frappe `check_if_latest`（`get_doc(for_update=True)` 加锁读出最新修改时间）
  对不上 → `TimestampMismatchError`，不在 `retried` 范围 → 417 给用户。数据没丢（比对挡住了）。

锁定版 Frappe（16.33，回归实验室镜像核过）：`get_doc(..., for_update=True)` 父行 `SELECT … FOR UPDATE`，**子表也带 `FOR UPDATE`**（`Document._load_child_table_from_db`），
所以「加锁读整张单」读到的是父表与子表的最新值，没有子表被旧副本整份写回的问题。

## 并发下数据库 / 框架会抛的冲突，各自怎么处理

| 冲突 | 什么时候 | Frappe 抛成 | 处理 | 现在落在哪 |
|---|---|---|---|---|
| 1020 `ER_CHECKREAD` | 隔离 ON：读视图之后加锁读 / 改到别人改过的行 | `QueryDeadlockError` | 整笔回滚、退避重来（最多 4 次），用尽 `Exhausted` | `conflicts.retried`；OFF 后基本不再出现 |
| 1213 `ER_LOCK_DEADLOCK` | 两笔事务互相等锁 | `QueryDeadlockError` | 同上（InnoDB 已回滚整笔） | `conflicts.retried`；另有各入口原有的 68 处捕获（11-23 记录第 5 条）照旧 |
| Frappe 保存前比对 `TimestampMismatchError`（抛出位置 `frappe/model/document.py` `check_if_latest`） | 隔离 OFF：加锁后普通读到旧副本再保存（与 ON 时的 1020 同一种冲突） | `TimestampMismatchError` | **本次起**同 1020：整笔回滚、退避重来，用尽 `Exhausted`；同时把根子去掉：加锁后的读改成加锁读（下节） | `conflicts.retried._stale_save`（只认这个抛出位置） |
| 业务自己抛的 `TimestampMismatchError`（「记录变了、请刷新」：Cowork 推送的版本号核对、`history.storage.conflict`） | 页面拿着旧版本号提交 | `TimestampMismatchError` | 如实报给用户（重来也没用；入口靠它给原来的提示） | 原样抛出，不重来 |
| 1205 等锁超时 | 等锁超过 `innodb_lock_wait_timeout` | `QueryTimeoutError` | 如实报给用户（已经等满了整个等锁时间，再等只会更久） | 不重来；`assistant.sessions` 等入口照原有形状接住 |
| `DocumentLockedError` | Frappe 文档锁（`doc.lock()`，后台任务占着单据） | `DocumentLockedError` | 如实报给用户 | 产品代码没用 `doc.lock()`，没有入口会碰到 |
| 1062 重复键 | 两边同时插同一个唯一键 | `DuplicateEntryError` / `UniqueValidationError` | 各入口按业务处理（回到保存点改更新、视为已存在、报重复） | 各入口原有处理（`line_memory.learn`、`notices`、`push`、`companies.service`、`companion.devices` 等），不统一重来 |

## 调用点盘点（全仓 apps/，排除 tests / patches；只读子代理 + 人工核对）

「同一事务里先普通读 → 加锁 → 普通 `get_doc` → 写」改成加锁读（`get_doc(..., for_update=…)`），不论在不在 `retried` 内：

| 产品 | 位置 | 调用方（是否在 `retried` 内） | 改法 |
|---|---|---|---|
| ERP | `purchasing/access.get_document(lock=True)` | 出凭证 `_attach_voucher`（在）；`entry._post` / `cancel` / `payment`、`files.add` / `remove`（不在） | `get_doc(..., for_update=lock)` |
| ERP | `purchasing/service.set_status` | 网页提交 / 作废（不在） | `_get(name, lock=True)` |
| ERP | `sales/access.get_document(lock=True)` | `entry._post` / `cancel` / `payment`、`files.add` / `remove`（不在） | `get_doc(..., for_update=lock)` |
| ERP | `cashier/sales.void_sale` | 收银作废（不在） | `_get(name, lock=True)` |
| ERP | `inventory/documents.delete_draft` / `submit_entry` / `void_entry` | 库存单网页、盘点生成调整单（不在） | `_get(name, lock=True)` |
| ERP | `integrations/intake._owned(lock=True)` | 网页从卡片打开提交、CC 引导就地改（不在） | `get_doc(..., for_update=lock)` |
| ERP / Cowork | `work/service._card(lock=True)`、`update_list` / `archive_list` / `restore_list` | 看板（不在） | `get_doc(..., for_update=…)` |
| Core | `express_operator/tasks._own(for_update=True)` | 代录任务各步（部分在：`message_sent`、经 `sessions.move` 的 `update`） | `get_doc(..., for_update=for_update)` |
| Core | `line_session._row(for_update=True)` / `set_session` | LINE 会话写入（不在；出错原本就被吞成 warning） | 两处都加锁读 |
| Core | `cc_desktop/jobs.acknowledge`、`requests.complete`、`devices.register` | CC 桌面版机器方法（在） | `get_doc(..., for_update=True)` |
| DMS | `operators/endpoints.sync_operator`（锁的是操作员行，读连接行） | 操作员保存钩子、账号保存（不在） | 连接行也加锁读 |
| Control | `onboarding/welcome.queue_for_request` | 开户发欢迎信（不在） | `get_doc(ISSUE, …, for_update=True)` |
| Cowork | `files/access.protected_sources(include_hash=True)` | 原件删除保护（不在） | 锁住了就加锁读 |
| Cowork | `history/removal.delete` | 删收单记录（不在） | `get_doc(..., for_update=True)` |

已经是加锁读、不用改（父表与子表都最新）：ERP `collection/service._own(for_update=True)`（改 / 放行 / 撤回 / 丢弃草稿）、`inventory/serials.turn_on` / `_unit`、
`masters/company_notes`、`mrerp/connection`、`inventory/count`；Core `assistant/guide/run._locked`、`cc_desktop/jobs` 其余、`requests`、`devices` 其余；
DMS `account/api`；Control `billing`、`billing_api`、`provisioning.queue_*`、`account/repool._queue`；Cowork 各 `read(..., for_update=True)`。
加锁后只读、与锁无关的三处标 `# lock-read-ok`：Control 欢迎信的发信账户核对、Cowork 对账重放只读回状态、ERP 收款放行后读本事务刚存的那张出卡。

不在 `retried` 内的入口为什么不再一个个包进去：根子（加锁后读旧副本）已在上表全部去掉，加锁读串行之后同一张单不会再撞保存前比对；
真正的死锁（1213）各入口原有处理照旧，与隔离开着时一样；包重来要逐个核对入口有没有提交前的外部 IO（LINE、文件、推送），范围大、收益只剩「死锁时自动重来一次」，不在本次做。
`retried` 里接住 `TimestampMismatchError` 是兜底：跨函数的写法（加锁在调用方、普通读在被调函数里）检查拦不到时，经 `retried` 的入口仍与 ON 时同样处理。

## 闸门

- `scripts/check-lock-then-read.py`（新，`tests/test_check_lock_then_read.py`）：同一函数里加锁调用（SQL `for update`、`for_update=…` 关键字、qb `.for_update()`）
  之后出现不带 `for_update` 的 `frappe.get_doc(<doctype>, <name>)` 就失败；确认无关的行末标 `# lock-read-ok: 原因`。跨函数靠评审与 `retried` 兜底。
- `tests/test_transaction_conflicts_stale_save.py`（新）：保存前比对那一种重来、次数与提示清理同 1020，用尽 `Exhausted`；业务的「请刷新」原样抛出；去掉修法 2 项失败。
- 两项进 `development.yml`「Block unlocked reads written back…」那一步；规矩写进 `docs/development/architecture.md`（transaction 段）。

## 验证

（施工中，见下方追加）
