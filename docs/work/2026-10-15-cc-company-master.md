# 2026-10-15 CC 公司主档（A 服务端包）

- 状态：A 包本次范围完成，待审查；**未推送、未合并 `main`、未部署**；用户还没在测试机试过。
- 架构决定：[ADR-020](../adr/0020-cc-company-master.md)。
- 仓库 / 分支：`pearnly/cc-company-core2`，基线 `9f434323`（集成分支 `pearnly/cc-night-batch` 当时的头）；集成目标
  `pearnly/cc-night-batch`，由集成者叠合 B（前端）与 C（CC 桌面版 `be797d1c`，分支 `pearnly/cc-company-desktop`）。
- 规格：`~/pearnly-devtools/night-2026-10-15/company-master-spec.md`（两份设计合成）与补充 `company-master-amend.md`
  （分店、待处理、与科目建议的衔接；冲突处以补充为准）。上一版（按个人分组）的半成品 `wf_d7546dc0-29d-1` 只作参考，
  没有改动它。

## 用户原话（2026-10-15 凌晨，主会话）

- 「左侧栏可不可以做成按公司分类，新建公司（基础信息）为主档，然后在公司里新增对话+本次处理任务的文件夹和资料」，选择
  「现在停下，改成主档模式再做」。
- 「这样还可以为这个公司填写一点基础的知识库一样的东西」。
- 「注意一点，exp这边，主档公司或者供应商存在分店的可能性如何兼容处理哦」。
- 「设计好规划好，让会计使用更智能，体验到简单便捷智能都具备的一个新工具，智能化便捷化准确化」。

## 规格摘要（本包落地的部分）

| 规格节 | 落点 |
| --- | --- |
| §3.1 会话三字段 + 补充 甲.2 `project_scope` | `pearnly_assistant_session.json`：`project`(140, 索引) / `period`(7) / `project_scope`(40) / `project_noted`(64) |
| §4.1 共用层接口 | `C/assistant/api.py`：`projects` GET、`create(project, period, scope)`、`move(session_id, project, period, scope)`、`listing(…, grouped, group)`；逻辑在 `projects.py`、`sessions.py` |
| provider 约定 | `S/companies/provider.py`：`KIND="company"`、`PERIOD=True`、`SCOPE=True`、`allowed / view / context / valid_scope` |
| §3.2–3.4 主档、身份、知识库 | `S/companies/{identity,service,notes}.py`；DocType `Cowork Company`、`Cowork Company Note` |
| §3.5 文件夹绑定 | `S/folder_intake/company_folders.py`；DocType `Cowork Company Folder`（`binding_key`、预留 `reader_device`） |
| §4.2 Cowork 接口 | `S/companies/api.py`：`candidates / companies / create / detail / rename / delete / note_save / note_delete / folder_bind / folder_unbind / choice_forget / choice_set`，另加 `combined_vat`、`pending`、`pending_counts` |
| §4.3 文件夹同步 | `MAX_ROOTS=200`、`FOLDERS_TOTAL=20000`、`roots_only` 快速同步、回复带 `max_roots` / `roots_in_use` |
| §5 记住的选择 | `S/companies/choices.py`（Express 七类、POS 版式、收入对账预设、Express 历史读接口位） |
| §6 CC 上下文 | `S/companies/context.py`（纯函数）+ `service.context_text`；共用层 `projects.note` / `sessions.send` |
| §7 月份子文件夹 | `S/companies/months.py`（纯函数） |
| §8 CC 工具 | `S/assistant/company_tools.py`（4 个工具 + 3 条 GUIDANCE）；`tools.py` 只改 `cowork_folder_task_options` 的说明 |
| §9 实时事件 | `pearnly.project`：`move{session_id, project, period, scope}`、`upsert{project, part}`、`delete{id}` |
| 补充 甲 分店 | 身份 `tax:{税号}` 不带分店；分店清单现算；`project_scope`；上下文分店说明；`route_branch` 纯函数；`combined_vat`；草稿带分店；对方档按税号合并显示与删除 |
| 补充 乙 待处理 | `S/companies/pending.py`：`pending / pending_counts / morning`；接在 `folder_intake.schedule.notices`（经 hooks `cowork_scheduled_notices`） |
| 补充 丙 历史科目 | `choices.history_rows`：hooks `cowork_company_history_choices` / `cowork_company_history_forget`（科目建议那一包合入后登记） |

## 文件归属（本包）

- Core：`assistant/{projects.py(新), sessions.py, api.py, contracts.py, sidebar.py, notices.py, push.py}`、会话与提醒两张表的
  JSON。
- Cowork：新域 `companies/`（`__init__, access, api, choices, context, identity, months, notes, pending, provider, service`）、
  `assistant/company_tools.py`（新）、`assistant/tools.py`（一句说明）、三张新 DocType、`folder_intake/{company_folders(新),
  access, agent, contracts, machine, schedule}`、`connections/service.py`（只加 `account_set_facts`）、`push/choices.py`
  （加 `of_sets / forget`）、`pos_import/layouts.py`（加 `of_companies`）、`tax/service.py`（加 `drafts_of`）、
  `contracts/history.py`（加 `MONTH_WORDS`）、`hooks.py`、`i18n/messages.tsv` 与生成的 `translations/*`。
- 测试：新 `tests/test_cowork_companies.py`；`tests/test_assistant.py`、`tests/test_cowork_folder_intake.py`；为新键改的断言：
  `tests/test_cowork_assistant_tools.py`（hooks 列表写法）、`tests/test_cowork_folder_reminders.py`（替身加 `get_hooks`、
  新增 hooks 任务一条）、`tests/test_cowork_folder_tasks_mrerp.py`（同步回复多两个键）；`.github/workflows/development.yml`
  加一步跑 `test_cowork_companies.py`。
- 文档：ADR-020、`architecture.md`、`architecture-policy.json`（新域 `companies`，`assistant` 追加依赖）、本记录。
- 没碰：`frontend/**`、`companion/**`、`tests/test_companion_contract.py`（C 包拥有）、并行三包的文件（`thread.tsx` 等、
  `push/application/service.py`、`targets/account_match.py`、`contracts/push.py`）。

## 自主决定（规格 §16 之外的）

见交付说明的 decisions；要点：分店信息不存表（账套现算）；`project_scope` 由 provider 的 `valid_scope` 认 5 位分店号；
`companies` 接口直接回列表（页面按「不满一页就是最后一页」翻）；侧栏副标题只给税号；待处理不含「仅识别」的单、刚上传
15 分钟内的不算停着；早上汇总 08:30–10:30（曼谷）之间每 5 分钟看一次、每人每天最多一条；CC 工具单独放
`company_tools.py` 以减少与并行包的冲突；没挂对话的提醒多一个 `view` 字段，点开去「待处理」。

## 验证

| 代码 | 环境 | 场景 / 命令 | 结果 |
| --- | --- | --- | --- |
| 本分支工作树 | 本机 python 3.12（`~/pearnly-devtools/bin`） | `SKIP_NPM_CI=1 ~/pearnly-devtools/gate.sh`（同 `development.yml`） | 全绿 |
| 同上 | 同上 | `tests/test_cowork_companies.py`（41 条）、`tests/test_assistant.py`（120 条）、`tests/test_cowork_folder_intake.py`（111 条） | 通过 |
| 同上 | 同上 | `python3 scripts/check-architecture.py`、`i18n-build.py --check`、`check-i18n.py --strict` | 通过 |
| 同上 | 同上 | `tests/test_companion_contract.py`（C 包叠合前，服务端那一半：`roots_only / max_roots / roots_in_use`、`MAX_ROOTS / FOLDERS_TOTAL`） | 现有 40 条通过；`be797d1c` 新增的 `CompanyFolders` 由集成者叠合后复跑 |

没有做：生产镜像上的候选离线检查（改了 `pearnly_core`，合入前要跑 `ci-candidate-checks offline`，含要 cryptography 的
`tests/test_assistant_push.py`）、真实 Site 迁移、测试机、真 iPhone。

## 前端包（B）要点（并自 B 工作树 `wf_bc483668-71b-2` 的 `docs/work/2026-10-15-cc-company-master-ui.md`）

- 分支 `pearnly/cc-company-ui2`（基线 `9f434323`），已叠进 `pearnly/cc-night-batch`（`b376011c`）。只在 Cowork 出现
  （共用层 `projects()` 的 `kind = "company"`），ERP / DMS 侧栏照旧。
- 新增 / 改动的入口：侧栏「待处理 N」（N=0 不显示）、侧栏「公司」与公司页、侧栏公司组（组头名 → 主档页、＋ → 这家新对话
  默认上个月、待处理数）、添加公司对话框（只从账套目录建）、公司主档页 `?company=`、待处理视图 `?view=pending[&company=]`、
  对话菜单「移到 ▸」、顶栏「公司 ›」与月份 / 分店胶囊、新对话输入框上方公司条（放在 `Welcome`，不改 `thread.tsx`）、
  定时任务页 `?view=tasks&company=`。
- 去掉的入口：设置「此电脑 → 网页可读取的文件夹」（桌面版声明 `company_folders` 时）、网页「连接」每台电脑下的文件夹
  清单；旧桌面版保留旧块过渡，`legacy_roots` 不为空才出「以前授权的文件夹」。
- 证据（当时）：`CompanySidebarBehaviourTests`、`CompanyPageBehaviourTests`、`CompanyHelpersBehaviourTests` 等离线测试。

## 对齐与补齐（2026-10-15，分支 `pearnly/cc-company-align`，基线 `f88ef573` = 集成分支当时的头）

| # | 问题 | 做法 | 落点 | 测试 |
|---|---|---|---|---|
| 1 | 待处理「当场选」两端形状不一致（服务端只给 `choice{connection, account_set_key, kinds}`，页面读 `choices{needed, options}`，当场选永远不出现；审查定为 major）；只记选择不会重推 | 服务端列表维持轻量；页面点开那一行才取 `push.application.api.choices`（复核页、CC 同一套判定）；作答走新接口 `assistant.pending_api.answer`：`save_choices` 后按 CC `cowork_push_documents` 同一条路（`tools._push_one`）重推，推出去后推送状态变了就从「待处理」消失。可选值没读到（科目表在读）的那张就地「交给 CC」 | `assistant/pending_api.py`（新）、`components/pending.tsx`、`lib/companies.ts` | 跨端契约 `tests/fixtures/cc-pending-contract.json`：`PendingContractTests`（服务端真实的 pending 项、计数、task_overview）、`PendingChoicesContract`（真实 `choices_view` 输出）、`PendingAnswerTests`（记选择 → 重推、坏输入、无去向、无权限）、页面 `test_choosing_in_place_follows_the_server_contract` 用同一份当假数据 |
| 1b | `pending_counts` 形状 | 服务端 `{total, companies, unassigned}`，页面 `parseCounts` 读 `companies`/`total`，原本就对得上；纳入同一份契约 | — | 同上 |
| 2 | Express 历史学到的科目没接进主档 | readback 域实现两个钩子：`history.company_choices`（这家公司各 Express 账套里推送时会直接用的「供应商 → 科目、次数、最后日期」，按供应商键 = 税号或 `name:` 合并分店）、`history.company_forget`（记在 `<摘要>.forgotten.json`，与汇总分开存，重读不冲掉；删了这家供应商在这份账套里不再按历史用，也不再按历史建议） | `readback/history.py`、`history_rules.py`、`hooks.py` | `SettledHistory`（列出、删、幂等、重读不冲掉、各年度各判）、`TaskOverviewTests.test_the_hooks_are_declared` |
| 3 | 历史很一致的直接用（用户 2026-10-15 同意） | `history_rules.settled`：合并后只有一个科目、近 12 个月 ≥3 张、最近一次在 12 个月内、这份历史 31 天内读过 → `for_push` 回 `settled`；推送准备 `_decisions` 在要选费用科目、这家没被人选过、没有任务默认时，把它当已记住的选择用（只这一次判断，不写账套记忆；科目须仍在科目表里），`decision["history"]` 标来源；不改 Express 写入格式 | `push/application/service.py`（`_settled_account`） | `SettledRules`、`PushDecisions`（一致 ≥3 自动用、<3 / 不一致仍要选、删了历史后不自动用、手选与任务默认优先、科目不在表里不用） |
| 4 | 分店路由没接进推送（补充 甲.3） | `targets/account_match` 加 `BRANCH_MISMATCH`：同一法人在这条连接里有 ≥2 个分店的账套时，进项看票面买方分店（销项看卖方分店，写「สำนักงานใหญ่ / Head office」的算 00000），票面没有就用 CC 对话的分店（钩子 `cowork_push_work_branch` → `companies.provider.work_branch`，只在推往这段对话所挂公司的账套时给）；与所选账套分店不同就按现有硬闸拦，不静默改。只有一个分店的公司不按分店拦（分店号无校验位、识别常读错） | `targets/account_match.py`、`push/application/service.py`、`companies/provider.py` | `BranchRouting`（两分店账套、票面分店不符拦、无分店用会话分店、对话没定不拦、单分店不拦、销项看卖方）、`test_the_conversation_branch_is_given_only_for_this_companys_sets` |
| 5a | 侧栏每家「⏱ 定时任务 N」 | 新接口 `companies.api.task_overview`：任务表一次查询，按账套现算归属（同 `attribution`），`alert` = 有要人处理的票（`rules.results`，按文件行戳缓存）或文件夹不在；页面一次取全部（定时任务事件 / 主档建删 / 回到前台再取，不新开轮询），不再逐家读 detail | `companies/service.py`、`api.py`、`projects.tsx` `CompanyTasks`、`thread-list.tsx` | `TaskOverviewTests`、`test_each_company_group_shows_its_scheduled_tasks` |
| 5b | 总览按公司分组 | `groupTasks`：侧栏顺序的公司在前，其余按名称，归不上的放「其他」 | `scheduled-tasks.tsx`、`lib/companies.ts` | `test_scheduled_tasks_group_by_company_and_seed_from_the_company` |
| 5c | 提醒 / 简报点开到这家主档页 | 共用层提醒 `view` 认 `company:<编号>`（存放字段 20 字，编号是 10 位 hash），`deep_link` 给 `/assistant?company=`；`folder_intake.notify.send` 经钩子 `cowork_rules_company`（`companies.service.company_of_rules`，按收件人能用的连接算）只在这条提醒的任务全属于同一家时带上；页面通知按 `view` 点开 | `pearnly_core/assistant/contracts.py`、`folder_intake/notify.py`、`lib/notify.ts`、`lib/desktop.ts`、`lib/nav.ts` | `test_a_notice_can_open_a_company_page`、`test_a_notice_of_one_companys_tasks_opens_that_company`、`test_a_notice_opens_the_company_only_when_all_its_tasks_belong_to_it` |
| 5d | 新建任务统一从公司里建 | 总览「＋」先开添加公司对话框（标题「选择公司」，选已添加的或新加一家），再按主档页同一个 `taskSeedOf` 预填覆盖本月的账套与本机已绑定的文件夹 | `scheduled-tasks.tsx`、`add-company.tsx`、`company-page.tsx` | `test_new_tasks_start_from_a_company` |
| 6 | 主档页「对话」只列侧栏已加载的 | 改为分组列表 `group=<公司>` 分页取（「显示更多」），这家的对话进出时重取 | `company-page.tsx` `Conversations` | `test_the_company_page_lists_its_own_chats_from_the_server` |
| 审查 1（major） | 主档页改「付款方式 / 抵不抵进项」只写一份账套 | 写进这家公司在这条连接上所有记过这一条的账套（每份用它自己最近那条的归类键），再加覆盖这个月的各分店账套（没给月份 = 最新年度各分店）；页面把当前月份传给 `choice_set` | `companies/choices.py` `set_value`、`company-page.tsx` | `test_changing_a_choice_reaches_every_branch_of_the_month`、`test_only_payment_and_vat_can_be_set_and_they_go_to_every_set_that_remembered_it` |
| 审查 2 | 删 / 改只覆盖最新 20 份账套 | `service.books(row, limit=None)` 给全集，只在页面展示截到 20 | `companies/service.py`、`api.py` | `test_forget_and_change_use_every_set_not_only_the_page_s_twenty` |
| 审查 3 | 页面把站点时区裸时间当 UTC（晚 7 小时） | `detail` 把知识库、记住的选择、申报草稿、建档时间换成带时区的 UTC ISO（`service.when`，复用共用层 `utc_iso`）；页面 `formatWhen` 不再补 Z，只有日期的只显示日期 | `companies/service.py`、`lib/companies.ts` | `test_detail_has_every_block_and_hides_what_the_user_cannot_see`、页面时间断言 |

新文案：页面 `co.other`（Other / 其他 / อื่น ๆ）、`co.chooseCompany`（Choose company / 选择公司 / เลือกบริษัท）；服务端
`cowork.company.pending.no_target` 与分店不符的硬闸一句（四列）。`targets/account_match.py` 里的泰文「总公司」写法登记进
`scripts/i18n/keep-cjk.tsv`（数据，不进界面）。

自主决定：
- 当场选的接口放在 `assistant` 域（`pending_api`）：它依赖 `companies` 与 `push`，直接复用 CC 的 `_push_one`，不经钩子、不成环。
- 历史学到的只列「推送时会直接用」的（很一致的），不一致的只是建议、不进「记住的选择」；删了连建议也不再按历史给。
- 分店硬闸只在同一法人有 ≥2 个分店的账套时生效。
- 侧栏「定时任务 N」N=0 不出现；归不上公司的任务只在总览「其他」里。
- CC 桌面版托盘里的提醒点开仍是对话或定时任务页（桌面版 `tomo_links` 没改，改要发新版）；网页推送与页面通知按公司开。

## 验证（对齐包）

| 代码 | 环境 | 场景 / 命令 | 结果 |
| --- | --- | --- | --- |
| `pearnly/cc-company-align` 工作树 | 本机 python 3.12（`~/pearnly-devtools/bin`）、node 22 | `tests/test_cowork_companies.py`（53）、`test_cowork_account_suggest.py`（34）、`test_cowork_push_direction.py`（10）、`test_cowork_folder_intake.py`、`test_cowork_folder_reminders.py`、`test_assistant.py`（122）、`test_assistant_ui.py`（78）、`test_companion_contract.py`、`test_cowork_push_fixed.py`、`test_cowork_assistant_tools.py` | 通过 |
| 同上 | 同上 | `check-architecture.py`、`check-frontend-tokens.py`、`check-frontend-i18n.py`、`i18n-build.py --check`、`check-i18n.py --strict`、`npm --prefix frontend/assistant run build` | 通过 |
| 同上 | 同上 | 全闸门 `SKIP_NPM_CI=1 ~/pearnly-devtools/gate.sh`（同 `development.yml`，78 步） | 全绿 |

没有做：生产镜像上的候选离线检查（改了 `pearnly_core/assistant/contracts.py`，合入前要跑 `ci-candidate-checks offline`，
含要 cryptography 的 `tests/test_assistant_push.py`、`test_cowork_targets.py` 等）、真实 Site、测试机、真机。

## 公司对话的默认范围（2026-10-15，分支 `pearnly/cc-company-scope`，基线 `0069d6d6` = 集成分支当时的头）

测试机实测（04:3x，testcowork）：在主档「T.8 บริษัท ซินเซียร์ไอซ์ จำกัด」的 9 月对话里问「เดือนนี้มีบิลอะไรเข้า Express ไปแล้วบ้าง」，
CC 调 `cowork_list_documents` 没带账套，答出整个工作空间 9 月已推送的 128 张（demo01/02/03 三家别的公司的账套）。

口径：公司对话里，按公司可筛的读工具调用时没点账套、连接或公司，服务端默认只取这家公司的账套（`companies.service.work_sets`：
`books` 全集，含各年度、各分店；对话指定了分店只取该分店的，该分店没有自己的账套时仍取全部，同给 CC 的公司说明）。会计员问
所有公司 / 别家（ทุกเจ้า、เจ้าอื่น）时模型传 `all_companies: true` 放开，「所有公司逐家覆盖」照旧按 `by_account_set` 逐家列；
问另一家时传 `company`（主档编号）。结果带 `scope`（这次按哪家、是否分店、几份账套；或 `all_companies`）。
没挂公司的对话、调用自己点了账套 / 连接的，行为不变（不碰主档、不加 `scope`）。

| 工具 | 套默认范围 | 理由 |
| --- | --- | --- |
| `cowork_list_documents`、`cowork_push_results`（同一处理函数） | 套 | 按推送账套筛：库里按这家的连接与账套键筛；各连接上的账套不一样时改为取回后逐对核对（不多圈别的连接上同名的账套）；`by_account_set` 也只数这家的 |
| `cowork_folder_tasks` | 套 | 任务带连接与账套，按同一组账套留这家的 |
| `cowork_vat_draft`、`cowork_wht_draft` | 不套 | 必填账套 / 税号，调用本来就点名了一家；申报草稿列表在 `cowork_company_profile` 里已按公司 |
| `cowork_company_profile`、`cowork_companies` | 不改 | 前者本来就默认本对话那家；后者是公司清单 |
| `cowork_intake_options`、`cowork_folder_task_options` | 不套 | 选去向的清单：会计员说「这张是某某的」要能找到别家（现有 GUIDANCE） |
| `cowork_recon_*`、`cowork_stocktake_*`、`cowork_work_*` | 不套 | 记录不带账套 / 公司，无从筛 |
| 单据详情、原件、识别结果、运行记录、回读状态 | 不套 | 按编号读一条，目标已明确 |
| 推送、改单、删除、保存选择、定时任务增改删、回读、POS 导入等写入类 | 不改 | 已有明确目标与允许卡片 |

落点：`assistant/company_tools.default_scope`（判范围，依赖 `companies`，方向已登记）、`assistant/tools.py`
`_company_scope`（不在 CC 对话里、也没给 `company` 就不导入主档模块）与 `list_documents` / `folder_tasks`、
`companies/service.work_sets`；工具说明与两处 GUIDANCE 写明口径；泰文说法登记进 `scripts/i18n/keep-cjk.tsv`。

验证：`tests/test_cowork_assistant_tools.py` `CompanyScopeTests`（9 条：公司对话只这家含两年度两分店、分店、all_companies 逐家、
点了账套不被覆盖、没挂公司不变、两条连接逐对核对、没有账套 / 点错公司、定时任务、说明与 GUIDANCE）；
`tests/test_cowork_companies.py` `test_a_company_conversation_defaults_to_that_companys_account_sets`（真 `books`：两年度、两连接、
分店、分店无自己账套、放开、点名别家、点错、已删主档）；`check-architecture.py`；全闸门。测试机未复测。

## 已知未做 / 下一批

- 合并申报的汇总算法、同税号 Express 与 MR.ERP 合成一家、公司级参考文件、批量添加、按公司分权限。
- 跨年度自动换账套（定时任务）：下一批；本批不加任务表字段、不改执行逻辑。
- CC 桌面版托盘提醒按公司点开：要改 `companion/tomo_links.py` 并发版。
- 待处理的计数每次现算（最多读 500 张 + 每条连接的目录快照一次）、`task_overview` 每个任务一次带缓存的结果查询；
  侧栏若频繁取，测试机上看一次耗时。

## 测试机要实测的

1. 待处理：造一张停在「要人选」的进项（Express），在「待处理」点开那一行 → 出现科目 / 付款下拉（建议在第一）→ 确认 →
   CC 桌面版写入 Express 后这张从列表消失、侧栏数减一；科目表还没读到时那一行给「交给 CC」。
2. 历史一致：同一供应商近 12 个月 ≥3 张都记同一科目的账套，推一张新的服务类进项 → 不再问科目、直接写这个科目；主档页
   「记住的选择 · Express 历史」看得到这一行，删掉后再推 → 又问一次。
3. 分店：一家有总公司与分店 00012 两份账套，选总公司账套推一张买方分店 00012 的进项 → 被拦（分店不符）；在指定分店
   00012 的对话里推一张没印分店的收据到总公司账套 → 被拦；推到 00012 的账套 → 通过。
4. 定时任务：侧栏公司组里「定时任务 N」数对、有失败或停下的单时有点；点开只看这家；总览按公司分组、归不上的在「其他」；
   总览「＋」先选公司再预填账套与本机文件夹。到点的提醒 / 简报（没挂对话的）点开到这家主档页。
5. 主档页「对话」列出这家的全部对话（超过一页有「显示更多」），包括侧栏还没加载到的。
6. 主档页改某供应商的付款方式（多分店公司）→ 各分店账套推送都用新值；删一条记住的选择 → 旧年度也不再借用回来；
   各处时间与本机时间一致（不再晚 7 小时）。
7. 公司默认范围：在 T.8 的 9 月对话里问「เดือนนี้มีบิลอะไรเข้า Express ไปแล้วบ้าง」→ 只答 T.8 的账套；接着问「ทุกเจ้าเลย」→ 逐家列
   （含 demo01/02/03）；在没挂公司的对话里问同一句 → 与以前一样列全部。

## 收尾

- A 包分支 `pearnly/cc-company-core2` 与工作树 `wf_bc483668-71b-1` 由集成者清理（已叠进集成分支）。
- 对齐包 `pearnly/cc-company-align` 只在本地提交，未推送、未合入、未部署；工作树交接保留，等集成者叠进
  `pearnly/cc-night-batch` 并在测试机验证后清理。
