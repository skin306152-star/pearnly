# 需求影响评估：多仓闭环第二期（P2 调拨单 + P2x CC 写 Express 调拨 RL + G17 后两项）

> 上位评估：[一公司多仓库闭环](2026-10-26-multi-warehouse-closed-loop-评估.md)（10-26 审定，13.2-3 / 13.2-4、G6 / G7 / G17、问 6）。
> 任务记录：[多仓闭环](2026-10-26-multi-warehouse-closed-loop.md)（P0 fcb65cca、P1 8fd0fa4d 已上线）。
> 基线：`origin/main` d22b7b2b（10-30 19:40）；分支 `pearnly/multi-wh-p2-ca3eab`。下文路径省略 `apps/pearnly_erp_portal/pearnly_erp_portal/`。
> 证据层级：代码（本分支逐处读过）+ 原生样本记录（`2026-10-25-express-native-rules.md` 4.6 / 4.6b，3e1a7fd5）；**本评估没有新做实验栈或测试机实测**。

## 0. 需求原文与理解

- 来源：岳父 10-26「一公司多仓库成为正式闭环」，主会话 10-30 下达本期：ERP 调拨单（同公司两仓之间，带库位，按台商品按台调）、CC 把调拨写进
  Express RL（照原生样本，含作废）、G17 后两项（选台按仓过滤、出库核库位）。待问岳父的事项用户已全部划掉，本期按已有口径自定。
- 理解：库存岗位在「调拨」页开一张单，选调出仓（+ 库位）、调入仓（+ 库位），录商品与数量，提交即过账：调出仓减、调入仓加，成本随货走，
  两仓合计数量与金额不变；按台商品选台随单换仓；作废两边一起冲回。接 Express 的公司，提交后 CC 桌面版写成一张原生调拨 RL，作废时照原生作废。
- 不在本期：G14（出入库 / TK / RL / SR 遇新商品建 STMAS，P3x）；P4 跨仓合计视图；成员按仓权限（问 4）；跨公司调拨；调拨在途
  （发出、收到分两步）；调拨打印格式（没有岳父样板，见 12 节）；从 Express 读回别人在 Express 里录的 RL 单（结存由现有主档同步按 STLOC 对齐）。

## 1. 现有同类与复用

- 出入库单 `ERP Stock Entry`（`inventory/documents.py`）：表头仓 + 库位、明细、提交即过账、作废反向冲销、按台、附件、取号、Express 推送
  （`express/stock_entry.py` → CC `dbf_stock` / `dbf_void`）、权限（`inventory/access.py` 按 `stock.<方向>`）都现成。
- 流水与结存 `inventory/stock.py`：`apply_movement` 已按「商品 + 仓 + 库位」记、入库核库位清单、成本按 `contracts.apply_move` 滚动加权。
- 按台 `inventory/serials.py`：登记 / 出台 / 收回 / 反向都有，缺「换仓」一种动作。
- CC：`dbf_stock`（OU / JU / TK 写入、读回、备份回滚）、`stock_location`（STLOC 增减、调入仓没有行时新建）、`stock_guard`（不许负库存时核结存）、
  `dbf_void`（照原生作废库存单）。
- **结论：扩展现有，不新建单据。** 调拨 = `ERP Stock Entry` 第三个方向 `transfer`（上位评估 13.2-3 已定，同 ERPNext Material Transfer 语义），
  一路复用取号、权限、附件、作废、推送、CC 写库存单的骨架。新建一张 DocType 要把上面每一样再抄一遍，不取。

## 2. Frappe 原生能力

- 单据：原 DocType 加字段（`direction` 选项加 `transfer`、表头 `to_warehouse` Link / `to_location` Data、明细 `to_location` Data），
  结构走原生迁移（JSON + `bench migrate`），不手改库。
- 选择框：调出 / 调入仓都是原生 `ControlLink` + 现有仓库 `get_query`（服务端分页，只出当前公司启用仓）；库位同现有单头库位控件。
- 页面：沿用出入库同一份 Desk 页面代码（`public/desk/stock-entry.js`）、原生 Dialog / FieldGroup / Grid / DataTable，不加自定义样式。
- 菜单：ADR-009，产品自己的 `hooks.py` `pearnly_navigation` 加一页 `pearnly-stock-transfer`、`access/menu.py` 加菜单键 `stock-transfer`。
- 需自定义：无。

## 3. 按类通用

- 「所有库存单」：调拨与出入库同一套表头仓 / 库位控件、取号、附件（统一原件层加 `stock_transfer` 方向）、作废、Express 推送与作废通道、
  体检（`audit.entry_lines_mismatch` 按方向核，调拨要按两边各核）。
- 「所有选台入口」（G17）：销售收据、收银、出库、调拨的选台下拉与扫码认台，一律按**这一行的仓 + 库位**过滤（`serials.js` 一处改，
  不在各页各写）；服务端出台 / 调拨核台时仓与库位都要对上。入库「退货」收回已售的台不按仓过滤（台已不在库）。
- 不覆盖：盘点生成的出入库单（不动台，现状）；收银退货（回原小票仓，现状）。

## 4. 上下游数据

- **ERP 单据流**：
  - 提交：一个服务端函数（`stock.apply_transfer`，同一事务）——锁商品 → 按台换仓 → 每行先写调出流水（`transfer_out`，按调出位置当前均价出）
    → 再写调入流水（`transfer_in`，单价 = 调出那一笔的单价，成本随货走）。调出位置成本未知时调入同样「成本未知」（`receive_at_cost`），不猜。
  - 新流水类型 `transfer_out` / `transfer_in`（`contracts.py`、`ERP Stock Movement.movement_type` 选项），收发存按数量正负已能归到发 / 收，
    库存卡显示「调出 / 调入」；体检的「未知流水类型」认这两种。
  - 作废：现有 `reverse_movements` 按原流水逐条冲回原仓原库位（调入仓按其当前均价减、调出仓按其当前均价加，与出入库作废同口径，
    期间两仓均价变了时合计金额会有分位差，不另做）；按台车辆退回调出仓原库位，台已从调入仓卖出 / 出库 / 再调走则整单拒（先作废后面那张）。
  - 受影响：库存一览、收发存、库存卡、体检、Express 对齐差异（两仓同时变，与 Express RL 后的 STLOC 一致）。
- **Express 映射**（接 Express 的公司以 Express 为准）：
  - 写入：调拨 → 原生 RL（4.สินค้า → 1 → 2 โอนย้ายระหว่างคลัง）：STTRN 一行（POSOPR 4、LOCCOD 调出、PEOPLE 调入、REMARK 单头说明、
    NXTSEQ 右对齐 3 位行数、DOCSTAT N、无金额）；STCRD 每件一对（调出 POSOPR 4 / PEOPLE 调入，调入 POSOPR 3 / PEOPLE 调出，同 DOCNUM、
    同 SEQNUM，数量、单位、TFACTOR，**单价金额全空**，STKDES = 单头说明，REIMBURSE `\x00`，MREMBAL / MREMVAL = 写后全仓结存即不变，
    MLOTNUM 指调出仓来源批次、没有写「B/F」）；STLOC 调出减、调入加（调入仓没有这件的行就新建，金额栏留空）；**STMAS 不动、不写 GL**；
    ISRUN RL +1。单号沿用 CC 写库存单的格式（单别 + 佛历年月日 + `-` + 当日序号）。
  - 两个仓都必须有 Express 仓库码，任一没有 → 停在人工 `warehouse_unmapped`（P0 已有通道），不写主仓；两仓对到同一个 Express 仓（RL 写不出意义）→ 同样停在人工，ERP 里的调拨照常有效。
  - 商品没有 Express 编码：照现有库存单交品名，CC 认不出整单停 `STOCK_ITEM_NOT_FOUND`（建 STMAS 是 G14 / P3x，本期不做）。
  - 科目：RL 不过账，不问科目；只核这本账套 ISRUN 有 RL 单据簿（没有 → `NO_STOCK_BOOK` 停下说明）。
  - 负库存：账套不许负库存时，CC 按调出仓结存核（`stock_guard`），不够停 `STOCK_INSUFFICIENT` 交人；ERP 自己同出库口径允许负。
  - 作废：照 4.6b 原生——STTRN 留行 DOCSTAT C、NXTSEQ 清空；那一对 STCRD 打删除标记（字段不改，SEQNUM 不改「@@@」）；STLOC 两仓回滚；
    不写 GL、ISRUN 不回退。调入仓在 Express 里已被卖走、不许负库存时作废停下转人工（同 JU 作废口径）；Express 里改过原单 → `VOID_DOCUMENT_CHANGED`。
  - 读回核对：单头一行、明细行数 = 2 × 行数且成对、两仓 STLOC 终值、这张单号下没有凭证。
- **CC 桌面版载荷**：要变。调拨 RL 是新单别、作废 RL 是新形状 → 新载荷版本（main 现在最大 12；分支 `pearnly/express-operator` 也在用 12，
  合入时取当时 main 最大值 + 1，常量 `TRANSFER_PAYLOAD_VERSION` 不写死数字）；旧版 CC 诚实拒收、派单只给报过支持新版本的电脑。
  主档读取报的 `stock_books` 加 RL（旧版不报 → ERP 不能据此判，照旧交给电脑，电脑没有 RL 簿报 `JOURNAL_BOOK_NOT_FOUND`）。
  CC 要发新版本（与 P3x 能否合一个版本看 P3 进度，不等）。
- **Cowork / DMS**：不涉及（Cowork 推送 Express 不经 ERP 仓；DMS 无库存）。

## 5. 隔离与权限

- 新能力 `stock.transfer`（别名到 `stock.write`），菜单键 `stock-transfer`（库存岗位），团队管理里逐人可勾；`entry_permission` / `entry_query`
  已按 `stock.<方向>` 判，加上 `transfer` 即可；附件、打开详情、作废都过同一道。
- 服务端拦：调入仓必须启用、与调出仓同公司（`warehouses.required(..., company=调出仓的公司)`）、不能同仓（同仓不同库位也拒——那是库位挪动，
  不是仓间调拨；库位挪动另议，本期不开）；调入库位按调入仓清单核（`positions.header` / `apply_movement` 已有），调出库位按调出仓核。
- 列表只出当前公司仓的单（调出仓属于当前公司即可，调入仓必同公司）。不跨公司，不泄露别家仓名。

## 6. 入口：网页、手机、LINE、拍照

- 网页 Desk：新菜单「调拨」，列表 + 新建弹窗 / 整页（同出入库两级），表头「调出仓库 / 调出库位 / 调入仓库 / 调入库位」，明细商品 / 数量 / 单位
  （无单价格，同出库），逐行可改调出库位、调入库位；提交前按台商品弹选台（只出调出仓这个库位在库的台）。窄屏走同一 Desk 页面（流程用例跑 mobile 宽度）。
- 明细物料格扫码：沿用出入库同一个行编辑器（`p.trade.lines_editor`），相机 / 扫码枪不改。
- LINE：不涉及（LINE 没有出入库 / 调拨入口，不新加）。CC 对话：不涉及（CC 对话不建库存单）。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files <计划改动文件>`（10-30 跑）命中：

- `cc-dbf-write`（7）：`cc.desktop.write`、`erp.cc.write`、`erp.cc.stock-entry`、`erp.cc.stock-void`、`erp.cc.pos-day`、`erp.cc.pos-refund`、`cowork.cc.channel`
- `cc-request-worker`（2）：`cc.desktop.requests`、`cc.desktop.places`
- `erp-printing`（6）：`erp.print.stock-entry` 等（只因改了 `printing/context.py` 的方向判断，调拨不出打印）
- 页面 / 详情 / 扫码 / 按台 / 附件 / 单头仓：`erp.page.stock-in|out|sales|cashier`、`erp.detail.*`、`erp.*.item-scan`、`erp.serials.dialog`、
  `erp.place.header`、`erp.stock-in|out.attach`、`shared.navigation`、`shared.shell-gate`、`erp.masters.scope`、`erp.records.scope`、`erp.entry.choices` 等

合计约 45 个入口；分支只跑受影响流程，不跑全套。新入口补进 `scripts/impact/shared-parts.toml`：`erp.page.stock-transfer`（auto）、
`erp.stock-transfer`（调拨提交 / 作废 / 按台）、`erp.cc.stock-transfer`（RL 写入与作废）、`erp.serials.pick-by-place`（选台按仓库位）。
冒烟 `scripts/smoke/express_flow.py` 加 RL 写入与作废两条。流程用例 `scripts/flows/erp_stock_transfer.py`（登记 `tests/flows/registry.toml`）：
新 / 老两套数据 × desktop / mobile，页面上建调拨、提交、断言两仓结存与流水、作废后回原；汽车公司按台商品调一台、断言台换仓与作废回调出仓。

## 8. 存储与成本

- 每张调拨 2 × 行数条流水、单头多两个字段，量级同出入库，忽略不计；附件走统一原件层（ADR-024）不变。
- 无新外部调用、无固定月费；CC 写 RL 在本地电脑执行。

## 9. 删除、作废、追溯

- 调拨提交即过账，不留草稿（同出入库）；提交后只能作废，流水不删、冲回可追溯（同一单号）；按台流水留痕。
- 已写进 Express 的 RL：作废走现有库存单作废通道（`express_push.void` → CC `dbf_void`），照原生作废；没写进去的只在 ERP 作废。

## 10. 文案与官方 UI

- 新增文案（官方简短，`scripts/check-copy.py` 过闸，交付附 `--report`）：「调拨」（菜单 / 页名，泰文照 Express 菜单 โอนย้ายระหว่างคลัง）、
  「新建调拨单」「调出仓库」「调出库位」「调入仓库」「调入库位」「调出」「调入」（库存卡类型）、错误「调入仓库不能与调出仓库相同」；
  四语齐（zh / th / en / ja）。
- 改已有文案：无。
- 组件：Page / Dialog / FieldGroup / Grid / ControlLink / DataTable / msgprint，全部官方。

## 11. 测试与验收

- 单元 / 离线：调拨成本随货走（有成本 / 成本未知 / 包装单位换算）、两仓合计不变、作废冲回、同仓 / 跨公司 / 停用仓 / 库位不在清单被拒、
  按台换仓与作废（台已卖出拒）、选台按仓库位过滤与出台核库位（G17）、权限（只有出库菜单的人不能建调拨）、Express 载荷（两仓码、无码停下、
  同码拒）、CC 写 RL（逐字段对 4.6 / 4.6b 样本：STTRN、STCRD 成对、STLOC 新建行、STMAS 不动、无 GL、ISRUN）、CC 作废 RL、旧版载荷拒收、
  CC 离线回归（S 盘真账副本里有 RL 的账套）加写入与作废读回。
- 本机实验栈：流程用例两套数据 × 电脑 / 手机；受影响入口按 check-impact 清单实测记「通过 / 不通过 / 没测到」。
- 测试机（动之前问主会话、登记占用表）：装本分支 CC 测试包 → 实验栈 / 测试租户接 Express 的公司建调拨 → CC 写进 **C 盘 T 账套**（可随时写）读回一致
  → 作废读回一致；白天服务器在线时再在服务器版 Express 打开 `70EXP\test` 写入的 RL 看原生画面（约 20:40 前收尾）。
- 用户 / 岳父要亲自看：调拨页建一张、两仓结存、作废回原；Express 里看 RL。

## 12. 要问的点（按已有口径自定，报主会话审定）

| # | 问题 | 我的定法 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 调拨入口放哪 | 侧栏「货品与库存」下单独一项「调拨」（与 Express / MR.ERP 一样是单独菜单），团队管理可单独勾 | 放进出库页当按钮会让只管出库的人也能调拨 |
| 2 | 编号 | 第四个库存单据定义，前缀 `TF`（`TF2569-000001`；MR.ERP 调拨模块 `stktrant` 前缀是用户自填，旧线上无先例） | 只影响显示 |
| 3 | 同仓不同库位要不要允许 | 不允许（本期只做仓间调拨，库位挪动另议） | 允许的话 Express 写不了（RL 不分库位，净 0） |
| 4 | 调拨打印 | 本期不出「打印」（没有岳父样板，不自造版面）；记后续项 | 仓管要纸单时再按样板加 |
| 5 | 出台核库位的严格度 | 严格：台的库位必须等于这一行的库位；选台下拉只出这一行仓 + 库位里的台，所以正常操作碰不到拒绝 | 放宽会让结存库位与台的库位对不上 |
| 6 | 读测试机上 4.6b 的原件副本（`_srvtest_record\s12-*`） | 施工时只读拷回本机，核 RL 的 ISRUN DOCTYP、LMOVDAT、MLOTNUM 实际值（样本记录没写全） | 不核就只能照记录写，个别栏位可能与原生不一 |

## 13. 工作量与排期（机器耗时）

| 步 | 内容 | 依赖 | 耗时 |
| --- | --- | --- | --- |
| 1 | ERP 调拨：DocType 字段、`apply_transfer`、按台换仓、取号、权限菜单、体检、附件方向、页面 | 无 | 约 2.5 小时 |
| 2 | G17：选台按行仓 + 库位过滤（`serials.js` / `search_units` / `api_lookup`）、出台核库位 | 无（与 1 同批） | 约 0.5 小时 |
| 3 | Express 推送：RL 载荷、两仓码、作废载荷、预检 | 1 | 约 0.5 小时 |
| 4 | CC：`dbf_stock` 写 RL、`dbf_void` 作废 RL、`stock_books` 报 RL、载荷版本、离线回归 | 读样本副本（问 6） | 约 2 小时 |
| 5 | 流程用例 + 登记表 + 冒烟 + 受影响入口实测 | 1–4 | 约 1.5 小时机器 |
| 6 | 测试机：CC 测试包写 C 盘 T 账套读回、作废；白天服务器版 Express 看 RL | 主会话同意占用 | 约 1 小时 + 等白天 |

撞文件：`drop-batch-expiry`（删批次，动 `erp_stock_entry_line.json`）、`express-operator`（动 `payload_contract.py`、`hooks.py`）、
`scan-data-matrix`（动 `serials.py` 的 `lookup`）——合入前 fetch 变基，冲突处合并对方改动，不覆盖。
