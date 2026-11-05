# 施工记录：删没人调的白名单接口（D 块待删候选，11-05）

- 分支 `pearnly/drop-dead-endpoints-5d31e3`，工作树 `.claude/worktrees/drop-dead-endpoints-5d31e3`，基线 `56c1807ed`（= origin/main，11-04 那批已上线）。
- 来源：[产品地图记录](2026-11-04-product-map.md)「D 块」列的 44 个待删候选（`scripts/impact/product-map/not_entries.toml` 里 `kind = "delete"`；
  同文件里 `pearnly_core.express_operator.api.task_*` 那 6 条是统一对话框架 U1 的，不在本批）。
- 主会话口径：上线后单独一批删；删前全仓再核一遍调用方，含 CC 桌面版和站外配置；拿不准就不删、标兼容保留。不部署。

## 1. 核调用方的范围与做法

每个接口按完整模块路径、方法名（单词）、前缀常量拼名三种写法全仓搜，范围：

| 范围 | 怎么查 | 结果 |
|---|---|---|
| 前端（`frontend/`、各 App `public/`、DocType 表单脚本、报表 JS、打印格式） | 完整路径 + 方法名单词（`frm.call("名字")`、`api + "名字"`、模板串）+ 产品地图扫描器 | 44 个都没有 |
| Python（产品代码、hooks、定时任务、队列目标、`override_whitelisted_methods`） | 同上 + `from … import` | 只有接口自己的文件；测试、流程用例、实验室验证脚本在调的另列 |
| CC 桌面版 | `companion/src` 现行代码；**全部历史版本**：`git log --all -S <模块路径> -- companion`（从 1.1.77 迁入本仓起，覆盖正式版 1.12.12 及更早还在跑的客户端） | 13 个相关模块在 CC 桌面版历史里一次都没出现；CC 只调 `pearnly_cowork.companion.*`、`folder_intake.machine.*`、`pearnly_core.cc_desktop.machine.*` |
| LINE、站外配置 | `infrastructure/`（nginx、Keycloak 主题）、`scripts/production/`（部署、回读、探针）、LINE 回调与 fatcc.com 入口都走别的路径 | 没有 |
| 仓库外 | `~/pearnly-devtools`（部署回读、候选检查、测试机驱动脚本） | 只有旧日志和旧副本里提到，没有脚本调 |
| 文档登记的外部对接 | `docs/` 全文 | 只有历史施工记录提到，没有对外承诺的接口 |

## 2. 每个接口的结论

**删 36 个，保留 8 个**（`api_stock_count_import` 见第 3 节）。

### 共用层（pearnly_core）

| 接口 | 结论 | 依据 / 连带删除 |
|---|---|---|
| `manifest.describe` | 删 | 外壳启动数据走 `shell.describe`（扫描器当初把流程 `cc_person_names.py` 里的 `shell.describe` 误认成它）；`unguarded_endpoints.toml` 删登记 |
| `ocr.api_selftest` | 删 | 系统管理员自检，没有页面、没有运维脚本调；样板脚本 `sample-verify-ocr.py` 删掉调用它的两行 |
| `sessions.my_sessions` | 删 | 「登录设备」列表一直没有界面；两个样板脚本（`sample-session-revoke.sh`、`sample-cross-product.sh`）里只拿它打印，删掉那一步，验证仍靠 `get_logged_user`；`docs/p2/04-sessions-and-revocation.md` 同步 |
| `sessions.logout_here` | 删 | 退出走 `pearnly_core.identity.sign_out`（身份服务登出）；模块说明同步 |

### Cowork

| 接口 | 结论 | 依据 / 连带删除 |
|---|---|---|
| `files.guard.optimize_file` | **保留**（改 `internal`） | 它覆盖 Frappe 原生 `File.optimize_file`：原生 File 表单「优化」按钮经 `run_doc_method` 调，方法名不带模块路径，所以扫不到；先过原件保护再交原生，删了会让原生按钮绕过保护 |
| `masters.api.workspaces` / `clients` / `create_client` | 删 | Cowork 没有主档页面（导航里没有客户 / 商品 / 供应商入口）；实验室验证脚本里拿 `create_client` 建测试客户的 8 处改成直接建 `Cowork Client`（与原接口同样的字段） |
| `masters.api.roster` | 删 | 连带删 `clients.list_clients` 与 `_aggregates`（名册三项合计），只有它调 |
| `masters.api.products` | 删 | 连带删 `products.list_products` |
| `masters.api.supplier_profiles` | 删 | 连带删 `push_inputs.list_profiles` 与 `supplier_posting.MAX_LIMIT`；`masters/push_inputs.py` 不在 `push/` 目录，不碰预选卡片窗口的文件 |
| `stocktake.api.create` / `add_entry` / `edit_entry` / `close` / `report` / `export` | 删 | 页面、LINE 盘点、CC 工具都走 `stocktake/desk_api.py` 与 `service`；连带删 `service.create`、`native_contract.decoded_import` 及其单测三条、`_write_with_photos`、`tests/test_exports.py` 里那一行；`service._existing` 仍被 `upload.create` 用，保留 |

Cowork 实验室验证脚本：
- `scripts/cowork-masters-lab-verify.py`：名册 / 商品 / 供应商档案的列表断言改成直接按库读（同样的筛选和排序）；名册三项合计那几条断言随功能删除；匿名 HTTP 检查去掉三个删掉的接口。
- `scripts/cowork-backend-lab-verify.py`：删掉只测 `masters.clients` 分页的一条用例。
- `scripts/cowork-stocktake-lab-verify.py`、`scripts/cowork-stocktake-export-lab-verify.py`：**整份删除**。它们验的就是这批删掉的原生写接口（建盘点用的正是被删的 `create`，记数、关闭、报告、导出、HTTP 段都走 `stocktake.api`）；改写等于把删掉的代码搬进测试。产品路径的覆盖在流程 `cowork.stocktake.list` / `cowork.stocktake.entry` / `cowork.stocktake.web-code` 与 `cowork.exports`（`tests/test_exports.py` 的 `desk_api.api_stock_count_export` 一行）。这两份脚本不在任何闸门里。

### ERP

| 接口 | 结论 | 依据 / 连带删除 |
|---|---|---|
| `integrations.api.api_line_bot` | 删 | 连带删 `binding.account`（只有它调；实验室脚本 `erp-pos-lab-integrations-verify.py` 里只验它的两条检查一起删，越权读仍由 `binding.status` 那条覆盖）；绑定页要的官方号信息（名字、Basic ID、加好友码）随绑定码一起给（`api_line_binding_code` → `binding.start`） |
| `inventory.api.api_count` | 删 | 连带删 `stock.count_lines`（只剩实验室脚本调；`erp-pos-lab-inventory-verify.py` 那一步改成按原算法直接落一笔 count 流水）；页面盘点走 `count.py` |
| `inventory.api.api_list_openings` / `api_upsert_opening` | 删 | 页面期初走 `api_preview_stock_opening` / `api_submit_stock_opening`；连带删 `openings.list_openings`、`opening_for`；`openings.upsert_opening` 保留（站点测试与实验室脚本拿它准备期初数据），说明改成「测试与实验室准备数据用」 |
| `inventory.api.api_list_warehouses` | 删 | 仓库页走 `api_company_warehouses`；站点测试改调 `warehouses.list_warehouses` |
| `inventory.api.api_rebuild_balance` | 删 | `stock.rebuild_balance` 保留（测试与实验室脚本拿它验「结存能由流水重建」） |
| `inventory.api.api_stock_count_items` / `api_stock_count_entries` / `api_stock_count_entry_total` / `api_stock_count_item_total` / `api_stock_count_post` | 删 | 盘点页走 `api_stock_count_review` / `api_stock_count_close` 等；连带删 `count.items_total`；站点测试改调 `count.list_items` / `count.list_entries` / `api_stock_count_close`（关闭就是同一个 `post_count`） |
| `inventory.serials.api_unit_history` | 删 | 按台页面只用 `api_list_units` / `api_lookup` 等，不显示单台流水 |
| `masters.api.api_company_terminals` / `api_create_company_terminal` / `api_update_company_terminal` | 删 | 公司页存公司资料时带收银台（`masters/company` 内部调 `cashier.terminals`）；连带删 `company.create_terminal` / `update_terminal` 两个包装 |
| `masters.api.api_set_customer_active` | 删 | 连带删 `customers.set_active`；实验室脚本改调 `update_customer(is_active=…)`（原函数就是这一行） |
| `products.service.api_add_unit` | 删 | `add_unit` 保留（实验室脚本拿它准备数据）；商品页不调 |
| `purchasing.api.api_purchase_category` | 删 | `records.category` 保留（实验室脚本拿它建测试分类） |
| `team.api.api_set_member_companies` | 删 | `team.service.set_member_companies` 保留（回归实验室 `lab_check_p1.py` 与站点测试拿它准备数据） |
| `mrerp.api.account_sets` / `choose` / `company_choices` / `configure` / `connection_view` / `detach` / `refresh_catalog` | **保留 7 个**（改 `compat`） | 11-03 合入的 ERP 接 MR.ERP 账套域（`c2159f8bb`），代码开关 `MRERP_LEDGER_ENABLED = False`、页面未接，等岳父的 MR.ERP 账号实测后接页面；是待接功能不是废弃 |

## 3. 主会话定的两件事

- `inventory.api.api_stock_count_import`（往已开的盘点里传表）：页面不调（页面导入走 `api_create_stock_count` 带文件），只剩流程 `erp.stock.excel-units`、站点测试和实验室脚本在调。
  主会话同意删：流程改走页面同一条路（建盘点时带表），去掉「同一张表再传一次不重记」那一步（页面没有这个操作）；站点测试去掉重传那条断言；
  实验室脚本 `erp-pos-lab-stock-count-verify.py` 验的是 `count.apply_import` 的覆盖语义（建盘点带表也走它），改成脚本里直接调 `_stock_rows` + `apply_import`。
- 产品地图规则 6：`inventory/api.py` 被一个只能真机验的 LINE 盘点相机行调用，原来按整个文件算「改到它调用的接口」，删同文件里没人调的函数也命中、要求附本次 SHA 的真机记录。
  主会话定（11-05）：不拿旧真机记录充数，也不为删没人调的函数去占测试机重跑；按类修闸门精度，改成按函数算（见第 6 节）。

## 4. 同模块里剩下的（不在本批，建议另一批核）

本批只删 44 个候选。下面这些同样全仓没有产品调用方，D 块分诊时因为文档提到被归成兼容保留 / 内部，建议下一批按同一做法复核：
- Cowork `masters/api.py` 剩下 13 个（`client_detail`、`save_client`、`delete_client`、`resolve_buyer`、`product_detail`、`save_product`、`delete_product`、`save_erp_mapping`、`save_supplier_profile`、`delete_supplier_profile`、`preview_client_import`、`commit_client_import`、`create_workspace`）——Cowork 没有主档页面；实验室验证脚本拿 `create_workspace` 建测试数据。
- Cowork `stocktake/api.py` 剩下 3 个只读接口（`records`、`detail`、`entries`），登记为 `internal`「给程序调」，但全仓没有程序调。
- ERP `inventory/api.py` 里 `api_adjust`、`api_issue`、`api_receive`、`api_stock_summary`、`api_submit_stock_entry`（`compat`）。

## 5. 共用件连带回归（`check-impact`）

命中共用件的四个文件改动（`git diff --numstat`）：`products/service.py` 0 增 8 删、`inventory/count.py` 0 增 5 删、`ocr.py` 0 增 52 删、`sessions.py` 3 增（模块说明）31 删。

命中的共用件：`erp-products-lookup`（ERP 按码找商品（products.service / purchase / sales））、`erp-count-code`（ERP 盘点按码认商品）、`ocr`（识别（ocr / claude_ocr / 原件））、`login-session`（登录、会话与开通闸（identity / sessions / shell / account））
只改到调用方的入口：`erp.serials.dialog`、`erp.report.stock-summary-qty-sort`、`erp.products.new-dialog.opening`、`erp.stock-in.attach`、`erp.stock-out.attach`、`erp.stock-count.upload`、`erp.line.chat`、`erp.line.binding`、`erp.exports`、`cowork.exports`、`shared.shell-gate`、`erp.masters.scope`、`erp.records.scope`、`erp.stock-count.new-product`、`erp.stock-count.code`、`erp.ocr`、`shared.execution`、`erp.browser-text`、`erp.serials.export`、`shared.scan-codes.web-address`

要实测的入口 44 个（每行填「通过 / 不通过 / 没测到（原因）」，不能用推断代替）：

| 入口 | 产品 | 位置 | 怎么点 | 预期 | 方式 | 结果 |
|---|---|---|---|---|---|---|
| `erp.products.find-by-code` | ERP | 按码找商品接口（扫码、新建物料查重、出入库扫码共用） | 自动：取一件在架商品，按编码 / 条码调 api_find_product | 找回同一件商品 | auto：find_product | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.entry.choices` | ERP | 单据对方 / 物料选择与提交时按码认 | 采购 / 销售明细物料格、对方格打字或扫码；提交 | 只出当前公司 + 本地；提交按名称 / 编码认同一件，单位跟商品 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.products.new-dialog.scan` | ERP | pearnly-products 新建弹窗 | 物料 → 新建 → 编码格扫码图标（相机）；扫码枪在编码格回车 | 相机有画面、读出码填进编码格；已有的码提示已存在 | auto：scan | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.products.new-page.scan` | ERP | pearnly-products/new 表格 | `/desk/pearnly-products/new` → 表格上方扫码按钮；表内扫码枪回车 | 每扫一个加一行；已有码提示 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.products.detail.barcode-scan` | ERP | 物料详情条码格 | 物料列表点本地商品 → 条码格扫码图标 | 填入条码；Express 同步来的商品不出扫码图标 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.stock-in.item-scan` | ERP | pearnly-stock-in 新建明细 | 入库 → 新建 → 明细物料格扫码图标；扫码枪回车 | 填入当前公司商品 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.stock-out.item-scan` | ERP | pearnly-stock-out 新建明细 | 出库 → 新建 → 明细物料格扫码图标；扫码枪回车 | 填入当前公司商品 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.cashier.camera` | ERP | pearnly-cashier 搜索框相机 | 收银台 → 搜索框右侧相机 | 相机有画面，读出码填进搜索框，唯一匹配加入购物车 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.inventory.opening.item-scan` | ERP | 库存 → 录入期初 | 库存 → 录入期初 → 明细商品格扫码 | 填入当前公司商品 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.cashier.search` | ERP | 收银台搜索框（打字 / 扫码枪 / 相机共用的按码找商品） | 收银台 → 搜索框打编码 / 条码；扫码枪扫；搜索结果唯一命中直接进车 | 按当前公司 + 本地认出同一件商品（cashier/counter.py → products.find_product） | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.cashier.scanner-gun` | ERP | pearnly-cashier 扫码枪 | 收银台直接用扫码枪扫 | 商品加入购物车 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.cc.masters` | ERP | Express 主档同步 | 公司页「同步 Express」；定时 advance（每分钟）/ check_marks（10 分钟）/ sync_all（每小时） | 主档、仓库、库存按 Express 对齐；改 Express 主档后约 20 分钟跟上 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.cc.write` | ERP | ERP 单据写 Express | 接 Express 的公司采购 / 销售提交（网页手录、识别、LINE 编辑页） | 预检缺选项当场弹框；写进 C 盘 T 账套并读回一致；关账回原因弹窗 | auto：express | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.line.intake.submit` | ERP | LINE 采购 / 销售卡片确认、编辑页提交（按名称 / 编码认商品与对方） | ERP-TEST 上传单据 → 卡片确认，或「编辑」→ 提交 | 认当前公司的商品与供应商 / 客户，认不到自动建本地档；写进 Express | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.serials.dialog` | ERP | 按台管理：入库 / 出库 / 调拨 / 进货 / 销售收据 / 收银提交前的「登记车辆 / 选择车辆」弹窗 | 商品开按台 → 入库「其他」提交 → 逐行登记车架号（扫码填号）；出库、调拨、销售收据、收银完成订单 → 选在库的台（下拉搜索、扫码认台）；入库「退货」→ 选已售的台；取消弹窗；同一台车放在同仓另一个库位再选 | 台数等于数量才提交；扫车身二维码认到台；选台下拉只出本公司这个车型、这一行仓（写了库位时这个库位）的台（G17）；出台时台不在这一行的仓 / 库位被拒；取消就不提交 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.stock-count.new-product` | ERP | 盘点录入弹窗就地新建商品（Desk）与盘点 Excel 导入的未知码行 | 盘点 → 开始盘点 → 输入系统没有的码 → 新建物料（品名、单位、可选参考成本）→ 记数 → 结束 → 过账；导入表里带品名的未知码行 | 商品建成（码作条码 / 二维码内容、编码自动发、来由「盘点新建 · 盘点单号」），同码只建一件；盘点行来源「新发现」；过账后库存按实盘；接 Express 的公司盘点调整停在「Express 中没有这件商品」 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.line.stocktake.new-product` | ERP | LINE 手机盘点页就地新建商品、改记在哪个库位 | LINE 打开盘点 → 输入系统没有的码 → 填品名、单位 → 新建物料 → 记数；库位一格选清单里的库位 | 与 Desk 同一个服务端函数建商品；记数落在所选库位；清单外的库位按库位清单规则拒 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.stock-count.code` | ERP | 盘点按码认商品 | 盘点录入弹窗输码 / 扫码；上传导入；LINE 手机盘点 | 认盘点仓库所属公司的商品 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.ocr` | ERP | ERP 上传识别（网页 / LINE） | 采购 / 销售上传识别；LINE 发图片 / PDF（付费识别，按小样本） | 识别后预览 / 出卡片 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `cowork.ocr` | Cowork | Cowork 录入识别 / 远端识别 / 对账识别 | 录入工作台上传识别；对账中心单据识别（付费识别，按小样本） | 识别成功、结果入库 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `dms.ocr.id-card` | DMS | DMS 身份证识别 | LINE 拍身份证 / LIFF 扫证 | 读出证件信息 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.originals.download` | 共用 | 原件图片 / 下载原件 | ERP / Cowork 原件页图、「下载原件」 | 跳转并下载正确 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.ocr.pipeline` | 共用 | 识别队列、识别记录与识别服务部署 | 三产品各上传一张（付费识别，小样本）；部署后 claude-ocr 服务健康 | 排队、识别、记录、计费正常 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.stock-count.upload` | ERP | 盘点上传 / 凭证照片 | 库存盘点 → 上传表；录入里拍凭证（电脑选图 / 拍照，手机 LINE 盘点页拍照） | 导入成功；凭证照片先压缩、存进统一原件层（本机只留登记）、挂到这条记录，最多 5 张；别人上传的文件挂不上 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.stock-in.attach` | ERP | 入库单附件（图片、PDF、拍照） | 入库 → 新建（弹窗或整页）→ 附件区「Attach」选图 / 拍照 / PDF → 提交；打开已提交的单再加一张；草稿删一张 | 照片先压缩、存进统一原件层，挂到单上能打开；已提交只能加不能删，作废的单不再加；每张单最多 10 个；别人上传的文件挂不上 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.stock-out.attach` | ERP | 出库单附件（图片、PDF、拍照） | 出库 → 新建（弹窗或整页）→ 附件区「Attach」选图 / 拍照 / PDF → 提交；打开已提交的单再加一张 | 同入库单附件；只有出库能力的岗位能往出库单挂 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.cc.troubleshooting` | ERP | ERP 版 CC 对话与单据页：待处理、单据详情、看原件、补票面合计 | 识别一张缺页的采购单并提交 → 停在「没有票面合计」进待处理；CC erp_pending / erp_document_detail / erp_document_original；erp_set_printed_total 带依据补上，或在单据页直接改票面合计 | 补对后当场重新写进 Express（C 盘 T 账套读回一致）；版本记录与依据可查；已写进账本的单不能改 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.login` | 共用 | 登录 / 退出 / 改密码 | OIDC 登录、退出（不出确认页）、改密码；CC 对话页登录 | 登录跳回原页；退出直接回登录；改密码后别的会话失效 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `control.issue-and-entry` | 控制面 | 开户链接 / 号池 / 智能体入口 | 控制面开户、repool、fatcc.com 入口登录 | 与 main 相同 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.shell-gate` | 共用 | 产品与功能开通闸 | 没买的功能 / 没权限的页面直接打开；CC 对话页功能清单 | 出原生无权限页；功能清单与购买一致 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.team.members` | 共用 | 团队页建成员同步身份服务 | ERP / Cowork 团队页新建成员、改语言 | 成员能登录；语言跟个人设置 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.line.login` | 共用 | LINE 登录（绑定即登录） | 解绑后打开 LIFF；每小时 line_login.reconcile | 解绑后 LINE 会话吊销、LIFF 要重新登录 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.report.stock-summary-qty-sort` | ERP | 收发存汇总 · 按期末数量排序（接口计时） | 以冒烟账号调 api_stock_summary_page（起始日 2026-01-01，按期末数量倒序，每页 20）；大账套用 run.py --company 指定那家公司 | 服务端 3 秒内（第一次算预热不计），返回这一页与总数 | auto：api_time | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.products.new-dialog.opening` | ERP | 物料 → 新建弹窗 / 整页的「期初库存」（多仓闭环 P1 G1） | 新建商品，期初库存填两行（两个仓，各带库位、数量、成本）→ 提交；整页一行带期初；接 Express 的公司不出现这一节 | 商品与期初同一事务建成（库存一览两个仓各有数、收发存有 OPN）；任何一行不成整件商品都不建；库位不在该仓清单被拒 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.line.chat` | ERP | LINE ERP-TEST 一对一 | 三星 adb：关注、绑定码、解绑词、富菜单 ซื้อ/ขาย、เมนู、1/2、每步快捷回复、打字找公司 / 供应商 / 客户 / 仓库、上传图片 / PDF、乱发文字 | 马上能回的不出三个点；慢的先出三个点、回复到达时消失、不出现在回复之后；回复与按钮同 main | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.line.binding` | ERP | 团队页绑定码 / 解绑 | 团队页生成绑定码 → LINE 绑定；解绑词；删除 / 停用成员 | 解绑后再发消息提示未绑定 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.exports` | ERP | ERP 下载 | 班次导出、任务管理导出、采购 / 销售 / 库存导出、盘点导出 / 模板、期初模板、商品导入模板、收银小票 | 文件名中文 / 泰文不乱码，能打开 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `cowork.exports` | Cowork | Cowork 下载 | 银行 / 收入对账、销项税核查导出；盘点导出 / 模板；任务管理导出；原件下载；工作台导出 | 文件名正确、能打开 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.masters.scope` | ERP | 主档按公司隔离 | 物料 / 客户 / 供应商列表、搜索、计数、Link 搜索、打开单条；切换公司前后各看 | 只出当前公司 + 本地；直接调别家被拒（需要已读权限 / 未找到），不是 403 白页 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.records.scope` | ERP | 单据 / 库存 / 收银按公司 | 收银台商品区、班次、终端；库存总览 / 库存卡 / 出入库 / 期初 / 盘点 | 只含当前公司 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.execution` | 共用 | 共用：登录会话、LINE 会话、识别、原件、会话锁 | 登录；LINE 交互；上传识别 | 照常 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.browser-text` | ERP | ERP 导出 / 导入 / 报表文案 | 收银小票、库存导出、期初导入、商品导入、盘点差异报表、LINE 盘点页 | 三种语言文案正确 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `erp.serials.export` | ERP | 商品 → 编辑 → 按台记录 / 按台期初登记 | 按台记录 → 导出；有库存的商品勾按台管理 → 下载模板 → 填号上传 | 导出与模板表头按站点语言（车：车架号 / 发动机号 / 颜色）；台数对上才开按台 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
| `shared.scan-codes.web-address` | 共用 | 扫到认证网址二维码（TIS 码等） | ERP 盘点录入弹窗、LINE 手机盘点、物料码（认码、建码、导入）、Cowork 盘点录入弹窗：扫到 / 输入 https:// 开头的码 | 提示「这不是商品条码」，不查、不出新建；扫码器继续开着、先跳过这个码；普通条码照常认 | manual | 没测到：本次在命中的共用件文件里只删了没有调用方的函数（diff 只有删除行与说明），这个入口调用的代码一行未改；以调用方核对与本地闸门代替 |
登记表完整：56 个共用件、205 个入口。

## 6. 闸门精度修正：规则 6 的「改到它调用的接口」按函数算（主会话 11-05 定）

- 起因：删 `inventory/api.py` 里没人调的接口，整文件算命中，碰上只能真机验的 LINE 盘点相机行，要求附本次 SHA 的真机记录；
  页面实际调的接口一行没改。主会话定：旧真机记录不算数，也不为删没人调的函数重跑真机（测试机正在给 CC 1.12.14 发版实测），按类修闸门精度。
- 做法（`scripts/impact/product_map.py` 的 `function_changes` / `changed_functions` / `deleted_whitelisted`，`scripts/check-product-map.py` 的 `impact_rows`）：
  `git diff -U0` 的行号对到基线与工作区两边的 ast 顶层函数范围（含装饰器），空行与纯注释行不算；只有入口调用的那个接口函数有 diff 才命中。
  拿不准照旧按整个文件命中：改到模块级代码（import、常量、类、顶层语句）、改到前后都在的函数的装饰器、新建或删掉的文件、解析不了、取不到 diff、`--files` 模式。
  删掉的白名单接口：页面文件（或同目录文件）还写着这个模块、又提到这个名字的行照样命中。
- 自测（`tests/test_check_product_map.py::test_rule_4_an_endpoint_counts_as_changed_only_when_its_own_function_has_a_diff`）：
  删同文件里别的函数不命中；改被调用函数的函数体命中；改 import / 装饰器 / 模块级常量按整个文件命中；只加注释不算；新文件整个算；
  页面还在调的接口被删命中、别的模块同名不算。
- 本分支的结果：`inventory/api.py` 按函数算只剩被删的接口，LINE 盘点相机行不再命中。删接口后 `openings` 这行导入已经没用，但删 import 属于
  「拿不准照旧命中」，会让那一行重新命中，所以**这次保留这行导入**，下次这个文件有要真机验的改动时一起删。
- 中间发现：任务记录里只要同时写着行 id 和任意一个 SHA，就会被当成真机记录（11-05 本记录初稿就这样误过了一次，已改写）。
  记录检查只认「行 id + 日期 + SHA 同在一份改到的记录里」，没有核 SHA 是不是本次；这一条没改，列为后续（见第 8 节）。

## 7. 检查

基线 `56c1807ed`（施工中 origin/main 前进了，合入前变基后重跑闸门）。

| 层 | 做了什么 | 结果 |
|---|---|---|
| 本地全闸门 | `~/pearnly-devtools/gate.sh`（照 development.yml 的 186 步） | 186 步全过（前端几步要先在工作树 `npm ci`） |
| 产品地图 | `check-product-map.py`；另把任务记录挪开再跑一次，确认不是靠记录里碰巧的行 id + SHA 过的 | 通过；不靠记录也通过 |
| 候选离线检查（改了 pearnly_core） | `cand-local.sh offline`，生产镜像 `sha256:29ae8ca6`（11-04 那批），日志 `~/pearnly-devtools/cand-drop-dead.log` | 通过 |
| 改动文件的静态核对 | 每个改动的 .py：ast 列出「基线有、现在没了」的顶层函数 / 常量，逐个对照本记录；「引用了却没定义的名字」扫描 | 只剩本记录里列的删除；没有未定义的名字 |
| 流程 `erp.stock.excel-units` | 自起的一套回归实验栈 `pearnly-dropdead-lab`（共用的 `pearnly-regression-lab` 当时被预选卡片窗口占着，没碰） | 第一次**不通过**，抓到真问题：删 `api_stock_count_import` 时切分规则把紧挨着的 `_stock_rows`（建盘点带表要用）一起切掉了，页面「上传导入」会报 NameError；恢复后通过（old / desktop） |
| 改动涉及的全部流程 | `run.py --changed --base 56c1807ed`，同一套自起的实验栈，结果 `~/pearnly-devtools/flows-drop-dead-changed.json` | 22 条流程、两套数据、81 格全部通过（1206 秒） |

误删的教训：按「下一个三连换行」切函数，遇到两个函数之间只空一行就会多切；之后改成删完用 ast 逐个核「少了哪些顶层名字」。



