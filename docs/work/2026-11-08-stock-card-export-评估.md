# 需求影响评估：收发存报表导出（照 MR.ERP 三种）

- 主会话 local_e395dace 派；工作树 `.claude/worktrees/loving-greider-e8ed08`，分支 `pearnly/loving-greider-e8ed08`，基线 `07e5c04af`（= 11-08 当时的 origin/main）。
- MR.ERP 源码：`~/mrerp-source/src`（只读）。
- **11-08 主会话审定可以施工**：§12 ①～⑥ 按建议；⑦ 文字跟界面语言、泰文逐字用 MR.ERP 原文，日期跟界面语言（泰文佛历，其他公历）；⑧ 不照搬配色，三种同一套中性样式（同现有打印格式，表头浅灰底），版式、栏位、分组、小计、表头内容照 MR.ERP。成本池差异不改，见 §C。

## 0. 需求原文与理解

- 用户原话：「严格参考 mr.erp 的源码导出功能……业务逻辑全部参考 mr.erp，前端全部用我们自己的前端……不扩大设计，不漏业务逻辑」；「三种都做吧……它这个很细致，可以根据选择导出的内容」。
- 岳父口述（泰语译）：汇总版一个物料一行，四块（上期结转 / 期间收入 / 期间发出 / 期末结余）× 三栏（数量、单价、金额）；按日期区间和物料范围出；明细版逐笔列出哪张单造成的；Excel、PDF 都要；格式照 MR.ERP 模板；成本按移动平均（5.33 已确认对，不改）。
- 我的理解：ERP「收发存报表」页（`pearnly-stock-card`）加一个「导出」，选三种报表之一（合并全部仓明细 / 汇总 / 分仓明细）+ 范围 + 格式（Excel / PDF），出的文件结构、列、分组、小计合计、表头表尾照 MR.ERP 模板，数字照 MR.ERP 的算法从我们的库存流水算。
- 不在范围：改成本算法（我们按「商品 × 仓库 × 仓位」滚动加权，见 §B-1）；改收发存页面现有列表 / 明细的显示；MR.ERP 的 MRP 报表、库存余额报表（同菜单其他两项）；打印模板以外的版面美化。

---

## A. MR.ERP 三种报表源码对照

入口：菜单 รายงาน ระบบสินค้า → รายงานรับ-จ่ายสินค้า(สต็อคการ์ด) → 三个按钮。筛选页 `rpstkmm/rpstkmmform.php`（合并）、`rpstkmmsum/rpstkmmsumform.php`（汇总）、`rpstkmmstore/rpstkmmstoreform.php`（分仓），顶部两个下拉：报表种类（`tbl_reporttype`：1 = Excel，2 = PDF）+ 报表（`tbl_report`），点 Print 交 `component/php/showreportfile.php`，它按报表号 include `report/<menu>/all/<id>.php`。

| 报表 | 筛选页目录 | Excel 模板 | PDF 模板 |
|---|---|---|---|
| รวมทุกคลัง（合并全部仓，逐笔明细） | `rpstkmm` | `report/50/all/57.php` | `report/50/all/58.php` |
| สรุป（汇总，一物料一行） | `rpstkmmsum` | `report/51/all/59.php` | `report/51/all/60.php` |
| แยกคลัง（按仓分开，逐笔明细） | `rpstkmmstore` | `report/52/all/198.php` | `report/52/all/199.php` |

### A-1 筛选项

| 项 | 合并 | 汇总 | 分仓 | 空值 / 默认 / 校验 |
|---|---|---|---|---|
| รหัสสินค้า 从 / ถึง（带 ชื่อสินค้า 只读回显） | ✓ | ✓ | ✓ | 从列表选（`tbl_stkmas` 按 `stkcode` 排）；默认 = 第一个 ~ 最后一个物料（即全部）；可点 C 清空，但清空后 Print 报「กรุณาเลือก "รหัสสินค้า" ให้ครบ」（**必须两头都有**）；从 > 到 报错 |
| วันที่ 从 / ถึง | ✓ | ✓ | ✓ | 默认今天 ~ 今天（佛历 dd/mm/yyyy）；必填；从 > 到 报错 |
| ประเภทสินค้า 从 / ถึง | ✓ | ✓ | ✓ | `tbl_stktype` 三种：1 สินค้า : จัดทำสต๊อค、2 สินค้า : จัดชุด, ประกอบ, สูตรผลิต、3 สินค้า : บริการ, ค่าใช้จ่าย；默认 1 ~ 3（全部）；从 > 到 报错 |
| รหัสคลังสินค้า 从 / ถึง（带 ชื่อคลังสินค้า） | — | — | ✓ | 从列表选（`tbl_store` 按 `storecode`）；默认第一个 ~ 最后一个仓；必填；从 > 到 报错 |
| 格式 | 顶部下拉：Excel / PDF | | | |

范围条件全是 `BETWEEN`（物料 `stkcode`、类型 `idstktype`、仓 `storecode`），按**代码字符串**比较。

### A-2 哪些单据算收 / 算发

报表只读库存卡 `tbl_stkcard`（每行 `qstd` 数量、`ustd` 单价、`amtstd` 金额）+ 明细 `tbl_stktrand.statustran`（**1 = 收，2 = 发**）+ 单头 `tbl_stktran`（`trandate`、`trandocno`）。**不按单据类型分**，写库存卡的各单据各自定方向：

| MR.ERP 单据（目录） | 方向 | 我们对应的流水类型（`ERP Stock Movement.movement_type`） |
|---|---|---|
| 收货单 `stktranrec`、采购 `aptran`/`appe`、采购加价 `appc`、销售退货 `arsc` | 收（1） | `purchase_in`、`stock_in`（含退货入库） |
| 领料单 `stktraniss`、销售 `artran`/`arse`、销售加价 `arsd`、采购退货 `appd` | 发（2） | `sale_out`、`stock_out` |
| 调拨 `stktrant` | 调出仓一笔发（2）+ 调入仓一笔收（1），同单号 | `transfer_out` + `transfer_in`（或 stock_out/stock_in 带调拨来源） |
| 调整 `stktranmod` | 数量 ≥ 0 收、< 0 发 | `adjust`、`count`（带符号） |

→ 我们的规则：**带符号数量 `qty_base` > 0 算收，< 0 算发**，与 MR.ERP 一致（类型只影响成本，不影响收发方向）。

### A-3 上期结转（ยอดยกมา）

- 取该物料（分仓版再加 `idstore = 该仓`）`trandate < 起始日` 的全部库存卡：收的 `qstd`/`amtstd` 加、发的减 → 结转数量 `calcbqty`、结转金额 `calcbamt`。
- 结转单价 = 数量为 0 时 0，否则 `round(金额 / 数量, 2)`。
- 起始日**当天**算期内（`<` 起始日为期初，`BETWEEN 起 AND 止` 为期内），与我们 `contracts.movements_before(after=True)` 相同。

### A-4 单价

| 位置 | 取法 |
|---|---|
| 明细行 · 收 / 发三格 | 直接印库存卡的 `ustd`（单价）、`amtstd`（金额）——由成本引擎 `component/php/stkcardpc.php` 事先算好写进库存卡（移动平均：发出按当时结存单价；FIFO 另一路） |
| 明细行 · 结余三格 | **报表自己滚**：结余数量 += 收 / −= 发，结余金额 += 收金额 / −= 发金额；结余单价 = 数量 0 → 0，否则 `round(结余金额 / 结余数量, 2)`（不读库存卡里存的 `baprice`） |
| 汇总 · 收、发 | 期内收（或发）的 `SUM(qstd)`、`SUM(amtstd)`；单价 = `round(SUM 金额 / SUM 数量, 2)`，数量 0 → 0 |
| 汇总 · 期末 | 结转 + 期内逐笔滚（同明细结余）；单价 = `round(金额 / 数量, 2)` |
| 合计 / 小计行 | **只合计数量和金额，单价格留空** |

### A-5 排序

- 物料：`ORDER BY stkcode`（字符串）。分仓版：仓 `ORDER BY storecode`，仓内物料 `ORDER BY stkcode`。
- 明细行：`trandate ASC, statustran ASC（同一天先收后发）, trandocno ASC, idstktrand ASC`。

### A-6 哪些物料出现在报表里

- 合并明细：结转数量 ≠ 0 或 结转金额 ≠ 0 或 期内有行。
- 汇总：12 个数（四块 × 数量 / 单价 / 金额）任一 ≠ 0。
- 分仓明细：先取「**这个仓历来有过流水**」的物料（不限日期），再按「该仓结转数量 / 金额 ≠ 0 或 期内有行」筛。仓里一个物料都不出现的仓，整块（含「รวมคลัง」）不印。
- 一行都没有时：表头照印，合计行不印（`if 计数 != 0 || 任一合计 != 0`）。

### A-7 分组与小计 / 合计

**合并明细（57/58）**：每个物料一块 →
1. 物料标题行：`<代码>  <名称>`，右侧 `หน่วย : <标准单位>`；
2. `ยอดยกมา` 行：只填结余三格（数量 / 单价 / 金额）；
3. 期内逐笔：วันที่、เลขที่，收或发三格（另一侧空），结余三格；
4. `รวม  N  รายการ`：收数量、收金额、发数量、发金额（单价、结余空）；
- 表尾 `รวมทั้งสิ้น  M  สินค้า`：收数量、收金额、发数量、发金额、**结余数量、结余金额**（各物料期末之和；结余单价空）。

**汇总（59/60）**：一物料一行：รหัสสินค้า、ชื่อสินค้า、四块 × (จำนวน, ราคาต่อหน่วย, จำนวนเงิน)；表尾 `รวมทั้งสิ้น  M  สินค้า`：四块的数量与金额（单价空）。**无单位列**。

**分仓明细（198/199）**：每个仓一块 →
1. 仓标题行：`<仓代码>  <仓名>`；
2. 仓内每个物料：物料标题行（同合并）、`ยอดยกมา`（该仓）、逐笔（该仓）、`รวมสินค้า`（收数量、收金额、发数量、发金额）、下一行 `รายละเอียดรวมสินค้า  N  รายการ`；
3. `รวมคลัง`：收数量、收金额、发数量、发金额、结余数量、结余金额；下一行 `รายละเอียดรวมคลัง  N  รายการ  M  สินค้า`；
- 表尾 `รวมทุกคลัง`（同上六个数）+ `รายละเอียดรวมทุกคลัง  N  รายการ  M  สินค้า  K  คลัง`。
- 分仓版每仓的结余是**该仓自己的流水**滚出来的（行上的金额仍是成本引擎按物料算的）。

### A-8 表头 / 表尾

| 行 | 合并 | 汇总 | 分仓 |
|---|---|---|---|
| 1 | 公司名（主分店 `tbl_othcombrh.combrhname`） | 同 | 同 |
| 2 左 | `รายงานรับ-จ่ายสินค้า` | `รายงานรับ-จ่ายสินค้า - สรุป` | `รายงานรับ-จ่ายสินค้า:แยกคลัง` |
| 2 右 | `เลขประจำตัวผู้เสียภาษี  <税号>` | 同 | 同 |
| 3 | `วันที่  <起>  ถึง  <止>` | 同 | `รหัสคลังสินค้า  <起>  ถึง  <止>` |
| 4 | `รหัสสินค้า  <起>  ถึง  <止>` | 同 | `รหัสสินค้า  <起>  ถึง  <止>` |
| 5 | — | — | `วันที่  <起>  ถึง  <止>` |
| 页码 | Excel 页眉右 `&P/&N`；PDF 每页右上 `n/N` | | |

- 日期一律佛历 `dd/mm/yyyy`（筛选框里的原值）。物料类型范围**不印**在表头。
- 文件名：`<报表名> [ddmmYYYY(佛历)_HHMMSS].xlsx / .pdf`。

### A-9 列名（泰文原文 → 意思）

**明细（合并、分仓）**，两层表头：
- 第一层：`ใบสำคัญ`（单据，跨 2）｜`รายการรับ`（收，跨 3）｜`รายการจ่าย`（发，跨 3）｜`คงเหลือ`（结余，跨 3）
- 第二层：`วันที่` 日期｜`เลขที่` 单号｜`จำนวน` 数量｜`ราคาต่อหน่วย` 单价｜`มูลค่ารับ` 收金额｜`จำนวน`｜`ราคาต่อหน่วย`｜`มูลค่าจ่าย` 发金额｜`จำนวน`｜`ราคาต่อหน่วย`｜`มูลค่าคงเหลือ` 结余金额

**汇总**：`รหัสสินค้า` 物料代码、`ชื่อสินค้า` 物料名称（两行合并）｜第一层 `ยอดยกมา` 上期结转｜`มูลค่ารับ` 本期收｜`มูลค่าจ่าย` 本期发｜`มูลค่าคงเหลือ` 期末结余；第二层每块 `จำนวน`｜`ราคาต่อหน่วย`｜`จำนวนเงิน` 金额。

### A-10 数字格式

- 数量、单价、金额一律 `#,##0.00`（两位小数，千分位）；数量也是两位。计数（N รายการ / M สินค้า / K คลัง）整数带千分位。
- 库存卡字段本身是 `decimal(12,2)`；单价按 `round(…, 2)`（PHP 四舍五入）。

### A-11 Excel 与 PDF 的差别

| | Excel（PHPExcel，xlsx） | PDF（FPDF） |
|---|---|---|
| 纸 | A4 **竖**（打印设置），边距 0.5"，页眉右页码 | A4 **横**，边距 10/7/10 mm，每页重复表头（公司、标题、范围、列头） |
| 字体 | Tahoma 11，行高 23 | 泰文字体 eucro：公司 18 粗、标题 14、列头 14 粗（汇总 12 粗）、内容 12（汇总 10） |
| 单元格 | 数字是数值格 + 格式码；列宽自适应；合并单元格同 A-7 | 定宽：明细 日期 19、单号 42、九个数字列各 24 mm；汇总 代码 20、名称 29（过长自动换行）、12 个数字列各 19 mm；数量居中、单价金额右对齐 |
| 底色 | 合并 / 汇总：列头 `D5D5D5`/`ECECEC`；分仓：列头 `0099FF` 白字，仓标题 / รวมคลัง `22A9FF` 白字，小计 `F0F0F0`，รวมทุกคลัง `0075BF` 白字 | 合并 / 汇总灰；分仓同 Excel 配色 |
| 边框 | 小计上细下粗、总计上细下双线 | 全格边框 |

源码里的两处缺陷（**不照抄**）：① Excel 明细的日期格把佛历字符串交给 `PHPExcel_Shared_Date::PHPToExcel`（只认时间戳 / DateTime），实际写进去的是 FALSE；② 合并版 Excel 第 5 行底色只涂到 I 列。我们写成佛历日期文本、整行底色。

---

## B. 我们 ERP 的数据能否对上

| MR.ERP | 我们 | 对得上？ |
|---|---|---|
| 库存卡一行（数量、单价、金额、收 / 发） | `ERP Stock Movement`（`qty_base` 带符号、`unit_cost`、`cost_known`）+ `contracts.roll` 逐笔算出的 `unit_price` / `amount` | ✓（收发方向 = 符号） |
| 单据日期 `trandate`、单号 `trandocno` | `posting_date`、`doc_no`（空时页面用 `ref_id` / `reason` 兜底） | ✓ |
| 物料代码 `stkcode`、名称、标准单位 | `ERP Product`：显示代码 = `express_code` 或 `code`（`printing.context._code`，`code` 建档必发号）；`product_name`；`base_unit` 或 `unit` | ✓ |
| 仓库代码 `storecode`、名称 | `ERP Warehouse`：`warehouses.code_of`（`warehouse_code` 或 `express_code`）、`warehouse_name` | ✓（没代码的仓按名称排在最后，见 §12-4） |
| 公司名、税号 | 当前公司 `ERP Company.company_name`、`tax_id`（与打印抬头 `printing.context.header` 同源） | ✓ |
| 一个公司一个库 | 一租户多公司：按当前公司的仓库取数（`warehouses.scope_filter`） | ✓ |
| — | 仓位（location） | MR.ERP 没有；合并 / 汇总把仓位全合起来，分仓把同仓各仓位合起来 |
| 物料类型 `idstktype`（库存 / 组合 / 服务） | **没有**。`ERP Product` 只有分类（`category`，自定义、无序号）；我们只有库存物料才有流水 | ✗ 见 §12-1 |
| 成本引擎：**按物料**全公司一个移动平均池 | **按「物料 × 仓库 × 仓位」**各滚移动平均（`report._project_position`）；调拨入按调出单价 | ✗ 方法不同，见 B-1 |
| 期初录入就是一张收货单（落在期内就是一笔「收」） | 「初始化」流水（`movement_type=opening` 或 `ref_type=opening`）**并进期初**，不算期内收（现有页面口径，`report.py` 文件头） | ✗ 见 §12-2 |
| 成本总有数（没有就是 0） | 成本未确立时为空（`cost_known=0`），页面显示「—」 | ✗ 见 §12-3 |
| 无成本权限之分 | 没有成本权限的岗位看不到单价、金额（`guard.cost_visible`） | 我们多一层，照我们（导出同样留空） |

**B-1 成本方法差异（不改，只说明）**：MR.ERP 一件物料在全公司只有一个移动平均价；我们按仓（及仓位）各算各的。同一物料两个仓进价不同时，发出单价会不同于 MR.ERP。岳父已确认 5.33 对（他看的是单仓）。导出时**行上的单价 / 金额取我们引擎算出的值**，结余、汇总、合计按 MR.ERP 报表算法（A-3 / A-4）重算。合并版里多个仓的行混在一起，结余单价 = 结余金额 / 结余数量（A-4），与 MR.ERP 同式。

---

## C. 与 MR.ERP 的已知差异（给用户）

1. **同一物料放在多个仓（或仓位）时，单价可能和 MR.ERP 不同。**
   MR.ERP 一件物料在全公司只算一个移动平均价：不管从哪个仓进、从哪个仓出，都按全公司的平均价计。
   我们按「物料 × 仓库 × 仓位」各算各的移动平均：A 仓进价 10、B 仓进价 12，从 A 仓卖出就按 A 仓的均价 10 计，不按两仓混合的 11 计；调拨时货按调出那一仓的单价带到调入仓。
   所以只有一个仓的物料，两边单价一致（岳父看的 5.33 就是这种情况）；多仓的物料，导出里每一笔发出的单价、金额按我们的仓位均价，合并报表里的结余单价 = 结余金额 ÷ 结余数量（算法和 MR.ERP 一样，只是每笔金额来源不同）。这次不改成本算法。
2. **期初录入**：MR.ERP 的期初是一张收货单，日期落在所选区间里就显示成一笔「收」；我们把期初录入并进「上期结转」（与收发存页面一致）。
3. **成本未确立**的流水（从没给过成本的物料）：MR.ERP 记 0；我们单价、金额留空，所在小计 / 合计的金额也留空，数量照算。
4. **没有物料类型（ประเภทสินค้า）筛选**（用户 11-08 确认）：MR.ERP 的这项筛选在我们的数据上等于全选——只有库存物料有流水，服务 / 费用不进库存，我们也没有组装套装。现有「物料分类」不动（岳父 11-01 要的自由文本，用来记尺寸等，不改成 MR.ERP 的 3 种固定类型）。以后做组装或服务进收发存时再补「物料类型」。
5. **物料 / 仓库的从 ~ 到留空 = 全部**（MR.ERP 必须两头都选，默认就是第一个 ~ 最后一个，结果相同）。
6. **配色**：三种报表用同一套中性样式（表头浅灰底），不照 MR.ERP 的分仓蓝色。日期跟界面语言：泰文佛历，中文 / 英文公历。
7. MR.ERP Excel 明细的日期格有错（写进 FALSE），我们写成正确的日期。

## D. 影响评估（模板 1–13）

### 1. 现有同类与复用
- 同类：收发存页 `public/desk/stock-card.js`（汇总列表 + 单品明细，同样两层表头、佛历日期）；`inventory/report.py`（`_project_position` 逐仓位滚成本、`_position_sums` 一条分组查询）；导出服务 `pearnly_core.exports.service.deliver`（小的当场给、大的排后台、做完推送下载；在库一览 `api_stock_export` 已用）；Excel 用 openpyxl（`sales/output.py`、`purchasing/output.py`）；PDF 用 `frappe.utils.pdf.get_pdf`（wkhtmltopdf）+ 泰文字体 `printing.context.fonts_css`、抬头 `printing.context.header`。
- 结论：**扩展现有**。取数复用 `report._project_position` / `_movements`；导出交付复用 `exports.deliver`；PDF 复用框架 `get_pdf` 与现有字体；不新建 DocType。

### 2. Frappe 原生能力
- 页面：原生 `p.dialog`（Frappe Dialog）+ 字段：Select（报表种类、格式）、Link（物料从 / 到：`ERP Product`，服务端 `search_link`；仓从 / 到：`ERP Warehouse`，按当前公司 `get_query`）、Date（起 / 止）；按钮在页面工具栏（原生 `page.add_inner_button` / 已有的 `p.export_file` 走法）。
- 文件：Excel 用 openpyxl（Frappe 依赖里自带），PDF 用 `frappe.utils.pdf.get_pdf`（锁定版 Frappe 16.33.0；wkhtmltopdf 支持 `thead` 每页重复与页眉页码）。
- 不用 Frappe Report / Query Report：带分组表头、物料块、多级小计的报表 DataTable 做不了（与现有页面同一理由）。

### 3. 按类通用
- 类：「报表导出」。同类已有在库一览导出（CSV）、盘点导出；本需求只给收发存，不顺手改其他导出。

### 4. 上下游数据
- 只读：库存流水、物料、仓库、公司。不写任何业务数据（只有导出任务记录 `Pearnly Export`，1 小时后清理）。
- Express：不涉及（不读不写 Express）。CC 桌面版载荷：不涉及。Cowork / DMS：不涉及。

### 5. 隔离与权限
- 权限同收发存页：`guard.require("report.read")`；成本列按 `guard.cost_visible()` 留空；物料 / 仓库只取当前公司（`warehouses.scope_filter`、物料 Link 的查询同库存页）；导出文件只发起人能下（`exports` 已有）。
- 后台任务以发起人身份、发起时语言跑（`exports.run`），跨公司不会串。

### 6. 入口
- 网页桌面：收发存列表页工具栏「导出」→ 弹窗。窄屏 / 手机：同一个弹窗（原生 Dialog 自适应），Excel 下载、PDF 由浏览器打开。
- LINE、拍照：不涉及。
- **CC 对话（ADR-027）**：同一个服务端函数。建议工具形状（交「CC 一套」窗口 local_fd1ef94e 按模板接）：

```
erp_stock_card_export(
  report: "all" | "summary" | "by_warehouse",   # 合并全部仓 / 汇总 / 分仓
  format: "xlsx" | "pdf",
  date_from: "YYYY-MM-DD", date_to: "YYYY-MM-DD",
  product_from?: 物料代码或名称, product_to?: 同,      # 空 = 不限（等于 MR.ERP 默认的第一 ~ 最后）
  warehouse_from?: 仓代码或名称, warehouse_to?: 同      # 只 by_warehouse 用
) -> {status: "ok", file: <对话里的文件位置>, rows: N} | {status: "queued", export_job} | {status: "not_found"|"failed", reason}
```
  业务函数：`pearnly_erp_portal.inventory.card_export.build(**同上参数) -> {"filename", "content"}`（网页导出、CC 都调它）；`card_export.size(**参数)` 数行数（含权限检查）。CC 工具 = 调 `build` → `assistant.files.add_to_conversation`（同 Cowork 复核表的做法）；超过同步上限时排同一个导出任务。

### 7. 共用件与连带回归
- `check-impact.py`（预计改动文件 report.py / api.py / stock-card.js / messages.tsv / printing/context.py 只读引用）：命中 `erp-printing`（若复用 `fonts_css` / `header`，6 个打印入口要回归；**建议只调用、不改**这两个函数，则不触发改动）、`erp.page.stock-card`、`erp.report.stock-summary-qty-sort`、`erp.exports`、`erp.browser-text`。
- 新入口补进 `scripts/impact/shared-parts.toml`（`pearnly_core.exports` 的新调用方）与产品地图；新增冒烟：收发存页点「导出」→ 三种 × 两种格式各出一份能打开。

### 8. 存储与成本
- 无外部调用、无新增固定月费。文件在站点私有目录 1 小时后删。
- 计算量：整公司合并明细要读截止日前全部流水。按 10-25 实测（30 万行流水汇总 6–13 秒）估：小租户当场出；大租户超过 1000 行排后台（25 分钟时限）。实现按物料分批（每批几百件）读流水、逐批写文件，内存不随全量涨。**PDF 大文件**：wkhtmltopdf 一次渲染整份 HTML，上万行时耗时与内存要实测（验收里用大账套各测一次，超标再按物料分段渲染后合并）。

### 9. 删除、作废、追溯
- 作废单据对流水的处理沿用现有库存口径；导出只读当时的流水，与收发存页面看到的一致。不涉及删除与 Express。

### 10. 文案与官方 UI
- 新增文案（四语：en / zh / th / ja；**泰文逐字用 MR.ERP 原文**，A-8 / A-9 / A-7 里的全部列名、行名、标题），另加弹窗用词：导出、报表、合并全部仓 / 汇总 / 分仓、格式、物料从 / 到、仓库从 / 到、日期从 / 到。能复用已有键的复用（`trade.from` / `trade.to` / `trade.product` / `trade.warehouse`）。
- 不改已有文案。新增过 `check-copy.py`，交付附 `--report`。
- 组件：原生 Dialog、Link、Date、Select；导出走 `p.export_file`。无自定义样式。

### 11. 测试与验收
- 纯函数（不依赖 Frappe）：A-3 / A-4 / A-6 / A-7 的算法放 `inventory/card_export.py` 的纯部分，单测覆盖：期初 / 期内 / 同日先收后发 / 结余为 0 时单价 0 / 汇总单价 = 和 ÷ 和 / 小计不含单价 / 不出现的物料与仓 / 成本未知 / 无成本权限。
- 实验栈：造一组流水（采购入、销售出、退货入、采购退货出、调拨两仓、调整 ±、盘点、期初、负库存、两个仓价不同），按 MR.ERP 源码算法手算期望值，与导出的 Excel（openpyxl 读回逐格）、PDF（pdftotext 读回）逐栏比对；权限拒绝（无 report.read）、无成本权限留空；截图电脑 + 手机。
- 闸门按任务书：gate → cand-local → 受影响流程 + 新流程 → CI。

### 12. 要问用户 / 主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | MR.ERP 的「物料类型 从 / 到」（库存 / 组合 / 服务）我们没有对应数据 | **不做这一项**（我们只有库存物料有流水，等于 MR.ERP 默认的 1~3 全选）；表头本来也不印类型 | 若要按分类筛，属于扩展，需另定 |
| 2 | 期初「初始化」：MR.ERP 期初是一张收货单，日期落在区间内就印成一笔「收」；我们现在把初始化并进「ยอดยกมา」 | **照现有页面口径并进期初**（与屏幕上的收发存一致，同一区间两边数字相同） | 改成 MR.ERP 口径则导出与页面不一致 |
| 3 | 成本未确立（`cost_known=0`）的行 | 单价、金额格**留空**；所在小计 / 合计的金额格只要有一笔未知也留空（现有页面「—」口径）；数量照算 | 照 MR.ERP 记 0 会把未知当成 0 元 |
| 4 | 物料「从 / 到」与仓「从 / 到」 | 用 Link 选物料 / 仓，**留空 = 不限**（等于 MR.ERP 默认全选）；按显示代码（物料 `express_code` 或 `code`，仓 `warehouse_code` 或 `express_code`）字符串区间取；没代码的仓按名称排在有代码的之后，区间留空时包含 | MR.ERP 强制两头必选；我们留空即全部更省事，结果相同 |
| 5 | 明细行排序 | 照 MR.ERP：日期 → 同日先收后发 → 单号 → 录入先后；行金额取我们引擎按真实时间算的值 | 页面现在按日期 + 时间排，同日顺序可能与导出不同（每行金额不变，只结余过程数顺序不同） |
| 6 | 导出日期区间默认值 | 带入页面当前的「从 / 到」（MR.ERP 默认今天~今天） | — |
| 7 | 文件里的语言与日期 | 文字跟用户界面语言出（泰文逐字用 MR.ERP 原文；中文 / 英文 / 日文按官方风格译）；日期**一律佛历 dd/mm/yyyy**（现有收发存页面各语言都显示佛历） | 若要非泰文用公历，需另定 |
| 8 | 分仓版配色（蓝底白字）与合并 / 汇总（灰） | 照 MR.ERP 模板配色（文件不是界面，岳父看惯） | 统一成灰色也可以，二选一 |

### 13. 工作量与排期（机器耗时口径）
1. 纯算法 + 单测（三种报表的行模型）：写码 2 h。
2. 取数（复用 `_project_position`，按物料分批、分仓 / 合并两种聚合）+ 权限：写码 1.5 h。
3. Excel / PDF 两种渲染（三种版式）：写码 2.5 h。
4. 页面弹窗 + 接口 + `exports.deliver` + 文案四语 + 产品地图 / 登记表：写码 1.5 h。
5. 实验栈造数、手算比对、截图（电脑 + 手机）：机器 1.5 h。
6. 闸门（gate、cand-local 102 组、流程、CI）：机器 1.5–2 h。
- 合计写码约 7.5 h、机器约 3–3.5 h；CC 工具由「CC 一套」窗口接（约 1 h），同批上线。

---

## E. 实现（11-08 施工）

| 文件 | 内容 |
|---|---|
| `inventory/card_rows.py` | 纯算法（不依赖 Frappe）：MR.ERP 的结余滚动、单价、小计合计、出现规则、代码区间；成本未知 / 无成本权限留空 |
| `inventory/card_export.py` | 取数（当前公司仓库、按物料代码 / 仓代码区间、按物料分批读流水、复用 `report._project_position`）+ 版面模型（Excel、PDF 共用一份）+ Excel（openpyxl）+ PDF（`frappe.utils.pdf.get_pdf`，A4 横、thead 每页重复、页眉「页码/总页数」）；`size` / `build` 网页与 CC 共用 |
| `inventory/api.py` | `api_stock_card_export`（POST）→ `pearnly_core.exports.deliver` |
| `public/desk/stock-card.js` | 页头「导出」→ 原生 Dialog（报表、格式、日期从 / 到、物料从 / 到、分仓时仓从 / 到） |
| `hooks.py` | 页头入口声明 `pearnly-stock-card: trade.export.action` |
| `i18n/messages.tsv` | `trade.export.*` 41 条四语（泰文逐字 MR.ERP） |
| `tests/test_erp_stock_card_export.py` | 16 个用例：纯算法 + 假站点上的三种报表逐栏（按 MR.ERP 算法手算）+ Excel 读回 + PDF 版面 HTML；进 Development checks 与候选离线检查 |
| `scripts/flows/erp_stock_card_export.py` | 流程 `erp.stock-card.export`：页头导出 → 汇总 Excel、合并 Excel、分仓 PDF，POST / 200 / 文件内容 / 期末数量对账面 |
| 产品地图 / 共用件登记 | `erp.desk.pearnly-stock-card.action.trade.export.action`；`erp.exports` 入口补收发存导出 |

CC 工具（ADR-027）：形状已发「CC 一套」窗口 local_fd1ef94e（11-08），调 `card_export.build` / `size`，同批上线。

## F. 验收与连带回归（11-08，实验栈 pearnly-cardexport-lab，线上同版镜像 26a2b0e8，分支 13ac2c490 起）

- 手算比对：不接 Express 的测试公司、两个仓；采购、销售、出库、入库、调拨、物料调整 ±、期初；三种报表 Excel 逐栏 0 处不符，PDF 三种生成（脚本与成品在共享笔记「收发存导出-验收-11-08/」）。
- 本地闸门 gate.sh 206 步全过；候选离线检查 cand-local 105 组 LANE EXIT 0；受影响流程 10 条 × 新旧数据 × 电脑 / 手机 37/37（已回写 `pearnly/flows`）。
- 语言（主会话 11-08 要锁住，10-08 口径「文件跟导出人的网页语言」）：`test_erp_stock_card_export` 里同一份数据以中文、泰文、英文导出，断言报表名、表头、上期结转、日期年份（2026 / 2569）、文件名各自不同，泰文逐字等于 MR.ERP 原文；后台那条路用真的 `pearnly_core.exports.service`：以中文发起、后台进程是泰文时文件仍是中文，反之亦然。术语表 41 条四种语言都有、都不是键名。改坏验证：让日期不跟语言或让后台不恢复语言，测试都会失败。`tests/test_exports.py` 的导出入口表登记了新接口（成功 / 失败 / 无权限三条路径）。
- 修过的三处：PDF 明细格子边框与日期折行；手机弹窗物料 / 仓库「从、到」交错；英文文件名冒号替换多一个空格。

影响清单（`check-impact.py --base origin/main`；命中原因：改到登记文件 `hooks.py`、`inventory/api.py`、翻译包）：

| 入口 | 结果 | 依据 / 原因 |
|---|---|---|
| erp.page.stock-card | 通过 | 流程 erp.stock-card、erp.stock-card.export 新旧 × 电脑 / 手机全过 |
| erp.report.stock-summary-qty-sort | 通过 | 实验栈老数据按期末数量倒序一页 0.02 秒（`report.py` 未改） |
| erp.stock-in.attach / erp.stock-out.attach | 没测到 | 只因 `inventory/api.py` 登记命中；本次只在文件里新增一个导出接口，附件接口未动；离线 `test_erp_stock_attachments` 在 CI 过 |
| erp.line.chat | 没测到 | 要三星真机；本次不改 LINE 代码，只因 `inventory/api.py` 登记命中 |
| erp.cc.connect | 没测到 | 要动测试机 CC（任务书不碰测试机）；本次不改连接代码 |
| erp.cc.write | 没测到 | 写 Express 要测试机；本次不改写账代码 |
| erp.cc.chat-push | 通过 | 离线检查（候选离线 105 组内）过 |
| cc.operator.thread | 通过（离线） | gate 里 `test_express_operator_*`、`test_cc_operator` 过；实机没测，本次不改代录 |
| erp.exports | 通过 | 流程 erp.exports 新旧 × 电脑 / 手机全过 |
| shared.navigation | 通过 | `hooks.py` 只在页头入口声明加 `pearnly-stock-card: trade.export.action`（`check-entries.py` 过）；侧栏 / 菜单项未改，10 条流程打开各页正常 |
| erp.masters.scope | 没测到 | 只因 `hooks.py` 登记命中；本次不涉主档 |
| erp.stock-count.new-product | 通过 | 流程 erp.stocktake.new-product 全过 |
| erp.stock-count.code | 没测到 | 扫码要真机；盘点代码未改 |
| erp.execution | 没测到 | 付费识别不进默认回归；识别代码未改 |
| erp.provisioning | 没测到 | 要控制面开测试户；`hooks.py` 只加页头入口声明，菜单、权限、功能码未改 |
| erp.browser-text | 通过 | 翻译包只新增 41 条、未删改；导出文字泰文（单测）、中文（流程）、英文 / 日文（实验栈出表头与文件名）核过 |
