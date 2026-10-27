# 打印第二批：采购订单、报价单、销售单、收银小票、交班小结、盘点表

基线 main `4561884f`，分支 `pearnly/print-batch2`。评估：`2026-10-26-print-batch2-评估.md`（`b3bff441`，主会话 10-26 审定，§12 八条已定）。
同分支前一步：打印字体换 Prompt（见 `2026-10-26-print-batch1.md`「字体换 Prompt」）。

## 主会话定的口径（§12）

① 六类没有样板，照第一批风格做，样张交用户和岳父看，有纸质样板再改 1:1；② 销售单标题写死：接 Express 打 ใบส่งของ / DELIVERY NOTE
并印 Express 单号，没接打 ใบเสร็จรับเงิน / RECEIPT，不印税票字样；③ 报价 / 销售单印客户税号、分店号，从客户档取，只取本单公司或共用档；
④ 收银小票印 ใบเสร็จรับเงิน，不印简易税票；⑤ 点数表不印账面数（盲盘），结果表印账面 / 实点 / 差异；⑥ 交班小结要打，热敏一张；
⑦ 小票页眉只印公司抬头资料，不加感谢语；⑧ 浏览器直打热敏为主，PDF 存档。多仓库：现在不读单头仓库，等多仓闭环 P1 定了字段再补。

## 做了什么

| 单据 | Print Format | 版面 | 入口 |
| --- | --- | --- | --- |
| 采购订单（`Purchase Document` doc_kind=purchase_order） | Pearnly Purchase Order | `printing/templates/trade.html`，A4 横 | 采购单详情菜单「Print」（submitted / void；入了库的照旧开收货单） |
| 报价单 / 销售单（`ERP Sales Document`，非收银来源） | Pearnly Sales Document | 同上，标题按 doc_kind 与是否接 Express | 销售单详情菜单「Print」（submitted / void） |
| 收银小票（`ERP Sale`，销售 / 退货） | Pearnly POS Receipt | `printing/templates/thermal.html`，热敏 80 mm | 收银台「打印收据」（`p.pos_receipt.print` 带上格式）、收银记录小票查看器「Print」 |
| 交班小结（`ERP Shift`，已交班） | Pearnly Shift Summary | 同上 | 交班记录汇总弹窗「Print」（只对已交班） |
| 盘点（`ERP Stock Count`） | Pearnly Stock Count | `printing/templates/stock_count.html`，A4 竖、每页 22 行 | 盘点详情「Print」：进行中打点数表，结束后打结果表 |

- 取数 `printing/context.py`：`pearnly_print_context(doc, kind)` 按单据类型分派（第一批两种 + 本批四种）；纯规则在 `printing/contracts.py`
  （标题键、分店、车号行、盲盘 / 结果、热敏纸长）。Python 只给键，泰英文字（标题、信息栏、合计项、签字栏、付款方式、分店、车号）
  集中在 `printing/templates/_labels.html`（`check-i18n --strict` 不许 Python 里出现给人看的泰文，第一批也是文字在模板）。
  公共零件 `_parts.html` 加竖版、行下印车号、合计框。
- 抬头公司：采购 / 销售单按单上的公司，小票 / 交班按收银台的公司，盘点按仓库的公司；热敏抬头另印分店（公司分店号 0 = สำนักงานใหญ่）与税号。
- 按台商品（车辆）：采购订单、销售单、小票在该行下印 เลขตัวถัง / เลขเครื่อง / สี（单上 `serials`，按行号对应）；报价单不印（还没选台）。
- 热敏：`.print-format` 上声明 `page-width: 80mm`、按行数估的 `page-height`，Frappe 交给 wkhtmltopdf，PDF 一张不分页；浏览器打印按纸宽 100% 排。
- 越权（照第一批两处规则）：只打库里已有、当前用户读得到的单（按名字重取 + 读权限）；采购订单格式只给采购订单；交班只打已交班；
  客户 / 供应商档只在属于本单公司或是共用档时读（第一批收货单的供应商档同时收紧到这一口径）；退货小票只印同一收银台、读得到的原单号。
- 共用件：新登记 `erp-printing`（`scripts/impact/shared-parts.toml`，6 个打印入口）；`erp-transactions` 补交班记录页入口；`erp-masters-access` 补采购、销售打印入口。

## 与评估的出入

- 收银台交班弹窗（`pearnly-pos-shift.js`）不加打印：弹窗是交班**之前**的对账表，班还没关；交完到交班记录页打（评估第 6 节原写两处）。
- 盘点两种打印共用一颗「Print」按钮、一个格式，按盘点状态出点数表或结果表（评估原写两颗按钮），界面不多一条词条。

## 验证

环境：本机一次性实验栈 `pearnly-print-lab`（`~/pearnly-devtools/print-lab/`，自带库 / Redis、只开本机端口），底座 = 线上同一镜像
`ghcr.io/skin306152-star/pearnly/frappe@sha256:fb1037ad…`（Frappe 16.33.0、wkhtmltopdf 0.12.6），产品代码只读挂本分支。测试机没动。

| 场景 | 结果 |
| --- | --- |
| 框架原生 `frappe.get_print` 出 PDF：采购订单、报价单、销售单（没接 Express）、销售单（接了 Express，样张脚本里把 `linked` 换真）、收银小票、退货小票、交班小结、点数表、结果表 | 九份都是一页；嵌入字体 Prompt-Regular / Prompt-Bold；热敏两份 PDF 纸宽 80.1 mm（一张不分页），盘点 A4 竖 |
| 样张数据（主会话 10-26 核：报价单 6,206.50 − 100.00 + 427.46 应为 6,533.96，初版样张印 6,533.95） | 查清是初版样张直接写库造数、各自舍入不自洽（库里存的就是 6,533.95），模板照印存着的数。采购订单、报价单、销售单、收银小票、退货、交班改走系统真实建单接口重出（`purchasing.entry.post`、`sales.entry.post`、`cashier.sales.create_sale` / `refund`、`cashier.shifts.open_shift` / `close_shift`，金额、税、单号都由系统算 / 发）：报价单合计 6,533.96、采购订单 10,696.26、小票 573.00、退货 48.50、交班实点 1,514.50，PDF 里印的与库里存的逐一相同。盘点两张没有金额，仍是直接造的明细。按台商品车号行由单元测试覆盖（系统建单要先登记车辆，这批样张没带） |
| 报价单编号 | 系统给报价单也走销售方向发号（`sales/entry.py` `numbering.allocate(direction="sales")` → `SI`，`numbering/contracts.py` `SALES = "SI"`），与销售单共用一个计数；模板印的是单上的 `system_no`，没拿错字段 |
| 采购订单合计行 | 初版没有预扣税也印了「ยอดชำระสุทธิ / Net Payable」：已改成预扣税不为 0 才印预扣税、应付净额两行（`test_erp_print_security.py` 新增「合计只抄单上存的数」） |
| 版面逐张看 | 采购订单印供应商税号 + 「สำนักงานใหญ่」、需要日期、对方单号、申请人，合计含折扣 / VAT；销售单印客户税号 + 「สาขา 00003」、车号行（เลขตัวถัง / เลขเครื่อง / สี）在第 2 行下；接 Express 的标题 ใบส่งของ / DELIVERY NOTE 并印 Express 单号，没接的 ใบเสร็จรับเงิน / RECEIPT；全部不含「ใบกำกับภาษี」；小票页眉只有公司抬头（含分店、税号），无感谢语；退货小票印原单号与原因；点数表没有账面数列，结果表有账面 / 实点 / 差异 |
| 浏览器直打热敏（Chrome 打印，纸宽 80 mm） | 小票、交班各一页，文字最右到 76.2 mm，不截边（`browser-print-80mm-pos-receipt.png`） |
| 逐入口点「打印」（无头 Chrome，拦下打开的地址） | 采购订单 → `format=Pearnly Purchase Order`；销售单、报价单 → `Pearnly Sales Document`；小票查看器 → `Pearnly POS Receipt`；收银台「打印收据」（`p.pos_receipt.print`）→ 同格式且 `trigger_print=1`；交班记录（已交班）→ `Pearnly Shift Summary`；盘点进行中 / 已过账 → `Pearnly Stock Count`。出入库单弹窗（第一批入口）本批没改 |
| 原生打印预览 `/printview` 截图 | 八张都正常出版面（`preview-*.png`）。改模板后网页进程要重启才换新模板（实验栈 `bench serve --noreload` 实测，旧进程报 417）；部署本来就重启进程 |
| 非管理员越权（两个收银员账号，角色 ERP Menu cashier / pos-receipts） | 收银员 1 打自己的小票成功；收银员 2 打同一张被 `PermissionError` 拒 |
| `tests/test_erp_printing.py`（生产镜像） | 14 项 OK（新增：标题不含税票、分店写法、车号行、盲盘、热敏纸长；贸易单据 / 小票 / 退货 / 交班 / 点数表与结果表渲染） |
| `tests/test_erp_print_security.py`（生产镜像） | 8 项 OK（新增：四种单读不到拒打、采购订单格式只给采购订单、开着的班不打、别家公司的客户档不读、别的收银台的原单号不印） |
| `scripts/check-impact.py` | 登记表完整（46 个共用件、169 个入口）；命中 `erp-transactions`（9 个入口）、`erp-printing`（6 个入口） |

**连带回归说明**：`erp-printing` 的 6 个打印入口上面逐个点过。`erp-transactions` 本批只在小票查看器里加了一颗按钮（`receipt_view`），
它登记的 9 个页面没有逐页冒烟：实验栈 ERP 站点没装 ERPNext、没有测试租户数据，页面级冒烟留给验收窗口在测试租户上跑（`scripts/smoke`）。

**发现的 main 回归（不在本批范围，已报主会话）**：`96034aa0`（10-26 仓库库位清单）把采购 / 销售明细「库位」改成原生 Autocomplete；
已提交 / 作废单的详情页只读，只读的 Autocomplete 不建下拉，`add_line` 赋值时 `ControlAutocomplete.validate` 读 `awesomplete._list` 报错，
详情页后面的 Express、收款、取消和本批的打印菜单都出不来。生产 `08bfb419` 不含这个提交。当时实验里用只在测试浏览器生效的垫片
（`print-lab/shim-autocomplete.js`）绕过；main 已由 `ad21fcb0` 修复，10-27 变基后去掉垫片复测（见下节）。

## 待办

- 样张交主会话转用户和岳父；岳父有纸质样板就照 1:1 改。
- 多仓闭环 P1 定了单头仓库字段，模板补印仓库（主会话通知）。
- 真热敏打印机实测（测试机若有，动之前先问主会话）。

## 变基后复测（10-27）

- 分支变基到 `origin/main` `1dabbd06`（含库位只读 Autocomplete 回归的修复 `ad21fcb0`）；唯一冲突在 `scripts/impact/shared-parts.toml`
  `erp-transactions` 的入口清单：保留 main 新增的详情页入口，再加本批的 `erp.page.pos-shifts`。
- 实验栈 `pearnly-print-lab` 重起（迁移、清缓存、`bench serve`），**不加任何垫片**，无头 Chrome 逐张打开详情页，记页头按钮、点「Print」拦下打开的地址、再截打印页。
  草稿 / 作废单是补建的：采购草稿走正式 `purchasing.service.create_purchase`；销售草稿只有 LINE 流程会产生，实验站点里复制已提交单改成草稿；
  作废一律走正式 `entry.cancel`（脚本 `print-lab/work/seed4_states.py`，`print-lab/run-details.sh`）。

| 详情页 | 草稿 | 已提交 | 已作废 |
| --- | --- | --- | --- |
| 采购订单（未入库） | 无「Print」（页头 Submit / Actions） | `Pearnly Purchase Order` | `Pearnly Purchase Order`，带「ยกเลิก / CANCELLED」水印（`detail-void-purchase-order.png`） |
| 采购发票（已入库） | 无「Print」 | `Pearnly Purchase Goods Receipt`（第一批收货单） | 同左，带水印 |
| 销售单（非收银来源） | 无「Print」 | `Pearnly Sales Document` | 同左，带水印（`detail-void-sales.png`） |
| 报价单 | 无「Print」 | `Pearnly Sales Document` | 同左，带水印 |

- 12 种组合与 `purchase-form.js` / `sales-form.js` 的条件一致，页面没有报错（只有实验栈没起 socket.io 的连接提示）。
- 入了库的采购订单（实验库里 `PUR-2026-00201`）开的是收货单格式：评估第 108 行审定「入了库的照旧开收货单」，符合口径。

## 页头入口按页登记（10-27 验收复核）

- 验收复核发现：本批在销售详情（`pearnly-sales`）、盘点（`pearnly-stock-count`）新加的「Print」没登记进这两页的声明；当时 `check-entries` 按整个产品汇总，
  「Print」在采购页登记过就放过了。检查已在 `pearnly/native-routes` 改成按页核对（同一工具在两页各报一处）。
- 本分支改为叠在 `pearnly/native-buttons-whitelist`（a162b649）之上（它先合入 main），在 ERP `pearnly_navigation` 的 `actions` 两页补「Print」；
  按页核对版检查对本分支 110 个调用点无漏登记。

## 验收复核（10-27，bfb4b033 通过）后的两条小改

- 拒打改在原生打印渲染前判：`printing/context.py` 抽出 `_admit`（单在库里、读得到；采购订单格式只给采购订单；交班小结只给已交班的），
  模板取数和新挂的 `before_print`（六类可打单据的 `doc_events`，`printview.get_rendered_template` 渲染前调）共用；格式名取请求参数
  `format` / `print_format`。以前这两类拒打在模板里抛，框架按模板出错回 417。实验站点：采购订单格式打采购发票（打印页、PDF 接口）、
  开着的班打交班小结都回 403；采购订单格式打采购订单、收货单格式打采购发票、已交班的交班小结照常 200。`test_erp_print_security.py` 加一条（9 项）。
- `_labels.html` 的 `payment(method)` 找不到名称原样输出时加 `| e`。

验收提的两条存量问题（不在本批）：盘点页在页内切单不清页头菜单；采购岗打开同事的采购单详情页面空白（没出无权限页）。已报主会话。

