# 2026-10-15 夜间批验收与修复

发布见 [生产发布 `2bc4aad1`](2026-10-15-release-2bc4aad1.md)。本记录收夜间批上线后验收中发现的问题与修复，一节一个问题。

## 连接只读接口死锁

分支 `pearnly/connection-read-no-lock`，基线 `a32c8d66`（当时的 `origin/main`）。

### 现象

生产 testcowork Error Log，2026-10-15 13:16 与 13:32 各一次：`GET /api/method/pearnly_cowork.companies.api.task_overview`
报 `frappe.exceptions.QueryDeadlockError (1213)`。调用栈：

`companies/api.py:126 task_overview` → `companies/service.py:628 task_overview` → `service.py:75 usable_connections`
→ `connections/service.py:212 records` → `connections/state.py:18 read` → `frappe.db.get_value(..., for_update=True)`。

### 原因

`connections/service.py: records()` 是只读列表，却先 `SELECT … LOCK IN SHARE MODE` 拿连接行的共享锁，再对每行
`state.read`（默认 `lock=True`，即 `SELECT … FOR UPDATE`）升级成排他锁。网页侧栏（`frontend/assistant/src/lib/companies.ts`）
加载、回到前台、收到推送事件时同时发 `pending_counts` 和 `task_overview`，两者在服务端都经
`companies/service.py: usable_connections()` 调 `records()`：两个事务各持同一批连接行的共享锁、又都要升级排他锁 → 死锁。
同一请求里随后的 `account_set_facts()` 还会再对连接、快照行加排他锁。只读请求持锁也会挡 CC 桌面版每分钟写连接行的心跳。

### 改动（`apps/pearnly_cowork/pearnly_cowork/connections/service.py`）

纯只读、只回 DTO 的视图改为不加锁读（`state.read(..., lock=False)`）。Frappe 在 MariaDB 用 REPEATABLE READ，
同一事务里的普通读是同一份一致快照，快照 / 刷新行与连接 `record_revision` 的比对照样成立（过期快照仍报
`connection.snapshot_invalid`，测试覆盖）。

| 函数 | 改法 |
|---|---|
| `records()` | 去掉 `LOCK IN SHARE MODE`；逐行 `lock=False` |
| `detail()` | 连接 `lock=False` |
| `targets()` | 连接、刷新行、快照 `lock=False` |
| `account_set_facts()` | 连接、快照 `lock=False` |
| `refresh_status()` | 连接、刷新行 `lock=False`（`assistant/tools.py` 轮询它：原先每轮拿了排他锁再睡 3 秒，正挡着电脑读完后写回） |
| `stock_acc_group()` | 连接 `lock=False` |
| `mrerp_menu()` | 连接 `lock=False` |

模块文件头写明这条口径。写路径一律未动：`configure`、`request_refresh`、`ensure_account_masters`、`install_snapshot`、
`read_for_push`、`preparation_for_target`、`read_for_log`、`stock_groups_for_log`、`choose_stock_acc_group`、
`choose_mrerp_menu`、`choose_mrerp_default`、`request_mrerp_menu`、`observe_mrerp_*` 仍加锁；`state.read` 默认仍是 `lock=True`。

### 调用方核对

逐个看了 `records(` / `targets(` / `detail(` / `account_set_facts(` / `refresh_status(` / `stock_acc_group(` / `mrerp_menu(`
的全部调用方，没有一个靠这些读持锁再写连接、快照或刷新行：

- `companies/service.py`：`usable_connections`、`facts` 只拼目录；`books()` 顺手补税号写的是公司主档行（自带 savepoint），与连接行锁无关。
- `assistant/tools.py`：`companion_status`、`intake_options`、`_account_names`、`_selectable` 只读；`recognize_attachments`
  校验账套后由 `ensure_account_masters` 自己加锁写；轮询 `refresh_status` 前每轮 `rollback`，不加锁读每轮拿到新快照。
- `folder_intake/rules.py`：`_catalog_item`、`_readers`、`computers`、`listing`、`_stock_ready` 只读；保存任务要写连接的
  （请电脑读主档）走 `ensure_account_masters` 自己加锁；`_connection()` 本来就是不加锁的 `frappe.get_doc`。
- `line/flow.py`：`_account_rows`、`_connections`、`_ask_stock_group` 只读，写的是 LINE 会话状态；选存货科目组走
  `choose_stock_acc_group`（自己加锁）。
- `pos_import/service.py: company_key`、`readback/service.py: _known_version / request`、`readback/history.py`：只读目录；
  之后写的是版式、回读请求等各自的表，推送时 `read_for_push` / `preparation_for_target` 重新加锁核对。
- `connections/api.py` 的 `detail`、`targets`、`records`、`refresh_status`、`stock_acc_group`、`mrerp_menu`：直接回结果。

唯一的行为差别：`readback.request`、`folder_intake.rules.save` 校验目录与随后写入之间不再挡并发的 `configure`；
这两处写的不是连接行，推送前仍由加锁的推送准备按连接版本重核，结果与原先一致。

### 公司页只读 GET 往下的锁（`companies/api.py`）

`candidates`、`companies`、`detail`、`pending`、`pending_counts`、`task_overview` 往下追：

- 已改：上面的 `records`、`account_set_facts`、`targets`（`detail` → 记住的选择 → `pos_import.company_key` 也走它）。
- 本来就不加锁：`folder_intake.agent.read`（Redis 状态，不碰库）、`folder_intake.rules.results`（普通 `SELECT` + 缓存）、
  `company_folders.view`、`notes.listing`、`choices.listing` 各来源（`push.choices.of_sets`、`pos_import.layouts.of_companies`、
  `preset_store.under`、`readback.history.company_choices`）、`tax.service.drafts_of`、`projects.mine`、`pending` 的候选查询。
- 保留：
  - `connections/access.py` 授权的当前读（用户、角色、归属行 `LOCK IN SHARE MODE`）：迁移保护要求身份与授权保护不关闭，
    写路径靠它在提交前挡住并发撤权；这些行不被这些 GET 升级成排他锁，不构成本次的死锁环。
  - `access/scope.ensure_site_workspace` 的 `for_update`：只在站点还没有归属行、要现建时才走（写路径）。
  - `companies.service.detail` 里 `books()` 补税号的 `set_value`：真写入（接口随后显式提交），不是锁读。

### 测试与闸门

- 新增 `tests/test_cowork_connection_read_locks.py`（加进 `.github/workflows/development.yml`）：真 `connections/service.py`
  跑在记录锁请求的 Frappe 替身上，断言侧栏两条读、全部只读视图都不请求行锁、SQL 不带 `LOCK IN SHARE MODE` / `FOR UPDATE`，
  过期快照不加锁仍被拒，`read_for_push`、`choose_stock_acc_group`、`state.read` 默认仍加锁。在原代码上跑 3 条失败，改后 4 条全过。
- 定向：`test_cowork_companies`、`test_cowork_any_set_catalog`、`test_cowork_folder_intake`、`test_cowork_readback`、
  `test_cowork_assistant_tools`、`test_cowork_pos_import`、`test_cowork_account_suggest`、`test_cowork_connections` 通过；
  `scripts/check-architecture.py` 0 违规。
- 全闸门（`~/pearnly-devtools/gate.sh`，照 `development.yml` 全部 run 步骤，`SKIP_NPM_CI=1`、前端依赖先离线 `npm ci`）：79 步全绿、0 失败，含新测试一步。

### 未做

- 未推送、未合并 `main`、未部署；未在测试机复现死锁，也没有并发压测验证。
- 授权当前读（`connections/access.py`）的共享锁保留未动，见上；若之后仍见同类死锁，先看栈是否落在 `tabUser` / `tabHas Role` 上再议。
