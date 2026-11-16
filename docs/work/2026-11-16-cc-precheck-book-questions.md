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
