# 盘点菜单做完整：施工记录

- 评估（用户 10-29 全部按建议批复）：[2026-10-29-stocktake-complete-评估.md](2026-10-29-stocktake-complete-评估.md)
- 分支：`pearnly/stocktake-complete`，基线 origin/main `8fd0fa4d`
- 顺序（主会话定）：S1 → S2 → S6 → S0 → S3 → S4 / S5，每步独立交付、验收后接下一步
- 实验室：本机 `pearnly-verify-lab`（生产镜像 fcb65cca 底座 + 本分支源码），站点 `erp-pos-a.localhost`，合成数据；
  两套数据：「新开户」每轮新建一家 7 仓零库存公司（照 polymer）、「老数据」两家有存量和历史采购单的公司（照 mrerp）。
  流程脚本与页面探针在本机临时目录（`stk/flows.py`、`stk/ui.sh`，无头 Chrome 带 `--mute-audio`、跑完退出）。

## S1 批次 / 有效期删除

**改动**
- 商品：新建弹窗、整页表格不再有「批次管理 / 有效期管理」；服务端建 / 改商品不再写这两项，列表 / 详情 / 单据物料选择不再返回；
  `api_create_product` 收到旧页面带来的这两项直接略过。
- 商品导入：模板与导出去掉两列；老表里「批次」「效期」列略过不认。
- 采购：明细不再有「批号 / 失效日期」，提交与识别结果都不再写；采购导出 Excel 不再出这两列；识别自动建商品不再按批号勾标志。
- 出入库：明细不再写、不再返回批号。
- 表结构：`ERP Product.track_batch / track_expiry`、`Purchase Line.batch_no / expiry_date`、`ERP Stock Entry Line.batch_no`
  **列保留**、设为隐藏只读（生产四个站点这些数据 0 条，删列不可逆，留列零成本）。
- 识别提示词**没改**（改提示词要过金标闸门）；官方 POS 的批次显示本来就由适配层固定为「无批次」，不动。

**验证（实验室，本分支源码）**

| 检查 | 结果 |
|---|---|
| 老数据：商品留着旧标志 → 详情 / 列表不再带，库里值不动，改商品不碰它 | 通过 |
| 新开户：旧页面带 track_batch 建商品 → 照建、不写标志 | 通过 |
| 新开户：采购行带批号 / 失效日期 → 单照建、两项不落库；入 3 号仓 +4 | 通过 |
| 新开户：入库行带批号 → 批号不落库，4 号仓 +2 | 通过 |
| 老数据：历史采购行留着批号 → 照常打开、库里不删 | 通过 |
| 导入表带「批次」「效期」列 → 略过、商品照建 | 通过 |
| 表结构：五列都在、隐藏只读 | 通过 |
| 页面：物料新建弹窗、物料新建整页无两项；采购新建、入库新建、出库新建明细无批号 | 通过 |

**连带回归**（`check-impact`：erp-masters-selection 5 个入口、erp-products-lookup 15 个入口，重叠合并）

| 入口 | 结果 |
|---|---|
| erp.products.find-by-code（按码找商品） | 通过（条码、内部编码各认回同一件） |
| erp.entry.choices（单据物料选择） | 通过（打字、扫码模式各认出；按品名认同一件） |
| erp.page.products / erp.products.new-dialog.scan（打开 + 弹窗） | 通过（页面与弹窗打开；相机读码本步未改相机，没重跑相机画面） |
| erp.products.new-page.scan（新建整页） | 通过（页面打开、无批次列） |
| erp.page.purchase / erp.purchase.item-scan | 通过（列表、新建页打开，明细无批号） |
| erp.stock-in.item-scan / erp.stock-out.item-scan | 通过（新建页打开，明细无批号；入库过账 +2） |
| erp.cashier.search / scanner-gun / camera | 页面打开（未开班状态）；收银走 `find_product`，按码找商品已通过；没开班实卖（本步不改收银） |
| erp.inventory.opening.item-scan | 库存页打开；期初不读批次（没单独录期初） |
| erp.exports（采购导出） | 通过（导出无批号列）；其余导出不涉及批次 |
| erp.products.import-upload | 通过（解析层：带旧列照建）；没走网页上传框 |
| erp.detail.purchase.draft / submitted / void | 老数据历史单（含旧批号）打开通过；作废单没单独开 |
| erp.cc.write / erp.cc.stock-entry / erp.cc.stock-void / erp.cc.masters | 没测到：实验室无 Express；写 Express 的载荷不含批号 / 失效日期（代码核对 `express/` 无这两项），本步不改载荷 |
| erp.line.intake.submit / erp.ocr / erp.execution / erp.purchase.original-upload / erp.sales.original-upload / erp.tax-documents | 没测到：付费识别不进回归；识别结果落采购明细时这两项被略过，已由采购提交检查覆盖 |
| erp.serials.dialog / erp.place.header / erp.products.new-dialog.opening / erp.products.image-upload / erp.products.detail.barcode-scan / erp.masters.scope / erp.print.purchase / erp.browser-text | 本步不改这些代码路径；随后续步骤（S0 按台、S6 打印、S5 仓库）实测 |

实验室已知噪声：首页取计费信息 `billing_www.home` 返回 417（实验室没接计费服务），与本改动无关。

## S2 电脑盘点录入框显示编码

**改动**：盘点录入框（Desk，电脑与手机窄屏同一个）在「物料名称」下面加只读「编码」，值取服务端 `lookup_count` 已返回的
`product.code`（`count_code`：内部编码，没有就系统编码），与 LINE 手机页同一个。只改前端一处，服务端不动；栏名复用已有词条「编码」。

**验证**

| 检查 | 结果 |
|---|---|
| 新开户：期初主仓本地 10、二号仓 A1 Express 商品 5 | 通过 |
| 服务端：扫内部编码 EX-0001 → 回 EX-0001；扫本地条码 → 回系统编码 P-000004 | 通过 |
| 二号仓盘点 A1 记 7（账上 5）→ 结束 → 过账，A1 = 7 | 通过 |
| 页面：盘点详情「开始盘点」→ 录入框输码查找 → 「编码」栏分别显示 EX-0001、P-000004，品名对应 | 通过 |

连带：`stocktake-entry.js` 登记在 erp-count-code（盘点按码认商品）——认码逻辑没改，上面两种码都认回同一件；相机读码本步未改。
无头浏览器里扫码提示音被浏览器拦（没有真人点击），属测试环境现象。

## S6 收发货页与打印显示内部编码

**改动**（范围按批复 #8 只做收发货，#9 打印印内部编码）
- 入库 / 出库新建与草稿明细：物料下面加只读「内部编码」（选商品时带出）；已提交 / 已作废单的明细表物料列显示「品名 · 内部编码」。
  服务端 `documents.get_entry` 明细带上 `product_name`、`internal_code`（只取这张单的商品）。提交时内部编码不回传（`p.trade.clean` 只认原来那几项）。
- 收货单（入库、入了库的采购单）、领料单（出库）编码列：内部编码 → Express 编码 → 系统编码（`printing/contracts.goods_code`，纯规则带单测）。
  采购 / 销售单、报价单、收银小票、盘点表不改。

**验证**

| 检查 | 结果 |
|---|---|
| 新开户：5 号仓入库 Express 商品 3 + 本地 1 → 详情明细内部编码 EX-0001 / 空，品名对应；库存 +3 | 通过 |
| 收货单取数编码列 = [EX-0001, 系统编码]；领料单 = [EX-0001]；出库后库存 -1 | 通过 |
| 入了库的采购单收货单编码列 = EX-0001 | 通过 |
| 真打印（`frappe.get_print`）收货单、领料单 HTML 含 EX-0001、不再印旧 Express 码 EXP-0001 | 通过 |
| 页面：入库新建行扫 EX-0001 → 「内部编码」= EX-0001；已提交入库单明细显示「验证Express商品 · EX-0001」 | 通过 |
| `tests/test_erp_printing.py`（本机裸 Python） | 通过（版面渲染部分要 jinja2，合入前在候选离线检查里跑） |

**连带回归**（erp-transactions 21 个入口、erp-printing 6 个入口）

| 入口 | 结果 |
|---|---|
| erp.page.stock-in / stock-out、erp.detail.stock-in.submitted、erp.stock-in.item-scan / stock-out.item-scan | 通过（新建、扫码填物料、已提交详情） |
| erp.print.stock-entry、erp.print.purchase | 通过（上表） |
| erp.page.purchase、erp.detail.purchase.*、erp.page.sales、erp.detail.sales.*、erp.page.pos-receipts、erp.print.sales / pos-receipt / pos-shift / stock-count | 不受影响：只读明细表的内部编码只在 `stock` 表显示（采购 / 销售 / 小票不传），编码规则只换了收发货两处；采购列表与新建页 S1 已开 |
| erp.page.inventory / stock-card / stock-count / pos-shifts | 本步不改这些页的代码；库存页 S1 已开、盘点页 S2 已开 |
| erp.place.header、erp.products.new-dialog.opening、erp.inventory.opening.row-warehouse、erp.warehouses.express-new-hidden、erp.scan.pick-product | 本步不改 `p.place`、期初、仓库页、撞号选择；入库新建页单头仓预填照常（页面检查里新建行仓库镜像正常） |
| erp.cc.stock-entry / erp.cc.stock-void | 没测到：实验室无 Express；写 Express 的载荷不读 `get_entry` 的新字段 |
| erp.products.find-by-code | 通过（扫码填物料走它） |

实验室注意：改了 Python 后要重启网页容器（gunicorn 不自动重载），否则页面拿到的是旧接口结果。
