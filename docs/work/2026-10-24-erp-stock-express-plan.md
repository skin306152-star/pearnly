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

1. **同步抹掉 ERP 的库存动作**（接了 Express 的公司）：对齐目标只算「还没写进 Express 的采购 / 销售单」，收银台、出入库单、盘点、作废都不算 → 下次同步按 Express 结存记一笔 `express_sync` 调整，把这些数量调回去；调整还写到空库位，和原库位的结存分开（`P/express/masters.py:647-707`）。
   **10-25 实验栈实测属实，而且范围更大：已写进 Express 的采购也会被抹掉**（见九.1）。生产只读：真实租户 mrerp 没有电脑 / 账套接 Express，线上没有发生；第一期必须在任何真实客户接 ERP 之前上线。
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

## 九、主会话 10-24 决定后的补充调研（10-25，只读 / 实验栈，不改代码）

主会话审定第八节：成本与按仓数量以 Express 为准（ERP 回读单位成本与 STLOC）；分期一 → 二 → 三（二期 GR、SR / CN → RL → PD / PI）；作废不改 Express 原单（冲销还是 Express 侧作废按样本定）；
NEGALLOW=N 库存不足停在「需要人工」；金样本先最大化用 S 盘只读样本；四.1 先实测；第一期动共用写账路径时 Cowork 全部推送类型进回归。

### 九.1 四.1 实测（实验栈 testerp、main `d5b2c3c9` 同代码的镜像 `0d6bbeb8`，公司 CO-42069 ↔ C 盘 T9HENG15，测试机 CC 1.12.0）

| 场景 | ERP 流水 | 同步后 | 结论 |
|---|---|---|---|
| 手工出库 1 件（IS2569-000001，PRD-167899 / Express PN00002，仓 01），不写 Express | 00:02:19 `stock_out` −1，结存 −1 | 00:02:37 同步记 `adjust` +1（`express_sync`），结存回 0 | **没写进 Express 的动作被抹掉：属实** |
| 采购入库 3 件（PUR-2026-167900，新品，已写进 HP691024-010） | 22:39:04 `purchase_in` +3 | 22:39:25 同步记 `adjust` −3，结存 0；Express STMAS 结存 3 | **已写进 Express 的采购也被抹掉** |

原因（逐层核过）：

- 主档同步读的是 Express **STLOC**（`CC/express_masters.py` 的 `stock`：STLOC 的 LOCBAL），按「STLOC 结存 + 未写入单」对齐（`P/express/contracts.stock_targets`）。
- ERP 给 CC 的载荷只在「建单时商品已挂 Express 编码」时才带仓库码（`P/express/contracts.document`：`loccod … if code`）。本地商品、随单新建的物料、预检时才选的现成 Express 物料，行上都没有仓库码 →
  载荷是旧版（实验栈 PUR-2026-167900 版本 1、PUR-2026-167882 版本 2，`items` 里都没有 `loccod`）→ CC 不碰 STLOC（`CC/stock_location.py`「一个 loccod 都没带的载荷与以前逐字节相同」）。
- C 盘 T9HENG15 只读核对：PN00002 STMAS 结存 3、**STLOC 没有这件货的行**；「1 -    -」STMAS 结存 128、STLOC 01 / 02 的 LOCBAL 都是 0（HP691024-004 的 125、-011 的 3 都只进了 STCRD / STMAS）。
- 所以 Express 自己的分仓结存也和总结存对不上（Cowork 的推送从来不带仓库码，同样如此，即四.4）；ERP 以 STLOC 为准对齐时就把这些数量清掉。

第一期最先修（方案不变，补两点）：对齐目标算全部未写入的库存单；**所有库存行都带仓库码、CC 不论有没有仓库码都按实际写入的仓维护 STLOC（含新建物料的 STLOC 行）**；
对齐前先核「STLOC 合计 = STMAS 结存」，不一致的货不按 STLOC 抹 ERP 数，进差异清单。实验栈留下的测试单：STE-2026-167906（IS2569-000001）与两笔同步调整。

主会话 10-25 批准上面两点（库存行都带仓库码、CC 按实际写入的仓维护 STLOC 含新建物料行；对齐前核 STLOC 合计 = STMAS 结存，不一致不抹数、进差异清单），并补：

- **存量差异**：已有账套的 STLOC 历史上就和 STMAS 对不上（Cowork 推送一直不带仓库码）。第一期不擅自改 Express 的存量数据：对齐时对不上的货只列差异、ERP 不按 STLOC 抹数；
  要修 Express 存量（例如按 STCRD 重算 STLOC）须先另出方案给主会话定。第一期交付前用 62 套离线副本统计各账套存量差异的件数与金额，写进方案。
- **上线约束：第一期上线之前，生产不给任何真实客户接 ERP 和 Express。**（生产 10-25 只读：真实租户 mrerp 没有电脑 / 账套接 Express。）
- 排期：先做「共用件连带回归闸门」工作包（`pearnly/regression-safety-net`），第一期写代码排在它合入之后；S 盘取样白天 accserver 在线时做；测试机归验收窗口，动实验栈 / 域名 / CC 前先问主会话。

### 九.2 并进第一期的缺陷：CC 成员拿着服务端不认的旧密钥，永远 401（主会话 10-25 登记）

- 现象（验收窗口线上补测）：测试机 CC 的 ERP 成员（testerp，`cc_desktop` 通道）手里是实验栈发的密钥，生产不认，每分钟心跳 401；这个成员没被标成「密钥失效」，
  用户打开 ERP 对话页登记只回「已登记」、不发新令牌（生产这台电脑 10-23 已有令牌）→ 永远卡在 401、不会自己恢复。同类：实验栈与生产共用域名和设备密钥；生产库从备份恢复后。
- 要求（按类通用自愈）：对话页登记能换新（服务端在设备已有令牌、但 CC 报自己 401 时重新发令牌），或 CC 把这个成员标成需要换新、下次打开对话页自动换；Cowork 成员查同类问题；
  补回归用例「本地旧密钥 + 服务端已有令牌」。临时绕过：用户手动移除成员再重新登记。

九.2 修法（代码已核，第一期做）：
- 现状：CC 桌面版已有「这把密钥服务端不再用」的成员标记 `device_state`（`CC/config.py` 有它就不干活；对话页 `needsKey` 看到它就带 `rekey` 重新登记，服务端 `cc_desktop.devices.register`
  收到 `rekey` 会重新发令牌）。但只有 Cowork 通道在「认电脑」回 `superseded / claimed_elsewhere` 时才打这个标记（`CC/device_link.py`）；**401（`KeyRejected`）故意不打**，
  ERP 的 `pcc_` 成员根本不走「认电脑」。所以两种成员拿着服务端不认的旧密钥时都只会一直 401，对话页登记回 `exists`、不换新。
- 改法（按类通用）：成员心跳 / 取任务连续收到 401（例如两轮、间隔 ≥ 1 分钟，避开偶发）→ CC 给这个成员打 `device_state = "rejected"`、停下这个成员的心跳与写账；对话页 `needsKey` 已认任何非空
  `device_state` → 打开对话页时自动带 `rekey` 登记 → 服务端发新令牌 → 配对换上新密钥、标记清掉。Cowork 成员同样适用。服务端不用改（`rekey` 已只许登记人 / 管理员）。
- 回归用例：CC 侧「本地旧密钥 + 服务端已有令牌」：连续 401 后成员带 `rejected`、不再干活；对话页侧 `needsKey` 为真时 `exists` 后再带 `rekey`；服务端 `rekey` 发新令牌、旧令牌即失效。

### 九.3 第一期改动落点（代码已核，开工时以此为单）

| 项 | 现状（文件） | 第一期改成 |
|---|---|---|
| 对齐目标 | `P/express/masters._pending` 只算 `ERP Sales Document` 与 `purchase` 两种 ref 的未写入单 | 所有库存流水来源都算：`stock_entry`（入库 / 出库 / 盘点 AD 单，`P/inventory/documents.py`）、`sale` / `refund`（收银台，`P/cashier/sales.py`）、作废的反向流水；每种单都有自己的 `express_state`，写进 Express 前一律算「未写入」 |
| 对齐写到哪 | `masters._stock` 的 `apply_movement` 不带库位 → 记到空库位 | 记到这件货在这个仓的原库位（`positions` 的默认库位），不另起一行空库位 |
| 对齐前核对 | 直接按 STLOC 抹 | 先核每件货 STLOC 合计 = STMAS 结存，不等的不抹 ERP 数、进差异清单（公司页 Express 区显示，只列不拦） |
| 库存行的仓库码 | `P/express/contracts.document`：`loccod … if code`（只有建单时已挂 Express 编码的商品才带） | 库存行一律带仓库码；预检时才选定的现成物料、随单新建的物料也带 |
| CC 维护 STLOC | `CC/stock_location.py`：一个 loccod 都没带的载荷不碰 STLOC；新建物料不建 STLOC 行 | 不论带没带仓库码都按实际写入的仓维护 STLOC（没带就是 MAINLOC），新建物料补 STLOC 行；**改的是 Cowork 也走的共用写账路径**：62 套离线回归（STLOC = BEGBAL + Σ期、STMAS = Σ STLOC、其它表逐字节）+ Cowork 全部推送类型回归 + 共用件连带回归闸门清单 |
| 新增写入 | 只写 RR / HP / IV / HS | 收银台日汇总 HS、收银台退货 SR / CN、手工出入库 OU / JU、盘点 JU（单据簿按用途每套账问一次记住）；作废按样本定的原生做法 |
| NEGALLOW=N | CC 不拦 | 写出库前按 Express 结存核，不够停在「需要人工」（新回报码，文案按规矩登记） |
| 401 自愈 | 见九.2 | **已上线**（10-25 生产 `08bfb419`，CC 1.12.1；线上 testerp 换新密钥验收通过） |
| ERP 写入冒烟只写 C 盘 | `scripts/smoke/express_flow.py` 写到哪份账套全看 CC 给这家公司报的账套，测试机白天连上 accserver 时会把 S 盘同税号真账也报上来 | **已做**（分支 `pearnly/erp-smoke-guard`）：建单前这家公司报上来的账套必须全部是 `C:\PearnlyExpress\` 下以 T 开头的才建单，写完按单据的 `express_book` 再核实际路径（同 Cowork 版 `cowork_express_flow.py`）。第一期新增的出入库写入冒烟沿用这个检查 |
| CC 旧授权目录（按类缺陷） | 已核代码：`express_machine.root_gone`（所在盘能访问、文件夹却不在才算没了）只在用户手动触发的路径上用——「重新查找」、改位置、还没有成员时的自动查找（`desktop_settings` → `rescan_roots` / `offline_roots`）；平时的定时重扫只扫数据根下的账套，不删数据根；配对 / 重连走 `pairing_flow.run(roots=machine_roots(store) or _detected)`，新成员的 `authorized_roots` 直接照抄现有成员的并集（`pairing_flow.py:88`），断开后记下的 `_kept` 也不过 `root_gone`。所以已经没了的目录（测试机 `C:\PearnlyExpress\71EXP` / `72EXP`）不会自己清掉，还会被带进新成员，每次配对都报「暂时无法读取」 | 一处收口：配对、重连、定时重扫给成员定数据根前都过 `root_gone`（同一次查找共用 `asked`，每台服务器只问一次），确实没了的去掉；盘本身不可达（网络盘掉线、服务器关着、U 盘拔了）的照旧保留。补用例：断开再重连、新成员配对、定时重扫三条路径都不带回已经没了的目录，也都不丢暂时访问不到的。随第一期一起发 CC |
| 存量差异 | — | 不改 Express 存量；62 套离线统计各账套 STLOC ≠ STMAS 的件数与金额写进本文；要修另出方案 |

开工前置：共用件连带回归闸门（`pearnly/regression-safety-net`）合入；S 盘只读金样本（九.4，待白天 accserver 在线）补齐作废 / 退货 / 领用 / 调整的原生字段。

### 九.4 S 盘只读金样本（计划；10-25 下午已做，结果见九.6）

- 选账套（从测试机真账旧副本语料 158 套按单据用量挑，旧副本的 `来源.txt` 记着 S 盘路径）：68POLYM（OU、JU 增 / 减、RL、SR、GR，ISPERPETUA=N 定期盘存）、68SKCHE 与 69SKCHE（PD、PI，同一家两年，
  推断月结前后 XUNITPR 回填）、68NLTB（RL 1.07 万行、SR、GR、HS，NEGALLOW=N、LOC_COST=Y）、6907GIZ（RL、JU）、6907WNF（CN）、6902ASC（GR）、6819EXCP 与 68PAISAR（HP，找「现购转账」）。
- 做法：以 skin3 身份在测试机（S 盘是 skin3 的映射）把这几本的 STTRN、STCRD、STLOC、STMAS、GLJNL / GLJNLIT、ISRUN、APTRN / ARTRN、ISINFO、ISTAB、APMAS / ARMAS、ISVAT、
  APRCPIT / ARRCPIT、GLACC 只读复制到 `C:\PearnlyStress\_stock_sample_1025`（`shutil.copy2` 只读源文件）；再只读分析副本：每类各挑最早 1 张 + 最近 2 张原生单，拼出它在各表的全部非空字段；
  作废单（DOCSTAT=C 等）每表最多 3 张；STLOC 合计与 STMAS 结存是否一致；销售出库 XUNITPR=0 按月分布；与语料旧副本同一行比 XUNITPR 前后变化（推断月结 / 计算成本是否回填）。
- 旧副本试跑已看到的（68NLTB、68POLYM，只读）：作废标记在 APTRN / ARTRN 的 DOCSTAT（C = 作废，另有 M / R 用在收款类）；ISPERPETUA=N 的账套销售出库成本全是 0（定期盘存不结转）；
  68NLTB（永续盘存）每月只有 3%–5% 的销售行成本为 0。STTRN 在旧副本里没有，要靠这次 S 盘复制。
- 做完列「只读样本仍答不了、必须在 C 盘用 Express 程序录一张看前后变化」的最小清单（每条写为什么），交主会话请用户安排办公室的人录。

### 九.5 并进成本项的已知现象（主会话 10-25 转验收）

- 先卖后买（负库存）时，销售那张没有成本分录：CC 卖出时这件货无成本基础（零 / 负结存、也没有标准成本）就不结转 COGS（`CC/stock_sale.py` 的设计），之后进货也不回补那张销售的成本。
  并进第一期（NEGALLOW=N 停在「需要人工」，不再产生这类单）与第三期（成本以 Express 为准：样本确认 Express 月结 / 计算成本会不会回填，会就回读，不会就由 CC 按 COSMTD 补结转）。

### 九.6 S 盘只读金样本结果（10-25 下午，用户确认服务器在线后做）

- 做法：以 skin3 身份一次性计划任务，把 9 本账套的出入库相关表 `shutil.copy2` 只读复制到测试机 `C:\PearnlyStress\_stock_sample_1025`（55 秒，约 645 MB），
  另补 6907GIZ 的 BKMAS / BKTRN / BKTRNIT；之后只读分析副本，**做完副本已删**。没写 S 盘 / accserver，没起实验栈，没动 CC。
- 9 本：68POLYM、68SKCHE、69SKCHE、68NLTB、6907GIZ、6907WNF、6902ASC、6819EXCP、68PAISAR。COSMTD 全是 `F`；NEGALLOW=N 的有 68NLTB、68PAISAR、6907GIZ；
  ISPERPETUA=N（定期盘存）的有 68POLYM、6902ASC、6907WNF。
- **STLOC 合计 = STMAS 结存**：9 本里 7 本完全一致，68POLYM 4878 件里差 1 件、6907WNF 159 件里差 1 件（都是负数 / 零结存的旧件）。
  和 C 盘 T 账套一样，存量差异少，第一期的「差异只列不拦」够用。

**库存单（STTRN + STCRD）**

| 类 | STTRN | STCRD | 过账（GLJNL） |
|---|---|---|---|
| OU / XS / XC / ED / OR / ZZ / PD 及 68PAISAR 的 PA、PB、PC… 领用 / 发料 | POSOPR=6；`ACCNUMDR` = 费用 / 在制科目；LOCCOD；REMARK；TRNVAL | 每行 POSOPR=6，TRNQTY / XTRNQTY、XUNITPR、XTRNVAL、MREMBAL / MREMVAL（写后结存），ACCNUMDR 同单头 | 一张 **JNLTYP=00** 普通日记账，VOUCHER = 单号，摘要「จ่ายสินค้าภายใน + 单号」；借 STTRN.ACCNUMDR，贷存货科目（PD 按物料分到多个存货科目） |
| JU / PI / JG / CARR 调整 / 收料 | POSOPR=8（CARR 为 5）；`ACCNUMCR` = 对方科目；JU 数量可正可负 | 行 POSOPR=8（另有 POSOPR=1），带 LOTBAL / LOTVAL / LUNITPR / VALCHG | 同上，借存货、贷 STTRN.ACCNUMCR（JU 为负时方向相反） |
| RL 仓间调拨 | POSOPR=4；LOCCOD = 调出仓，PEOPLE = 调入仓 | 每件一对：POSOPR=4（调出）+ POSOPR=3（调入）；部分行 REIMBURSE=`ฝ` | **不过账**（6907GIZ 258 张 0 张过账）；68NLTB 只有「ปรับต้นทุนโดยโปรแกรม」（程序调成本）那几张有凭证 |

- **过账金额跟 STCRD 的 XTRNVAL 走，不跟 STTRN.TRNVAL 走**：69SKCHE `PD690630-001` STTRN.TRNVAL 343,848.49，STCRD 合计与 GL 都是 344,670.13。
  同一本账套 08-29 的旧副本到 10-23 的这份之间，4 行 PD 的 XUNITPR 被改写（193.696 → 195.531 等），说明 Express 重算成本会改写 STCRD 与凭证，不改 STTRN.TRNVAL。
- **零金额的库存单不过账**（6907GIZ 的 OU、JU、JG，68PAISAR 的 AT、OR 没过账的都是 TRNVAL=0）。**定期盘存账套的库存单一律不过账**（68POLYM 的 OU 2595 张、JU 115 张、RL 206 张在 GLJNL 里 0 张）。
- STTRN.POSTGL 在所有样本里都是空，过账与否看 GLJNL 有没有同号凭证。
- 用 STCRD 按 POSOPR 方向（0 / 1 / 2 / 3 / 5 / 8 加，4 / 6 / 7 / 9 减）重算各仓结存：69SKCHE 3725 行与 STLOC.LOCBAL 全部一致，方向判定无误；
  跨年的账套（6907GIZ、68NLTB）有出入，是 BEGBAL 只含本年度起点，不影响方向结论。

**销售 / 采购的退与改**

| 类 | 单头 | STCRD |
|---|---|---|
| SR / CN / EC / SE（ลดหนี้、退货） | ARTRN RECTYP=5，CNTYP=1 / 2 / 3，SONUM、YOUREF，ISVAT VATREC=S | POSOPR=2；`RETSTK`=Y（退回仓）/ N / 空；RDOCNUM = 原单号 + 行号（CNTYP=3 时为 `AVGPR`，不挂原单）；CNTYP=1 的行 XTRNVAL 多为 0 |
| GR（采购退货） | APTRN RECTYP=5，DNTYP=2，PONUM = 原进货单，ISVAT VATREC=P | POSOPR=7；RETSTK=Y / N；RDOCNUM = 原 RR + 行号 |
| DX*（เพิ่มหนี้ 加价单） | ARTRN RECTYP=4，CNTYP=2 | POSOPR=`=`，服务料号，不动库存 |

**作废**：DOCSTAT=C 的单（IV、RR、RL、IC、RN…）**单头保留、STCRD 明细删掉**，IV / RR 的凭证也删掉；ISVAT 那一行保留并标 C。
DOCSTAT=M / R 只出现在收付款单（PS、RE），不是作废。

**收银（HS / HSP / HSPB / HSPO / HSPM / HH）**：一张收据一张单，ARTRN RECTYP=1（现销），STCRD POSOPR=9，和 IV（RECTYP=3）只差 RECTYP；
68NLTB 各分店 / 各台用不同前缀。样本里没有 Express 原生的「日汇总」单——收银日汇总写成什么样是产品决定（一天一张 HS、客户用现销客户），不需要样本。

**现购转账（HP）**：现金付的 APTRN 记 `CSHPAY`，凭证贷现金科目（1111）；转账付的 APTRN 记 `CHQPAY` 与 `CHQPAS`，凭证贷银行科目（6907GIZ 为 1112-07），
同时 BKTRN 写一行：`BKTRNTYP=bP`、`BNKACC`（对应 BKMAS 的账户，C5 → 1112-07）、`VOUCHER` = HP 单号、`CHQNUM` = 「SC」+ 单号、`CHQDAT` / `GETDAT` = 单据日、
`CHQSTAT=19`、`JNLTRNTYP=1`、`CUSCOD` / `NAME` = 供应商、AMOUNT / NETAMT。6907GIZ 的 HP 里 389 张现金、161 张转账、其余混合（含预扣税 2132-02）。

**成本（XUNITPR）**：定期盘存账套销售行 XUNITPR 全是 0；永续盘存的 68NLTB 每月 2%–8% 的销售行是 0；69SKCHE 1–6 月 0 张为零、7 月 48 / 127、8 月 86 / 163 为零，
08-29 → 10-23 之间这些零没有被补上（只改写了 6 月 PD 那 4 行）。说明「计算成本」是人手动按月跑的，没跑的月份销售成本一直是 0——见九.7 第 1 条。

### 九.7 只读样本答不了、必须在 C 盘 T 账套用 Express 程序录一张看前后的最小清单（交主会话审；10-25 下午重排）

每条都要在测试机 `C:\PearnlyExpress` 的 T 账套里由人用 Express 程序操作一次，我们录前后各复制一份比对全部表（只读比对，写入的是 T 账套）。
第 1 条决定 A 段能不能部署（主会话 10-25：A 段改变 Cowork 写进真实客户账套的内容，这条核实前不部署）。

1. **负库存按仓还是按全仓拦、分仓能不能为负**：在 NEGALLOW=N 的 T 账套里，让一件品只在 02 仓有货、01 主仓为 0（全仓 > 0）：
   - 用 Express 程序从 01 仓录一张销货，看拦不拦；把 ISINFO.CHKNEGLEV 换 0 / 2 各录一次，顺带看 NEGAUTH；
   - 录完看 01 仓的 STLOC.LOCBAL 能不能变负（验收 10-25 实测：现在不带仓库码按全仓放行、写进主仓，T9NIEX12 的 USD 主仓已是 −6、全仓 +3）。
   Express 不允许分仓为负 → 不带仓库码的行改成从有货的仓扣之类（九.8「待主会话定」那条一起定）；允许 → A 段现状即可。
   只读样本答不了：6907GIZ（NEGALLOW=N、CHKNEGLEV=2、多仓）5289 条出库行写后全仓结存一次都没变负，说明至少按全仓拦；STCRD 只有全仓滚动结存
   （MREMBAL），没有各仓的，按录入顺序重算各仓结存在单仓的对照账套 6819EXCP 上都对不上（同一天里的先后复原不了）。68NLTB 另有 240 条出库后
   全仓变负，集中在 2021 年的收银单（HSPM 等），像是收银程序导入、没经过 Express 的检查。
2. **作废一张 OU（或 JU）和一张 IV**：样本只看到作废后的结果（单头留、明细删、IV 凭证删），看不到 STLOC / STMAS 是否回滚、库存单凭证是删还是标 C、
   单号会不会被再用。B 段「作废冲销」等这一条。
3. **跑一次「计算成本」（ประมวลผลต้นทุน）**：选一本有 XUNITPR=0 销售行的 T 账套（先由 CC 写一张先卖后买的单造出来），看会不会补上销售行的
   XUNITPR / XTRNVAL、改不改 IV 与库存单的凭证金额、动不动 STTRN.TRNVAL。决定第三期成本是「回读 Express」还是「CC 按 COSMTD 补结转」；
   B 段领用 / 调整的成本先照销货用移动均价（`stock_sale` 现状），这一条核实后再定要不要改。
4. **现购转账 HP 一张**（选一本有 BKMAS 的 T 账套）：确认 BKTRN 之外不再写别的表（APRCPCQ 等），以及 `CHQNUM`（样本是「SC」+ 单号）与
   `CHQSTAT=19` 是程序自动给的还是人填的。B 段「转账付款」等这一条。
5. **退货 RETSTK=N 与减价 CNTYP=1 各一张**：看 STLOC / STMAS 动不动（样本里有这两种行，但没有能把它们和结存对上的账套）。
   B 段收银退货只用 RETSTK=Y（货回仓），不等这一条；ERP 现在没有减价 / 不回仓的退货。
6. （可选）**仓间调拨 RL 一张**：确认 STCRD 那一对 4 / 3 行与 REIMBURSE=`ฝ` 是什么意思。ERP 现在没有调拨单，不录不挡任何项。

不需要录的（样本已答清）：OU / JU / PD / PI / TK 的单头、明细、编号（ISRUN）与过账、零金额与定期盘存不过账、SR / GR / CN 的字段、
收银单结构、作废后的表面状态、HP 转账的 BKTRN 字段。

### 九.7b 库存单过账的写法（10-25 下午补读，S 盘只读，副本已删）

- 凭证头 GLJNL：`JNLTYP=00`、`VOUCHER` = 单号、`VOUDAT` = 单据日、`TRNSTAT=P`、`DOCSTAT=N`、`SRCJNL` 空；摘要 `DESCRP` 取 STTRN.REMARK，
  没写备注时领用类（DOCTYP OU：OU / PD 及各账套自设前缀）是「จ่ายสินค้าภายใน + 单号」，调整类（DOCTYP JU：JU / PI / TK）是「ปรับปรุงสินค้า + 单号」。
- 分录 GLJNLIT：每行 `SEQIT='1'`（同一张凭证的行全是 1），`TRNTYP` 0 借 / 1 贷，`DESCRP` 同凭证头，`DEPCOD` 取单头。
  领用：借 STTRN.ACCNUMDR，贷各物料的存货科目（按物料科目组分行，PD 样本贷 1140-04 / 05 / 06 三行）；调整为正：借存货、贷 STTRN.ACCNUMCR，为负反过来。
  金额 = 明细 XTRNVAL 合计。
- 编号 ISRUN：`DOCTYP` OU / JU / CA 各有若干前缀（OU「จ่ายสินค้าใช้ภายใน」、PD「จ่ายวัตถุดิบเพื่อผลิต」、JU「ปรับปรุงเพิ่ม/ลดสินค้า」、
  PI「รับส/คสำเร็จรูปจากการผลิต」、TK「ปรับปรุงจากการตรวจนับ」），`DOCNUM` 是下一个流水；默认科目：OU 类 `ACCNUM01`（借方费用），JU 类 `ACCNUM02`（对方科目）。
- 明细 STCRD：领用 POSOPR=6、调整 POSOPR=8（数量可正可负），带 ACCNUMDR / ACCNUMCR、LOCCOD、TQUCOD / TFACTOR、TRNQTY / XTRNQTY、
  UNITPR / XUNITPR、TRNVAL / XTRNVAL、MREMBAL / MREMVAL；先进先出账套另带 MLOTNUM（领用指向被消耗的进货批次）、LOTBAL / LOTVAL / LUNITPR（收料批次）。
  CC 现在的销货不维护批次（金样本 HS580101-001，移动均价），B 段沿用；批次由 Express「计算成本」重排（第 3 条核实）。

### 九.9 第一期 B 段：样本已能定写法、先开工的项（10-25，主会话同意先开工）

ERP 这边要写进 Express 的库存单据只有三类（没有独立的销售退货 / 采购退货单据）：ERP Stock Entry（出库：内部领用 / 报损 / 其他；入库：退库 / 其他）、
盘点调整、收银（含退货）。按依赖排：

| 顺序 | 项 | Express 写法 | 等不等 C 盘 |
|---|---|---|---|
| B1 | ERP 出库单 → **OU**，入库单 → **JU**（正） | STTRN + STCRD + STMAS + STLOC + 凭证（九.7b）；编号取 ISRUN 对应前缀；费用 / 对方科目默认 ISRUN 的 ACCNUM01 / 02；零金额、定期盘存账套不过账 | 不等 |
| B2 | 盘点调整 → **TK**（JU 类，正负都有） | 同 B1，前缀 TK | 不等 |
| B3 | 收银日汇总 → **HS**（一天一张、现销客户），当天退货 → **SR**（CNTYP=2、RETSTK=Y） | 照 CC 现有 HS 写法；SR 照样本字段 | 不等 |
| — | 作废冲销 | — | 等第 2 条 |
| — | 转账付款（现购转账 HP） | — | 等第 4 条 |
| — | 不回仓退货、减价 | — | 等第 5 条（ERP 现在没有） |

每项都按 A 段的做法：CC 写入（只写 C 盘 T 账套副本做离线回归）→ ERP 推送 → 同步对齐把这类来源挪进 `WRITTEN_SOURCES`（验收 10-25 提醒）→
连带回归闸门 → 实验栈冒烟 → 验收复核 → 合入 main。部署随 A 段一起由主会话定。

### 九.8 第一期 A 段交付（10-25）

五个分支，按 ①→③→②→⑤→④ 一条线叠起来，均已推送、CI（Development checks）全绿，**未合入 main**，等验收复核：

| # | 分支 / 提交 | 内容 |
|---|---|---|
| ① | `pearnly/stock-a1-stloc` `9cef7f77` | 所有库存行带仓库码；CC 不论有没有仓库码都按实际写入的仓维护 STLOC，新建物料补 STLOC 行 |
| ③ | `pearnly/stock-a3-negallow` `f9522f14`、`3af811ec`、`4ad0636d` | NEGALLOW=N 的账套库存不足时整单不写，停在「需要人工」（`STOCK_INSUFFICIENT`）。只有行上带了仓库码、且这件品各仓合计 = STMAS 结存时按那个仓核；不带仓库码的行（Cowork 销项，验收实测 T9NIEX12 货在 02 仓被按主仓 01 误拦）和各仓对不上的品（老账套 STLOC 偏少）一律按全仓结存核，宁可少拦不误拦（主会话 10-25；九.7 第 1 条核实前的口径） |
| ② | `pearnly/stock-a2-sync` `691f89b8` | 同步对齐算上全部还没写进 Express 的 ERP 库存单、调整写回原库位；STLOC 合计 ≠ STMAS 的不抹 ERP 数，进公司页 Express 区差异清单（只列不拦） |
| ⑤ | `pearnly/stock-a5-opening` `3f492849` | 接了 Express 的公司期初从 Express 读：服务端拒绝手录，前端不显示入口 |
| ④ | `pearnly/cc-stale-roots` `4e405f15`、`eff17d26`、`1bd7bdc6`（CC 1.12.2）、`dfc9ea31`（离线回归脚本） | CC 定数据根前先去掉确实没了的根：配对、重连、定时重扫三条路径 |

**验收复核通过**（10-25 14:55，链尾 `dfc9ea31`，测试机 CC 测试包 run 40173611960；记录见共享笔记「出入库A段-验收.md」）：③ 新口径 T9NIEX12 实测——ERP 不对得上的 USD 从 01 卖 3 照写、卖 20 拦（`USD@all need 20 have 6`），
对得上的 EUR 从 02 卖 1 拦（`EUR@02`）、从 01 卖 1 照写；Cowork 不带码卖 3 照写、卖 10 拦；再同步调整 0 笔、停下的单按未写入计。三种对齐与 STLOC 不变式都成立；
多备份一份 STLOC 耗时 0.41 秒（RR 全程 1.5 秒），可接受。

**验证**

- 本机：链尾全闸门通过（123 项）；候选离线车道（镜像 `29404c6a`）通过；CC 全量 1096 过、42 跳过（macOS）；ERP 直连测试 65 过；期初测试 19 过；check-copy 新增 5 条无拦截。
- 101 本 C 盘 T 账套 STLOC 离线回归（`scripts/express-stloc-regression.py`，测试机，链尾 CC 代码，只写副本）：85 过 / 0 不过 / 16 跳过（15 本没有库存品、1 本没有 STLOC）。
- NEGALLOW 离线实测（测试机，T 账套副本）：T9NIEXCP / T9GIZ / T8NLT 超量卖回 `STOCK_INSUFFICIENT`、文件逐字节不变；先进 5 再卖 1 正常写入；NEGALLOW=Y 的 T9HENG15 超量照写。
- 实验栈（ERP、Cowork 都从生产重新导入，令牌与线上一致；镜像线上 `fb1037ad` + 链尾源码；测试机 CC 1.12.2 测试包，构建 run 40169743502）：

| 入口 | 结果 |
|---|---|
| `erp.page.inventory` / `stock-in` / `stock-out` / `stock-card` / `stock-count` / `purchase` / `sales` / `pos-receipts` | 通过（1.5–2.4 秒） |
| `erp.page.company` | 首次 3.4 秒超 3 秒时限（重新导入后冷启动，同批 clients、tasks 也超）；连跑 3 次 1.5–1.6 秒，通过 |
| `erp.products.find-by-code` | 通过 |
| `erp.cc.write` / `cc.desktop.write` | 通过：PUR-2026-140407 → HP691025-024、PUR-2026-140409 → HP691025-025，写进 C 盘 T9HENG15、CC 读回一致；STLOC 01 仓最后进货日更新到 10-25 |
| `erp.cc.masters` | 通过：CO-00106（T9HENG15）同步 8 秒落完、再落不改任何东西；差异清单列出 3 件（「1 -    -」各仓 25 / 结存 153，PN00001、PN00002 各仓 0 / 结存 3——旧版建物料不补 STLOC 的存量） |
| 期初入口（⑤） | 通过：CO-00106 手录期初被拒「Opening stock is read from Express」 |
| `cowork.cc.channel` / `cowork.express.push` | 通过：SMOKE-1792912382 → HP691025-003 写进 T9NIEXCP、读回一致（服务类推送，不带库存行；不带仓库码的库存路径由 101 本离线回归覆盖） |
| `cc.desktop.places`（④ 定时重扫） | 通过：装 1.12.2 后两个成员的授权数据根从 70 / 71 / 72EXP 三个自动减到 70EXP 一个（71EXP、72EXP 早已删掉，正是 10-25 测试机的现象） |
| Cowork 其余页面与 LINE 模拟 | 通过（11 项） |
| `erp.cc.connect`、`cc.desktop.bridge`、配对路径（④） | 没测到：要在测试机重新配对 / 点桌面版界面，会顶掉测试机现有成员；由 `test_stale_roots.py` 10 个用例覆盖，交验收按需真机点 |
| `cc.desktop.requests` | ERP 同步用到的按需读取通过；Cowork 月度账本回读没测到（本批未改） |
| `erp.company-menu`、`erp.stock-in.item-scan`、`erp.stock-out.item-scan`、`erp.entry.choices`、`erp.browser-text` | 没测到：清单因同目录文件被列入，本批没改这些代码路径；`check-copy` 已过 |

- 实验栈途中发现：lab-start Cowork 后 CC 的 Cowork 心跳仍打到线上 testcowork（办公室上游 DNS 缓存旧 IP，心跳连接一直连着线上），
  实验库判这台电脑不在线，`cowork.express.push` 被跳过；清 DNS 后重启 CC 即好。昨晚那次能过是因为导入后 3 分钟内就跑了冒烟（导入带来的 last_seen 还新鲜）。
  以后实验栈冒烟前先核实验库里电脑的 last_seen，再跑写入冒烟。
- 待主会话定（不挡本段）：不带仓库码的库存销项现在照 ① 写进主仓。按全仓放行后，货在别的仓时主仓 LOCBAL 会变负（全仓不负）。
  是继续写主仓，还是自动写进有货的仓，属于产品口径；Express 自己按哪个仓拦（九.7 第 1 条）核实后一起定。
- 验收复核（10-25）提出、记给 B 段：② 的 `_pending` 把收银、出入库单、盘点、作废冲销按全部历史算成「ERP 独有」；B 段把 HS / OU / JU 等写进 Express 后，
  这些来源要按「这张单是否已写入」挪进 `WRITTEN_SOURCES`，否则会重复计算。① 之后有 STLOC 的账套每次推送（含服务类）多备份一份 STLOC，耗时验收在实验栈量。
- **测试机 CC 现在是 1.12.2 测试包，不是正式版**；之后线上冒烟和平时都按这个版本跑，CC 正式版发布等主会话定。

## 十、状态（10-25 白天）

- 10-25 下午：S 盘只读金样本做完（九.6），C 盘最小录单清单见九.7，第一期 A 段五个分支已推送、CI 全绿、实验栈冒烟通过（九.8），等验收复核后合入 main；部署由主会话定。

- 10-25 生产已部署 `08bfb419`（发布记录 `docs/work/2026-10-25-release-08bfb419.md`），含 401 自愈、ERP 写账派单修复、收发存索引；第一期其余项未开工。
- S 盘只读金样本（九.4）10-25 没做成：S 盘服务器 `192.168.0.212` 与 accserver 周日没开机（445 端口不通）；脚本已能以 skin3 身份跑，开机后照计划做。

## 十（旧）、状态（10-25 凌晨收工时）

- 本文在分支 `pearnly/erp-stock-research`（只有文档，已推送、未合入，等主会话审）。四.1 已实测属实（九.1）；第一期改动单见九.3；S 盘金样本明天白天做（九.4）。
- 前置的「共用件连带回归闸门」已合入 main（`7327ed9e`，验收复核通过）；第一期动共用写账路径时按它出影响清单、逐入口实测。
- 上线约束不变：第一期上线前，生产不给任何真实客户接 ERP 和 Express。
