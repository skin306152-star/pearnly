# 需求影响评估：连续盘点改成框内扫码图标，扫完自动接下一项

基线：`cf9b3faf6`（= origin/main，含 11-02 字段说明图标）；分支 `pearnly/stocktake-scan-inline`。状态：主会话 11-02 审定通过（LINE `/stocktake` 不改；Cowork 只列本任务上传的物料；单条录入 / 修改同一个框；扫码器在弹窗里展开），已施工，验证见文末。

## 0. 需求原文与理解

- 主会话 11-02 转达、用户已确认的改法（ERP 与 Cowork 的「连续盘点」弹窗一样改）：顶上只留「条形码、二维码或物料编码」一个框，框内右侧扫码图标；删「查找物料」「扫描条形码或二维码」两颗按钮，相机画面平时不显示；点图标才开扫码器，扫到后扫码器自动关、直接显示该物料全部字段（字段不增不减）；「保存并下一项」改名「继续」：上一项是扫码来的就自动再开扫码器，打字选的就回到框里、光标放进去、不开相机；「完成」不变；扫码器开着能手动关，关后回到框；打字自动弹出匹配物料（本次盘点上传的物料 + 仓库在库物料，原生 Link / awesomplete，服务端分页）；扫到系统没有的码保持现状（先提示、再给「新建」）。
- 不在范围：LINE 手机盘点页 `/stocktake`（见 §12-1）、单条「录入」/「修改」弹窗以外的扫码入口的交互、DMS、控制面。

## 1. 现有同类与复用

- 框内扫码图标：共用层 `pearnly_core/public/desk/link.js` 的 `p.attach_scan_btn`（Link 输入框右侧灰图标，形状照官方 `Data` + `options:"Barcode"`），动作由产品给（ERP `p.scan_doctypes = ["ERP Product"]` + `p.scan_link`：扫到 → 按码找商品 → 填进选择框）。
- 相机：共用 `p.scan_stage` / `p.scan_reader`（`pearnly_core/public/desk/scan.js` + `scan/reader.js`），录入弹窗现在就在弹窗里开画面；ERP 另有独立扫码弹窗 `p.scan`（只 ERP）。
- 查找：ERP `inventory.count.lookup_count`（码 / 商品 → 本盘点的行、别处、已记、按台、网址码、撞码候选、查不到），Cowork `stocktake.counting.lookup`（只认本任务上传的行）。
- 结论：扩展现有。框内图标用 `link.js` 那一颗（加「这一格自己的扫码动作」）；相机仍用弹窗里那块共用画面（保留安卓 LINE 转 LIFF、权限三步、失败原因提示这些已验过的路径），只是平时收起、点图标才开、读到即收。

## 2. Frappe 原生能力

- 输入框 + 下拉：原生 `ControlLink`（awesomplete），`get_query` 指向服务端自定义查询方法（`frappe.desk.search` 约定的 `(doctype, txt, searchfield, start, page_len, filters)`），按 `start` / `page_len` 分页，不在前端过滤。
- ERP 的 Link 指 `ERP Product`；Cowork 没有物料主档，指 `Cowork Stocktake Item`（本任务上传的行），值就是行号，查找走现有 `lookup(item_id=…)`。
- 框内图标：沿用 `link.js` 现在的官方样式按钮，不画新图标。

## 3. 按类通用

- 同类：ERP 盘点录入弹窗（连续 / 单条 / 修改共用 `stockCountEntry`）、Cowork 盘点录入弹窗（照 ERP 的副本）。两份一起改，单条「录入」与「修改」也换成同一个框（只是不连续），避免一个弹窗两套交互。
- 不跟着改：出入库 / 采购 / 销售 / 期初明细的物料格（已经是框内扫码，扫到填进选择框，交互不同）、收银台、LINE 手机盘点页。

## 4. 上下游数据

- 只改录入弹窗的交互，记数接口、载荷、盘点结果、Express、CC 桌面版都不变。
- 新增两个只读查询接口：ERP「本次盘点的物料 + 本盘点仓在库的物料（盘点没指定仓时为本公司在库）」，Cowork「本任务上传的行」；按编码 / 内部编码 / 条码 / 名称模糊查，分页、带上限，`check-unbounded-queries` 照过。

## 5. 隔离与权限

- 查询接口与现有 `lookup_count` 同一个权限（`count.read`）、同一公司 / 工作区范围；ERP 只列本盘点所属公司的物料，Cowork 只列本任务的行。服务端拦，不靠前端。

## 6. 入口：网页、手机、LINE、拍照

- 电脑与手机网页、LINE 富菜单进的 LIFF 网页端都是这一个弹窗。
- 相机按「相机权限三步」：有权限直接用 → 弹原生授权 → 弹不出才引导去设置（沿用 `p.scan_hint` / `scan_liff_first`，安卓 LINE 普通内置浏览器转 LIFF 后自动开扫码器）。
- 键盘：打字选物料时 Link 下拉在键盘上方（`keyboard.js` 已把带建议列表的格摆在上部三分之一）；扫码回来不弹键盘（焦点给数量格时才弹，照现在）。

## 7. 共用件与连带回归

`check-impact`（stocktake-entry.js ×2、core `scan.js`、ERP `scan.js`）命中 `desk-scan-core` 17 个入口、`erp-scan-dialog` 13 个入口；若改 `link.js`（给单格自己的扫码动作）还命中 `desk-link`。全部入口逐个实测记「通过 / 不通过 / 没测到（原因）」，扫码类入口用无头 Chrome + 假摄像头逐个验画面与读码，不抽样。

## 8. 存储与成本

不涉及：无新数据、无外部调用。

## 9. 删除、作废、追溯

不涉及：记数、作废、修改记录的逻辑不变。

## 10. 文案与官方 UI

- 改名：「保存并下一项」→「继续」（`stock.workbench.saveNext`、`cowork.stock.workbench.saveNext`；用户已同意），泰 / 英 / 日：ดำเนินการต่อ / Continue / 続ける；登记 `scripts/i18n/copy-approved.tsv`（日期 11-02，路径本记录）。
- 删除：「查找物料」「扫描条形码或二维码」两颗按钮的词条（按钮去掉后无引用）。
- 不变：框的标签「条形码、二维码或物料编码」、「完成」、提示句。框旁的说明「扫码后填写实际仓位和数量，保存后进入下一项。」按 11-02 已在说明图标里，文字不动。
- 组件：原生 Link、Dialog、共用扫码按钮与画面，无新样式。

## 11. 测试与验收

- 流程用例：`erp.stocktake.scan`、`erp.stocktake.new-product`、`erp.stocktake.places`、`erp.stocktake.web-code`、`erp.stocktake.gs1` 的步骤与断言改成「点框内图标 → 读码 → 自动关、显示字段 → 继续 → 自动重开」；补 Cowork 连续盘点一条；断言打字下拉只出本盘点 / 在库物料且是服务端分页请求。
- 无头 Chrome 假摄像头：每个扫码入口（上面 30 个里属于相机的）验画面与读码。
- 真机（主会话 11-02 补充）：三星 adb 反连本机实验栈，测试机屏幕全屏显示码图（先登记占用表、用完关图、照规矩关屏）：已有物料 EAN、岳父 Isuzu 标签二维码（车架号）、系统里没有的码、认证网址二维码；扫码器自动关闭、字段显示、点「继续」后自动重开每步截三星屏。上线后走 testerp / testcowork LINE 菜单同样几项（主会话安排部署后做）。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | LINE 手机盘点页 `/stocktake`（独立页，已经是相机优先）要不要跟着改 | 不改：它不是这个弹窗，交互本来就是开着相机连续扫 | 改的话另起一份评估 |
| 2 | Cowork 打字匹配范围：Cowork 没有仓库库存，只能是本任务上传的行 | 就只列上传的行 | 无 |
| 3 | 单条「录入」/「修改」弹窗也换成同一个框 | 换（同一个弹窗，一套交互）；「修改」打开已录那一行时照现在直接显示字段 | 不换的话一个函数里留两套 |
| 4 | 扫码器形式：弹窗里展开相机画面（读到收起）还是另弹一个扫码窗（ERP `p.scan`） | 弹窗里展开：Cowork 没有 `p.scan`，且保留已验过的 LINE 转 LIFF、权限三步路径 | 另弹窗要把 `p.scan` 挪进共用层，波及 13 个入口 |

## 13. 工作量与排期（机器耗时）

写代码约 1.5 小时；本机流程与假摄像头逐入口约 1 小时；真机（含测试机显示码图）约 40 分钟；CI 与合入约 30 分钟。

## 施工与验证（11-02）

### 改动

| 文件 | 内容 |
| --- | --- |
| `pearnly_core/public/desk/link.js` | 字段可带自己的扫码动作 `df.scan(control)`：照样放框内扫码图标，点了走它，不看 `p.scan_doctypes`、不走 `p.scan_link` |
| ERP / Cowork `public/desk/stocktake-entry.js` | 「查找物料」「扫描条形码或二维码」两颗按钮删掉；`code` 改原生 Link（ERP `ERP Product`、Cowork `Cowork Stocktake Item`，`only_select`、`ignore_link_validation`，`get_query` 走服务端分页查询）；框内图标开扫码器（开着时再点是关）、「关闭」在画面上方；读到物料扫码器就关、出字段；`scanned` 记这一件是不是扫码来的：「继续」后扫码来的自动再开扫码器并跳过刚扫的码（镜头还对着它时不重复记），打字 / 输码回车 / 下拉选的回到框里；触屏打开弹窗不自动聚焦（`no_focus`，不弹键盘挡扫码）；下拉第二行去掉内部编号、不出「已应用筛选」那一行；回车在捕获阶段先按码认（用方向键挑了一项才交给原生选中） |
| ERP / Cowork `public/desk/stocktake.js` | 从 LINE 转 LIFF 回来（`pearnly_liff=scan`）时传 `scan: true` 一打开就开扫码器 |
| `pearnly_erp_portal/inventory/count.py`、`inventory/api.py` | `search_count_products` / `api_stock_count_product_search`：本次盘点的物料 + 本盘点仓在库的物料（没指定仓时为本人能看的仓），按公司分（同按码认），名称 / 编码 / 内部编码 / Express 编码 / 条码 / 二维码内容模糊查，每页最多 50；`count.read` 判权 |
| `pearnly_cowork/stocktake/counting.py`、`desk_api.py` | `search` / `api_stock_count_item_search`：本任务上传的行，名称 / 编码 / 条码模糊查，只读不锁任务，每页最多 50；`access.require` 判权 |
| `Cowork Stocktake Item` DocType | `title_field = product_name`、`show_title_field_in_link = 1`（选择框显示物料名，不显示行号；只改元数据） |
| 术语表 / 翻译包 | `saveNext`「保存并下一项 → 继续」（ดำเนินการต่อ / Continue / 続ける），登记 `copy-approved.tsv`；删 `find`、`camera` 两条 |
| 流程用例 | 5 条 ERP 盘点流程改成「框里输码回车 / 点框内扫码图标」（公共片段 `scripts/flows/stocktake_lib.py`）；`erp.stocktake.scan` 加断言：扫码读到后扫码器关、「继续」后扫码的自动重开 / 打字的回到框里、打字下拉选中、查询接口的权限与分页；新增 `cowork.stocktake.entry`；提示框只认 `<p>`（HTML 格里本来就有框架的隐藏字段名，旧写法会把它当成提示） |
| `tests/test-link-options.cjs` | 9-23 起没进 CI、已跑不通：补桩恢复，加「单格自己的扫码动作」两条，接进 development.yml |

### 验证（实验栈 `pearnly-fieldhelp-lab`，源码 = 本工作树）

- 盘点流程 6 条 24/24 通过（ERP 5 条 + Cowork 1 条，两套数据 × 两种宽度，手机宽另跑假摄像头）。
- 截图（无头 Chrome，电脑 1280 / 手机 375）：打开（一个框 + 框内图标 + 说明图标）→ 打字下拉（名称 + 编码 · 条码）→ 扫码器开着带「关闭」→ 读到后出字段、按钮「完成 / 继续」→「继续」后扫码器重开且不重复读。
- 三星 SM-A146P 真机（adb 反连本机实验栈，真相机对着测试机屏幕全屏显示的码，截图在共享笔记 `盘点框内扫码-真机-11-02/`）：
  - ERP：打开不弹键盘 → 点图标 → Chrome 原生相机授权（三步的第②步）→ 读到 EAN 8850002000025 → 扫码器关、出「สินค้าเก่า 2」字段 → 输数量点「继续」→ 扫码器自动重开，屏幕上同一码 7 秒内不重复读；换系统里没有的 EAN 2009876543213 → 扫码器关、「未找到与该编号匹配的物料」+「新建物料」；认证网址二维码 → 浮动提示「这不是商品条码」、不出新建、扫码器继续开着；岳父 Isuzu 标签二维码 → 车架号在实验栈没有这件物料 → 未找到 + 新建，新建弹窗预填完整车架号 `MP1TFR41JTG032993`（没保存）；打字「OLD-00」出下拉、点选出字段；扫码器开着时点「关闭」或再点图标都能关、回到框里。
  - Cowork：同样的授权 → 读到上传那件的条码 → 关扫码器出字段 →「继续」自动重开不重复读 →「关闭」→ 打字「typed」下拉选第二件 → 出字段 →「继续」回到框里、光标在框里、不开相机。
  - ERP 打字那件保存时服务端报「盘点期间库存已变化」：后台同时在跑的出入库 / 调拨流程改了那件物料的库存，是现有防护，与本次无关；流程用例里同一路径通过。

### 共用件连带回归（check-impact 实际命中 `desk-link`；主会话要求另把共用相机 `desk-scan-core`、ERP 扫码弹窗 `erp-scan-dialog` 一并逐个测）

| 共用件 | 入口 | 结果 |
| --- | --- | --- |
| `desk-link` | `erp.page.home` pearnly-home | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.products` pearnly-products | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.inventory` pearnly-inventory | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.stock-in` pearnly-stock-in | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.stock-out` pearnly-stock-out | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.stock-transfer` pearnly-stock-transfer | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.stock-card` pearnly-stock-card | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.stock-count` pearnly-stock-count | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.cashier` pearnly-cashier | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.company` pearnly-company | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.warehouses` pearnly-warehouses | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.team` pearnly-team | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.purchase` pearnly-purchase | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.suppliers` pearnly-suppliers | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.sales` pearnly-sales | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.pos-receipts` pearnly-pos-receipts | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.pos-shifts` pearnly-pos-shifts | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.clients` pearnly-clients | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.page.tasks` pearnly-tasks | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `cowork.page.intake` pearnly-intake | 通过（冒烟 auto，本机实验栈） |
| `desk-link` | `erp.stock-in.item-scan` pearnly-stock-in 新建明细 | 通过（假摄像头探针：入库新建 → 明细物料格扫码图标 → 有画面，读出 8850002000025，物料与名称填上「สินค้าเก่า 2」；流程 erp.stock-entry.scan 相机那遍也过） |
| `desk-link` | `erp.stock-out.item-scan` pearnly-stock-out 新建明细 | 通过（探针：出库新建 → 明细物料格扫码 → 有画面、填上「สินค้าเก่า 2」） |
| `desk-link` | `erp.inventory.opening.item-scan` 库存 → 录入期初 | 通过（探针：库存 → 录入期初 → 商品格扫码 → 有画面、填上「สินค้าเก่า 2」） |
| `desk-scan-core` | `erp.products.new-dialog.scan` pearnly-products 新建弹窗 | 通过（冒烟 auto，本机实验栈） |
| `desk-scan-core` | `erp.products.new-page.scan` pearnly-products/new 表格 | 通过（探针：/desk/pearnly-products/new 表格上方扫码 → 有画面、加一行编码 2000000000008） |
| `desk-scan-core` | `erp.products.detail.barcode-scan` 物料详情条码格 | 通过（探针：物料列表点第一行 → 编码格扫码图标 → 有画面、填上 2000000000008；没保存） |
| `desk-scan-core` | `erp.cashier.camera` pearnly-cashier 搜索框相机 | 通过（探针：开班 → 收银页搜索框右侧相机 → 有画面、读出码、商品进购物车 0→1） |
| `desk-scan-core` | `erp.cashier.scanner-gun` pearnly-cashier 扫码枪 | 通过（探针：搜索框直接输码 → 同一件数量 1→2） |
| `desk-scan-core` | `erp.stock-count.entry.camera` pearnly-stock-count 录入弹窗 | 通过（流程 erp.stocktake.scan / gs1 / new-product / web-code 相机那遍；三星真相机对着测试机屏幕：EAN、未知码、网址二维码、Isuzu 二维码） |
| `desk-scan-core` | `erp.line.stocktake.camera` LINE 手机盘点 /stocktake | 通过（流程 erp.line.stocktake.phone 相机那遍；首轮与真机测试同时跑偶发不过，单独补跑通过。LINE 手机盘点页这次没改） |
| `desk-scan-core` | `erp.stock-in.item-scan` pearnly-stock-in 新建明细 | 通过（假摄像头探针：入库新建 → 明细物料格扫码图标 → 有画面，读出 8850002000025，物料与名称填上「สินค้าเก่า 2」；流程 erp.stock-entry.scan 相机那遍也过） |
| `desk-scan-core` | `erp.stock-out.item-scan` pearnly-stock-out 新建明细 | 通过（探针：出库新建 → 明细物料格扫码 → 有画面、填上「สินค้าเก่า 2」） |
| `desk-scan-core` | `erp.inventory.opening.item-scan` 库存 → 录入期初 | 通过（探针：库存 → 录入期初 → 商品格扫码 → 有画面、填上「สินค้าเก่า 2」） |
| `desk-scan-core` | `erp.purchase.item-scan` pearnly-purchase 新建明细 | 通过（探针：采购新建 → 手动录入 → 明细物料格扫码 → 有画面、物料格填上「สินค้าเก่า 2」） |
| `desk-scan-core` | `erp.sales.item-scan` pearnly-sales 新建明细 | 通过（流程 erp.sales.scan 手机宽假摄像头那遍） |
| `desk-scan-core` | `cowork.stocktake.entry.camera` pearnly-stocktake 录入弹窗 | 通过（新流程 cowork.stocktake.entry 相机那遍；三星真相机对着测试机屏幕） |
| `desk-scan-core` | `dms.liff.id-scan` LINE → 扫身份证（LIFF #/dms/scan） | 通过（流程 dms.liff.scan-loading；DMS 这次没改） |
| `desk-scan-core` | `shared.upload.take-photo` 上传框「拍照」（电脑） | 通过（探针：电脑上 frappe.ui.Capture 有画面） |
| `desk-scan-core` | `erp.line.stocktake.liff-camera` LINE 富菜单「盘点」→ LIFF 打开 pearnly-stock-count | 没测到（要真 LINE 富菜单开 LIFF；上线后按主会话安排走 testerp LINE 入口真机测） |
| `desk-scan-core` | `cowork.line.stocktake.liff-camera` LINE 富菜单「库存盘点」→ LIFF 打开 pearnly-stocktake | 没测到（同上，走 testcowork LINE 入口） |
| `erp-scan-dialog` | `erp.products.new-dialog.scan` pearnly-products 新建弹窗 | 通过（冒烟 auto，本机实验栈） |
| `erp-scan-dialog` | `erp.products.new-page.scan` pearnly-products/new 表格 | 通过（探针：/desk/pearnly-products/new 表格上方扫码 → 有画面、加一行编码 2000000000008） |
| `erp-scan-dialog` | `erp.products.detail.barcode-scan` 物料详情条码格 | 通过（探针：物料列表点第一行 → 编码格扫码图标 → 有画面、填上 2000000000008；没保存） |
| `erp-scan-dialog` | `erp.cashier.camera` pearnly-cashier 搜索框相机 | 通过（探针：开班 → 收银页搜索框右侧相机 → 有画面、读出码、商品进购物车 0→1） |
| `erp-scan-dialog` | `erp.cashier.scanner-gun` pearnly-cashier 扫码枪 | 通过（探针：搜索框直接输码 → 同一件数量 1→2） |
| `erp-scan-dialog` | `erp.stock-count.entry.camera` pearnly-stock-count 录入弹窗 | 通过（流程 erp.stocktake.scan / gs1 / new-product / web-code 相机那遍；三星真相机对着测试机屏幕：EAN、未知码、网址二维码、Isuzu 二维码） |
| `erp-scan-dialog` | `erp.line.stocktake.camera` LINE 手机盘点 /stocktake | 通过（流程 erp.line.stocktake.phone 相机那遍；首轮与真机测试同时跑偶发不过，单独补跑通过。LINE 手机盘点页这次没改） |
| `erp-scan-dialog` | `erp.stock-in.item-scan` pearnly-stock-in 新建明细 | 通过（假摄像头探针：入库新建 → 明细物料格扫码图标 → 有画面，读出 8850002000025，物料与名称填上「สินค้าเก่า 2」；流程 erp.stock-entry.scan 相机那遍也过） |
| `erp-scan-dialog` | `erp.stock-out.item-scan` pearnly-stock-out 新建明细 | 通过（探针：出库新建 → 明细物料格扫码 → 有画面、填上「สินค้าเก่า 2」） |
| `erp-scan-dialog` | `erp.inventory.opening.item-scan` 库存 → 录入期初 | 通过（探针：库存 → 录入期初 → 商品格扫码 → 有画面、填上「สินค้าเก่า 2」） |
| `erp-scan-dialog` | `erp.purchase.item-scan` pearnly-purchase 新建明细 | 通过（探针：采购新建 → 手动录入 → 明细物料格扫码 → 有画面、物料格填上「สินค้าเก่า 2」） |
| `erp-scan-dialog` | `erp.sales.item-scan` pearnly-sales 新建明细 | 通过（流程 erp.sales.scan 手机宽假摄像头那遍） |
| `erp-scan-dialog` | `erp.serials.dialog` 按台管理：入库 / 出库 / 调拨 / 进货 / 销售收据 / 收银提交前的「登记车辆 / 选择车辆」弹窗 | 通过（流程 erp.stock-in.units；老数据首轮因启动信息缓存不过，清缓存后 2/2） |
