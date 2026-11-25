# 写与只读判定之间的提交边界（11-25，接代录确定回归的同类审计）

基线：`origin/main` f18fa1ec8（含代录确定回归修复）。分支 `pearnly/operator-commit-boundaries`。主会话 11-25 派：审计出的同类
A1 / A2 / B2 / B3 每条先复现、复现不了的如实标、不改；复现的按同一原则修（提交边界放在调用方自己的边界），不逐处撒 commit。

规矩（11-19 回滚审计，`transaction.snapshot`）：Cowork 写账判定开头结束读快照（`precheck_source.document` → `require_clean`），
调用方有没提交的写就拒（PendingWrites，记 Error Log「Snapshot reset with pending writes」）。ERP 判定不做这一步，所以下面都只在 Cowork 上报错。

## 复现（回归实验室 cowork-new，真 Frappe；`scripts/cowork-commit-boundaries-lab-verify.py`）
| 条 | 场景 | 修前 | 修后 |
|---|---|---|---|
| A1 | 代录开始卡上答「已入账」（结果不明的单） | PendingWrites（4 笔），推送记录仍结果不明 | 推送记录 success、不再结果不明 |
| A2 | 开始卡（本月没单、别的月有）选另一个月份 | PendingWrites（6 笔），任务月份没改成 | 月份改成、按新月份重开卡 |
| B2 | 写入计划开始时对话存的账套是 11-12 以前的写法（「连接\|键」） | PendingWrites（1 笔），计划开不了 | 开始成功 |
| B3b | 对话口径逐张判，第一张结果不明提前返回 | 第二张 PendingWrites，计划做不出 | 计划 ready |

审计推断、未复现、不改：B3a（判定里连接刷新租约过期 `refresh_leases.expire` 的写漏到下一张：要租约恰好过期且这次没建新请求）、
B3c（`readback.history.for_push` 出错日志漏到下一张：要读回出错）。

## 修法
- **读账套不写**（B2、B3b，也盖住审计 B1：对话里 Cowork `sources` 先读账套）：`ledger.of_session` / `of_task` 是写账判定、写入计划、
  工具里「此刻写哪本」的查找，原来顺手把补算的编号记存（`current(store=True)`），读的人在写，留下没提交的一笔。改成只读
  （`current(row, store=False)`）；补算结果只在发消息那条写路径上存（`projects` 拼公司说明时 `current(row)`，原样）。
- **答卡与判定之间提交**（A1、A2）：`guide/documents.answer` 在落下代录卡自己的题（`guide_start.apply`：已入账 / 没有入账走产品核对、
  改月份走 `sessions.move`）之后、进写账判定（`record`）之前提交——这几笔是人对 Express 现状的回答，独立成立；
  `guide/run._answer_documents` 改月份重开卡时，这次失效（`_end`）落定后提交、再按新月份开卡进判定。两处都是「先写、后判」的编排者自己的边界。
- 没在判定里加提交、没在产品里撒提交。

## 测试
- 单元：`tests/test_cc_conversation_ledger.py`（查找账套从不写，发消息那条照旧存；去掉修法失败）；`tests/test_cc_guide_documents.py`
  （答「已入账」后、改月份后与重开卡判定之间都有提交；去掉修法两条失败）。
- 实验栈核对脚本 `scripts/cowork-commit-boundaries-lab-verify.py`：四条修前不过、修后过。

## 验证
- 清卷新栈 pearnly-fix1125-lab（本工作树实时源码）：受影响的 21 条页面流程（按改动文件选出的 18 条 + cowork.operator.unknown、
  cowork.cc.guide.pending-operator、cc.operator）42/42 过。
- 核对脚本四条：修前（a1/a2/b2/b3 同一造法）全部 PendingWrites，修后全过。
- code-review（medium）：发现 3 / 真问题 1（核对脚本里一段恒为空的拼接）/ 修 1 / 约 10 分钟；另 2 条判定无需改（改月份重开卡抛错时旧卡已失效：
  失效与月份是用户刚定的事实，修前这条路在 Cowork 上本来整笔失败；会话列表里的 ledger 字段可能是旧写法：页面当前账套走 GET ledgers 现算，不用它）。
