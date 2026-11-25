# 代录列出快照未提交 → Cowork 代录确定「未完成」（11-25 修复记录）

基线：`origin/main` 4eb0b2499。分支 `pearnly/operator-listed-commit-fix-924bcd`。生产 d8510e61a 不含 11-24 列出快照，不受影响。

## 现象与证据
- 本机实验栈 `pearnly-cov1124-lab`（复用卷，已迁移）纯 4eb0b2499：`cowork.cc.guide.operator-purchase [new / desktop]` 点确定 → 结果卡「未完成」；
  Error Log「Snapshot reset with pending writes」`precheck_source.document: 1 uncommitted write(s)…` 与「Assistant guide confirm failed」。
- 调用链：guide confirm → `guide/documents.confirm` → `express_operator.api.hand_in` → `start` → `waiting` →
  `listed.remember`（`frappe.db.set_value` 未提交）→ `products.precheck` → Cowork `operator_source.precheck` → `precheck_source.document`
  → `transaction.snapshot.require_clean` 抛 PendingWrites。ERP 判定不做 require_clean，所以 ERP 代录不报这条。
- 触发条件：交单 / 写入计划第一次把某张单记进快照（快照有变化）。真实场景：资料清单开着时又进来一张票，不刷新就确定；
  或代录引导对话里从没列过资料清单就确定（现有流程就是这种）。

## GitHub 为什么是绿的（11-24 推断已推翻）
- 不是「全新实验室上清单先列过并提交」：GitHub 那次按改动文件只选了 22 条流程（`run.py --changed --base 90bf74118`），
  `cowork.cc.guide.operator-purchase / -sales` 的 covers 没登记 `express_operator/api.py` / `listed.py`，**根本没跑**。
- 修：这两条与 `erp.cc.guide.operator-purchase / -sales` 补登 `api.py`、`listed.py`；`cc.operator`、`erp.cc.operator.intake(.sales)` 补登 `listed.py`。

## `erp.cc.operator.intake` 在 4eb0b2499 上不过（11-24 晚）
- 不是第二个缺陷。实验栈 erp-new Error Log 11-24 16:51 / 16:53 原文 `OperationalError (1054, "Unknown column 'listed' in 'SET'")`：
  代码换成 4eb0b2499 后、`run.py --migrate` 之前跑的，库里还没有 `listed` 列。迁移后（17:00 起）没有再出现；
  11-25 纯 4eb0b2499 上连跑两次通过（它只登记 new / 电脑一格）。

## 修法（结构）
- 提交边界放在 `api.waiting`：记账（`listed.remember` 记快照、`listed.gone` 读不到现状时记错误日志）到此为止、`frappe.db.commit()`，
  再进只读判定（`products.precheck`）。
- 为什么是这一层：三个记快照的调用方里只有 `waiting` 记完在同一事务里接着判；资料清单（POST）与 CC 工具 `tools._preparing` 记完就结束，
  由请求 / 工具收尾提交，不需要自己提交。放在 `remember` 里（第一版）盖不住 `gone` 那条错误日志（Error Log 也是一笔写，审计 A3）；
  若再在 `remember` 进口加 `require_clean`，对话里 Cowork `sources` 先记存本对话账套（`ledger.current(store=True)`）时会让
  `_preparing` 新失败（审计 B1），所以不加。
- `waiting` 的调用方：写入计划 `make`（之前至多有对话账套的幂等记存，一起落库无妨）、交单 `start` ← `hand_in` ← 写入计划 `start` /
  引导 `confirm`（进来前都已提交）。没有 GET 能到 `waiting` / `remember`（`api.documents` 只收 POST，其余只经工具与 POST）。

## 测试
- 单元：`tests/test_express_operator_code_cc.py`（替身 frappe 数未提交的写，判定照 Cowork 先 `require_clean`）——清单开着后进新票再交单、
  写入计划第一次见到新票、快照里有读不到现状的单（记日志）仍能判、资料清单与准备中清单记快照不自己提交；去掉修法前三条失败。
- 页面流程：新增 `cowork.cc.guide.operator-new-after-list`（先开清单 → 复核页同一条路把另一张票的日期改进本月 → 不刷新就确定），
  旧代码不过（同一条 PendingWrites）、修后电脑 / 手机过。

## 同类审计（只读代理读代码得出，11-25；A1 / A2 / B2 / B3 未实测复现）
本分支只修上面这条（A3 一并）。下面是同一规矩（Cowork 判定 `require_clean`）下的既有同类，**不是 11-24 引入**，记在这里另开分支：
- A1：Cowork 代录开始卡上答「已入账 / 没有入账」→ `guide_start.apply` → `api.resolve` → 推送记录在保存点里写、未提交 →
  同一请求 `write_precheck.record` → `card` → 判定 `require_clean` 拒。
- A2：开始卡上改月份 → `sessions.move` / `tasks.update`、`run._end` 写、未提交 → `guide_start.start` → `check_handed` → 判定拒（新月份有单时）。
- B2：写入计划 `start` 里 `_ledger(row)` 可能记存对话账套、提交在判定之后。
- B3：`precheck_source.document` 逐张判时自己留下的写（连接刷新租约过期 `refresh_leases.expire`、对话口径 `_aimed` 记存账套后提前返回、
  `for_push` 出错日志）漏到同一批下一张。
- 已查干净：资料清单、ERP 全部判定（不做 require_clean）、`submit`、GET 路径。

## 验证
- 复用卷 `pearnly-cov1124-lab`（11-24 建、跑过一天）：最终代码跑 10 条流程（cowork.cc.guide.operator-purchase / -sales、cowork.cc.operator.upload-on-send、
  cowork.cc.operator-ledger、erp.cc.guide.operator-purchase / -sales、erp.cc.operator.intake / .sales、cc.operator.replay、新流程）18/20 格过；
  不过的是 `cc.operator.replay [new / 电脑、手机]`，**纯 4eb0b2499 在同一卷上同样不过**（开始卡渲染出别的遗留任务的「请在 Express 中选择公司」，
  与本分支无关，是这套卷的旧数据），清卷上四格都过。
- 清卷新建 `pearnly-fix1125-lab`（11-25 新起、本工作树源码）：同 10 条 20/20 过。
- 验收场景（编排 B：本机 Mac 栈挂本分支源码 + 测试机2 CC 1.12.24 + 69EXP\TEST2）：acc.erp.company.connect.fresh、acc.erp.operator.listed.direct 通过；
  测试机2 按哈希还原 176DE98E、TEST2 按写前基线还原 0 差异，CC 停止状态交还。
- 新流程反证：旧代码（只把 listed.py / api.py 换回 4eb0b2499）下不过，Error Log 同一条「Snapshot reset with pending writes」。
- code-review（medium）：发现 5 / 本分支真问题 2（流程文件头写错修法位置、每月 1 号日期落在未来）/ 修 2；另 1 条（提交释放任务行锁）判定可接受；2 条既有（A1、A2）。
