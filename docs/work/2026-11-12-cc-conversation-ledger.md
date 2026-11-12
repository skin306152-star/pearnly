# CC 本对话账套 + 写入只走计划（A 线 11-12）

- 分支 `pearnly/cc-conversation-ledger`，基线 origin/main 32695c276，工作树 `.claude/worktrees/frosty-faraday-cdfc2b`。
- 来源：审计表「A线-CC不认上下文-审计-11-12.md」（共享笔记 local_74a4f119…），主会话 11-12 批复 D1 / D2 / D3 与分批。
- 本支范围：审计表 A1–A9、B1–B5、C2–C5 的缺省 + 端到端测试（含 R2-5 回放）。收款 A10 / A11 不动（收款窗口合入后接同一个函数）。
  第 5 批语言、第 3 / 4 批另起分支。

## 0. 需求原文与理解

用户 11-11：「网页端的写入，都是先选套帐，然后上传，识别录入，line 也是先选帐套，然后识别录入」「我对话框都选了目标帐套，还要问写入哪个帐套干嘛」。
原则：用户已给定的上下文（对话的公司、月份、分店、账套，答过记住的选择）是权威输入，CC 与服务端只用、不重判；真缺的才问，问过就用。

主会话 D1：服务端一个函数定「本对话账套」（公司 + 月份 + 分店 → 一本），存进会话新字段；右栏现在显示账套的那一处改成可选（默认按函数算、
用户可改），不另造入口；右栏、公司说明、识别缺省目标、候选、推荐、写入闸、代录、ERP 全部只认它；算出 0 本或多本才问一次、记在会话上；
前端 `lib/companies.ts` 的现算删掉改读服务端；老对话打开时按函数补算一次，多本就问一次。
D2：ERP 的 CC（直写、代录）同口径。ERP 网页 / LINE 按票据日期不动（保留项）。
D3：期间闸与 Express 真实行为一致。现状 `express_rules.targets.account_match.conflict` 按 Express 的本年 + 次年段（Message#70
「วันที่ไม่อยู่ในงวด」）拦，即「期间内含次年段照写，之外 Express 拒」，本支不改；测试机实测三种日期后对齐（见第 11 节）。

## 1. 设计

### 1.1 共用层：会话的本对话账套

- 会话表 `Pearnly Assistant Session` 加字段 `project_ledger`（Data，产品给的不透明编号，空 = 未定）。不回填（老对话按需补算）。
- 新模块 `pearnly_core.assistant.ledger`（共用层只存编号、调产品）：
  - provider（hooks `pearnly_assistant_projects` 声明的模块）可选声明：
    - `LEDGER = True`
    - `ledgers(ref, period, scope, user) -> {"options": [{"id", "label", "covers"}], "default": "<id 或空>"}`：这家（该分店）可选的
      账套，覆盖本月的在前（`covers=True`）；恰好能定一本才有默认，0 本或多本 default 为空。
  - `current(row)`：存了且仍在 options 里就用存的；没存或已失效 → 按 `ledgers` 补算，有默认就存下（不改 modified），否则 `needed`。
    没声明 LEDGER 的产品 / 没挂公司的对话回 None。
  - `of_session(session_id=None)`（CC 对话里执行时取 `frappe.local.pearnly_assistant.session_id`）、`of_task(task)`（代录任务的会话）。
  - `choose(session_id, ledger_id, by)`：页面（右栏下拉）与 CC 工具 `pearnly_conversation_ledger`（只认用户在本对话最近一张提问卡上
    选的那本，同 `sessions.file_chosen` 的 `ask.names_one_of`）共用；推 `pearnly.project` move 事件带 `ledger`。
- 会话写入点：`create`、`open_operator` 按函数算、能定就存；`move(session_id, project, period, scope, ledger=None)` 给了 ledger 校验后存，
  改了月份 / 分店没给 → 存的那本仍在新 options 里就保留（用户选过的不被冲掉），否则按新默认重算；`send` 挂了公司而没存 → 补算（老对话）。
- `_summary` 带 `ledger` 与 `ledger_label`；接口 `api.ledgers(session_id)` 回 `{"current", "options"}` 给右栏下拉。
- 给 CC 的说明：`projects.note` 在产品公司说明之后由共用层追加一段（产品无关，含当前账套，换账套就重带）：
  - 已定：本对话的写入目标是哪本；所有写入进它；不按单据日期另选、不问；用户在右栏改。
  - 未定：这个月有几本（列名称）；第一次写之前用 ask_user 问一次（选项名照列的名称），再用 `pearnly_conversation_ledger` 设。
- 新共用工具 `pearnly_conversation_ledger`（两产品都挂）：参数 `ledger`；回 `{"status": "set", ...}` 或 `{"status": "refused", "reason":
  "not_chosen_by_user" | "not_an_option"}`。

### 1.2 Cowork

- provider（`companies/provider.py`）：`service.work_sets(company, scope)` → `identity.covering(sets, period)`（没月份用当月）→ 多本时
  `current_book.pick`（记住的现用账套 / 可写优先）→ 恰好一本即默认；一本都不覆盖 → 最新一本可写的（同原右栏兜底）。
  编号 = `"<connection>|<account_set_key>"`。
- 写入闸 `assistant/write_scope.py`：CC 对话里（直写）只允许本对话账套那一对；代录按任务会话的账套；账套未定回 `ledger_needed`
  （带 options）。拒绝回包只给本对话账套，删「同期两份就问」。定时任务（`_task_scope`）与查单范围（`_company_scope`）保持公司级。
- 对话里写入目标 = 本对话账套：识别缺省目标并抄到票上；`_submit_one` 连接与账套一律取它、空账套不回落连接默认（A6）；待选候选、
  当场选、判定卡账套项、录入选项推荐只给这一本（A3、A7）；建计划前把要写的票改投它（`retarget`，被改的列进 `retargeted`）。
- 回读、增值税草稿、按账套记的选择（C2–C5）：账套缺省本对话账套，传了别的过闸；月份、分店缺省对话的。

### 1.3 ERP

- provider（`masters/cc_projects.py`）：`cc_desktop.books.for_company(tax, branch)` 按月 `account_match.for_month` → 多本时按
  `choices.ACCOUNT_SET` 记过的；编号 = 账套键。
- `express/target.pick(company_row, day, wanted=None)`：给了 `wanted` 就用那一本（不按日期另判；不在这家名单里 → `NO_BOOK`）；
  CC 对话 / 代录任务里 `prepare / stock_entry / pos_day / precheck` 经同一取法拿到 `wanted`。
- 写入闸 `express/cc_tools._other_company` 加「账套 = 本对话账套」。`NO_BOOK` 移出 `RETRY_REASONS`（A9）。
- 右栏、公司说明、`erp_company_profile` 的账套读同一个函数（A8）。

### 1.4 写入只走计划（B1–B5）

- 计划闸下沉到推送入口：Cowork `_submit_one`、ERP `push.send`，在 CC 对话里执行时都要 `write_plan.permit` 放行。
- `pearnly_precheck_answer` 的 `resume` 在对话里只记答案（B1）；`cowork_choose_document_policy` 选现用账套不当场推（B2）；
  `erp_set_printed_total` 只改数（B3）；`erp_resolve_push_result` 的「没有就写」过计划（B4）。
- 计划记下建计划时的本对话账套；开始 / 放行时变了 → 不写，回 `ledger_changed`（B5）。

### 1.5 前端

- 右栏公司卡「ชุดบัญชี」那一行改成下拉（标准件），选项与当前值来自 `api.ledgers`，选了调 `move(..., ledger)`；未定显示空值，不加文字。
- 删 `lib/companies.ts` 的 `setsForScope / setForPeriod / coveringSet / coveringOf / coveringOrNewest` 现算；「全部账套」里的覆盖本月
  高亮读服务端 `covers`。侧栏 `pearnly.project` move 事件带 `ledger` 时就地更新。

## 2–10. 影响评估（按模板）

- 同类复用：沿用会话 project / period / project_scope 的存法与 provider 声明；CC 设置沿用 `file_chosen` 的「只认提问卡答案」。
- Frappe 原生：字段随 DocType JSON；右栏下拉用现有标准件。按类通用：不针对某公司 / 账套；直写、代录、Cowork、ERP 同一函数。
- 上下游：只改 CC 对话里的写入目标；网页待处理 / 复核页、LINE、定时任务、自动推不在 CC 对话里执行，不受影响。
- 隔离权限：账套编号由产品校验（这家、本人能用）；页面 / CC 只能在 options 里选。入口：CC 网页、桌面版、手机（同一组件）。
- 共用件：`assistant.sessions / projects`、`write_scope`、`target.pick`、`write_plan` → `check-impact.py` 清单逐入口。
  发布窗口在改 `sessions.py` 读历史（`_segment_events` 等）、输入框窗口在改发送接口：本支只动 `create / move / open_operator / send`
  一行补算与 `_summary`，合入前对一遍它们的分支。
- 存储成本：一列。删除：随会话删；账套从目录消失 → 存的编号失效 → 按需重算。
- 文案：右栏沿用已有词条「ชุดบัญชี」，不加新文案；给模型的英文说明不经 `_()`。
- 部署回读：`tabPearnly Assistant Session.project_ledger` 存在；Cowork、ERP 各开一段对话右栏账套有值；`pearnly_conversation_ledger`
  进两产品工具清单。

## 11. 测试与验收

- 单测（先写，旧代码红）：R2-5 回放（选中 T.8、同期 T.9、票据日期 2026-06 → 识别缺省 T.8；计划 ready、目标 T.8；推 T.9 → 拒）；
  0 本 / 多本 → `ledger_needed`；CC 设账套只认提问卡答案；move 改月份保留 / 重算；四条绕过计划的路在对话里被拒；计划后改账套 →
  `ledger_changed`；ERP `pick(wanted=…)`、NO_BOOK 不归等电脑。
- 流程：受影响 CC 流程（`run.py --changed`）+ 新端到端：选账套 → 上传 → 识别 → 复述 → 录吧 → 写进那本，中间无重判提问。
- 真模型：Cowork / ERP × 直写 / 代录四格（约 2 美元，记台账）。测试机：TEST 账套真写 + D3 三种日期实测（找主会话排时间）。

## 12. 改投口径（主会话 11-12 同意，带三个条件）

对话里已有别的目标的票（网页 / LINE 选过别的账套）也改投本对话账套，不另问：
1. 复述里把改投的票单独列出（哪几张、原来哪本 → 这次哪本）；计划回包 `retargeted` 逐张带 `{document, from, to}`，测试钉住。
2. 只在计划开始后真推送那一刻落到票上；建计划时不写票（判定在内存里按本对话账套算），用户没点头时票上原选择不变。
3. 只在同一家公司的账套之间改投；目标是别家公司的不改投、不写，进计划 skipped，原因 `not_this_company_book`。

R2-5 真实路径（主会话 11-12 生产只读查清，会话 sesn_01XqgMQffeAyb38uf9BJrjah）：模型按 `cowork_company_profile` /
`cowork_intake_options` 里的账套与期间自己判，单据目标一直未选，最后在 `cowork_push_documents` 里自己填了账套键；没走待选账套。
所以公司说明、公司资料、录入选项在对话里只给本对话账套并明说它就是写入目标，推送不收模型另填的账套。

第一段施工（338fb5bb8..975bd1298）与规格的偏离：
- 账套未定时识别不拦、也不补目标（识别不是写入），写入时才回 `ledger_needed`。
- 停在「待选账套」的单在对话里直接写进本对话账套，不顺带记「现用账套」。
- 回读、增值税草稿传了同公司另一本也拒（`not_this_conversation_book`）。
- 老对话的补算放在 `projects.note`（发话前带公司说明时），`send` 本身一行没改（避开发布窗口在改的读历史那段）。
- 右栏下拉接口 `api.ledgers` 在前端接上前暂登记 `not_entries.toml` compat，前端那段删掉。

## 13. 工作量

写码约 14–18 小时（机器另计）：共用层 + Cowork 6–8，ERP 3–4，写入只走计划 2–3，前端 2，流程与端到端 2。

## 14. 第二段施工（759598637..e2cf313cf：ERP 本对话账套 + 写入只走计划）的偏离与行为变化

- ERP「没有账套」（NO_BOOK）移出会自己好的重试原因，改进新的 `POLL_REASONS`：网页的单仍每分钟再看（电脑报了新年度账套就接着写），
  但现在会进待处理（算要人处理），不再显示成「等电脑」；判定与计划如实报 `no_account_set`，代录报 held。**这是 ERP 网页可见的变化，请主会话知悉。**
- ERP 的模型给不了账套：对话里本对话账套不在这家名单里报「没有账套」；网页按日期准备成别的账套的单在对话里重推时改投本对话账套。
  结果不明的单再核时仍用原来那本核对（核的是当时写的那本）。
- 代录开始卡那条页面预检不带任务号，页面上仍按日期判：属第 4 批 D-1（开始卡改渲染 write_precheck），本支没动。
- B3：`erp_set_printed_total` 只改数（回 `released: false`），加了公司闸。B5：Write Plan 加字段 `ledger`，开始时账套变了回
  `ledger_changed` 并作废计划，放行时变了整批拒。
- 部署回读加：`tabPearnly Assistant Write Plan.ledger` 存在。
- 期间闸（主会话 11-12 追加）：`target.pick(wanted=…)` 原来只按名单取那一本、不看单据日期，对话里期间外的单会被直写进 Express，
  而 Express 原生录单拒（Message#70）。改为：账套报了期间、单据日期落在「本年起 ~ 次年止」之外 → 不写，原因
  `express.account_period_mismatch`（判据复用 `account_match.conflict`，没报期间不拦）；不进每分钟重试；判定 / 计划报
  `account_period_mismatch`（同 Cowork 推送的码，`write_plan.SKIP_PERIOD_MISMATCH`），代录交单照 held 报。新词条
  `express.reason.periodOutside`（四语，沿用共用层 `operator.report.reason.period_outside` 的说法），单据页同一张原因表。

## 15. 已知限制：Cowork 账套身份按电脑（主会话 11-12 定单开一支修）

- 单电脑没问题。同一本账套被两台电脑以不同写法上报（测试机1 用 UNC `\\accserver\d$\account\69exp\test`、测试机2 用映射盘
  `P:\69EXP\TEST`）时，Cowork 的账套身份是「连接 + 规范化路径」（只统一大小写与斜杠），两台各算一本：右栏下拉出现两条同名账套、
  本月算「多本」要问一次；选了哪条，写入就绑在报那条的电脑的连接上，另一台不能代写。「现用账套」的键是同一个根因，早于本支。
- ERP 不受影响：共用机器通道的 `book_key` 按文件号（`uid`，CC 1.7.0 起上报）取，与电脑和写法无关。
- 修法（单开一支，排在本支真机验完之后、第 3 批之前）：Cowork 改用 `cc_desktop.contracts.book_identity`（有 uid 用 uid，没有才用路径）
  作账套身份，本对话账套、现用账套、写入闸比对、右栏去重都按它；写入时按「谁的活谁的电脑做」挑执行电脑，换成它的连接与路径；
  老数据读时按身份映射。开工前先交一页短评估。

## 16. D3 实测（测试机1，11-12）：期间闸与 Express 一致

69EXP\TEST，CC 报的期间 2026-01-01～2027-12-31（本年 2026、次年段 2027），用 Express 原生（代录引擎）录 RR：

| 日期 | 落在 | Express | 我们的 `account_match.conflict` |
|---|---|---|---|
| 2026-06-15 | 本年 | 收（RR0000297） | 放行 |
| 2027-02-15 | 次年段 | 收（RR0000298） | 放行 |
| 2025-12-15 | 期间之前 | 拒，Message#70「วันที่ไม่อยู่ในงวด งวดบัญชีของข้อมูลนี้จะอยู่ในช่วง 01/01/2569 - 31/12/2570」，不留空单头、不占号 | 拦（account_period_mismatch） |
| 2028-02-15 | 次年段之后 | 同上拒 | 拦 |

结论：期间闸边界「本年起 ～ 次年止」就是 Express 的真实行为，Cowork（`conflict`）与 ERP（本支 f9a624d64 在本对话账套路径上复用同一判据）都不用改。
