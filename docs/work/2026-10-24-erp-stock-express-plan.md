# ERP 出入库与 Express 对接方案（调研 + 方案，待主会话审定，未施工）

- 用户口径（主会话转）：「出入库是否符合 erp 正常逻辑以及和 exp 如何对接这个要摸清楚再拿方案做，所有功能必须验证合格且不会影响其它功能的正常使用才能上线」。
- 本文只读调研（代码、测试机 C 盘 102 套 T 账套、真账只读副本语料 158 套 / 约 224 万行 STCRD），**没有改代码、没有写 Express、没有碰 S 盘 / accserver**。
- 代码基线：集成分支 `pearnly/erp-batch-integration` `3b5ae066`。下文 `P/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`，`CC/` = `companion/src/companion/`，`R/` = `apps/pearnly_core/pearnly_core/express_rules/`。

## 一、结论先说

1. **接了 Express 的公司，库存的「账」在 Express**（数量按仓、成本、总账），ERP 是前台；但现在 ERP 只把采购单、销售单写进 Express，**收银台销售 / 退货、手工出入库、盘点调整、作废都不写**，而主档同步每 10 分钟把 ERP 库存「对齐」回 Express 结存——这些动作的数量会被同步抹掉（只按代码推导，未实测；见四.1）。这是方案要先解决的一致性问题。
2. Express 里每种出入库都有原生单据，真账用得很多：领用出库 OU / 生产领料 PD（POSOPR 6）、调整 JU（1 增 / 8 减）、完工入库 PI（1）、调拨 RL（4 出 / 3 入成对）、销售退回 SR / CN（2）、采购退货 GR（7）。CC 现在只会写 RR / HP / IV / HS（采购 / 销售）。
3. 成本：158 套里 128 套是 COSMTD=F（先进先出类）、30 套 A（平均）；CC 写销售出库成本时一律用移动均价，Express 有没有自己重算没核实；ERP 自己也是移动均价且不回读 Express 成本。**成本以哪边为准要定**（建议 Express，见五.2）。
4. ERP 门户缺的标准出入库能力：调拨、采购退货、销售单退货、生产领用 / 完工入库、按用途区分的领用科目、回溯重估。是否补、补哪些要按事务所实际用量排（见二、三的用量表）。
5. 建议分三期做（六），第一期只做「已有动作写进 Express + 同步不抹数」，不加 ERP 新功能；每期都有金样本 + 62 套离线回归 + 实验栈端到端 + 跨功能回归（七）。

## 二、ERP 门户现在的出入库（代码现状）

| 动作 | 入口 | 单据 / 流水 | 成本 | 写不写 Express |
|---|---|---|---|---|
| 采购入库（含费用单的货物行、LINE / 识别采购） | pearnly-purchase `api_post_purchase` | Purchase Document → `purchase_in` | 行净额 ÷ 数量 × 汇率（整单折扣不摊） | 写：RR / HP（采购订单不写） |
| 销售出库（含 LINE 销售） | pearnly-sales `api_post_sale`（只 receipt 出库） | ERP Sales Document → `sale_out` | 当前移动均价 | 写：IV / HS |
| 收银台销售 / 退货 / 作废 | pearnly-cashier | ERP Sale → `sale_out` / 退货记 `stock_in` / 反向流水 | 当前均价（退货按退货时均价） | **不写** |
| 手工入库（用途：退库 / 其它） | pearnly-stock-in | ERP Stock Entry IR → `stock_in` | 填了单价重算均价，否则当前均价 | **不写** |
| 手工出库（用途：内部领用 / 报损 / 其它） | pearnly-stock-out | ERP Stock Entry IS → `stock_out`（用途只记在单上） | 当前均价 | **不写** |
| 盘点过账 | pearnly-stock-count 结束 | 每个差异一张 AD 号出入库单 | 位置当前成本 → 商品默认成本 → 空 | **不写** |
| 期初 | 库存 → 录入期初 | ERP Stock Opening → `opening` | 导入的单价 | 不写（Express 有自己的期初） |
| 采购 / 销售 / 出入库作废 | 各页作废 | 反向流水（按冲回当时均价） | — | **不写**（已写进 Express 的单作废后两边不一致） |
| 调拨、采购退货、销售单退货、生产领用 / 完工 | **没有** | — | — | — |
| Express 库存对齐 | 主档同步（10 分钟 / 每小时 / 写单后 / 手动） | `adjust` 流水，ref `express_sync`，写到**空库位** | 不带价，按 ERP 当前均价 | 从 Express 读回：目标数 = Express 结存 + 还没写进 Express 的采购 / 销售单 |

其它要点（出处见代码）：库存流水只追加、结存表实时推进；只有移动加权平均；允许负库存且无开关；库位是自由文本；没有批次 / 序列号成本层、没有部门 / 项目维度；回溯补录不重估（`P/inventory/stock.py`、`contracts.py`、`report.py`）。

## 三、Express 的写法（真账统计 + 表结构）

### 三.1 表

- **STCRD**：所有库存明细（采购、销售、领用、调整、调拨、退货都在这张），关键字段 `DOCNUM`（前缀即单据簿）、`LOCCOD`、`POSOPR`（动作）、`TRNQTY / UNITPR / TRNVAL`（单据数）、`XTRNQTY / XUNITPR / XTRNVAL`（库存数量与成本）、`ACCNUMDR / ACCNUMCR`（行科目）、`DEPCOD`、`MREMBAL / MREMVAL`（结存）。
- **STTRN**：库存类单据（OU / JU / RL / PD / PI …）的单头，含 `POSOPR`、`LOCCOD`、`TRNVAL`、`ACCNUMDR / ACCNUMCR`、`POSTGL`（是否已过总账）、`DOCSTAT`。采购 / 销售单头在 APTRN / ARTRN。**语料没有 STTRN，C 盘 T 账套里 STTRN 为空** → 需白天以 skin3 从 S 盘只读复制统计（七.1）。
- **STLOC**：分仓结存（`LOCBAL`、各期 `QTYn / VALn`、`BEGBAL`）；5 套真账核过 `LOCBAL = BEGBAL + Σ各期数量`。**STMAS**：全仓结存 `TOTBAL / TOTVAL`、`UNITPR`、标准成本 `STNPR`、单品 `NEGALLOW`。
- **ISRUN**：单据簿（前缀、`DOCTYP`、`JNLTYP`、`ACCNUM01..12` 默认科目、默认部门 / 仓库）。C 盘 101 套里库存类簿：OU（828 条：จ่ายวัตถุดิบเพื่อผลิต 生产领料、จ่ายสินค้าเป็นตัวอย่าง 样品、ตัดสินค้าชำรุด 报损、จ่ายสินค้าใช้ภายใน 自用、สินค้าหายไป 丢失、บริจาค 捐赠、แถม 赠品 …）、JU（342 条：ปรับปรุงจากการตรวจนับ 盘点调整、รับสินค้าสำเร็จรูปจากการผลิต 完工入库、ปรับปรุงเพิ่ม/ลด 增减调整、จัดชุด 组套）、RL（106 条，调拨）。
- **ISINFO**：`COSMTD`、`NEGALLOW`、`ISPERPETUA`、`USEDEP`、`MAINLOC`、`LOC_COST`（是否按仓算成本）、`ANYLOC`、`CHKNEGLEV`、日记账类型 `INTISSJNL`（领用）、`TAKADJJNL`（盘点调整）、`COSADJJNL`（成本调整）。

### 三.2 POSOPR 与各单据簿的真实用量（语料 158 套 / 40 家公司）

| POSOPR | 含义（按数据推定） | 单据簿（行数 / 账套数） | 数量 / 成本列 |
|---|---|---|---|
| 0 | 采购入库 | RR 26.3 万 / 124、HP 7.5 万 / 52、RN 9.5 万 / 30、RO、RP、R1、RQ、RX | 正数，XTRNVAL = 入库值 |
| 1 | 调增 / 完工入库 / 盘盈 | JU 1.0 万 / 33、PI 1.7 万 / 14、JG 1.6 千 / 7、TK 909 / 11、JB | 正数；JU 样本单价 0、贷方科目 5120-04；PI 带单价、贷方 5130-99 |
| 2 | 销售退回 | SR 1.06 万 / **44**、CN、FC、SC | 正数；CN 样本 XTRNQTY = 0（退回不进库存成本？待核） |
| 3 / 4 | 调拨入 / 调拨出（同一张 RL 成对：4 从 01 出、3 进 02） | RL 3：5.2 万 / 30；RL 4：4.6 万 / 30 | 两行数量相同、XTRNVAL = 0（调拨不带价） |
| 5 | 成本调整 | CA 2.2 千 / 11 | 数量 0 |
| 6 | 领用 / 生产领料 / 报损等出库 | PD 5.3 万 / 12、OU 1.1 万 / 17、C1、DM、XA、OO、ZZ、PA、PB、PR … | 正数；OU 行借方科目在行上（如 51-03-18-00）；PD 借方空（走单据簿科目） |
| 7 | 采购退货 | GR 4.9 千 / **87** | 正数 |
| 8 | 调减 / 盘亏 | JU 2.9 千 / 23、JG 6.0 千 / 7、TK 633 / 5 | **负数** |
| 9 | 销售出库 | HS 69.6 万 / 32、IV 45.5 万 / 94、IE、IC、NV、HH、NT、HL … | 正数 |
| `>` / `<` | OE / OI（其它费用 / 收入单上的库存行） | OE 4.5 千 / 23、OI 3.4 千 / 15 | — |

设置分布（语料 158 套；C 盘 102 套同向）：COSMTD F 128 / A 30；NEGALLOW Y 106 / N 52；ISPERPETUA Y 129 / N 29；LOC_COST N 88 / Y 34 / 空 36；MAINLOC 01 占 142；USEDEP 全是 Y（但真账表头 82%–90% 不填部门）。

销售出库行成本：F 账套 146.6 万行里 36.4 万行（25%）`XUNITPR = 0`，A 账套 3.8 万行里 1.6 万行（41%）为 0 → Express 很可能在月结 / 「计算成本」时回填，或这些是负库存行（待核）。

### 三.3 CC 现在的写法（只写 RR / HP / IV / HS）

- 写表：APTRN / ARTRN、STCRD、STMAS、ISVAT、GLJNL / GLJNLIT、ISRUN、APBAL / ARBAL；载荷带仓库码（版本 4）时另写 STLOC（`CC/dbf_writer.py`、`dbf_sales.py`、`stock_location.py`）。
- POSOPR：采购 0、销售 9；数量都写正数；行上 LOCCOD = 载荷仓库码 → MAINLOC → `01`。
- 出库成本：CC 自己算全仓移动均价（零 / 负结存用 STNPR，再没有就不结转 COGS）；**不看 COSMTD、NEGALLOW、USEDEP**（只读出来交给服务端）。
- HP 现购一律贷现金 ACCNUM00（付款方式是「转账」也一样）。
- 不带仓库码时**不写 STLOC**（Cowork 现有推送都不带）。
- OU / JU / RL / PD / PI / SR / CN / GR、STTRN、POSTGL：**都没有写入代码**。

## 四、现在的问题（按影响排）

1. **同步抹掉没写进 Express 的动作**（接了 Express 的公司）：对齐目标只算「还没写进 Express 的采购 / 销售单」，收银台、出入库单、盘点、作废都不算 → 下次同步按 Express 结存记一笔 `express_sync` 调整，把这些数量调回去；调整还写到空库位，和原库位的结存分开（`P/express/masters.py:647-707`）。**推导结论，第一期先在实验栈实测确认**。
2. 已写进 Express 的单在 ERP 作废，Express 不跟（两边永久不一致）。
3. 成本两套：ERP 移动均价、CC 移动均价、Express 按 COSMTD（F 占 81%），三者不保证一致；ERP 不回读 Express 成本。
4. 没带仓库码的写入不维护 STLOC（Cowork 全部、ERP 新商品首单），按 STLOC 回读会少算。
5. NEGALLOW=N 的 52 套：CC 不拦负库存，Express 自己录单时会拦，CC 写进去会出现 Express 不允许的负数。
6. 转账付款贷现金（应贷银行科目或走付款明细，待核 Express 原生记法）。
7. ERP 缺调拨 / 退货 / 生产，事务所在 Express 里实际在用（RL 30 家账套、SR 44、GR 87、PD 12、PI 14）。

## 五、对接方案（逐项）

### 五.1 动作 → Express 单据

| ERP 动作 | 建议写成 | 单头 / 明细 | 仓库 | 成本 | 期 |
|---|---|---|---|---|---|
| 采购入库 | RR / HP（现有） | APTRN + STCRD(0) | 行仓库码；新商品首单也带（第一期补 STLOC） | 进价（现有） | 现有 + 一 |
| 销售出库 | IV / HS（现有） | ARTRN + STCRD(9) | 同上 | 见五.2 | 现有 |
| 收银台销售 | **HS 日汇总**（每台收银台 / 每天一张，CC 已有 Cowork POS 日汇总写法可复用） | ARTRN + STCRD(9) + POS 收款凭证 | 收银台仓 | 见五.2 | 一 |
| 收银台退货 | **SR / CN**（POSOPR 2，挂原 HS） | ARTRN + STCRD(2) | 收银台仓 | 待金样本 | 一 |
| 手工出库：内部领用 / 报损 / 其它 | **OU**，按用途选这套账里对应的 OU 单据簿（ISRUN 里 จ่ายสินค้าใช้ภายใน / ตัดสินค้าชำรุด …，每套账问一次记住） | STTRN + STCRD(6) | 单上的仓 | Express 成本（CC 按 COSMTD 或交 Express 计算，见五.2） | 一 |
| 手工入库：退库 / 其它 | **JU 调增**（POSOPR 1，单据簿 ปรับปรุงเพิ่ม/ลด） | STTRN + STCRD(1) | 单上的仓 | 单上填的单价，没填按 Express 当前成本 | 一 |
| 盘点过账 | **JU**（盘点调整簿 ปรับปรุงจากการตรวจนับ：盘盈 1、盘亏 8 负数） | STTRN + STCRD(1/8) | 盘点仓 | 同上 | 一 |
| 作废（已写进 Express 的单） | 不改原单；写**冲销单**：销售 → CN / SR；采购 → GR；OU ↔ JU 反向；或在 Express 侧作废（DOCSTAT）——**待金样本定哪种是 Express 原生做法** | — | 原仓 | 原单成本 | 一 |
| 调拨（ERP 新功能） | **RL**（同一张单两行：4 出源仓、3 入目的仓，不带价） | STTRN + STCRD(4/3) + 两边 STLOC | 源 / 目的仓 | 不带价（Express 原生） | 二 |
| 生产领用 / 完工入库（ERP 新功能：出库用途「生产」、入库用途「完工」） | **PD**（6）/ **PI**（1） | STTRN + STCRD | 单上的仓 | 领用按成本；完工按单价或 Express 计算 | 二 |
| 采购退货（ERP 新功能） | **GR**（7）+ 应付冲减 | APTRN + STCRD(7) | 原仓 | 原进价 | 二 |
| 销售单退货（ERP 新功能） | **SR / CN**（2）| ARTRN + STCRD(2) | 原仓 | 待金样本 | 二 |
| 期初 | 不写（Express 有自己的期初 / BEGBAL）；接了 Express 的公司期初从 Express 读回，不让在 ERP 录 | — | — | — | 一（入口限制） |

单据号、单据簿：一律用这套账 ISRUN 里已有的簿（不新建前缀）；每种用途第一次用时问会计选一次、按账套记住（与「存货科目组」同一做法，按类通用、不写死某家公司）。

### 五.2 成本以哪边为准（建议）

- **接了 Express 的公司：成本以 Express 为准**（总账在 Express，F/A 由会计在 Express 里定）。ERP 的库存金额、毛利只做参考，同步时**回读 Express 的单位成本**（STLOC / STMAS），ERP 不再自己滚移动均价作为结论。
- CC 写出库行成本：第一步先做金样本核实「Express 会不会在月结 / 计算成本时按 COSMTD 回填 XUNITPR」——
  - 会：CC 继续写近似成本（或写 0 交给 Express），以 Express 计算后为准，ERP 读回；
  - 不会：CC 要按 COSMTD 实现 F（先进先出）/ A（平均）出库成本（工作量大，放第三期）。
- 没接 Express 的公司：ERP 自己的移动均价为准（现状）。

### 五.3 库存余额以哪边为准

- 接了 Express 的公司：**按仓数量以 Express 的 STLOC 为准**。前提是第一期把 ERP 所有动库存的单都写进 Express（五.1），对齐目标改成「Express 结存 + 所有还没写进 Express 的 ERP 库存单（不止采购 / 销售）」，调整写到原库位而不是空库位。
- 没接 Express 的公司：ERP 自己为准（现状）。

### 五.4 两边差异怎么处理

- 同步对齐时：差额照记 `express_sync` 调整（保持 ERP 与 Express 一致），但**留一份差异清单**（商品、仓、ERP 数、Express 数、差额、原因猜测：Express 里直接录的单 / 写入失败 / 作废未跟），在公司页 Express 区显示，只列不拦。
- 写入失败 / 待人处理的单：沿用现有「需要人工」弹窗与待处理状态；对齐时把这些单算进「还没写进」，不抹数。
- NEGALLOW=N 的账套：写出库前按 Express 结存核，不够就停在「需要人工」（回报码新增「库存不足」），不写成 Express 不允许的负数；NEGALLOW=Y 照写（现状）。

### 五.5 挂着的三项

| 项 | 方案 | 影响面 | 验证 |
|---|---|---|---|
| 不带仓库码的写入也维护 STLOC | CC 写库存行时，没有仓库码就按实际写入的 LOCCOD（MAINLOC / 01）同样维护 STLOC（与带码时同一套代码） | **Cowork 全部租户的库存推送**都会变（多写 STLOC），属共用写账路径 | 62 套离线回归：写前后 `LOCBAL = BEGBAL + Σ各期数量` 不变式成立、其它表逐字节不变；Cowork 实验栈推一张库存单读回 |
| 出库成本与 COSMTD 核对 | 先做金样本（五.2），再定是否实现 F/A | Cowork 与 ERP 的销售出库 | 金样本 + 62 套离线回归对比 Express 计算后的成本 |
| 转账付款贷现金 | 先在 C 盘用 Express 程序本身录一张「现购、转账付款」的 HP 当金样本，看它贷什么（银行科目 / 付款明细表）；照原生记法改共用写账规则（付款方式「转账」→ 贷这套账的银行科目，每套账选一次） | Cowork 与 ERP 的现购（HP / HS） | 金样本对照 + 62 套离线回归 + 两边实验栈各推一张读回 |

## 六、分期建议

| 期 | 内容 | 不做 |
|---|---|---|
| 一（一致性，不加 ERP 新功能） | 收银台日汇总 HS、收银台退货 SR / CN、手工出入库 OU / JU、盘点 JU、作废冲销；对齐目标算全部未写入单、调整写原库位、差异清单；期初入口对接了 Express 的公司改为读回；不带仓库码也维护 STLOC；NEGALLOW=N 拦库存不足 | 调拨、退货单、生产 |
| 二（补 ERP 标准能力，按用量排） | 采购退货 GR（87 套在用）、销售单退货 SR / CN（44）、调拨 RL（30）、生产领用 PD / 完工 PI（12 / 14） | 成本法实现 |
| 三（成本） | 视金样本结论：回读 Express 成本为准，或 CC 实现 F / A；转账付款贷银行 | — |

## 七、验证办法与回归范围

### 七.1 开工前要补的事实（不改代码）

1. **S 盘只读统计**（白天 accserver 在线、以 skin3 身份、先复制到临时目录再读）：STTRN 的 POSOPR / POSTGL / DOCSTAT 分布、OU / JU / RL / SR / GR 的单头字段怎么填、作废单在 STCRD / STTRN 里的样子；`STLOC` 与 STCRD 对账。
2. **金样本**（在 C 盘 T 账套里用 Express 程序本身各录一张，读回所有表的变化）：OU、JU（增 / 减）、盘点调整、RL、SR / CN、GR、作废一张已过账的 IV、现购转账付款 HP、月结 / 计算成本前后的 XUNITPR。**需要有人在测试机上操作 Express 程序**（或经用户同意用远程桌面自动化），这是第一期开工的前置条件。
3. 实验栈实测四.1（收银台卖一件 → 同步后 ERP 结存是否被调回）。

### 七.2 每项的验证

- 写入器：金样本逐字段对照 + 62 套离线回归（写前备份、写后不变式：STLOC = BEGBAL + Σ期数量、STMAS 结存 = Σ STLOC、GL 借贷平、ISRUN 计数器 +1、其它表逐字节不变）+ 实验栈端到端（ERP 页面操作 → CC 写 C 盘 → `readback.py` 读回）。
- 同步对齐：实验栈造「ERP 有未写入单 / 写入失败单 / Express 里直接录的单」三种情况，看对齐结果与差异清单。
- 成本：金样本 + 对比 Express 计算后的成本。

### 七.3 回归范围（每期上线前）

- Cowork：所有推送类型（采购 / 销售 / 库存 / POS 日汇总）照旧写入、读回一致（共用写账规则与 CC 写入路径变了的话必测）。
- ERP：采购、销售、收银台（开单、退货、作废、班次）、出入库单、盘点（含扫码 16 个入口里涉及库存的）、期初、库存页 / 库存卡 / 收发存报表、Express 同步、公司页。
- CC 桌面版：两种成员的心跳、写入、按需读取；`docs/work/2026-10-24-erp-batch-regression-entries.md` 里第六、八、十、十一节作为固定回归集。
- 生产镜像完整候选离线检查、本地全闸门、分支 CI。

## 八、要主会话 / 用户定的

1. 接了 Express 的公司，成本与按仓数量是否以 Express 为准（本文建议是）。
2. 分期与先后（本文建议一 → 二 → 三，第二期按用量先 GR / SR）。
3. 作废已写进 Express 的单：写冲销单，还是 Express 侧作废（金样本出来后再定，先定原则：不改 Express 原单）。
4. 金样本由谁在测试机上操作 Express 程序（前置条件）。
5. NEGALLOW=N 的账套库存不足时停在「需要人工」是否可以。
