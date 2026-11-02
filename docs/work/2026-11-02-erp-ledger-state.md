# ERP 单据账本栏改通用列（评估 §13 第 6 步第一块，11-02）

分支 `pearnly/erp-ledger-state`，基线 origin/main `9be471c5`；field-help-icon 合入后变基到 `cf9b3faf`，第 9 步（ERP 对话直写）合入后再变基到 `9b3f66e4`。评估与定案：
[2026-11-02-mrerp-operator-and-erp-company-评估.md](2026-11-02-mrerp-operator-and-erp-company-评估.md) §4、§12 第 6 条、§12.1 #6、§13 第 6 步。
没有合入 main、没有部署（由主会话安排）。

## 结论

- 采购、销售、出入库、盘点、收银日汇总五种单上的 Express 专用栏 `express_*` 改成通用的 `ledger_*`，另加 `ledger_target`
  记这张单的账本（现在只有 `express`；以后 ERP 接 MR.ERP 记 `mrerp`）。一张单只会有一个账本，一列一义。
- 原生迁移：DocType 改列 + 补丁 `v1_24_ledger_state`（模型同步之后）把旧列抄到新列、记账本；幂等，重跑不拿旧值盖新状态。
  旧列这一版**保留在库里不删**（不在 DocType 里、代码不再读写）作回退；回退方法见下。
- **对现有 Express 行为零变化**：界面上的列名、筛选名、导出表头、状态文字都没改（新列沿用原来的标签），「接 Express」四条规则
  改前改后同一组输入结论相同（测试证据见下）。
- 顺带补齐 ERP 版 CC 待处理：收银日汇总、出入库（入库 / 出库 / 调拨）、过账后的盘点也进「待处理」，`action` open 到各自页面。

## 提交

| 提交 | 内容 |
|---|---|
| `08ea1b66` test | 改列前先锁「接 Express」四条规则的结论（`LinkRulesUnchanged`，结论表在改列前的代码上跑出） |
| `3d3d85a4` feat | 五种单加 `ledger_*` 与 `ledger_target`（此时新旧列并存），补丁 `v1_24_ledger_state` 与它的离线测试 |
| `9a632d54` refactor | 调用方全部改到新列；旧列从 DocType 拿掉；导出不带 `ledger_target`；守护测试 |
| `7ebe6278` test | 受影响流程（采购、销售、出入库、调拨、收银、导出）加通用列断言 |
| `41a0557c` feat | 待处理补齐收银日汇总、出入库、盘点；出入库页、收银记录页支持带单号打开 |
| `97980a2f` fix | 补丁自己改个人视图设置（实验栈发现 Frappe 连着调 `update_user_settings` 会把整份设置清空，见「迁移实测」） |
| `13aea062` refactor | field-help-icon 合入 main 后变基，采购 / 销售单据页（`purchase-form.js` / `sales-form.js`）读新列，守护测试去掉例外 |
| `9f935865` refactor | 变基到含第 9 步的 main 后，`express/cc_tools.py`（ERP 对话直写工具）读写与筛选改到新列，`tests/test_erp_cc_chat_push.py` 替身同改 |
| `5a0e307f` test | 导出流程加「按新列筛选」断言（原生标准筛选列 `ledger_state`） |
| 本记录 | docs |

## 字段与迁移

| 旧列（五种单） | 新列 | 说明 |
|---|---|---|
| `express_section` | `ledger_section` | 栏（Section Break），标签仍是 Express |
| `express_state` | `ledger_state` | 状态，Select 选项不变（日汇总多 `handled`），`in_standard_filter`，标签 Express Status |
| `express_docnum` | `ledger_docnum` | 账本单号，带索引，标签 Express No. |
| `express_reason` | `ledger_reason` | 停下 / 失败原因码 |
| `express_sent_at` | `ledger_sent_at` | 交进写账队列的时刻 |
| `express_job` | `ledger_job` | 写账件（`Pearnly CC Job`），带索引 |
| `express_book` | `ledger_book` | 写入用的账套键（**只改单据上的**；公司、对方、商品、仓上的 `express_book` 是主档同步来源，不动） |
| `express_needed` | `ledger_needed` | 要人选的项 |
| `express_void_state / _reason / _job`（只出入库单） | `ledger_void_state / _reason / _job` | 在账本作废原单 |
| — | `ledger_target`（新） | Select `express` / `mrerp`，隐藏、只读、不复制；排进账本时记（`push.queue` 写 `contracts.LEDGER_EXPRESS`） |

- 标签、类型、选项、只读、隐藏、索引、标准筛选与旧列逐项相同（`LedgerFields` 测试锁住），位置在原来的位置，`ledger_target` 排在这一栏最后。
- 补丁 `pearnly_erp_portal.patches.v1_24_ledger_state`（`patches.txt` `[post_model_sync]` 最后一条）：
  - 每种单一条 UPDATE：`ledger_*` = `express_*`、`ledger_target = 'express'`，条件「`ledger_target` 为空且旧列任一有值」——
    抄过的、改列后新排上的（排队时就记了账本）都跳过；从没排进过 Express 的单不动（账本留空）。
  - 原生列表 / 报表视图里存着的旧列名照 Frappe `rename_field` 同一套四个函数改（`update_reports`、`update_users_report_view_settings`、
    `update_property_setters`、`update_user_settings`），栏名一起改。
  - 打印每种单新列里有几行 express（发布回读用）。
  - 不用 `rename_field` 本身：它整表 `新列 = 旧列` 无条件覆盖，重跑会拿旧值盖掉改列后的新状态。
- **回退**：新镜像还在时先跑 `bench --site <站点> execute pearnly_erp_portal.patches.v1_24_ledger_state.rollback`（新列抄回旧列、
  清 `ledger_target`、删这条补丁的执行记录、视图里的列名改回），再换回旧镜像，旧代码读写旧列；以后再升级时补丁重跑、重新抄。
- **旧列什么时候删**：确认不回退后另起一条补丁删（同 `v1_21` 删批次列的做法：删前打印有值行数、清掉提到它的个人视图）。不在本步做。
- `v1_22_express_linked_at`（已上线的补丁）仍按旧列读：它排在 `v1_24` 之前、旧列在库里还在，没跑过它的站点照样能跑；新建站点不跑补丁。

## 调用方盘点（基线 `9be471c5`）

旧列在 ERP 应用、流程、冒烟脚本里的全部读写处（`git grep` 逐个核对；`pearnly_core` / Cowork / CC 桌面版里的 `express_docnum`
是写账回执的线上键名，与 ERP 单据列无关，不动）：

| 文件 | 函数（基线行） | 用到的列 | 用途 | 改法 |
|---|---|---|---|---|
| `express/push.py` | `queue` L124-133 | state、reason | 提交时排写账 | 新列；另写 `ledger_target` |
| 同上 | `_set` L320 | state、reason | 写单据栏、进出待处理推送 | 新列 |
| 同上 | `release_operator` L364-369 | job、state、reason、needed | 代录再录前撤件 | 新列 |
| 同上 | `send` L382-439 | state、job、reason、needed、book、sent_at | 准备并交写账件、停下记原因 | 新列 |
| 同上 | `detach` L460-474 | state、job、reason、needed | 断开 Express 撤单（四条规则 ④b） | 新列 |
| 同上 | `void` / `_void_changed` L489-520 | state、docnum、job、needed、void_* | 出入库作废、账本作废原单 | 新列 |
| 同上 | `job_changed` L542-557 | state、docnum、job、reason、needed | 回执写回 | 新列 |
| 同上 | `_queued_note` / `poll` / `requeue_unmapped` L705-748 | state、reason、job、book | 每分钟重试、排队原因、仓库认领后重排 | 新列 |
| `express/api.py` | `_document` / `_needed` / `_view` / `_pos_stuck` / `mark_handled` / `answer` / `retry` | 全部 + void_* | 单据页 Express 弹窗、选一次、再写一次、标手工处理 | 新列；回给页面的键（state / docnum / reason / void）本来就是通用名，不变 |
| `express/operator.py` | `sources` / `precheck` / `status` | state、reason、job、docnum | CC 代录取单、预检、恢复任务收尾 | 新列 |
| `express/pending.py` | `_ledger_rows` | state、reason | ERP 版 CC 待处理（只在这一处读） | 新列；见下「待处理补齐」 |
| `express/pos_day.py` | `pending_refunds`、`document` | state、docnum | 退货单等原 HS 写成、取原 HS 单号 | 新列 |
| `express/stock_entry.py` | `void_payload` | book、docnum、job | 作废载荷（写入时的账套、单号、实际编码） | 新列 |
| `express/masters.py` | `_pending` | state、void_state | 库存对齐叠加还没写成的单（只改这两列；公司 / 主档的 `express_book` 不动） | 新列 |
| `express/contracts.py` | 注释 | reason | — | 改注释；加 `LEDGER_EXPRESS` |
| `cashier/sales.py` | `list_sales` | state、docnum | 收银记录「Express」一栏（日汇总状态并进小票行） | 新列；行上的键改成 `ledger_state` / `ledger_docnum`，页面同改 |
| `cashier/vat.py` | `refresh_company` | state | 已写进 Express 的日汇总不重算税额 | 新列 |
| `inventory/documents.py` | `LIST_FIELDS`、`void_entry` | state、docnum、void_state | 出入库列表与查看、盘点生成的单不单独作废 | 新列 |
| `inventory/count.py` | `get_count` | state、docnum | 盘点页「Express」按钮 | 新列；回给页面的键同改 |
| `printing/context.py` | `_sales` | state、docnum | 销售单打印标题与 Express 单号 | 新列 |
| `purchasing/records.py`、`sales/records.py` | `FIELDS` | state、docnum | 列表、合计、导出取数 | 新列 |
| `purchasing/output.py`、`sales/output.py` | `_columns` | （全部 DocType 字段） | 导出表头 = DocType 标签 | 不带 `ledger_target`；其余列名标签同前，导出不变 |
| `public/desk/purchase.js`、`sales.js`、`pos-receipts.js`、`stock-entry.js` | 列表「Express」列、出入库查看弹窗按钮 | docnum、state、void_state | 页面 | 新列（列标签不变） |
| `public/desk/stocktake.js` | 盘点页按钮 | state | 页面 | 新键 |
| `public/desk/purchase-form.js`、`sales-form.js` | 单据页「Express」按钮 | state | 页面 | 新列；与 field-help-icon 重叠，等它合入 main、变基后在 `13aea062` 单独改 |
| `express/cc_tools.py`（第 9 步，主会话通知后纳入） | `Kind` 搜索字段元组、`_job`、结果 / 列表 / 已写入 / 结果不明核对、按状态筛选 | docnum、job、state、reason、sent_at | ERP 版 CC 对话里直写 | 新列（`9f935865`）；回给 CC 的键（docnum、status、last_pushed_at…）不变，`tests/test_erp_cc_chat_push.py` 17 条全过、Cowork 工具字节比对测试照过 |
| `patches/v1_22_express_linked_at.py` | 回填接上时刻 | state | 已上线的旧补丁 | 不动（见上） |
| `scripts/flows/cc_companies.py`、`cc_operator.py`、`erp_document_express.py`、`erp_express_new_item.py`、`erp_express_service_vat.py` | 流程准备 / 落库检查 | state、reason、job、book | 流程 | 新列 |
| `scripts/smoke/express_flow.py` | Express 冒烟 | state、docnum、reason、job、book | 冒烟 | 新列 |
| `pearnly_core/express_operator/products.py` | 文件头注释提到「ERP 单据的 express_state」 | — | 注释 | 不动（改它要跑共用层候选检查；只是注释，留给下一次动这个文件时顺手改） |
| Desk 原生列表 / 报表视图里存的列 | — | state 等 | 个人列表设置、报表 | 补丁里照 `rename_field` 改名 |

没有给旧列名留读取兼容层：ERP 内的读取方都改完了，守护测试 `LedgerFields.test_nothing_reads_the_old_express_columns_any_more`
扫 ERP 应用、流程、冒烟里旧列名，只许两条迁移补丁出现（单据页两份 JS 在 `13aea062` 之前临时列为例外，那一提交去掉）。

## 「接 Express」四条规则：改前改后一致

规则来自 [express-link-states 评估](2026-11-01-express-link-states-评估.md) 审定四点：①② 没勾 / 没接上不排；③ 接上排且不看此刻电脑；
④a 开始早于接上时刻不排不补（采购「修改」沿原单）；④b 断开优先（撤下标已断开，正在写的看结果）；④c 没报账套停下每分钟再试；
④d 断开后作废已写的出入库单照排。

- `tests/test_erp_express_direct.py::LinkRulesUnchanged`：同一组输入（4 种公司状态 × 开始早于 / 晚于接上 × 五种单、采购修改链、
  没账套停下再 poll、断开撤单与正在写的两种结果、断开后 poll、断开后作废三种状态）跑一遍，结论收成一张与列名无关的表
  （`LINK_RULES_GOLDEN`，59 项）。这张表是在**改列前的代码**（提交 `08ea1b66`，DocType 还是 `express_*`）上跑出来写进去的；
  之后每个提交同一份用例按 DocType 实际列名跑，结论逐项相同。
- 原有 `LinkStates` 13 条、`StockVoid`、`PosDay*`、`UnmappedWarehouse`、`Operator*` 等用例只把列名换了：`8e6f7a14 → b5cfe67b` 这份
  测试文件删掉的 81 行，每一行把 `express_X` 换成 `ledger_X` 后都原样出现在新增行里（逐行核对，0 行对不上；多出的 66 行是新加的
  `LedgerFields` 守护测试），全过。

## 待处理补齐（`express/pending.py`）

| 单据 | 「可以写了」 | 读权限（同 Express 弹窗） | 归哪家公司 | 单号 / 日期 / 金额 | 点开去 |
|---|---|---|---|---|---|
| 采购、销售（原有） | submitted | purchase.read / sales.read | 单据上的公司 | system_no / doc_date / grand_total | `/desk/pearnly-purchase|sales/<单>` |
| 收银日汇总 / 退货单 | submitted | cashier.read | 单据上的公司 | 单据名 / day / grand_total | `/desk/pearnly-pos-receipts/<日汇总>`：收银记录页打开这张日汇总的 Express 弹窗 |
| 出入库（入库 / 出库 / 调拨） | submitted | stock.read + 本方向（stock.in / out / transfer，同出入库单读权限） | 仓库的公司 | doc_no / posting_date / — | `/desk/pearnly-stock-in|out|transfer/<单>`：回到列表并打开这张单 |
| 盘点 | Posted | count.read | 仓库的公司 | title / posted_at / — | `/desk/pearnly-stock-count/<盘点>` |

- 形状同 `PendingItem`，`action` 一律 open；范围仍是 `contracts.needs_person`；员工只看本人录入的（日汇总是系统合的，员工看不到）。
- 仍只有 `_ledger_rows` 一处查库；出入库与盘点按这几家公司的仓取（一次查仓、一次查单）。
- 页面：`stock-entry.js` 路由第二段是单号时回到列表并弹出这张单（原来跳回列表）；`pos-receipts.js` 带日汇总单号打开时弹出那张日汇总的
  Express 弹窗。没有新增文案。
- 测试：`tests/test_erp_cc_companies.py::PendingLedgerKinds`（三种新单的形状与网址、方向 / 公司 / 本人录入的权限、作废与没过账不算、
  进出待处理推送按仓库的公司）、`tests/test_erp_express_direct.py::PendingKinds`（种类 = 写账本的单据、可以写了的状态与读权限同
  `push` / `api`）。

## 迁移实测（本机实验栈 `pearnly-ledger-lab`，镜像同线上默认，站点 erp-new / erp-old）

1. 实验栈先挂 **origin/main 源码快照**（`9be471c5` 之后的 `93f7e758`，DocType 还是 `express_*`）建两套固定数据站点，按正式函数补齐
   七类单据（`erp_lib.ensure_documents`），再把五种单的旧列铺成各种状态（写成带单号 / 账套 / 送出时刻 / 写账件、停下要人选带
   needed、失败、已断开、核对不上、日汇总标手工处理、出入库已在 Express 作废），留一张从没排过的销售单；在 erp-old 存一份个人
   视图设置（报表列、筛选里有 `express_state` / `express_docnum`）。迁移前逐行快照。
2. 切到本分支源码，`bench --site … migrate`：补丁打印 erp-new 1 / 1 / 3 / 1 / 3、erp-old 2 / 1 / 2 / 2 / 3 行（采购 / 销售 / 出入库 /
   盘点 / 日汇总）。逐行比对：20 张单 × 全部旧列，新列与旧值**全部一致（0 处不同）**；19 张记了 `ledger_target = express`，从没排过的
   那 1 张账本留空；旧列仍在表里（每种单 7 列，出入库 10 列）。
3. 重跑补丁（`bench execute …v1_24_ledger_state.execute`）：两站点快照逐字节相同。
4. 个人视图：第一版补丁照 Frappe `update_user_settings` 改，**实测整份设置变成 NULL**（它清缓存后下一次调用把空值同步回库）；改成
   补丁自己一次换完（`97980a2f`），复测：报表列与筛选里的旧列名都换成新列名，其余不变。离线测试同补。
5. 回退：erp-new 跑 `rollback()` → 旧列与迁移前快照逐行一致、`ledger_target` 清空、补丁执行记录删掉；再 `migrate` 补丁重跑、重新抄，
   与迁移前逐行一致（0 处不同）。（没有真换回旧镜像跑旧代码；回退只验证了数据这一层。）
6. 迁移后接口（管理员）：采购列表行带 `ledger_state` / `ledger_docnum`、没有旧键；采购 / 销售导出账本那 7 列标签与改列前相同
   （Express Status … Express Choices Needed），`ledger_target` 不导出；单据 Express 弹窗（`document_express`）对 11 张迁移过来的单回的
   状态、单号、原因、作废进度与旧值一致；待处理列出迁移来的停下 / 失败单，新种类的网址按方向 / 页面对（erp-old：日汇总、出库单、
   盘点、采购各一张）。

另：查到存量补丁 `v1_21_drop_batch_expiry_columns` 用 `frappe.db.table_exists("__UserSettings")` 判表，Frappe 会自己加 `tab` 前缀，
所以那条补丁清个人视图的那一步从没生效过（已上线、只影响旧的批次列视图；不在本步改，记在这里）。

## 测试与检查（head `5a0e307f`，变基到 `9b3f66e4` 之后）

| 项 | 结果 |
|---|---|
| 本地全闸门 `~/pearnly-devtools/gate.sh`（`SKIP_NPM_CI=1`，与 development.yml 同名各步） | 168 / 168 ok（`5a0e307f`；变基前 `13aea062` 上 166 / 166） |
| `tests/test_erp_express_direct.py` | 166 条全过（新增 `LinkRulesUnchanged` 1、`LedgerColumnsPatch` 6、`LedgerFields` 4、`PendingKinds` 1） |
| `tests/test_erp_cc_companies.py` | 26 条全过（新增 `PendingLedgerKinds` 3） |
| `tests/test_erp_cc_chat_push.py`（第 9 步）、`tests/test_cowork_push_tools_golden.py` | 17 / 1 条全过 |
| `test_cc_desktop_channel` / `test_erp_express_locks` / `test_erp_system_paths_company_scope` / `test_express_operator_shared` / `test_express_operator_tasks` | 29 / 5 / 4 / 11 / 12 条全过 |
| `scripts/check-architecture.py` | PASS（0 违规） |
| `scripts/check-unbounded-queries.py` | 通过（存量 332 处，未超基线；新查询带 `# bounded`） |
| `scripts/check-copy.py --report` | 新增 0、改动 0、删除 0、译文改动 0、拦截 0；1–5 各节「无」（新列沿用原标签；`ledger_target` 标签 Ledger 是隐藏列、不导出） |
| `scripts/flows/coverage.py` | 通过（改到入口文件 8 个，都有流程登记） |
| 流程（`run.py --changed --base origin/main --post-status`，实验栈 `pearnly-ledger-lab`，站点先迁移） | **64 / 64 通过**，19 条流程 × 两套数据 × 两种宽度，2169 秒，提交状态 `pearnly/flows` 已回写到 `5a0e307f` |
| 自动冒烟 `scripts/smoke/run.py --target local --diff` | 通过 13、不通过 2（都是本机实验栈不是测试租户、Express 写入探针拒跑，见下）、没测到 35 |

流程：erp.stocktake.scan、erp.document.express、erp.stocktake.new-product、erp.stocktake.places、erp.stocktake.web-code、erp.purchase.create、
erp.exports、erp.line.stocktake.phone、cc.operator、cc.companies、erp.stock-entry.scan、erp.pos.shift、erp.print.all、erp.sales.scan、
erp.stock.excel-units、erp.stocktake.gs1、erp.express.service_vat、erp.stock-transfer、erp.express.new-item-stock，全部通过。其中本步加的断言：
采购（关了接 Express → 不排、无账本）、销售（有状态就记 express）、出入库扫码（接了 Express 的公司两张单都排进账本、带单号打开回到列表弹出这张单）、
调拨（不排）、收银（收银记录行带 `ledger_state` / `ledger_docnum`、没有旧键）、导出（采购 / 销售导出账本 7 列标签同前、列表带新列、按新列
筛选与库里一致）、cc.companies（待处理里出入库单开到 `/desk/pearnly-stock-<方向>/<单>`、日汇总开到 `/desk/pearnly-pos-receipts/<日汇总>`）。
（变基到 `9b3f66e4` 之前在 `332f031b` 上跑过一遍 new 数据集的 31 格，全过；收到主会话通知后停下、变基、在新头整套重跑。）

### 共用件连带回归（`check-impact.py`：命中 erp-printing、erp-count-code 等，逐入口）

| 入口 | 方式 | 结果 |
|---|---|---|
| erp.page.stock-in / stock-out / stock-transfer / stock-count / purchase / sales / pos-receipts | auto：page | 通过（7 个） |
| erp.detail.sales.submitted / sales.void / purchase.submitted / stock-in.submitted | auto：doc_detail | 通过（4 个） |
| erp.stock-count.switch.posted-to-active / active-to-posted | auto：record_switch | 通过 |
| erp.detail.sales.draft / purchase.draft / purchase.void / stock-out.void | auto：doc_detail | 没测到（实验栈老数据站点没有这种单） |
| erp.purchase.detail.not-permitted / sales.detail.not-permitted | auto：not_permitted | 没测到（本机没配受限账号） |
| erp.cc.write / erp.cc.stock-entry | auto：express | 没测到（冒烟探针拒绝非测试租户：要测试机真 CC 写 C 盘 T 账套；排队、停下、弹窗、回执写回由流程 erp.document.express / express.service_vat / express.new-item-stock 与离线测试覆盖） |
| erp.print.stock-entry / purchase / sales / pos-receipt / pos-shift / stock-count | manual | 通过（流程 erp.print.all） |
| erp.stock-in.item-scan / stock-out.item-scan | manual | 通过（流程 erp.stock-entry.scan） |
| erp.sales.item-scan | manual | 通过（流程 erp.sales.scan） |
| erp.stock-count.new-product | manual | 通过（流程 erp.stocktake.new-product） |
| erp.stock-count.code | manual | 通过（流程 erp.stocktake.scan / gs1 / web-code） |
| erp.exports | manual | 通过（流程 erp.exports；文件名乱码一项没单独看） |
| erp.cc.companies | manual | 通过（流程 cc.companies） |
| cc.operator.page | offline | 通过（`tests/test_cc_operator.py` 在闸门里；流程 cc.operator） |
| erp.cc.chat-push | offline | 通过（`tests/test_erp_cc_chat_push.py` 17 条） |
| erp.cc.masters、erp.cc.pos-day、erp.cc.pos-refund、erp.cc.stock-transfer、erp.cc.stock-void | manual | 没测到（要测试机真 CC 与 C 盘 T 账套，部署后交验收窗口；ERP 这一侧的排队 / 回执写回 / 作废排件由离线测试 `StockVoid`、`PosDay*`、`LinkRulesUnchanged` 覆盖） |
| erp.line.stocktake.liff-camera | manual | 没测到（真机 LINE 相机；手机页面由流程 erp.line.stocktake.phone 覆盖） |
| erp.place.header、erp.entry.choices、erp.records.scope、erp.serials.dialog、erp.purchase.item-scan、erp.stock-count.upload、erp.stock-in.attach、erp.stock-out.attach | manual | 没测到（人工入口，本步没改它们的逻辑，交验收窗口按清单实测） |


## 文件重叠与合入顺序

- `pearnly/field-help-icon`（字段说明图标）：两边都改 `public/desk/purchase-form.js`、`sales-form.js`（不同行）。按主会话规矩本分支
  先不改这两个文件；它合入 main（`cf9b3faf`）后本分支变基（无冲突），在 `13aea062` 补这两行（`doc.express_state` → `doc.ledger_state`），
  守护测试同提交去掉例外。`transactions.js`、`hooks.py`、`shared-parts.toml`、`registry.toml`、`development.yml` 本分支都没动。
- `pearnly/stocktake-scan-inline`（盘点扫码，开工时 origin 上还没有这个分支）：本分支改了 `public/desk/stocktake.js` 一行
  （`state.task.express_state` → `ledger_state`）和 `inventory/count.py` `get_count` 回的键。它后合的话变基时注意这一行。
- 第 9 步 `pearnly/erp-cc-chat-push`（ERP 对话直写工具）：已先合入 main（`024e206d` … `9b3f66e4`）。本分支变基到它之后，把它新加的
  `express/cc_tools.py` 读写账本栏的地方一并改到新列（`9f935865`），没留兼容层；守护测试扫 ERP 应用，以后再有人写旧列名会直接失败。
- 建议顺序：本分支已在 field-help-icon、第 9 步之后，可直接合入；stocktake-scan-inline 谁后合谁变基（`stocktake.js` 一行）。

## 剩余

- 旧列删除另排（确认不回退后）。
- `ERP Company` 选账本（Express / MR.ERP）、MR.ERP 连接、导入直写与代录接线是第 6 步后面几块，不在本提交范围；单据上的标签仍写 Express，
  接 MR.ERP 时要改成按账本显示（改已有文案，届时先列清单给用户看）。
