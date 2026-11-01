# 需求影响评估：物料加「特性/颜色」、新建物料手机顺序

分支 `pearnly/product-attribute-field-e458d8`（基线 `origin/main` `20e7a9de`）。生产 `c9e28ace`（11-01 16:49 上线）。
状态：**评估待主会话审定，未施工**。

## 0. 需求原文与理解

- 原话：岳父手写清单（用户 11-01 晚转述，原图 `~/.claude/uploads/ed1273bf-…/029445fe-image.jpg`）：
  1 BARCODE、2 ชื่อสินค้า（商品名称）、3 ขนาด（尺寸）、4 คุณลักษณะ / สี（特性 / 颜色）。
- 用户口径（主会话转达）：第 3 项「尺寸」用现有「分类」，不新加字段；第 4 项新加，全局覆盖；最后才排序——新建物料弹窗与盘点就地新建弹窗
  （同一个复用弹窗）手机上依次是 条码、商品名称、分类、特性/颜色，其余在后；电脑宽保持两栏规则。
- 主会话 11-01 补：① 「分类」也要和特性/颜色一样全局出现（现有缺的都补）；② 岳父说分类只能输数字（属实），要改成支持中 / 泰 / 英文字和数字，
  在 Link（自有物料分类主档）与 Data 自由文本里二选一给建议，并核清存量 `category_id` 的含义、来源、Express 有没有写，盘点按分类筛选 / 报表跟着改。
- 我的理解：岳父建物料时要记「是什么颜色 / 什么款」，并希望一扫码、手机上最先填的四格就是他常用的四格；之后在单据、盘点、导入导出、
  打印里都能看到这件货的特性，区分「同名不同色」的货。
- 不在范围：不做「款式 × 颜色 × 尺码」矩阵 / 变体物料（Frappe ERPNext 的 Item Variant 那一套）；不做颜色下拉字典；不改 Express 主档；
  不动按台管理里「每台车的颜色」（`ERP Serial No.color`，那是每一台的，不是物料的）；不碰共用读码器 `reader.js`（另一窗口在改）。

## 1. 现有同类与复用

| 已有 | 位置 | 与本需求的关系 |
| --- | --- | --- |
| 物料主档 `ERP Product` | `pearnly_erp_portal/doctype/erp_product/erp_product.json` | 加一列 `attribute`（Data，140） |
| 新建 / 编辑物料共用字段 `product_fields` | `public/desk/products.js:83` | 新建弹窗、详情编辑、盘点借用（`link_editors["ERP Product"]` → `open_quick`，64a9623b / cc850eee）**同一份**，加一格三处都有 |
| 建档的标准字段 `standard_fields` | `products/service.py:211` | 商品页建档与盘点就地新建（`count.create_product_here`）共用；加 `attribute` 一处两边都收 |
| 二级页面批量新建 `grid_fields` | `products.js:493` | 同一套字段（用户 09-22「字段不变」），加进行展开 |
| 物料导入模板 `products/workbook.py` FIELD_LABELS | `:20` | 加一列，表头走词条四语（同 internal_code 的做法） |
| 盘点表 / 期初表模板 | `inventory/workbook.py:20`、`inventory/opening_workbook.py:8` | 下载时多出一列（只读参考，导回不改物料） |
| 每台车的「颜色」 | `ERP Serial No.color`、词条 `serial.color` | **不复用**：那是每一台的颜色（车架号级），本字段是物料级；按台公司里两者可并存（例如物料「Wave 110i · 红黑」下每台车再记实际颜色） |
| Cowork 商品 `Cowork Product` | `pearnly_cowork/doctype/cowork_product` | 另一产品的商品档（多语品名 / SKU / 单位，供识别与盘点），**不同类**：见第 3 节 |

| 选择框里原地新建 / 编辑主档 | `pearnly_core/public/desk/link.js`（`p.link_editors`，仓库 / 供应商 / 客户 / 物料已接） | 分类做成 Link 后照这个接法登记一个小弹窗 |
| 采购费用分类 `ERP Purchase Category` | 采购明细的费用类别（父子两级） | 不是物料分类，不复用 |

结论：特性/颜色扩展现有，一个字段 + 同一份字段定义与 `standard_fields`；分类按 4.1 建议新增一个小主档（Link），接法全用现有的。

## 2. Frappe 原生能力

- 原生 DocType 字段 `Data`（`length` 140），加进 `search_fields`，列表搜索与 Link 下拉原生就能搜到；迁移走原生 `bench migrate`
  （加列，存量为空，不写补丁）。
- 原生有 Item Variant / Item Attribute（ERPNext），本仓不装 ERPNext，也不需要变体矩阵——不用。
- 特性/颜色用 Data 自由文本、分类用 Link 的理由：特性是描述（「แดง ลายจุด」「红/黑 · 加厚」），组合多、不拿来分组；分类是反复选的一小组值、
  分区盘点要按它分组（4.1）。
- 界面用原生 Dialog / FieldGroup 的 Section / Column Break 排序；打印走现有 Print Format 取数（`printing/context.py`）。不自造控件。

## 3. 按类通用

- 类别：「物料主档的描述性字段」——凡是**显示物料是哪件货**（品名 / 编码 / 内部编码）的地方都属于这一类。
- **分类与特性/颜色同一处理**（主会话 11-01 补①）：两格一起覆盖下面全部入口，逐项见 6.1 的 27 行清单。覆盖口径分三档：
  - **录入与维护（必做）**：新建 / 编辑弹窗、二级批量新建页、盘点就地新建（Desk 借弹窗 + LINE 盘点页 `www/stocktake.html` 的 NEW_FIELDS）、
    物料导入模板与导入、物料列表两列、物料搜索（含收银台搜索）。
  - **认货显示（必做）**：物料选择下拉与撞码候选、出入库 / 调拨明细、盘点录入屏、盘点工作台与分区盘点、盘点表 / 期初表下载、
    盘点结果 Excel、盘点差异报表、库存一览与 CSV、库存卡。
  - **打印（建议做，12-1）**：入库 / 出库 / 采购收货单 / 销售 / 收银小票 / 盘点打印——印在品名下面一行（模板已有按台车号的副行样式），
    不加新列（岳父样板版面 1:1，加列会挤）。单据明细行**不快照**两格，打印时按物料现值取（同 `internal_code` 现在的取法）。
- 不覆盖及原因：
  - **Express 写入**：STMAS 69 列里没有对应栏（见第 4 节），不写；
  - **识别（采购 / 销售原件识别、LINE 单据）**：识别出的是供应商单据上的品名，按名称 / 编码认物料；特性不参与匹配（加进去会让同名不同色更难认，
    且改识别口径要过金标闸门）；认到后单据明细显示物料时自然带出；
  - **Cowork 商品**：Cowork 是会计事务所的录入工作台，商品档只为识别与 Express 对照服务，没有岳父这类门店物料维护需求；ADR-009 各产品
    自己声明，不强行同步。不加。
  - **CC 桌面版**：CC 只按 Express 主档读写，没有显示 ERP 物料资料的界面；载荷不变。

## 4. 上下游数据

- ERP 内：两格只在物料主档（分类另有自己的小主档），单据行（入库 / 出库 / 调拨 / 采购 / 销售 / 收银 / 盘点行）都只存物料链接与品名快照，不新增快照列；
  不进库存流水、收发存、成本、对账，不影响 Express 同步对齐。
- **Express 映射（已核实）**：读真账套 STMAS 表头（本机一份只读副本，282 件商品）共 69 列：
  `STKCOD STKDES STKDES2 STKTYP STKLEV STKGRP BARCOD STKCODS ACCCOD … SELLPR1–5 … PACKING REMARK DAT1 DAT2 NUM1 STR1–STR4 …`。
  没有颜色 / 特性栏。近似的几列都不合适：`STKDES2` 是第二品名（CC 建档写同一品名，存量 110 件在用）；`STKGRP` 是商品组（4 位码，
  279 件在用，要对 ISTAB 码表）；`REMARK` / `PACKING` / `STR1–4` 是备用列，存量里放的是乱码与日期，Express 界面上是什么含义没核实，
  往里写有覆盖客户自用内容的风险。
  **结论：只在 ERP 记，不写 Express，也不从 Express 读**。接了 Express 的公司，从 Express 同步来的物料特性为空、可在 ERP 填，
  同步（`express/masters.py` `PRODUCT_FIELDS`）不覆盖它（它不在同步字段里）；Express 锁（`express/locks.py`）不锁这一格。
- CC 桌面版：载荷不变，**不升载荷版本号**（不占用代录 16、多仓 15 之外的号）。
- Cowork / DMS：不涉及（见第 3 节）。

### 4.1 「分类」：现状核实与方案（主会话 11-01 补：分类同样全局出现、要能输中 / 泰 / 英文字和数字）

**现状（已核）**

- `ERP Product.category_id` 是 `Int`（`erp_product.json`）。来历：09-21 `1a9b644b`「照旧线上 `products` 表列对齐」搬来；旧项目
  （`pearnly-app` `docs/db/prod-schema.sql` 的 `products."category_id" bigint`、`services/sales/products.py`）也只是一格**没有外键的整数**，
  旧销售迁移稿注释「复用现有分类体系（迁回补 FK）」，从没补上——**两边都没有分类表，数字没有名称、没有含义，是谁填什么就是什么**。
- 谁写它：只有人手——物料新建 / 编辑弹窗（原生 Int 控件，输 `XL` 被清成 0）、二级批量新建页、物料 Excel 导入（`workbook.py:155` 按数字解析）、
  盘点就地新建（Desk 借弹窗、LINE 页 `inputmode=numeric`）、服务端 `int()`（`service.py:225`、`:429`，非数字直接报错）。
  **Express 同步不写它**（`express/masters.py` `PRODUCT_FIELDS` 与 `_products` 只写品名 / 单位 / 售价 / 条码 / Express 编码等）；没有任何从旧系统
  搬物料数据的迁移脚本写它；CC 读回的 Express 商品组 `STKGRP`（`companion/express_masters.py:136` 的 `group`）ERP 没接。
  所以存量值 = 各租户 09-21 以来人手填的数字（多半为空 / 0）；实验栈种子 `seed-stocktake-chart.py` 用 1–6。生产各租户有多少件填了，
  按「别 exec 进生产数据库」不在评估里查，迁移补丁执行时按租户记数到补丁日志。
- 读它的：多人分区盘点按「仓库 / 类别」分工（`stocktake.js:35–107`、`:240–292`，纯数字显示成「类别 3」）；盘点明细 `count.py:744–750`
  （`row.category = str(category_id)`）；LINE 盘点页新建卡片；物料列表 `LIST_FIELDS` 带出但**列表不显示、不能搜**；单据、打印、导入导出以外都没有。
- 已有的「分类」DocType `ERP Purchase Category` 是**采购明细的费用分类**（父子两级，进采购行 `category`），与物料分类不是一回事，不复用。

**二选一：建议 Link（自有「物料分类」DocType）**

| | A. Link → 新 DocType `ERP Product Category`（建议） | B. Data 自由文本 |
| --- | --- | --- |
| 输入 | 原生 `ControlLink`，打字服务端搜索（`search_link` + `get_query`，同仓库 `p.warehouse_query` 写法），下拉末尾「新建」原地开小弹窗（`p.link_editors`，同仓库 / 供应商），箭头原地改名 | 直接打字 |
| 一致性 | 选同一条，`XL` 不会变成 `xl` / `X L` / `XL `；改名一次所有物料跟着变 | 写法各异就分成几类，分区盘点、筛选、报表按「类」聚不起来 |
| 分区盘点 / 筛选 / 报表 | 按分类分组可靠，下拉列出的就是分类清单 | 只能按出现过的文字聚 |
| 工作量 | 多一个 DocType、一个小弹窗、一个查询函数、迁移补丁 | 改类型 + 补丁，最少 |
| 文案 | 新增「物料分类」类词条（弹窗标题用已有「分类」） | 无 |

理由：岳父把分类当尺寸用，尺寸是一小组反复用的值（S / M / L / XL、40 / 42、12 นิ้ว），正是「选一个」而不是「每次打」；分区盘点按类分工
要求同一个值写法一致。Frappe 原生就是 Link + 主档，`link.js` 的「新建 / 原地编辑」本仓已有现成接法，不自造控件。

**方案 A 细节**

- DocType `ERP Product Category`：`category_name`（Data 140，必填；中 / 泰 / 英 / 数字都行）、`is_active`（Check）。命名 hash，`title_field =
  category_name`、`show_title_field_in_link = 1`、`search_fields = category_name`。同名（按库排序规则不分大小写，泰文同 `products.taken` 口径问库）不许重复。
- 隔离：**租户（站点）内共用，不按公司分**——本地物料就是租户内各公司共用的（`express_company` 为空），分类跟着物料走；Express 没有对应
  （`STKGRP` 不接，见下），接 Express 的公司也用同一套分类。服务端：读要 `products.view` 或 `count.write`；新建 / 改名 / 停用要 `products.manage`；
  盘点岗（只有 `count.write`）在就地新建弹窗里**只能选已有分类、不出「新建」**，`create_product_here` 服务端核分类存在且在用。
- 删除 / 停用：被物料用着的分类不许删（原生 Link 引用检查），只能停用；停用的不进下拉，已挂的物料照常显示。
- 物料字段：新加 `category`（Link → `ERP Product Category`），`category_id` 从 DocType 移除（库里旧列原样留着不读，原生 sync 不删列；不跑 trim）。
- 迁移补丁（`patches.txt` 登记，原生 `bench migrate` 执行，幂等）：取每个租户 `category_id` 非空非 0 的不同值，各建一条分类、名称就是这个数字
  （`"3"`），再把物料的 `category` 指过去；补丁日志记建了几条、挂了几件。数字本来就没名称，用户以后在分类弹窗里改名（例如把 `3` 改成 `XL`），
  所有物料一起变。
- 读写口径统一改成 `category`：`standard_fields` / `update_product` / 导入（表头「分类」认分类名称，名称不存在：有 `products.manage` 的导入时
  顺手建、否则该行报错；老表头 `category_id` 列照认）/ 盘点就地新建白名单 / LINE 页（改成可选列表，不再数字键盘）/ `count.py:744`
  分区盘点的分组键改成分类 docname、显示分类名称（去掉「类别 N」数字拼接）。
- **不接 Express 商品组 `STKGRP`**：岳父把分类当尺寸用，`STKGRP` 是 Express 的商品组（如 `OIL`），语义不同；接上会把尺寸冲掉。以后要接另议。

若主会话选 B：`category_id` 改 Data（字段名与标签不变），原生迁移把列从 int 改成 varchar（数字原样成文字），补丁把存量 `0` 清空，三处 `int()` 改截断，
其余显示 / 搜索 / 导入导出覆盖范围与 A 相同。

## 5. 隔离与权限

- 字段跟着物料走，物料的租户（站点）/ 公司（`express_company`）隔离不变；读跟 `products.view`、改跟 `products.manage`，
  盘点就地新建跟 `count.write`（`create_product_here` 白名单加 `attribute`），全在服务端，不靠前端隐藏。
- 不跨公司、不跨分店：没有新的取数路径；打印取物料时沿用 `_products(names)` 只取本单的物料。

## 6. 入口：网页、手机、LINE、拍照

| 入口 | 现在 | 本次 |
| --- | --- | --- |
| 物料页 新建 / 编辑弹窗（电脑 / 窄屏） | `product_fields` | 加一格 + 排序（第 10 节） |
| 物料页 二级批量新建 | `grid_fields` | 行展开加一格 |
| 盘点录入 就地新建（Desk，手机走 LINE 富菜单进 Desk） | 借 `open_quick` | 自动带上，服务端白名单加一项 |
| LINE 手机盘点页 `/stocktake` | 自带 NEW_FIELDS（富菜单从未指向它，见 `2026-11-01-stocktake-new-product.md`） | 照弹窗同一顺序加一格，保持「同一套字段」口径 |
| 盘点录入那一屏（认出后的只读信息） | 名称 / 编码 / 单位 | 加只读「分类」「特性/颜色」（有值才出） |
| 拍照 / 相机 | 共用读码器 | 不涉及（不碰 `reader.js`） |
| CC 对话 / CC 桌面版 | 不显示 ERP 物料 | 不涉及 |

### 6.1 全部入口清单（分类与特性/颜色同一处理，主会话 11-01 补①）

下文「两格」= 分类 + 特性/颜色。显示口径：有单独列的地方各占一列；只有品名一格的地方，在品名后用 ` · ` 接（本仓已有写法，如 `scan.js:49`），
打印在品名下面一行小字。**单据行的品名快照（`description` / `product_name`）不拼两格**——快照进 Express `STKDES`、按品名认物料
（`masters/selection.py:103` `match_product`、`products/purchase.py`、`sales/entry.py:245`）都靠它，拼进去会改 Express 品名、认不回原物料。

| # | 入口 | 现在读写哪里 | 本次 | 必要性 |
| --- | --- | --- | --- | --- |
| 1 | 物料主档 DocType | `erp_product.json` | 加 `attribute`；分类改 Link（4.1）；`search_fields` 加两格 | 必做 |
| 2 | 建档 / 修改服务端 | `service.py:211` `standard_fields`、`:232` `create_product`、`:405-437` `update_product`（**按名列参数，不加会 TypeError 或被丢**） | 三处加两格 | 必做 |
| 3 | 盘点就地新建服务端 | `count.py:440` `allowed` 白名单（**不加会被静默丢掉**；Desk / LINE / 盘点表导入三条路都走这） | 加两格；分类核存在 | 必做 |
| 4 | 新建 / 编辑弹窗、盘点借用 | `products.js:83` `product_fields` | 加一格 + 排序（第 10 节）；分类改 Link 控件 | 必做 |
| 5 | 二级批量新建 | `products.js:493` `grid_fields`、`:580` `row_payload`（手工逐字段拼） | 两处都加 | 必做 |
| 6 | 物料列表 | `products.js:401` 列、`:390` 排序；服务端 `LIST_FIELDS :47`、`SORT_FIELDS :319` | 加两列（compact 宽度内）；可按列排序 | 必做 |
| 7 | 物料搜索 | `service.py:56` `SEARCH_FIELDS`（**收银台搜索同用**，`cashier/counter.py:55`） | 加 `attribute`；分类按名称搜（Link 要连表或先查分类名再 in） | 必做 |
| 8 | 物料导入 / 模板 | `products/workbook.py:21` FIELD_LABELS、`:40` 别名、`:111` TEXT_FIELDS；`service.py:561-621` | 加一列；分类列改读名称；四语表头 | 必做 |
| 9 | LINE 盘点页新建卡片 | `www/stocktake.html:358` NEW_FIELDS（手抄一份） | 同顺序加一格；分类改成从清单选 | 必做 |
| 10 | 盘点录入屏（认出后） | `stocktake-entry.js:29-34`、lookup 返回 | 只读显示两格（有值才出） | 必做 |
| 11 | 撞码候选、物料选择下拉 | `scan.js:49`、`entry-choice.js:20-21`、`masters/selection.py:14` FIELDS、`products/codes.py:96`、`service.py:358` `find_product` | 品名后接两格，区分同名不同色 / 尺寸 | 必做 |
| 12 | 出入库 / 调拨明细 | `transactions.js:348-361`、`:421-447`；`inventory/documents.py:241-246` | 明细显示品名后接两格（只读） | 必做 |
| 13 | 采购 / 销售明细 | `purchase-form.js:88`、`sales-form.js:73` 选中回填 `description` | **不改快照**；明细行物料格的下拉标签（#11）已带两格 | 只靠 #11 |
| 14 | 收银台 | `pos/pearnly-pos-adapter.js:299-321` 商品卡、`:789` 下单 description | 商品卡名称下显示两格；下单快照不变 | 建议 |
| 15 | 库存一览 / CSV | `inventory/stock.py:397`、`:433`；`inventory/contracts.py:51` 自己一套搜索；`inventory.js:128-146`；`inventory/api.py:246-300` | 加两列、可搜、CSV 两列 | 必做 |
| 16 | 库存卡 | `inventory/report.py:97-102`、`stock-card.js:183-316` | 品名后接两格 | 必做 |
| 17 | 盘点工作台、分区盘点 | `stocktake.js:35-107`、`:346-411`；`count.py:744-750` | 分类分组改 Link（4.1）；明细显示两格、本地搜索带两格 | 必做 |
| 18 | 盘点表下载 / 导入 | `inventory/workbook.py:20`、`count.py:604-648`、`inventory/api.py:682` | 下载多两列（只读参考，导回不改物料，同品名列口径） | 必做 |
| 19 | 盘点结果 Excel | `inventory/api.py:352-377` COUNT_COLUMNS / DETAIL | 加两列 | 必做 |
| 20 | 盘点差异报表 | `report/erp_stock_count_variance/*.py:31,92`、`.js:82,91` | 加两列（按物料现值取） | 必做 |
| 21 | 期初表下载 / 导入 | `inventory/opening_workbook.py:8`、`opening_batch.py:25,421`、`stock-opening.js` | 下载多两列（只读参考） | 必做 |
| 22 | 打印 6 种 | `printing/context.py:121-127` `_products`、`_stock :184`、`_purchase :211`、`_trade_rows :244`、`_sale :319`、`_count :386`；模板 `_parts.html:106`、`thermal.html:56` 已有品名下 `pp-sub` / `tp-sub` 副行（按台车号用） | context 每行加 `spec`（「分类 · 特性」），两处模板各加一行输出；不加列 | 建议（12-1） |
| 23 | 按台弹窗、按台导出 | `serials.js:50`、`serials.py:592-696` | 不改：那里的「颜色」是每台的 | 不做 |
| 24 | Express 读 / 写、CC | `express/masters.py:615`、`push.py:118`、`stock_entry.py:112`、`pos_day.py:213`、`express/locks.py:17` | 不改，不升载荷版本；两格不进 Express 锁（本地可改） | 不做 |
| 25 | 识别 | `purchasing/recognition.py`、`sales/recognition.py` 只抄品名 / 码 | 不改 | 不做 |
| 26 | Cowork 商品 / 盘点 | `pearnly_cowork/masters/products.py`、`stocktake/*`（ERP 文件的手工副本，不共用代码） | 不改（第 3 节） | 不做 |
| 27 | CC 前端 | `frontend/assistant` 不显示 ERP 物料 | 不改 | 不做 |

回归会受影响的：`tests/test_erp_portal_access.py:236-241`（每个模板标签键必须在 `messages.tsv`，要同时补 `translations/*.csv`）；
`scripts/flows/erp_stocktake_new_product.py:46-113`（STANDARD 字段集，加进去覆盖）、`erp_products_import.py:85`（回读字段）、
`erp_line_stocktake.py`、`tests/test_stock_count_workbook.py`、`tests/test_opening_workbook.py`（模板列）。
`tests/test-product-scan.cjs`、`tests/test-product-multi.cjs` 断言的字段集合本来就过期（还有 `default_warehouse`、没有 `internal_code`）且 CI 不跑——
本次顺手对齐到现状并加两格，或登记为已知过期，施工时定。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files products.js service.py selection.py printing/context.py count.py stocktake.html transactions.js`：
命中 `erp-transactions`（23 入口）、`erp-printing`（6）、`erp-masters-selection`（5）、`erp-products-lookup`（17）、`erp-count-code`（1）。
施工后按清单逐项实测记「通过 / 不通过 / 没测到（原因）」；自动冒烟的 `[auto]` 项跑 `--changed`，手工项挑与本字段相关的
（新建弹窗、盘点就地新建、LINE 盘点新建、各打印、选择框）实测。

## 8. 存储与成本

- 每件物料多一列 ≤140 字；无文件、无外部调用、无识别费用、无固定月费。

## 9. 删除、作废、追溯

- 特性：清空即清空，物料停用 / 删除照现有规则；两格都不写 Express，没有「已写进 Express 怎么办」。
- 分类：被物料引用的不能删（原生引用检查），只能停用；没被引用的可真删（轻便工具口径）。改名即所有物料跟着变。
- 单据不快照，改了物料特性后**重打旧单会印新值**（同品名以外的物料资料现在的口径）。如要冻结历史，得在每张明细加快照列，不建议（12-3）。

## 10. 文案与官方 UI

- 新增词条（四语进 `apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv`，写法照「条码/二维码」：中文不带空格，泰 / 英 / 日带空格）：

  | key | en | zh | th | ja |
  | --- | --- | --- | --- | --- |
  | `product.field.attribute` | Attribute / Color | 特性/颜色 | คุณลักษณะ / สี | 特性 / 色 |
  | `product.col.attribute` | 同上 | 同上 | 同上 | 同上 |

- 分类做成 Link 时：新 DocType 名 `ERP Product Category` 的界面名沿用已有「分类 / หมวดหมู่ / Category / カテゴリ」（进 `translations/*.csv`），
  弹窗只有「分类」「启用」两格，「新建」「编辑」用 `link.js` 已有文案，不新增句子。
- 不改已有文案（「分类」标签不改；要改成「分类/尺寸」之类属于改已有文案，先给用户看，见 12-2b）。`check-copy.py --report` 交付时附上。
- 排序（只动顺序与分栏，用原生 Section / Column Break）：现在左栏「是什么」= 名称、编码、来由、内部编码、条码/二维码、分类；右栏「怎么计量和卖」
  = 单位、库存单位、售价、参考成本、默认仓位、低库存。手机上左栏整列在前。改成：
  - 左栏：**条码/二维码、物料名称、分类、特性/颜色**、编码（自动生成）、内部编码、来由（详情才有）——手机上就是 条码 → 名称 → 分类 → 特性 → 其余；
  - 右栏不变。按 `check-form-layout.py --list` 的估算：现在左 5.4（详情 6.4）行、右 6.8 行；加一格后左 6.4（详情 7.4）、右 6.8，
    栏长差在 1.5 行内（施工时跑检查与 375 / 1280 截图确认）。
  - 二级批量新建页的 Grid 露出列不变（≤10 列限制），特性在行展开里。LINE 页 NEW_FIELDS 同一顺序。
- 光标：新建弹窗原来第一格是名称（自动聚焦）；改成条码在第一格后，盘点借用时条码已预填，焦点要落到名称（施工时确认原生 Dialog 的
  首格聚焦行为，必要时只对借用情况把焦点设到名称）。

## 11. 测试与验收

- 单元 / 离线：`standard_fields` 收 `attribute`（截断、空值）；`create_product` / `update_product` / 导入模板表头四语与别名；
  `create_product_here` 白名单；分类：中 / 泰 / 英 / 数字都能建、同名（不分大小写）被拒、停用后不进下拉、被引用不能删、
  盘点岗不能新建分类（服务端拒）、迁移补丁把存量数字变成分类且幂等（重跑不重复建）、存量 0 不建；`tests/test-product-*.cjs` 弹窗字段顺序；
  `check-form-layout.py`、`check-copy.py`、`check-architecture.py`、`check-unbounded-queries.py`。
- 实验栈流程：`erp.products.*`（新建 / 编辑、导入模板）、`erp.stocktake.new-product`（弹窗格数 13→14、顺序、存库）、
  `erp.line.stocktake`（NEW_FIELDS）、打印各格式出「特性」行、选择框按特性搜到；成功、空值、无 `products.manage` 的盘点岗仍能就地新建、
  无权限改物料被拒。
- 三星真机（LINE ERP-TEST 富菜单进 Desk）：盘点扫未知码 → 弹窗前四格顺序 → 填特性 → 保存 → 录入屏显示特性；物料页新建手机顺序。
- 用户 / 岳父要亲自看：手机上前四格顺序、特性在单据与打印上的位置。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 特性在单据打印上印不印、印在哪 | 印在品名下一行（小字，同按台车号），不加新列 | 不印：打印模板不动，工作量少约 1/4 |
| 2 | 「分类」做成 Link（物料分类主档，可搜可就地新建）还是 Data 自由文本（4.1） | Link，租户内共用；新建分类要物料修改权，盘点岗只能选 | 选 Data：少一个主档与弹窗，写法不一致时分区盘点按类聚不起来 |
| 2b | 「分类」标签要不要改成「分类/尺寸」 | 不改（改已有文案要先给用户看） | 改：走文案审批 |
| 3 | 历史单据重打是否要冻结当时的特性 | 不冻结（同其他物料资料） | 冻结要给 8 类明细加快照列，工作量翻倍 |
| 4 | 盘点录入加「编辑物料」入口（见 12.1） | 加：只给有物料修改权（`products.manage`）的人，开原生物料编辑弹窗 | 不加：录错名称要去物料页改 |

### 12.1 「盘点扫到新商品，为什么物料名称和编码不可以编辑？」

证据：主会话 11-01 17:18 在生产 `c9e28ace`（testerp，三星 SM-A146P 走 LINE ERP-TEST 富菜单 → Desk 盘点）实拍的截图
（共享笔记 `盘点标准新建弹窗-生产复测-11-01/`），与代码（`stocktake-entry.js:29–34`、`products.js:95–99`）。三处分开：

1. **新建弹窗里的「编码」（P-000009，灰色、下写「自动生成」）**：系统编码按 09-22 口径由系统发、不给手输（`next_code_preview` 注释），
   本来就只读。截图 2。**不是缺陷**。
2. **新建弹窗里的「物料名称」**：可填、必填（截图 2 光标在名称格，截图 3 已填 `QA1101-std-line`）。**名称不能填的缺陷不存在**。
   11-01 16:49 之前的旧版（4 格简化弹窗）同样名称可填，只是那格「编码」其实是扫到的条码、灰色只读。
3. **建好回到盘点录入那一屏的「物料名称」「编码」（灰色）**：截图 6。这一屏是记数，不改物料；盘点岗未必有物料修改权
   （`products.manage`），所以只读。岳父问的最可能是这一屏（建完发现名称打错，想当场改）。

建议：盘点录入那一屏，**有物料修改权的人**在名称下面多一个「编辑物料」按钮，点开物料页同一个编辑弹窗
（`p.link_editors["ERP Product"]({name})` → `open_detail`，服务端 `update_product` 仍要 `products.manage`），保存后刷新这一屏；
没有权限的人不出按钮。不需要新文案（按钮用已有词条 `product.action.edit`「编辑」）。

复现说明：上面三屏已有当天生产真机截图，代码自 16:49 后未变；再在实验栈与真机走一遍只会得到同样三屏，**分不出岳父看到的是哪一屏**——
要岳父当时的截图或时间点才能定。若主会话仍要求，我再走一遍实验栈 + 真机。

## 13. 工作量与排期（机器耗时）

拆成四步，依赖顺序 A → B → C → D，同一分支、同一次合入（中间提交各自过闸）：

| 步 | 内容 | 写代码 | 机器跑 |
| --- | --- | --- | --- |
| A | 分类主档：`ERP Product Category` DocType + 权限 + 查询函数 + `link_editors` 小弹窗；物料 `category` Link 与迁移补丁（存量数字 → 分类）；`attribute` 字段；6.1 #1–#3 服务端 | 约 1.5 小时 | 补丁在实验栈两套数据迁移 + 单测约 20 分钟 |
| B | 录入与维护：6.1 #4–#9（弹窗、批量页、列表、搜索、导入模板、LINE 页）+ 四语词条 | 约 1.5 小时 | 闸门约 15 分钟 |
| C | 认货显示与导出：6.1 #10–#21（含分区盘点改 Link 分组）；打印 #22（若批） | 约 2 小时 | 打印 PDF 与导出实测约 20 分钟 |
| D | 弹窗排序（物料页 / 盘点借用 / LINE 页同一顺序）+「编辑物料」入口（若批）；`check-form-layout` 与 375 / 1280 截图 | 约 45 分钟 | 流程 `--changed` 约 40 分钟 + 三星真机约 20 分钟 |

合计约 6 小时写代码 + 约 2 小时机器（实验栈迁移与流程、全闸门、CI）+ 等审定与真机占用。选 B 方案（分类 Data）可省约 1 小时。
