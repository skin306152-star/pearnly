# 物料「分类」改主档、加「特性/颜色」、新建物料手机顺序（岳父 11-01）

分支 `pearnly/product-attribute-field-e458d8`（基线 `origin/main` `20e7a9de`）。评估见 [2026-11-01-product-attribute-评估.md](2026-11-01-product-attribute-评估.md)，
主会话 11-01 按评估全部建议审定（用户拍板），并补：盘点里就地新建物料的人（`count.write`）也能在那个弹窗里新建分类、不能改名 / 删除；
录入屏编码标签按显示的是哪种编码换（用户同意改文案，见下「文案改动清单」）。

## 做了什么

| 入口 | 改动 |
|---|---|
| 物料分类主档 `ERP Product Category`（新） | `category_name`（必填、唯一）+ `is_active`；`products/categories.py` 建 / 改 / 取 / 按名认 / 盘点就地认（单号、名称都认，名称没有就建）；控制器在原生写入上同样判：新建要 `products.manage` 或 `count.write`，改名 / 停用 / 删只给 `products.manage`，不许改单号 |
| 物料 `ERP Product` | `category_id`（Int）换成 `category`（Link → 分类主档），加 `attribute`（Data 140）；`search_fields` 加 `attribute` |
| 迁移补丁 `v1_23_product_category_master` | 存量每个不同的数字建一条同名分类（0 不建），商品挂过去，旧列真删；幂等；打印建了几条、挂了几件 |
| 角色 | 菜单角色：物料、盘点可写，其余业务角色只读（`access/menu.py`、`setup.sync_roles` 的 dependencies）；`features.json` 同口径（物料、库存岗位可写，采购 / 销售 / 收银只读）；已有站点的管理员与老岗位角色在 `sync_roles` 补 |
| 建档 / 修改 / 导入（`products/service.py`、`products/workbook.py`） | `standard_fields` / `create_product` / `update_product` 收两格；列表带 `category_name`；搜索含特性和分类名称；导入模板「分类」写名称（老表的数字列照认，没有的名称有权就建）、「特性/颜色」紧跟分类；Excel 把 3 存成 3.0 按 3 认 |
| 物料页（`products.js`） | 新建 / 编辑弹窗左栏：条码/二维码 → 物料名称 → 分类（Link，下拉里原地新建 / 改名）→ 特性/颜色 → 编码 → 内部编码 → 来由，右栏不变；码已带进来时光标落在名称；列表加「分类」「特性/颜色」两列、特性可排序；二级批量页行展开加两格；`link_editors["ERP Product Category"]` 小弹窗 |
| 盘点就地新建（Desk 借物料弹窗、LINE 页、盘点表导入） | `create_product_here` 白名单收两格，分类认单号或名称、名称没有就建；LINE 页新建卡片同一顺序，分类一格带在用分类的候选、打字照收 |
| 盘点录入屏（`stocktake-entry.js`） | 认出后只读显示分类、特性/颜色（有值才出）；编码一格的标签：显示内部编码时叫「内部编码」、显示系统编码时叫「编码」；有物料修改权的人多一个「编辑」，开物料页编辑弹窗，保存后这一屏刷新 |
| 盘点工作台 / 分区盘点（`stocktake.js`、`count.list_items`） | 分区「分类」按分类名称分组（不再拼「类别 N」）；明细品名后接「分类 · 特性」，搜索带两格 |
| 盘点表 / 期初表下载、盘点结果 Excel、盘点差异报表 | 品名后多「分类」「特性/颜色」两列（只给人看，导回不改已有物料）；差异看板可「按分类」看 |
| 选择下拉、撞码候选（`selection.py`、`entry-choice.js`、`scan.js`、`codes.py`、LINE 候选） | 下拉标签与候选品名后接「分类 · 特性」 |
| 出入库 / 调拨明细（`documents.py`、`transactions.js`） | 明细品名后接「分类 · 特性」；编辑行物料格下面一行小字 |
| 库存一览 / CSV、库存卡 | 品名后接「分类 · 特性」，搜索带两格；CSV 多两列 |
| 打印 6 种（入库 / 出库 / 收货单 / 采购销售 / 小票 / 盘点） | 品名下面一行小字「分类 · 特性」（`_parts.html`、`thermal.html`），按物料现值取，不加列；小票纸长算上这一行 |

主会话 11-01 晚补两项（用户更正岳父本意 / 新增）：

| 入口 | 改动 |
|---|---|
| 盘点录入屏（Desk `stocktake-entry.js`、LINE `www/stocktake.html`） | 岳父那张单主要说的是**盘点扫码后录入屏显示的物料信息**：认出后只读按 **条码 → 名称 → 分类 → 特性/颜色 → 内部编码 / 编码** 显示，再接账面位置、单位、数量等。条码显示这件物料存的条码（没有条码时存的二维码内容）；物料没存条码时显示这次扫到的码（比如扫的是箱码），扫的本身就是系统 / 内部 / Express 编码时这一行不出（编码在下面那格）；扫到的码与物料条码不同（GS1 码、箱码）时按物料条码显示。条码、分类、特性有值才出 |
| 扫到网址码（TIS 认证码 `appdb.tisi.go.th/Q/i.php?d=…`） | 认码那一层统一拒：`products.codes.refuse_web_address`（判据 `products.contracts.web_address`：`http://` / `https://` 开头）在 `codes.candidates`（物料页查重、出入库 / 调拨 / 销售选择框扫码、收银台查码共用）和 `codes.assert_free`（所有建码、改码）里，单据选择框的扫码查（`selection.api_choices` 精确查）同样拒，提示「这不是商品条码」。盘点两条连续扫码路径不弹提示框：Desk 录入弹窗写一句 + 浮动提示、不出「新建物料」、相机接着扫并先跳过这个码（读码器已有的 `resume(code)`）；LINE 页说一句、不出新建卡片、扫码模式下重开相机并跳过这个码；盘点表导入里的网址行列进问题行。共用读码器 `reader.js` 没改。按台的车架号 / 序列号认台不受影响 |

不做（同评估）：Express 读写（STMAS 没有对应栏）与载荷版本；识别；Cowork；CC；按台每台的颜色；单据明细不快照两格。
收银台商品卡不加：官方商品卡只读品名，品名会进小票与 Express 快照，改它等于改快照；收银台搜索已能按特性、分类名称找到。

## 文案改动清单（用户 11-01 已确认，主会话转达）

| 位置 | 原来 | 现在 |
|---|---|---|
| 盘点录入屏，认出物料后编码那一格的标签 | 一律「编码」 | 显示的是内部编码（含 Express 编码）时用词条 `product.col.internalCode`「内部编码」；显示系统编码时仍是「编码」（`stock.col.code`） |

两个词条本身的文案都不变，只是录入屏按情况选用；登记在 `scripts/i18n/copy-approved.tsv`。

新增词条（四语）：`product.field.attribute` / `product.col.attribute`「特性/颜色」· Attribute / Color · คุณลักษณะ / สี · 特性 / 色；
服务端报错「该分类已存在」「请填写分类名称」「未找到分类」；「这不是商品条码」（This is not a product barcode · นี่ไม่ใช่บาร์โค้ดสินค้า · これは商品バーコードではありません）。

## 验证

（施工中，见下）
