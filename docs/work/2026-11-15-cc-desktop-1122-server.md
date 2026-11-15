# CC 桌面版 1.12.22 的服务端部分（11-15）

主会话 11-15 晚批准范围（候选 2、3、5 的服务端 + 契约测试；4 留下一版；VATDAT 在 CC 端）。服务端先上线、CC 桌面版后发，
旧版（1.12.21）在新服务端上照旧能用。分支 `pearnly/cc-1122-server`，基线 origin/main 19d9d0d3a。CC 端在 `pearnly/cc-1122-desktop`
（记录写在 `companion/README.md` 的 1.12.22 一段）。

## 影响评估（按模板要点）

| 项 | 结论 |
|---|---|
| 同类复用 | 2 照「按编码认档」现有逻辑；3 照按需请求已有的「按版本派」（种类级 `KIND_MIN_COMPANION`）加参数级；5 照领单回复已带 `master_refresh` 的先例 |
| Frappe 原生 | 改名用 `frappe.db.set_value`；缓存用 `frappe.cache`；没有新 DocType / 补丁 |
| 上下游 / Express / CC | 只读 Express；CC 1.12.22 起带新键，旧版不带时服务端照旧（契约 `Release1122Edges`） |
| 隔离权限 | 认档仍按「公司 + express_code」；按单号查只在已有的收款流程里（`collection.service.open_invoices` 的 `guard.require`） |
| 各入口 | 主档同步（ERP 公司页 / 定时）、收款（CC 对话建草稿；LINE 不变）、所有按需请求的叫醒（ERP、Cowork 两条通道） |
| 共用件 | `cc-desktop`（机器通道）命中，按连带回归闸门列 17 个入口（下表） |
| 成本 | 零：不多发请求，领单时多一次缓存 GET |
| 作废删除 | 不涉及 |
| 文案 | 没有界面文案；给模型的定式说明（英文，同已有 READ_NOTES） |
| 待真机核 | 前缀与核心名之间的空格以 Express 打印为准（`contracts.PRENAME_JOIN`；改它不会引起整批改名，见下） |

## 2 主档显示名带前缀
- `express/contracts.party`：显示名 = PRENAM + 空格 + CUSNAM / SUPNAM（`display_name`；没前缀或核心名已带前缀时只用核心名）；另给 `core`。
- `express/masters._parties`：
  - 按编码认档不变（不会把老客户认成新客户）；没挂编码的本地档按名称认领时全名、核心名各试一次。
  - 现有名称与新显示名只差空白（人在 ERP 打的「ร้านลีฟทดสอบ 1115」）→ 留原写法，不改名。
  - 只有名称变了的行（1.12.22 首次同步时绝大多数）走 `_rename`：`frappe.db.set_value` 直接改这一列、`modified` 不动，不读出整份、
    不 `save`、不留修改记录（11-13 那次是逐条 `save` 一万多条、各多一条修改记录）；每 500 条提交一次。
- 量：本机测试账套快照 70EXP\test 客户 552 条里 495 条带 PRENAM、供应商 490 条里 443 条，69EXP\TEST 供应商 707 条里 617 条（约九成）。
  正式租户与 testerp 5 家公司的实际条数没连生产查；按九成估，一家几千条客户的公司第一次同步是几千次单列 UPDATE，后台主档任务里几秒到十几秒。
- 搜索（`masters/search_rules.pattern`：选择框、LINE 选客户 / 供应商、收款选客户同一份）：打的字以常见前缀开头且连着打时在前缀后断开
  （「ร้านลีฟ」→「%ร้าน%ลีฟ%」），连着打、隔空格打、只打核心名都命中全名；只会多命中、不会少命中。

## 3 未收发票分清「已收清」与「账套里没有」
- 共用通道：`request_contracts.PARAM_MIN_COMPANION = {"express_ar_open": {"docnums": "1.12.22"}}`、`minimum(kind, params)`；
  `requests.waiting(..., params=)` 按它判 `update_needed`；`lease_next` 不把带新参数的请求派给旧版（旧版领了只会回 `params_invalid`）。
- `collection/snapshot`：`request(..., docnums=)` 请求号另带单号摘要；读回的 `looked_up` 记缓存 10 分钟（`looked_up()`），
  读回里没有的单号 = `absent`；快照对齐（`replace`）不变。
- `collection/cc_tools.draft`：单号不在快照 → 同一次即时读带上这个单号；读回 ready 时结果多 `invoice_state`（settled / cancelled /
  absent / 在别的客户名下 open）+ 定式说明；够得到的电脑都还旧 → 改发不带单号的那种，照旧只说「此刻不是这位客户的未收发票」。

## 5 领单回复叫醒按需请求
- `pearnly_core/cc_desktop/request_wake.py`（新，多产品共用）：建请求时 `ring`（ERP：够得到这本账套的电脑；Cowork：`cw:<连接>`），
  领单时 `take`（看过即清，60 秒过期）。
- `queue_contracts.lease_response(..., requests_waiting)`：有才带 `"requests_waiting": true`；ERP `jobs.lease`、Cowork `transport.lease` 都接上。
- 电脑那边（1.12.22）收到就叫醒按需请求线程；领单本来 3 秒一次，不多发请求。

## 检查
- 定向：`test_erp_express_direct`（204，新 3 条：首次全名只改名称列、只差空格留原名、本地档按全名 / 核心名认领）、`test_master_search_rules`、
  `test_erp_collection_cc`（35，新 2 条：按单号查四种状态、电脑都旧时退回）、`test_erp_collection`、`test_cc_desktop_channel`（46，新 2 条：
  新参数只派给新版、新请求叫醒一次）、`test_cowork_companion_queue_contracts`、`test_cowork_express_dispatch`、`test_cowork_account_suggest`、
  `test_companion_contract`（113，新 `Release1122Edges` 三条：按仓库里这版 CC 认不认分两种核）；`check-architecture` 通过。
- 全闸门、候选离线检查、GitHub Development checks：推送后补记。

## 共用件连带回归（`check-impact.py`：`cc-desktop` 命中，17 个入口）

| 入口 | 结果 |
|---|---|
| `erp.cc.write`（auto：express） | 没测到（要实验栈 / 测试机，明天随 CC 冒烟排） |
| `erp.cc.masters` | 没测到（同上；离线 `MasterSync` 通过） |
| `erp.cc.connect`、`erp.cc.stock-void`、`erp.cc.companies`、`erp.entry.choices`、`cc.mrerp-login`、`cc.desktop.bridge` | 没测到（manual，要测试机；本批没改这些入口的代码，只经过领单回复多一个可选键） |
| `cowork.cc.channel`、`cowork.cc-chat.push`、`cowork.execution` | 没测到（manual，要测试机；Cowork 只多了建请求后的叫醒与领单回复的可选键） |
| `cc.operator.page`、`erp.cc.chat-push`、`erp.mrerp.direct`（offline） | 全闸门里跑（本批没改它们的代码） |
| `erp.collection.server`（offline） | 通过：`test_erp_collection` |
| `erp.line.collection`（offline） | 全闸门里跑（LINE 不带单号，行为不变） |
| `erp.cc.collection`（offline） | 通过：`test_erp_collection_cc` |

## CC 桌面版这一侧（分支 `pearnly/cc-1122-desktop`，叠在本分支上；发版另等主会话点头）
- `express_masters._party` 多报 `prename`（PRENAM）；`express_receivables` 认 `docnums`（≤20、每个 ≤40 字），结果另报 `looked_up`
  （open / settled / cancelled，不论客户与列表的筛选，没有的单号不列）；`request_wake`（新）+ `queue_client.poll` 读 `requests_waiting` 叫醒、
  `request_worker._rest` 空闲时等这个事件（停线程也叫醒）；`dbf_writer` 进货单头口径 2 时 `VATDAT` 空（同销货 `dbf_sales`、同原生）；
  `version.py` 1.12.22；README 改正 1.12.21 已于 11-13 发布并加 1.12.22 一段。
- 检查：CC 全量 `pytest companion/tests` 1870 通过、60 跳过（Windows 专属）；新 `test_release_1122.py`（叫醒只认 true、叫一次醒一次、停线程即醒；
  按单号查五种情形、不带单号结果不变、坏参数拒收），`test_express_masters`（prename）、`test_service_vat`（口径 2 VATDAT 空、口径 1 有）；
  `test_companion_contract` 的 `Release1122Edges` 此时走「这版 CC 认得」那一支（两端状态集合、上限、键名一致）。
- 连带回归：另命中 `cc-request-worker`（`cc.desktop.requests`、`cc.desktop.places`、`cc.desktop.operator`）与 `cc-dbf-write`（9 个写入口）；
  离线全量通过，实机各行见共享笔记「CC桌面版1.12.22-真机待测-11-15.md」，明天由主会话排测试机。`request_wake.py` 登记进 `cc-request-worker` 的 paths。
