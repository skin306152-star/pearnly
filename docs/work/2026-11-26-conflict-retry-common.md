# B ① 共用层：各处接冲突的写法统一认关隔离后的保存前比对（11-26）

- 分支 `pearnly/conflict-retry-common`，基线 origin/main = 71c46b036。评估：[2026-11-26-conflict-retry-common-评估.md](2026-11-26-conflict-retry-common-评估.md)，主会话 11-26 审定。
- 总表：~/pearnly-devtools/handoff-1126/B-逐入口幂等核对表.md。B 的顺序：① 共用层 → ③ 静默丢更新与管钱底线 → ② ERP 同单写 → ④ 带外部 IO。
- 范围只到「接冲突」这一层。根函数「先加锁后读」（`tasks._own`、`set_session`、`access.get_document`、`intake._owned`、`collection._own`）随 ③ / ② 各入口一起改，不在本批；`payments/transactions.retry_service` 与 billing.charge 归 ③。

## 为什么

生产今晚部署后 MariaDB 快照隔离为 OFF（11-23 那批）。同一种并发冲突原来报 1020，Frappe 抛 `QueryDeadlockError`；关掉后改成 Frappe `check_if_latest` 抛 `TimestampMismatchError`（417）。

A（`f7fd77bcf`）只让 `conflicts.retried` 认了后一种。仓库里其余自己写 `except frappe.QueryDeadlockError` 的地方都只认前一种，关隔离后：
- 入口层：三次循环接不住 417，冲突直接回给用户。
- 保存点 / 降级处：冲突被当普通错误吞掉或降级，外层重试永远看不到。
- 后台：冲突被记成出错、延后，或者当失败。

## 改了什么

`transaction/conflicts.py` 新增两个函数，`retried` 本身不变：
- `is_conflict(error)`：判断是不是 1213 / 1020，或 Frappe 保存前比对那一种冲突（业务自己抛的「请刷新」不算）。
- `attempt(function, …)`：白名单入口用的 `retried`。用尽或回滚失败时整笔回滚，回已上线的「Please try again later」（四语已有，不新增文案，主会话认可）。

### 第 1 类：入口层三次循环 → `conflicts.attempt`（18 处改，3 处不改）

| 入口 | 原来用尽时回什么 | 现在 |
|---|---|---|
| `line_binding.generate_bind_code`（发 LINE 绑定码，Redis 锁内） | 抛 `QueryDeadlockError` → 500 + 数据库原文 | attempt |
| `write_precheck/api.check`（写账前置判定） | 已经回「Please try again later」 | **不改**：只读判定，没有保存前比对。改这个文件会连带要 LINE 出卡前问题的真机记录，所以不在源码里标注，登记在 `scripts/conflict-catch-reviewed.tsv` |
| ERP `work/api._run`、Cowork `work/api._run`（看板） | 500；`Invalid` 翻成提示 | attempt；`Invalid` 照旧在外层翻译 |
| ERP `inventory/api._opening_transaction`（期初） | 500；调用方有没提交的写时不重来 | 同样只在请求自己拥有事务时 attempt，否则原样直调 |
| Cowork `connections/api._call` | 500；`ConnectionError` 翻成提示 | attempt；`ConnectionError` 照旧 |
| Cowork `history/api.create`、`history/api.save`（手工建票、改页） | 500 | attempt |
| Cowork `history/category_api._call`（分类） | 500 | attempt |
| Cowork `masters/workspace_api.commit_workspace_import` | 500 | attempt |
| Cowork `folder_intake/api._retried`（自动录入规则页） | 500 | attempt |
| Cowork `ocr/api.submit`（录入上传识别） | 500；`Deferred` / `QueueFull` / 结构化错误回 `accepted: False` | attempt；后三种照旧 |
| Cowork `recon_center/api.start`（对账中心） | 500 | attempt |
| Cowork `reconciliation/bank_recon_api._transaction`、`income_recon_api._transaction` | 500 | attempt |
| Cowork `stocktake/api._transaction`、`stocktake/desk_api._transaction` | 500 | attempt |
| Cowork `files/api.upload`、`files/api.finish_upload`（原件上传） | 第 3 次原样抛（死锁或同号并发插入）；存放处连不上回「upload.deferred」 | **保留循环**：同号并发插入也要重来，`retried` 不管这种；判断换成 `is_conflict(error) or DuplicateEntryError`，第 3 次照旧原样抛；`Deferred` 分支排在通用分支前面，照旧回「upload.deferred」（第一版排反了，被 `test_cowork_upload_deferred` 拦下） |
| ERP `express/bulk.reserve`（批量取号） | 用尽原样抛给调用方 | **不改**，标注：只有 FOR UPDATE + UPDATE 计数器，没有保存前比对；它是内部函数 |
| control `payments/transactions.retry_service`（计费） | — | **不改**，标注：扣费撞 1213 重试不生效的原有问题按主会话定随 ③ 修 |

### 第 2 类：保存点 / 降级处把冲突吞掉 → 按 `is_conflict` 往外抛（7 处改）

| 位置 | 原来冲突落到哪 | 现在 |
|---|---|---|
| `cc_desktop/machine._changes`（CC 心跳带的账套变化） | 回到保存点、记「CC ledger change not queued」、心跳照常 | 往外抛，由机器方法的 `retried` 整笔重来 |
| `history/certificates.keep_skipped`（不录入的 50 ทวิ 参考记录） | 记「WHT certificate reference not kept」、回 0 | 往外抛 |
| `ocr/feedback/service.capture_saved_edit`（人改识别结果的纠错事件） | 回 `failed` | 记 `transaction_retry`，往外抛（同死锁） |
| `history/categories._optional`、`history/names._optional`（分类 / 名称记忆） | 降级成「记忆暂不可用」、回 fallback | 往外抛 |
| `history/duplicates.check`（查重） | 回 `unavailable` | 往外抛 |
| `masters/workspace_import._judge`（批量建账套的一行；保存前比对是 `ValidationError` 的子类） | 记成这一行 `error` | 往外抛 |

### 第 4 类：后台「这次不记、下一轮再来」（4 处改）

| 位置 | 原来冲突落到哪 | 现在 |
|---|---|---|
| `folder_intake/pump._each`、`_push_step`（文件夹收单两段） | 记错误日志、延后 10 分钟 | 同死锁：回滚，下一轮再来 |
| `ocr/remote/callback._record`（云上识别回调写检查点） | 抛出 → 回调 500 | 同死锁：回滚重写，最多 3 次 |
| `ocr/execution/workflow.run` 结果入库 | 当识别流程出错 | 同死锁：`Deferred("database_busy", 5)` 稍后再来 |

### 第 3 类及其余：不改，逐条标 `# conflict-ok: 原因`（23 处）

- `assistant/guide/run._locked`：加锁读本身的 1020，回滚后再锁一次。这一段不保存，关隔离后读到最新、不再抛。
- `assistant/sessions._remember_context`：附带记录只用 `set_value`，没有保存前比对；冲突时这次不记、下一轮再记。
- `assistant/toolrun:167`：插入抢占。同号并发插入只会撞唯一键或死锁，插入没有保存前比对。
- `assistant/toolrun:270 / 351`、`assistant/waits:137 / 223`：带条件的 UPDATE 抢占，按 rowcount 判输赢，没有保存前比对。
- `cc_desktop/jobs:397`：领单加锁读本身的 1020，输了空手回去、下一轮再领。这一段不保存。
- `line_session._row`、`_comparison_row`：CAS 的加锁读本身，输家不写。先加锁后读的根函数随 ③ / ④ 改。
- `express_operator/tasks._current`：已经一并接 `TimestampMismatchError`，按当前状态再做一次。`_own` 根函数随 ③ 改。
- ERP `express/bulk.reserve`、control `payments/transactions`：理由见第 1 类表。
- ERP `inventory/count_state`：只读的加锁读（盘点时比对库存），冲突转成「库存变了、请重盘」的业务提示。
- Cowork `stocktake/service.atomic`、`push/native/service.atomic`、`history/recognition.persist_batch`、`history/storage.complete`：保存点。死锁整笔回滚后往外抛；其余异常（含保存前比对）回到保存点后同样往外抛，外层 `retried` 接得到。
- Cowork `companion/credentials._atomic`、`companion/transport._atomic`、`companion/credential_api._attempt`：保存点，所有异常都往外抛（死锁不回保存点）。
- Cowork `companion/heartbeat._attempt`：保存点，只把三种业务错误转成提示，其余异常往外抛。
- control `billing.gate`：只读查余额，没有保存；死锁交外层重试。

### 闸门

- `scripts/check-conflict-catch.py`：`apps/` 产品代码（测试、补丁、`conflicts.py` 除外）里 `except` 只认 `QueryDeadlockError`（含元组），那一行又没有 `# conflict-ok: 原因`、也没在 `scripts/conflict-catch-reviewed.tsv`（文件、函数、原因）里登记的，就失败；原因不许空，登记了却找不到的条目也算失败。
- 测试 `tests/test_check_conflict_catch.py`、`tests/test_conflict_catch_sites.py`，接进 `development.yml`（gate 照它跑）。
- 共用件登记：`transaction` 加入口 `shared.conflict-catch`（26 个调用方文件）。

## 验证

- 离线 `tests/test_conflict_catch_sites.py`，16 项。按原文加载每个被改模块，Frappe 和依赖用最小替身，让那一步抛「Frappe 保存前比对」那一种冲突（抛出位置是替身 `frappe/model/document.py` 的 `check_if_latest`）：
  - 第 2 类每处：冲突往外抛；业务错误照旧降级（对照）。
  - 第 4 类每处：冲突不记错误日志、不延后，或回滚重写，或稍后再来。
  - 第 1 类每处：冲突整笔重来、第二次成功；一直冲突回「请稍后重试」，次数 = ATTEMPTS；业务自己的「请刷新」不重来。
  - 期初：调用方有没提交的写时不重来。
  - 原件上传：判断式在两处。
- **修前红**：同一套测试放到 origin/main 71c46b036 的原文上（只换成本分支的 `conflicts.py`），27 处失败（9 failures + 18 errors），涉及的每一个被改位置都在内；`is_conflict` / `attempt` 两项本身通过。修后 16 项全过。
- 旧测试随行为改的 4 处：
  - `test_cowork_folder_intake`：网页写入与心跳并发，改为一共 4 次，用尽回「请稍后重试」；pump 的替身补上真 `conflicts`。
  - `test_opening_batch`：期初用尽，改为回「请稍后重试」、回滚 5 次（4 次各一次，用尽再整笔一次）。
  - `test_cowork_stocktake_desk_time`：替身补 `conflicts`。
  - `test_cowork_upload_deferred`：没改测试，修的是代码里的分支顺序。
- 产品地图：第 1 类改到的 Cowork 7 个、ERP 2 个入口文件，命中 LINE 富菜单盘点相机的两行 `cowork.scan.line.stocktake.liff-camera`、`erp.scan.line.stocktake.liff-camera`，这两行只能真机验，要附真机记录才能进 main。
- 全闸门、候选离线、逐提交扫描、清卷全量、code-review：结果见下方（施工中补）。

## 真机（11-26 晚，三星 SM-A146P + 测试机1 testenv，源码挂 a1a0ed9e2，底座线上镜像 e695b76f）

真机记录 | erp.scan.line.stocktake.liff-camera | 2026-11-26 | a1a0ed9e2 | 三星 SM-A146P + 测试机1 testenv（屏幕码图 code-erp-qr-ice.png） | 通过 | ERP-TEST 富菜单「盘点」→ LIFF 打开 testerp 盘点列表 → live-1115-test → 开始盘点 → 连续盘点弹窗点扫码图标 → LINE 原生「是否允许此网站访问您的相机？」→ 仅允许本次 → 真相机对测试机屏幕读码 → 认出「น้ำแข็งหลอด」、账面仓库「คลังสำนักงานใหญ่ · 10,774 กิโลกรัม」、实际仓库预填 → 关闭不保存
真机记录 | cowork.scan.line.stocktake.liff-camera | 2026-11-26 | a1a0ed9e2 | 三星 SM-A146P + 测试机1 testenv（屏幕码图 EAN-13 8851932100014） | 通过 | Cowork-test 富菜单「库存盘点」→ LIFF 打开 testcowork 盘点列表 → 123 → 开始盘点 → 扫码图标 → LINE 原生相机授权 → 仅允许本次 → 真相机读 EAN-13 → 认出「สบู่ก้อนลักส์ 70 กรัม」、账面「คลังสาขาบางนา / S-02-02 · 150 ก้อน」→ 关闭不保存；盘点查找请求 `api_stock_count_lookup`（本分支换成 `conflicts.attempt` 的入口）回 200

- 过程中一次：测试机屏幕还停在 ERP 的冰块二维码时，Cowork 查找回「找不到盘点记录。」——这是 Cowork 这次盘点里没有该编码时的原有提示（`counting.lookup` → `stocktake.not_found`），与本分支无关；换成 Cowork 码后同一入口认出物料。
- 两次都没保存实盘数量，两站测试数据未改。做完 testerp、testcowork 已 lab-stop，三个测试域名回读到线上地址，两站定时任务 enabled。
