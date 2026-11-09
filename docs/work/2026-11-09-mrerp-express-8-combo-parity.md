# MR.ERP 与 Express：CC 8 组合能力对照（11-09，D 线 MR.ERP 窗口，只读）

- 主会话 local_e395dace 11-09 派出（总表「CC直写代录-总表-11-09」D 线第 ② 步）。用户要求：「MR.ERP 在 CC 里要和 Express 一样，Cowork 和 ERP 的直写、代录四种组合都要做到 Express 现在的程度」。
- 只读，没有改代码。代码基线：`origin/main` 4e9dec232 + MR.ERP 代录执行层分支（a954866d8 + 测试修正 c47982448，开关关，本表成稿时正在排号合入）。
- 依据：读代码；ADR-026 / ADR-027；`2026-11-02-cc-mrerp-operator.md`、`2026-11-02-mrerp-core-rules.md`、`2026-11-02-mrerp-operator-and-erp-company-评估.md`、
  `2026-11-03-cowork-mrerp-operator-wiring.md`、`2026-11-03-erp-mrerp-direct.md`、`2026-11-08-erp-cowork-cc-parity-盘点.md`；11-08 盘点笔记；11-09 真站点只读探路（3 次）。
- 四个只读子代理分别收各组合的证据，本窗口汇总、去重、统一口径。查不到写「没查到」。

## 0. 一屏结论

- **31 项能力 × 4 组合，MR.ERP 一侧共 124 格：有 20、有（开关关）23、部分 18、缺 42、MR.ERP 本身没有 4、不适用 17**（统计见 §3）。
  缺的 42 格里 24 格在「ERP 代录」一列（整条没接），4 格是复核卡（Express 也缺，A 线做）。按组合：Cowork 直写缺 6、Cowork 代录缺 7、ERP 直写缺 5、ERP 代录缺 24。
  「有（开关关）」= 代码在、开关没开、配方或导入模块没在真站点核过，**不算能用**。
- **最大的缺口是「ERP 代录 → MR.ERP」整条没有**：ERP 声明的 `target()` 写死 `express`，共用机器通道建任务不带账本、还要求 Express 账套可达，开关开了也派不出去。
  其余三组合的 MR.ERP 代码大多已在，差的是「接上卡片 / 词条 / 设置入口」和「真站点核对」。
- **真站点是总闸**：四个录单菜单的列表页被「กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"」挡住，回读必然判成「结果不明」；TEST2020 库不在。岳父改好前，任何 MR.ERP 写入都不该试。用户 11-09 定 TEST2020 不测，探路和实测只用 TEST2019。

| 类 | 内容 | 谁做 | 写码 + 机器（小时） |
|---|---|---|---|
| **现在就能做（不依赖真站点）P0**：开开关前必须有 | ① ERP 代录接 MR.ERP（target 读公司账本、submit 出 MR.ERP 载荷、共用通道任务带账本 + 不看 Express 账套可达 + 账号租约下沉共用层、预检、status）② ERP 代录资料区按账本分流（潜在缺陷，见 §2）③ 开始前卡片接 MR.ERP（Cowork 直写 / 代录、ERP 代录；ERP 直写已有）+ 代录开始 / 续录卡的登录行带 target ④ 原因码：代录发专用码、补约 30 个 MR.ERP 码四语词条、ERP 复用 Cowork 词条 ⑤ ERP 的 CC 设置 MR.ERP 一行接口补齐（现在开关一开就坏）+ ERP 公司页 MR.ERP 段 ⑥ ERP 单据页弹窗认 MR.ERP「选一次 / 再写一次」、取消 MR.ERP 时撤掉未导入的单 ⑦ 结果不明人报结果：按 A 批 3 统一后的 resolve 接口接 MR.ERP | D（①的共用通道部分先与 A 对文件） | 写码 38–48 + 机器 10–12 |
| **现在就能做 P1**（开开关后可跟进） | 认领 MR.ERP 已有客户 / 供应商 / 物料档（主档读取器已有）、ERP 直写按单据日期挑年度账套、Cowork 现销 / 赊销按客户记住、仓库按行选、代录认不出画面交 AI 接手、代录疑似重复提示、定时任务自动推档位、词条与工具说明不写死 Express（改已有文案先给用户看） | D | 写码 28–40 + 机器 8–10 |
| **A 线共用层做，D 按接口接** | 复核卡（8 格全缺）、乱用兜底、已写入判定与跨模式同票跳过、结果不明统一（A 批 3）、`check-cc-tools.py` 两个盲区（只一边有的工具不报、代录接法只比键名） | A 做；D 每项接 MR.ERP 1–3 | D 接入写码 6–10 + 机器 3 |
| **等岳父改好菜单** | 探路 15 项、配方逐项核成 `verified:true`、列表回读与 Save Success、主档导入模块真站点跑、税率四档文字、现销 / 转账收款格、按行仓库、免税 / 零税单、被踢表现、按税号找档的列表接口（只用 TEST2019；只有一个测试年度，跨年度切换没实测）、少量写入实测（先审）；Cowork「回读账本 / VAT 草稿」要读 MR.ERP 报表页 | D | 探路 1 + 机器 3；每项核完修 1–3，合计写码 12–20 + 机器 6–8；报表回读另 8–12 |
| **MR.ERP 本身不支持，换做法** | 见下表 | D | 见下表 |

| MR.ERP 本身没有 | 建议做法 | 写码 + 机器 |
|---|---|---|
| **现购**（导入只有赊购；ERP 采购多是已付 + 到货） | 直写遇现购**自动转 MR.ERP 代录**（同一批，不停下等人）：代录走 aptran 网页新增页，保留「เงินสด」勾选（`chkcash`），配方加 `purchase_cash`；服务端代录分支放行 | 5–6 + 2，真站点核付款格 |
| 采购**付款方式** | 随现购走代录（同上） | 含在上项 |
| **POS 日汇总** | 按天拆成一张现销，非现金方式写进 3 个「รับอื่นๆ」槽（共用层 `sales_cash.py` 已支持这三个槽） | 4 + 1 |
| **逐行免税** | MR.ERP 只有整单税率：免税行拆成一张 0% 单（要会计口径，**主会话定**） | 2–3 + 1 |
| 收付款单、退货、贷项、日记账 | 导入没有 → 走代录（评估第 2、3 期）；退货 / 贷项先在真站点看有没有菜单 | 随代录分期 |
| 库存进出导入 | **不是不支持**：旧线上有，迁移时删了，可恢复 | 6–8 + 2，真站点核 |
| 仓位（二级） | Express 也没有，不做 | — |
| 预扣税 | Express 推送也没有，不算 MR.ERP 缺口 | — |

合计（D 线，不含 A 线共用层本身）：**P0 写码 38–48 + 机器 10–12**；P1 写码 28–40 + 机器 8–10；接 A 的共用件 6–10 + 3；真站点核对 12–20 + 6–8；换做法 17–21 + 6。

## 1. 对照表

图例：**有** 能用；**有·关** 代码在、开关关（未在真站点核）；**部分** 有但不全（见备注）；**缺** 该做未做；**本身无** MR.ERP 自己没有这功能；**不适用** 该产品 / 模式本来就不做；**没查到** 证据不足、不下结论。
列：CW = Cowork，ERP；直 = 直写，代 = 代录；E = Express，M = MR.ERP。「归属」只写 MR.ERP 列「缺 / 部分」由谁补：**A** 共用层（CC 线 local_fd1ef94e），**D** MR.ERP 专属（本线）。

| # | 能力 | CW直·E | CW直·M | CW代·E | CW代·M | ERP直·E | ERP直·M | ERP代·E | ERP代·M | 归属与做法 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 赊购 | 有 | 有 | 有 | 有·关 | 有 | 有·关 | 有 | 缺 | D：ERP 代录接 MR.ERP（P0-①） |
| 2 | 现购 | 有 | 本身无 | 有 | 缺 | 有 | 本身无 | 有 | 缺 | D：直写自动转代录，代录加 `purchase_cash` 配方 |
| 3 | 赊销 | 有 | 有 | 有 | 有·关 | 有 | 有·关 | 有 | 缺 | D：P0-① |
| 4 | 现销 | 有 | 有 | 有 | 有·关 | 有 | 有·关 | 有 | 缺 | D：P0-①；收款格待真站点 |
| 5 | 其他单据类型 | 有（POS 日汇总；贷项转人工） | 缺 | 部分（只登记分期） | 缺 | 有（出入库 / 调拨 / 调整 / 收银日汇总 / 盘点调整） | 缺 | 部分（只采购、销售） | 缺 | D：POS 按天拆现销；库存进出导入恢复；其余走代录分期 |
| 6 | 新建客户 | 有 | 有 | 有 | 有·关 | 有 | 有·关 | 有 | 缺 | D：P0-①；主档导入模块真站点跑 |
| 7 | 新建供应商 | 有 | 有 | 有 | 有·关 | 有 | 有·关 | 有 | 缺 | 同上 |
| 8 | 新建物料 | 有 | 有 | 有 | 有·关 | 有 | 有·关 | 有 | 缺 | 同上；同名不同码会失败 |
| 9 | 挑已有的档 | 有 | 缺 | 有 | 部分（自动匹配，不出人选） | 有 | 部分 | 有 | 缺 | D：认领 MR.ERP 已有档（P1） |
| 10 | 仓库 | 有 | 部分（账套默认仓） | 有 | 有·关（行 `txtstore`） | 有 | 部分（默认仓，行不传） | 有 | 缺 | D：按行选（P1，按行交要真站点核） |
| 11 | 仓位（二级） | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | Express 本身没有，不做 |
| 12 | 付款方式 | 有 | 本身无 | 有 | 缺 | 部分（已付 / 未付） | 本身无 | 有 | 缺 | D：随现购走代录 |
| 13 | 收款方式 | 有 | 有（现金 + รับอื่นๆ） | 有 | 部分（只现金） | 有 | 部分（一律现金） | 有 | 缺 | D：接付款方式到 `bank_account`，真站点核 |
| 14 | 科目 | 有 | 有 | 有 | 有·关 | 有 | 部分（只公司默认四项） | 有 | 缺 | D |
| 15 | 税率 | 有 | 部分（免税不单列） | 有 | 部分（整单 4 档） | 有 | 部分（只交合计税额） | 部分 | 缺 | D：免税行拆 0% 单（口径主会话定）；预扣税两边都没有 |
| 16 | 查重 | 有 | 有（单号已存在 = 已推过） | 有 | 缺 | 按口径不查重（请求号幂等） | 同口径 | 只按任务幂等 | 缺 | A：跨模式同票跳过；D：代录疑似重复提示（P1） |
| 17 | 已写入判定（读回） | 有 | 有（真站点列表被挡 → 必判结果不明） | 有 | 有·关 | 有 | 有·关 | 有 | 缺 | A：已写入判定共用接口；D 接 |
| 18 | 结果不明核对后记下 | 有 | 有 | 有 | 有·关（本机记录；文案写 Express） | 有 | 缺 | 部分（未声明 resolve） | 缺 | A：批 3 统一 resolve；D 接（P0-⑦） |
| 19 | 开始前卡片 | 有 | 缺（跳过，改走 `cowork_mrerp_setup`） | 有 | 缺（回「未连接」） | 有 | 有·关 | 有 | 缺 | D：P0-③ |
| 20 | 复核卡（11-09 刚批） | 缺 | 缺 | 缺 | 缺 | 缺 | 缺 | 缺 | 缺 | A 做一份；D 接 MR.ERP 字段 |
| 21 | 问一次记住的选择 | 有 | 部分（只账套级菜单 / 默认值，公司规则拒存） | 有 | 部分（不在卡片里） | 有 | 有·关（单据页弹窗不认） | 有 | 缺 | D：P0-⑥、P1 按客户记 |
| 22 | 定时任务 | 有 | 有（无自动推档位） | 不适用（定时走直写） | 不适用 | 不适用（ERP 不做） | 不适用 | 不适用 | 不适用 | D：自动推档位（P1） |
| 23 | 收发存导出 | 不适用（Cowork 无库存） | 不适用 | 不适用 | 不适用 | 有（ERP 自家数据） | 有（同左，与账本无关） | 有 | 有 | — |
| 24 | 账套与年度切换 | 有 | 有 | 有 | 有·关 | 有（按日期挑） | 部分（一家一份，不按日期换年度） | 有 | 缺 | D：P1；只有一个测试年度（TEST2019），跨年度切换没实测（用户 11-09 定 TEST2020 不测） |
| 25 | 被踢出登录 / 授权 | 不适用（Express 无登录） | 有 | 有（凭据 + 授权上限） | 有·关 | 不适用 | 有·关 | 有 | 部分（授权入口有，无派单） | D：P0-①；风险见 §2 |
| 26 | 四语原因码 | 有 | 部分（约 30 个码无词条） | 有 | 部分（不发专用码、词条写死 Express） | 有 | 缺 | 有 | 缺 | D：P0-④（改已有文案先给用户看） |
| 27 | CC 设置 / 公司设置入口 | 有 | 有 | 有 | 部分（开始 / 续录卡只弹 Express 登录） | 有 | 缺（接口对不上，开关一开就坏） | 有 | 有·关（授权） | D：P0-⑤ |
| 28 | ADR-027 工具 / 接法一致 | 有 | 有 | 有（11 键） | 有（同一套） | 部分（resolve 参数收了不用） | 部分（说明写死 Express） | 部分（缺 regroup、resolve） | 缺（target 写死） | A：接法统一 + 检查盲区；D：ERP target |
| 29 | 回读账本 / VAT 草稿（Cowork 专属） | 有 | 缺 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | D：读报表页，要真站点 |
| 30 | 自动推档位 / 提议（Cowork 专属） | 有 | 缺 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | D（P1） |
| 31 | 代录认不出画面交 AI 接手 | 不适用 | 不适用 | 有 | 缺 | 不适用 | 不适用 | 有 | 缺 | D（P1） |

## 2. 依据（按行；路径缩写见末尾）

| # | 关键依据 |
|---|---|
| 1、3、4 | CW直：MP `modules.py:36-64`、`routing.py:68-85,121-136`；Express ER `mapper.py:15,339`、`sales_mapper.py:10,388`。CW代：Mjson `:37-64`、PC `mrerp/push/operator_draft.py:89-113`、Ejson `:46-107`。ERP直：E `mrerp/contracts.py:51-53,67-72`、E `express/push.py:290-292`。ERP代·M：E `express/operator.py:123-124`（target 写死 express）、E `mrerp/__init__.py:6`（「代录不在这里」）、PC `express_operator/products.py:26`、PC `cc_desktop/jobs.py:198,243-244,254-257`（任务不带账本、要求 Express 账套可达） |
| 2、12 | CW直 / CW代：CW `push/application/service.py:856-859` 服务端拒现购；Mjson `:38,75`（新增页有 `chkcash`）。ERP直：E `mrerp/contracts.py:23,68-72`（停在 `mrerp.purchase_cash`）、R `:137`、评估 `:44,96` |
| 5 | CW直：CC `pos_journal.py:1-12`、ER `doc_sanity.py:9`、CW service `:150`（POS 拒）、MP `routing.py:101`。CW代：Ejson `:111-117`、MO `draft.py:28`。ERP直：E `express/cc_tools.py:15-16,36-44`、E `express/push.py:32-35,542`、E `mrerp/push.py:8,31,46`、MP `modules.py:1`、`xlsx/generator.py:333`（库存进出迁移时删）。ERP代：E `express/operator.py:22,47-77` |
| 6–8 | CW直：CC `master_create.py`、`product_master.py`；MP `autocreate.py:49-86,118-150,223-267`、`transport.py:240-262,291-315`。CW代：EO `masters.py:1-20,205`。ERP直：E `express/api.py:77-92`、`push.py:238-257`；`modules.py:65-71`（主档模块 verified=False）、`transport.py:345-348`（同名不同码失败）。ERP代：E `express/operator.py:203-204,293,317` |
| 9 | CW `push/express/catalog_resolver.py`；MP `autocreate.py:73-80,234`、`transport.py:344-347`；E `mrerp/contracts.py:60`、`xlsx/lookups.py:88-115`；主档读取器 `master_reader.py:19-27` |
| 10–11 | CW service `:441-475`、PS `:31,312`；MM `:33,38,45`；EO `draft.py:46,148`、Mjson `:88`、MO `engine.py:222-224`；E `express/push.py:180-196,438-442`、E `express/stock_entry.py:122`（Express 无二级仓位） |
| 13–15 | WP `:66-70`；MP `sales_cash.py:71-76,229`、`xlsx/fmt.py:158-180`、`purchase.py:166,208`；MO `engine.py:169-171`、`operator_draft.py:35-37,108`；E `express/prepare.py:162-168`、`push.py:166-199,302-324,453-456`；E `mrerp/contracts.py:45-51`、`lookups.py:134-155`；R `:140` |
| 16–17 | T `:721`、CW service `:124,234`、CC `worker.py:235-246`、CC `write_verify.py`；MP `duplicates.py`、`transport.py:458-500`、`readback.py:28-40`、`execute.py:41-52`；EO `engine.py:4-5`、MO `engine.py:242-318`、MO `book.py:8,14,316`；E `express/push.py:401-402,458-463,592-630`；E `express/operator.py:584-620`；11-09 只读探路（列表页被挡） |
| 18 | T `:2300`、CW service `:930`、CW `push/mrerp/runner.py:133-135`；MO `book.py:107-182`、`engine.py:83-93`；CW `assistant/operator_source.py:367-378`；E `express/cc_tools.py:512-535`（只认 Express 写账件）、E `mrerp/push.py:110-111`、R `:139`；ERP hooks `:626-637`（无 resolve）、`cc-tools-baseline.txt:28` |
| 19 | PS `:55-150`，`:94-99`（跳过 MR.ERP）；T `:887-905`；FE `components/tool-uis/operator-shared.ts:278-293`；E `express/precheck.py:400-451,458-478,633-650`；E `express/operator.py:202-227`（代录预检只调 Express 的 `collect_document`） |
| 20 | 代码根全仓没查到；`docs/work/2026-11-09-cc-start-card-short.md:4`；设计在分支 `pearnly/cc-misuse-graceful-design` 的 `docs/work/2026-11-09-cc-misuse-graceful-design.md` 第 0 层 |
| 21 | CW `push/express/company_choices.py`、CT `:472-501`、T `:134-143,887-897`；E `express/choices.py`、E `mrerp/choices.py:97-117`、E `mrerp/push.py:184-202`、E `express/api.py:53-56`（单据页弹窗要求接 Express）、R `:138`；E `express/operator.py:511-519` |
| 22 | FI `push.py:217-231`、FI `destinations.py:21-52`、T `:3527-3561`；PC `express_operator/contracts.py:105`、MO `host.py:134-137`；盘点 §5 #16（ERP 不做） |
| 23 | E `inventory/card_export.py`、E `inventory/cc_tools.py:159,232`；盘点 `:258` |
| 24 | T `:1797-1855,2248`；`mrerp/catalog.py`；Ejson `:24-29`、EO `session.py:235-250`、MO `session.py:248-282`；E `express/target.py:1-46`、E `mrerp/catalog.py:75-89`、MP `execute.py:22`；E `express/operator.py:496`、PC `contracts.py:40`；11-09 只读探路（TEST2020 Unknown database） |
| 25 | CW `connections/mrerp_login.py`、CW runner `:112-116`、PC `mrerp/session.py:119-155`；EO `credentials.py:51-111`、Ejson `:17`；Mjson `:156`、MO `session.py:13-17,195-246`、`companion/src/companion/tomo_bridge.py:186-198`；E `mrerp/connection.py:74-109`、ERP hooks `:374-379`、E `mrerp/push.py:151-159`、MP `execute.py:30-34,60-63`；PC `cc_desktop/machine.py:102-160`；MO `engine.py:99-101` |
| 26 | TR `*.csv`（intake.error 105 条）、CW `push/export_facts.py:34-48`；PC `i18n/messages.tsv:978-1011`、FE `i18n.ts:1125-1354`；MO `engine.py:99-101`、`session.py:252-281`；E `i18n/messages.tsv`（无 mrerp 词条）、E `express/pending.py:59-74` |
| 27 | FE `components/settings.tsx:454`、`company-cards.tsx:110,772`、T `:4233`；FE `components/mrerp-connect.tsx:32,56,104-107,145`、`tool-uis/operator-resume.tsx:43`；E `masters/cc_projects.py:31-35`、E `mrerp/api.py:11-30`（无 `records` / `request_refresh`，`configure` 参数不同）；E `public/desk/company.js:195,256-266` |
| 28 | CW hooks `:684-706`、ERP hooks `:626-646`；PC `express_operator/products.py:97-110,140-143`；CW `assistant/operator_source.py:21-31`；PC `express_operator/tools.py:56-76`（说明写死 Express）；E `express/cc_tools.py:182-198,541,567-576,590,615`；`scripts/check-cc-tools.py`（已存在，在 `ci-candidate-checks.sh:26` 里跑；只比两边都有的工具、接法只比键名） |
| 29–30 | T `:2740-2747,3434`；CW `push/automation/sweep.py:49,102` |
| 31 | EO `host.py:440-448`；MO `host.py:82`、`engine.py:365-377` |

### 风险与潜在缺陷（开关关着，线上不受影响）

1. **ERP 代录资料区不分账本**：`MRERP_LEDGER_ENABLED` 一开，MR.ERP 公司排队 / 停下的单会进代录资料区；交单时 `push.send(operator=…)` 在 E `express/push.py:399-400` 转去 `mrerp_push.send`，代录标记丢掉，单子被悄悄改成导入直写。Cowork 有 `operator_express_only`（T `:909-911`）拦，ERP 没有。修：allowed / sources / submit 按公司账本拦或分流（P0-②，约 1 小时）。
2. **ERP 的 CC 设置 MR.ERP 一行开关一开就坏**：前端调 `records`、`request_refresh`、`configure(adapter, config…)`，ERP `mrerp/api.py` 没有前两个、`configure` 参数不同（P0-⑤）。
3. **直写会踢掉浏览器里的同账号**：MR.ERP 一登录就踢掉前一个会话；直写只避开代录占着的账号（CW runner `:146-148`），用户自己在浏览器登着同一账号没有保护。要不要处理（例如开始前卡片提醒、或排到代录同一把账号锁）**主会话定**。
4. **真站点现状**：列表页被「未链日记账」挡住 → Cowork 直写到 testcowork 的 MR.ERP 单现在会全部落「结果不明」（正式租户的账套菜单不同，不受这条影响，没核）。

## 3. 统计口径

按 §1 表格逐格归类（取格子开头的词；「同口径」「按口径不查重」算有，「只按任务幂等」算部分），脚本算出：

| 组合 | 有 | 有·关 | 部分 | 缺 | 本身无 | 不适用 |
|---|---|---|---|---|---|---|
| Cowork 直写 · MR.ERP | 16 | 0 | 4 | 6 | 2 | 3 |
| Cowork 代录 · MR.ERP | 1 | 12 | 6 | 7 | 0 | 5 |
| ERP 直写 · MR.ERP | 2 | 10 | 7 | 5 | 2 | 5 |
| ERP 代录 · MR.ERP | 1 | 1 | 1 | 24 | 0 | 4 |
| **合计** | 20 | 23 | 18 | 42 | 4 | 17 |

Express 一侧 124 格：有 93、部分 8、缺 4（复核卡）、不适用 19。表改了以重算为准。

## 4. 本记录的状态

- 只读，没有改代码、没有写 MR.ERP；子代理有一个在调查时跑过一次 `git fetch`（只拉取，没改动），违背任务书「不连网络」，已记在这里。
- 施工顺序照总表 D 线：③ 不依赖真站点的补齐（先 P0，再 P1）→ ④ 等岳父改好菜单后探路 15 项、少量写入实测（先审）→ ⑤ 两产品一起开代录开关、开 ERP 账本开关。每批单独分支、单独合入，动共用层先与 A 对文件。
- 要主会话定：逐行免税的会计口径（拆 0% 单？）；直写踢掉浏览器同账号要不要处理；P0 顺序是否照上表。

路径缩写：E=`apps/pearnly_erp_portal/pearnly_erp_portal/`，PC=`apps/pearnly_core/pearnly_core/`，MP=`PC mrerp/push/`，MM=`PC mrerp/masters.py`，ER=`PC express_rules/push/`，WP=`PC write_precheck/contracts.py`，
CW=`apps/pearnly_cowork/pearnly_cowork/`，T=`CW assistant/tools.py`，CT=`CW assistant/company_tools.py`，PS=`CW assistant/precheck_source.py`，FI=`CW folder_intake/`，TR=`CW translations/`，
CC=`companion/src/companion/`，EO=`CC express_operator/`，MO=`CC mrerp_operator/`，Ejson=`EO recipes/express-1.0.1.94.json`，Mjson=`MO recipes/mrerp-2026.11-src.json`，FE=`frontend/assistant/src/`，
R=`docs/work/2026-11-03-erp-mrerp-direct.md`，评估=`docs/work/2026-11-02-mrerp-operator-and-erp-company-评估.md`。
