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

## S0 公司类型与接 Express（前置）

**改动**
- `ERP Company` 加两栏：「公司类型」（普通 / 汽车 / 手机电器，默认普通）、「接 Express」（默认不勾）。补丁 `v1_17_company_settings`：
  记着 Express 账套（`express_book`）或 CC 桌面版报过它账套的公司自动勾「接 Express」，类型一律普通；按账套自动建的公司建时就勾上。
- 接 Express 的统一判断 `express.push.linked` = 勾了「接 Express」且已连上（`push.connected`：CC 桌面版报过这家公司税号 + 分店的账套）。
  推送、主档同步、收银日汇总、采购 / 销售自动建档归属、仓库新建限制、期初限制、打印里的 Express 判断都走它，不勾就是普通 ERP。
  页面上的 Express 列 / 按钮（启动数据 `pearnly_express`）改成**当前公司**接没接，不再是整个站点有没有账套。
- 已连上的公司不能直接取消（服务端拒「请先断开 Express」），公司页给「断开」（`api_disconnect_express`：只把这家公司的「接 Express」关掉，
  已写进 Express 的单和本地数据都不动）；勾了未连上只显示「CC 桌面版未连接」和「下载 CC 桌面版」。
- 按台管理只对汽车 / 手机电器类型的公司生效（`companies.unit_tracking`）：服务端 `serials.tracked(products, company)`（入出库 / 采购 / 销售 /
  收银登记与选台、收银退货退台、库存调整拦截、盘点差异报表「没盘到的台」）、盘点认码与逐台要求（`count.per_unit`）、期初拦截都按公司；
  页面上新建 / 编辑物料的「按台管理」两格、「按台记录」按钮、提交前选台弹窗（`p.serials.collect`）都按当前公司类型出现。
  普通公司下商品的按台标志与已登记的台**都不动**，改回汽车照旧生效。
- 新文案 7 条（公司类型、普通、汽车、手机电器、接 Express、断开、请先断开 Express），复用已有「CC 桌面版未连接」「下载 CC 桌面版」。

**验证**

| 检查 | 结果 |
|---|---|
| 老数据：迁移后记着账套的乙公司「接 Express」= 勾、类型普通；甲公司不勾 | 通过 |
| 新开户：默认普通、不接；按台商品在普通公司按数量入 6 号仓 2 件（不要台），商品标志与台数不变；启动数据当前公司类型普通、未接 | 通过 |
| 新开户：普通公司盘点按台商品不要求逐台、记 2 件，过账后 6 号仓 = 2 | 通过 |
| 改成汽车：同一商品入库不给台被拒、盘点要逐台；改回普通照常入库，台数不变 | 通过 |
| 勾「接 Express」未连上：资料页 `express_connected=false`、无同步；采购单不排 Express | 通过 |
| 模拟 CC 报上账套：连上、启动数据接了；采购单排队写 Express；此时取消「接 Express」被拒；断开后不再排队 | 通过 |
| 页面：普通 / 不接 → 公司页有「公司类型」= 普通、「接 Express」，无断开 / 下载；新建物料无按台两格 | 通过 |
| 页面：接了未连上 → 公司页「CC 桌面版未连接」+「下载 CC 桌面版」，无断开 | 通过 |
| 页面：汽车（未接）→ 新建物料有「按台管理」「号码种类」；汽车且已连上 → 公司页有「断开」「同步 Express」，无下载 | 通过 |
| `tests/test_erp_boot_items.py`（加当前公司口径一条）、`tests/test_erp_serials.py` | 通过 |

**连带回归**（erp-transactions 21、erp-company-menu 4、erp-count-code 1 及 Express / 按台入口）

| 入口 | 结果 |
|---|---|
| erp.page.company、erp.company-menu | 通过（公司资料三种状态；保存后 `refresh_company_context` 同时刷新当前公司接没接 Express） |
| erp.page.products、erp.products.new-dialog.* | 通过（按台两格按公司类型出现 / 不出现） |
| erp.serials.dialog | 通过（服务端：汽车公司不给台被拒、普通公司不要台）；页面弹窗前端同一判断，未逐个单据点弹窗 |
| erp.stock-count.code、erp.report.stock-count-variance | 通过（普通公司不按台认码；报表按盘点公司判断） |
| erp.page.stock-in / stock-out、erp.detail.* | S6 已测；本步只加了按台按公司判断 |
| erp.cc.write / erp.cc.connect / erp.cc.pos-day / erp.cc.stock-void | 服务端「排不排 Express」三种状态通过；实验室无 CC，真写 Express 没测到 |
| erp.warehouses.express-new-hidden、erp.inventory.opening.row-warehouse、erp.place.header | 走同一个 `linked`；未接的公司不再被当成 Express 公司（生产已连的公司由补丁自动勾上，行为不变） |
| 其余（导出、附件、扫码、导入、团队页、库存汇总排序等） | 本步不改这些代码路径 |

**开户自动建公司带仓**（主会话 10-29 同意随 S0 修；起因生产 gize 1 家公司 0 个仓）
- 原因：开户时 `pearnly_tenant_ready` → `masters.company.ensure_default` 只建空公司不建仓；只有「新建公司」表单和按 Express 账套建公司才保证至少一个仓。
- 修法：`ensure_default` 新建公司后调 `ensure_warehouse`，走表单同一段 `_create_warehouses`、同一个默认仓名词条 `company.warehouseDefaultName`（按站点语言）；
  勾了「接 Express」或已连上 Express 的公司不建（仓以 Express 同步为准）。补丁 `v1_18_default_company_warehouse` 只给「0 仓、未接也未连 Express」的公司补，
  幂等、打印补了哪家。全仓查过建 `ERP Company` 的路径只有三处（新建公司表单、开户默认公司、按 Express 账套建公司），都已带仓；
  `tests/test_erp_default_company_warehouse.py`（候选车道，Frappe 装好、不连库）守住：表单默认仓名、已有仓 / 接了 / 连上 Express 不建、幂等、
  **每一处建公司的代码都要经过建仓**（以后新加建公司的路径没建仓就失败）。
- 部署时：补丁前后在生产只读回读每个站点「0 仓公司」清单（预期只有 gize，补后为空），写进发布记录。实验室复测待起栈（当时本机已有两套实验栈在跑）。

**对生产的影响**：生产已连 Express 的公司（testerp 62 家记着账套）由补丁自动勾上，推送 / 同步不变；没连的公司本来就不推送。
之后手工新建、但 CC 桌面版报过账套的公司，要在公司页勾「接 Express」才开始推送（按批复：不勾就是普通 ERP）。

## S3 盘点就地新建商品

**改动**（照多仓评估 13.2-6 与批复 #7）
- 新入口 `count.create_product_here`（Desk `api_stock_count_create_product`、LINE `api_line_stocktake_create_product`）：有盘点录入权（`count.write`）
  就能建；盘点锁里先按码再认一次，同码已有就回那件（并发两人只建一件，品名以先建为准），撞码几件报错让人选；扫到的码作条码 / 二维码内容，
  编码系统发；接了 Express 的公司建成这家公司自己的档。
- 商品「来由」三栏（`origin` / `origin_doctype` / `origin_name`）按类写全：手建、导入、Express 同步、采购自动建、销售自动建、盘点新建 · 盘点单号；
  物料详情显示只读「来由」。谁 / 何时用原生建档人与时间。
- 盘点行「来源」（`row_origin`，空 = 账上快照）：补进来的已有商品 / 新发现 / 停用启用。
- 停用商品的码（多仓评估问 8）：盘点里照样认出记数，不建重复码；过账时实盘 > 0 的自动重新启用，商品时间线记「盘点启用 · 盘点单号」。
- Excel 导入：带品名的未知码行当场建并记数；没品名的照旧列问题行。
- 接 Express 的公司：盘点调整里有还没 Express 编码的商品，写 TK 前停下交人（新原因「Express 中没有这件商品」，不每分钟重试）；CC 在 TK 里建档另排。
- Desk 录入框：服务端回「没有这个码」时出现「新建物料」，填品名、单位（可选参考成本）提交后接着记数。
- LINE 手机页：认不到时给品名、单位和「新建物料」；记数多一格「仓位」（带这份盘点仓的库位清单，G11），可改记在哪个库位。
- LINE 页查码 GET 不提交、快照外商品记不进去的问题：实验室里同样撞到，main 上 C 盘窗口已修（`15ffa12d`：查码只收 POST，加 GET 写库扫描器）；本分支变基后统一用它的做法，我原先那份补提交的修法已删。
- 新入口登记进连带回归表：`erp.stock-count.new-product`、`erp.line.stocktake.new-product`；`desk-link` 对 LINE 页的误命中登记为非入口（写明原因）。

**验证**（实验室，新开户每轮新建公司）

| 检查 | 结果 |
|---|---|
| 二号仓盘点扫系统没有的码 → 认不到；当场建「盘点新货Y」→ 来由盘点新建 · 盘点单号、码作条码、编码 P-、不属 Express | 通过 |
| 另一个人同码再建（品名不同）→ 回同一件、库里只一件、品名不变 | 通过 |
| Desk A1 记 3 + LINE 来源记 2 → 同一行两条记录、实盘 5、行来源「新发现」 | 通过 |
| 导入：带品名的未知码建档并记 4（A2）；没品名的未知码列问题行 | 通过 |
| 停用商品的码 → 认出（停用标记）、记 1、行来源「停用启用」 | 通过 |
| 过账：Y@A1 = 5、Z@A2 = 4、W@A1 = 1；W 自动启用，时间线有「盘点启用 · 盘点单号」 | 通过 |
| 无权限账号建不了 | 通过 |
| 接 Express 的公司：盘点新建的档属这家公司；过账后盘点调整停在「Express 中没有这件商品」 | 通过 |
| 来由：手建 = manual、采购自动建 = purchase；老数据旧商品来由空、照常打开 | 通过 |
| 页面（Desk）：录入框输系统没有的码 → 出现「新建物料」→ 填写提交 → 录入框认出新商品、编码栏 P-000017 | 通过 |
| 页面（LINE 手机页，合成绑定走已登录回落）：输码 → 新建表单 → 建好认出 → 仓位清单 A1 / A2 → 改 A2 记 2 → 服务端一条 2 件、A2、来源 LINE | 通过（当时带着我的临时修法；变基到 POST 做法后要重测） |

**连带回归**

| 入口 | 结果 |
|---|---|
| erp.stock-count.new-product、erp.line.stocktake.new-product、erp.stock-count.code | 通过（上表） |
| erp.stock-count.upload | 通过（导入建档 / 问题行） |
| erp.products.*（物料页） | 来由只读一格只在有来由时出现；保存时不回传；新建弹窗不变（S0 页面检查已开） |
| erp.cc.masters | Express 同步建档写来由 express（只在新建分支加一项）；实验室无 Express 没跑真同步 |
| erp.cc.stock-entry（盘点 TK） | 有新商品时停人工通过；其余 TK 载荷不变；真写 Express 没测到 |
| erp.line.intake.submit、采购 / 销售自动建档 | 来由 purchase / sales（采购已测）；LINE 收单付费识别不进回归 |

## S4 进行中报表 / 打印 / 导出跟筛选

**改动**（批复 #1 两种版式、#2 做成筛选）
- 盘点行筛选成一条纯规则 `inventory.contracts.count_rows`（只看全部 / 已盘 / 未盘 / 有差异、仓库、库位），差异报表、打印、导出共用。
- 差异报表：加「仓库」（只列当前公司的仓）「仓位」两个筛选、「来源」列（补盘 / 新发现 / 停用启用）；进行中照常能看。
  不带盘点号时给**当前公司**最新的一份（原来不分公司，多仓评估 13.4 记过）。
- 打印：盘点详情的「打印」先弹筛选窗（版式、只看、仓库、仓位），再「打印」/「PDF」/「导出」。进行中默认点数表（盲盘，不印账面）；
  有库存管理权（`stock.manage`）的可选核对表（账面 / 实点 / 差异，新标题「ใบตรวจสอบการนับ / STOCK COUNT CHECK」），服务端 `count_mode` 同样核权限；
  结束 / 过账后照旧结果表。筛选经打印地址参数传给打印取数（`printing.context._count` 读 `form_dict`），`p.trade.print` 多一个参数入口。
- 导出 Excel：跟同一份筛选；进行中默认盲盘（不出账面、差异、差异金额），选核对表且有库存管理权才出；明细只出筛出的那些行的记录。
- 新文案：点数表、核对表、来源、补盘、新发现、停用启用；复用 Frappe 自带「打印格式」「PDF」「打印」。

**验证**

| 检查 | 结果 |
|---|---|
| 新开户：二号仓盘点进行中 —— A1 本地商品记 7（账上 5）、A2 Express 商品记 3（无差异）、新发现一件记 2、按台车辆未盘 | 通过 |
| 差异报表（进行中）：有差异 + A1 → 本地商品、新发现两行；别的仓 → 空；未盘 → 只按台车辆；来源列「新发现」 | 通过 |
| 不带盘点号 → 当前公司最新一份；切到老数据公司给的不是这一份 | 通过 |
| 打印取数：默认点数表不带账面；核对表 + 有差异 + A1 → 两行、带账面、标题 count_check；真打印 HTML 有「STOCK COUNT CHECK」，默认打印无「Book Qty」 | 通过 |
| 导出：默认盲盘无账面 / 差异列；核对表有；未盘 → 1 行（全部 4 行） | 通过 |
| 老数据：已过账的盘点出结果表、报表「有差异」可用 | 通过 |
| `tests/test_erp_printing.py`（加核对表权限与筛选规则两条） | 通过 |
| 页面：打印弹窗有版式 / 只看 / 仓位和「PDF / 导出 / 打印」；选核对表 + 有差异 + A1 → 打印地址带参数，打印页是核对表、只这两行、筛掉的行不在 | 通过 |
| 页面：差异报表有仓库 / 仓位筛选，选有差异 + A1 → 两行，来源「新发现」 | 通过 |
| PDF 下载 | **没测到**：实验室容器里 wkhtmltopdf 拉站点资源被拒（现有收货单 PDF 同样失败，环境问题）；地址已带筛选参数，取数与打印页同一条路，留测试环境验 |

**连带回归**：erp-printing（`p.trade.print` 多一个可选参数，其余单据调用不变；只改了盘点打印取数与版面）；erp.print.stock-count、
erp.report.stock-count-variance、erp.exports（盘点导出）上表已测；其它单据打印 S6 已测。

## S5 仓库编号与库位说明

**改动**（批复 #3 #4）
- `ERP Warehouse` 加「仓库编号」（`warehouse_code`，30 位）：接了 Express 的仓编号就是 Express 仓库码（只读，改了不生效）；其余手填、同公司不重复、可空，
  空时页面显示系统号。`warehouses.code_of / label` 统一「编号 · 名称」写法。仓库选择框按编号也能搜（原生 `search_fields`，下拉里名称下面带编号）。
- 仓库页：列表多一列「仓库编号」，新建 / 编辑弹窗有编号一格（Express 的仓只读）；库位清单多一列「说明」。
- `ERP Warehouse Location` 加「说明」：库位选择框显示「A1 · 说明」（值仍是库位名），LINE 手机页的仓位清单同样；盘点打印的库位格带说明。
- 收货单 / 领料单 / 盘点打印的仓库行、差异报表的仓库列印「编号 · 名称」，没编号只写名称。
- 已有文案不改：原「仓库」列名照旧，编号是新加的一列（新文案「仓库编号」「说明」「仓库编号已存在」）。

**验证**

| 检查 | 结果 |
|---|---|
| 新开户：七个仓填 W01–W07；同公司重复编号被拒；新仓不填编号可存；标签「W02 · 仓2」 | 通过 |
| 二号仓 A1 说明「前排货架」→ 库位选项 `{value: A1, label: A1 · 前排货架}` | 通过 |
| 收货单仓库行「W03 · 仓3」；盘点打印库位格「A1 · 前排货架」、仓库行「W02 · 仓2」；差异报表仓库列「W02 · 仓2」 | 通过 |
| 老数据：接了 Express 的仓编号 = Express 仓库码、改编号不生效；没编号的旧仓标签只写名称 | 通过 |
| 页面：仓库页有「仓库编号」列与 W02；编辑弹窗编号 W02、库位表有说明列且 A1 = 前排货架；仓库选择框搜 W02 搜得到 | 通过 |

**连带回归**：erp-transactions（差异报表登记为本共用件入口：它用了选仓函数）、desk-link（仓库选择框多了按编号搜，选法不变）、erp-printing（仓库行写法）。

## 总验证与交付状态

- **整轮回归**：六步的流程脚本合在一轮从头跑（新开户每轮新建公司 + 老数据两家公司），42 条全部通过；无头 Chrome 页面检查 S1–S6 共 17 项通过
  （商品 / 采购 / 入库 / 出库新建页、盘点录入框编码与就地新建、LINE 手机页新建与改库位、公司页三种 Express 状态、汽车 / 普通公司的按台格子、
  打印弹窗与核对表、差异报表筛选、仓库页编号与库位说明）。
- **没测到（要测试环境 / 真机）**：盘点打印 PDF 下载（实验室 wkhtmltopdf 拉资源被拒，现有 PDF 同样）；真写 Express（CC 桌面版写 TK / 推送）；
  LINE 真机（这次用合成绑定走已登录回落，LIFF 验签路径没走）；收银台按台选台弹窗在汽车公司实卖（服务端已测、前端同一判断）。
- **对生产的影响**：一次迁移带 5 张表的新字段与补丁 `v1_17_company_settings`（已连 Express 的公司自动勾「接 Express」）；批次 / 效期列保留不删；
  无新外部调用、无固定月费。迁移钩子修复（`pearnly/migrate-hooks-unprovisioned`）要先合入，否则半开户站点迁移会被任务看板钩子挡住。
- **实验室**：`pearnly-verify-lab` 换挂本分支源码做验证，验证完已停（数据卷保留，合成数据）；合入后再清理。

## 合入前补的几件（10-29 晚）

- **手机盘点页按台与电脑一致**（验收窗口发现）：汽车 / 手机电器类型的公司里，LINE 页扫按台商品的商品码会出数量框、保存时才被拒，
  查码时还会给它补登一行快照（抬头 y 多 1）。改成同电脑：认出是按台商品就提示逐台扫车架号 / 序列号，不出数量框、不补登行；扫到具体某一台再按台记。
  普通公司按 S0 不受影响。
- **按台登记扫码缺陷**单独提交；全仓 20 处「三元否则分支里不加括号的 `||`」存量挪到本地分支 `pearnly/mixed-operators-parens`
  （34a7c5b9，未推送：那些模块还没有流程用例，推上去覆盖闸会红）。检查脚本 `scripts/check-mixed-ternary.py` 带基线
  `scripts/mixed-ternary-baseline.tsv`（文件 → 处数，只许减少）：以后各模块补流程用例时从那个分支逐个拿过来，改一处删一行。
  保留原因：等各模块的流程用例；复核时点：每补一个模块的流程。
- **流程用例**（`tests/flows/registry.toml`，主会话 10-28 的覆盖闸）：新增 7 条——盘点就地新建、LINE 手机盘点（就地新建、选库位、按台要逐台扫且抬头 y 不变）、
  入库按台登记（留空保存要提示台数不符）、物料新建（无批次 / 有效期 / 按台格、来由、按码认出）、公司设定（接 Express 三种状态、改类型启动数据跟着变）、
  仓库编号与库位说明、采购单（无批号 / 失效日期、明细输条码回车按码认出、入库 +2）；原有的盘点扫码流程加「编码栏显示内部编码 / 系统编码」断言，
  按台商品在普通公司按数量照常出录入框（S0）。两套数据、电脑与手机宽度全过（LINE 页只跑手机宽度）。
- **各窗口自己的实验栈**：`scripts/flows/run.py` 认 `PEARNLY_FLOWS_LAB`，回归实验室编排三个端口都可改；本任务用 `pearnly-verify-lab`
  （照 regression-lab 的编排）。页面驱动加 `--autoplay-policy=no-user-gesture-required`（仍静音），保存成功的提示音不再被记成页面报错。

## 暂停点（2026-10-29 晚，用户下班）

- **合入**：本分支变基到 main `70aa07d8` 后，本地全量闸门通过、GitHub Development checks 通过、流程用例 `pearnly/flows` 在 `f61b48a4` 上
  success（`base 70aa07d855f1; 9 flows; datasets new,old; 34/34 runs passed`）；按主线集成快进合入 main（本记录这一提交即合入点，SHA 报主会话）。
- **未部署**：这一批由主会话在最后那个 main 提交上统一跑全套流程、部署。部署时要做的只读回读：
  - 补丁 `v1_17_company_settings`：记着 Express 账套 / 连上 Express 的公司 `express_enabled = 1`、`company_type = general`（testerp 预期 62 家）；
  - 补丁 `v1_18_default_company_warehouse`：补丁前后列每个站点「0 个仓的公司」（预期只有 gize，补后为空），写进发布记录；
  - 新列：`ERP Company.company_type / express_enabled`、`ERP Product.origin*`、`ERP Stock Count Item.row_origin`、`ERP Warehouse.warehouse_code`、
    `ERP Warehouse Location.description`。
- **还没测到（要测试环境 / 真机）**：盘点打印 PDF 下载；真写 Express（含「Express 中没有这件商品」停人工）；LINE 真机（LIFF 验签）；
  收银台汽车公司实卖选台。CC 在 TK 里给盘点新建的商品建档另排（批复 #7）。
- **保留项**：本地分支 `pearnly/mixed-operators-parens`（34a7c5b9，见上「合入前补的几件」）；实验栈 `pearnly-verify-lab` 已 `compose stop`、
  数据卷保留（两套合成数据，下次跑流程直接起）。工作树 `stocktake-assess` 与分支 `pearnly/stocktake-complete` 合入后清理。

## 部署前补：已有数据补值（2026-10-30，验收窗口在干净代码上查出）

- **问题**：「来由」（`ERP Product.origin`）只在 Express 主档同步**新建**商品时写 `express`，10-29 之前同步进来的商品来由是空的；部署后生产
  polymer、mrerp 等站点的同步商品来由都空，`erp.products.create` 在 erp-new 上「同步来的物料带来由」不通过。
- **补丁** `v1_20_product_origin_express`：带 Express 编码、来由为空的商品设成 `express`，只补空值，重跑无副作用；补丁里打印补前 / 补后空值条数
  （`ERP Product origin: N synced products without an origin before, 0 after`）。没有 Express 编码的旧商品分不清是手建、导入还是采购 / 销售自动建，
  保持空（来由栏显示空），不猜。
- **按类核对 S0–S6 新加字段**（有没有「对已有数据有含义、却没补值」的）：

  | 字段 | 已有数据 | 结论 |
  | --- | --- | --- |
  | `ERP Company.company_type` / `express_enabled` | 有含义 | 已由 `v1_17_company_settings` 补（类型 general；连过 Express 的设为接 Express） |
  | `ERP Product.origin` | 同步商品有含义 | 本次 `v1_20` 补；其余空值见上 |
  | `ERP Product.origin_doctype` / `origin_name` | 只对盘点 / 采购 / 销售当场新建的商品有值 | 不补：旧商品没有来源单 |
  | `ERP Stock Count Item.row_origin` | 空 = 普通盘点行 | 不补：空就是正确值 |
  | `ERP Warehouse.warehouse_code` | 接 Express 的仓显示 `express_code`（同步新建的也不写 `warehouse_code`） | 不补：新旧数据同一口径，显示处都是 `express_code or warehouse_code` |
  | `ERP Warehouse Location.description` | 可空的说明 | 不补 |
  | `ERP Product.internal_code`（10-26，商品内部编码显示） | 同步商品显示 Express 编码（读时回退） | 不补：同上，新旧同口径 |
  | 批次 / 有效期列（S1 删除，列保留隐藏） | 不再读写 | 不补 |
- **部署回读**：补丁前后各数一次 `select count(*) from tabERP Product where ifnull(express_code,'')!='' and ifnull(origin,'')=''`（每个 ERP 站点，补后应为 0），
  写进发布记录。
