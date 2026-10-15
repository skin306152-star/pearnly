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
  - `connections/access.py` 授权读：起初保留未动，同日 13:48 在 `tabUser` 上报 1020，已按下一节拆开（写路径仍当前读加锁）。
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
- 授权读的后续见下一节。

## 只读视图的授权读 1020（`tabUser`）

同一分支 `pearnly/connection-read-no-lock` 第二个提交。测试窗口报来（共享笔记 F1）。

### 现象

生产 testcowork 2026-10-15 13:48:10，侧栏 `pending_counts` 与 `task_overview` 各一条（Error Log `9t6fe22lte`、`9t6s9sth4i`）：
`QueryDeadlockError (1020, "Record has changed since last read in table 'tabUser'; try restarting transaction")`。栈：
`companies/api.py:119 pending_counts` → `pending.py:230` → `pending.py:209 _attribution` → `companies/service.py:598 attribution`
→ `:99 _rows` → `:75 usable_connections` → `connections/service.py:199 records` → `connections/access.py:56 require_actor`
→ `access.py:45 _current`（`SELECT enabled FROM tabUser … LOCK IN SHARE MODE`）；`task_overview` 同样落在 `access.py:45`。
同一秒另有两条 1213（上一节那类，已修）；14:00:10 又两条 1213。

### 原因

`access._current` 对用户、角色、工作空间、客户行一律 `LOCK IN SHARE MODE`（当前读）。MariaDB 11.8（兼容矩阵）默认开
`innodb_snapshot_isolation`：事务的一致快照建立后，若该行被别的事务改过并提交，再对它加锁读就报 1020。`tabUser` 行会被
Frappe 的会话更新（`last_active`）等改到；侧栏两条 GET 并发、恰逢另一个请求刚提交用户行，就会撞上。只读视图本不需要当前读：
它不写东西，用本事务开头的快照判断「用户启用、有角色、工作空间启用」就够；需要当前读挡住提交前并发撤权的是写路径。

### 改动

- `connections/access.py`：`_current(query, values, lock=True)`，`roles / require_actor / require_scope / require_connection`
  加关键字参数 `lock=True`（默认不变，照旧当前读加共享锁）；`lock=False` 时同样的 SQL 不带 `LOCK IN SHARE MODE`。
- `connections/service.py`：七个只读视图（`detail`、`targets`、`account_set_facts`、`records`、`refresh_status`、`stock_acc_group`、
  `mrerp_menu`）与只被它们用的 `_manager_view` 的授权调用都传 `lock=False`；写路径（`configure`、`request_refresh`、
  `ensure_account_masters`、`choose_*`、`request_mrerp_menu`、`observe_mrerp_*`、`read_for_push`、`preparation_for_target`、
  `read_for_log`）不变。
- 授权判断本身没少：用户停用、无角色、工作空间停用照样拒绝（测试覆盖），只是读快照而不是加锁的当前读。连接域以外直接调
  `access.require_*` 的地方都走默认，不受影响。

### 测试

`tests/test_cowork_connection_read_locks.py` 扩充：`service.py` 的授权替身记录每次调用的 `lock`，只读视图必须全部 `lock=False`、
写路径必须是当前读；新增 `AccessReadLocks` 按原文件加载真 `access.py`：默认每句带 `LOCK IN SHARE MODE`、`lock=False` 一句都不带、
停用用户不加锁仍被拒。换回旧 `access.py` 跑，新测试 2 条报错；改后 7 条全过。

## 科目历史请求在生产全部失败（V9）

分支 `pearnly/acceptance-1015`（基于 `pearnly/connection-read-no-lock` 的 `4852b018`，之后验收期间的修复都叠在这里一并合入）。
测试窗口报来（共享笔记 F2）。

### 现象

生产 testcowork 2026-10-15 14:20:03 / 08 / 12 / 19 四条 Error Log「Cowork account history failed」：
`ValidationError: 类型不能为“account_history”。只能是“express_read", "folder_list", "folder_fetch”其中一个`，栈
`companion/request_service.py:125 create → write(row)` → `request_access.py:34 doc.insert` → Frappe `_validate_selects`。
V1 推送前查科目建议时每次都请电脑读历史、每次都失败；推送本身照常成功（落回名称建议）。生产
`sites/testcowork…/private/cowork_account_history/` 不存在，即从没存下过历史，「历史一致直接用」与主档页「来自 Express 历史 · N 次」
在生产完全不工作。

### 原因

夜间批在 `companion/request_contracts.py: KINDS` 加了 `account_history`，表 `Cowork Companion Request` 的 `kind` 下拉选项没跟着加。
离线测试用的是替身表，不跑 Frappe 的选项校验，所以没拦住；测试机 lab 也没走到真机读历史这一步。

### 改动

- `cowork_companion_request.json`：`kind` 选项加 `account_history`，`modified` 改为 `2026-10-15 15:00:00`（`bench migrate` 按 JSON 同步表定义；
  Select 列本是 varchar，不改表结构、不需要补丁）。
- `tests/test_cowork_companion_requests.py`：`test_the_request_table_accepts_every_kind_and_status` 读真表 JSON，核对 `kind` / `status`
  选项与契约 `KINDS` / `STATUSES` 一致。换回旧 JSON 跑失败，改后通过。
- 同类排查：夜间批改过的表里只有 `Cowork Company.adapter`（express / mrerp）与 `Cowork Company Note.origin`（person / cc）是下拉，
  与代码取值一致；新加的会话、提醒字段都是 Data。夜间批新增的取值常量只有 `KINDS` 落在下拉字段上。

### 未做

- 未部署；部署后要重验 V9：生产 testcowork 上推同一供应商 3 张后第 4 张直接用、主档页出现「来自 Express 历史 · N 次」、删掉后再推又问。

## 推送路径的 1020（写路径，记录不改）

测试窗口共享笔记 F3：14:20:16「Assistant tool failed」，`assistant/tools.py:1501 push_choices` → `_choices_waited` → `_choices_view`
→ `push/application/service.py:364 choices_view` → `connections/service.py read_for_push(lock=True)` →
`1020 Record has changed since last read in table 'tabCowork ERP Connection'`。

`choices_view` 先读了票（建立快照），再对连接行加锁读；中间连接行被别的事务改过并提交（CC 桌面版心跳 / 回报）就报 1020。
这是写路径的连接锁（迁移保护里的外部写者锁），按设计保留；CC 对话里表现为多等了一轮、随后推送成功，没有错写或漏写。
本仓同类写路径的处理方式是在入口捕获 `QueryDeadlockError` 回滚重试（如 `line_session`、`income_recon_api`、`inventory/api`）。
这次只记录，不改：若之后在推送准备（`prepare` / `choices_view` / 定时任务推送）里看到 1020 造成失败或用户可见的报错，再按入口重试的做法统一处理。

## 新对话建会话期间发送键还是「发送」（F4）

分支 `pearnly/acceptance-1015-ui`（基于 `pearnly/acceptance-1015` 的 `7de2848d`），F4–F6 三节同一分支。

### 现象

生产电脑 Chrome 与安卓都复现：新对话发第一句，状态行「0 秒 · 思考中…」已出现，发送键 aria-label 仍是「发送」约 4 秒才变「停止」，
这几秒里还能再发一条。已有对话、先加附件的新对话 0 秒起就是「停止」。

### 原因

`runtime-provider.tsx` 的 `useSessionThreadRuntime` 把 `isRunning: isBusy(snapshot)` 交给组件库。新对话发送时 `onNew` 先
`await ensureSession()`（服务端建会话约 1–4 秒），这期间没有会话控制器，snapshot 是空状态，`isRunning` 为假；状态行看的是
`pending-send.ts` 的发送中气泡，两边对不上。`session-controller.ts: sendMessage` 乐观 fold 的 `session.status_running` 要等会话建好才有。

### 改动

- 新增 `lib/managed-agents/new-chat-send.ts`（`NewChatSend`）：没有会话时「建会话 → 发出」期间算在跑；期间点停止先记下，发出后对
  新会话 `interrupt()`（用户那条照常发出、留在对话里）；失败照常抛出、恢复可发送，记下的停止不留到下一次。
- `runtime-provider.tsx`：每个对话实例 `useState(() => new NewChatSend())`（不用模块级的发送中气泡，后台挂着的对话互不影响），
  `isRunning: isBusy(snapshot) || creating`；`onNew` 有会话控制器（已有对话、先加附件）走原路径，没有才经 `NewChatSend.run`；
  `onCancel` 先问 `newChat.cancel()`，在建会话就不再自己打断。

### 测试

`tests/test_cc_run_indicator.py`：`test_a_new_chat_is_running_while_its_session_is_created`（建会话期间在跑、期间点停止发出后才打断且
消息还在、没点停止不打断、建会话失败恢复可发送）、`test_new_chat_wiring_is_per_conversation`（接线：每个对话一个、`isRunning` 并上、
停止先问它、已有会话走原路径）。

### 未做

- 组件在 node 里不能整页跑，`isRunning` 接线靠源码断言；未部署，部署后在生产电脑 / 安卓重看新对话第一句 0 秒起就是「停止」，
  期间点停止后用户消息在、回复被打断。

## 断网时对话顶部露出「Failed to fetch」（F5）

### 现象

安卓（界面中文）回复中途开飞行模式，30 秒后对话内状态行正常显示「连接中断，正在重连…」，对话区顶部同时出现灰字「Failed to fetch」，
恢复联网后消失。

### 原因

`assistant.tsx` 的 `SessionStatusBar` 显示 `transportError || (lastError.retryStatus === "retrying" ? lastError.message : "")`：
`transportError` 是 fetch 的原始 `err.message`（`session-controller.ts: describe`）或中继包的 `envelope.message`，`lastError.message`
是引擎原文，都可能是英文原始报错。

### 改动

- `turn.ts` 加 `showsReconnecting`（读写出错、引擎在重试、加载完了尾巴没连上时说；`stalled` 为真时不说）与 `connectionDetail`（原文）。
- `SessionStatusBar` 只显示已有词条 `t("reconnecting")`（「正在重新连接…」），原文 `console.debug` 留着排查；对话内状态行已在说
  「连接中断，正在重连…」时顶部这条不显示。未新增、未改词条。

### 测试

`tests/test_cc_run_indicator.py`：`test_the_top_line_only_says_reconnecting_in_the_page_language`（真会话控制器读事件抛
`TypeError("Failed to fetch")`：原文只在 `connectionDetail`，顶部要显示、`stalled` 时不显示；引擎重试 / 重试用完；源码里顶部只渲染
`t("reconnecting")`、不再拼原文）。

### 未做

- 未部署；部署后在安卓飞行模式重看顶部只出「正在重新连接…」或不出（对话内已在说时）。

## 公司页发第一句，新对话先闪在「最近」（F6）

### 现象

生产电脑 Chrome 14:14：公司主档页（这家还没有任何对话，公司组第一次出现）的输入框发第一句，侧栏「最近」顶部先出现
「新对话 · 9月」，约 2 秒后公司组出现并挪进去。

### 原因

主档页输入框这条路径没问题：`CompanyComposer` → `useStartIn` 先 `setDraft`，再切新对话、两帧后 `composer.send()`；
`company-page.tsx` 读到详情时已 `hintTitle`；组件库 `initialize` 里 `execute`（适配器的 `initialize`）同步先跑，`noteFilings({本地编号: 草稿})`
在组件库把这一行变成 regular 之前就记下了；侧栏归组按 `filings[remoteId] ?? filings[本地编号]`，键一致；`kind` 已加载。
「· 9月」也说明归属记上了，是侧栏里没有这一组。

真正的原因：`noteFilings` 发现这家不在侧栏，先搭出临时组，同时立刻重取 `projects()`。这次请求发在 `createSession` 之前，服务端
`projects()` 按「我有对话的公司」现算，不可能有这家；回复一到（比建会话快）就整组盖掉临时组，新对话落回「最近」。会话建好后
`initialize` 再记一次归属，临时组重新搭出、再重取，才进公司组。夜间批的测试只在 `initialize` 调用后同步断言了一次，没等那次回复。
确认方法：新测试让假站点的 `projects()` 按已建会话现算、`create` 等放行才回，修前在「建会话前发出的那次重取回来后」断言失败
（`'' !== 'comp_nipa'`），与生产现象一致。同一毛病也在「移到 ▸」侧栏还没有的公司（移动前就重取）。

### 改动

- `projects.ts`：本页新建、移动走新的 `fileLocally`：先按本页记下归属（侧栏还没有就按已知名称搭临时组），服务端收下之前不重取；
  别处触发的重取（回到前台的核对等）回来时，对这期间问的回复不拆临时组（`holds`：还没回的请求数 + 最后回来的时刻，之前问的不作数）。
  `done` 照服务端回的再记一次，临时组（或还不在侧栏）就重取一次换成服务端那一行；`undo` 撤回，临时组没别的对话就去掉，不留空组。
  之后的重取照服务端（别处删了照常去掉）。`fileSession` 移动请求在途时 `movedSince` 一律算移动过，在途读到的旧列表不改它的归属。
- `session-list-adapter.ts: initialize` 改用 `fileLocally`，建会话失败时撤回。
- 名称提示补齐：输入框上方公司条 / 「移到 ▸」的选公司菜单（`CompanyChoices`）与待处理页「交给 CC」（有公司名时）也 `hintTitle`。

### 测试

`tests/test_assistant_ui.py`（`CompanySidebarBehaviourTests`）：`test_the_first_chat_in_a_company_stays_in_its_group_while_the_session_is_created`
（这家第一段对话：建会话期间两次重取都不散，建好后重取一次换成服务端那一行，之后照服务端）、
`test_a_chat_in_an_existing_company_group_never_shows_in_recents`（已有公司组：全程在组里，不多重取）、
`test_a_failed_first_chat_leaves_no_empty_company_group`、`test_moving_into_a_company_not_in_the_sidebar_keeps_the_group_until_the_server_answers`。
新测试 8 路并行反复跑两轮都稳定。

### 未做

- 已有公司组的情况生产未测，靠测试覆盖；未部署，部署后在生产主档页给一家没有对话的公司发第一句，看侧栏直接出现公司组、不经「最近」。

## F7 页面把原始报错直接显示给用户

测试窗口共享笔记 F7：测试机 CC 桌面版主档页（14:46–14:49）与 Mac Chrome 定时任务页（14:52）顶部红色横幅显示
`(1213, 'Deadlock found when trying to get lock; try restarting transaction')` 原文（英文、带错误码）。对应 Error Log 14:48:06 两条
`companies/api.py:31 detail → … → connections/service.py records`，即本记录第一节已修的只读锁。

- 原因：`lib/frappe.ts` 已把本站拒收（`frappe.throw`，已按用户语言翻译）分成 `SiteError`，其它失败是带原文的普通 `Error`；但各页
  （公司页、定时任务页、待处理、添加公司、公司列表、选公司菜单、附件上传状态）一律 `e instanceof Error ? e.message : String(e)` 显示原文。
  死锁修好后，别的服务端异常也会这样露出。
- 改动：`lib/frappe.ts: shownError`——`SiteError` 照原文，其它只说通用的一句 `somethingWrong`（新词条：Something went wrong. Try again. /
  出了点问题，请重试。/ เกิดข้อผิดพลาด โปรดลองอีกครั้ง，照已有「Could not update. Try again.」的说法），原文 `console.debug`；上述各处都改用它。
  CC 桌面版选文件夹的回报（`picked.message`）、待处理重推的 `reason` 是本来就给人看的说法，不动。
- 测试：`tests/test_assistant_ui.py` `ShownErrorTests`：源码里除 `lib/frappe.ts` 与 `session-controller.ts` 外不得再有
  `instanceof Error ? x.message`；三种语言都有新词条；中文页面下拒收原文照显、1213 / Failed to fetch / 非 Error 都显示通用句并记进控制台。

## F8 「更换…」文件夹后另一台设备不刷新

测试窗口共享笔记 F8：测试机 14:50:26 把นิภาฯ的文件夹换成同根下子文件夹「原件」（服务端已更新），Mac 主档页 40 秒以上仍显示旧路径；
首次绑定时 1.5 秒内刷新。

- 核对：`Cowork Company Folder` 只有 `company_folders.bind` 一个写入口，新绑定与更换都经 `companies.service.folder_bind`，都
  `announce("upsert", part="folders")`；主档页收到同一家的 upsert 就重载 `detail`（不走缓存，`folder_label` 会带上新子文件夹）。
- 证据：测试窗口查 testcowork 14:50:00–14:52:00 Error Log 为 0 条，那段时间 Mac 页面也没有报错横幅——不是 1213 连带（先前的判断不成立）。
  开发窗口只读核对生产：`Cowork Company Folder` 那一行 `folder = 原件`、`label` 带 `\原件`，服务端 `company_folders.view` 此刻算出的显示名也带
  `\原件`、状态 ok。服务端数据与显示名都对，是 Mac 页面没有重取。
- 原因：主档页只靠推来的 `pearnly.project` upsert 重取，错过那一次就一直停着；不像待处理、定时任务页那样在回到前台、拿回焦点、实时通道重连时
  核对。测试用的 Mac 标签页由扩展驱动、多在后台（同 F14），后台标签页的实时连接会被浏览器节流断开，正好错过。
- 改动：`components/company-page.tsx` 接上站点通用的 `useLiveRefresh(reload, { events: TASK_EVENTS })`（电脑同步文件夹事件、回到前台、
  拿回焦点、重连时重取，另有 60 秒兜底，同待处理与定时任务页）。测试：`tests/test_assistant_ui.py` `CompanyPageLiveTests`。
- 未做：未部署；部署后在两台设备上重验（一台「更换…」，另一台前台打开主档页应在几秒内更新）。

## F9 新建定时任务默认 include_existing=1（口径确认，不改）

10-12 [CC 闭环](2026-10-12-cc-closed-loop.md) F1 定的口径：新任务到点处理文件夹里本任务还没处理过的全部文件，包括建任务前就在的；
允许卡片与表单显示「文件夹计数」（现有 N 份）。默认值是设计如此；已请测试窗口核对表单选好文件夹后是否显示了这个数，没显示再按缺陷处理。

## F10 待处理页没有归属的那组没有标题

`components/pending.tsx`：全所待处理里归不上公司的那一组标题用 `co.other`（「其他」），同定时任务总览；只看一家时仍不显示组名。测试同 F7 一节。

## V8 备注：重新配对后旧文件夹授权被清（口径确认，不改）

测试机 13:40 重新配对（新的成员档案）后，14:45 打开选择框时 CC 桌面版按 `pick_folder` 的整理清掉了只属于旧成员的授权 `C:\PearnlyStress`。
按现有设计是预期的：文件夹授权跟着连接成员走，断开即收回这台电脑原来的授权，文件夹权限只在电脑上改；被清的根上没有任务。

## F12 待处理页对 CC 对话里停下的单不能当场选（V7）

测试窗口共享笔记 F12：15:22 在 CC 对话里推 MEA 电费单到 T9ASC，CC 停在要选付款方式与科目；待处理页那一行显示「请重新提交」、点不开当场选。
票 `CWH-9465db0c…`：`last_push_status` 为空、`last_push_reason = choice_needed:payment,account`，接口回 `reason_code: failed`。

- 原因：CC 对话（`assistant/tools.py: _push_one`）与定时任务（`folder_intake/push.py`）在推之前停下要人选时用 `push_state.record_failure` 只记原因、
  不改推送状态（空，或上一次的失败）；只有推送服务停在复核时状态才是 `manual`。`companies/pending.py: classify` 只在 `manual` 下认 `choice_needed`，
  其余落到「有原因 = 失败」。早上汇总、侧栏计数用的是同一个分类，也一并算错。
- 改动：`classify` 排除已入账 / 在途之后，原因以 `choice_needed` 开头即为「要人选」，不看状态。
- 测试：`tests/test_cowork_companies.py` `test_each_reason_is_classified` 补状态为空、为 failed、为 success 三种。
- 未做：未部署；部署后重验 V7（当场选 → CC 桌面版写入 → 从列表消失、计数减一）。

## F11e 手机上加知识库没有保存按钮、存了不显示（乙包）

- 现象：主档页知识库点「＋」只有输入框，没有保存、取消；手机按回车后服务端已存（15:00:12「test」），页面 1 分钟不显示，背景 1213 横幅。
- 原因：原来是回车或离开输入框即保存；存后列表只靠整页重新加载更新，那次重新加载撞上 1213 失败，列表没变。同页改名、记住的选择、移除文件夹同理。
- 改动（`components/company-page.tsx`）：知识库新增与编辑改为多行输入框 +「取消」「保存」（电脑与手机一致；电脑回车保存、Shift+回车换行，
  手机回车换行、保存靠按钮，Ctrl/⌘+回车都保存，Esc 取消，选字中的回车不算）；去掉离开即保存；保存 / 删除成功直接按服务端回的那一条显示，
  失败留着字、用 `shownError` 报；改名、记住的选择、移除文件夹同样即时显示。新增词条「保存」（Save / 保存 / บันทึก）。
- 测试：新增 `tests/test_cc_mobile_content.py`（已登记进 `development.yml`）。
- 未做：新绑定的文件夹仍等整页重新加载才出现（返回内容缺显示字段）。

## F11f 长对话打开停在中间、加载慢、附件选择器有录像（乙包）

- 打开长对话停在中间：对话视口「这一轮从顶上开始」时组件库只在消息第一次出现时滚到底一次，之后消息量出真实高度、正文排版、文件卡片陆续到达
  使内容变高，没人跟。`lib/follow-scroll.ts` 新增 `settle()`：打开对话后内容一变高就贴回最底下，用户一碰（滚轮、按下、按键）即交还，
  静止 1.5 秒或最多 10 秒后停；「往上翻不拉回」照旧。`thread.tsx` 在换对话、消息取回时调用。测试在 `test_cc_mobile_content.py`。
- 「正在加载消息」约 7 秒：只查了原因。浏览器端处理 2040 条事件约 16ms，不是瓶颈；时间在服务端打开对话时按页顺序从引擎取完全部事件记录。
  改进方向（服务端，未做）：整段读取加大每页条数，或缓存已取过的历史只补新增部分。
- 附件选择器里有「相机（视频）」：Chrome 106 在可选类型跨多个大类时退回通用选择器并附上录像入口，与写不写 `image/*` 无关，去不掉。
  子代理曾把可选类型收窄到服务端白名单，开发窗口审查后没有采用：录像入口仍在，录了视频反而在上传前被静默丢掉（原来服务端会给已翻译的拒收提示），
  iPhone 拍照入口能否保留也没把握。可选类型保持原样。

## F13–F15（记录）

- **F13 刚打开页面后的第一条消息丢失**：测试窗口用浏览器扩展模拟输入复现 3 次（输入框清空、无会话、无报错），待用户用真键盘确认；确认前不改。
- **F14「正在重新连接…」在 Mac 顶部长挂**：测试窗口核实那个标签页 `document.visibilityState = "hidden"`、`hasFocus = false`（扩展驱动、窗口不在前面），
  `performance` 里没有 `assistant.api.events` 请求。补拉源（`poll-source.ts`）在页面看不见时不补拉、尾巴停在「连接中」，顶部就挂着这句；
  回复照样经推送到达。15:29 标签页回到前台后已消失。不是缺陷，不改；已请用户在前台正常使用时留意。
- **F15 杂项（这批不改）**：CC 回复夹英文过程句（模型的过程旁白，属提示词）；分店拦截时 CC 没说出按对话指定分店判的（工具结果措辞）；
  「立即运行」约 6 分钟后才执行、网页点完无反馈；CC 桌面版 `folder_intake.machine.sync` 一次 500 与同秒 8 条只读接口 1213 同时发生
  （只读锁已修，部署后观察是否再现）。
