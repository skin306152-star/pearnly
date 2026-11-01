# 需求影响评估：多仓闭环 P3x（Express 端新商品）+ P4（报表跨仓合计）

> **审定：主会话 11-01 同意按评估施工，12 节四问全部按建议定。** 另两条：载荷号谁先合 main 谁拿 15（代录窗口 local_ce7e3ac9 也占了 15，后合的变基改下一个号，合前告诉主会话）；CC 正式版发布前先走 staging，在测试机从 1.12.9 实机升级通过。施工记录见 [P3x / P4 施工](2026-11-01-multi-warehouse-p3x-p4.md)。
>
> 按 [需求影响评估模板](../development/templates/impact-assessment.md) 填写；只写事实和建议，不施工。
> 基线：origin/main `99ac4898`（多仓 P2 / P2x、盘点 S0–S6 都在内；生产 072fad84）。
> 来源：[多仓闭环评估](2026-10-26-multi-warehouse-closed-loop-评估.md) 13 节 G14（P3x）、G18（P4）；[盘点评估](2026-10-29-stocktake-complete-评估.md) 12 节第 7 条。
> 说明：主会话把 P3x 称为「跨仓」，按原分期表 P3x 是 **G14：Express 端新商品**（库存单 / 盘点 / 退货遇到 Express 里没有的商品），跨仓合计是 P4。下文按原表写。

## 0. 需求原文与理解

- 原话：岳父 10-26「一公司多仓库全链路闭环」（主会话转述，见多仓评估 0 节）；分期由主会话 10-26 定。P3x 的直接起因是盘点评估第 7 条（用户 10-29 批「全部按建议」）：
  「接 Express 的公司里盘点新建的商品，本批先在过账写 TK 前停人工；CC 新版本（TK 建档）另排」。
- 我的理解：
  - **P3x**：接了 Express 的公司，库存类单据（出库 OU、入库 JU、盘点 TK、调拨 RL）里有 Express 还没有的商品时，现在整单停下：
    - 盘点：服务端停在 `express.item_unmapped`（`push.py:291`）。
    - 出入库、调拨：电脑按品名找不到就失败（`STOCK_ITEM_NOT_FOUND`，`dbf_stock.py` / `dbf_transfer.py`）。

    采购 / 销售已经有成熟的通道：同名直接用，不同名在 ERP 里问一次（选现有存货或新建），电脑建 STMAS（`product_master.ensure_stock_item`），写完把编码挂回 ERP 商品（`push.link_products`）。P3x 就是把这套通道扩到库存单，并把收银日汇总建档后不回填编码的缺口补上。
  - **P4**：库存一览现在一行是「商品 × 仓 × 库位」，P4 要做四件事：
    - 加「按商品合计」视图（跨仓合计）。
    - 低库存改成按商品在公司内的合计来比（现在按单个位置比）。
    - 收发存开放库位筛选。
    - 盘点结果 PDF 接盘点结果表：盘点 S4 已做，见 1 节。
- 不在本需求范围：
  - 盘点范围（G8）、Cowork 新发现行（G20）：属 P3，另排。
  - 成员按仓权限（G19）。
  - 新仓建档：G15 在 P0 / P1 已做，建仓时必选 Express 仓，不在电脑上建 Express 仓。
  - 改 Express 已有存货的品名或单位。

## 1. 现有同类与复用

| 项 | 现状（路径） | 结论 |
|---|---|---|
| 选存货（问一次） | 采购 / 销售走 `pearnly_core/express_rules/push/choices.py::resolve`：行上无码 → 同名直接用；不同名 → `needed` 里出 `kind=item`，按「对方 × 品名」记住；`apply_to_payload` 把选的码写进行。ERP 弹窗已能渲染 `item` 选项 | **复用**：库存单在 `express/stock_entry.prepare` 里调同一套判断（主题改按「库存 × ERP 商品」，见 4 节），不另做一套 |
| 建 STMAS | CC `product_master.ensure_stock_item`（采购 / 销售 / 期初在用，克隆账套结构、0 余额、记真实建档日） | **复用**：`dbf_stock` / `dbf_transfer` 遇到标了可建档的行调它 |
| 回填编码 | `push.link_products`：逐行按序号与品名认，只认领只被这张单用过的本地档。`LINES` 只登记了采购 / 销售（`push.py:455`） | **扩展**：加出入库单行、盘点（按商品直接认）、收银日汇总 |
| 回报逐行编码 | CC 回执的 `line_modes`（`jobs._stored_meta` 取 seq / name / stkcod）。`dbf_writer`（销售 / 日汇总）、`dbf_refund` 已报；`dbf_stock`、`dbf_transfer` 没有逐行回报 | 扩展这两个 |
| 库存一览 | `inventory/stock.py::_overview_rows`：先整批取再切页（无上限查询基线 6 处）；状态 `low` 按单个位置比 `min_stock`（`stock.py:404-409`） | 扩展：加合计视图；合计走服务端分组分页 |
| 收发存 | `stock-card.js` 原生筛选只列仓库、商品（`filter_fields`，`stock-card.js:242`）；服务端 `report.summary_page` 已收 `location` | 只开前端筛选项 |
| 盘点结果 PDF | 盘点 S4 已做：打印弹窗版式 / 只看 / 仓库 / 仓位 → 打印 / PDF / 导出 | **已完成，不再做**；S4 记的「PDF 下载没测到（实验室 wkhtmltopdf 拉资源被拒）」留测试环境验 |

## 2. Frappe 原生能力

- P3x：纯服务端与 CC 逻辑，不涉及界面组件；选存货沿用现有 Express 弹窗（Frappe Dialog）。
- P4：
  - 合计视图用现有 `p.list` 原生列表 + FilterArea，加一个原生 Select「显示方式：按位置 / 按商品」（同页切换，不另开页）。
  - 分组用 `frappe.qb` 的 `group_by` + `limit_start` / `limit_page_length`，服务端分页排序。
  - 收发存的库位筛选加到原生 `filter_fields`。
- 不需要自定义样式。

## 3. 按类通用

- P3x 属于「所有写进 Express、会动存货的 ERP 单据」：

  | 单据 | 现在 | P3x 后 |
  |---|---|---|
  | 采购 / 销售 | 已通 | 不动 |
  | 期初 | 接 Express 公司不让录 | 不涉及 |
  | 出库 OU、入库 JU、调拨 RL | 电脑失败 | 问一次 + 建档 |
  | 盘点 TK | 服务端停人工 | 问一次 + 建档 |
  | 收银日汇总 HS | 电脑会建档（`stock_sale`），但不回填 | 补回填 |
  | 退货 SR | 退货按原 HS 那一行找存货（`dbf_refund.py:528`），原 HS 写进去时已有档 | **不建档**；回填补上后 SR 自然按码找到 |

- P4 属于「库存余额类报表」，四处都按同一条口径「低库存按商品公司内合计」：
  - 库存一览
  - 标题旁四个数（低库存 / 缺货计数）
  - 导出 CSV
  - 首页 / CC 用到 `stock_totals` 的地方：CC 公司卡片若读这四个数也跟着变，施工时全仓查调用方

## 4. 上下游数据

- **ERP 内**：
  - P3x 不改库存流水，只改推送前准备和写完后的回填。
  - P4 只读，不改任何写入。
- **Express 映射**：
  - 选「现有存货」：行带那个码写，电脑按码认（码不在就报错交人重选，同采购）。
  - 选「新建」或同名认不到：行标 `new_ok`，电脑调 `ensure_stock_item` 建 0 余额档再写这一行。
  - **同名直接用**：Express 有同名存货就不问，电脑照名找（同采购口径）。
  - 选择记在哪：不像采购那样按「对方 × 品名」记，而是**按 ERP 商品**（`choices` 主题 `stock|<ERP Product>`）。原因：库存单没有对方，而且写完回填后这件商品就有了 Express 码，之后不会再问。
- **减少方向的新商品**（出库 OU、盘亏 TK、调出 RL）：建档后结存为 0，减少会成负数。
  - 建议与销售现行口径一致：照建、照写；账套不许负库存的，由现有 `stock_guard` 停下（`STOCK_INSUFFICIENT`，弹窗显示原因）。
  - 销售已是这样，不另设规则。见 12 节问 1。
- **CC 桌面版**：
  - 载荷要变：行上新增 `new_ok`，且库存单要回报逐行码。需要升载荷版本：取合入时 main 上的最大值 + 1，现在是 14，即 **15**。旧版 CC 诚实拒收（`payload_contract.py`）。
  - CC 要发新版本，合入 main 后直接发正式版。按「CC 更新走心跳」，先部署服务端再发 CC。
- **Cowork / DMS**：
  - Cowork 推送走同一个 core `resolve`。本期不改 `resolve` 本身，只在 ERP 的 `stock_entry.prepare` 新增调用，Cowork 不受影响。
  - DMS 不涉及。

## 5. 隔离与权限

- 选存货只出这张单所属公司当前账套的存货（`reported_products` 来自这本账套），记住的选择按公司存（`choices.remembered(company)`），不串公司。
- 回填只认领「只被这张单用过、还没挂码」的本地商品；同公司已有挂这个码的商品就不动（`link_products` 现有保护），不跨公司改。
- 合计视图按当前公司的仓汇总：`warehouses.scope_filter()`，不跨公司加总。成本列照现有 `guard.cost_visible()` 遮蔽。
- 权限沿用现有能力：推送弹窗为单据的写权限，库存一览为 `inventory.read`，不加新能力。

## 6. 入口：网页、手机、LINE、拍照

- P3x 的入口：
  - 出入库、调拨、盘点详情页上那个现有的 Express 弹窗：页面在线时，停在要选时自动弹（`NEEDED_EVENT`），同采购。
  - LINE 盘点页只录数、不推送，不涉及。
  - CC 对话不涉及。
- P4：
  - 库存一览与收发存的 Desk 页（电脑 + 窄屏），窄屏沿用现有列表。
  - LINE、拍照不涉及。
  - 相机不涉及。

## 7. 共用件与连带回归

`check-impact` 对预计改动文件（`express/stock_entry.py`、`express/push.py`、`inventory/stock.py`、`inventory/report.py`、`inventory.js`、`stock-card.js`、`companion/dbf_stock.py`、`dbf_transfer.py`、`product_master.py`、`payload_contract.py`）的输出：
命中 `cc-dbf-write` 共用件，入口如下：

| 入口 | 方式 | 本期怎么测 |
|---|---|---|
| `cc.desktop.write` | auto | 测 |
| `erp.cc.write` | auto | 测（采购 / 销售回填不能退步） |
| `erp.cc.stock-entry` | auto | 测（新商品 OU / JU / TK） |
| `erp.cc.stock-transfer` | manual | 测（新商品 RL） |
| `erp.cc.stock-void` | manual | 测（作废带新建档的单，档留着、只冲数量） |
| `erp.cc.pos-day` | manual | 测（日汇总回填） |
| `erp.cc.pos-refund` | manual | 测（回填后 SR 按码找到） |
| `cowork.cc.channel` | manual | 人工推一张，不应变化 |
| `erp.page.inventory`、`erp.page.stock-card` | auto | 测 |
| `erp.report.stock-summary-qty-sort` | auto | 计时，3 秒内 |
| `erp.entry.choices` | manual | 测 |

- **新冒烟 / 流程**：
  - `erp.express.new-item-stock`：离线 CC 回放，新商品 OU / JU / TK / RL 建档、读回、回填。
  - `erp.inventory.by-product`：两仓两库位同商品合计、低库存按合计、导出带合计。
  - 收发存库位筛选并入现有 `erp.page.stock-card`。
- **登记表**：
  - `erp.cc.stock-entry` 的场景补「新商品」。
  - 新入口补进 `scripts/impact/shared-parts.toml`。

## 8. 存储与成本

- P3x 不增加外部调用：不识别、不发 LINE、不用云存储。
  - 每件新商品在 Express 多一条 STMAS 行，在 ERP 多写两个字段。
  - 没有新增固定月费。
- P4 只读。合计视图在服务端分组，比现在整批取还少传数据。
- 不涉及原件层。

## 9. 删除、作废、追溯

- 作废一张带新建档的库存单：
  - 走现有作废通道（`dbf_void`），只冲回数量。
  - **建出的存货档不删**，同采购 / 销售现行：存货档是主档，可能已被别的单用，Express 原生作废也不删主档。
- ERP 本地商品挂上的 Express 码在作废后保留，下次按码认。
- 追溯：
  - 新建档的审计列沿用 `ensure_stock_item`：CREBY = CC 用户，CREDAT = 真实建档日。
  - 回执的逐行 `line_codes` 留在作业 meta 里。

## 10. 文案与官方 UI

- P3x：
  - 弹窗用现有「选存货」那套文案（采购同款），不新增。
  - 停下的原因沿用现有 `STOCK_ITEM_NOT_FOUND` 和 `express.item_unmapped`。本期之后盘点不再停在 item_unmapped，**那条文案保留**给旧版 CC 和读不到存货表的情况。
- P4 新增文案（官方简短）：
  - 「显示方式」「按位置」「按商品」
  - 合计视图列头复用现有词条（品名、编码、单位、在库数量、金额、状态），不加「仓库数」列（见问 2）
  - 库位筛选复用现有「仓位」
- 组件：原生 Select、`p.list`、FilterArea，无自定义样式。

## 11. 测试与验收

- **单元 / 离线**：
  - 服务端 `stock_entry.prepare`：
    - 同名不问
    - 不同名问一次
    - 选现有带码
    - 选新建标 `new_ok`
    - 读不到存货表照旧停 `READING`
    - 旧版 CC 拒收载荷 15
  - `link_products`：出入库行、盘点、日汇总三种回填，以及「别处用过不认领」「同公司已有该码不动」。
  - CC：
    - `dbf_stock` / `dbf_transfer` 新商品建档 + 回报 `line_modes`
    - 不许负库存账套减少行停 `STOCK_INSUFFICIENT`
    - 作废后档留着
    - 照 `ensure_stock_item` 现有离线用例的写法
  - P4：
    - 合计视图分组分页与排序
    - 低库存按合计
    - 成本遮蔽
    - 跨公司不串
    - 导出
- **实验栈流程**：
  - 新流程两条。
  - 受影响的 `erp.stock-entry` / `erp.stock-transfer` / `erp.stocktake.*` / `erp.inventory.*` / `erp.exports` 重跑。
- **测试机 C 盘 T 账套实写读回**（「C 盘 Express 随便写」）：
  - 新商品 TK +、JU、OU（允许负库存账套）、RL 各一张。
  - 作废其中一张。
  - 日汇总回填后跨天退货 SR 一张。
  - 用 Express 打开存货列表能看到新档、单据能开。
  - 动测试机前先问主会话并登记占用表。
- **验收**：
  - 用户看两处：库存一览「按商品」视图，以及盘点新商品写进 Express 后在 Express 里能看到。
  - 其余交验收窗口按登记表测。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | Express 里没有的商品出现在**减少**方向（出库 OU、盘亏 TK、调出 RL）时，照建档再写（会成负库存），还是停下交人？ | 同销售现行口径：照建照写；账套不许负库存的由现有检查停下并说原因 | 若定停下，要多一个停下码和一段判断；OU / RL 新商品都会停 |
| 2 | 「按商品」合计视图的列 | 品名、编码、单位、合计数量、合计金额（按权限）、状态；不加「仓库数」列，点一行跳到按位置视图并带上这件商品 | 要加列只是多一格 |
| 3 | 低库存在「按位置」视图里怎么显示 | 状态按商品公司合计判（同一件商品各行状态一致）；这个位置是 0 时仍显示「缺货」 | 若保持按位置比，四个数与合计视图会对不上 |
| 4 | 选「新建」时用 ERP 的品名，还是让人在弹窗里改名？ | 用 ERP 商品名（同采购 / 销售新建），不加改名格 | 加改名格要多一个输入与长度校验 |

## 13. 工作量与排期

| 步 | 内容 | 依赖 | 机器耗时（写码 + 本地检查） |
|---|---|---|---|
| A | P3x 服务端：`stock_entry.prepare` 选存货、`new_ok` 与载荷 15、盘点去掉 item_unmapped 停（新版 CC 才放行）、`link_products` 扩三种单、离线用例 | 问 1 | 约 1.5 小时 |
| B | P3x CC：`dbf_stock` / `dbf_transfer` 建档 + 逐行回报、`payload_contract` 15、离线回放、版本号 | A 的载荷形状 | 约 1.5 小时 |
| C | P4：合计视图（服务端分组分页 + Select 切换）、低库存按合计、导出、收发存库位筛选、用例 | 问 2、3 | 约 2 小时 |
| D | 本地闸门 + 实验栈流程（新两条 + 受影响的） | A–C | 约 1 小时机器 |
| E | 测试机 C 盘 T 账套实写读回（新 CC 装到测试机） | B；主会话同意动测试机 | 约 40 分钟 |
| F | 合入 main → 服务端随下一批部署 → CC 正式版发布 | D、E；部署按发布标记 | 不计 |

- C 与 A / B 文件不重叠，可以同时写。按「一个工作树一个写者」由本窗口顺序做 A → B → C，或者 C 交另一个窗口。
- A 和 B 必须同一批上线：服务端先部署，CC 后发。旧版 CC 遇到载荷 15 会诚实拒收，单据停在「需要新版 CC」，不会写错。
