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

## 已知未做 / 下一批

- 推送前按票面分店自动选账套（补充 甲.3）：本包只给了纯函数 `identity.route_branch` 与 CC 说明，没有接进
  `push/application/service.py`（`pearnly/cc-buyerless-direction` 在改那里），集成后再接。
- 合并申报的汇总算法、同税号 Express 与 MR.ERP 合成一家、公司级参考文件、批量添加、按公司分权限。
- Express 历史科目（补充 丙）的实际数据：等科目建议那一包合入后登记两个 hooks。
- 待处理的计数每次现算（最多读 500 张 + 每条连接的目录快照一次）；侧栏若频繁取，测试机上看一次耗时。

## 收尾

- 本分支只在本地提交，未推送；工作树 `/Users/skin/pearnly/.claude/worktrees/wf_bc483668-71b-1` 交接保留，等集成者叠合
  B、C 包后在测试机验证；合入 `pearnly/cc-night-batch` 后由集成者清理。
