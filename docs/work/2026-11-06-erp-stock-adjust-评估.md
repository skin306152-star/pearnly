# 需求影响评估：ERP 库存整改（菜单顺序、物料调整 IA、盘点改流程、编号、Express、仓库 / 仓位）

> **审定：主会话 11-06 审过、开工。** 第 12 节逐条定：#1 #2 #3 #4 #6 #8 #9 按建议；**#5 改为只带人亲手填 / 改过的单价**（见 4.4）；
> #7 先按 TK 做，副作用转用户（见文末「待爸爸确认」）；10.2 改文案清单由主会话转用户确认，确认前不登记 `copy-approved.tsv`。
> 叫法：界面一律「物料」（菜单「物料调整」、按钮「按盘点结果调整物料」）。
>
> 按 [需求影响评估模板](../development/templates/impact-assessment.md) 填写；只写事实和建议，**不施工**。交主会话审定。
> 基线：`origin/main` e60fcb20（11-06）；分支 `pearnly/erp-stock-adjust-df0d3d`。下文路径省略 `apps/pearnly_erp_portal/pearnly_erp_portal/`。
> 证据层级：代码（本分支逐处读过）+ testerp 测试租户只读统计（11-06，生产机 ERP 网页容器里只读事务查一次，不进数据库容器）。
> **没有**查正式租户（要先问主会话，见 12 节 #1）；**没有**做实验栈实测。
> 同类先例：[调拨 P2 评估](2026-10-30-multi-warehouse-p2-transfer-评估.md)（出入库单第三个方向）、[盘点做完整评估](2026-10-29-stocktake-complete-评估.md)、
> [多仓闭环评估](2026-10-26-multi-warehouse-closed-loop-评估.md)、[出入库接 Express 计划](2026-10-24-erp-stock-express-plan.md)。

## 0. 需求原文与理解

- **来源**：岳父 11-06 会议（主会话转述，范围已经用户确认）；用户看截图后追加「单头 / 明细重复的仓库、仓位」和「地点 / 库位 / 仓位混用」整改，并要求严格用官方组件、照产品设计语言。
- **叫法更正（主会话转用户，11-06）**：界面一律叫「物料」不叫「商品」——菜单「物料调整」、盘点按钮「按盘点结果调整物料」；泰 / 英 / 日照术语表里「物料」的现有叫法（สินค้า / Product / 商品），不另起新词。本评估已全文按此改。
- **我的理解**：
  1. 侧栏「物料与库存」顺序改为：库存、物料、入库、出库、调拨、**物料调整**、收发存报表、盘点；团队管理岗位目录同步加「物料调整」。
  2. 新单据「物料调整」（号 `IA`）：账面数与实物对不上时手工录；一张单正负混录（减少直接输 -100，同 Express）；增加行单价默认当前平均成本、可改；
     收发存里增加记「收入」、减少记「发出」；作废照出入库反向冲回。
  3. 盘点「结束盘点」只锁定结果、算差异，**不动库存、不开单、不写 Express**；新按钮「按盘点结果调整物料」把差异生成一张物料调整单**草稿**，人改完提交才入账。
     一份盘点同时只有一张有效调整单，作废后才能再生成；两边互相记着。停用物料重新启用、按台车辆挪位、盘点中新建的物料入库都挪到调整单过账时做。
  4. 新调整单一律 `IA`；历史 `AD` 单不改号，以后显示在「物料调整」列表，不再出现在入库 / 出库列表。
  5. 只有勾了「接 Express」的公司（`ERP Company.express_enabled`）连通：一张调整单 = Express 一张 `TK`；盘点本身不再写 TK。
  6. 一张单一个仓库（以单头为准），明细行不再显示「仓库」；仓位单头是默认、行上可改、以行为准；叫法统一「仓位 / ตำแหน่ง」。
- **不在本需求范围**：存放位置主档（暂缓，见 13 节末）；调整单打印版面（没有样板，见 12 节 #6）；盘点按库位 / 分类部分盘；跨公司调整；
  从 Express 读回别人在 Express 里录的 TK；手工「直接调数」接口 `api_adjust`（只有测试脚本在用，不改）；Cowork 盘点（独立一套）。

## 1. 现有同类与复用

| 项 | 现状（路径） | 结论 |
|---|---|---|
| 出入库 / 调拨单 | `ERP Stock Entry`（`inventory/documents.py`）：`direction` in / out / transfer；单头仓 + 仓位、明细、草稿 / 提交 / 作废状态、取号、附件（统一原件层）、按台、权限（`inventory/access.py` 按 `stock.<方向>`）、Express 推送与作废（`express/stock_entry.py`）。页面 `public/desk/stock-entry.js` 三个方向共用 | **扩展：加第四个方向 `adjust`**（同调拨 P2 的做法）。新建 DocType 要把取号、附件、作废、权限、Express 推送 / 作废、体检、CC 工具、待处理再抄一遍，不取 |
| 带符号流水 | `inventory/contracts.py`：`ADJUST = "adjust"` 已是带符号类型（`SIGNED_TYPES`），`stock.apply_movement` 正数按给的单价入、负数按当前均价出 | 复用 `adjust` 流水类型，不加新类型 |
| 盘点过账 | `count.post_count`：按快照算差异（含库位挪动「先减后加」、按台原处减 1），**每个差额开一张 AD 号的出入库单并当场过账**（`ref_type = count`），再整份排一张 TK（`express/stock_entry.count_document` 按品 × 仓轧差） | 差异算法原样复用，改成「生成一张 IA 草稿的明细」；过账时机挪到 IA 提交 |
| TK 写入 | CC 桌面版 `dbf_stock.py` 已支持 TK：数量带正负、正数行带 `unit_cost` 就按它入（否则取 Express 均价）、`dbf_void.py` 已能作废 TK | **CC 不用改、不升载荷版本**（施工时用离线回归核一次带单价的 TK） |
| 收发存 | `inventory/report.py` 按 `qty_base` 正负分「收入 / 发出」两栏 | 不改即满足「增加记收入、减少记发出」 |
| 体检 | `inventory/audit.py`：`count_link_mismatch`（已过账的盘点要有已提交的 AD 单）、`entry_lines_mismatch`（明细与流水对得上） | 扩展：盘点认 `adjustment`；明细核数认带符号 |
| 单头仓位 | `inventory/positions.py` `header` / `prepare`：行上没写仓用单头仓，行上没写仓位用单头仓位；`p.place`（`public/desk/transactions.js`）单头改了带着行走 | 复用；只改「行上的仓一律等于单头」与界面上去掉行仓 |

## 2. Frappe 原生能力

- **单据结构**：原 DocType 加选项 / 字段走原生迁移（JSON + `bench migrate`），不手改库：`ERP Stock Entry.direction` 加 `adjust`、加只读 `stock_count`（Link 到盘点）；
  `ERP Stock Count` 加只读 `adjustment`（Link 到调整单）。盘点 `status` 已有 `Closed` 选项（现在不用），直接启用。
- **页面**：新页 `pearnly-stock-adjust` 用标准 Page JSON/JS，走 `p.stockEntry(page, t, "adjust")` 同一份代码：列表 DataTable（服务端分页 / 排序 / 筛选）、
  新建 Dialog + 「在完整页面编辑」整页、明细官方 FieldGroup（`p.trade.lines_editor`）、提交 `frappe.confirm`、成功 `show_alert`、错误服务端 `frappe.throw`。
  单价格用原生 `read_only_depends_on`（数量 ≤ 0 的行只读），不写自定义样式、不画控件。
- **盘点页**：「按盘点结果调整物料」是 `page.add_inner_button`（同现在「结束盘点」那一排）；生成后用 `frappe.set_route` 打开那张调整单草稿。
- **菜单**：ADR-009，产品自己的 `hooks.py` `pearnly_navigation` 加页、调分组顺序；`access/menu.py` 加菜单键 `stock-adjust`。
- **结论**：全部原生；没有需要自定义样式或控件的地方，架构检查不应有新违规。

## 3. 按类通用

- **「所有库存单」**：调整单与入库 / 出库 / 调拨同一套单头仓 + 仓位、取号、附件、作废、按台、Express 推送 / 作废、体检、CC 工具、待处理、开始前确认卡片。
- **「一张单一个仓库」一类**（第 6 条）逐个核过：

| 单据 / 入口 | 单头 | 明细行现状 | 改法 |
|---|---|---|---|
| 入库 / 出库（`stock-entry.js`） | 仓库 + 仓位 | 行上有只读「仓库」镜像 + 可改「仓位」 | 去掉行上「仓库」；仓位保留 |
| 调拨 | 调出仓库 / 仓位、调入仓库 / 仓位 | 行上只读「仓库」（服务端强制 = 调出仓，真冗余）+ 调出 / 调入仓位 | 去掉行上「仓库」 |
| 物料调整（新） | 仓库 + 仓位 | — | 只有仓位 |
| 采购单（`purchase-fields.js`） | 仓库 + 「地点」 | 行上可改「仓库」+「地点」 | 去掉行上「仓库」；「地点」→「仓位」 |
| 销售单（`sales-fields.js`） | 仓库 + 「地点」 | 同采购 | 同采购 |
| 期初（`stock-opening.js`） | 仓库 | 行上可改「仓库」（10-26 多仓 P1 G5 有意做的「一张期初表分几个仓」）+ 仓位 | 去掉行上「仓库」，Excel 模板去掉仓库列（反转 G5，见 12 节 #4） |
| LINE 收单、收银 POS | 只有单头 / 终端仓 | 界面无行仓 | 不涉及 |
| 盘点 | 盘点仓 | 记录写「实际仓 / 仓位」（在别处找到的货，设计如此） | 不改（不是单据明细，是找到的位置） |
| 新建物料弹窗「期初库存」 | — | 每行一个仓（一件物料在几个仓的期初） | 不改（不是一张单） |

- **术语一类**：所有显示「地点」「库位」的界面文案统一成「仓位」（清单见 10 节）。代码注释里的「库位」不是界面文案，不改。

## 4. 上下游数据

### 4.1 表 → 字段 → 上下游（数据串联表）

| 表 | 字段 | 写入方 | 读取方 / 下游 | 本次变化 |
|---|---|---|---|---|
| `ERP Stock Entry`（单头） | `direction` | 建单 | 列表筛选、权限（`stock.<方向>`）、取号、Express 单别、打印 | 加 `adjust` |
| | `doc_no` | `numbering.allocate` | 列表、流水 `doc_no`、收发存「关联单据」、Express `ref_no` | 调整单 `IA2569-000001`；历史 `AD` 不动 |
| | `warehouse` / `location` | 单头 | 行默认、公司归属（仓属于公司）、Express 单头 `loccod`、打印 | 行上的仓一律 = 单头（新单） |
| | `ref_type` / `ref_id` | 系统单（采购入库 `purchase`、历史盘点 `count`） | Express `eligible`（跳过 purchase / count）、作废限制、流水 `ref_type` | 新调整单**不用** `count`（否则会被 Express 跳过），留空 |
| | `stock_count`（新） | 由盘点生成时 | 调整单详情显示来源盘点、作废时回写盘点 | 新增 |
| | `status` | draft → submitted → void | 列表、过账、作废 | 盘点生成的调整单停在 draft，人提交 |
| | `ledger_*` | Express 推送回执 | 单据 Express 弹窗、待处理、CC 工具、库存对齐 | 调整单照用 |
| `ERP Stock Entry Line` | `product` / `qty` / `unit` / `unit_cost` | 建单 | 流水、Express 明细、打印 | 调整单 `qty` 可正可负；`unit_cost` 只用于正数行 |
| | `warehouse` | `positions.prepare` | 流水的仓、Express 行 `loccod`（`express/stock_entry.document` 按**行**取仓） | 新单 = 单头；历史单原值保留 |
| | `location` / `to_location` | 行 / 单头默认 | 流水的仓位 | 不变 |
| | `unit_cost_manual`（新） | 页面：人填 / 改过单价时置 1；系统带出的默认值、盘点估值为 0 | Express TK 是否带 `unit_cost` | 新增（只对调整单有意义） |
| `ERP Stock Movement` | `movement_type` | `stock.apply_movement` | 收发存（按正负分栏）、库存卡类型、成本滚动 | 调整单写 `adjust`（带符号） |
| | `qty_base`（带符号）、`unit_cost`、`value_delta` | 同上 | 结存、收发存数量 / 金额 | 正数行按单价入；负数行按当前均价出 |
| | `ref_type` / `ref_id` / `doc_no` | 同上 | 作废冲回（按 ref 找原流水）、Express 对齐 `_pending`、库存卡 | 调整单 `ref_type = stock_entry`、`ref_id` = 单名、`doc_no` = IA 号 |
| `ERP Stock Balance` | `qty_on_hand` / `value` / `unit_cost` | 由流水推出 | 库存一览、盘点快照、调整单默认单价 | 不变 |
| `ERP Stock Count` | `status` | Active →（结束）**Closed** →（调整单提交）**Posted** | 盘点列表、按钮显示、差异报表、打印结果表 | 启用 `Closed`；`Posted` 改由调整单提交时置 |
| | `closed_at/by`、`posted_at/by` | 结束 / 调整单提交 | 详情 | 分开记 |
| | `adjustment`（新） | 生成调整单时 | 按钮（有就打开、没有才生成）、体检 | 新增；调整单作废时清空、盘点回 `Closed` |
| | `ledger_*` | 历史：整份盘点写 TK | Express 弹窗、待处理 | **新盘点不再写**；旧盘点的队列 / 状态照旧处理 |
| `ERP Stock Count Item` / `Entry` | 快照 `book_qty`、记录 `qty`、实际仓 / 仓位、`row_origin`、`serial` | 盘点 | 差异算法 → 调整单明细 | 不变（算法照搬） |
| `ERP Product` | `is_active`、来由 `origin` | 盘点就地新建 / 重新启用 | — | 重新启用从「结束盘点」挪到「调整单提交」 |
| `ERP Serial No` | 仓 / 仓位 / 状态 | 按台单据 | 选台、出台 | 盘点盘到别处的台：挪位从结束盘点挪到调整单提交 |
| 收发存（`inventory/report.py`） | 收入 = 正数流水、发出 = 负数流水 | — | 用户报表 | 不改，调整单自然分两栏 |
| Express | TK（ISRUN 单别 TK，STTRN / STCRD / STLOC，正数行带单价） | CC 桌面版 `dbf_stock` | 回读、作废 `dbf_void` | 一张 IA = 一张 TK；盘点本身不写 |

### 4.2 ERP 单据流

- **手工调整单**：建单（可留草稿或直接提交，同出入库「提交即过账」口径）→ 提交：每行一笔 `adjust` 流水（正数入、负数出），号 IA；
  按台物料见 12 节 #3；→ 接 Express 的公司排队写 TK。
- **盘点 → 调整单**：
  1. 「结束盘点」：锁任务（`status = Closed`、记 `closed_at/by`），保留现有「盘点期间库存变了要重盘」的核对与审阅令牌；**不写流水、不开单、不排 Express**。
  2. 「按盘点结果调整物料」（只在 `Closed` 且没有有效调整单时出现）：用现在 `post_count` 的算法算操作（按快照、库位挪动先减后加、按台原处减 1、估值先位置成本后物料参考成本），
     生成**一张** IA 草稿：单头仓 = 盘点仓、`stock_count` = 盘点、备注「盘点：盘点名称」（沿用 `adjustment_note`）；每个操作一行（数量带符号，正数行单价 = 那时的估值）。
     盘点记 `adjustment` = 这张单。重复点（并发）按盘点行锁只生成一张。
  3. 人在调整单里可改数量、单价、加删行（就是一张普通调整单）。
  4. 提交：同一事务里先做盘点带来的三件事——停用物料重新启用（`_reactivate`）、盘到别处的台换位（`_move_counted_units`）、盘点就地新建的物料照常随明细入库——
     再落流水；盘点置 `Posted`、记 `posted_at/by`。
  5. 作废已提交的调整单：反向冲回（同出入库）；盘点回到 `Closed`、清 `adjustment`，按钮重新出现。删除草稿：草稿真删、盘点清 `adjustment`（号不回收，同 MR.ERP 先占号）。
- **差额口径**：调整单落的是「实盘 − 快照账面」的差额，不是「改成实盘数」。结束盘点后到提交调整单之间发生的进出货是真实业务，差额叠在当时结存上仍然正确（与 Express TK 同口径）。
- **受影响**：库存一览、收发存、库存卡（类型显示「调整」）、盘点差异报表（`report/erp_stock_count_variance`：`Closed` 的盘点照样出差异，加一列 / 一行显示调整单号）、
  体检（`count_link_mismatch` 改成：新流程 `Posted` 的盘点要有已提交的 `adjustment`；历史 `Posted`（有 AD 单的）照旧核 AD；`Closed` 不算问题）、
  打印（盘点结果表在 `Closed` 就能印）。

### 4.3 编号

- `numbering/contracts.py`：`BY_DIRECTION` 加 `"adjust": "IA"`，`PREFIXES` 加 `IA` 并**保留 `AD`**（历史号仍是本系统形状，`split` / `matches` 照认）；
  新代码不再按 `stocktake` 取号（这条映射保留只为兼容，没有调用方后可删）。IR / IS / TF / PE / SI 不变。
- 列表：「物料调整」列表 = `direction = adjust` **或** `ref_type = count`（历史 AD）；入库 / 出库列表排除 `ref_type = count`。
  testerp 现状正好说明问题：入库 / 出库列表里的 9 张单**全部**是盘点生成的 AD 单（in 已提交 5、作废 1；out 已提交 3）。

### 4.4 Express 映射（只对 `express_enabled` 且已接上的公司，`push.started_linked` 现有判断不变）

- **写入**：调整单 → TK。`express/stock_entry.kind()` 加 `adjust → TK`；`document()` 对调整单**按「存货 × Express 仓」轧差（换库存单位）**、净 0 的行不写
  （Express 不分仓位，同一仓里「先减后加」的仓位挪动写成毛额会在不许负库存的账套里被拦——这是现在 `count_document` 的做法，挪进 `document()` 让写入与作废用同一份明细）。
  一张调整单全是仓位挪动、轧完一行不剩 → 不排 Express（`ledger_state` 留空，单据上 Express 按钮不出）。
- **单价**（主会话定 #5）：只带**人亲手填或改过**的单价——明细行记 `unit_cost_manual`（新 Check 字段）：选物料时系统带出的默认均价、盘点生成时的估值都是 0，人在单价格里填 / 改过才是 1；提交时只有 1 且数量为正的行带 `unit_cost`，其余不带，CC 取 Express 自己的均价。这样盘点来的调整单与 10-25「盘点 TK 不带价、以 Express 为准」一致，人改的价又能进 Express。ERP 自己的流水照单上单价入账（不论是不是人填的）。
- **科目 / 簿**：照现有 TK：这家公司为 TK 选过科目用选的，没选过用账套默认；账套没有 TK 簿停 `NO_STOCK_BOOK`；Express 里没有的物料走现有选存货 / 新建（载荷 15）。
- **作废**：已写进去的调整单作废 → 现有 `express_push.void` → CC `dbf_void` 作废 TK（照原生）；没写进去的只在 ERP 作废。
- **盘点本身不再写 TK**：`close_count` 不再 `express_push.queue(COUNT, …)`。历史盘点里还在队列 / 停下的 TK 照旧能处理（`push.COUNT` 通道、待处理、CC 工具里的「C 盘点」保留只读给历史）。
  这样同一笔差异只写一次：新盘点 → 调整单 TK；旧盘点 → 旧 TK。
- **CC 端副作用（如实记）**：TK 是 Express「ตรวจนับสินค้า」单别，CC 写 TK 时会把这些物料在该仓的「最后盘点日」（STLOC.TAKDAT）记成单据日、说明栏固定写
  「ตรวจนับสินค้า + 单号」。手工调整单走 TK 也会这样（任务书定的 TK，见 12 节 #7）。
- **回读对齐**（`express/masters._pending`）：调整单是 `ref_type = stock_entry` 的出入库单，现有「接上之后开始、还没写成的出入库单照算」自动覆盖；历史盘点那一段原样保留。
- **写账前置检查 / 开始前确认卡片**（`express/pending.py`、`precheck.py`）：`STOCK_ROUTES` 加 `adjust → pearnly-stock-adjust`、方向权限加 `stock.adjust`、
  `stock_entry.form_body` 认 `adjust → TK`；盘点类保留给历史。
- **CC 工具**（`express/cc_tools.py`）：「M 出入库」那一类按 `ERP Stock Entry` 取，调整单自动在内；说明文字加上「调整」。

### 4.5 CC 桌面版 / Cowork / DMS

- CC 桌面版：**载荷不变、不发新版**。TK 带正负与正数行单价、TK 作废都是现有能力（`dbf_stock` / `dbf_void`）；施工时跑一次 CC 离线回归核「带单价的 TK 写入 + 读回 + 作废」。
- Cowork：不涉及（Cowork 盘点是独立四张表，不经 ERP 库存）。DMS：不涉及。共用层 `pearnly_core`：预计不改（`express_rules` 不动）；若施工中碰到，合入前在生产镜像跑候选离线检查。

## 5. 隔离与权限

- **新能力 `stock.adjust`**（别名到 `stock.write`，同 `stock.transfer`），菜单键 `stock-adjust`（库存岗位），团队管理里逐人可勾；
  `entry_permission` / `entry_query` 按「这张单的种类」判：`direction = adjust` 或 `ref_type = count`（历史 AD）→ 要 `stock.adjust`；其余照旧 `stock.<方向>`。
  打开、附件、作废、Express 弹窗都过同一道。
- **盘点生成 / 提交调整单**：生成要 `count.write`；提交盘点来的调整单认 `count.write` **或** `stock.adjust`（以前「结束盘点」就是 `count.write` 过账，盘点员不因改流程失去入账能力），
  手工调整单只认 `stock.adjust`（见 12 节 #2）。全部服务端判。
- **成本**：调整单单价格只给能看成本的岗位（`guard.cost_visible()`）；看不到成本的人录调整单时不出单价格，服务端按当前均价填。
- **公司隔离**：单头仓必须是当前公司启用的仓（`warehouses.required`），行仓 = 单头；列表只出当前公司仓的单；盘点生成调整单按盘点所属公司（`count_company`）取仓。
- **已有员工**：历史 AD 单挪到「物料调整」列表后，只有入库 / 出库菜单的人会看不到它们。迁移补丁给已有「盘点」或「入库」或「出库」菜单的人加「物料调整」菜单（见 12 节 #2），ERP Admin 本来全有。

## 6. 入口：网页、手机、LINE、拍照、CC

| 入口 | 改动 |
|---|---|
| Desk 侧栏（电脑 / 手机窄屏同一套） | 顺序：库存、物料、入库、出库、调拨、物料调整、收发存报表、盘点 |
| 物料调整页 | 列表（单号、日期、仓库、来源盘点、状态、Express）+ 新建弹窗 / 整页；单头单号（只读预览）、日期、仓库、仓位；明细：物料、内部编码、名称或说明、仓位、数量（可负）、单位、单价（正数行可改）；备注；附件；按台物料提交前选台 / 登记台 |
| 入库 / 出库页 | 列表不再出 AD 单；明细去掉「仓库」 |
| 调拨页 | 明细去掉「仓库」 |
| 采购 / 销售页 | 明细去掉「仓库」；单头 / 明细「地点」→「仓位」 |
| 期初页 | 明细去掉「仓库」；Excel 模板去掉仓库列（12 节 #4） |
| 盘点详情 | 进行中：「结束盘点」（确认文案改，见 10 节）；`Closed`：打印 / 导出 /「按盘点结果调整物料」（已有调整单时换成打开那张）；`Posted`：打印 / 导出 / 打开调整单 |
| LINE 手机盘点页 | 不涉及（没有结束盘点入口） |
| 扫码 / 相机 | 调整单明细物料格沿用出入库同一个行编辑器的扫码，相机共用件不改 |
| CC 对话 | 不新加工具；现有出入库类工具自动认调整单 |

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files`（计划改动的 29 个文件，11-06 跑）：命中 `erp-transactions`（23 个入口）、`erp-printing`（6）、`erp-count-code`（1），
连同 Express 写入（`erp.cc.write`、`erp.cc.stock-entry`、`erp.cc.stock-void`）、按台（`erp.serials.dialog`）、单头仓（`erp.place.header`）、期初分仓（`erp.inventory.opening.row-warehouse`）、
盘点差异报表、各打印、附件、扫码、主档 / 单据隔离、导航等共 59 个入口。登记表完整（57 个共用件、205 个入口）。

- 新入口补进 `scripts/impact/shared-parts.toml`：`erp.page.stock-adjust`（auto）、`erp.stock-adjust`（建单正负混录 / 提交 / 作废 / 按台）、
  `erp.stock-count.adjust`（结束盘点不动库存、生成草稿、提交入账、作废后再生成）、`erp.cc.stock-adjust`（TK 写入与作废）。
- 要改的已登记入口说明：`erp.inventory.opening.row-warehouse`（期初分仓反转）、`erp.cc.stock-entry`（盘点改为调整单写 TK）、`erp.place.header`（行上无仓）。
- 流程用例：新 `scripts/flows/erp_stock_adjust.py`（登记 `tests/flows/registry.toml`）；改 `erp_lib.py`（第 146 行造「已过账盘点」的数据要走「结束 → 生成 → 提交」）、
  `erp_stocktake_*` 里断言「结束即过账」的、`scripts/regression/datasets.py` / `print_check.py` 的已过账盘点样本。产品地图 `scripts/impact/product-map/erp.toml` 同一提交更新。
- 冒烟：`scripts/smoke/` 加「物料调整」列表与详情只读探针。

## 8. 存储与成本

- 每张调整单 = 行数条流水，量级同出入库；盘点不再一差额一张单，单据数反而少。两个 Link 字段，忽略不计。
- 附件走统一原件层（ADR-024）不变；无新外部调用（识别、LINE、云存储）、无固定月费；Express 写入在客户电脑本地执行。

## 9. 删除、作废、追溯

- 调整单：草稿可删（真删，盘点清 `adjustment`）；已提交只能作废，追加反向流水（同一单号），流水不删；按台流水留痕。
- 盘点：`Closed` 之后不能再录；调整单作废回到 `Closed` 可再生成；`Active` 的盘点照旧能删，`Closed` / `Posted` 不能删（有调整单的追溯链）。
- 已写进 Express 的调整单作废 → 照原生作废 TK；Express 里改过原单 → 停人工（`VOID_DOCUMENT_CHANGED`，现有）。
- 历史 AD 单：不改号、不改数据；单张作废的限制照旧（所属盘点写过 TK 的不能单独作废，`stock.countEntryVoid`）。
- 部署时进行中的盘点：按新流程结束；**没有**处于「结束未过账」的老数据（现在结束 = 过账，`Closed` 从未用过；testerp：进行中 3、已过账 3）。

## 10. 文案与官方 UI

### 10.1 新增文案（官方简短，四语，`scripts/check-copy.py` 过闸）

物料调整（菜单 / 页名；按术语表「物料」的现有叫法：泰 ปรับปรุงสินค้า、英 Product Adjustment、日 商品調整）、新建物料调整单、按盘点结果调整物料、调整单、来源盘点、
错误「此盘点已有调整单」「数量不能为 0」。盘点 `Posted` 显示沿用已有「已过账」（`stock.count.statusPosted`），`Closed` 沿用「已结束」。

### 10.2 改已有文案（**要用户确认**后登记 `scripts/i18n/copy-approved.tsv`）

| # | 键 / msgid | 用在哪 | 中文 旧 → 新 | 泰文 旧 → 新 |
|---|---|---|---|---|
| 1 | `Location`（无键 msgid） | 采购 / 销售单头与明细、各 DocType 的 Location 标签 | 地点 → 仓位 | ที่ตั้ง → ตำแหน่ง |
| 2 | `stock.board.shelf` | 库存相关列头 | 库位 → 仓位 | 不变 |
| 3 | `This location is not in the location list of this warehouse` | 出错 | 库位不在此仓库的库位清单中 → 仓位不在此仓库的仓位清单中 | 不变 |
| 4 | `A location with stock cannot be turned off` | 出错 | 有库存的库位不能停用 → 有库存的仓位不能停用 | 不变 |
| 5 | `A location that has been used cannot be removed` | 出错 | 已使用的库位不能删除 → 已使用的仓位不能删除 | 不变 |
| 6 | `Duplicate location` | 出错 | 库位重复 → 仓位重复 | 不变 |
| 7 | `Locations`（无键）、`stock.location.list` | 仓库页清单标题 | 库位清单 → 仓位清单 | 不变 |
| 8 | `stock.location.fromStock` | 仓库页按钮 | 带入有库存的库位 → 带入有库存的仓位 | 不变 |
| 9 | `This warehouse has no active location` | 出错 | 此仓库没有启用的库位 → 此仓库没有启用的仓位 | 不变 |
| 10 | `stock.workbench.finishConfirm` | 结束盘点确认 | 确定要结束盘点并将已确认的差异写入库存吗？已盘点 {0} 项，未盘点 {1} 项保持不变，结束后仅可查看和导出。 → **确定要结束盘点吗？已盘点 {0} 项，未盘点 {1} 项，结束后不能再录入。** | ต้องการสิ้นสุดการตรวจนับและปรับสต็อกตามผลต่างที่ยืนยันแล้วใช่หรือไม่ … → **ต้องการสิ้นสุดการตรวจนับใช่หรือไม่ นับแล้ว {0} รายการ ยังไม่นับ {1} รายการ หลังสิ้นสุดบันทึกเพิ่มไม่ได้** |

英文、日文跟中文同步改（英文 msgid 无键的行按原规则换成新 msgid）。施工时以 `check-copy.py --report` 第 1 节为准再贴一次完整四语清单。

### 10.3 组件

Page / Dialog / FieldGroup（含 `read_only_depends_on`）/ ControlLink / Autocomplete / DataTable / `frappe.confirm` / `msgprint` / `show_alert`；无自定义样式、无自画控件。
调整单页照出入库页同一份代码与布局（单头两栏成行、明细三栏、删除单独一行、左下「在完整页面编辑」、右下「提交」）。

## 11. 测试与验收

### 单元 / 离线（还原修复就失败的那种）
- 调整单：正负混录落流水（正按单价入、负按均价出）、数量 0 被拒、成本未知时正数行不猜价、包装单位换算、作废冲回、权限（只有入库菜单的人建不了调整单）、
  看不到成本的人提交时按均价、单头仓跨公司被拒、行仓被强制为单头仓。
- 列表：调整单列表含历史 AD；入库 / 出库列表不含 AD；`ref_type` 为空的老单照常出现在入库 / 出库列表。
- 盘点：结束后结存与流水条数不变、不排 Express；生成草稿的明细与旧 `post_count` 算出的操作逐条相等（同一份快照对拍，含仓位挪动、按台原处减 1、估值回落参考成本）；
  只能有一张有效调整单（并发生成只得一张）；提交后盘点 `Posted`、物料重新启用、台换位；作废后盘点回 `Closed`、可再生成；删草稿同理。
- 体检：新旧两种盘点链都不误报。
- 编号：`IA` 取号、`AD` 仍被认作本系统号。
- Express：`kind(adjust) = TK`；轧差（仓位挪动净 0 不写、全净 0 不排）；正数行带单价；未勾 `express_enabled` 的公司不排；作废载荷与写入明细一致；
  盘点结束不再排 TK、历史盘点 TK 通道还在。CC 离线回归：带单价的 TK 写入、读回、作废。
- 仓库 / 仓位：采购 / 销售 / 出入库 / 调拨 / 期初提交时行仓 = 单头；历史单（行仓 ≠ 单头）读出来行仓不变、详情显示仓库列；Express 载荷按行取仓的老单不变。
- 菜单：顺序断言、岗位目录含「物料调整」（`tests/test_native_desk_contract.py`）。

### 实验栈（本机，生产镜像底座 + 本分支，用 testerp 导出数据）
录调整单正负混录 → 收发存两栏；盘点结束库存不动 → 按钮生成草稿 → 改一行 → 提交入账；作废 → 回 `Closed` → 再生成；
勾了 Express 的公司调整单排 TK（停在等电脑即可，不连真 Express）、没勾的不排；入库 / 出库列表看不到 AD、调整列表看得到；各单明细无「仓库」、叫法「仓位」；
电脑 / 手机宽度各截图存证。受影响入口按 check-impact 清单记「通过 / 不通过 / 没测到（原因）」。

### 验收
随 Cowork 下一批部署；测试机怎么验先问主会话。用户 / 岳父要亲自看：调整单正负混录、盘点结束→生成→提交、收发存两栏、Express 里的 TK。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 正式租户有没有「明细行仓库 ≠ 单头」的旧单（mrerp / polymer 等），能否只读查一次。**已查（11-06，同意后一次 SSH、ERP 网页容器只读事务）：gize / mrerp / polymer / test01 / testerp 五个 ERP 站点，出入库 / 采购 / 销售行仓 ≠ 单头的单全部 0 张；盘点跨仓记录只有 testerp 1 份（2 条）；所有盘点都没有 Express 状态** | 同意的话按 testerp 同一个脚本只读查（生产机 ERP 网页容器、只读事务、一次 SSH）。testerp 结果：出入库 / 采购 / 销售都是 **0 张**。无论查不查，做法都是「旧单原值保留、详情有不一致时显示仓库列」，不改数据 | 不查就不知道旧单里有没有要显示仓库列的情况，做法不变 |
| 2 | 「物料调整」权限怎么发 | 新菜单 `stock-adjust`；迁移给已有「盘点」「入库」「出库」任一菜单的人加上（不让人突然看不到历史 AD 单）；盘点来的调整单 `count.write` 也能提交，手工的只认 `stock.adjust` | 不补发会让现在能看到 AD 单的员工看不到 |
| 3 | 手工调整单里按台管理的物料怎么办 | 增加行登记新台、减少行选在库的台（同入库「其他」/ 出库，复用同一个选台弹窗）；盘点来的调整单照盘点口径：不登记 / 不出台，只把盘到别处的台换位 | 不定就只能拒按台物料进调整单 |
| 4 | 期初页去掉行上「仓库」等于反转 10-26 多仓 P1 G5「一张期初表分几个仓」 | 照「一张单一个仓库」反转：页面行上去掉仓库、Excel 模板去掉仓库列；旧模板带仓库列导入时，与表头不同的行在预览里列为问题行（期初预览本来有问题行） | 保留 G5 就和第 6 条口径冲突 |
| 5 | 调整单写 TK 带不带单价 | 带单上的单价（人能改，写什么就是什么；单价空的不带，CC 取 Express 均价）。10-25 盘点 TK 定的是「不带、以 Express 为准」，这里口径会变 | 不带的话人在 ERP 改的单价进不了 Express |
| 6 | 调整单要不要打印 | 本批不出打印（没有岳父样板，同调拨 P2 问 4）；历史 AD 单照旧印收货单 / 领料单 | 要打印就要样板 |
| 7 | 手工调整走 TK 会把 Express「最后盘点日」记成调整日、说明写「ตรวจนับสินค้า」 | 照任务书用 TK；如岳父在意，另议手工调整改 JU（Express 原生 JU 能不能负数未实录） | 只影响 Express 里的盘点日与说明 |
| 8 | 盘点在别的仓找到的货（跨仓记录），调整单怎么放 | 一份盘点一张调整单：单头 = 盘点仓，跨仓的那几行保留它们自己的仓（这是「一张单一个仓库」唯一的例外，详情这时显示仓库列；Express TK 本来按行带仓） | 拆成多张会违反「一份盘点一张有效调整单」 |
| 9 | 盘点生成的调整单草稿的号 | 草稿建时就占号（同现有草稿）；删草稿号不回收 | 改成提交时取号要改出入库草稿的通用取号 |

## 13. 工作量与排期（机器耗时）

| 步 | 内容 | 依赖 | 写码 + 本地检查 |
|---|---|---|---|
| S1 | 菜单顺序、权限目录、`stock.adjust`、迁移补丁（字段 + 补发菜单）、编号 IA | 12 节 #2 | 约 1 小时 |
| S2 | 物料调整单：服务端方向 `adjust`、正负混录、默认均价接口、列表（含历史 AD / 出入库排除 AD）、作废、附件方向、按台（#3）、页面 | S1 | 约 3 小时 |
| S3 | Express：`kind` / 轧差 `document` / 带单价 / 作废、盘点不再排 TK、pending / precheck / cc_tools、CC 离线回归 | S2、#5 | 约 1.5 小时 |
| S4 | 盘点改流程：结束只锁定、生成草稿、互相关联、提交时启用 / 换台 / 入账、作废回 `Closed`、差异报表、体检、打印结果表 | S2 | 约 3 小时 |
| S5 | 仓库 / 仓位：各单行仓 = 单头、页面去行仓、历史单显示、期初模板（#4）、术语与文案清单 | 10.2 确认 | 约 2.5 小时 |
| S6 | 流程用例、产品地图、冒烟、受影响入口实测、实验栈走查截图、全闸门 | S1–S5 | 约 3 小时机器 |

合计约 14 小时机器时间（不含等人）。S5 与 S2–S4 文件少有交叠，可在同一分支按序做；合入前先发消息给主会话排队（CC 1.12.15 也在排队），随 Cowork 下一批部署。

**存放位置主档（暂缓）**：以后在现有「仓库 → 仓位清单」（`ERP Warehouse Location`，已有名称、启用，10-29 加了说明）上扩展成主档（编号、类型、容量等），
单据上的「仓位」格继续是这份清单的选择框，不另起一套表。

## 旁见（不在本需求范围）

- `inventory/api.py::api_adjust`（无单据直接调数、`movement_type = adjust`、无单号）只有测试脚本在用；它的流水在 Express 对齐里算「其他来源」。保留不动，以后可删。

## 待爸爸确认（主会话转用户）

- 手工物料调整单在接 Express 的公司写成 TK（ตรวจนับสินค้า）。CC 写 TK 时，Express 会把这些物料在该仓的「最后盘点日」记成调整单日期，
  单据说明固定是「ตรวจนับสินค้า + 单号」（Express 原生盘点调整单的写法）。也就是说，Express 里看，手工调整和盘点调整是同一种单。
  如果岳父希望手工调整在 Express 里不算盘点，要另议改用调整单 JU（Express 原生 JU 能否录负数还没实录）。
