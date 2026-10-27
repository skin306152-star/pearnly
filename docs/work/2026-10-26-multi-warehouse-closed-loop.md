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

前端待做（等 native-buttons、打印第二批合入）：采购 / 销售 / 出入库 / 期初 / 盘点的单头「仓库 + 库位」共用控件（行继承、可展开逐行改，
选商品不再动行仓、库位只在商品建议库位属于该仓清单时预填）、删 `frappe.boot.pearnly_warehouse`、建商品对话框 / 整页 / Excel 的期初网格、
期初页的仓库列、仓库页对接 Express 的公司隐藏「新建」；新按钮 / 控件按 `data-pearnly-action` 声明登记。

## 暂停点（10-26 晚，用户指示全部窗口就地暂停；10-27 已按此继续）

- P0 六项代码与用例都已写完并提交在本分支（G13、G16 两项、G17 两项、G15），本地用例全过；闸门（132 项）跑在 G17 提示改动之前的内容上，
  候选离线检查跑在混合内容上——**都要在最终内容上重跑**。没推 main、没交验收、测试机没占用。
- **明天第一步**：在最终内容上重跑本地同名闸门（`~/pearnly-devtools/gate.sh`）+ 候选离线检查 → 推分支等 Development checks →
  实验栈实测 P0（差异报表「只看」三种值、停用有货的仓被拒、收银台绑停用仓被拒、按台期初 Excel 同名仓、打开按台的提示与账面表、
  无码仓的采购 / 出入库 / 收银日汇总停在 warehouse_unmapped）→ `check-impact` 受影响入口实测 → 交验收（只看差异）→ 合入 main →
  告诉代录窗口变基（把停下交人分支挪进它抽出的函数）。之后开 P1。
