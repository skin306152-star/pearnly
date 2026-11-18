# ERP 库存四个入口 → Express：与原生录入逐字段一致（11-18）

需求：Pearnly ERP 的出库、调拨、调整、入库单同步进 Express 后，账套里的变化要和财务在 Express 对应原生菜单亲手录一张完全一样
（用户 11-17 转述，菜单编号 4-1-1 / 4-2-1 / 4-3-1 / 4-3-2 是口述的，以账套实际菜单为准）。

基线：`origin/main` = `1a91b178b`；分支 `pearnly/stock-express-parity-3afd8c`。

分三步：① 字段对照表（只读，本节）；② 测试账套原生录入 vs Pearnly 同步逐表比对（待主会话批计划）；③ 评估 → 批 → 施工。

## 一、字段对照表（第一步，只读）

### 1.1 四个入口现在写成什么

| Pearnly 入口（单号前缀） | `express/stock_entry.py` 的单别 | CC 桌面版写法 | 用户说的原生菜单 | 账套里同类单据簿（ISRUN，69EXP\TEST / 70EXP\test / NLTTEST 一致） |
|---|---|---|---|---|
| 出库 IS（`direction=out`） | OU | `dbf_stock` POSOPR 6 | 4-1-1 出库 | OU 类：OU「จ่ายสินค้าใช้ภายใน」、PD「จ่ายวัตถุดิบเพื่อผลิต」、XS「จ่ายสินค้าเป็นตัวอย่าง」、ZZ「ตัดสินค้าชำรุด」…（每簿默认借方科目 ACCNUM01 不同） |
| 调拨 TF（`transfer`） | RL | `dbf_transfer` POSOPR 4 / 3 | 4-2-1 调拨 | RL「โอนย้ายระหว่างคลัง」（NLTTEST 另有 TE） |
| 物料调整 IA（`adjust`） | **TK** | `dbf_stock` POSOPR `:`、FLAG 5 | 4-3-1 调整 | JU 类：JU「ปรับปรุงเพิ่ม/ลดสินค้า」、PI「รับส/คสำเร็จรูปจากการผลิต」、TK「ปรับปรุงจากการตรวจนับ」 |
| 入库 IR（`in`） | **JU（正数）** | `dbf_stock` POSOPR 8 | 4-3-2 收货入库 | 同上；「收货入库」最像 PI，**待在界面上核实** |
| 采购自动入库（`ref_type=purchase`） | 不写（采购单已带库存行） | — | — | — |

**按菜单顺序推测**（JU → PI → TK 是 ISRUN 里的顺序；已实录 OU = 4→1→1→1、RL = 4→1→2）：4-3-1 = JU「ปรับปรุงเพิ่ม/ลดสินค้า」、4-3-2 = PI。
若属实，现状有两处不是用户要的原生写法：**调整写成了盘点调整 TK（应是 JU）**，**入库写成了 JU（应是 4-3-2 那一簿）**。第二步先在界面上核清。

### 1.2 Pearnly 侧能录的字段（网页与手机同一页）

手机录入就是同一个 Desk 页（`public/desk/stock-entry.js`）窄屏显示，调同一个 `inventory.documents.create_entry`，**没有另一套手机载荷**；
`inventory/line_mobile.py` 只是 LINE 盘点，不建这四种单。

| 字段 | 出库 | 入库 | 调拨 | 调整 | 进不进载荷 |
|---|---|---|---|---|---|
| 单号 `doc_no`（系统发 IS/IR/TF/IA + 佛历年 + 流水，只读） | ✓ | ✓ | ✓ | ✓ | `ref_no`（截 15）→ STTRN.REFNUM；**TK 不写**（原生 TK 无参考号） |
| 日期 `posting_date`（必填） | ✓ | ✓ | ✓ | ✓ | `docdate_be` → DOCDAT |
| 仓库 `warehouse`（必填，一单一仓） | ✓ | ✓ | 调出仓 | ✓ | `loccod`（Express 仓码，没有停下 G15） |
| 仓位 `location` | ✓ | ✓ | ✓ | ✓ | **不进**（Express 不分仓位） |
| 调入仓 / 调入仓位 | — | — | ✓ 必填 | — | `to_loccod`；仓位不进 |
| 供应商 `supplier`（字段在，页面没摆） | 可存 | 可存 | — | — | **不进** |
| 用途 `purpose`：出库 内部领用 / 报损 / 其他，入库 退库 / 其他 | ✓ | ✓ | — | — | **不进**（不影响选哪本 OU 簿或科目） |
| 备注 `reason` | ✓ | ✓ | ✓ | ✓ | `remark`（截 50）→ STTRN.REMARK、凭证摘要；RL 还写 STCRD.STKDES |
| 明细：物料（必填） | ✓ | ✓ | ✓ | ✓ | `code`（Express 码）/ `name`；没有码的问一次或 `new_ok` 新建 |
| 明细：数量（必填）、单位 | ✓ | ✓ | ✓ | ✓ 带正负 | `qty`、`unit`、`factor`（包装单位换算） |
| 明细：单位成本 | 自动均价 | ✓ 可改 | 不带 | ✓ 只带人改过的 | `unit_cost`（出库 / 调拨不带；调整按存货 × 仓轧差后才带） |
| 明细：仓位 | ✓ | ✓ | ✓ | ✓ | 不进 |
| 明细：按台号码 | ✓ | ✓ | ✓ | **不带** | `remarks` → ARTRNRM（载荷 17） |
| 科目 | — | — | — | — | 不在页面上；首次提交弹窗问一次（`stock:<单别>`，按公司记住），否则用 ISRUN 默认科目 → `account` |

### 1.3 CC 桌面版写进哪张表哪一栏

| 表 | OU（出库） | JU（入库 / 现在的调整类） | TK（现在的物料调整） | RL（调拨） |
|---|---|---|---|---|
| STTRN 单头 | DOCNUM、DOCDAT、POSOPR 6、REMARK、LOCCOD、TRNVAL（货值）、REFNUM、NXTSEQ、ACCNUMDR、DEPCOD、DOCSTAT N、USERID、CHGDAT | 同左，POSOPR 8、TRNVAL 带正负、ACCNUMCR | POSOPR `:`、TRNVAL 0、REFNUM 空、无科目 | POSOPR 4、LOCCOD 调出、PEOPLE 调入、TRNVAL 0、无科目 |
| STCRD 明细 | POSOPR 6、TRNQTY/XTRNQTY、TQUCOD/TFACTOR、UNITPR/XUNITPR = 移动均价、TRNVAL/XTRNVAL、MREMBAL/MREMVAL 写后、ACCNUMDR、STKDES 品名 | POSOPR 8、数量货值带正负、NETVAL、ACCNUMCR；单价 = 载荷给的或均价 | POSOPR `:`、FLAG 5、MREMBAL 写前、PHYBAL 写后仓结存、盘盈挂最近进货批（RDOCNUM / MLOTNUM / LOT*） | 每件一对 4 / 3，同 SEQNUM，无价无金额，REIMBURSE `\x00`，STKDES = 备注（没写是品名），MLOTNUM 不写 |
| STMAS | TOTBAL/TOTVAL/UNITPR/MREMBAL 增减 | 同左 | 同左 + LASUPD、盘盈行成最新批 | 不动 |
| STLOC | 该仓增减（LMOVDAT） | 同左 | 同左 + TAKDAT | 两仓增减，调入仓没有行就新建 |
| GLJNL / GLJNLIT | JNLTYP 00、VOUCHER = 单号、摘要 = 备注或「จ่ายสินค้าภายใน + 单号」；借科目 / 贷各存货科目 | 正：借存货 / 贷 ACCNUMCR；负反过来；摘要「ปรับปรุงสินค้า + 单号」 | 日记账取 ISRUN 那一行、摘要「ตรวจนับสินค้า + 单号」 | 不写 |
| 定期盘存账套 / 零金额 | 不过账 | 不过账 | 不过账 | — |
| ISRUN | 该前缀 DOCNUM +1 | 同左 | 同左 | 同左 |
| ARTRNRM | 按台号码一条一行 | 同左 | — | 同左 |

单号：CC 一律自编「单别 + 佛历年月日 + `-` + 当日序号」（如 `OU691118-001`），**同时把 ISRUN 计数 +1 却不用那个号**；原生是「前缀 + ISRUN 流水」（`OU0000001`）。

### 1.4 原生侧现有实录

| 单别 | 实录 | 出处 |
|---|---|---|
| OU | 有，但只记到表级（STTRN 1 行、STCRD、GL 2 行、STLOC 减），**没逐字段** | `2026-10-25-express-native-rules.md` 4.5 |
| RL | 逐字段（一行、两行、作废） | 同上 4.6 / 4.6b |
| TK | 逐字段（TK0000001，CC 写法已对齐，只差单号格式） | `2026-10-24-erp-stock-express-plan.md` 十二 |
| JU | **待实录**（照 S 盘真账样本写，没在界面上录过） | 同上 九.7b |
| 4-3-2「收货入库」（疑 PI） | **待实录**（不知道是哪簿、界面有哪些栏） | — |

### 1.5 结论（第一步）

**A. 单别对不上（最大的两处，第二步先核）**
1. 物料调整 IA 写成盘点调整 TK；用户要的是 4-3-1（疑 JU「ปรับปรุงเพิ่ม/ลดสินค้า」）。TK 与 JU 的落库差很多：POSOPR `:` vs 8、单头无货值无科目 vs 有对方科目、盘盈取批次成本 vs 取填的单价、凭证摘要不同。
2. 入库 IR 写成 JU（正）；4-3-2 是哪簿待核（疑 PI，对方科目是在制品 11-04-03 一类）。

**B. Express 有、Pearnly 没入口或没传**
1. **单据簿选择**：OU 类每家账套有多本（自用、样品、报损、生产领料…，各带默认科目），财务原生是按用途选簿；Pearnly 固定写 OU 一本，「用途」（内部领用 / 报损）没传过去。JU 类同理。
2. **单号**：原生用 ISRUN 流水；CC 自编号且白涨 ISRUN（原生下一张会跳号）。
3. 部门 DEPCOD：取 CC 桌面版设置，不是单据上选的；单头 / 明细的 JOBCOD、COSCOD、REFDAT、PEOPLE（出入库）Pearnly 都没有——原生界面上是否有这些栏待第二步看。
4. 科目：只能在第一次提交弹窗选一次、按公司记住；原生每张单都能改。
5. 行号补位：OU / JU 的 NXTSEQ、GLJNLIT.SEQIT 写的是否与原生补位一致待比（STCRD.SEQNUM 已右对齐）。

**C. Pearnly 有、Express 没处放（有意不同，建议保留）**
仓位、调入仓位、供应商、用途（若 B1 改成选簿就用上了）、附件、物料调整的按台号码（TK / JU 是轧差数）。

**D. 手机**：与网页同一页同一接口，字段一致；第二步照样各录一遍取证。

## 二、测试账套实录对照（第二步，待批）

计划见第 3 节发主会话的内容；批了再动测试机。

## 三、评估（第三步，待写）
