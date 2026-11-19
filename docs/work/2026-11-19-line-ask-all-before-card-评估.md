# LINE 识别：所有题问完再出卡（评估，2026-11-19）

用户 11-19 原话（经主会话转）：「我要所有问题问完发图片或者pdf识别，然后保存编辑或者取消，一气呵成，编辑是目的是有误的地方才点进去编辑然后在网页端点提交写入，而不是有些项 LINE 上问不了、只能进编辑页填，为什么问不到呢」

## 1. 目标流程

发图 / PDF → 识别 → LINE 里把要人定的题一次问完 → 最后发一张卡（保存 / 编辑 / 取消），卡上显示答过的结果。卡发出后不再追问。
「编辑」只改识别错的地方（网页编辑页改，网页上提交写入）。兜底不变：旧卡、题没答完点保存不入账，回原因。

## 2. 现在卡后还在问的（只读盘点）

| 入口 | 卡后追问 | 代码 |
|---|---|---|
| ERP 采购 / 销售 | 新物料怎么处理、多仓选仓；答完重发卡 | `integrations/intake.py:233-249`、`integrations/line_items.py` |
| ERP 采购 / 销售（接 Express / MR.ERP） | 写前判定要人选的项 LINE 不问，点保存才回原因 + 编辑 | `intake.save_card` → `express.api.preflight` |
| ERP 收款 | 缺转账凭证时卡后要照片 | `integrations/line_collection.py:830 _show_card` |
| Cowork 推送卡 | 新物料、多仓选仓；答完只回「已保存」不重发卡 | `line/intake.py:160 announce`、`line/items.py` |
| Cowork 推送卡 | 账套、存货科目组、Express 对应项 LINE 不问，点保存才停 | `push/application/card_save.py:63-76` |
| Cowork 仅识别 | 无 | — |

## 3. 写前判定项在 LINE 上怎么问（同一份出题与记答案，不另写）

出题与记答案一律走共用层 `pearnly_core.write_precheck`（产品钩子 `pearnly_write_precheck`：`check` / `search` / `answer`）与引导式出题 `write_precheck.guide.build`——就是 CC 开始前卡片、引导式录入、网页「选一次」用的同一套：题目、预选、推荐、按公司记住都由它出；LINE 只是把题渲染成消息、把点选交回 `answer`。

| 题类（`kind`） | 例子 | LINE 上的问法（现成组件） |
|---|---|---|
| 小列表（≤ 12 个选项） | 账套（同期几份）、现 / 赊、抵不抵、服务品口径、增值税登记户、免税写法、MR.ERP 菜单、代录子菜单 | 一条文字题 + 快捷回复按钮（同现有收款选方式、ERP 选仓） |
| 小列表（13–20 个） | 存货科目组、收款科目较多时 | 编号列表 + 快捷回复前 12 个，也可打编号（同现有「选择公司」那条） |
| 大列表（走搜索） | 对方档（供应商 / 客户）、存货、费用科目、收款 / 退货科目 | 预选项作第一个按钮；「请输入名称或编码查找」→ 打字 → `write_precheck.search` 服务端分页 → 编号结果 + 快捷回复（同现有「选已有物料」、收款选客户） |
| 新物料（ERP 本地物料） | 全部新建并入库 / 只记账 / 逐项 / 选已有 | 保留现有 `line_items`（它就是 ERP 代录的 own_products 那一类），先问它，再问写前判定 |
| 选仓 | 多仓定不下 | 保留现有快捷回复 |

- 题目一张票一题一条，答完自动下一题；一批几张票逐张问完、逐张出卡；没题的票直接出卡。
- 答案交 `answer`（同 CC 卡片）：按这家公司记住，下次不再问；答错（值不在可选范围）回原因、重问这一题。
- 问题会话沿用现有 LINE 会话（键 `ASKING`，30 分钟）；超时没答完：不出卡，回「已取消」，可重新上传或在网页「待处理」里处理（现有）。
- 实在判不了的（主档还在读、没电脑）：这些不是要人选的题，卡照出、照现在的兜底回原因。

## 4. 卡片

- 问完才发；卡上「仓库」「付款方式」等已有行显示答后的值；写前判定答过的项不另加行（已在编辑页可见）。
- 三按钮不变：保存 / 编辑 / 取消。
- 收款：缺凭证先要照片，收到后才出确认卡。

## 5. 新文案

只有一条新提示：「请输入名称或编码查找」——现有词条 `Type a word from the warehouse name to find it` 只对仓，ERP `line_items` 与收款选客户已有同类词条，优先复用现有的（施工时逐个核对，能复用就不加）。题目、选项文字全部来自共用层现有四语词条（CC 卡片已在用）。如确需新增，四语齐、过 `check-copy`，不改已有文案。

## 6. 改动量（机器耗时）

| 块 | 内容 | 估时 |
|---|---|---|
| 共用 LINE 出题器 | `pearnly_core` 里把 `guide.build` 的题渲染成 LINE 消息、处理点选 / 打字搜索、交 `answer`（ERP、Cowork 共用，纯渲染 + 会话） | 2 小时 |
| ERP | 识别完：先 `line_items` 再写前判定（收单记录 `INTAKE` 单号已被 ERP 判定认），问完出卡；`save_card` 题没答完不提交 | 1.5 小时 |
| Cowork | `announce`：先 `items` 再写前判定（`precheck_source` 已收账套、存货科目组、对应项），问完出卡；`items` 答完改成出卡 | 1.5 小时 |
| 收款 | 凭证提到卡前 | 0.5 小时 |
| 测试 | 每种题类、批量、超时、答错、旧卡兜底；源码锁 | 2 小时 |
| 闸门 + 候选 + CI | | 1.5 小时 |
| 真机 | ERP 采购（新物料 + 多仓 + 接 Express 要人选）、ERP 销售、ERP 无题直接出卡、Cowork 推送（新物料 + 账套 / 对应项）、收款缺凭证 | 1–1.5 小时 |

合计约 10 小时（写约 6、机器与真机约 4）。

## 7. 旧卡与兜底

不迁数据。已发出、题没答完的旧卡：点保存走兜底，不入账，回「请先回答上面的问题」或原因 + 编辑；题答完后再出的新卡正常保存。

## 8. 施工前要核对的两点

- ERP 判定的 `INTAKE` 单号原本给 CC 代录的收单记录用，LINE 收单记录能不能直接走（权限按发单人）——施工第一步核，不行就在 ERP 钩子里补认 LINE 收单记录，不另写判定。
- Cowork 判定里「结果不明（先核对）」要等电脑读账：这一项不出题，照现在的兜底。

## 9. 受影响入口与真机

产品地图：`erp.line.postback.erp_item`、`erp_save`、`erp.line.postback.col`、`cowork.line.postback.cowork_item`、`cowork_save`，新增 LINE 写前判定作答入口一行（ERP、Cowork 各一）。共用件：`pearnly_core.write_precheck`（已登记）连带回归 CC 开始前卡片、引导式、待处理。

## 10. 审定（11-19 主会话）

批准开工，附：① 推荐项排第一个按钮，只作推荐、点了才算，没点不往下走；钱和对象（对方、金额、科目这类）的题不预选；② 快捷回复按钮标签不超过 20 字，长名截短、全名写在题目正文；③ 新文案优先复用现成词条，真要新加四语齐、过 check-copy；④ 一类一提交：共用出题器、ERP、Cowork、收款各自分开提交，共用件按连带回归闸门出影响清单、逐个入口记结果；⑤ 真机格照第 6 节，另加「题答到一半又发了一张新票」，看会不会串单。

## 11. 共用件连带回归（11-19 晚，集成分支 e409145e1；命中 express-rules 20、write-precheck 10、assistant 13 及本次改到的 LINE 入口，去重 47 个）

依据：本机全闸门（3db9e8682 起只改了两个源码锁与一行产品地图说明）、候选离线 113 组 LANE EXIT 0（3db9e8682）、GitHub CI（推送后看，结果补在下面）、今晚测试机1 真机（`2026-11-19-erp-ui-three-device.md`）。

| 入口 | 类型 | 结果 | 依据 / 原因 |
|---|---|---|---|
| erp.line.precheck-questions | manual | 通过 | 真机（ERP 采购 A / B、销售、取消、排队、重带题、存货对照、科目搜索） |
| cowork.line.precheck-questions | manual | 通过 | 真机（K1–K4：问完出卡、卡列答案、排队、取消、仅识别、存货科目组题） |
| erp.line.item-questions | manual | 通过 | 真机（新物料题出卡前问；11-19 起答完不再回旧卡） |
| cowork.line.item-questions | manual | 通过 | 真机（全部新建存货 / 全部记费用） |
| erp.line.chat、cowork.line.chat | manual | 通过 | 真机走富菜单、快捷回复、打字搜、上传图片 |
| erp.line.intake.submit | manual | 通过 | 真机卡片保存写进 69EXP\TEST HP691119-001 |
| erp.ocr、erp.execution | manual | 通过 | 真机 LINE 发图识别（Claude，7 张） |
| cowork.execution、cowork.tax-documents | manual | 通过 | 真机 Cowork LINE 识别 4 张、推送 1 张写入（报税草稿没测到：不在本次改动链上） |
| cowork.cc.channel | manual | 通过 | 真机 Cowork 推送经 CC 桌面版写入 HP691119-002、读回一致 |
| erp.line.collection | offline | 通过 | 候选离线 + tests/test_erp_collection_line.py（缺凭证先要照片） |
| erp.cc.collection、erp.cc.chat-push、erp.cc.stocktake、erp.mrerp.masters、erp.mrerp.direct、cc.operator.page、cc.operator.start-card | offline | 通过 | 候选离线 113 组 + 对应单测 |
| erp.cc.write、erp.cc.stock-entry、cowork.express.push、erp.page.collections、erp.line.webhook-sim、cowork.line.webhook-sim | auto | 待 CI | GitHub Flows（推送后看） |
| erp.trade.line-stock、erp.place.header、erp.entry.choices | manual | 没测到 | 网页单据页本次未改；共用写账规则只多一道存货科目组题，单测 test_erp_write_precheck 覆盖 |
| erp.cc.pos-day、erp.cc.pos-refund | manual | 没测到 | 收银日汇总 / 退货不建新库存品，`new_item_group` 只在要新建库存品时生效；单测覆盖 |
| cowork.mrerp.push | manual | 没测到 | 存货科目组判定只走 Express 映射，MR.ERP 推送路径没改 |
| cowork.companies、cowork.connections、cowork.history、cowork.cc-chat.push、cowork.cc.troubleshooting、cowork.assistant | manual | 没测到 | 共用判定多一道存货科目组题（CC 卡片、复核页同用），今晚没开 CC 对话页实测；单测 test_cowork_write_precheck 56 个过 |
| cc.operator.thread、erp.cc.companies、control.assistant、shared.assistant、cc.assistant | manual | 没测到 | 只因 assistant 共用件被命中（write_plan / guide 数字规则）；CC 对话页今晚没开；单测 test_write_plan、test_cc_guide_documents 过 |
| erp.line.binding、cowork.line.binding、shared.team.members | manual | 没测到 | 本次没改绑定与团队页，命中来自 LINE 共用件 |
| erp.tax-documents | manual | 没测到 | 税务判断代码没改 |

已知限制：存货科目组「合格」只看存货科目，没看销货成本科目（CC 桌面版上报的判据）；69EXP\TEST 这类没配库存核算的账套，选了组仍会在写账时 `STOCK_IN_FAILED`。合格判据加成本科目派给库存对齐窗口，随 CC 1.12.22 发（主会话 11-19）。

补（11-19 晚，97df074d9 / d9b3494b5）：Flows 第一轮 16 个 ERP 库存 / 单据流程不通过——流程用例还按仓位写（11 个改成新口径），另修 4 处真缺陷（调拨行无仓时按台读行仓、LINE 卡片「编辑」打开时行仓丢了 LINE 答的仓、调整单先选物料后选仓弹「请选择仓库」、同名已有库存品被当新建而误判存货科目组）。共用件 `pearnly_core.express_rules.push.stock_acc_group` 与 `transactions.js` 连带入口：本机实验栈 25 格流程（新数据电脑 15、老数据电脑 10、手机 3）全过，候选离线 113 组 LANE EXIT 0（d9b3494b5），全闸门 exit 0；GitHub Flows 推送后复跑。
