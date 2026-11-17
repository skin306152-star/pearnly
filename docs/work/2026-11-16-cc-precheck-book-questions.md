# 开始前问完四题：单位、负库存、出库成本、现金科目（A3 / P2-2，11-16）

分支 `pearnly/cc-precheck-book-questions`，基线 origin/main 4879255e9。主会话 11-16 审定（评估：共享笔记「A3-开始前四题-评估-11-16.md」）。

## 为什么
CC 桌面版直写侧早已备好（载荷 21，1.12.21 已发）：`unit_code` / `factor`、`cost_choice` / `cost`、`cash_account`、`negallow`，`guided: true` 时缺了就停下回 `*_CHOICE`。
服务端从不判、不下发：单位、成本、现金科目由 CC 静默用默认（票面「箱」按「个」入库、没有成本基础不结转、现金科目不能记账改记应付 / 应收），负库存写时才拦。

## 原生依据
- 11-12 大样本只读统计（`docs/work/2026-11-12-cc-guided-entry-express-coverage.md` 第 2.4、9 节，在 `pearnly/cc-guided-entry-51673b`）：
  账套不许负库存 39.4%（S 盘 1033 本），物料级 NEGALLOW 空 65% / N 30% / Y 6%，NEGAUTH 非空 22.5%，原生出库行成本为 0 的 25–41%。
- 11-16 测试机1 只读统计（102 本 T 账套，主会话批）：ACCNUM00 102 / 102 可记账；账套不许负库存 29%、要批准 11%、按仓核 30%；物料级不许 22%；
  库存品 94.6% 只有一个单位；原生流水里单位不是该物料任何单位的行 0.2%。

## 做法（第 1 步：纯判据，本提交）
- `apps/pearnly_core/pearnly_core/express_rules/push/book_questions.py`：四题判据（逐条对齐 CC `stock_guard.shortages`、`guided._unit_line` / `_cost_line` /
  `choices_needed`）、出题 `questions()`、下发 `apply()`、记住 `remember_of()`、`held()`。
  - 负库存：已有物料不够 = 事实 + 「这张先留下」；新物料且账套不许 = 「设为允许负库存 / 先留下」，不预选，选了「允许」按账套记住，「先留下」不记；账套要批准只给「先留下」。
  - 单位：对不上才问；选项 = 物料各单位（带系数）+「填换算」，采购 / 销售只有一个对应包装单位时预选它；新物料解析不出 → 单位表。按「方向 × 物料 × 票面单位」记住。
  - 成本：出库无成本基础且负库存这一关已过才问；「不结转 / 填单位成本」，不预选、不记（D-2，主会话 11-16 再定）。
  - 现金科目：HP / HS 且 ACCNUM00 不能记账才问；名称含 เงินสด 的只有一个就自动用；按账套记住。
  - 代录：只出单位、负库存两题；`apply(mode=operator)` 不写 `guided` / `cash_account` / 成本键（CC 代录单带了会整单拒收）。
  - 题形状：选项可带 `entry`（选它还要填一个数，`{"input": "number", "min_exclusive": "0"}`，与引导引擎表单子题同一形状，P3 窗口 11-16 定），
    答案编码「码:数」（`factor:6`、`given:7.5`），没 `entry` 的选项答案就是码；服务端用 `answer_problem()` 核。已发 guided-erp（documents 源 / 预选卡）与 guided-frontend。
  - 资料缺（旧版 CC 主档读回没带 NEGAUTH / CHKNEGLEV / 单位表）不猜，那题不在开始前出，CC 写时照旧核。
- 测试：`tests/test_express_book_questions.py`（服务端 24 条）、`tests/test_companion_contract.py::BookQuestions`（常量两边一致、代录单过 CC 代录闸）、
  `companion/tests/test_seam_book_questions.py`（同一本合成账套两边真跑比结论，9 条；改坏按仓判法 / 成本判据会红，已反证）。

## 接下来（第 2 步，等 H5 服务端那块合 main 后在其上开）
- `choices.resolve` / `mapper` / `sales_mapper` 接出题，ERP `express/facts.py` 把主档镜像交给 `facts_of`、`express/precheck.py` 预选与记住，Cowork 先接现金科目。
- CC 写时回的 `UNIT_CHOICE` / `COST_BASIS_CHOICE` / `CASH_ACCOUNT_CHOICE` 转成同一题（与 H5 的停下出口钩子同一处）。
- documents 出卡已认 `entry` 形状（guided-erp `pearnly/cc-guide-documents` 7a997cb23，`guide/documents.py check_answers` 只核形状）；
  业务核对在记答那一步（`write_precheck.api.record`）调 `book_questions.answer_problem`，归本任务接线。
- 前端已认 `entry`（guided-frontend `pearnly/cc-guided-ui-review` f582741a8）：选中带 entry 的选项出一格数字、交「码:数」；负库存 Y / hold 按普通题画；
  「库存不够」事实按 `card.facts` 事实行显示。**前端不出词条：题目与选项字由服务端给**——接线时四题的题目、选项名、事实行四语词条由本任务补，过 check-copy。
- Cowork `precheck_source`：存货科目组用 H5 的 `_stock_group_item`；盘点窗口（clever-shannon）的 MR.ERP 菜单 / 默认值两类在 H5 之后接；
  本任务的现金科目题排在它们之后变基。
- 4 条流程：ERP 采购单位 / 销售新物料负库存 / 销售成本 / 现购现金科目。
- `guided: true` 只给人在场的入口（预选卡、题卡、人点的推送）；带它的单只派给报过载荷 21 的电脑，更老的沿用现成「要更新」词条。

## 要 CC 桌面版配合（cc-desktop-takeover 11-16 确认四项都归它；1.12.23 来得及就并进，否则 1.12.24；③④ 先上测试机拿原生证据）
mapper / sales_mapper 先后：H5 先合（等 erp-operator-intake 进 main 后变基），本任务接线分支在那之后的 main 上开。
服务端现读的字段名（`facts_of`）：settings `negauth` / `chkneglev`；products `std_cost` / `count_unit` / `count_factor`；units `short`。
1. 主档读回 settings 补 NEGAUTH、CHKNEGLEV，products 补 STNPR、点数单位 CQUCOD / CFACTOR，units 补 SHORTNAM；
2. 心跳目录补物料单位 / 系数 / negallow / 结存与账套设置、ACCNUM00（Cowork 用）；
3. 代录 `unit` 框按 `unit_code` / `factor` 点、去掉 `choice_points.NOT_WIRED`；
4. 原生实看：代录现购 HP 画面能不能选现金科目、ACCNUM00 不能记账时 Express 怎么拦。

## 未处理（记录在案）
- **G-A13 现销收的钱进哪个科目**：现销 HS 一律借账套现金科目，没有「收进银行」的选项（`companion/dbf_sales.py` 现销借方取 ACCNUM00 或载荷 `cash_account`）。
  影响：Cowork / ERP 直写的现销单（HS），客户实际收的是转账时总账记成现金。主会话 11-16 定本次只做 G-A2（现金科目不能记账时问），G-A13 不做。
- **LINE 上这四题怎么问**：LINE 入口也是人在场，但这次不带 `guided`、不出这四题（行为不变）。缺口已报引导式窗口写进总表对应格，估时见那边。

## 证据分层
- 实现：纯判据与测试（本提交）。本地测试：服务端单测、契约测试、跨边测试通过。部署、真机：无（第 2 步之后由主会话排）。

## 第 2 步：ERP 接线（分支 `pearnly/cc-precheck-book-wiring`，叠在 H5 089a1f6bb 之上；H5 进 main 后变基）
- `express/book_answers.py`（新）：什么时候问、记在哪、代录怎么下发。
  - **只在有人看着的地方问**：开始前确认卡片 / 代录预检 / 网页提交前弹窗（`precheck.collect` 包在 `asking()` 里）。提交后的直写在后台跑
    （`push.send`，分不出网页、CC 对话还是 LINE），只**套用**记住的与自动定下的值、不新增停下——LINE 这次不出四题、行为不变。
  - 记法（`ERP Express Choice`，对象带账套键）：单位 `{账套}|unit|…`、现金科目 `{账套}|cash` 按账套记；负库存选「允许」另记 `{账套}|new-item`、
    「先留下」不记（这次不写、下次再问）；出库成本对象带单据号 `{账套}|doc:{单据}|line-N`，只对这一张。
  - 网页提交前弹窗（表单没存，单据号固定 `preflight`）：**不问出库成本**（答案挂不到存好的单上）、负库存不给「先留下」（关弹窗就是不提交）。
    出库成本在 CC 直写的开始前确认卡片上问。网页提交、没经过确认卡片的销售单照旧由 CC 不结转（现状），记为余下缺口。
  - 代录（`operator.precheck` 包在 `operating()` 里；交单时 `conversation.for_task`）：只问单位、负库存，下发只写 CC 代录认的行键。
  - 已有物料库存不够：开始前判出时停在 `STOCK_INSUFFICIENT`（已有译文）；后台直写照旧交 CC 按原样核。
- `prepare.py` 准备好载荷后调 `book_answers.apply`；`precheck.py` 收齐时问、记答时用 `answer_problem` 核「码:数」、负库存「先留下」不记；
  `api._options` / `precheck._option` 保留选项的 `entry`；共用卡片契约 `write_precheck/contracts.py` 登记四类，带数字输入的选项收「码:数」。
- 选项字（四语，ERP 术语表 `express.book.*`、`express.precheck.note.packUnit`）；题类名在 CC 网页 `i18n.ts` 的 `precheck.kind`，由 guided-frontend 加
  （已发：unit 单位 / negative_stock 负库存 / cost_basis 出库成本 / cash_account 现金科目，四语见消息）。
- **`guided: true` 这次 ERP 不发**：发了 CC 会在没问过的入口（LINE、网页直接提交）停下，而 `*_CHOICE` 回执转成题这一处还没接；
  等回执转题接上再开（单据页 `api._party_blocked` 加三种码、代录卡同一出口）。
- Cowork 不改：四题资料（物料单位、结存、负库存设置、ACCNUM00）Cowork 心跳都没有，等 CC 桌面版补上（cc-desktop-takeover 已认领）。
- 测试：`tests/test_erp_book_questions.py`（内存替身 7 条）；流程 `erp.express.book-unit / -negative / -cost / -cash`（待排回归实验室：先红后绿）。

## 入口覆盖现状（11-16，接线分支；别当成已全覆盖）
| 入口 | 现在问哪几题 | 缺什么 |
|---|---|---|
| ERP · CC 直写开始前确认卡片（`precheck.collect_document`） | 四题全问 | 题目名词条在 guided-frontend `pearnly/cc-guided-ui-review` 336d991c9（未合入） |
| ERP · 网页采购 / 销售提交前弹窗（`api.preflight` → `collect_form`） | 单位、负库存（只给「允许」）、现金科目 | 出库成本不问（表单还没存，答案挂不到单上）：网页直接提交的销售单照旧不结转 |
| ERP · 代录开始前（`operator.precheck`，含收单记录 `I:`） | 单位、负库存 | 成本、现金科目由 Express 界面自己定（按口径不问）；代录里单位框还没按答案点（CC 桌面版 `choice_points.NOT_WIRED` → 1.12.23 / 1.12.24） |
| ERP · 后台直写（网页直接提交没经过确认卡片、定时、自动推） | 不问；只套用这本账套记住的答案 | 没记住的照旧：CC 静默默认（单位、成本、现金科目）或写时拦（负库存）。要靠 `guided: true` + CC 回执 `*_CHOICE` 转成题（单据页 / 代录卡同一出口），都还没接 |
| ERP · LINE 录单 | 不问（主会话 11-16 定这次不做） | 总表已记缺口，估 6–8 h / 2 h |
| ERP · CC 写时停下（`UNIT_CHOICE` / `COST_BASIS_CHOICE` / `CASH_ACCOUNT_CHOICE`） | 不会发生（ERP 不发 `guided`） | 开 `guided` 前先把三种码接进 `api._party_blocked` |
| Cowork · 全部入口（直写、代录、放票、文件夹、LINE） | 不问 | 四题资料不进心跳（11-17 与 CC 桌面版窗口定：心跳最多 2 万件物料，再加字段太大）：服务端开始前经按需请求通道请一次 `express_masters`（Cowork 已认这种请求，CC 不用改）、按 `express_marks` 缓存；之后 Cowork 判定卡接线（排在 H5、盘点窗口之后） |
| MR.ERP 账本 | 不涉及（四题是 Express 账套的规则） | — |

## 共用件连带回归记录（11-17，接线分支 744d77206；`check-impact` 命中 express-rules、write-precheck）
本分支在共用层只做两处：新文件 `express_rules/push/book_questions.py`（只被 ERP `express/book_answers.py` 调用，Cowork 与 LINE 不调）、
`write_precheck/contracts.py` 登记四个题类 + 带数字输入选项的答案核法（没有 `entry` 的选项走原来的分支，逐字节不变）。
| 入口 | 结果 | 证据 / 原因 |
|---|---|---|
| erp.cc.write | 通过 | 流程 erp.express.book-unit / -negative / -cost / -cash 4/4 绿（改前 4/4 红）；tests/test_erp_book_questions.py、test_erp_express_direct.py、test_erp_write_precheck.py |
| erp.cc.stock-entry | 通过（离线） | 库存单走 `stock_entry.prepare`，不经 `book_answers`；test_erp_express_direct.py 全过；流程没测到（未改这条路，没跑） |
| erp.page.collections、erp.cc.collection | 通过（离线） | 收款不经写账规则准备；test_erp_collection*.py 在本地全闸门里全过 |
| erp.cc.pos-day、erp.cc.pos-refund | 没测到 | 收银日汇总 / 退货单的载荷也经 `prepare.prepare`：没有四题资料（主档镜像里没有单位表 / 账套设置时不出题），离线 test_erp_express_direct 的收银用例全过；真机写 T 账套待主会话排 |
| erp.trade.line-stock、erp.place.header | 通过（离线） | 混行单、单头仓的载荷形状不变（四题只在有答案时加键）；test_erp_express_direct LineStockKinds 等全过 |
| erp.cc.companies、erp.mrerp.masters | 通过（离线） | 不经本改动的代码；MR.ERP 账本不调 `book_answers` |
| cc.operator.page、cc.operator.thread | 通过（离线） | 代录预检包了 `operating()`：test_express_operator_code_cc.py、test_cc_operator.py、test_express_operator_feed/tasks.py 全过；实机代录没测到（单位框 CC 未接） |
| cowork.express.push、cowork.cc-chat.push、cowork.cc.channel、cowork.cc.troubleshooting、cowork.companies、cowork.connections、cowork.history、cowork.line.item-questions、cowork.mrerp.push | 通过（离线） | Cowork 不调 `book_questions`；共用卡片契约只多了四个题类与 `entry` 分支：test_cowork_write_precheck.py、test_write_precheck.py 全过；Cowork 人工推送没测到（未改 Cowork 代码） |
- 本地全闸门（不跳 npm ci）744d77206：GATE EXIT 0，268 项。昨晚没过的 5 项是工作树没装前端依赖，装上后全过。
- 生产镜像候选离线（02eebaa4，`cand-local.sh offline`，744d77206）：111 组，LANE EXIT 0。
