# 代录：整批停下以后再交来的单、单张重试、整批继续（2026-11-03）

- 状态：本次范围完成（本地实现 + 本地测试 + 本地闸门）；**未推送、未合入 main、未部署**，Windows 测试机真机没测（见 §6）。
- 仓库 / 分支：`/Users/skin/pearnly`，`pearnly/operator-resume-after-stop`，基线 `origin/main` = `790dc639b`。
- 工作树：`/Users/skin/pearnly/.claude/worktrees/agent-a49908ffbd285e4cb`（桌面 App 为本会话建的工作树，分支已切到上面那条）。
- 范围：共用批次逻辑（CC `express_operator/batch.py`、`host.py`、写账线 `worker.py`）、服务端共用层任务记录（`pearnly_core/express_operator/tasks.py`、
  `api.py`）、CC 网页前端「需要处理」（`frontend/assistant/src/components/operator/`）。Express 与 MR.ERP 同一套（都在共用 Runner / Host，不按目标分叉）。
- 不碰：`apps/pearnly_cowork/pearnly_cowork/connections/`、`companion/src/companion/mrerp_operator/`、两份原因表
  （`companion/.../contract.py`、`pearnly_core/.../contracts.py`；本任务只**读** `contracts.BATCH_STOPPERS`，另一窗口在
  `pearnly/mrerp-operator-server` 上加的两个原因代码合入后自动算进来）；Express 载荷、直写、作废两道闸、写者锁一行不改。

## 1. 复核：三个问题都成立

| # | 现象 | 读代码确认的原因 |
|---|---|---|
| 1 | 批次停了（人点停止，或 `BATCH_STOPPERS` 里的原因）以后，站点按原批次号再交来的任何单都回「停止跳过」，永远录不进去 | `Host._finish` 不把 runner 移出 `runners`，批次留在 `BATCH_STOPPED`；`Runner.run_one` 一见 `_stop` 或 `BATCH_STOPPED` 就把这张记 `SKIPPED/stopped`。CC 重启后 `_start` 从批次文件读回同样是 `stopped`。`add()` 只重置 `WAITING`，`SKIPPED` 再交来不动 |
| 2 | 页面单张「重试」报错 | 页面走桥 `operator_control action=retry`，`Host.control` 没有 `retry` 分支，回 `False` → 页面 `operator_control retry refused` |
| 3 | 整批停后（非登录类）整批那条「继续」没效果 | 它调 `o.start` → 桥 `control resume` → `Runner.resume()` 只清暂停，对已停的批次什么都不做 |

另外读代码时发现的同链问题（一并按口径修）：

- **人点停止时正在录的那张**（引擎在等电脑空出来时抛 `Stopped`，这张留在「正在录」）：原来回执「等」（`network_wait`）→ 站点放回队列
  → 同一件再派来时被直接记成「停止跳过」，**没先读账套**——那张可能已经按过保存。
- **服务器连不上超时**（`network_down`）：第一次回「等」，站点放回、同一件再派来同样被记「停止跳过」、不读账套。
- **这一批录完 / 停下已退出 Express 后再交来一张**（作答、重试）：`finish()` 退出 Express 却没标「下一张先重开」，对着已退出的 Express
  录，靠引擎判崩溃、`_recover_crash` 重开才录上，这张平白记一次崩溃（测试 `test_a_held_document_answered_after_the_batch_finished_reopens_express`
  在旧代码上 `crashes == 1`）。

## 2. 口径（已实现）

CC 宿主按「写账件号」分辨两种来单：

| 来单 | 这张的状态 | 做法 |
|---|---|---|
| **换了写账件号**（站点按原批次号交来 = 人的新指令：作答、单张重试、整批继续） | 已存 `SAVED` | 不重录，回原单号；不为它重开这一批 |
| | 等人 `WAITING` / 跳过 `SKIPPED` | 换上新草稿、回「还没开始」重录 |
| | 停下时正在录（结果不明，见下「半路停下」） | 不当新单：回「正在录」、开录前行数留着，走原有续录——先读账套这之后新增的行，找到同对方同日同金额的就记已存（`express_restarted`），找不到才重录 |
| | 批次已停 | 重新打开这一批：状态回 `ready`、清停止 / 暂停标记、原因与画面文字；第一张照常重开 Express、重新核登录与公司 |
| **同一个写账件再派来**（租约过期后派回原电脑、CC 重启后重发） | 任何状态 | 不是新指令：原样不动，照上次的结果回执（已存回单号、停止跳过回停止、等人回原因）；还在「正在录」的照原有续录 |
| 第一次来的单（批次已停） | — | 照旧按停止跳过回执（人点停止后剩下没轮到的单不变） |

**半路停下**（新）：整批停下时正在录的那张不再留在「正在录」，也不当跳过：

- 人点的停止 → 记「停止跳过」（`SKIPPED/stopped`），回执 `stopped`（原来先回「等」、再派来才回停止，现在一次回对）；
- 整批停的原因（服务器、登录、公司……）→ 记「等人」，原因同整批；`network_down` 第一次照旧回「等」，同一件再派来回要人处理（原因同整批），
  不会「等」来回转；
- 都标上 `recheck`：下一次新指令先读账套判断存没存。

没交回来的单保持原状、不自动重录。批次文件新增两项都有默认值：`DocState.task`（最后处理它的写账件号，默认空）、`DocState.recheck`
（默认否）；旧文件没有这两项照样读（测试 `test_an_old_batch_file_without_the_new_fields_still_loads_and_continues`）。旧文件里 `task`
为空的单再交来一律按新指令（升级当口回执丢了又重派的极少数单会重录一次；没写成的才会是等人 / 跳过，不重复入账；已存的不重录）。

服务端：

- **单张重试**：页面不再走桥 `retry`（CC 本机重录不了已经回执给站点的单），改调 `task_answer`，`answers = []`——与作答同一条路
  （`tasks.answer`）：站点把这张按原批次号重交给原电脑。`api.task_answer` 放开空列表。对话里交代的「重做」（`push_control` → 页面
  `take_controls`）也改走这条，不再走桥。网页、手机、CC 重启后恢复出来的任务也能重试（原来因为要走桥才不给）；交单时就没收的单
  （`refused`）照旧只能跳过。
- **整批继续**：新增 `tasks.resume` / `api.task_resume`。只给本人（`_own`）、过产品权限；先以站点为准收尾一次（同 `settle`），任务须在
  「需要你处理 / 已停止」；只重交「因整批停没录成」的行：原因码是 `[OPERATOR_STOPPED]`（停止跳过）或 `[OPERATOR_<BATCH_STOPPERS>]`
  （卡住的那张），别的未完成的单（要人选、负库存……）不动，走单张重试。前端整批那条（非登录类）「继续」改调它；登录类照旧先「设置」
  （CC 授权），授权成功后自动接着调 `resume`。人自己点「停止」的任务批次原因为空，不出整批「继续」（不变）。
- `answer` 对 `not_done` 以外的限制核对后保留：已入账 `saved` 不再交；还在队列 `queued` 不交（在路上，重交会多一件）；已撤回
  `cancelled` 不交（人撤的）。补两处：任务记录里还记着「排队」的先问产品一次（CC 已回执、任务还没收尾时页面就能点重试）；推送结果不明
  （`push.result_unknown`）的不重交——要人先在 Express 核对（页面给的是「已入账 / 没有入账」，不是重试）。

## 3. 核实结论：写账件号

| 问题 | 结论（读代码） |
|---|---|
| `Host.handle` 原来拿到的 `task_id` 是不是写账件号 | **不全是**。写账线传的是 `str(task.id)` = 领单回包的 `log_id`（投递号）。ERP 共用通道 `log_id` 就是写账件（`cc_desktop/jobs.py::_job` 传 `doc.name`），重派「新 fence、同一个编号」；Cowork 通道 `log_id` 是**投递记录**（`companion/transport.py::_deliver` → `delivery_store.create`），丢在电脑手里重发时**换新**，同一条推送记录不变的是 `task_key`（`queue_contracts.task_key(record)`，1.12.11 起下发） |
| 改法 | 写账线改传 `task_journal.key_of(task)`（有 `task_key` 用它，否则用 `log_id`）——与本机「写过没有」记账同一个键。批次号缺省 `single-<键>`、草稿单据号缺省（载荷没有 `operator.batch.doc` 时）也随之用稳定键 |
| 新指令是不是一定换了写账件号 | **是**。Cowork：`operator_source.submit` → `tools._push_one` → `_submit_one` 的请求号 `_key(name, record_version, last_push_record)`，上一次推送记录号进了种子，每次新建一条推送记录 → 新 `task_key`；票上 `last_push_status` 为 `manual`（CC 回执要人处理）才放行，`pending/success` 拦下。ERP：`operator.submit` → `push.release_operator`（上一件代录写账件是 `manual` 就撤掉、单据回排队）→ `push.send` 新建写账件（请求号 `erp:operator:<批次>:<单号>#<第几件>`）→ 新 `log_id` |
| 停止跳过的单在站点上是什么状态、原因码 | CC 回执 `needs_review`，`error = "[OPERATOR_STOPPED] stopped"`（`worker._ack_operator`）；回执规则转 `manual`、原因照抄（`cc_desktop/wire/ack_rules.acknowledgement`）。Cowork 抄到票上 `last_push_reason`（`history/push_state.mirror`），ERP 抄到单据 `ledger_reason`（`express/push.job_changed`，`ledger_state = held`）。两边 `status` 钩子都回 `not_done` + 原文，任务记录行 `reason` 就是 `[OPERATOR_STOPPED] stopped`；卡住的那张是 `[OPERATOR_LOGIN_FAILED] …` 等 |

## 4. 改了哪些文件

| 文件 | 改动 |
|---|---|
| `companion/src/companion/express_operator/batch.py` | `DocState.task` / `recheck`；`Runner.add(draft, task)` 分同一件 / 新指令；`_reopen_batch`；`park`（半路停下）；`run_one` 停下分支与收尾；`_close` 退出后标「下一张先重开」、清单据窗口 |
| `companion/src/companion/express_operator/host.py` | `handle` 把写账件号交给 `add`；整批停（`NeedHuman`）时 `park` 半路那张 |
| `companion/src/companion/worker.py` | 两处 `handle(...)` 改传 `task_journal.key_of(task)` |
| `apps/pearnly_core/pearnly_core/express_operator/tasks.py` | 抽出 `_refresh` / `_conclude`（`settle` 行为不变）；`_resubmit`；`answer` 先问排队行、拒结果不明；新增 `resume`、`RESUMABLE` |
| `apps/pearnly_core/pearnly_core/express_operator/api.py` | `task_answer` 允许空答案（单张重试）；新增 `task_resume` |
| `frontend/assistant/src/components/operator/{types,live-source,mock-source,controller,panel}.ts(x)` | 数据源 `retry` 改走 `task_answer`、新增 `resume`（`task_resume`）；对话交代的重做改走站点；`Operator.resume`；授权成功后登录类整批接着继续；整批那条非登录类改调 `resume`；单张重试不再要求桥 |
| `companion/tests/test_operator_resume.py`（新）、`tests/test_express_operator_tasks.py`、`tests/test_cc_operator.py` | 见 §5 |

新增界面文案：**无**（按钮沿用「重试」「设置」）。`python3 scripts/check-copy.py --report`：新增 0 · 改动 0 · 删除 0 · 拦截 0。

## 5. 测试与闸门（本地，未提交改动所在工作树，基线 `790dc639b`）

CC 侧新测试 `companion/tests/test_operator_resume.py`（8 条），每条口径一条以上；在旧代码上跑 6 条失败（另 2 条是原有行为的回归保护：已存的回原单号、
录完后作答——后者旧代码靠崩溃重开也能录上，改为断言不记崩溃后旧代码失败）：

| 用例 | 口径 |
|---|---|
| `test_after_a_login_failure_the_site_hands_the_documents_back_and_the_batch_reopens` | 登录失败整批停 → 剩下的停止跳过 → 新写账件重交两张 → 重新打开、重开 Express 核登录、两张都录上 |
| `test_after_a_stop_the_same_job_sent_again_keeps_its_answer_and_a_new_one_enters_it` | 人点停止；同一件再派来照上次；单张重试（新件）重录；没交回来的保持停止跳过；重试那件再派来回原单号 |
| `test_a_saved_document_handed_back_returns_its_number_without_entering_again` | 已存的重交回原单号、不重录、不为它重开批次 |
| `test_a_document_stopped_halfway_is_checked_in_the_book_before_it_is_entered_again` | 停下时正在录（Express 其实已存）→ 记停止跳过 + recheck；同一件再派来照上次；新指令先读账套、记已存，账套里只一张 |
| `test_a_server_outage_answers_waiting_once_then_the_same_job_is_not_looped` | `network_down` 第一次回「等」；同一件再派来回要人处理（不来回转）；整批继续后先读账套、没存才重录 |
| `test_a_held_document_answered_after_the_batch_finished_reopens_express` | 录完已退出 Express 后作答：先重开，不记崩溃 |
| `test_an_old_batch_file_without_the_new_fields_still_loads_and_continues` | 旧批次文件（无 `task` / `recheck`）能读、能接着录 |
| `test_the_writing_line_passes_the_stable_job_key_not_the_delivery_number` | 写账线传 `task_key`（Cowork），没有时传 `log_id`（ERP） |

服务端 `tests/test_express_operator_tasks.py` 新增 3 条：重试（空答案）重交停止跳过的单、先问排队行、拒结果不明 / 已入账；`resume` 只交
整批停留下的行、任务状态推回排队、再点被拒；`resume` 的拒绝（还有单在路上、没有可继续的、别人的、公司权限收回、准备中）。
前端 `tests/test_cc_operator.py`：网页与桌面窗口的 `retry` / `resume` 走站点不走桥、对话里的重做走站点、整批那条非登录类调 `resume`、
网页 / CC 重启后恢复出来的任务停下等人的单能重试、假数据源 `resume`。

| 检查 | 结果 |
|---|---|
| CC 全套 `companion/tests`（Python 3.12.14 临时 venv：pytest、httpx、dbf、aenum、PySide6_Essentials，`QT_QPA_PLATFORM=offscreen`） | 1454 通过、46 跳过、0 失败（44 秒） |
| `python3 scripts/check-architecture.py` | PASS（0 violations） |
| `python3 scripts/check-copy.py --report` | 新增 0 · 改动 0 · 删除 0 · 拦截 0 |
| `python3 scripts/check-unbounded-queries.py` | 通过（存量 332 处，未超出基线） |
| `python3 tests/test_express_operator_tasks.py` | 15 条通过 |
| `python3 tests/test_cc_operator.py`（三语） | 14 条通过 |
| `npm --prefix frontend/assistant run build`（`tsc --noEmit` + vite） | 通过 |
| 本地全闸门 `gate.sh`（照 `.github/workflows/development.yml` 全部 run 步骤，Python 3.12.14） | 第一遍（未提交工作树，与 CC 全套测试同时跑）`tests/test_erp_express_direct.py` 失败 1 步，其余通过；该文件单独连跑 4 遍都通过（166 条），本任务没改到它测的代码。第二遍在已提交的 `2b8b16877` 上：171 步全部通过、0 失败 |

### 共用件连带回归闸门（`python3 scripts/check-impact.py`）

命中 `cc-operator-target`；登记表完整（55 个共用件、201 个入口），本次没有新调用方要登记。

| 入口 | 结果 |
|---|---|
| `cc.desktop.operator` | 通过（离线：CC 全套代录测试，含本次 8 条）；实机没测到（不动测试机 / 录制机） |
| `cc.desktop.write` | 没测到（要实验栈 + 测试机 C 盘 T 账套；直写代码没改，`worker` 只改代录两处传参） |
| `cc.desktop.places` | 没测到（不动测试机；本次没改位置与账套上报） |
| `cowork.cc.channel` | 没测到（要 testcowork 与测试机；本次没改通道） |
| `erp.cc.connect` | 没测到（要测试机 CC 配对；本次没改连接） |
| `cc.operator.page` | 通过（离线：`tests/test_cc_operator.py` 三语）；三端实机没测到 |
| `erp.cc.stock-transfer` | 没测到（本次没改调拨；只因 `worker.py` 同文件被列出） |
| `cc.operator.thread` | 通过（离线：`tests/test_express_operator_tasks.py`、`tests/test_cc_operator.py`）；实机没测到 |

## 6. 没测到的

- **Windows 测试机真机没测**：任务书要求不动测试机。要在测试机上验：Cowork 与 ERP 各开一个实操任务，(a) 断开 Express 登录授权让第一张
  登录失败 → 在 CC 授权 → 自动接着录完；(b) 录到一半点停止 → 点单张重试；(c) 一张停在要选的单 → 作答后录成；(d) 拔网线超过 10 分钟
  → 恢复后点「继续」，那张不重复入账。
- MR.ERP 执行层不在 main（另一分支），只验到共用 Runner / Host 的口径；那条分支合入后同一套生效。
- 没有部署，生产与测试租户都还是旧行为。

## 7. 收尾

见交付说明（分支、提交、未推送原因）。
