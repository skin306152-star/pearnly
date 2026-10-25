# 出入库第一期 B 段剩余部分设计（交主会话审，审定前不施工）

> 依据：服务器测试账套 `\\accserver\d$\account\70EXP\test`（DATAT）实录（共享笔记「服务器test账套-录单结果」，副本 `C:\PearnlyStress\_srvtest_record\*`），
> 开发窗口逐字段比对结果记在 `docs/work/2026-10-24-erp-stock-express-plan.md` 九.9（作废、转账付款、退货、按仓拦）。基线：B 段分支 `pearnly/stock-b3-pos` `1874e658`
> （含 B1 出入库单、B2 盘点调整、B3a 收银日汇总、缺索引程序拒写），A 段按仓判断 `pearnly/stock-guard-per-warehouse` `fc684551`。
> `CC/` = `companion/src/companion/`，`R/` = `apps/pearnly_core/pearnly_core/express_rules/`，`P/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`。

## 0. 总则

- **照 Express 原生的写法写**，连 Express 自己的怪现象（退现金也冲客户余额、作废后凭证金额改空）一起照做，不自作主张修正；Express 改单时追加的
  删除标记临时行、ADVNUM「AE」这类 Express 内部副作用不照抄（各项下面单列）。
- 只动 **ERP 自己写进去、Express 里没被会计改过** 的单；写前先核原单仍是写入时的样子，对不上一律转人工，回报写明哪张表哪个字段不同。
- 原单所在会计期间已关账：照现有 `respect_period_lock`，回 `PERIOD_LOCKED` 转人工。
- 共用写账规则（`R/`）改动同时影响 Cowork 推送：每项过 62 套离线回归 + 101 本 T 账套副本回归 + ERP / Cowork 两边冒烟；Cowork 线上行为有变化的单列出来另批。
- CC 载荷升到 **第 7 版**（B1 占用第 6 版），本文各项进 CC 1.13.0；旧版 CC 诚实拒收（`CC/payload_contract.py`）。车辆四项顺延到第 8 版。
- 顺序（每步独立交付、各自过闸门和验收）：① 作废 → ② 进货回填负库存成本（等验收补测「全范围计算成本」）→ ③ 退货 SR（B3b）→ ④ 转账付款 / 收款 → ⑤ 调拨 RL。

## 1. 作废：照 Express 原生作废原单（取代「写冲销单」）

**Express 怎么做（实录）**：在原单上作废，单头留着、DOCSTAT = C、金额清 0；ISVAT 留行清 0 标 C；GLJNL 头标 C，GLJNLIT 每行 AMOUNT 改成空（null）；
STCRD 那行打删除标记（赊销 IV 的 SEQNUM 改 `@@@`、TRNVAL 清 0；领用 OU 的 SEQNUM 不变、NETVAL 0→成本值）；STLOC / STMAS 数量金额回滚；ARMAS.BALANCE、
ARBAL 当月桶减回（AP 端对称）；赊销单头另改 CMPLAPP = Y、CMPLDAT = 作废日、NXTSEQ 清空；ISRUN 不回退，单号不复用；不要原因、不要密码。

**范围**：ERP 里已有「作废」的单据——采购（`P/purchasing/entry.py:275`）、销售单、出入库单（`P/inventory/documents.py:278 void_entry`）、收银小票作废
（`P/cashier/sales.py:462`）。只有 `express_state = written` 的才发作废任务；没写进去的照现在只在 ERP 作废。

**ERP 侧**：作废时若已写进 Express → 单据进「作废排队」（新状态 `void_queued` / `voided`，失败转人工），写账任务带 `action: "void"`、原单号、写入时 CC 回执里的
「写入快照」（各表行的关键字段与金额）。作废结果回写单据，失败原因同现在的 Express 弹窗。

**CC 侧**（新模块 `CC/dbf_void.py`，在现有备份-回滚事务里）：
1. 按单号找原单各表行，与写入快照逐字段核（金额、行数、DOCSTAT = N）。不符 → `VOID_DOCUMENT_CHANGED`（转人工，写明表与字段）；已是 C → 视为已作废、回成功。
2. 照上面实录逐表改；STLOC / STMAS 用写入时同一套 `stock_location.apply` 的反向变动；对方余额按 RECTYP 减回 ARMAS / ARBAL 或 APMAS / APBAL 当月桶。
3. 重建改过的表索引（runner），按 STCRD3 / 单头索引回查仍查得到（作废单在 Express 里照样能打开看）。
4. 回读核对后回执。

**收银日汇总里的小票**：日汇总已写成 HS 后，单张小票不能作废原 HS（会把整天作废）。建议已汇总的小票作废改为当天另记一张退货 SR（第 3 节），未汇总的照常。
界面上已汇总小票的「作废」改走退货流程——这是行为改变，列第 6 节第 1 问。

**风险**：作废动对方余额与 STLOC，写错比写单更难查；先只开 OU / JU / TK（无对方余额）→ 再开 IV / RR / HP / HS。

## 2. 进货回填之前负库存出库的零成本（第 3 条实录；方案待验收补测后定）

**Express 怎么做**：允许负库存时先卖 2（无货、成本 0），再进 5 @60——**存进货单时**就把前面那张销售的 STCRD XUNITPR = 60、XTRNVAL = 120、MLOTNUM 指向进货批次，
并把成本凭证（借成本 / 贷存货 120）补进那张销售的 GLJNLIT；之后跑「计算成本」（4→7→4）没有任何表再变。

**问题**：CC 写进货（RR / HP 带库存、JU 入库、退货回仓）现在不回填，账上留零成本出库，毛利虚高。服务器测试账套已有 9 行零成本（HS6901-101，疑为以前 CC / 演示推送）。

**两个方案**（验收补测「先给这些商品录进货、再跑全范围计算成本」之后定）：
- A（推荐，如果计算成本补不上）：CC 写入库类单据时，同事务里按 Express 的批次链找出同一商品（按 LOC_COST 决定看不看仓）在本单之前、成本为 0 的出库行，
  按本单单价回填 XUNITPR / XTRNVAL / MLOTNUM，并补那张出库单的成本凭证两行；回填到的出库单号写进回执，ERP 对应单据记「成本已回填」。
- B：如果全范围计算成本能补，CC 不回填，改为提示会计在 Express 跑一次计算成本（零开发，但依赖人）。
- 实现 A 前还要实录：两张负库存出库、一张不够补两张的进货时 Express 怎么分；跨仓时看不看仓（ISINFO.LOC_COST）。

## 3. 收银退货 SR（B3b，第 5 条实录：退现金）

**Express 怎么做**：ARTRN RECTYP 5、CNTYP 3（退现金）、SONUM = 原单号、IVCAMT = 原单金额、RCVAMT = 退款额、CMPLAPP Y；凭证 JNLTYP 03：借退货科目、借销项税、
贷现金（不挂应收），借存货 / 贷成本；STCRD POSOPR 2、FLAG 2、RETSTK Y、RDOCNUM = 原单号补空格到 14 位 + 行号、成本按原批次；STLOC 回仓；STMAS 指向退货批次；
另有一行旧 STCRD 的 MLOTNUM / MREMBAL 被 Express 维护（批次链，施工时逐行核是哪一行再照写）；ISVAT 一行（VATREC S、RECTYP 5、正数）；ISRUN SR +1；
ARMAS.BALANCE 与 ARBAL.SR 桶也冲减（照做）。

**ERP 侧**：收银退货小票（`sale_type = refund`，`return_against` 指原小票）按天合成「退货日汇总」——一家公司一天一张 SR、挂原小票所在的那张 HS
（跨天退货挂原小票那天的 HS），明细按品 × 仓汇总、全部回仓（收银退货都退货回仓）。退款方式非现金的，同日汇总一样另记转账凭证。

**CC 侧**：共用规则加 SR 写法（`R/` 新增退货映射，CNTYP 3），`CC/dbf_sales.py` 加 RECTYP 5 分支；载荷第 7 版。

**待测**：「只减价、不回仓」的行；「留着抵下次收款」（推断 CNTYP 2、挂应收）；SR 的 SONUM 指向一张 HS 日汇总时 Express 是否接受。

## 4. 转账付款 / 收款（第 4 条实录）

**Express 怎么做（付款端）**：现购 HP 默认全额现金（APTRN.CSHPAY）。改转账：从这本账套 ISRUN `DOCTYP = ZP` 的付款类型里选一种（PREFIX 是码、ACCNUM01 是科目），
新增 APRCPCQ 一行（RCPNUM = 单号、CHQNUM = PREFIX + 单号、PAYAMT），APTRN CSHPAY → 0、CHQPAY / CHQPAS → 金额，凭证贷该类型科目（不再贷现金）；BKTRN 不写。

**设计**：
- ERP 采购单付款方式是转账 / 支票时，按公司选一次 Express 付款类型（选项 = 这本账套 ISRUN 的 ZP 行；CC 主档读回加读 ZP / ZR 行），走现有「选一次、按公司记住」
  （新选择类型 `paytype`，主题如 `purchase|transfer`）。
- 写 HP / 现购时：APTRN 现金栏改支票栏、写 APRCPCQ、凭证贷类型科目；一张单分几种付款的按各自金额写几行 APRCPCQ（待测：Express 是否支持一张单多行）。
- 收款端（推断，**待测**）：ISRUN `DOCTYP = ZR` 是收款类型，ARRCPCQ 结构对称；现销 HS 收转账 = ARRCPCQ 一行 + ARTRN 现金栏改支票栏 + 凭证借类型科目。
- **对 B3a 与 Cowork 的影响**：B3a 日汇总现在照 Cowork 线上 POS 导入，非现金每种另记一张「借收款科目 / 贷现金」转账凭证（实验栈已写出 JV691025-001）。
  收款端若证实推断，原生写法是在 HS 上记收款类型。建议收款端实录后，B3a 与 Cowork POS 导入**一起**改成原生写法（共用规则一处改），Cowork 线上行为变化单独报批；
  实录前 B3a 先保持现在的写法（与 Cowork 一致，凭证借贷平衡、不错账）。
- Cowork 采购推送「已付款、转账」的单也会走新写法，列进两边冒烟。

## 5. 调拨 RL（第 6 条实录）

**Express 怎么做**：STCRD 一对行——调出仓 POSOPR 4（PEOPLE = 调入仓）、调入仓 POSOPR 3（PEOPLE = 调出仓），同 DOCNUM / SEQNUM，MLOTNUM 指向来源批次，
无金额，REIMBURSE = `\x00`；STTRN 一行（POSOPR 4、LOCCOD 调出、PEOPLE 调入）；STLOC 两仓各增减；STMAS 不变；ISRUN RL +1；不写凭证。

**ERP 侧**：现在没有调拨单（`ERP Stock Entry` 只有 in / out）。做的话要新增「调拨」（出库仓、入库仓、明细），ERP 流水记一出一入。这是新功能，按需求评估模板另写评估；
B 段只把 CC 写法备好（`CC/dbf_stock.py` 加调拨，载荷第 7 版同带），ERP 入口等主会话定。

## 6. 要主会话 / 用户定的

| # | 问题 | 我的建议 |
| --- | --- | --- |
| 1 | 已进日汇总的收银小票「作废」改成当天记退货（SR）吗？ | 是，否则只能作废整天的 HS |
| 2 | 作废先只开出入库 / 盘点（OU / JU / TK），再开进销货？ | 是，分两步上 |
| 3 | 进货回填零成本：等验收补测后在 A / B 里选 | 补测前不施工 |
| 4 | 收款端实录后，B3a 与 Cowork POS 导入一起改成原生收款类型写法？ | 是，Cowork 线上变化单独报批 |
| 5 | ERP 要不要新增调拨单？ | 另写评估，B 段只备 CC 写法 |

## 7. 待测清单（服务器测试账套，交验收）

1. 现销 HS 收款改转账 / 刷卡（ZR 类型）→ ARRCPCQ 与 ARTRN 栏位；
2. 一张现购分两种付款（现金 + 转账）→ APRCPCQ 是否两行；
3. 退货 SR「只减价、不回仓」的行；「留着抵下次收款」；SR 挂 HS 日汇总；
4. 两张负库存出库 + 一张不够补的进货 → 回填怎么分；跨仓（LOC_COST）；
5. 给零成本商品录进货后跑全范围「计算成本」，整本比对（决定第 2 节 A / B）；同时核**批次链口径**（验收 10-25 对照 OU691025-002 与原生 OU0000001）：
   原生 STCRD 出库行的 MREMBAL / MREMVAL 记来源批次出库前的剩余、收货行为 0 而批量记在 LOTBAL、出库行写 MLOTNUM 指向来源批次、
   STKDES 写单据备注；CC 一律写全品写后结存、不写 MLOTNUM、STKDES 写品名（A 段起的既有写法）。是否改成原生批次链与成本方案一起定；
6. 商品级 NEGALLOW = A 的含义；
7. 作废一张带 PromptPay 转账凭证的 HS：Express 作废时是否连带那张 JV（不连带的话，CC 作废 HS 时要不要一起作废 JV）。

## 8. 工作量（按机器耗时）

- ① 作废：CC 写法与回读约 4 小时，ERP 状态与界面约 2 小时，101 本副本回归 + 实验栈约 2 小时。
- ② 回填：方案定后约 3 小时（A）/ 0.5 小时（B，只加提示）。
- ③ 退货 SR：CC + 共用规则约 4 小时，ERP 退货日汇总约 2 小时，回归约 2 小时。
- ④ 转账付款 / 收款：付款端约 3 小时；收款端等实录，连同 B3a / Cowork 改写约 4 小时；62 套 + 两边冒烟约 2 小时。
- ⑤ 调拨：CC 写法约 2 小时；ERP 调拨单另评估。
