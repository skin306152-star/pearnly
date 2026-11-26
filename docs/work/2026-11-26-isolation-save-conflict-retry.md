# 关快照隔离后保存前比对的冲突同样重来（11-26）

- 分支 `pearnly/isolation-timestamp-retry`，基线 `origin/main` = `f18fa1ec8`。主会话 local_93ac5c59 11-26 派（合入列车窗口施工）。用户口径：「虽然没有真实用户，但是并发还是为未来考虑，建议处理好」。
- 起因：train-1125 清卷全量里 `erp.purchase.voucher-concurrent`（8 张采购单 × 补开收据 / 预扣税凭证同时出、外加重复点，共 18 个请求）偶发一个 417 `TimestampMismatchError`。
- 前因：11-23 `4380c28ab` 给所有 MariaDB 加 `--innodb-snapshot-isolation=OFF`（[记录](2026-11-23-snapshot-isolation-off.md)），线上还没部署（生产仍是隔离 ON）。

## 证据：是关隔离带出的（同一回归实验室、同一份数据，每轮 2 格）

| 源码 | 隔离 | 结果 |
|---|---|---|
| 线上版 `d8510e61a`（实验室按它的定义重建 db，默认 ON） | ON | 10/10 过 |
| main `f18fa1ec8` | OFF | 3/10 过（7 格 417 TimestampMismatchError） |
| train-1125 `59b87b82e` | OFF | 3/10 过 |
| main `f18fa1ec8` 代码 + 覆盖文件把 db 改回 ON | ON | 10/10 过 |
| 本分支（`conflicts.retried` 接住保存前比对） | OFF | 40/40 过（20 轮） |

11-20 以前各次全量这条都过，11-25 起不过，与 `4380c28ab` 进 main 吻合。

## 机制

`purchasing/output._attach_voucher` 在 `conflicts.retried` 里：`access.get_document(lock=True)` 先 `guard.require`（读了角色——普通读，建立读视图），
再 `select … for update`，再 `frappe.get_doc` 普通读。

- ON：那次加锁读碰到读视图之后被别人改过的行，报 1020 → `retried` 整笔回滚重来 → 过。
- OFF：加锁读通过；随后普通读仍读读视图里的旧副本 → `doc.save()` 里 Frappe `check_if_latest`（`get_doc(for_update=True)` 加锁读出最新修改时间）
  对不上 → `TimestampMismatchError`，原来不在 `retried` 范围 → 417 给用户。数据没丢（比对挡住了）。

## 修法

`transaction.conflicts.retried` 把 **Frappe 保存前比对抛的** `TimestampMismatchError`（抛出位置是锁定版 `frappe/model/document.py` 的 `check_if_latest`，
回归实验室镜像核过）与 1020 / 1213 同样处理：整笔回滚、退避重来、次数与提示清理相同，用尽抛 `Exhausted`。重来时是新事务、新读视图，读到最新单。
业务自己用 `TimestampMismatchError` 表示「记录变了、请刷新」（Cowork 推送的版本号核对、`history.storage.conflict`）不认、原样抛出——重来没用，入口靠它给原来的提示。
约定写进模块说明：被包的函数按名字自己读单据，不收外面读好的 `Document` 去 `save`（11-26 核过现有调用方都按名字读）。

离线 `tests/test_transaction_conflicts_stale_save.py`（去掉修法 2 项失败），进 `development.yml`。

## 试过又撤回的：加锁后改成加锁读（`get_doc(..., for_update=True)`）

同日先试过「全仓加锁之后的普通 `get_doc` 一律改成 `for_update=…`」（20 来处，外加一条静态检查），实验室最终头上反而更差，已整段撤回（`6ba30ca5e` / `67e5cf679` / `54d38b514` 的 revert）：

- `erp.purchase.voucher-concurrent` 8 轮 16 格全不过：变成 417 `LinkValidationError`。原因：加锁读读到了最新单（含别人刚挂上的附件行），但这笔事务的读视图早于锁，
  保存时链接校验（普通读）看不到别人刚建的 `File` → 报链接不存在。同一事务里「加锁读是最新值、普通读是旧视图」本身不一致，改读法只是把报错换个地方。
- 同单并发（下节）付款请求死锁更多：Frappe 16 的 `for_update` 连子表行一起加 next-key 锁，锁提前拿、拿得更多。
- 结论：关隔离之后，写事务要么**先加锁、后做任何普通读**（读视图从锁之后开始，普通读与锁一致），要么经 `retried` 在冲突时换新事务重来；不要在旧读视图里混用加锁读。
  写进 `docs/development/architecture.md`（transaction 段）。

## 另外发现的老问题（不是本次引入，单列）：同一张单并发写会死锁

新写的实验室流程（同一张采购单同时付款 / 作废 / 出凭证，4 张单 × 3 笔付款 + 2 张单各作废 + 付款 + 凭证）：线上版（ON）、main（OFF）、本分支，每版 3 轮 × 2 格**全部不过**，
都是付款请求 500 `QueryDeadlockError`。线上本来就有，与关隔离无关；第一次部署不会让它更差。修法要把采购 / 销售等写接口经「先结束读快照 + `retried`」的共用入口，
逐入口核对重来安全（幂等、提交前无外部 IO），属于改进项，另开分支做（流程文件留在 `~/pearnly-devtools/handoff-1126/erp_purchase_same_doc_concurrent.py`）。

## 并发下数据库 / 框架会抛的冲突，各自怎么处理

| 冲突 | 什么时候 | Frappe 抛成 | 处理 | 现在落在哪 |
|---|---|---|---|---|
| 1020 `ER_CHECKREAD` | 隔离 ON：读视图之后加锁读 / 改到别人改过的行 | `QueryDeadlockError` | 整笔回滚、退避重来（最多 4 次），用尽 `Exhausted` | `conflicts.retried`；OFF 后基本不再出现 |
| 1213 `ER_LOCK_DEADLOCK` | 两笔事务互相等锁（含子表 parent 索引的间隙锁） | `QueryDeadlockError` | 同上（InnoDB 已回滚整笔） | `conflicts.retried`；各入口原有的捕获照旧；没经 `retried` 的写入口直接报 500（同单并发老问题，见上节） |
| Frappe 保存前比对 `TimestampMismatchError`（抛出位置 `check_if_latest`） | 隔离 OFF：旧读视图里读出单据、别人提交后再保存（与 ON 时的 1020 同一种冲突） | `TimestampMismatchError` | **本次起**同 1020 | `conflicts.retried._stale_save`；没经 `retried` 的入口报 417（与 ON 时报 1020 一样是报错，没有变差） |
| 业务自己抛的 `TimestampMismatchError`（「记录变了、请刷新」） | 页面拿着旧版本号提交 | `TimestampMismatchError` | 如实报给用户 | 原样抛出，不重来 |
| 1205 等锁超时 | 等锁超过 `innodb_lock_wait_timeout` | `QueryTimeoutError` | 如实报给用户（已等满整个等锁时间） | 不重来；`assistant.sessions` 等入口按原有形状接住 |
| `LinkValidationError` | 旧读视图里看不到别人刚提交的被链接记录 | `LinkValidationError` | 不是冲突类型，不重来；靠「先锁后读 / 经 retried」不让它出现 | 撤回加锁读之后实验室 40/40 不再出现 |
| `DocumentLockedError` | Frappe 文档锁（`doc.lock()`，后台任务占着单据） | `DocumentLockedError` | 如实报给用户 | 产品代码没用 `doc.lock()` |
| 1062 重复键 | 两边同时插同一个唯一键 | `DuplicateEntryError` / `UniqueValidationError` | 各入口按业务处理（回到保存点改更新、视为已存在、报重复） | 各入口原有处理（`line_memory.learn`、`notices`、`push`、`companies.service`、`companion.devices` 等） |

## 经 `retried` 的入口（OFF 下撞上同一种冲突时，与 ON 时同样重来）

ERP 出凭证（`purchasing/output.voucher`）；CC 对话与执行记录（`assistant/sessions` 的 `_fresh_retried`、`_mark_after_send`、停止、标题写回，`toolrun`）；
CC 桌面版机器方法（`cc_desktop/machine._run` / `_guarded`、`api.register`、MR.ERP 登录）；Cowork 推送与 companion 接口（`push/application/api`、`card_save`、
`companion/*_api`、`submission`、`heartbeat`、`http_auth`、`assistant/tools` 交推送）。其余写入口不在 `retried` 内：并发冲突时 ON 报 1020、OFF 报 417 / 死锁 500，都是报错，
本次不变；统一接上重试见上节改进项。

## 验证

（见下方追加）
