# 一公司多仓库闭环 + 盘点就地新建：施工记录

方案与口径：[需求影响评估](2026-10-26-multi-warehouse-closed-loop-评估.md)（12 节主会话定的 8 条；13 节缺口、修法与分期）。
分支 `pearnly/multi-warehouse-p0`，基线 `origin/main` 1dabbd06（测试包 e 合入后）；评估、8 条口径与原生样本（调拨 RL、行备注，
`2025-10-25-express-native-rules.md` 4.6b / 4.9）三个提交从 `pearnly/stock-count-unknown-assess` 带进来。

## P0 卫生项

| 缺口 | 改法 | 用例 |
|---|---|---|
| G13 差异报表「只看」失效 | 「只看」「图表按」两个 Select 改官方 `{value, label}`：交给服务端的值固定（counted / uncounted / differences），显示跟界面语言；按类全仓查：脚本报表只有这一张，盘点工作台的「只看」已是 value / label，新建盘点的「方式」只在前端比较、没交服务端 | `tests/test-report-select-values.cjs`（进 CI：脚本报表的 Select 筛选不许拿译文当值；修前版本被拦） |
| G16 停用仓不查库存 | `warehouses.update_warehouse` 停用前按库位口径（`locations._stock_at`）查结存，有货拒「有库存的仓库不能停用」；不放进 DocType validate——Express 主档同步会自动停掉 Express 已没有的仓，不能被拦 | `test_erp_locations`：有货拒、没货照停、只改名不核 |
| G16 收银台可绑停用仓 | `ERP POS Terminal.validate`：新建或换仓时仓必须启用，拒「该仓库已停用」；仓后来停用的旧收银台不换仓照样能存 | `test_erp_locations`：启用仓过、停用仓拒、新建 / 换仓拒、没换仓照存 |
| G17 按台期初 Excel 找仓跨公司 | `serials.parse_workbook` 不再按仓名跨公司先找一遍（两家都叫 Main 时取到别家的仓、随后被拒），仓名原样交给开按台时的 `warehouses.required`，在本公司里按名称或编号认 | `test_erp_serials`：解析期初表不跨公司查仓（修前失败） |
| G17 打开按台跨公司汇总 | 主会话 10-26 定：数据口径不改（按台是商品级开关，共用商品各公司都有账，开之前全部公司都要登记到台），只改提示并守公司隔离——开不了时原生 msgprint 逐个列看得到的「仓 · 公司：已登记 / 账面」，看不到的公司合成一句「其他公司还有未登记的库存」，不露名称与数量；打开按台前的各仓账面表、期初模板同样只列看得到的仓（原来账面表露别家数量、模板写别家仓名）。按公司开按台记为后续项 | `test_erp_serials`：提示只列看得到的仓、别家合成一句；账面表别家合成一行不带数量 |
| G15 推送遇无码仓 | 采购 / 销售（`push.document`）、收银日汇总（`pos_day`）、出入库单与盘点（`stock_entry.document / count_document`）列出库存行上 Express 里没有仓库码的仓；`push.send` 在写账规则准备好之后（主档已读、仓库码已同步）停下交人 `express.warehouse_unmapped`（「仓库在 Express 里没有对应的仓库」），不再不带码、由 CC 桌面版静默写进 Express 主仓；主档还没读到时照旧先等读。处理：Express 建好同名仓、主档同步认领后「再写一次」，或手工处理后标记已处理；P1 建仓选 Express 仓后此路径变少 | `test_erp_express_direct`：`contracts.unmapped`；主档未读先等读、读到后停在 warehouse_unmapped、不排写账件、不交给共用规则；出入库单无码仓列出 |

新文案（新增 4 条，`check-copy` 过闸）：有库存的仓库不能停用 / 该仓库已停用 / 仓库在 Express 里没有对应的仓库 / 其他公司还有未登记的库存。

协调（主会话 10-26）：代录窗口（`pearnly/express-operator`）同期改 `express/push.py` 的 `send`（抽出「准备不入队」）。P0 先过闸、验收、合入，
代录窗口之后变基，把 `unmapped_warehouses` 那个停下交人的分支挪进它抽出来的函数；代录的单同样受「仓库没有 Express 码就停」约束，不能绕过。

## P0 检查（10-27，最终内容 fc9bc5e9）

- 本地同名闸门 132 项全过；候选离线检查（生产镜像 fb1037ad）通过；分支 Development checks 通过。
- 实验栈（REFRESH，挂 fc9bc5e9；只写实验库，不写 Express）：
  - G16：有货的仓 WH-00107 停用被拒「有库存的仓库不能停用」；新建的空仓停用成功；收银台换绑到停用仓被拒「该仓库已停用」，新建收银台
    指向停用仓被拒，旧收银台改名照样能存；
  - G15：在新建的、没有 Express 仓库码的仓里做采购单和入库单 → 都停在 `express.warehouse_unmapped`，没排写账件；
  - G17：打开按台前的账面表只列看得到的仓（「仓 · 公司」）；登记数不够时提示逐个列「仓 · 公司：已登记 / 账面」；期初表里写
    「คลังสำนักงานใหญ่」（另有 35 家公司同名）按名字认到本公司的 WH-00107；
  - G13：差异报表按固定值筛，STC-00039 全部 4 = 已盘 0 + 未盘 4，差异 0（报表路由按原设计转盘点工作台，不是用户直接入口；P3 改造）；
  - 详情页冒烟（实验库先造草稿 / 作废单据）8 个入口全过。
- 连带回归（`run.py --diff`，35 个入口）：自动入口通过——库存一览、入库、出库、库存卡、盘点、采购、销售、小票、商品各页，
  ERP 采购写 Express（HP691027-001）、出入库单写 Express（OU691027-001）、商品新建对话框扫码；详情页 8 个补造数据后全过（第一轮
  purchase.draft 一次「frappe is not defined」是页面还没载入就跑了探针，补跑通过）；「按码找商品」不通过是实验库 PN00003 两家同码
  的老问题。人工入口：
  - erp.serials.dialog：服务端（账面表、开按台提示）实验栈实测如上；对话框只改了「别家合成一行时数量格空着」，没点；
  - erp.cc.pos-day / erp.cc.pos-refund：日汇总只多列无码仓，仓有码时不触发（单测覆盖）；测试包 e 刚实测过；
  - erp.cc.stock-void、erp.entry.choices、扫码 / 图片 / 导入、serials.export、差异报表页：本期没改这些路径，不受影响。

## P0 合入（10-27）

验收复核通过，合入 main 5c866e1c；已通知代录窗口变基（`send` 里停下交人的分支挪进它抽出的函数，代录单同样受约束）。
验收的两条低优先意见并入 P1（见下）。

## P1（分支 `pearnly/multi-warehouse-p1`，叠在 P0 上）

主会话排定：native-buttons、打印第二批改 purchase-form / sales-form / stock-entry / stocktake / transactions.js，先合；P1 先做服务端，
前端等那两个合入后变基再做。**服务端「不再兜底」必须与前端预填一起上线**（否则现有前端在多仓公司里建单被拒），P1 整期一起合。

服务端已做：
- `warehouses.prefill(kind, company, context)` + `api_prefill`：来源（收银台 / 来源单据）→ 本人上次（Frappe 原生 user default
  `pearnly_last_warehouse`，按「单据类型:公司」记，问 2）→ 卖货仓（只给进货类：采购、入库、期初，问 1）→ 唯一启用仓 → 空（必须选）；
  `remember()` 在采购 / 销售过账、人建的出入库单、期初提交、建盘点时记。`receiving()` 收敛到 `required()`：没写仓且多仓就拒，不落卖货仓。
- 期初表加仓库列（空着 = 表头仓，按名称或编号在本公司里认，提交时锁住重读）；同一件商品不同仓各算一个位置；库位在预览就按这一行的仓的
  清单核（原来提交才整批失败）；审核签名带上仓。
- 建商品带期初：`create_product(opening=[{warehouse, location, qty, unit_cost}])`，商品与期初同一事务（`opening_batch.with_product`，
  同一套「每个位置只初始化一次」），任何一行不成整件商品不建；接 Express 的公司期初服务照样拒。
- 接 Express 公司的仓：方案写的是「建 / 改仓时必选一个 Express 仓」。核代码发现主档同步本来就按 Express 仓库码给**每个** Express
  仓建 / 认领一个 ERP 仓（同名的无码仓会被认领）——ERP 里新建的仓一定是 Express 没有的，也没有剩下可选的 Express 仓。所以落成：
  接 Express 的公司**不在 ERP 新建仓**，拒「请在 Express 中建仓库」，同步后自动出现（同方案「清单里没有 → 提示先在 Express 建、
  同步后再选」的意图）。
- 验收低优先 1：停在 `warehouse_unmapped` 的单，主档同步（`masters.apply`，可能刚认领了新仓库码）之后这家公司的这类单自动重排，
  下一分钟 `poll` 再交，仍没码再停（`push.requeue_unmapped`，`push.py` 新增函数，不在 `send` 里）。
- 验收低优先 2：按台期初模板只列当前公司的仓（开按台时仓名在当前公司里认），不留别家同名的歧义行；几家公司都有账的商品开不起按台，
  记入「按公司开按台」后续项。

服务端检查（6c92f4e8）：用例 test_erp_locations / test_opening_batch / test_erp_serials / test_erp_express_direct 全过；候选离线检查通过
（第一次 137 是本机同时跑着别的窗口的实验栈、进程被杀，重跑通过）。**合入前还要跑本地 ERP 实验室（`pearnly-erp-pos-lab`，
`infrastructure/compose/erp-pos-lab.yml`）的在库用例**：`erp-opening-lab-verify.py`（含新加的「建商品带期初、期初表仓库列」）、
`erp-pos-lab-inventory-verify.py`、`erp-pos-lab-purchasing-verify.py`、`erp-pos-lab-sales-verify.py`、`erp-entry-lab-verify.py`、
`erp-data-flow-lab-verify.py`——服务端不再兜底会碰到依赖卖货仓的老流程，离线检查不跑这些。

前端待做（等 native-buttons、打印第二批合入）：采购 / 销售 / 出入库 / 期初 / 盘点的单头「仓库 + 库位」共用控件（行继承、可展开逐行改，
选商品不再动行仓、库位只在商品建议库位属于该仓清单时预填）、删 `frappe.boot.pearnly_warehouse`、建商品对话框 / 整页 / Excel 的期初网格、
期初页的仓库列、仓库页对接 Express 的公司隐藏「新建」；新按钮 / 控件按 `data-pearnly-action` 声明登记。

### P1 前端（10-27，native-buttons、打印第二批合入后变基到 ab823298 开工）

- **单头「仓库 + 库位」共用控件**（`transactions.js` 的 `p.place`）：采购、销售、入库、出库同一套——仓库 Link（当前公司启用的仓）、
  库位 Autocomplete（按这个仓的清单）。新单按 `warehouses.api_prefill` 预填（本人上次 → 卖货仓（只给进货类）→ 唯一仓 → 空 = 必须选）；
  改单头：还跟着单头的行一起换（行上仓空着或等于旧单头仓的换仓并取新单头库位；同仓里库位空着或等于旧单头库位的换库位），
  逐行在「附加信息」里改过的行不动；换仓时单头库位清空。选商品**不再改行仓、不再硬塞商品建议库位**（服务端 `positions.prepare` 只在
  建议库位属于该仓清单时补）。老单没有单头时从明细推出（有货的行同仓 / 同库位才当单头）。
- **单头存在单上**：`Purchase Document` / `ERP Sales Document` 加 `warehouse`、`location`，`ERP Stock Entry` 加 `location`（仓原来就有）。
  服务端 `positions.header()` 核仓属于本公司且启用；`positions.prepare(line, 单头仓, location=单头库位)`：行上没写仓的用单头仓，没写库位、
  又落在单头仓的用单头库位（单头写了就不拿商品建议库位顶掉）。LINE / 识别收单不带单头时照旧按行。**打印窗口要的字段名**：采购 / 销售单
  `warehouse`（Link ERP Warehouse）+ `location`（Data）；出入库单 `warehouse` + `location`。
- 盘点新建弹窗、期初页表头仓：同样按 `api_prefill`（`count` / `opening`）预填。期初页每行加「仓库」（空着 = 表头仓；Excel 导入认出的仓名
  换成仓）。期初与盘点**不加单头库位**：期初行本来就逐行写库位；盘点范围按库位是 P3 的事。
- 删掉启动数据 `frappe.boot.pearnly_warehouse`（`masters/boot.py` 与全部前端用法；登录时算一次、切公司不跟）。
- **建商品带期初**：新建弹窗加可折叠「期初库存」网格（仓 / 库位 / 数量 / 成本，多行），整页每行在展开里带一组期初；交
  `create_product(opening=…)`，与商品同一事务。只给能录期初的人、接 Express 的公司不出现（`p.express_company()`，服务端同样拒）。
  **Excel 不另加期初列**：商品导入表照旧只建商品，期初走库存页期初表（已有 Excel、现在带仓库列），避免两份期初表格式。
- **仓库页**：接 Express 的公司不出「新建」（主按钮、空列表、别处选择框点「新建」跳来的都不出；跳来的说服务端同一句「请在 Express 中建仓库，
  再在公司页点「同步 Express」」）。
- 新增文案只有一条：`product.field.opening`「期初库存」（四语）。没有新的页头按钮（单头两格、期初网格都是表单字段），`hooks.py` actions 不变。

### 两条存量缺陷（验收 10-27，主会话并入 P1，按类修）

1. **页内切换记录不清页头菜单**。根因：原生 `page.add_inner_button` 同时往「⋯」菜单加一项（窄屏用，`hidden-xl`），`clear_inner_toolbar()`
   只清工具栏、不清菜单；盘点页切单时只清了工具栏和主按钮，上一张的「导出」「结束盘点」留在菜单里。修：core `p.clear_actions(page)`
   一次清工具栏、菜单、主 / 次按钮、自定义按钮、图标；`p.clear_page` 走它；ERP 盘点、Cowork 盘点、Cowork 对账三个自己清页头的页面改用它
   （其余产品页都走 `p.clear_page`，已覆盖）。
2. **无权限打开详情页白屏**。根因：详情接口 403 抛进路由重绘，没人接，页面停在空白、控制台 `Uncaught (in promise)`。修：core
   `p.not_permitted(error)` 认出 403 / `PermissionError` → 收起请求层那个关掉会跳回上一页的弹窗 → 换成框架原生无权限页
   （`frappe.show_not_permitted("pearnly-not-permitted")`，单独的消息页，不毁掉本页容器）；`p.routed_page`、`p.register` 的首次渲染、
   自己按路由重绘的页面（`p.guard_route`）都接上；其它错误照常抛出。
- 测试：`tests/test-desk-place-and-switch.cjs`（接进 CI）：清页头范围、无权限页与不吞别的错、`p.place` 跟随规则、老单推单头、预填走接口、
  按类静态扫描（不再有只清工具栏不清菜单的写法、自建路由页都包 `guard_route`、前端不再读 `pearnly_warehouse`）。
  `test_erp_locations` 加单头仓 / 库位给行当默认。
- 冒烟基线：新探针 `record_switch`（打开 A 状态再页内切到 B 状态，页头 + 菜单与 B 一致、无 A 的残留）、`not_permitted`（受限账号打开别人
  建的单：原生无权限页、无未捕获错误；受限账号由 `SMOKE_USER_ERP_LIMITED` 给，没配记「没测到」；单号由 `scripts/smoke/pick_doc.py`
  在容器里只读挑）。登记入口：`erp.stock-count.switch.*` 两条、`erp.purchase.detail.not-permitted`、`erp.sales.detail.not-permitted`（auto），
  `cowork.stocktake.switch`、`erp.place.header`、`erp.products.new-dialog.opening`、`erp.inventory.opening.row-warehouse`、
  `erp.warehouses.express-new-hidden`（manual）。

### P1 本机实验栈实测（10-27，compose 项目 `pearnly-p1-lab` = erp-pos-lab + erp-entry-preview，只挂本工作树，Frappe 16.33.0）

Site A（预览合成数据）造数：ERP 预览一店 CO-00033 主仓 WH-00034、二号仓 WH-00044（库位清单 A1 / A2）；盘点 STC-00045（置为已过账）、
STC-00046（进行中）；管理员建的采购单 PUR-2026-00032。浏览器用 `scripts/smoke/drive.mjs`（本机无头 Chrome + 会话 cookie）。

| 场景 | 改动前（ab823298） | P1 |
|---|---|---|
| 盘点页 已过账 → 进行中（`record_switch`） | 不通过：残留「导出」 | 通过：打印、结束盘点、开始盘点 |
| 盘点页 进行中 → 已过账 | 不通过：残留「结束盘点」 | 通过：打印、导出 |
| 只有采购岗的账号打开别人的采购单（`not_permitted`） | 不通过：白屏 + `Uncaught (in promise)`（product.js:810） | 通过：原生无权限页 `pearnly-not-permitted`，控制台无报错 |
| 新建采购单：单头预填 → 改单头仓 → 填单头库位 | — | 预填卖货仓（一店仓库）；换二号仓后明细跟着换、单头库位清空；单头库位 A1 后明细 A1 |
| 服务端：采购单头二号仓 A1、明细不写仓 | — | 单头存 WH-00044 / A1，明细 WH-00044 / A1，二号仓 A1 入 3 件 |
| 多仓公司单头、明细都不写仓 | — | 拒「Pick a warehouse」 |
| 再建采购单的预填 | — | WH-00044（reason last） |
| 销售单头二号仓 A1 | — | 单头与明细一致 |
| 入库单头二号仓 A2 / 单头库位不在清单 ZZ | — | 明细 A2、详情接口回 location A2 / 拒「不在库位清单」 |
| 新建商品弹窗 | — | 有「期初库存」网格，新行仓预填 WH-00034 |
| 期初页 | — | 表头仓预填一店仓库，每行仓库格空着 = 表头仓 |
| 建商品带两个仓期初（服务端） | — | WH-00034 5 件、WH-00044/A2 3 件；库位 ZZ 不在清单 → 整件商品不建 |
| 接 Express 的公司（主会话 10-27 要求合入前必须测到，见下节） | — | 通过：页面不出「新建仓库」、接口拒、文案对、同步落新仓 |

**接 Express 的公司（10-27 补测）**：实验站点按正式方式标成已接——公司更新接口设 13 位税号 + 分店，再走 CC 桌面版心跳报账套名单的
同一入口 `pearnly_core.cc_desktop.books.replace`（假路径、假电脑），`express.push.linked()` 从否变是，启动数据当前公司带 `express_linked`。
夹具做成脚本 `scripts/erp-express-linked-lab-fixture.py <站点> <公司名> <管理员>`（只在 ERP / POS 实验室或回归实验室容器、`*.localhost`
站点上跑；每家公司一个过校验位的合成税号、一台假电脑），回归实验室可直接重放。结果（ERP 预览二店 CO-00036；夹具四店 / 五店重放同样通过）：

| 项 | 结果 |
|---|---|
| 仓库页（当前公司 = 接了 Express 的二店） | 页头没有「新建仓库」，列表是同步来的仓；对照一店（没接）有「新建仓库」 |
| 直接调 `api_create_warehouse`（带 company） | 二店 HTTP 417「请在 Express 中建仓库，再在公司页点「同步 Express」」（英文 Create the warehouse in Express, then use Sync Express on the company page）；一店 200 照常建 |
| 公司页「同步 Express」（`masters.start`） | 没有在线电脑：回 `offline`，不落库 |
| 离线夹具代替电脑读回的主档 → 读回后落库的同一个函数 `masters.apply` | 原有的仓认作 Express 主仓 01；Express 新建的仓 02 出现在 ERP（启用），仓库页列出 |

没测的一处：别处仓库选择框点「新建」跳到仓库页时弹同一句提示（`p.take_create` 一次性路由），代码路径与页头判据同一个 `p.express_company()`。

在库用例（Site B）：`erp-opening-lab-verify`、`erp-entry-lab-verify` 通过。`erp-pos-lab-inventory / purchasing / sales-verify` 与
`erp-data-flow-lab-verify` 在**改动前的 ab823298 上同样失败**（同一实验栈切源码对比：采购用例两边问题项完全相同的 7 项；库存用例调了已删的
`create_entry(doc_no=…)`；销售用例依赖的 owner 账号被前一套删掉；数据流用例调了 09-24 就删掉的 `create_product(default_warehouse=…)`）——
都是脚本过期，不是本次引起的，记入后续项。

### 验收复核（10-27，a3db0b6e，回归实验室）与修复

验收：2 条应修、4 条建议，其余通过（单头带明细、拒绝路径、预填、建商品带期初、接 Express、两条存量缺陷；数据在
`~/pearnly-devtools/regression-baseline/2026-10-27/p1/`）。已按条修复（变基到 native-routes 合入后的 main 6c8860b9）：

1. **单头库位不核清单**（应修）：销售单头写 W1 + ZZ-99 被收下、从不存在的库位出货。`positions.header()` 对单头库位按这个仓的清单核
   （人这一次明写的，进出都核）；行上带着的旧库位照旧放行。实验栈：销售单头 ZZ-99 → 拒「不在库位清单」。
2. **`requeue_unmapped` 漏推**（应修）：公司条件放进查询（采购 / 销售 / 日汇总按 `company`，出入库 / 盘点按 `warehouse in` 这家公司的仓），
   只重排还能写的单（`status = ready`，作废的停单不动，即建议 4），一批 100 张改完不再命中、取到空为止。用例：别家更早停着 130 张、
   这家 105 张 + 出入库单 1 张 → 这家 106 张全排上，别家、别的原因、作废的不动。
3. 出入库草稿只换仓不给库位：单头库位清空（不把旧仓的库位带过去）。实验栈：换仓后 location 空；换回并给 A2 → A2。
5. 建商品弹窗期初网格「数量」「成本」标必填（同期初页；服务端没有成本整件不建）。
6. 接了 Express 的公司在别处选择框原地「新建仓库」：不开新建表单，直接弹服务端同一句提示（`warehouses.js` 的原地编辑器入口）。
   实验栈：二店（已接）→ 只弹「请在 Express 中建仓库，再在公司页点「同步 Express」」；一店（没接）→ 照常开「新建仓库」。
- 存量小事（不在本批）：同一个「库位」采购 / 销售显示 __("Location")，出入库单头是 `trade.location`，两种译文。

旁见（不在本任务范围，记作后续项）：`tests/test_stock_opening_ui.js`、`tests/test-stock-entry.cjs`、`tests/test-product-multi.cjs`、
`tests/test-product-scan.cjs` 在改动前（变基后的 HEAD）就全部失败，也不在 CI 里——是过期的页面测试，要么修要么删；
上表四套实验室在库用例同样过期。

## 暂停点（10-26 晚，用户指示全部窗口就地暂停；10-27 已按此继续）

- P0 六项代码与用例都已写完并提交在本分支（G13、G16 两项、G17 两项、G15），本地用例全过；闸门（132 项）跑在 G17 提示改动之前的内容上，
  候选离线检查跑在混合内容上——**都要在最终内容上重跑**。没推 main、没交验收、测试机没占用。
- **明天第一步**：在最终内容上重跑本地同名闸门（`~/pearnly-devtools/gate.sh`）+ 候选离线检查 → 推分支等 Development checks →
  实验栈实测 P0（差异报表「只看」三种值、停用有货的仓被拒、收银台绑停用仓被拒、按台期初 Excel 同名仓、打开按台的提示与账面表、
  无码仓的采购 / 出入库 / 收银日汇总停在 warehouse_unmapped）→ `check-impact` 受影响入口实测 → 交验收（只看差异）→ 合入 main →
  告诉代录窗口变基（把停下交人分支挪进它抽出的函数）。之后开 P1。

## P2 + P2x（10-30 起，分支 `pearnly/multi-wh-p2-ca3eab`）

范围：调拨单（G6，出入库单第三个方向 `transfer`）、CC 写 Express 调拨 RL 与作废（G7）、G17 后两项（选台按仓 + 库位过滤、出台核库位）。
施工评估见 [P2 评估](2026-10-30-multi-warehouse-p2-transfer-评估.md)，主会话 10-30 审定（12 节自定各点同意；同仓不同库位移动记为后续项）。

### 施工（基线 origin/main d22b7b2b）

- **调拨 = 出入库单第三个方向**：`ERP Stock Entry.direction` 加 `transfer`，表头加 `to_warehouse` / `to_location`，明细加 `to_location`；
  新流水类型 `transfer_out` / `transfer_in`；`stock.apply_transfer` 一个事务内先出后进，调入按调出那一笔的每基本单位单价入
  （`apply_movement(base_unit_cost=…)`）。调出那边成本未确立时调入不带价，按调入位置现有均价入（同退货入库口径），调入位置也没有成本才同样未确立
  ——评估里写的「未知随未知」照这个实现：调入仓已有均价时不会被改成未知。服务端拦：调入仓必填、同公司启用、不能同仓（同仓不同库位也拒）、
  调入库位按调入仓清单核。作废走现有逐条冲回；单号 `TF` + 佛历年（第四个库存单据定义）；不带单价、没有用途、不出打印（打印上下文对调拨拒打）。
- **权限菜单**：能力 `stock.transfer`（别名 `stock.write`），菜单键 `stock-transfer`（团队管理可单独勾），侧栏「货品与库存」下单独一项
  `pearnly-stock-transfer`（页面在 ERP 自己的 App，图标 arrow-right-left）；列表 / 详情 / 附件按 `stock.<方向>` 判，加了调拨；附件方向 `stock_transfer`。
- **按台**：`serials.transfer` 核选中的台在调出仓 + 库位在库，换到调入仓 / 库位，记 `transfer_out` / `transfer_in` 两条按台流水；作废先核每台还在调入位置
  （已卖出 / 出库 / 再调走整单拒），再退回调出位置。
- **G17**：出台（销售、收银、出库、调拨）核台的仓与库位都等于这一行；选台下拉与扫码认台按这一行的仓（行上没写用单头 / 收银台的仓）过滤，
  这一行写明库位时再按库位过滤（`search_units` / `api_lookup` 加 `location`）。
- **页面**：出入库同一份 `stock-entry.js`，调拨多一组「调入仓库 / 调入仓位」（`p.place.fields` / `track` 加 `keys`），明细每行多「调入仓位」
  （`lines_editor` 的 `to_warehouse` / `to_location`），只读明细多一列；单头标签「调出仓库 / 调出仓位」。
- **Express 推送**：调拨 → RL（`express/stock_entry.py`）：行上带调出仓码、单头 `to_loccod`、不带价；两仓任一没码列进 `unmapped_warehouses`
  停下交人；不问科目；CC 报了核过 RL（`stock_books_checked`）而账套没有 RL 簿才停 `NO_STOCK_BOOK`，旧版没报的交给电脑核。
  载荷版本 `TRANSFER_PAYLOAD_VERSION = 14`（作废 RL 同号；13 留给 express-rules-vat，合入时按 main 当时最大值 + 1 再定）。
- **CC**：新模块 `dbf_transfer.py` 照 4.6 / 4.6b 写 RL；10-30 从测试机只读拷回原件副本 `s12-before / rl2 / rl3 / rl3v` 逐栏核过：ISRUN 那一行
  DOCTYP / PREFIX 都是 RL、没有科目、日记账空；没有凭证；STMAS 不动；STLOC 只动 LOCBAL 与当期数量栏，**不动日期栏和金额栏**，调入仓新建的行
  没有分类（STKCLASS 空）；STCRD 的 STKDES 有单头说明时是说明、没有时是品名（RL0000001）；作废时那一对只打删除标记、NETVAL 不写、
  单头 C + NXTSEQ 空。与样本不同、有意不照做的：MLOTNUM（Express 自己的先进先出批次链，录别的单时还会重排旧行）、MREMBAL / MREMVAL
  （样本里是 Express 批次链上的数，不等于主档结存）——CC 不写批次、MREMBAL 记主档结存，同 CC 写的其他库存单，交给 Express「计算成本」。
  `stock_location` 加 `KIND_TRANSFER`（不动日期 / 金额栏、新行不带分类）；`dbf_void` 认 RL（成对核、只回滚两仓、不动主档与凭证）；
  `express_masters` 报 RL 单据簿与 `stock_books_checked`；worker 按 `doctype = RL` 派给 `write_transfer`；`payload_contract` 加 14。
- 流程用例：新 `erp.stock-transfer`（`scripts/flows/erp_stock_transfer.py`，库存岗、两套数据 × 电脑 / 手机：页面建调拨、同仓被拒、提交；
  库里核 TF 号、一出一进同单价、两仓数量与合计金额、作废回原；按台在服务端调一台车、同仓拒、作废回 A）；sales-form 的选台改动由 main 上的
  `erp.sales.scan` 覆盖（10-31 变基后删掉了本期自写的重复流程 `erp.sales.receipt`）；`erp.cashier.first-sale` 加 covers 收银适配器。回归数据库存岗加「调拨」菜单。
- 登记表：`erp.page.stock-transfer`（auto）、`erp.cc.stock-transfer`（manual）；`erp.serials.dialog` 步骤加调拨与按行仓 / 库位。

### 验证（10-31 续做）

| 层 | 结果 |
| --- | --- |
| 离线单测（ERP） | `test_erp_serials` 16 过（新增调拨换仓 / 作废回退 / 台已卖出或再调走拒 / 出台核库位）；`test_erp_express_direct` 113 过（新增 RL 载荷、两仓码、无码停下、只在报过 RL 时判没有单据簿）；`test_companion_contract` 81 过（新增调拨版本与键两边一致）；`test_native_desk_contract`、`test_document_numbering`（顺手修了 main 上就过期的前缀断言）、`test_erp_locations`、`test_erp_printing`、`test_erp_stock_attachments`、`test_erp_portal_access` 照 CI 方式单跑都过 |
| 离线单测（CC） | 全量 1237 过、42 跳过（新增 `test_dbf_transfer.py` 13 项：原生单头、成对明细、STLOC 新建行不带日期 / 金额 / 分类、STMAS 不动、无凭证、ISRUN +1、作废、改过的单不作废、坏载荷不动账、仓码不在、没有 RL 簿、未知商品、负库存） |
| 变基 | 10-31 变基到 origin/main b7db199f：`registry.toml` 冲突按 main 收，本期只补 `erp.stock-transfer` 与收银适配器 covers；`erp_stock_entry_line.json` 与 main 去批次那次同一个 modified，改成 `2026-10-31 09:00` 让迁移认得出 `to_location` |
| 检查 | 本地同名闸门（照 development.yml）：152 步全过（5cf478fc；最终 1514fa1b 再跑一遍也是 152 步全过，中途补了 `test-desk-place-and-switch` 的 `put` 用例与库存卡流程覆盖） |
| 实验栈（真站点） | 起了本窗口自己的栈 `pearnly-mwh-p2`（端口 18797–18799）。第一次跑在建 old 数据集时就抓到本期缺陷：`stock.apply_movement` 函数体用了 `base_unit_cost`、签名里没加，**所有库存流水都会 NameError**（离线单测没走到真函数）；已修（b38f92fe），pyflakes 扫本期改过的全部 Python 没有别的未定义名字。流程结果见下一行 |
| 流程用例 | 本机栈 `pearnly-mwh-p2` 上 `run.py --changed` 跑了 4 轮。第 4 轮在 3750a362（变基到 0836f929 后）：**30/31 通过**，只有 `erp.stock-entry.scan` 旧数据 / 手机那格账面不对。途中查出并修了：①流程 `pick` 发 change 事件、只剩最近 10 件（36d94714），按名字前缀认错（3750a362），都是测试问题；②旧数据集建了一半（只按公司判断建过没有），重建站点；③调拨流程把出库金额当带符号的数（流水 value_delta 存的是大小）；④**产品竞态两处**：出入库 / 商品 / 库存卡三页 render 没接 still_current（490021f0，加契约测试）、表头建议仓走 Link 异步校验期间人选的仓会被框架丢掉（43438a88，`p.place.put` 同步写入，出入库 / 采购 / 销售 / 期初 / 盘点共用）。修复后那一格单跑 1 次通过（实时源码已含两处修复）。之后按用户指示本机流程停了，见下面「待转 GitHub 流程」 |
| 测试机：C 盘 T 账套 | 回归脚本改成挑不到正结存、或照负库存规则核不过时，先在副本里用 JU 按缺口垫货（`T9GIS` 主档结存原是 −1，垫 1 不够）。101 本：**80 本写 RL + 读回 + 作废 + 已装 CC 索引程序核 CDX 全过、0 失败**；21 本跳过（15 本没有库存品、5 本没有可记账的 JU、1 本没有 RL 簿）。副本已删、占用表已登记释放 |
| 测试机：服务器版 Express（69EXP\TEST） | 主会话批准。写前整本只读拷到测试机 `C:\PearnlyStress\_book_record\mwh-rl-before`（167 个文件 4,717,416 字节）；确认没有 Express 进程、账套最后写入 10-30 18:47；持 `PEARNLY.LCK` 用 CC 写入代码写 **RL691031-001**（น้ำแข็งหลอด 1 个，01 → 02；账套允许负库存、01 仓有 12,155，不用垫 JU）。用户本人登录 Express 后远程打开：单号、日期 31/10/69、调出 01 / 调入 02、说明、明细 1 行 1.000 กก **与写入一致**（截图 `rl-open.png`）。再在 Express 里**原生作废**：确认框 → 43 → 红字「ถูกยกเลิก」、明细清空（截图 `rl-void-confirm.png`、`rl-void-msg43.png`、`rl-voided.png`，都在共享笔记 `多仓P2-69EXP-RL截图-10-31`） |

**69EXP\TEST 前后对照**（写前 / CC 写后 / Express 作废后）：

| 项 | 写前 | 写后 | 作废后 |
| --- | --- | --- | --- |
| STLOC 01 / 02 | 12155 / 无行 | 12154 / 1（新建行） | 12155 / 0 |
| STTRN RL691031-001 | 无 | 1 行：POSOPR 4、LOCCOD 01、PEOPLE 02、DOCSTAT N | DOCSTAT **C**、NXTSEQ 清空 |
| STCRD 那一对 | 无 | 2 行（4 / 3） | 2 行打删除标记 |
| ISRUN RL | 0000001 | 0000002 | 0000002（不回退） |
| STMAS 结存 / 货值 | 12155 / 18,039,046.22 | 不变 | **4 / 3,203,154.38** |
| 凭证 | — | 无 | GLJNLIT 改 12 行（见下） |
| 写入集合外的表 | — | 逐字节不变（只动 STTRN / STCRD / STLOC / ISRUN 及索引） | — |

- **作废时 Express 重算了这件商品的成本链**（10-31 12:59，原始字节比对 `diff-void.json`）：这件品早先 14 行流水的累计结存从约 12,14x 重算成 −2…3，
  批次链改指（HP0000107 → B/F），几张 IV 的销货成本改了金额，IV0000006 / IV0000027 的成本分录打删除标记，新增一行「ปรับต้นทุนโดยโปรแกรม」1,820,996.64；
  STMAS 12155 → 4。原因是这件品在这本测试账里早就前后不一致（主档 12,155，流水链从期初推下来只有个位数，还有单价 98 万的流水，是之前各轮测试留下的）；
  RL 本身全仓净 0，CC 写的那一对不在重算范围。4.6b 的样本里 Express 作废 RL 也会「批次链改回原样」，只是那次数据一致所以主档没变。
  作废后这本测试账 STLOC 合计（12155）和 STMAS（4）对不上，报主会话后按下面整本还原。
- **主会话定：整本还原**（10-31 13:1x）：确认没有 Express 进程，现状另存 `mwh-rl-after-void`，持 `PEARNLY.LCK` 用写前快照整本覆盖回去（实际覆盖 10 个文件：
  STTRN / STCRD / STLOC / STMAS / ISRUN / GLJNLIT 及索引），再从服务器另拷一份逐文件比对：167 个文件与写前**逐字节一致**。69EXP\TEST 回到测试前原样。
- **结论 ①：Express 原生作废 RL 会对该商品重算整条成本链**（改 STMAS 结存 / 货值、历史 STCRD 的累计结存与批次链、销货成本分录 GLJNLIT），这是 Express 原生行为。
  **CC 自己写的作废（`dbf_void` 认 RL）不触发重算**：只把单头改 C、那一对打删除标记、两仓 STLOC 回滚，STMAS 与凭证不动（同 CC 写的其他库存单，成本交给 Express「计算成本」）。
  所以同一张 RL，在 Express 里作废和由 CC 作废，账套结果不同：数据一致的商品两者 STLOC / 单据状态一样，主档与成本分录只有 Express 作废会重算。
- **结论 ②：69EXP\TEST 的 น้ำแข็งหลอด 原本主档与流水链不一致**（主档 12,155，流水链从期初推只有个位数，有单价 98 万的旧测试流水）。以后拿这本账测试要避开这件品，
  或先挑主档与流水链一致的干净商品。
- **过程事故（已补救）**：第一次写时脚本照搬了回归副本的「不备份」写法，又没带 CC 的索引程序 → 表写进去、索引没重建、没回滚（正式 CC 默认先备份，失败会整单回滚，不是产品缺陷）。
  当场用测试机已装 CC 的 `cdx_reindex_runner.exe` 重建 STTRN / STCRD / ISRUN / STLOC 索引，再跑 CC 自己的写后核对（单头、成对明细、STCRD 单号索引读回）全过，才交给 Express 看。
- 收尾：Express 已退出、本窗口计划任务 `PearnlyMwhP2` 已删、python 进程 0；保留 `C:\PearnlyStress\_book_record\mwh-rl-before / after / voided / after-void` 四份副本与 `C:\PearnlyStress\_rl_srv`（脚本、截图、比对结果），等主会话定何时删。
- 没做：ERP 页面 → CC 桌面版测试包 → 账套的端到端（主会话定不装测试包）。

### 最终提交上的流程（10-31 晚，用户改回本机跑）

- 1f363f24（基线 0836f929）`run.py --changed --post-status`：13 条 42/45。不过的 3 格：`erp.stock-in.units` 新开户 / 手机超时 900 秒（已知时序问题）；
  `erp.products.create` 新开户两格，新建弹窗出了按台两格——同一套数据里 `erp.stock-in.units` 把公司改成汽车类型不还原，测试数据串扰，
  商品新建流程准备时把公司类型设回普通（b2497cb1，变基后号见下）。
- 变基到 origin/main c64dc30d（冲突：载荷号 main 已占 13，本分支用 14、支持集 1–14；`serials.lookup` 合 GS1 与库位参数；流程登记表两边都留），
  本地闸门 155 步全过，2a987e5e 上 `run.py --changed --post-status`：16 条 53/57。不过的只有 `erp.exports` 四格「no export action」——
  已知问题第 18 条（实验栈没接计费控制面，首页加载失败），跑 `scripts/regression/lab_billing.py` 后单跑 4/4 通过。合计 57/57；
  GitHub 上 2a987e5e 的 `flows` 状态是那次整套的「不通过」，没为这 4 格再跑一小时整套。新写的 `erp.stock-card` 在两轮里都通过。

### 待转 GitHub 流程（10-31，用户指示流程搬到 GitHub 机器跑，本机停；已作废，见上一节）

- 要在**最终提交**上跑：`python3 scripts/flows/run.py --changed --base origin/main --post-status`（本分支相对 main 的受影响流程，约 9–12 条 × 两套数据 × 电脑 / 手机）。
- 3750a362 之后又改了 `stock-entry.js`、`products.js`、`stock-card.js`、`transactions.js`、`stock-opening.js`、`stocktake.js`，
  这些覆盖到的流程在新提交上**都还没整套跑过**：`erp.stock-entry.scan`、`erp.stock-transfer`、`erp.stock-in.units`、`erp.sales.scan`、
  `erp.purchase.create`、`erp.products.create` / `opening` / `clash`、盘点各条（`erp.stocktake.*`、`erp.stock.excel-units`）、`erp.cashier.first-sale`、`erp.document.express`。
  另新写的 `erp.stock-card`（库存卡：两次渲染只出一份卡、期末结存与账面一致）**一次都没跑过**，要先在 GitHub 机器上跑通。
- 已单独验过的：`erp.stock-entry.scan` 旧数据 / 手机修复后 1 次通过（之前 2 次里 1 次不过）；`erp.stock-entry.scan`、`erp.sales.scan` 新数据 / 电脑各 1 次通过（pick 修复后）。
- 共用件连带回归（`check-impact`）里的手工项（扫码枪、LINE、CC、导出、开户等）本窗口**没测到**：流程转 GitHub、测试机按指示不动，留给验收窗口。
- 本窗口实验栈 `pearnly-mwh-p2` 已停、数据卷保留（主会话定何时删）。

### 合入前

- 分支 `pearnly/multi-wh-p2-ca3eab` 已变基到 c64dc30d；载荷 14（main 上直写口径占 13）；`serials.lookup` 已与 GS1 合并。合入与部署状态见交付说明。
- 后续项（不在本期）：同仓不同库位移动。
