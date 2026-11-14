# 收款完整闭环（2026-11-14）

分支 `pearnly/collection-loop-5ac1a1`，基线 origin/main a838abe54。主会话任务书：收款 11-13 晚已对全部 ERP 租户上线，本任务把它做成闭环。
接手自 11-13 的收款窗口（交接见共享笔记「收款-真机与CC侧-交接-11-13晚.md」「收款-LINE侧-交接-11-13晚.md」）。

## 0. 旧分支核对（11-14 开工）

| 分支 | 结论 |
|---|---|
| `pearnly/collection-cc-chat` | 代码全部已在 main（按标题与内容核对 4 个未等价提交，main 都有同标题提交） |
| `pearnly/collection-line-side` | 同上，5 个都在 main |
| `pearnly/collection-on-ledger` | 7 个在 main；只差一条文档提交 6cf9786c3（银行流水抽样 5 行证据），已带进本分支 |

## 1. 管理员默认有收款

现象（11-13 晚用户自己撞到）：管理员在团队管理里「收款」显示打勾（管理员行一律画成全勾、不能改），LINE 菜单却没有收款格。
原因：`collection.write` 只认收款岗位角色。

改法（主会话定）：
- `access/rules.py`：新增 `ADMIN_FEATURE_CAPABILITIES = {"collection.write": "collection"}`，`required_roles` 给这些能力加上管理员角色。
- `access/guard.py`：这类能力仍跟着功能开通走——站点没开通「收款」时管理员也没有（功能角色本来就按开通摘，管理员角色不摘，所以单独判）。
- `hooks.py`：「我的收款」页加管理员角色（与 `collection.write` 同一判据）。
- 三处同时生效：网页（我的收款、以后的网页新建）、LINE（富菜单第 4 格与「เมนู」兜底，都按 `guard.has("collection.write")`）、CC（收款工具同一判据）。
- 补丁 `v1_28_admin_collection_menus`：部署迁移时给已绑定 LINE 的人各排一次菜单重挂（去重、只在该挂的那套不同才写 LINE），管理员不用等下次发消息。
- 测试 `tests/test_erp_admin_collection.py`（镜像里跑，已加进 `run-offline-tests.py` 的 IMAGE 清单）：管理员 × 录收款 / 菜单收款格 / 我的收款；功能没开通时谁都没有。

上线后逐正式租户回读（6 家 ERP：testerp、test01、mrerp、gize、klm、polymer），每家一次 web 容器 bench：
1. `pearnly_features` 含 `collection`；
2. 每个有 `ERP Admin` 的启用账号 `guard.has("collection.write")` 为真；
3. 其中已绑定 LINE 的，`line_menu.wanted_variant(user)` 含 `collection`，且 LINE `GET user/<id>/richmenu` 等于 `wanted_menu` 算出的那套（补丁排的重挂已跑完）；
4. 后台错误日志里没有 `ERP LINE menu` 相关新错误。

## 2. 岗位文案「收款员」→「收款」

用户 11-13 晚原话：「收款员的文案按照功能名称写：收款」（主会话交接第十节转）。四语取功能名「收款」现有词条（`erp.nav.collections`），不另造：

| 来源·键 | 中文 旧 → 新 | English | ไทย | 日本語 |
|---|---|---|---|---|
| pearnly_erp_portal·team.role.collector | 收款员 → 收款 | Collector → Collections | พนักงานเก็บเงิน → รับชำระหนี้ | 集金担当 → 入金 |

没改的：收款列表 / 导出里的「收款员」列（无键词条 `Collector`）指录这笔的人，不是岗位名，保持不变。

## 3. LINE 菜单发布不下载整张图

`_has_image` 用 GET 读整张图判断「有没有图」，生产到 api-data.line.me 慢时 20 秒读不完（11-13 testerp 首次发布超时）。
DMS、Cowork 的菜单发布是同一写法，按类一起改：共用层 `pearnly_core.line.rich_menu_has_image`（流式取，只看状态、不读正文、立刻关连接），三个产品的 `_has_image` 都调它。
断点续做：发布按内容散列命名复用已建菜单、只给没图的传图，中途失败后重跑就是接着做；测试 `tests/test_line_menu_publish_lock.py` 新增「只看状态」与「半途重跑只补剩下的」两条（三个产品各跑）。

## 4. LINE 侧后续

### 4.1 后台任务回复语言（已做）
共用 `pearnly_core/execution/language.py`（`settle` / `use`）：设语言同时置 `job.lang_resolved`。收款 `speaking`、Cowork LINE 收单与追问、DMS `line_language`、
三产品识别失败回执（`work._notify` 一处）、ERP 网页识别与 DMS 账号检查任务、CC 工具与 Cowork 对账的手写副本都改用它。同类清单见共享笔记
「LINE后台回复语言-同类清单-11-14.md」。测试 `pearnly_core.execution.test_job_language` +5 条（候选检查本来就跑）。

### 4.2 旧步骤按钮重问（不用做）
已在 main（fbde8a5af）。

### 4.3 写失败的卡带原因还回、换新号（主会话 11-14 同意、次数不设上限；已做，见本节末「施工」）
现状：写失败只推一条原因，记录停在「失败」，没有路再放行；请求号 `erp:receipt:<记录号>` 每笔只交一次（`cc_desktop.jobs.enqueue` 按请求号去重）。
提议（照卡片有效期规则 A9 / A10）：
- 只对 CC 明确「写前拒绝、一行没写」的码开放：`already_collected`、`invoice_not_found`、`receipt_type_invalid`、`cash_account_missing`
  （`dbf_receipt.REFUSALS` 里还有 `JOURNAL_BOOK_NOT_FOUND`、`CUSTOMER_NOT_IN_ARMAS`、`ACCOUNT_NOT_POSTABLE`，同为写前拒绝，一并开放；
  其余失败——写到一半回滚、结果不明、没账套、撤掉——维持现状：不还回，只读核对）。
- 这类失败：记录退回草稿（版本 +1、放行次数 +1），LINE 推一张「原卡 + 失败原因」的新确认卡（新确认号，旧卡作废）；网页「我的收款」、CC 同一状态可改再放。
- 再放行的请求号带次数：`erp:receipt:<记录号>:<次数>`（第一次不带，保持线上已有写账件的请求号不变）。
- 不会重复写账的依据：CC 写前本来就按「同客户、同 YOUREF（= 记录号）、没作废的 RE 已在 → 回原号、一行不写」，再放行即使上次其实写成了也只回原号。
- 改动：`collection/service.py`（失败转草稿的一个函数、放行计数）、`collection/push.py`（请求号、回执里判码）、`integrations/line_collection.py`（失败卡）、
  ERP Collection 加一个整数字段 `attempt`（原生迁移）；文案只用现有的确认卡与原因，不新增；测试补「写前拒绝 → 草稿 → 再放行新请求号 → 同 YOUREF 不重复」。
- 待定：改了草稿后余额仍不够，CC 再拒一次就再还回（不限次数）；是否需要上限请主会话定（建议不限，和人工在 Express 改一样）。

#### 4.3 施工（11-14）
- 7 个码逐个对到 CC 写前的位置（`companion/src/companion/dbf_receipt.py` `write_receipt`，写前备份 `_backup` 之前）：
  `JOURNAL_BOOK_NOT_FOUND` ← `_missing_tables`（直接回码）、`run_row` 为空、`plan_bank` 户头表不在；`CUSTOMER_NOT_IN_ARMAS` ← 客户档不在；
  `receipt_type_invalid` ← `plan_methods`；`cash_account_missing` ← `cash_account`；`ACCOUNT_NOT_POSTABLE` ← `receivable_account`；
  `already_collected` / `invoice_not_found` ← `check_invoices`。写入阶段 `_next_docnum` 也会抛拒绝，但那里走 `except Exception` →
  `_error_code` 只认 `_WriteError` → 回 `WRITE_FAILED`，不会被当成写前拒。契约测试 `ReceiptContract.test_codes_the_server_acts_on_are_the_ones_cc_refuses_with_before_writing` 钉住。
- **顺带发现并修的线上缺陷**：服务端 `contracts.UNCOLLECTABLE` 与 LINE `BALANCE_CODES` 写成大写 `ALREADY_COLLECTED`，CC 回的是小写
  `[already_collected] …`，从没对上过：CC 拒收时各张余额没记、快照没按客户重读、LINE 说不出「已被收款，当前余额」。改成原样小写，
  测试里的假回执也改成 CC 的真写法；LINE 去码前缀的正则也认小写。
- 请求号：`push.request_key`——第一次 `erp:receipt:<记录号>`（线上已有的不变），退回后再放行 `erp:receipt:<记录号>:<次数>`；YOUREF 仍是记录号。
  CC 桌面版不收也不解析请求号（领单只交投递号 / 记录号 / 单号 / 载荷，`queue_contracts.job`；`companion/` 里没有 `erp:receipt`），
  已发的 1.12.21 照常；契约测试 `test_the_request_key_never_reaches_cc_so_a_retried_key_works_on_1_12_21`。
- 回执：写前拒 → 草稿、版本 +1、`attempt` +1（ERP Collection 新加的隐藏整数字段，原生迁移）、原因留着；草稿状态下、或回执来自已换掉的上一件，一律不改记录。
- LINE：还回原卡（新确认号、旧卡失效、120 分钟、`_show_card` 同一套），最前面一句是现有的失败原因 / 「已被收款，当前余额」；
  余额不够时卡上照规则不出【确认】，改了再确认。正在录另一笔时不打断，只推原因，草稿留在「我的收款」/ CC。没有新文案。
- 网页「我的收款」：退回的草稿打开时最前面显示原因（原生「Reason」字段）。
- 测试：`test_erp_collection.Receipts`（退回、再放行新请求号、迟到回执不改、只 7 个码）、`test_erp_collection_line.Returned`（新卡、旧卡不回话、
  再放行、忙时只说原因、等结果时直接回卡、LINE / 网页 / CC 三处同一草稿同一版本）。

## 5. 网页新建收款
评估 `docs/work/2026-11-14-collection-web-create.md`，一屏摘要在共享笔记「收款网页新建-一屏-11-14.md」，等主会话审。

## 6. 「没有覆盖此日期的 Express 账套」改得更明白（用户 11-14「文案没问题」，已改）

现象：testerp CO-00146 只报了 69EXP\NLTTEST（2024 年度，到 2025-12-31），今天收款被拒，只说「没有覆盖此日期的 Express 账套」。
提议：说出日期和已连接账套各自的期间；一本都没连的另一句。同一句也是 ERP 采购 / 销售单的停下原因（`express.reason.noBook`），建议一起换（按类）。

| 场景 | 语言 | 改前 | 改后 |
|---|---|---|---|
| 有已连接账套，但都不覆盖这天 | 中文 | 没有覆盖此日期的 Express 账套 | {0} 不在已连接账套的期间内：{1} |
| | English | No Express account set covers this date | {0} is outside the periods of the connected account sets: {1} |
| | ไทย | ไม่มีชุดบัญชี Express ที่ครอบคลุมวันที่นี้ | วันที่ {0} อยู่นอกงวดของชุดบัญชีที่เชื่อมต่อ: {1} |
| | 日本語 | この日付を含む Express 会計データがありません | {0} は接続済みの会計データの期間外です：{1} |
| 一本都没连 | 中文 | （同上一句） | 此公司没有已连接的 Express 账套 |
| | English | | This company has no connected Express account set |
| | ไทย | | บริษัทนี้ไม่มีชุดบัญชี Express ที่เชื่อมต่อ |
| | 日本語 | | この会社に接続済みの Express 会計データはありません |

`{0}` = 日期（如 2026-11-14），`{1}` = 每本「账套名 起–止」用「、」隔开（如 `NLTTEST 2024-01-01–2025-12-31`）。

施工（11-14）：共用 `express.target.no_book_text`（`company_no_book_text` / 列表用的 `no_book_texts`，每家公司只读一次账套名单）。
- 收款：`service.book_for` 抛「没有账套」时带公司与日期，`texts.explain` 出新句；LINE 写失败那句（放行时账套没了）同样。
- ERP 单据：单据页 Express 弹窗（`express.api._view` 的 `reason_text`，`transactions.js` 优先用它）、待处理 / CC `erp_pending`（`pending._items`）。
- 没有单据日期、拿不到公司的地方（CC 代录原因、推送后补一句）仍用旧词条 `express.reason.noBook`。
- 新文案两条（新增，已按主会话要求登记 copy-approved）；收款列表「收款员」列不改。
- 测试：`test_erp_collection.Books.test_no_book_names_the_date_and_each_connected_book_period`、
  `test_erp_cc_chat_push.PendingToolTests.test_no_book_names_the_date_and_each_book_period_reading_the_books_once_per_company`。
