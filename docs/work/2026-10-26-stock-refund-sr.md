# 收银跨天退货写成 Express 退货单 SR（B3b）

基线 main `d8d42e6f`，分支 `pearnly/stock-refund-sr`。评估（已审定）：`docs/work/2026-10-26-stock-refund-sr-评估.md`；
共用写账规则与 CC 桌面版那一层的记录：`docs/work/2026-10-26-stock-refund-sr-cc.md`。

评估第 12 节三问按建议做：退货日汇总用同一个 DocType（`ERP POS Day Summary` 加单别 `doc_kind`、原 HS `against`）；
B3a 里晚到的同日退货（原小票已进 HS）不再冲成负数停下，改写 SR；一天退了几张不同原 HS 的小票按原 HS 分张。

## ERP 侧

- `express/pos_day.py`：现销日汇总只并「同一天卖、同一天退、原小票还没汇总」的退货（先选再改，不在一条 UPDATE 里自连接判断）。
  原小票已进某张 HS 的退货（跨天、晚到）每晚按 (退货那天, 原 HS) 合成退货日汇总（`refund_groups` / `summarize_refunds`），排进写账队列。
  退货单内容照正数记（退回的数量、税前额、退给客户的钱），带 `refund: {against: 原 HS 的 Express 单号, against_total: 原 HS 含税合计}`；
  原 HS 还没写进 Express 时停在「等待原日汇总写入」（`express.pos_refund_waiting`，列进 `RETRY_REASONS`，每分钟再看），写进去了自动接着写。
- `express/contracts.py::pages`：refund 块照 pos 块的路写进主页面（`pos_contract.refund_audit`）。
- `express/prepare.py`：`choice_rules.resolve(..., refund=...)`，退货科目问一次、按公司记住；`express/api.py` 退货科目从这份账套的科目表选。
- 库存对齐：退货小票进了写成的退货单就不算 ERP 独有（同一个 DocType，原查询照用）。
- 页面：选择种类「销货退回科目」、原因「等待原日汇总写入」「退货明细须为 Express 库存商品」，CC 回报的五个退货原因码照 Cowork 同一套文案。
- 新增文案（ERP）：`express.kind.refundAccount`、`express.reason.refundWaiting`、`express.reason.refundItems`、`express.error.REFUND_*` 五条；不改已有文案。

## 验证

- `tests/test_erp_express_direct.py`：退货单照正数记、挂原 HS、原 HS 没写成先等；按原 HS 分组建退货日汇总；
  退货日汇总经共用写账规则先问退货科目、记住后出 SR 载荷（第 10 版、CNTYP 3、RETSTK Y）。CC 与共用规则的测试见 cc 那份记录。

## 待做

- 第 ③ 步（要测试机）：101 本 T 账套离线回归加 SR 写入与读回；实验栈冒烟（造昨天的小票 + 今天退货 → 写 C 盘 T 账套 → 读回）；出 CC 1.12.6 测试包。
- 等服务器测试账套实录（不挡施工，挡上线）：SR 的原单号挂 CC 自编的日汇总单号时 Express 认不认；非现金退款的凭证写法（RDOCNUM 的多位行号写法已由 S 盘副本答清，见下）。

## 验收复核修正：RDOCNUM 拼法（10-26）

验收：原来「原单号补空格到 14 位再接行号」从原单第 10 行起超出 15 位、错位。在测试机 S 盘副本（`C:\PearnlyStress\S盘副本`，验收从 S 盘只读拷出，
1039 本账套、STCRD 里约 300 万行带 RDOCNUM 的退货 / 引用行）只读扫了一遍：RDOCNUM 一律 C(15)，**原单号左对齐、原单行号右对齐到第 15 位，
中间补空格**（如 `HS0000039     1`、`IV5801890    12`、单号 10 位 + 3 位行号 `…   100`；单号 14 位时行号紧贴）。`dbf_refund.rdocnum` 照此改，
读已退数量按「单号后面只能是空格加数字」认行号。补用例：1 / 12 / 100 行的写法与已退数量累计。CC 全量测试 1175 过。

## 验收复核修正：ERP 侧（10-26，「B3b-SR-df3416f6-复核.md」，主会话口径）

- 混合免税的应税基数：退货日汇总与原 HS 用同一套可改常量和算法（只记应税部分）；`exempt_total` 对退货小票记负数，退货单里照正数扣
  （`vat_fields(..., sign=-1)`），两边基数对得上（例：卖应税 107 + 免税 50、次日全退，SR 的 AMT01 = 100，同原 HS）。
- 出口：收银日汇总 / 退货单停住（停下、失败、电脑转人工）时，Express 弹窗给「再写一次」（停下的按此刻内容重新准备）和
  「标记为已在 Express 手工处理」（新状态 `handled`，撤掉挂着的写账件，库存对齐当作已写进 Express）。原 HS 是手工处理的，退货单停在
  「原日汇总已在 Express 手工处理」，也走同一个出口。
- 待写退货：公司页 Express 区显示「待写退货」张数（原小票已进日汇总、退货自己还没进写成的退货单）；收银记录的 Express 列对还没合成退货单的
  退货小票标「待写退货」；退货单等原 HS 时显示「等待原日汇总写入」，原 HS 写成后每分钟的检查自动接着写。
- 跨年：原 HS 的日期与退货日期选到的账套不同（年度账套分开）时停在「原单与退货在不同年度账套」，Express 原生跨年退货写法列进实录点，实录前停人工。
- 合单的 UPDATE 再核 `status = 'completed'`（选出来之后被作废的小票不进，HS 同样受益）。

## 验收复核修正：已退数量精确匹配（10-26 第二轮）

- 原来读已退数量按「RDOCNUM 以原单号开头、后面是空格加数字」认，单号长短不一时会误认：14 位单号第 1 行（`HS000003912345` + `1`）
  与 13 位单号第 51 行拼出来一模一样。改为按原单真有的每一行用 `rdocnum(原单号, 行号)` 拼出原生写法全等比较，物料码也要一样才算。
- 合成表 `table_specs.py` 的 STCRD.RDOCNUM 已是 C(15)（e7cea853），与真表一致。
- 免税基数、非库存品、待写退货计数、换电脑重写幂等、跨年四条中优先级已在 60dad37f / e7cea853 修完（见上两节与 cc 记录）。

## 验收第三轮（10-26，5bbe6671 静态复核建议合入）低优先级

已修：
- 「已在 Express 手工处理」留审计：标记时必须填原因，在单据时间线记一条原生评论（带标记人、时间和原因）。不做撤销：标错了先在 Express 里核对，再照实处理（轻便工具口径）。
- 状态是 FAILED、写账件已撤销时，「再写一次」照样可用：按此刻内容重新准备，不再报 Not Permitted。按钮只在真能重写时显示。
- `cashier/vat.py` 公司 VAT 状态改变时重算的范围，跟 `express.masters.WRITTEN_STATES` 同一口径（含 handled），已手工处理的日汇总不再重算。
- 作废的退货小票不再标「待写退货」：收银记录只标已完成的退货小票，公司页计数原本就只算已完成的。

暂缓（有出口、不会写错账，记理由）：
- 原单同一物料同一仓分「箱 / 个」两行时，要求单行够退，会误判退超、转人工。SR 每条退货行只能挂原单一行（RDOCNUM），拆到两行要跟原生写法一起实录确认。现在停在人工，用「标记已手工处理」出口处理，不会多退。
- 写入中途存货档被删，回报码是普通写盘失败，不是 STOCK_ITEM_NOT_FOUND。整笔照样回滚，只是原因文字不同；极少见，等下次改回报码时一起处理。
- 原 HS 是 mismatch（已写进、读回有差）也当已写入：退货单会去核原单，对不上就回 REFUND_ORIGINAL_CHANGED、转人工，有出口，不会写错。
- `day_ref` 在票号过长时退回缩写，同 HS、同日、同金额的第 2 张会被当成重复跳过。按现行票号格式，参考号放得下完整区间，不会走到缩写。

## 上线前测试机三项（10-26，主会话批）

- 101 本 C 盘 T 账套副本离线回归（`scripts/express-refund-regression.py`，测试机，带正式 CC 的索引重建程序）：80 过 / 0 不过 / 21 跳过
  （10 本没有库存品、5 本没有客户档、5 本没库存又没有可记账的调整单别、1 本没有 HS 单别）。每本先写现销 HS 卖 2 个、次日写 SR 退 1 个：
  单头 / 明细 / RDOCNUM / 结存 / 凭证 / ISVAT / 索引都核；同一份退货再写只认回原 SR、再退超被拒，两次都不改账套一个字节。
  过程中改了共用的索引核对：键与前面标签重复的标签（GLJNL5 与 GLJNL1 都是 VOUCHER，带筛选条件，只收未过账的）不拿来核新单，
  Express 自己录的已过账凭证同样不在 GLJNL5。T9GIS 主档结存与库存守卫算的数不一致，回归先垫库存再卖。
- 实验栈（ERP 从生产重新导入，挂本分支；测试机 CC 1.12.6 测试包）：前天卖（应税 3 个 + 免税 1 个，371）→ HS691024-006 写进 C 盘
  T9HENG15；昨天退应税 1 + 免税 1（157）→ 退货科目问一次（选 4100-01）→ SR691025-001。C 盘读回：RECTYP 5、SONUM 挂原 HS、CNTYP 3、
  两行 RDOCNUM「HS691024-006  1 / 2」、RETSTK Y、凭证 265.99 / 265.99 平衡、ISVAT RECTYP 5 AMT01 100 VAT01 7、索引查得到；
  「待写退货」合成前 1、合成后 0。
  - SR 的写账任务没有「写完顺手核对」（HS 有）：SR 写入自带严格读回（不符整单回滚）；顺手核对拿同一个参考号比表头与税记录，
    而 SR 的 ISVAT 参考号按原生写法是 SR 自己的单号，接上会误报，所以不接，读回状态为空是预期。
  - 实测发现：免税行的含税额（STCRD.TRNVAL）被摊进了税（应税 104.67 / 免税 52.33，应为 107 / 50）。合计、凭证、税记录都对。
    修：ERP 日汇总明细标 `vat_exempt`（这一行没拆出税额），共用写账规则照传，CC 摊各行含税额时免税行含税额就是税前额，
    其余按占比摊给应税行。
- 修正后复测（CC 1.12.6 测试包 b，12380923，run 40249625720）：同一流程写 HS691024-007 / SR691025-002，SR 两行含税额 107 / 50、
  税前 100 / 50，其余读回同上。ERP 按改动的自动冒烟：页面全过；写账三项（设测试机 CC 上的员工为冒烟账号）进货 HP691026-001、
  出库 OU691026-006、CC 写入 HP691026-002 都过；「按码找商品」不过：实验库两家公司各自从 Express 同步来的商品同用 PN00003，
  冒烟账号 Administrator 两家都看得到，返回候选（main 上编码空间那批的既有行为，与本分支无关，另报）。
- Cowork 推送冒烟（实验栈 cowork 挂本分支，共用写账规则有改动）：SMOKE-1792976783 → T9NIEXCP HP691026-001，CC 读回 ok。

## 实录第 10 条：混合免税的原生写法（10-26，验收「服务器test账套-录单结果.md」第 10 条）

S 盘副本里 Express 原生写的单（6412NLT 的 HH2200117，ISINFO.VAT_INITEM = Y）：ISVAT AMT01 = 应税税前、VAT01 = 税额、
AMTRAT0 = 免税金额；ARTRN.AMTRAT0 = 免税金额、NETVAL = 应税税前 + 免税；STCRD 免税行 VATCOD = '0'、NETVAL = TRNVAL。
整张免税的退货 SR002100392：FLGVAT 2、AMT01 0、AMTRAT0 = 全额、明细 VATCOD 都是 '0'。

- 已改：`pos_day.ISVAT_EXEMPT_FIELD = "AMTRAT0"`（现销日汇总与退货单同一条规则），CC 照载荷 `vat_fields` 写进 ISVAT，1.12.5 / 1.12.6 都认 AMTRATn，
  不发新 CC。逐行 VAT 没开的账套照同一写法：免税销售额在销项税报表里本就单列，AMT01 只记应税部分才对得上税额。
- 主会话 10-26 追加：ARTRN 与 STCRD 也照原生写，HS 与 SR 两边，进 1.12.6 的下一个测试包（不动已验收的测试包 b）：
  - ERP：只在增值税登记户的单里标免税行（`vat_exempt`，这一行没拆出税额），非登记户不标；整张都是免税的也带金额栏（AMT01 0、AMTRAT0 全额）。
  - CC：免税行 STCRD.VATCOD '0'、NETVAL = TRNVAL（`dbf_fields.exempt_line`，库存行、非库存行、退货行三处）；ARTRN.AMTRAT0 记免税金额；
    带了金额栏的整张免税单照样写销项税记录（税额 0）。老账套没有这些栏的不写。合成表 STCRD 补 VATCOD、ARTRN 补 AMTRAT0（真表都有）。
  - 账套没开逐行 VAT（ISINFO.VAT_INITEM 不是 Y，Express 自己整张计税）而单里有免税商品：按公司问一次「免税商品写法」——
    「按商品免税拆开写（建议）」/「照账套现在的设置整张计税」，记在这家公司的 Express 选择里复用。CC 读主档时回报 `facts.vat_initem`
    （1.12.6 测试包 c 起）；旧版没有这一项的当开了逐行 VAT（拆开写）。选「整张计税」：不带金额栏、明细不标免税，应税基数照整张税前
    （税额仍是收银小票逐行拆出的实数）。
  - 新增文案 3 条（`express.kind.exemptMode`、`express.option.exemptSplit`、`express.option.exemptWhole`，选项原文由主会话给定）；
    文案闸门拦截 0、标黄 1（R-PAREN：「（建议）」照主会话原文保留）。
- VAT_INITEM 分布（测试机只读统计）：S 盘副本 1047 本——空 929、Y 115、N 3；C 盘 70EXP 103 本——空 91、Y 12（T7ASC、T7ASIM、T7NLT67C、
  T7SANGJT、T8ASC 等）。大多数账套没开逐行 VAT，第一次遇到免税商品会问一次。

## B3b 实录点 ①（10-26，验收「服务器test账套-录单结果.md」）与两处修正

- SR 挂 CC 自编的日汇总单号：Express 认（服务器 test 账套录 SR0000002 挂 HS690705-001，自动带出原单金额 214、列出原单明细；SONUM / CNTYP 3 /
  IVCAMT 照原生）。
- 修 1（中高）：CC 写的 STCRD.SEQNUM 原来左对齐「1␣␣」，Express 原生右对齐「␣␣1」；Express 拼 RDOCNUM 是「原单号 C(12) + 原单 SEQNUM 原样」，
  所以对着存量 CC 单录的退货是「HS690705-0011␣␣」，原来的已退数量认不出、可能退超。dbf 库写字符栏会去掉两头空格，右对齐写不进去：
  新增 `dbf_schema.append_stcrd`，追加后按字节补写 SEQNUM，所有写 STCRD 的地方（销货库存行 / 非库存行、进货、期初、退货、库存单）都走它。
  已退数量两种写法都认；盘点 TK 挂批次的 RDOCNUM 照那一行存的行号原样拼。单头 NXTSEQ、凭证 SEQIT 也是同样的左右之差，Express 照常认、
  不影响挂单，所有单据一起改另立一项。
- 修 2（中）：CC 写的 HS 明细单位 TQUCOD 空着（ERP 收银小票、非库存服务没单位），Express 录退货时要人手补选、倍数默认 0、不改成 1 报 13。
  `account_rules.localize_stcrd`：行上单位解析不出时用这件品主档的库存单位、倍数 1。

## 跨年退货照原生写（10-26，主会话定；B3b 实录点 ②）

S 盘 1039 本副本：77,824 张 SR 里 4,079 张挂本账套里没有的原单（319 本），最常见的写法是明细 RDOCNUM「AVGPR」、货回仓、按平均成本回冲
（1,019 行全部有成本），MLOTNUM 挂本账套已有的一批（没有时「B/F」）。改为不停人工：ERP 判出原 HS 与退货在不同年度账套时，退货块带
`cross_year`（共用写账规则照传），CC 写进退货那天的账套：SONUM / IVCAMT 用 ERP 记的原单号与原单合计、对方用载荷的现金客户，明细挂「AVGPR」、
按这件品此刻的平均成本（没货按标准成本，再没有记 0）、MLOTNUM 取主档上的最新一批（STMAS.MLOTNUM）；不核原单、不核已退数量。没标跨年的照旧
找不到原单就拒。凭证 SEQIT、现金退款栏 CSHRCV 0、现金客户余额变负都与原生一致（验收核过）；非现金退款原生只有 CNTYP 3 贷现金、CNTYP 1 / 2
贷应收，CC 现在的「SR 贷现金 + 借现金 / 贷收款科目的转账凭证」不变。

## 下一个 CC 测试包（1.12.6 测试包 c，pearnly/isvat-exempt 51a33801）上线前测试机核对（10-26 13:00–13:16）

- 内容：第 10 条（免税 AMTRAT0、VATCOD、按公司问一次）、第 9 条（盘点 TK 原生写法）、明细行号右对齐、补单位、跨年 SR、root_gone 网络用例。
- 101 本 C 盘 T 账套副本离线回归（带正式 CC 的索引重建程序，只写副本、用完删）：Cowork 推送形状 RR / IV 85 过 / 0 不过 / 16 跳过
  （62 套 T8 / T9 里 54 套过、其余跳过）；库存单 79 / 1 / 21（T9GIS 盘亏被库存守卫正确拦下，同以前）；退货单 80 / 0 / 21。三组都核明细
  行号右对齐、单位不空、倍数 > 0（`scripts/express_native_detail_check.py`）。T 账套里现有的明细全是 CC 以前写的（账套清过单据），不能当原生
  参考；原生右对齐以 S 盘副本与服务器 test 账套实录为准。
- 实验栈（ERP / Cowork 都从生产重新导入，挂 51a33801；测试机 CC 测试包 c）：T9HENG15 写 HS691024-009（应税 3 + 免税 1）、SR691025-003（次日退
  两行）、TK691026-003（盘盈 2）、HP691026-003 / -004、OU691026-007，C 盘读回：五张新单明细 SEQNUM 全是「␣␣1」、TQUCOD「ชน」、TFACTOR 1；HS 免税行
  VATCOD '0'、ARTRN / ISVAT AMTRAT0 50、AMT01 300；SR 挂「HS691024-009␣␣1 / 2」、含税 107 / 50、免税行 VATCOD '0'；TK POSOPR ':'、FLAG 5、单头货值 0、
  盘盈挂 HP691026-002 那一批（旧版 CC 写的左对齐行号，照原样拼「HP691026-0021」）、批成本 10；各凭证借贷平衡。Cowork 推送冒烟 SMOKE-1792995300 →
  T9NIEXCP HP691026-002（服务费、只写单头），CC 读回 ok。ERP 按改动的自动冒烟 11 过；「按码找商品」不过是实验库两家公司同用 PN00003、冒烟账号
  两家都看得到（main 上编码空间那批的既有行为，另报）。
- 没覆盖：跨年 SR 在实验栈实写（要两本年度账套与跨年的原 HS，用 CC 用例与 101 本离线写法覆盖）；整张免税单的实验栈实写（用例覆盖）。

## 行号类字段一律照原生右对齐（10-26，主会话：同 STCRD.SEQNUM 一批修）

S 盘副本 121 本只读扫（直接读 DBF 字节，每本每表最多 300 行）各表名字带 SEQ 的字符栏：
| 字段 | 宽度 | 右对齐 | 左对齐 | 空 / 满宽 |
|---|---|---|---|---|
| STCRD.SEQNUM | 3 | 28,565 | 3 | 满宽 97 |
| GLJNLIT.SEQIT | 2 | 29,929 | 0 | 空 787 |
| ARTRN.NXTSEQ | 3 | 23,577 | 0 | 空 2,975 |
| APTRN.NXTSEQ | 3 | 23,482 | 0 | 空 3,283 |
| STTRN.NXTSEQ | 3 | 13,907 | 243 | 空 666 |
| OESOIT / POPRIT.SEQNUM、OESO / POPR.NXTSEQ、STBOM.SEQNUM、STTAK.SEQNUM | 3 | 全部 | 0 | — |
| ARTRNRM.SEQNUM | 8 | — | 6,854（「@1」等整单说明） | 其它 3,299（行号右对齐 3 位再补满 8 位） |

ISVAT、GLJNL 没有这类字段。改法：`dbf_schema.append_native`（原 `append_stcrd` 推广）追加后把 SEQNUM / SEQIT / NXTSEQ 按字段宽度右对齐
按字节补写；写 STCRD、GLJNLIT、ARTRN、APTRN、STTRN 的所有追加都走它（销货 / 现销、进货、退货、库存单、日汇总的转账凭证）。存量单不改；
读回与匹配一律去空格比（两种对齐都认）。ARTRNRM 这批不写，写时照上表另定（已记入车辆评估第二部分）。

## 验收复核 045bb818 的修正（10-26，「isvat-exempt-045bb818-复核.md」，主会话口径），进测试包 d

- 整单折扣：免税行与应税行分两组各自摊到税前合计——免税组合计 = 收银小票按行摊折扣后的免税净额（`cashier.vat.exempt_total`，同
  ISVAT.AMTRAT0），应税组摊其余（= AMT01），VAT01 是收银小票逐行拆出的税额之和。例：应税 107 + 免税 50、整单折 15.70 → 免税行 45.00 =
  AMTRAT0、应税行 90.00 = AMT01、税 6.30（原来统一按比例摊成 44.79 / 90.21）。HS 与 SR 同一套。
- 「照账套整张计税」（主会话选 A）：同 Express 没开逐行 VAT 的账套（原生 HS0000041），整张按含税价倒算——税额 = 合计 × 7/107
  （157 → 10.27），税前 = 合计 − 税额（146.73），销项税记录、凭证跟着这个，合计不变、付款不变；与收银小票逐行拆出的税额不同，日汇总
  勾上「按账套设置整张计税」（`ERP POS Day Summary.vat_whole_document`，Express 弹窗也显示）。
- 跨年退货、新年度账套没有现金客户档：照 Express 年度结转，从原年度账套（ERP 在退货块带 `against_book` = 原 HS 所在账套位置）只读复制
  这个客户档到新账套（余额从 0 起），再写 SR；原年度账套读不到或里面也没有这个客户档 → `REFUND_CUSTOMER_NOT_COPIED`，停下转人工（单据上
  「再写一次 / 标记为已在 Express 手工处理」两个出口）。
- 没有分仓表（STLOC）的账套：盘点只更新商品主档，明细 PHYBAL 记主档写后的结存，不新建分仓表。
- 盘盈挂批次只认盘点日（含当天）以前的进货。
- 载荷升第 11 版：明细带免税行标记或跨年退货单盖 11，旧版 CC（1.12.4 / 1.12.5 / 1.12.6 测试包 b、c 以前的）诚实拒收、只派给报过支持 11 的电脑。
- 老测试包写的盘点单（POSOPR 8）作废会报「Express 里改过」：只影响测试数据，按主会话口径不修。

## CC 测试包 d（pearnly/isvat-exempt 836cceb5）上线前核对（10-26 14:00–14:36）

- 相对测试包 c 的差异：行号类字段全右对齐（GLJNLIT.SEQIT、ARTRN / APTRN / STTRN.NXTSEQ 加上）；复核修正（整单折扣分组摊、整张计税按含税价倒算
  并在日汇总标明、跨年退货从原年度复制现金客户档否则停人工、无 STLOC 账套的盘点只动主档、盘盈只挂盘点日以前的进货、载荷第 11 版）；冒烟脚本
  Chrome 收尾（scripts/smoke/chrome_reaper.py、drive.mjs、selftest.py、cc-visual/shoot.mjs，用例 tests/test_smoke_chrome_cleanup.py）。
- CC 全量 1220 项；闸门 130 项；候选离线检查通过。
- 101 本离线回归（加核单头 NXTSEQ、凭证 SEQIT）：RR / IV 85 / 0 / 16；库存单 79 / 1 / 21（T9GIS 同前）；退货单 80 / 0 / 21。
- 实验栈：T9HENG15 HS691024-011（这本没开逐行 VAT，先按公司问一次「免税商品写法」，答拆开写，载荷第 11 版）、SR691025-004、TK691026-004、
  HP691026-005、OU691026-008，C 盘读回明细 SEQNUM「␣␣1」、单头 NXTSEQ「␣␣2 / ␣␣1」、凭证 SEQIT「␣0」…「␣9」全右对齐；单位「ชน」倍数 1；HS / SR
  AMTRAT0 50、免税行 VATCOD '0'；SR 挂「HS691024-011␣␣1 / 2」；TK 挂 HP691026-004（进货日不晚于盘点日）批成本 10；凭证平衡。Cowork 推送冒烟
  SMOKE-1793000159 → T9NIEXCP HP691026-003，CC 读回 ok。ERP 自动冒烟 11 过（「按码找商品」照旧是实验库 PN00003 两家同码的既有问题）。
- 没在实验栈实写：跨年 SR、整张计税、整张免税（用例覆盖）。

## 验收复核测试包 d（836cceb5）后的修正（10-26）

结论：CC 写 Express 部分通过、没有新缺陷。修：
- 冒烟清理（`scripts/smoke/chrome_reaper.py`）只认孤儿 Chrome 进程树的根：GPU 等子进程的父进程带同一个 profile，是别人正在跑的冒烟，不杀
  （验收本机实测原来会误杀）；`ps -axww` 读全命令行；自己的工作目录里记进程号（`.owner-pid`），跑超过 1 小时也不会被别的窗口启动时清掉。
  用例加两条（正在跑的冒烟树不动、记着进程号的目录不删）。
- 整张计税下全是免税商品的一天不倒算出税，照免税写、税额 0（同原生对全免税的单不计税）。口径已请主会话定，先按这个写。
- 跨年复制现金客户档：原年度账套必须与当前账套是同一家公司（ISINFO 公司名，`consistency.account_company_matches`），不信任载荷给的位置；
  复制时余额清零、最近开票 / 收款日清空、建档 / 修改日记这一天、操作员记 CC 桌面版。
- 更正：提交 5d03cd71 的说明里写了「old TK documents can still be voided」，那段兼容在 82b35b29 已撤掉——老测试包写的盘点单（POSOPR 8）
  作废仍会报「Express 里改过」，按主会话口径不修（只影响测试数据）。

## 测试包 e（主会话 10-26 四条）

- 冒烟清理改成只认运行标记：`drive.mjs` 在自己的 profile、冒烟与相机自检在自己的工作目录里写 `.owner-pid`；清理时只结掉「标记的那次运行已经结束」
  的 Chrome（主进程、GPU 等子进程一样），只删「标记过、那次运行已经结束、也没有进程在用」的目录；没有标记的、那次运行还在跑的一律不碰。
  不再按进程名或父进程是否还在判断孤儿（上一版只认进程树根的写法作废）。用例 4 条（超时不留 Chrome、驱动被打断收干净、只收已结束的标记运行、
  驱动标记自己的 profile）。
- 整张计税（口径主会话定）：全免税的一天不计销项税、照免税写；有应税行才整张按含税价倒算；全应税照小票税额、不问写法。用例覆盖三种。
- 跨年复制现金客户档：除同一家公司外，原账套的会计期间起始必须早于当前账套（`fiscal_period`），否则停人工；日期类字段照新建客户档：
  建档 / 修改日记写入日，其余日期（最近开票、收款等）空着，余额 0、状态 A、操作员记 CC 桌面版。
  测试机 C 盘真账只读核对（`cross_year_copy_check.py`，41 家公司、T7 / T8 / T9 年度账套）：同公司早年度 → 晚年度复制 77 对全对；
  晚 → 早、同年度（如 T7GISP / T8GISS 都从 2024 起）、别家公司拒绝 124 次全对。
- 价含税单的明细单价（主会话转试点窗口发现，10-26 只读核对）：S 盘副本 286 本，现销 HS（FLGVAT 1）32 万行 98% 明细 UNITPR / TRNVAL
  记含税、NETVAL 记税前（TRNVAL ≈ NETVAL × 1.07）；赊销 IV（FLGVAT 2）TRNVAL = NETVAL；进货侧同样（现购 HP FLGVAT 1 含税、赊购 RR 税前）。
  原生 SR 实录（SR0000001：UNITPR / TRNVAL 100、NETVAL 93.46）也是这样。CC 以前写 HS / HP 单头 FLGVAT 1、明细却记税前（实验栈
  HS691022-002 读回 UNITPR = TRNVAL = NETVAL = 97.82），与原生不一致。修：
  - CC：价含税的单（FLGVAT 1）把含税合计按各行税前占比摊成各行含税额（免税行含税额 = 税前额，同退货单原来的算法，挪到
    `dbf_fields.line_grosses` 共用），明细 UNITPR / TRNVAL 记含税、NETVAL / XSALVAL 照税前、成本 XUNITPR / XTRNVAL 照税前
    （`dbf_fields.inclusive_items` / `priced_line`，现销 `dbf_sales`、现购 `dbf_writer → purchase_lines`、库存行 `stock_sale` /
    `stock_purchase`、非库存行 `dbf_detail` 同一处）；赊单、无税单不变。
  - ERP 整张计税：各行税前按收银小票含税额摊（含税额 × 100/107，同 Express 没开逐行 VAT 的账套），CC 摊回的含税额就是小票含税额
    （以前按小票税前摊，免税商品的行被多分了整张税前）。
  - 真账回归共用检查 `express_native_detail_check` 加核：价含税单应税行 TRNVAL ≈ NETVAL × 1.07、免税行与其余单 TRNVAL = NETVAL；
    `express-stloc-regression.py --cash` 写现购 HP / 现销 HS 跑同一套核对。

## CC 测试包 e2（pearnly/isvat-exempt d1d49292）上线前核对（10-26 15:00–16:42）

- 相对测试包 d（836cceb5）的差异：冒烟清理只认运行标记；整张计税全免税的一天不计税、全应税不问、各行税前按小票含税额摊；跨年复制客户档
  核同公司与更早年度、日期照新建；价含税的现销 HS / 现购 HP 明细 UNITPR / TRNVAL 记含税（见上一节）。中间出过测试包 e（015ae359，
  只差价含税明细一项），已被 e2 取代。
- CC 全量 1221 项；ERP 用例 106 项；闸门 130 项；候选离线检查通过；分支 Development checks 通过（d1d49292）。
- 101 本离线回归（加核明细金额口径）：RR / IV 85 / 0 / 16；`--cash` 现购 HP / 现销 HS 85 / 0 / 16；库存单 79 / 1 / 21（T9GIS 守卫拦负库存，
  同前）；退货单 80 / 0 / 21。跨年复制客户档真账只读核对：复制 77 对、拒绝 124 次，全对。
- 实验栈 erp（REFRESH）挂 d1d49292、CC 测试包 e2：T9HENG15 写
  - HS691022-004（整张计税，应税 107 + 免税商品 50）：先问「免税商品写法」答整张计税；税 10.27、税前 146.73、日汇总标整张计税；明细
    UNITPR / TRNVAL 107 / 50、NETVAL 100 / 46.73；ISVAT AMT01 146.73、VAT01 10.27；
  - HS691023-002（只卖免税品 50）：不再问、税 0、FLGVAT 0、不标；
  - HS691024-013（3 × 107 + 50，整张计税）明细 UNITPR 107 / TRNVAL 321 / NETVAL 300、50 / 50 / 46.73；SR691025-006 挂它，明细含税 107 / 50；
  - TK691026-006（盘盈挂 HP691026-008 批成本 10）；ERP 自动冒烟 HP691026-009 明细 UNITPR / TRNVAL 10.70、NETVAL 10、成本 10；
    OU691026-010 再在 ERP 作废，Express 原生作废完成；
  - 行号 / NXTSEQ / SEQIT 全右对齐；凭证全部借贷平衡。ERP 自动冒烟 11 过（「按码找商品」照旧是实验库 PN00003 两家同码的既有问题）。
- 实验栈 cowork 推送冒烟 SMOKE-1793007644 → T9NIEXCP HP691026-004，CC 读回 ok。
- 共用件连带回归（`check-impact`，命中 cc-dbf-write 7 个入口）：cc.desktop.write 通过（ERP / Cowork 各推一张）；cowork.cc.channel 通过（推送冒烟，
  心跳 101 / 133）；erp.cc.write 通过；erp.cc.stock-entry 通过；erp.cc.stock-void 通过（OU691026-010 作废）；erp.cc.pos-day 通过（上面三张 HS）；
  erp.cc.pos-refund 通过（SR691025-006）。没测到：旧版 CC 拒收新载荷（本批没改载荷版本）。
- 收尾：lab-stop erp、cowork，定时任务恢复；DNS 已清，三域名 → 107.155.65.74；容器 0；CC 以 skin3 重启，心跳 101 / 133。

## 验收复核测试包 e2 后的修正（10-26）

结论：1 处中等问题，其余通过。修：
- 混合税率的现购 HP（ERP 采购单逐行定税率、有的行 0）：免税行原来会被摊上税额（应税 100 + 免税 50、含税 157 写成 104.67 / 52.33）。
  ERP 采购推送（`express/push._lines`）在整张有税时给 0 税率的行带 `vat_exempt`（同收银日汇总、退货单），CC 桌面版摊含税额时不摊税给它、
  明细记 VATCOD '0'、NETVAL = TRNVAL，得 107 / 50；带免税标记的载荷在共用收口（`common.finalize_payload`）盖第 11 版，旧版 CC 诚实拒收。
  整张无税的不标；ERP 销售收据是非税务单据，不受影响。CC 代码没变（e2 已认 `vat_exempt`），不用重出测试包。
  用例：ERP `MixedRatePurchase`（应税 + 0 税率才标、全 0 / 全应税不标、载荷盖 11）、CC 混合税率现购 HP（107 / 50、VATCOD '0'）。
- 限制（照旧，不在本批）：混合税率的采购单进项税记录 ISVAT 仍按整张税前记 AMT01（没有分出 AMTRAT0）；Cowork 识别的票明细没有逐行税率，
  价含税的现购明细按税前占比摊含税额。
- 未核原生样本：账套没开逐行 VAT 且选了「整张计税」时，全免税的一天照无税写（FLGVAT 0、不写 VATCOD '0' 与单头 AMTRAT0），
  原生对这种单的写法没有样本。
- 冒烟清理改成只认运行标记后，改版前留下的、没有标记的 smoke-* / scan-selftest-* 目录不会再自动清：本机临时目录现有 13 个（都是 10-26 的，
  其中一个是别的窗口 16:41 跑相机自检留下的），别的窗口可能还在用旧脚本跑，没有一并删；各窗口合入本批后各自手工清一次。
- 实写读回：测试机现由验收窗口录服务器版 Express 原生样本，暂不能起实验栈；ERP 混合税率现购的实验栈实写待测试机空出后补（CC 写法有用例与
  e2 实测的免税行写法覆盖）。

## 测试包 e3（主会话 10-26 追加三项，用户要求不带问题上线）

### ① 混合税率现购的进项税记录分出免税额

- 原生依据（S 盘副本只读统计，2568–2569 年 ISVAT 进项 66.5 万行）：进项税记录带 AMTRAT0 的单，现购 HP 价含税 314 张、价不含税 754 张，
  赊购 RR / RN 等 1,100 多张。例 6808GIZE 的 HP67012204：APTRN AMOUNT 437、NETVAL 410.18、VATAMT 26.82、**AMTRAT0 27**；ISVAT
  **AMT01 383.18**（= 税前 410.18 − 免税 27）、VAT01 26.82、**AMTRAT0 27**。与销项（实录第 10 条）同一口径。
- 修：ERP 采购推送有免税行时带 `vat_fields`（AMT01 = 税前 − 免税行合计、AMTRAT0 = 免税行合计，`express/push.vat_fields`），
  `send` 对所有单据同样处理 `vat_fields`；CC 采购写入 APTRN.AMTRAT0、ISVAT 照 `vat_fields` 写金额栏（`dbf_writer`），
  金额栏校验挪到 `purchase_adapter._validate_vat_fields` 进项 / 销项共用；合成表 APTRN 补 AMTRAT0。
- 用例：ERP `MixedRatePurchase`（vat_fields 100 / 50、没有免税行不带）；CC 混合税率现购（ISVAT AMT01 100、VAT01 7、AMTRAT0 50，
  APTRN.AMTRAT0 50；税额栏不许改）；契约用例核 CC 侧金额栏校验在 purchase_adapter。

### ② 采购 / 销售明细的库位格只读时报错（96034aa0 引入，打印窗口发现）

- 原因（Frappe 16.33.0 `ControlAutocomplete.validate` 直接读 `this.awesomplete._list`）：只读的 Autocomplete 不建输入框、没有 awesomplete，
  已提交 / 作废单据的详情页里 `add_line` 的 `set_values` 给库位格赋值就抛 TypeError，编辑器中断，之后的 Express 按钮、收款、取消菜单都出不来。
- 结构性修两层：
  - 字段定义：能变成只读的格按能不能改选控件——采购 / 销售明细（`sales-fields.js`、`purchase-fields.js` 按 `state.editable`）、
    只读明细表（`transactions.js` 的 `lines(readonly)`，已提交的出入库单、小票详情）只读时用原生只读文字（Data）；
  - 共用层（`select.js`）给原生 Autocomplete 加同 ProductSelect 一样的只读不核：没有选项列表（只读）时值照原样，不再抛错。
- 按类全仓查 11 处 Autocomplete：仓库对话框的公司格早已按 is_new 选控件；明细物料 / 对方格（entry-choice）带 ignore_validation；
  其余是始终可编辑的对话框（期初录入、盘点录入、出入库单编辑器、Express 要人选、切换公司、Cowork 推送前选）。
- 防再犯：`tests/test-desk-readonly-controls.cjs`（进 CI）逐处扫产品 Desk 脚本里的 Autocomplete 格：要么按能不能改选控件、要么带
  ignore_validation、要么登记为始终可编辑并写明原因，另核共用层保护还在；`tests/test-native-desk.cjs` 的原生 Autocomplete 桩照 Frappe
  读 awesomplete 列表，加只读赋值不报错、可编辑仍校验的用例（去掉保护即复现线上 TypeError）。

### ③ 冒烟为什么没拦住、补什么

- 原因：自动冒烟的页面入口只打开列表页（`/desk/pearnly-sales` 等），从没打开过已提交 / 作废单据的详情页，回归出在详情页。
- 补：新探针 `scripts/smoke/probes/doc_detail.js`——在列表页找这一类最新的一张某状态的单、点开（销售 / 采购走路由，出入库单点列表行开弹窗），
  等预期的按钮 / 菜单项出现，页面异常一律不通过，没有这种状态的单记「没测到」；登记表加 8 个入口（销售 / 采购 × 草稿 / 已提交 / 作废，
  已提交入库单、作废出库单），挂到 desk-shell、page-errors、desk-select、desk-list、erp-transactions（销售 / 采购另挂 desk-viewer、
  erp-entry-choice）；`run.py --shots <目录>` 每个浏览器探针跑完存一张截图留证。

### 测试包 e3（pearnly/isvat-exempt ad21fcb0）核对（10-26 17:20–19:30）

- CC 全量 1222 项；闸门 131 项；候选离线检查通过；分支 Development checks 通过（ad21fcb0）；CC 测试包 run 40316484328，SHA256 bae8fdbd…。
- 101 本离线回归：RR / IV 85 / 0 / 16，`--cash` 现购 HP / 现销 HS 85 / 0 / 16，退货单 80 / 0 / 21。
- 实验栈（REFRESH 后 CO-00106 = T9HENG15 那家）：
  - 混合税率现购：HP691026-011（59c09ac3 + e2）明细对、进项税还没分；HP691026-013（ad21fcb0 + e3）APTRN.AMTRAT0 50，ISVAT AMT01 100 /
    VAT01 7 / AMTRAT0 50，明细 107 / 100、50 / 50（VATCOD '0'），凭证平衡；载荷第 11 版、免税行带标记。
    HP691026-012 是 e2 收的同样载荷（不认采购的 vat_fields，照整张税前记）——e2 只装过测试机，没发过。
  - 详情页冒烟（`--company CO-00106`，实验库里先造了草稿 / 作废的销售、采购单与作废出库单、已提交销售单与入库单，明细带库位 A-01）：
    8 个入口全过；同一批入口在**修前代码**（临时换回四个 JS、testenv live）上，已提交的销售 / 采购详情页不通过，页面异常正是线上那句
    `TypeError: Cannot read properties of undefined (reading '_list')`——冒烟现在拦得住。截图 8 张（修后）+ 8 张（修前）留在开发窗口。
  - ERP 自动冒烟（`--diff`，46 个入口）：通过 34、不通过 2、没测到 10（manual）。不通过的两项与本批无关：收银台页在 CO-00106 不通过是
    这家公司在 REFRESH 后的实验库里没开收银（弹「这家公司还没开收银」），默认公司下同一入口通过（1.4 秒）；「按码找商品」照旧是
    实验库 PN00003 两家同码。
  - Cowork 推送冒烟：第一次 CC 刚重启、还没在 Cowork 报上线（跳过「CC not online」），等上线后重跑 SMOKE-1793017282 → T9NIEXCP
    HP691026-005，CC 读回 ok。
- 收尾：lab-stop erp、cowork，定时任务恢复；DNS 已清，三域名 → 107.155.65.74；容器 0；CC 以 skin3 重启，心跳 101 / 133；测试机 CC 现为测试包 e3。
