# 商品内部编码（不接 Express 的部分）

基线 main `d8d42e6f`，分支 `pearnly/internal-code`。评估：`docs/work/2026-10-25-stock-attachments-vehicle-评估.md` 第二部分（内部编码）；
主会话 10-26 审定：系统编码不改，另加「内部编码」；接了 Express 的公司内部编码 = Express 商品编码（只读）；扫码、搜索、盘点、两个 Excel 模板都认；
盘点「编码」列显示内部编码；先定 20 位。CC 第一次写 Express 时用内部编码建 STKCOD（冲突转人工）属于 Express 层，等服务器测试账套实录再做。

## 做了什么

- `ERP Product` 加 `internal_code`（Data 20 位、唯一索引）。只有 ERP 本地建的商品手输；从 Express 同步来的商品不存这一格，
  「内部编码」就读它的 Express 编码（`products.contracts.internal_code`）。这样同步、旧数据都不用回填，同编码的旧重复档也不会撞唯一索引。
- 唯一：本地商品各家公司都看得到，所以全租户不能重，也不能与哪家公司从 Express 同步来的商品编码撞（扫码时两件都认得到就分不清）；
  报「该内部编码已存在」。
- Express 来的商品：内部编码一格只读显示 Express 编码；`express.locks` 把 `internal_code` 加进商品锁定栏位，服务端拦；页面原样带回来的不算改。
- 认商品同一套列（`products.contracts.LOOKUP_FIELDS`：系统编码 → 内部编码 → Express 编码 → 条码 → 二维码）：商品页扫码查重、收银台搜索 / 扫码
  （`products.find_product`）、盘点扫码 / 录入 / 导入 / LINE 手机盘点（`inventory.count.product_of_code`，仍按盘点仓库所属公司认）、
  主档选择框（`masters.selection`）、商品列表搜索、库存一览搜索都认内部编码与 Express 编码。
- 盘点：新开盘点的行、盘点 Excel 模板（现拉的与按快照的）、录入弹窗的「编码」显示内部编码，没有就用系统编码（`count_code`）；
  已开的盘点行是开单时抄的，不改。表头仍是「编码」，没改已有文案。
- 两个 Excel 模板：商品导入模板加「内部编码」列（四种表头写法都认）；盘点模板「编码」列填内部编码，导回来照常认。
  期初导入的「编码」列也认内部编码（系统编码对上的优先）。
- 不改 CC 载荷、不写 Express、不扣费。

## 与评估的出入

- 评估第 5 节写的是「内部编码按公司唯一（`公司|内部编码` 唯一索引）」。商品档没有公司栏，本地商品各家公司共用，所以落成：
  本地商品全租户唯一（唯一索引就在 `internal_code` 上），Express 商品不存这一格、用各自公司的 Express 编码，效果等同「在每家公司里唯一」。

## 用途 / 入口 / 配置

| 入口 | 去向 | 需要 |
| --- | --- | --- |
| 商品 → 新建 / 打开 → 「内部编码」 | 本地商品手输；Express 商品只读显示 Express 编码 | `products.manage` |
| 商品列表 → 「内部编码」列、搜索框 | 显示 / 搜到 | `products.view` |
| 收银台搜索、扫码枪、相机 | 认内部编码 | 收银岗位 |
| 盘点录入 / 扫码 / 上传导入 / LINE 手机盘点 | 认内部编码；「编码」显示内部编码 | `count.write` |
| 商品 → Excel 导入模板 | 多一列「内部编码」 | `products.manage` |

## 新增文案（无改已有文案）

| 键 | 中文 | English | ไทย | 日本語 |
| --- | --- | --- | --- | --- |
| product.field.internalCode / product.col.internalCode | 内部编码 | Internal code | รหัสภายใน | 社内コード |
| （msgid） | 该内部编码已存在 | This internal code already exists | รหัสภายในนี้มีอยู่แล้ว | この社内コードは既に存在します |

盘点「编码」列：表头文字不改，内容改为显示内部编码（没有内部编码的显示系统编码）。

## 验证

- 单元：`tests/test_erp_internal_code.py`（内部编码 / 盘点编码取值、认商品列顺序、导入模板列与四种表头）；
  `tests/test_erp_express_locks.py` 补 Express 商品不能另填内部编码、本地商品可改；`tests/test_opening_batch.py` 补期初编码列认内部编码；
  `tests/test_erp_codes_per_company.py`（生产镜像跑）补盘点扫码认内部编码与本公司 Express 编码、别家公司的不认、手输内部编码撞内部编码 / Express 编码被拒。
- 实验栈逐入口：待做（见下）。

## 待做 / 等实录

- Express 层（等服务器测试账套实录）：本地商品第一次由 CC 写进 Express 时用内部编码当 STKCOD、冲突转人工；随之升载荷版本、CC 回归。
