# 半页评估：CC 写销货单的收入科目照 Express 原生按物料科目组（11-07）

> 主会话另立、排在进货总账之后。只读统计：测试机本机 95 本账套副本（同混行取证的抽样），脚本与输出在共享笔记 `进货总账原生-11-07/sgl.*`。

## 原生（人录的赊销 IV / 现销 HS）

| 行 | 张数 | 收入记哪 |
| --- | --- | --- |
| 库存品 | IV 259,633（78 本）、HS 262,752（23 本） | **账套默认收入 `ISINFO.ACCNUM11`**：库存品的科目组几乎都没配收入科目 ACCNUM03；配了的只有 10 张（3 本），记 ACCNUM03 |
| 库存品，其他 | IV 4,598（46 本） | 别的收入科目（会计在单上改过），不跟规则 |
| 非库存品（服务 / 费用类，STKTYP 3 / 4 / 5） | IV 65,843（69 本）、HS 2,317（14 本） | **物料科目组的收入科目 `ISACC.ACCNUM03`**，同一科目合成一条，SEQIT 5；记账套默认的只有 IV 250 / HS 287 |

## CC 现在

- 收入照载荷的 `lines`（role `revenue`，SEQIT 5；ERP 按行科目拆，Cowork 一张单一条，通常是服务端读到的账套默认 ACCNUM11）。
- **库存品**：载荷默认 ACCNUM11，与原生一致（库存品科目组配了 ACCNUM03 的账套极少）。
- **CC 自建的服务类物料**（「编码 = 科目码」，`product_master._ensure_acccod`）：科目组的 ACCNUM01 = ACCNUM03 = 这个科目，载荷收入也是这个科目，与原生一致。
- **差别只在一种情况**：行上挂的是会计在 Express 里原有的服务品（科目组 ACCNUM03 是某个收入科目），而载荷给的收入科目不是它（Cowork 一张单只给一个默认收入科目时会这样）。原生记那件品的 ACCNUM03，CC 记载荷的科目。

## 建议

- CC 写销货时，非库存行挂着 Express 物料码、且那件品科目组的 ACCNUM03 可记账：这一行的收入改记 ACCNUM03（从载荷收入那条里扣出、同科目合并，做法同进货的 `stock_journal`）；
  其余照载荷。库存行不动（载荷默认就是原生口径；科目组配了 ACCNUM03 的极少数账套可一并照科目组记，审定时定）。
- 影响面：需要先看服务端载荷里「非库存行带 Express 码、收入科目 ≠ 那件品 ACCNUM03」的有多少（服务端推送记录只读可统计，按需再申请）。
- 测试：单元（服务品 ACCNUM03 ≠ 载荷科目 → 记 ACCNUM03；CC 自建服务品 → 不变；库存行 → 不变；借贷平），离线回归按科目净额核，TEST 一张。
- 版本：跟进货总账一起进 1.12.18（同一类改动、同一套回归），或再下一版，主会话定。

## 要主会话定

1. 是否做（差别只在「Express 原有服务品 + 载荷收入科目不是它」这一种）；2. 库存行在科目组配了 ACCNUM03 时是否也照科目组记；3. 版本。

## 主会话审定（11-07）

① 做；先只读统计服务端载荷里「非库存行挂 Express 原有服务品、载荷收入科目 ≠ 该品 ACCNUM03」有多少（生产只读，照今天的做法：一次 SSH、只进 web 容器、
先把查询发主会话），一张都没有也照做。② 库存品的科目组配了 ACCNUM03 时也照科目组记（原生如此，虽只 10 张；与进货同一原则：照原生）。
③ 跟进货总账一起进 **1.12.18**。今晚不动代码，明天新窗口接手（交接见共享笔记「交接-CC写账-11-07.md」）。

## 施工记录（11-08，分支 `pearnly/cc-sales-revenue-native`）

### 生产只读统计（主会话批准后跑一次，脚本与输出：共享笔记 `CC写账-11-08/sales_revenue_impact.py`、`sales_revenue_impact_out.txt`）

- 数的是 CC 成功写进 Express 的销货单里「挂 Express 原有物料（回执 created = False）、物料码 ≠ 收入科目」的行——改后可能换科目的候选。
- 正式客户只有 erpcowork 1 张赊销（库存行）；其余都在演示 / 测试租户：ccdemo 10 张（20 行服务品）、testcowork 30 张（26 行服务品 + 62 行库存品）、
  testerp 1 张；gize / mrerp / polymer / test01 为 0。候选物料在 accserver 上的账套里，当天 accserver 关机，没核它们的科目组收入科目。
- 脚本缺陷（如实记）：老载荷没有 role 时「载荷收入科目」那一列取成了销项税科目，这一列不能用；候选行数不受影响。没有重跑。

### 原生口径补充：科目组收入科目是哪一类（95 本，共享笔记 `sgrp.py`）

| 科目组收入科目的类别（GLACC.GROUP） | 原生非库存行正数贷它的行数 | 说明 |
| --- | --- | --- |
| 4 收入 | 89,307（64 本） | 主体 |
| 2 负债 | 5,184（6 本） | 预收定金（如 2134-01），会计有意 |
| 1 资产 | 1,528（7 本） | 押金、出售资产等 |
| 5 费用 | 51（19 本） | 几乎不用 |

离线回放小跑 5 本看到：CC 销货行按品名能配上的「服务品」不少是会计记费用 / 资产用的（科目组收入科目 51-03-04-00、5152-02、14-01-04-00），
照科目组记会把收入贷进费用科目。**主会话定（11-08）**：照科目组 ACCNUM03；**科目组收入科目是费用类（GROUP 5）时照载荷**（只偏离原生 51 行）；
资产 / 负债类照原生。

### 改了什么

- CC（`dbf_sales.revenue_journal`）：每行物料科目组的 ACCNUM03 能记账、不是费用类 → 这一行税前额从它原来那条收入里扣出、记 ACCNUM03（SEQIT 5，
  同科目一条）；没配 / 不能记账 / 费用类照载荷；行额合计超过原来那条收入（整单折扣等）→ 那一条照载荷。库存行、非库存行同一规则。
  回执 `meta.revenue` 带实际记的收入贷方（`revenue_entries`）。
- 服务端：`common.sanitize_push_meta` 收 `revenue`（≤ 40 条、科目 ≤ 15 字、金额非负，有一条认不出整项不留）；`push.express.pushed.project`
  用它换掉载荷的收入行（有 role 的是 role = revenue 的几行，旧载荷是第一条贷方），`readback/diff.py` 照常比——不再把照科目组记的收入报成
  「会计改过」；没有 `revenue` 的旧记录照载荷。ERP 那边（Pearnly CC Job）只回读主档与库存、没有按载荷科目比分录的地方，不用改。
- 顺序：**服务端先随下一批部署，部署后才发 CC 1.12.18**（旧服务端会把 `revenue` 滤掉，读回照载荷比、会误报）。
- 没做：收银退货 SR（`dbf_refund`）的收入冲回仍照载荷；另议。

### 测试

- CC：`tests/test_sales_revenue_native.py` 13 条（服务品记科目组收入、混行拆分、库存品配了就记、没配 / 不能记账照载荷、直接科目行不变、
  现销同规则、四类科目：收入 / 负债 / 资产照科目组、费用照载荷、回执带实际收入、按行科目找原来那条收入、行额超收入照载荷）；
  `tests/test_push_meta.py` 2 条。业务代码还原后 6 条失败。CC 全量 1574 通过。
- 服务端：`tests/test_cowork_readback.py` 新 3 条（白名单只留干净的 revenue、投影替换收入行、照科目组记的单读回不报改过 / 没有 meta 时会报）；
  去掉投影替换后 2 条失败。

### 离线回归（测试机 101 本 T 账套副本，11-08）

- `express-stock-balance-regression.py --scenario same --service`，比对 `--gl`；另加现销服务品一步 SVC（挑 Express 原有服务品，优先科目组收入科目
  可记账、≠ 载荷、不是费用类），期望值由脚本按 Express 表自己算（科目组 ACCNUM03 能记账且 GROUP ≠ 5，否则载荷收入），收入合计新旧一致。
  - g4（main 对 进货分支，两边都还照载荷记收入）：79 本收入 ≠ 期望——就是本改动要改的情形（含 T8POLYM 库存品配了 ACCNUM03 的 IV / HS）。
  - **g5（进货分支 对 本分支）：85 本通过、0 报差**、16 本跳过（无 ISPRD / 主仓没有品）；上面 79 本全部照科目组记对，其余表与库存一栏不差。
  结果在测试机 `C:\PearnlyStress\_stockbal1107\g5\cmp.log`、`result.jsonl`。

### TEST 实测（11-08 13:18–13:25，accserver 在线）

- 写前整本快照（`C:\PearnlyStress\_stockbal1107\t8\before`）。TEST 的账套默认收入 41-01-00-00；挑三件 Express 原有服务品：
  42-11-00-00（科目组收入科目 42-11-00-00，收入类）、14-01-08-00（14-01-08-00，资产类）、53-01-06-00（53-01-06-00，费用类）；库存品 500603（ST10，没配收入科目）。
- 原生（代录引擎后台开服务器版 Express，用户 BIT9）与 CC 本分支代码（安装版 `cdx_reindex_runner`）各录同样两张，比 GLJNLIT 收入行（SEQIT 5）：

| 单 | 原生 | CC 本分支 |
|---|---|---|
| 现销 HS（三件服务品各 100） | HS0000044：42-11-00-00 100、14-01-08-00 100、**53-01-06-00 100** | REV1108C：42-11-00-00 100、14-01-08-00 100、**41-01-00-00 100** |
| 赊销 IV（500603 ×1 @300 + 42-11-00-00 100） | IV0000042：41-01-00-00 300、42-11-00-00 100 | REV1108D：41-01-00-00 300、42-11-00-00 100 |

  收入、资产类照科目组、库存品没配照默认，与原生一致；费用类那一行按主会话定照载荷（原生记费用科目），是有意的偏离。现金 / 应收、销项税两边相同。
  CC 回执 `revenue` = 实际收入贷方（HS 三条、IV 两条），与 GLJNLIT 一致。赊销库存品 CC 另有成本分录（SEQIT 8 / 9，既有设计 V2-b），原生 IV 存档时没有，不在本次范围。
- 原生作废：IV0000042、HS0000044 在 Express 里原生作废干净（单头 C、分录金额清空）。REV1108C / REV1108D 单号是载荷单号、不带单别前缀，
  Express 的单据窗口按前缀翻页翻不到（11-07 的 BAL1107B 也一样），代录引擎没法原生作废。
- 收尾：确认快照之后 TEST 只有本窗口这几张单（另有进货 ASIAFC 那张，见进货记录 10.1）的改动，当前版本另存 `t8\after`，19 个变了的文件按快照放回，
  逐文件哈希与快照一致、无残留单据。
- 没测到：Cowork 推一张销货 → CC → 读回不报改过的端到端（要 lab-start 带本分支服务端 + 换装 CC，问主会话）；服务端与 CC 的对接由单元测试覆盖。

### 共用件影响清单（`check-impact.py`：`express-rules` 15 + `cc-dbf-write` 9，去重 22 个入口）

| 入口 | 结果 |
|---|---|
| `cc.desktop.write` | 通过：离线回归 101 本 + TEST 直写 REV1108C / D（上面） |
| `erp.cc.write` | 通过（销售走同一个 `dbf_sales.write_sale`，覆盖同上）；ERP 网页提交一张没测到（ERP 服务端未改） |
| `cowork.express.push` / `cowork.history` | 单元测试通过（白名单、投影替换、读回不报改过；旧记录照载荷）；端到端没测到（见上） |
| `cowork.cc.channel` | 单元测试通过（`test_push_meta`：成功回执带 `revenue`）；端到端没测到 |
| `erp.cc.pos-day` | 没测到：日汇总 HS 走同一个 `write_sale`，收入行照同一规则；收银小票的物料多是库存品、科目组多没配收入科目（照默认），离线回归 HS 步骤已覆盖写法 |
| `erp.cc.pos-refund` | 没测到：退货 SR 走 `dbf_refund`，本次不改（收入冲回仍照载荷，见「没做」） |
| `erp.cc.stock-entry` / `stock-void` / `stock-transfer` | 没测到：写 OU / JU / TK / RL，不经 `revenue_journal` |
| `cc.desktop.operator` / `cc.operator.page` | 没测到：代录走 Express 界面录单，收入科目由 Express 自己定，不经本改动 |
| `cc.desktop.places` / `cowork.connections` / `cowork.companies` | 没测到：不读写回执 meta 的收入 |
| `cowork.mrerp.push` | 没测到：MR.ERP 推送不经 `sanitize_push_meta` 的收入字段（新字段缺省为空，旧行为不变） |
| `cowork.cc-chat.push` / `cowork.cc.troubleshooting` / `cowork.line.item-questions` / `erp.trade.line-stock` | 没测到：只经过推送载荷生成，不读回执收入；载荷未改 |

- 受影响流程：`scripts/flows` 没有覆盖 Express 写账 / 读回的流程，无流程可跑。

### ASIAFC 停单（主会话 11-08 定，随本次合入）

- 依据：进货记录 10.1，TEST 原生实录——物料科目组的存货科目不在科目表时 Express 存档报 1155、整张拒存。
- 改法（CC）：`purchase_lines.stock_journal` 发现库存行科目组的科目（永续 ACCNUM01 / 定期 ACCNUM02）不在科目表 → 抛 `GroupAccountMissing`，
  整单回滚，回 `ITEM_GROUP_ACCOUNT_MISSING`（原文 `item group ST01 ACCNUM01 1140-02 not in GLACC`）；在科目表但不能记账仍记账套默认；
  科目表读不到或是空的不拦（`account_rules.chart_account` 回 None）。
- 回报口径：CC `worker` 与服务端 `cc_desktop/wire/ack_rules.NEEDS_PERSON_CODES` 都按「要人处理」，不烧重试；文案 Cowork
  `intake.error.ITEM_GROUP_ACCOUNT_MISSING`、ERP `express.error.ITEM_GROUP_ACCOUNT_MISSING`：「物料科目组的科目不在 Express 科目表中」
  （四种语言，`check-copy.py` 新增 2 条、无拦截）。网页按错误码出译文、不带参数，科目组与科目码在回报原文里（CC 对话的单据自检读得到）。
- 测试：`test_purchase_gl_native.py` 新增 2 条（永续 / 定期：不在科目表 → 不写、错误码对、原文带科目组与科目、账套字节不变），把原来那条
  「不在科目表退回默认」改成「在科目表但不能记账（ACCTYP 1）退回默认」；业务代码还原后新增 2 条失败。CC 全量 1576 通过。
- 销货收入不跟着改：收入科目组的科目不能记账 / 不在科目表照载荷（没有原生证据，主会话只定了进货）。
- 离线回归里的 T7 / T8 / T9ASIAFC（种结存挑中的物料在 ST01，存货科目 1140-02 不在科目表）现在会停单，是预期。

### 发版后验收必做（主会话 11-08 定）

- 服务端随下一批部署、CC 1.12.18 发出后：testcowork 推一张带服务品的销货 → CC 写 TEST → 读回不报「会计改过」（回执 `revenue` 投影替换收入行）。
  本次没做端到端，靠单元测试与 g5 回放覆盖。
