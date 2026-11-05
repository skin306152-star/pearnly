# CC 删对话 500（回复一出来就删）：根因与修复

- 分支 `pearnly/cc-delete-race`，基线 `main` = `ccc2b9b3d`。共用件 CC 对话（`pearnly_core.assistant`），不部署。
- 现象（产品地图 e2e，11-05，testcowork / testerp）：回复刚出来、侧栏标题还是用户那句话时调 `assistant.api.delete`，返回 500，
  `exc_type = QueryTimeoutError`，提示「暂时不能删除」；等标题改成模型起的（约 10–15 秒）再删就是 200。手机上回复完马上长按删除会碰到。

## 根因（实验室 MariaDB 11.8 实证）

站点库开着 `innodb_snapshot_isolation = 1`（REPEATABLE READ）。`sessions.delete` 的顺序是：

1. `owned()` 读这一行，这一步建立了读快照；
2. 调引擎删远端会话（网络调用）；
3. 删提醒；
4. `frappe.delete_doc` 对这一行做不等锁的加锁读（`get_value(..., for_update=True, wait=False)`）。

第 1 步到第 4 步之间，只要别的事务改过这一行并提交，第 4 步就报 1020（`ER_CHECKREAD`）。Frappe 的 `delete_doc` 接住 1020 / 1213，
改抛 `QueryTimeoutError`。回复后 10–15 秒正是这一行被反复改的时候：推送线程记最后活动时间、记上下文用量；页面读事件时补记；
起标题任务先写 `title_source = summarizing`、调完模型再写标题。

不是「有人长时间锁着」：起标题任务只在开头、结尾各锁一下就提交，推送线程用 `skip locked` 不等锁。

实证（实验室 `pearnly-precheck-w3-lab` 的 MariaDB 11.8.9）：连接 A 先读 → 连接 B 改同一行并提交 → A 做 `FOR UPDATE NOWAIT`，
报 `(1020, "Record has changed since last read")`；A 回滚换新快照后再锁成功。

## 修法

- **会话的本站写统一走 `sessions._write`**：删除、归档、改名、星标、移月份。
  - 先做引擎调用和只读核对，再结束请求的读快照（之前做完的写随之提交）。
  - 在新事务里先 `FOR UPDATE` 锁住这一行（等锁，起标题、推送线程的写都是毫秒级短事务），再写。
  - 撞上 1020 / 1213 用共用的 `transaction.conflicts.retried` 整笔回滚、退避重来，最多 4 次。
  - 用尽或等锁超时，回「对话修改失败，请重试」，不是 500。
- **幂等**：
  - 删除：引擎删远端已经是「不存在算成功」；本站这一行在重来时已经没了（另一台设备删了），算成功、不重复推侧栏。
  - 归档：引擎归档改成已停下、已归档、不存在都算成功。
  - 远端调用在重来之外，只做一次。
- **代录会话**：外层删除整个任务（`tasks.delete`）同样在新事务里、撞上冲突整笔重来。任务删除回头删它的对话时就地写，不中途提交，
  不提前放开任务行的锁。
- **起标题任务**：概括期间对话被删了（远端读不到），静默收手，不记 Error Log；行没了 `_finish` 本来就不动。
- **推送线程**：写一条已删的行是 0 行更新，不报错，保持不变。
- **同类排查**：会话表上「先读、中间有网络调用、再写」的只有删除、归档两处（改名的引擎调用原来在写之后，现在挪到写之前）；星标、
  移月份没有网络调用，窗口很小，也一并走 `_write`。

## 测试

- 离线 `tests/test_assistant.py` 新增 `SessionWriteRaceTests` 7 条：用模拟快照隔离的会话表替身
  （`_SessionSnapshotDb`，与已有的 `_SnapshotDb` 同一套行为）。
  - 修前顺序报「暂时不能删除」；删除时起标题任务写了标题，照样删成；加锁撞上冲突重来、远端只删一次；
  - 重来用尽回可读的错误、行还在；另一台设备已删算成功；改名 / 星标 / 归档同样写成；
  - 起标题任务在对话已删时不记错误、不写回。
  - 原有 175 条照过（替身补了 `commit` / `rollback` / `exists` 与 `for_update` 参数）。
- 实验室 `scripts/cc-delete-race-lab-verify.py`：在真 MariaDB 上，删 / 归档 / 改名 / 星标时另一个连接改这一行并提交，
  本站这一半照样写成；修前的顺序在同一情形下报 `QueryTimeoutError`。

## 验证（基线 `ccc2b9b3d`，分支 `ea206450e`）

- 全闸门（`development.yml` 同名步骤）190 项通过；离线 `tests/test_assistant.py` 190 条通过。
- 生产镜像候选离线检查：线上同版镜像 `fb1037ad…` 挂上本分支五个产品源码跑 `ci-candidate-checks.sh offline`，102 步通过。
- 实验室（独立栈 `pearnly-cc-race-lab`）：
  - 实证脚本 `cc-delete-race-lab-verify.py`：Cowork、ERP 两个站点都是 `{"old_order_fails": true, "delete": true, "archive": true, "rename": true,
    "star": true}`，即修前顺序在真 MariaDB 上报 `QueryTimeoutError`，修后四种写法都写成。
  - `run.py --changed`：14 格中 10 格通过（`cc.chat` 4、`cc.operator` 2、`cc.sidebar-modes` 2、`cowork.cc.sidebar-modes` 2）。
    另 4 格是 `cc.operator.replay` 在 prepare 报 need two submitted purchase documents：新栈里没有已提交采购单，是共享
    「已知问题与结论」11-05 已记的流程数据依赖。先跑 `erp.purchase.create`（4/4）后重跑 `cc.operator.replay` 4/4 通过。
  - `cc.chat` 单独再跑 4/4；`cc.companies` 1/1。

## 影响清单（`check-impact.py`：共用件 `assistant`，以及 `transaction` 新增调用方 `cc.assistant`）

| 入口 | 结果 |
|---|---|
| `cc.assistant` | 通过：流程 `cc.chat`（新 / 老 × 电脑 / 手机，新建对话、发消息、删除）、`cc.sidebar-modes`；实证脚本删除、归档、改名、星标 |
| `shared.assistant` | 通过：流程 `cc.chat` 打开对话页、无页面报错 |
| `cc.operator.thread` | 通过：流程 `cc.operator`（新 / 老）、`cc.operator.replay`（新 / 老 × 两宽度） |
| `erp.cc.companies` | 通过：流程 `cc.companies`（老） |
| `erp.cc.chat-push` | 离线：`tests/test_assistant.py` 190 条；直写工具链没改 |
| `control.assistant` | 没测到：fatcc.com 智能体入口在控制面，回归实验室没有控制面站点；改动只在会话表的写操作，入口登录与跳转没动 |
| `cowork.assistant` | 没测到：要真引擎调 Cowork 工具，实验室的假智能体不调工具；离线覆盖会话写操作 |
| `cowork.cc-chat.push` | 没测到：同上（要真引擎让 CC 推送）；推送链路没改 |
| `cowork.cc.troubleshooting` | 没测到：同上（要真引擎调排查工具）；工具没改 |
