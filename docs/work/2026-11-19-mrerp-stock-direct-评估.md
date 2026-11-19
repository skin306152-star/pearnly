# 需求影响评估：ERP 库存单（出库、调拨、调整、入库）直写进 MR.ERP

> 依据：MR.ERP 源码只读（`impstktranrec` / `impstktraniss` / `stktrant` / `stktranmod` / `component/php/stkcardpc.php`，11-19 子代理读）、
> 11-16 在测试账套只读看过的两个导入页、Express 那边的口径 [`2026-11-18-stock-express-parity-评估.md`]（`pearnly/stock-express-parity-3afd8c`）。
> 本文不写测试账号、站点、单号（仓库公开）。

## 0. 需求原文与理解

- 原话：用户 11-16 定「MR.ERP 是『引导式覆盖所有』的一列，收款直写要做，出入库直写也做」；主会话 11-19 任务书：范围是 ERP 库存单
  （出库、调拨、调整、入库）写进 MR.ERP，原则「和 MR.ERP 原生录入一致」，会计叫法照 MR.ERP 源码。
- 理解：账本是 MR.ERP 的公司在 ERP（网页 / 手机 / CC 引导）提交出入库单、调拨单、物料调整单（含盘点生成的调整单），由服务端写进
  这家公司当年的 MR.ERP 账套，结果与财务在 MR.ERP 对应菜单亲手录一张一样；单据上记 MR.ERP 单号，同采购 / 销售 / 收款。
- **现状缺口**：这类公司提交库存单时，`express.push.queue` 交给 `mrerp.push.queue`，后者只认采购 / 销售（`DIRECTIONS`），所以库存单
  **静默不写、也不报**。
- 不在范围：Express 那边的写法（另一个窗口 `stock-express-parity`）；MR.ERP 代录（11-12 用户定代录库存先不做）；POS；付款。

## 1. 现有同类与复用

| 层 | 现有 | 本次 |
|---|---|---|
| 共用层导入流水线 | `pearnly_core/mrerp/push/execute.py`（登录、formupload、importpc、report、回读），收款 `push/receipt.py` | 复用；`push/modules.py` 加回 `stock_in`（impstktranrec 356，列表 stktranrec）、`stock_out`（impstktraniss 357，列表 stktraniss）——文件头写着当年从旧仓搬来时「删去库存进出两项」 |
| 导入表生成 | `push/xlsx/receipt.py`（模板拼 XML，相对路径） | 新 `push/xlsx/stock.py`：入库 Sheet1 6 列 + Sheet2 8 列，出库 6 + 6 |
| 菜单问一次记住 | `mrerp/menus.py`（销售、采购、收款，只列已关联日记账的） | 加入库菜单（linkidmenu 9）、出库菜单（linkidmenu 8） |
| ERP 侧执行与状态 | `pearnly_erp_portal/mrerp/push.py`（单据通用账本栏、排队、幂等、结果不明、读回） | `DIRECTIONS` 加 `ERP Stock Entry`；载荷由新 `mrerp/stock.py` 从 `express/stock_entry.document` 的同一份行（物料码、仓码、库存单位数量、单价）转成 MR.ERP 的样子 |
| 主档对应 | `mrerp/masters.py` 已把 MR.ERP 物料 / 仓库 / 按仓结存整读进 ERP（来源 mrerp，编码在 `express_code`） | 直接用：物料码 → รหัสสินค้า，仓码 → คลัง（4 位） |
| 作废已写入 | 收款：MR.ERP 没有删除导入，撤回转人工、给单号与菜单 | 照收款 |

结论：全部扩展现有模块，不新建写入器；ERP 不加 DocType。

## 2. Frappe 原生能力

- 不涉及新页面 / 字段；单据弹窗里的 MR.ERP 单号栏、「MR.ERP 导入菜单」问一次的弹窗都已有（采购 / 销售在用）。

## 3. 按类通用

这一类 =「ERP 库存单写 MR.ERP」：出库、入库、调拨、物料调整（人建）、盘点生成的调整单。MR.ERP 原生对应（源码叫法）：

| ERP 单 | MR.ERP 原生 | 有没有导入 | 记日记账 |
|---|---|---|---|
| 入库 | รับสินค้า（stktranrec） | **有** impstktranrec | 成本会计制度 = Perpetual 时记（借存货） |
| 出库 | จ่ายสินค้า（stktraniss） | **有** impstktraniss，成本系统算 | 同上（贷存货） |
| 调拨 | ย้ายคลังสินค้า（stktrant） | **没有**，只有网页表单 | 不记 |
| 物料调整 / 盘点调整 | ปรับปรุงสินค้า（stktranmod），正数记收、负数记发 | **没有**，只有网页表单 | 记 |

**更正 11-16 笔记**：出入库导入「不记日记账」不对。源码 `stktrantojournal` 在成本会计制度是 Perpetual 时会记；选 Periodic 时不记。
科目来源有两种：菜单关联里设了借贷科目就按「งาน + แผนก」汇总记，没设就用物料主档上的存货 / 成本科目。

## 4. 上下游数据

### 4.1 入库 / 出库导入的口径（源码，导入时照做）

- **单头**（Sheet1）：单号 ≤ 18 字、同菜单查重；日期按公历；分店正好 5 位；备注 1–3。最后有数据的列不能早于第 6 列，第 1 行是表头。
- **明细**（Sheet2）：
  - 必填：物料码、แผนก（部门）、งาน（工作代码）、คลัง（4 位仓库码）、数量。
  - 只有入库有单价、金额两列，必填，MR.ERP 不核对「数量 × 单价」。
  - 明细要按单号连续排放。
- **数量**：MR.ERP 会删掉数字和小数点以外的所有字符，所以**负号被静默去掉**，数量 0 也不拦。我们这边要先拦住。
- **单位**：导入**没有单位列**，固定记物料的标准单位。所以按库存单位换算后再交（同 Express 盘点轧差的 `base=True`），不带包装单位。
- **成本**：
  - 出库由系统算：平均成本法取当日或之前的均价，先进先出取剩余批次。
  - 写入后从这一天起**重算这件物料之后的全部库存卡**，补录早日期的单会改动后面单子的成本。这是 MR.ERP 自己的行为，与原生录入一致。
- **负库存**：出库导入**不检查**「อนุญาตให้สินค้าติดลบ」（允许负库存），网页表单检查。见 §12 #4。
- **单号**：MR.ERP 单号 = 菜单前缀 + 我们给的号。号用 ERP 单号，同一张单固定（`contracts.history_id`），重推遇「已存在」回读认领。
- **读回**：在所选菜单的单据列表里按单号查（同采购 / 销售 `readback`）。写后照常排一次主档整读，结存跟上。

### 4.2 调拨、调整：没有导入（与原则冲突，需主会话定，见 §12 #1）

- **甲（建议）：服务端照 MR.ERP 网页表单原生保存。**
  - 用我们的 MR.ERP 会话提交和人工录入同一个保存端点（`stktrant/allsave.php`、`stktranmod/allsave.php`），结果就是原生的调拨单、调整单。
  - 要先在测试账套抓一次表单协议（字段、隐藏值、校验顺序），再写。
  - 风险：表单协议没有文档，MR.ERP 升级可能变。缓解办法是每张都按单号回读核对，对不上就停在人工。
- **乙：拆成导入单。**
  - 调拨拆成「从 A 仓出库 + 进 B 仓入库」两张；调整拆成增加的行走入库、减少的行走出库。
  - 与原生不一致：没有调拨单；原生调拨不记账，拆了会记两笔分录，两张单的单价还可能不同；调整变成两张单。**不建议。**
- **丙：先只做入库 / 出库。**
  - 调拨、调整提交后停在单据上，显示「请在 MR.ERP 录入」，不写。
  - 等用户另定或甲做完。

### 4.3 要人定一次、记住的项（照采购 / 销售「问一次记住」）

- **导入菜单**：入库菜单、出库菜单，按公司记住，只列已关联日记账的。
  - 出库菜单按 ERP 用途（内部领用 / 报损 / 其他）分别记，对应 Express 那边「出库按用途选簿」。
  - 只有一个已关联菜单的不问。
- **แผนก / งาน**：导入必填，ERP 库存单没有这两栏。
  - 用连接上已有的 MR.ERP 默认值（`mrerp_defaults`，采购 / 销售在用）。
  - 没设过的，在单据的 MR.ERP 弹窗问一次。
- **分店**：用公司的 MR.ERP 分店（同采购 / 销售）。
- **入库单价**：ERP 入库行有单价就用。
  - 没有单价（退库、找到货）时，建议用 MR.ERP 按仓结存读到的均价，读不到就停下让人填。见 §12 #3。

### 4.4 Express / CC 桌面版 / Cowork

- Express：不涉及。账本是 MR.ERP 的公司本来就不排 Express（`express.push.queue` 先问 `handles`）。
- CC 桌面版：不涉及，服务端直写，载荷版本不变。
- Cowork：共用层 `modules.py` 加两项、`xlsx` 加一个生成器，Cowork 的推送不用这两项。`mrerp-rules` 共用件的入口照跑。

## 5. 隔离与权限

- 不加权限：沿用 `stock.out` / `stock.in` / `stock.transfer` / `stock.adjust`（`documents.require_kind`）。
- 公司按单头仓库定（`stock_entry.company_of`），账套按单据日期选（`catalog.pick`），同期两本问一次。
- MR.ERP 账号同账号互斥锁照旧。

## 6. 入口

- 网页（桌面 / 窄屏）、手机网页、CC 引导式的库存四入口，全部走同一个 `create_entry` → 提交 → `push.queue`，入口处自动覆盖。
- LINE 只做盘点，盘点生成的调整单按 §4.2 处理。拍照不涉及。

## 7. 共用件与连带回归

`check-impact.py --files express/push.py mrerp/push.py core mrerp/push/modules.py express/stock_entry.py` 命中两个共用件：
- `mrerp-rules`：cowork.mrerp.push / catalog / cc.mrerp-setup、erp.mrerp.direct / masters、erp.collection.mrerp；
- Express 写账：erp.cc.write、erp.cc.stock-entry / stock-void / stock-transfer、erp.entry.choices。

控制回归面：
- `express/stock_entry.py` 只读调用 `document(..., base=True)`，不改它，避免与 stock-express-parity 窗口撞文件。
- `express/push.py` 只在已有的 MR.ERP 分流处不动，改动落在 `mrerp/push.py` 的 `DIRECTIONS` 与新模块。
- 新入口 `erp.mrerp.stock` 登记进 `shared-parts.toml` 与产品地图。

## 8. 存储与成本

- 不涉及：不加外部付费调用。每张单一次 MR.ERP 登录 + 导入 + 回读，与采购 / 销售相同。

## 9. 删除、作废、追溯

- MR.ERP 没有删除导入。ERP 作废已写进 MR.ERP 的库存单，照收款的做法：
  - 单号留着，转人工；
  - 给出单号与所在菜单，请人在 MR.ERP 里作废；
  - 作废后问是否在 ERP 重录。
- 还没写进的（排队、失败）直接作废。

## 10. 文案与官方 UI

- 复用现有：「MR.ERP 导入菜单」「MR.ERP 单号」「MR.ERP 设置」、mrerp.error.* 各原因句、收款已有的作废两句（改成库存单通用要另给用户看）。
- 可能新增：调拨 / 调整停在人工那句（方案丙）、问 แผนก / งาน 的题目名。会计叫法取源码：รับสินค้า / จ่ายสินค้า / ย้ายคลังสินค้า / ปรับปรุงสินค้า、แผนก、งาน。
- 新增文案过 `check-copy.py`，改已有文案先列给用户。

## 11. 测试与验收

- **离线**：新 `tests/test_erp_mrerp_stock.py`。
  - 导入表：列数、单号 18 字、仓 4 位、库存单位换算、负数与 0 拦住、入库单价金额、出库无单价。
  - 菜单与默认值：问一次记住。
  - 流程：开关关着全挡；读回单号；结果不明；已写入作废转人工。
  - 候选检查里对假 MR.ERP 站点跑一遍导入。
- **测试账套真写**（只写那本专属测试账套，单号记在本机笔记）：
  - 入库、出库各一张；出库后核对库存卡均价与日记账（Perpetual 时）。
  - 用方案甲时，另写调拨、调整各一张，与同菜单人工录入的一张逐字段对比单据页与库存卡。
- **流程**：推分支后跑受影响流程（erp.mrerp.direct、erp.cc.stock-entry 等）。

## 12. 要主会话 / 用户定的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 调拨、调整没有导入，怎么写（§4.2） | 甲：服务端照网页表单原生保存；先做入 / 出库，甲随后 | 不定就按丙：调拨 / 调整停在人工 |
| 2 | 出库菜单按用途分别记 | 照 Express「按用途选簿」，按公司 + 用途记 | 不定就一个出库菜单记一次 |
| 3 | 入库没有单价时用什么 | MR.ERP 按仓结存的均价，读不到停下让人填 | 不定就没有单价的停下要人填 |
| 4 | 出库导入不查负库存，网页表单查 | 照原生网页：读这本账套「允许负库存」设置，不允许时 ERP 提交前拦 | 不拦就可能把 MR.ERP 写成负库存 |

## 13. 工作量与排期（机器耗时）

1. 共用层：modules 两项 + 导入表生成器 + 测试：写码约 1 小时 + 测试 15 分钟。
2. ERP 侧：分流、菜单 / 默认值问一次、单位换算、作废转人工、产品地图与共用件登记：写码约 1.5 小时 + 测试 20 分钟 + 闸门 / 候选约 40 分钟。
3. 测试账套真写入库 / 出库并读回：约 40 分钟。
4. 方案甲：抓表单协议约 30 分钟、写码约 1.5 小时、真写对比约 40 分钟。
5. 合入队列，随 MR.ERP 开关那一批上（开关仍关）。

## 14. 主会话审定（11-19）

1. 先做入库、出库，接着连续做甲（照 MR.ERP 网页表单原生保存调拨、调整单，每张回读核对），不选乙。甲做好之前，调拨、调整如实显示没写入（现有词条），
   不装成功、不静默——这是修缺陷，不算过渡版。
2. 出库菜单按用途问一次记住；用途清单与库存对齐窗口用同一份（`inventory.documents.PURPOSES`）。
3. 入库没有单价：照 MR.ERP 原生入库界面——源码与测试账套实测：选物料时带物料主档的标准成本（`checkcoststd.php` → `tbl_stkmas.coststd`），
   单价不是必填（初值 0.00、前后端都不拦）。所以照写：没有单价的行取标准成本，读不到按 0。
4. 负库存照 MR.ERP 网页：只有出库网页按「允许负库存」拦（全部仓合计、截至单据日）；导入、调拨、调整都不查。账套设成不允许时，ERP 出库单
   过账前按本公司各仓合计拦下，说「库存不足」（现有词条）。
部门、工作代码用连接上的默认值，没设过的问一次记住。

## 15. 施工进度（第 1 批：入库 / 出库，分支 `pearnly/mrerp-stock`）

| 项 | 位置 | 状态 |
|---|---|---|
| 导入表生成（Sheet1 6 列；Sheet2 入库 8 列 / 出库 6 列；只两张表；负数与 0 拦住） | `pearnly_core/mrerp/push/xlsx/stock.py` | 完成，`tests/test_mrerp_stock_core.py` |
| 执行（导入 5 步、菜单、分店 / 部门 / งาน、补标准成本、按单号回读） | `pearnly_core/mrerp/push/stock.py`、`push/modules.py`、`menus.py`（`stock_in`、`stock_out.<用途>`）、`masters.py`（`stock` 字段组、`stock_negative`）、`needs.py` | 完成；实机待测（见下） |
| ERP 分流（库存单进 MR.ERP 的排队 / 领取 / 结论；调拨 / 调整标失败；物料 / 仓库没有编码停下） | `pearnly_erp_portal/mrerp/stock.py`、`mrerp/push.py`（`_row`、`STOCK`、`INVOICES`） | 完成，`tests/test_erp_mrerp_stock.py` |
| 负库存 | 主档整读顺带读出库新单网页的 `ssunegative`（`master_export.stock_negative`），存进连接的主档记录；`inventory.documents._post` 出库过账前 `mrerp.stock.check_negative` | 完成 |
| CC 预选卡 | 库存单不进 MR.ERP 预选卡（`precheck` 改用 `mrerp_push.INVOICES`；库存四入口的 CC 引导式现在只做 Express），在单据上停下问 | 不变 |
| 文案 | 新增 7 条 `mrerp.error.ERR_MRERP_*`（物料 / 仓库不在 MR.ERP、单号过长、没有明细、代码不符、数量须大于 0、缺少单价），四语；改已有 0 | `check-copy` 拦截 0 |
| 测试账套实机 | 入库 / 出库各一张，读回单据与库存卡 | **受阻**：11-19 只读实测，测试账套的导入菜单（入库 29 / 327、出库 33，连收款 169）都显示未关联日记账（11-16 / 11-17 时是已关联），已报主会话 |

测试账套只读实测得到的事实（11-19）：库存网页菜单号入库 29（前缀 IR）、出库 33（IS）、调拨 37（IT）、调整 39（IA）；允许负库存 = 1；
主菜单页要带 `comidyear` 与 `seldb`，不带会清掉会话里的账套（之后的请求都被当成未登录）。

## 16. 下一步（第 2 批：调拨 / 调整，方案甲）

照源码协议（共享笔记「MR.ERP-库存网页表单协议-源码-11-19.md」）：先 GET `allview.php?idmenu=` 再 GET `allform.php` 取隐藏值，POST `allsave.php`；
物料 / 仓库 / 分店 / 部门 / งาน 传数据库 id（`bshlistboxdata.php`）；单号查重（`checkac.php`）、会计期间（`checkothperiod.php`）、调拨两仓不同、
同物料不重行由我们先做；调整每行带成本法、负数行单价取 `baprice.php`；成功看 `Save Success`，按单号回读列表与明细。
