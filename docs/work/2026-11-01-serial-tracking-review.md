# 按台管理复核：规模、生产用量与岳父 11-01 说法

11-01 用户定：暂缓，等岳父答复两问后再议。

只读调研，不改产品代码。分支 `pearnly/serial-tracking-review-268e5c`，基线 `fb22a60f`（main）；生产 `c9e28ace`（web 容器 revision 回读）。

岳父 11-01 晚原话（用户转述）：「如果加了特性 / 颜色后，车的公司就可以不用在建公司时选择类别了」。
问题：按这个说法，能不能把按台管理的逻辑删掉。

## 结论先说

1. **规模**：专门为按台写的约 1,850 行（11 个文件：`inventory/serials.py` 697 行、`serials.js` 120 行、3 个 DocType、单测与流程），
   另外嵌在约 40 个共用文件里约 700 行（盘点、期初、出入库、进货、销售、收银、退货、打印、扫码认码、码命名空间、权限、公司类型），
   词条 35 条 × 5 个文件。**入口 17 个**，挂在库存层唯一汇合点 `stock.apply_lines` / `reverse_movements` 和扫码共用入口上。
   结论：**不大但很宽**——删它是一次跨 ERP 进销存全链的改动，要按共用件闸门逐入口回归。
2. **生产用量是零**：5 个 ERP 站点，真实客户（polymer 681 件物料、gize、mrerp、test01）全是「普通」公司、0 件按台；
   只有测试站 testerp 有 1 家「汽车」公司、1 件勾了按台的物料；**全生产已登记 0 台、按台单据 0 张、按台流水 0 条**。删或改都没有客户数据要迁。
3. **CC 写 Express 不带号**：`ARTRNRM`（Express 单据行附加说明）只有 `dbf_schema.py:150` 一行注释，没有写入代码；接 Express 的公司车号只在 ERP。
4. **岳父那句话只能确定一半**：颜色进了物料「特性 / 颜色」以后，**颜色**不再需要按台记；但他 10-25 要的**车架号、发动机号印在税票行下**、
   **盘点逐台扫车身二维码（= 车架号）**这两条，颜色字段替代不了。删不删取决于这两条他还要不要——要问他（第 5 节两问）。
5. **推荐**：先问两问，不先删。按回答：两条都不要 → 方案 A 整套删；只要税票印号 → 方案 C（号码改成单据行上的附加文字，删每台档案，
   与 Express 惯例一致）；两条都要 → 保留按台，只能去掉公司类型开关（方案 B，不建议）或保持现状。

## 1. 代码清单

### 1.1 专门为按台写的文件（整删即可）

| 文件 | 行数 | 作用 |
| --- | ---: | --- |
| `inventory/serials.py` | 697 | 登记 / 出台 / 收回 / 调拨 / 作废回退、认台（主号 → 副号 → 末 8 位）、开关按台、按台期初 Excel、按台记录列表与导出、选台下拉 |
| `public/desk/serials.js` | 120 | 单据提交前逐行「登记车辆 / 选择车辆」弹窗（原生 Dialog + Table + Link） |
| DocType `ERP Serial No`（json 188 + py 7） | 195 | 一台一条：车型、公司、车架号 / 发动机号 / 颜色、状态、仓 / 库位、进出单据 |
| DocType `ERP Serial Movement`（json 118 + py 7） | 125 | 按台流水 |
| DocType `ERP Line Serial`（json 67） | 67 | 单据上的台（子表，按行号对回明细） |
| `tests/test_erp_serials.py` | 342 | 单元 |
| `tests/test-serials-register.cjs` | 77 | 前端登记弹窗 |
| `scripts/flows/erp_stock_in_units.py` | 95 | 流程 `erp.stock-in.units` |
| `scripts/flows/erp_stock_excel_units.py` | 124 | 流程 `erp.stock.excel-units`（盘点 / 期初 Excel 逐台） |
| 合计 | 约 1,850 | |

### 1.2 嵌在共用文件里的部分（要逐处改）

| 区域 | 文件（行数是按台相关的约数） | 内容 |
| --- | --- | --- |
| 库存核心 | `inventory/stock.py`（约 10） | `apply_lines` / 调拨 / `reverse_movements` 调 `serials.apply / transfer / reverse` |
| 盘点 | `inventory/count.py`（约 215：`per_unit`、`unit_of_code`、`_import_unit`、`_expand_units`、`_unit_sources`、`_move_counted_units`、`units_here` 等 10 个函数 + 分支） | 扫车架号认台、一台一条、扫到别处挪位、Excel 逐台导入、本仓台列表 |
| 盘点 Excel / 期初 | `inventory/workbook.py`（约 20）、`opening_workbook.py`（约 15）、`opening_batch.py`（约 70） | 号码三列、期初逐台登记 |
| 盘点差异报表 | `report/erp_stock_count_variance`（约 15） | 「未盘到的台」列 |
| LINE 盘点 | `inventory/line_mobile.py`（约 5）、`www/stocktake.html`（约 15） | 扫车架号认台 |
| 单据 | `inventory/documents.py`、`purchasing/entry.py`、`purchasing/service.py`、`sales/entry.py`、`cashier/sales.py`（合计约 35） | 行上 `serials` 收发 |
| 单据页面 | `stock-entry.js`、`purchase-form.js`、`sales-form.js`、`pos/pearnly-pos-adapter.js`、`pos-receipts.js`、`pos/pearnly-pos-receipt.js`（合计约 25） | 提交前调 `p.serials.collect` |
| 商品页 | `public/desk/products.js`（约 120）、`products/service.py`（约 10） | 「按台管理 / 号码种类」两格、开关、按台期初登记弹窗、按台记录列表 |
| 盘点录入页 | `public/desk/stocktake-entry.js`（约 20） | 本仓台列表、逐台盘 |
| 扫码认码 / 码命名空间 | `products/codes.py`（约 15）、`count.product_of_code` | 车架号 / 发动机号与商品码同一空间、扫车架号认到车型 |
| 打印 | `printing/context.py`（约 15）、`contracts.py`（6）、`templates/_labels.html` 宏 `vehicle`、`_parts.html`、`thermal.html` | 行下印 เลขตัวถัง / เลขเครื่อง / สี |
| 公司类型 | `masters/company.py`（约 25：`company_type`、`unit_tracking`、`UNIT_KINDS`）、`company.js`（约 8）、`transactions.js:105`（`p.unit_kind` / `p.unit_tracking`）、`express/companies.py:71`、`erp_company.json`、补丁 `v1_17_company_settings`（存量设 general） | 只有汽车 / 手机电器类型公司按台生效 |
| DocType 字段 | `ERP Product.track_serial / serial_kind`；`Purchase Document`、`ERP Sales Document`、`ERP Sale`、`ERP Stock Entry` 各一个 `serials` 子表；`ERP Stock Count Entry.serial` | |
| 权限 / 菜单 | `access/menu.py`（`SERIALS`）、`inventory/access.py`、`features.json`（28 处） | 按台两张表进各菜单只读 |
| 词条 | `i18n/messages.tsv` 35 条 + `translations/{zh,th,en,ja}.csv` | `serial.*`、`company.type.*` |
| 登记表 | `scripts/impact/shared-parts.toml`（自有 `erp.serials.dialog`、`erp.serials.export`，另挂在扫码读码器、`product_of_code` 两个共用件的入口清单里）、`tests/flows/registry.toml`、`tests/baseline/page-entries/erp.json`、`scripts/regression/datasets.py` | |
| 其他单测 | `test_opening_batch.py`、`test_stock_count_workbook.py`、`test_erp_gs1_codes.py`、`test_erp_codes_per_company.py`、`test_opening_workbook.py`、`test-stocktake-entry-scan.cjs`、`test_erp_printing.py` 里的按台用例 | |

另：`public/desk/pos/official-pos_*.js` 里的 `serial_no` 是照搬 ERPNext 收银台原文、本仓桩成空，不属于按台管理。

### 1.3 入口（17 个）

商品新建 / 编辑勾按台；盘点就地新建（借商品弹窗，汽车 / 手机电器公司建档即开按台，`cc850eee`）；按台期初登记；按台记录列表与导出；
入库「其他」登记 / 「退货」收回；出库选台；调拨按仓位选台；进货（带入库）登记；销售收据选台；收银选台；收银退货两处；
盘点录入扫台；LINE 手机盘点扫台；盘点 Excel 逐台；期初 Excel 逐台；盘点差异报表；打印行下印号（收货单 / 领料单 / 销售 / 小票）；
外加扫码共用入口认车架号、公司档案的公司类型。

## 2. 生产实际用量（11-01 只读，一次 SSH，在 ERP web 容器里 `bench mariadb` 只取聚合，不进 mariadb 容器）

| 站点 | 公司类型 | 物料总数 | 勾按台 | 已登记台 | 按台流水 | 按台单据 | 盘点按台记录 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gize | 普通 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| mrerp | 普通 1 | 4 | 0 | 0 | 0 | 0 | 0 |
| polymer | 普通 1 | 681 | 0 | 0 | 0 | 0 | 0 |
| test01 | 普通 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| testerp（测试租户） | 普通 64、汽车 1 | 58,051 | 1（车） | 0 | 0 | 0 | 0 |

polymer 10-28 误勾的两件地板现已不是按台。删除或改造时，唯一要处理的存量是 testerp 那 1 家汽车公司和 1 件物料的标志。

## 3. 需求原文对照

| 时间 | 口径 | 现状 |
| --- | --- | --- |
| 10-25 岳父答复 | 新车；商品 = 车型（如 UPG20）；每台进货登记车架号、发动机号、颜色；税票照标签印车架号全 17 位、发动机号原样、颜色码，挂在商品行下；盘点一台台扫车身二维码（内容 = 车架号全号）；二手车 / 金融 / 赠品不做 | 全部已做（不接 Express 那条线）；Express 写号（`ARTRNRM`）未做 |
| 10-25 S 盘调研 | 753 本账套没有用 Express 的整车经销商；有 S/N 的行业惯例是**把号写进单据行附加说明 `ARTRNRM`，没有一车一码** | 与方案 C 的形状一致 |
| 10-26 设计 | 自建 `ERP Serial No`；台数 ≠ 数量不让过账；有库存先按台期初 | 已做，含三轮验收复核 |
| 10-28 用户 | 质疑车辆与普通商品为何两套；倾向「一个界面通用、数量为准、编号只是附加信息」，待评估 | 当时定案没拆，改为按公司类型显示 |
| 10-29 | 公司类型 普通 / 汽车 / 手机电器，按台只在后两类生效，普通公司忽略按台标志 | 已做（`2dd78fbe`） |
| 11-01 岳父 | 加了特性 / 颜色后，车的公司可以不用在建公司时选择类别 | 本文 |

## 4. 岳父那句话的几种理解

岳父说的「建公司时选择类别」就是公司档案里的「公司类型（普通 / 汽车 / 手机电器）」（`company.js:34`）。

### A. 车行按「车型 + 颜色」建物料、按数量管，不要每台的号 → 整套按台删掉

- 理解：颜色是他原来要「每台登记」的主要原因之一；颜色进了物料特性，同型号同颜色的车就是同一件货，数一数就行，自然不用公司类型。
- 删：1.1 全部；1.2 里除「打印行下副行样式」外全部（副行样式留给物料特性用，见 `product-attribute` 评估 6.1 第 22 行）；公司类型字段
  与 `unit_tracking` 判断；3 个 DocType（`scripts/production/doctype-removals.txt` 登记，部署守卫才不拦）；4 种单据的 `serials` 子表、
  盘点记录 `serial` 字段、物料 `track_serial / serial_kind`；35 条词条；登记表与流程。
- 保留：内部编码、库位、扫码读码器、码命名空间里商品自己的码。
- 对 10-25 要求：**税票不再印车架号 / 发动机号；盘点扫车身二维码认不出车**（二维码里只有车架号，系统里没号就对不上车型，只能贴物料条码或手选）。
- 迁移：testerp 1 家汽车公司改普通、1 件物料去标志；无台数据。补丁删 DocType。
- 风险：动 `stock.apply_lines` / `product_of_code` / 盘点 / 期初 / 收银 / 打印，全是共用件；要跑 `check-impact` 全入口回归。
  以后真有车行要号，得重做。
- 机器耗时估：删改代码约 3–4 小时，闸门 + 实验栈逐入口回归约 2–3 小时。

### B. 不要公司类型开关，按台改回在物料上直接勾、所有公司都能勾

- 理解：他只是嫌建公司多一步。
- 删：公司类型字段与 `unit_tracking`（约 60 行 + 补丁 + 3 条词条）；按台本身全保留。
- 对 10-25 要求：不变。
- 风险：**10-28 polymer 员工误勾地板、盘点录不进**正是这个形态；10-29 加公司类型就是为挡这个。不建议。

### C. 数量为准，号码只是单据行上的附加文字（10-28 用户倾向），去掉公司类型和每台档案

- 理解：颜色进物料；车架号 / 发动机号只在进货 / 卖车那张单的行上填一格文字，税票照印，不设「台数 = 数量」闸，不建每台档案。
- 删：3 个 DocType、`serials.py` 绝大部分、选台 / 认台 / 按台期初 / 按台盘点 / 码命名空间里的车号、公司类型、物料两格。
- 新加：4 种单据明细行一格「附加说明」（Small Text，可空，所有公司都有，不设闸）；打印把它印在行下（沿用现有副行）；
  以后 CC 写 Express 时这一格原样写进 `ARTRNRM` 行说明——**正是 S 盘用 Express 的公司的惯例**，载荷只多一格文字。
- 对 10-25 要求：税票印号保留；**盘点扫车身二维码认不出车**（同 A）；进货时不登记、卖车时手填或扫码填进那一格。
  丢掉的是「这台车在不在库、在哪个库位、哪张单卖出」的台账。
- 迁移：同 A（无台数据）；新列为空。
- 风险：同 A 的共用件回归；新增一格要过文案闸、打印回归、CC 回归（写 Express 那层另做）。
- 机器耗时估：约 A + 1.5 小时（加附加说明列、打印、单测）。

### 推荐

**先问岳父，不先删**。现在生产零使用，删 / 留都不急；但删了以后他要「盘点扫车架号」就得重做。按回答选：

| 税票印车架号 / 发动机号 | 盘点逐台扫车身二维码 | 选 |
| --- | --- | --- |
| 不要 | 不要 | A：整套删 |
| 要 | 不要 | C：号码改行上附加说明，删每台档案 |
| 要 | 要 | 保留按台；公司类型保留（它是防误勾的闸，只多建公司时一格），不做 B |

不管选哪个，物料「特性 / 颜色」照另一窗口评估做；选 A / C 时按台记录里的每台颜色随之消失，不必再并存。
选 A / C 前另需主会话定：是否连「手机电器」类型（IMEI / S/N）一起删——岳父只说了车，S 盘 GISS、GIZE、BP Siam 在 Express 附加说明里记 S/N，
那正是 C 的形状。

## 5. 要问岳父的两问（泰文草稿，句尾 ครับ 可按需换 ค่ะ）

1. 中文：卖车开税票时，商品行下面还要印车架号、发动机号吗？
   泰文：ตอนขายรถออกใบกำกับภาษี ยังต้องพิมพ์เลขตัวถังกับเลขเครื่องไว้ใต้รายการสินค้าเหมือนเดิมไหมครับ
2. 中文：盘点时还要一台一台扫车身上的二维码吗？还是按「车型 + 颜色」数数量就够？
   泰文：ตอนตรวจนับสต็อก ยังต้องสแกน QR ที่ตัวรถทีละคันไหมครับ หรือนับเป็นจำนวนตาม รุ่น + สี ก็พอแล้วครับ

可选第三问（确认建物料方式）：
3. 中文：加了「特性 / 颜色」以后，同一车型不同颜色是分开建物料吗（例如 UPG20 白、UPG20 黑）？
   泰文：พอมีช่อง คุณลักษณะ / สี แล้ว รุ่นเดียวกันแต่คนละสี จะสร้างเป็นสินค้าแยกกันใช่ไหมครับ (เช่น UPG20 สีขาว, UPG20 สีดำ)

## 6. 限制

- 只读：没改产品代码、没部署、没动生产数据。生产查询为一次 SSH，在 ERP web 容器里 `bench --site … mariadb` 取聚合计数。
- 行数中「嵌在共用文件里」的部分是按函数与相关行估的约数；施工时以 `check-impact` 清单为准。
- 「泰国卖新车税票须印车架号 / 发动机号」是 10-25 岳父给的口径与行业惯例，本次未核对法规原文。
