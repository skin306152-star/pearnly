# 需求影响评估：B ① 共用层——各处接冲突的写法统一认「关隔离后的保存前比对冲突」

> 分支 `pearnly/conflict-retry-common`，基线 origin/main = 71c46b036（A 已在内）。主会话 11-26 定的 B 分批：① 共用层 → ③ 静默丢更新与管钱底线 → ② ERP 同单写 → ④ 带外部 IO。
> 总表在 ~/pearnly-devtools/handoff-1126/B-逐入口幂等核对表.md。本节每一处都按 71c46b036 的原文重核过，不凭子代理盘点。

## 0. 需求原文与理解

- 原话：主会话 11-26「① 共用层：37 处循环统一换 retried；tasks._own、set_session、三个取单函数改成先加锁后读」。
- 理解：生产随今晚部署把 MariaDB 快照隔离关掉（OFF）。同一种并发冲突原来报 1020（Frappe 抛 `QueryDeadlockError`），关掉后改由 Frappe `check_if_latest` 抛 `TimestampMismatchError`（417）。
  - A 只让 `conflicts.retried` 认了后一种。
  - 仓库里其余各处自己接 `QueryDeadlockError` 的写法都还只认前一种。关隔离后，这些地方原本会整笔重来的冲突，会变成 417 回给用户，或者被保存点吞掉。
- 不在本批范围：
  - 「加锁后普通读」的根函数顺序（`tasks._own`、`set_session`、`access.get_document`、`intake._owned`、`collection._own`）。这些要按入口逐个判断能不能整笔重来，各入口还要包一层，改动面和 ② ③ ④ 重叠。本批只修「接冲突」这一层，根函数放 ③ / ② 随入口一起改。这一条对主会话原话做了调整，请审。
  - `payments/transactions.retry_service` 与 billing.charge：按主会话定，归 ③。
  - 静默丢更新四处，归 ③。

## 1. 现有同类与复用

按 71c46b036 原文重核，除 `conflicts.py` 外，仓库里接 `QueryDeadlockError` 的地方分三类。

### 第一类：入口层自写的「回滚再来」循环（21 处）

都是 `for attempt in range(3): try … except QueryDeadlockError: rollback; 第 3 次 raise`，只认死锁：

- pearnly_core：`line_binding`（发绑定码）、`write_precheck/api`（写账前置判定）。
- ERP：`express/bulk`（批量取号）、`work/api._run`（看板）、`inventory/api`（库存，带 `retry_safe`）。
- Cowork：
  - `work/api._run`、`connections/api`；
  - `history/api` ×2（手工建票、改页）、`history/category_api`；
  - `files/api` ×2（还认 DuplicateEntryError）、`masters/workspace_api`、`folder_intake/api._retried`；
  - `ocr/api`、`recon_center/api`、`reconciliation/bank_recon_api`、`income_recon_api`、`stocktake/api`、`stocktake/desk_api`。

结论：**复用 `conflicts.retried`**，它已经认两种冲突，退避、次数和提示清理都已经过 A 验证。
- 各入口原来另外接的异常照留：Invalid、ConnectionError、Deferred / QueueFull、DuplicateEntryError、`retry_safe` 判定。
- 用尽时原来是把 `QueryDeadlockError` 抛出去，变成 500 并带数据库原文。改为 `frappe.throw(_("Please try again later"))`，这是已上线、已有四语翻译的词，不新增文案。
- 每处改前都要核一遍被包的函数在提交前有没有外部 IO。会重来的部分原来就是整笔回滚重来，所以只是把同一种冲突的另一个报法也纳入，不扩大重来的范围。

### 第二类：保存点内只把死锁往外抛（12 处）

写法是 `except QueryDeadlockError: raise`，其余异常回到保存点吞掉或转成提示。

- 位置：
  - `cc_desktop/machine`、`stocktake/service`、`push/native/service`；
  - `history/recognition`、`history/certificates`、`history/storage`；
  - `companion/credentials`、`companion/transport`、`companion/heartbeat`、`companion/credential_api`；
  - `ocr/feedback/service`、control `billing.gate`。
- 关隔离后的问题：保存前比对冲突落进 `except Exception`，被回到保存点吞掉或转成业务提示（例如「CC ledger change not queued」「WHT certificate reference not kept」），外层的 `retried` 永远看不到，冲突就丢在半路。

结论：给 `conflicts` 加一个公开判断 `is_conflict(error)`，内容是 `QueryDeadlockError` 或 `_stale_save`。这 12 处的「往外抛」分支改成按它判断，其余分支不变。

### 第三类：抢占、领单类的「输了就算没抢到」（9 处）

- `assistant/toolrun` ×2、`assistant/waits` ×2、`assistant/guide/run._locked`、`assistant/sessions:1040`、`cc_desktop/jobs:397`、`line_session._row` 两处、ERP `inventory/count_state`。
- 结论：**不改**。这些地方的冲突来自带条件的 UPDATE，或者加锁读本身。关隔离后加锁读读到的就是最新值，带条件的 UPDATE 按当前行判断，不会出现保存前比对冲突。
- 逐条理由写进施工记录。

### 第四类：后台分步提交，冲突时「这次不记、下一轮再来」（4 处）

- `folder_intake/pump` ×2、`ocr/remote/callback`、`ocr/execution/workflow`。
- 关隔离后，保存前比对冲突落进通用 `except Exception`：pump 会记错误日志、延后 10 分钟，callback 会当失败。
- 结论：同第二类，按 `is_conflict` 走原来「死锁」那条分支，也就是下一轮再来。

## 2. Frappe 原生能力

- Frappe 16.33 没有现成的事务重来机制，后台任务的自动重试只接 `InternalError`。`conflicts.retried` 是本仓的共用层（ADR 在 `transaction` 段）。
- 不涉及 DocType、字段、补丁。

## 3. 按类通用

- 这一类是「所有自己接数据库冲突的地方」，四类全部列在上面。
- 闸门：新增 `scripts/check-conflict-catch.py`。`conflicts.py` 以外出现 `except frappe.QueryDeadlockError`（或元组里含它）就失败，除非同一行标注 `# conflict-ok: 原因`。第三类各处逐条标注原因。这样以后新写的代码只能走 `retried` / `is_conflict`。
- 加进 `development.yml` 与 gate。

## 4. 上下游数据

- 不改任何数据结构与载荷。CC 桌面版载荷不变，不升版本。
- 行为变化只有三种：
  1. 关隔离后原本会 417 的冲突，现在整笔重来；
  2. 原本被保存点吞掉的冲突，现在整笔重来，或下一轮再来；
  3. 第一类用尽时的报错从 500 + 数据库原文，改成「请稍后重试」。
- Express / MR.ERP 写入：只在提交前没有外部 IO 的本地事务里重来。第一类各处改前逐一确认，有外部 IO 的不纳入、单列给 ④。

## 5. 隔离与权限

- 不涉及：权限判断都在被包的函数里，重来时照原样再判一次。

## 6. 入口：网页、手机、LINE、拍照

- 受影响的入口就是第一类的 21 个白名单接口，加上第二、四类所在的机器通道和后台任务（CC 心跳 / 回执 / 凭据、推送账本、识别回调、文件夹收单）。
- LINE、相机：不直接涉及（`line_session` 属于第三类，不改）。

## 7. 共用件与连带回归

- 改到共用件：
  - `pearnly_core/transaction/conflicts.py`：新增 `is_conflict`，`retried` 行为不变；
  - `cc_desktop/machine`；
  - Cowork `companion/*`、`push/native/service`。
- 施工时贴 `check-impact.py --files` 的输出，影响清单里的流程逐入口照跑。
- 证明：
  - 离线：`is_conflict` 认两种冲突、不认业务自己抛的那种；第一类各处用尽转「请稍后重试」、其余异常原样；第二类各处保存前比对冲突往外抛、不吞。去掉修法后对应测试要失败。
  - 实验室：清卷全量，看并发类流程（voucher-concurrent、conc30、userdel、writer-at-claim、stocktake 并发、推送并发）与 Error Log。
  - 第二类在机器通道上能不能真的撞出保存前比对冲突，要写并发脚本才能证明。先在自己的栈上做修前红、修后绿，挑 CC 心跳 + 回执同时到这一条。

## 8. 存储与成本

- 不涉及：不新增存储、外部调用或月费。重来最多 4 次、合计约 1 秒，只发生在冲突时。

## 9. 删除、作废、追溯

- 不涉及：不改删除和作废的语义。被回滚的那次什么都不留，同 A。

## 10. 文案

- 不新增文案，复用已上线的「Please try again later」（四语已有），不进 copy-approved。

## 11. 验收

- 闸门 292 项全过，加上新检查、候选离线、逐提交扫描拦截 0。
- 回归实验室清卷全量全过；服务端 Error Log 只出现预期内的记录。
- code-review（medium）：本分支引入的问题修掉。

## 12. 待问主会话

1. 根函数「先加锁后读」挪到 ③ / ② 随入口一起改，不在 ① 做：是否同意？理由见第 0 节。
2. 第一类用尽时，从 500 改成「请稍后重试」提示，算行为变化，但不新增文案：是否同意？
