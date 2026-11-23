# 增值税 / POS / 预扣税引导补「结果」步骤（11-23 评估）

基线 main 2e006a7bc。三项都是 Cowork 直写、表单源（`assistant/guided/{vat,pos,wht}.py`），声明里只缺 `result`，所以闸门判「不完整」、总表只能 ◐。
页面上其实已能走到结果卡：确定回包存进运行记录，引擎没有 `result` 时原样显示回包；缺的是「结果此刻怎样」的现算（过期、在写、已入账）。

## 三项各缺什么、结果步骤显示什么

同一套引擎与卡片：声明加 `"result": "…<模块>.result"`，签名同读回 / 对账 `result(ctx, draft)`，确定回包在 `ctx["done"]`，回 None 时引擎照存下的显示。
表单结果卡（一行状态 + 原因 + 合计 + 文件）与单据结果行（每张一行，`rows` 有就按单据画）都已有；前端只加「服务端回码 → 已有词条」的显示（见「实现」）。

| 项 | 确定做了什么（已有） | 结果步骤现算并显示 |
|---|---|---|
| 增值税 ภ.พ.30（不写账） | 按本对话账套这个月的回读出草稿，工作簿放进对话 | 重读草稿记录：仍是最近那份回读出的 → 「已就绪」、应缴 / 留抵那一栏作合计、检查项条数作原因行、工作簿；这本账套之后又回读完成了一份（月结台账「草稿已过期」同一判据 `companies.exceptions.tax_items`：草稿 `source_ref` ≠ 最近完成的回读）→ 原因行「草稿已过期」（沿用 `tax_draft_stale` 词条）；草稿被删 → 未完成 |
| POS 日汇总（写账） | 按天建票（`pos_import.service.admit`，幂等），票上写好推送目标；推送另走推送链 | 每天那张票一行（单据结果行）：取数直接用 Cowork 采购销售引导同一个 `guide_documents.result`（推送状态与单号一次取回），不另写；还没推过的标 `not_pushed`（页面显示月结台账已有词条「未推送」，不说已跳过），推了以后是已入账带单号 / 在写 / 未完成 |
| 预扣税 ภ.ง.ด.3 / 53（不写账） | 按复核后的草稿出工作簿 | 重读草稿记录：合计 = 草稿税额、工作簿（两张表收款方数与税额、待确认行数仍在回包里给模型；待确认行数没有现成词条，卡上不显示）；草稿被删 → 未完成 |

## 与 Express / MR.ERP 读回的关系

- 增值税：数据来自这本 Express 账套这个月的回读（第 2 步「回读」自动定，没读回退回那一步）。MR.ERP 没有读回，声明不含 mrerp（总表那格 `out:MR.ERP 无读回`）；
  云端 Express 仍等。结果步骤的「过期」只看回读，不读账本。
- POS：不用读回；确定只是按天建票，推送另走推送链写进 Express（只支持 Express 账套，MR.ERP 那格 `out`）。结果行的单号来自推送回执。
- 预扣税：数据来自 Cowork 自己识别的费用票（`tax.sources.wht_documents`），与账本无关，声明三种账本都含；结果不读账本。

## 页面流程（补齐后升格）

照 `cowork.cc.guide.readback` 的写法（菜单 → 题卡 → 复核 / 确定卡 → 结果卡），各一条：`cowork.cc.guide.vat`（造一份完成的回读）、
`cowork.cc.guide.pos`（对话里交一份导出文件）、`cowork.cc.guide.wht`（造带预扣税的已识别费用票）。`pearnly/cc-guided-flow-markers` 里
1b819808d / 63c2b5117 的三条旧等待流程按现在的卡片标记改写后挪进来，`tests/flows/waiting.toml` 与它的测试一并处理，挪完删旧分支。
升格：增值税、POS、预扣税 · Cowork 直写 · 本地 Express（预扣税是一格）half → done（✅7 之上 +3）。

## 实现（11-23，主会话批准后）

- 声明加 `result`：`vat.result`、`pos.result`、`wht.result`，回「确定回包 + 现算部分」（原字段保留，模型与原测试照看）。
- 结果卡上的字只用已有词条：服务端回码（表单结果 `marks`：`tax_issue` 带条数、`tax_draft_stale`；单据结果行 `reason_code`：`not_pushed`），
  页面按月结台账的 `co.ledger.code` / `co.ledger.issues` 显示；新增文案 0 条（`check-copy`）。「已就绪」状态行沿用现有结果卡（ready 不另写字）。
- 顺带修问题一的漏洞：没有复核的表单「已可确定、剩下的只有选填题」也出确定卡（增值税第 3、10 栏跳过后提交会回到同几道选填题、到不了确定）。
- 离线测试：`test_cowork_{vat,pos,wht}_guide` 各加一条结果现算的断言（过期 / 删了 / 未推送→已入账）；`test_cc_guide_run_card` 断言新条件。
- 页面流程：`cowork.cc.guide.vat`、`cowork.cc.guide.pos`、`cowork.cc.guide.wht`（共用 `cowork_cc_guide_form_lib.py`）。增值税的「之后又回读一份 → 过期」
  在 verify 里造第二份回读后按页面同一个取卡接口读（run.py 不支持步骤之间改站点数据）；预扣税「草稿删了 → 未完成」同样在 verify 里。
- 旧分支 `pearnly/cc-guided-flow-markers` 的三条等待流程、`tests/flows/waiting.toml` 与 `tests/test_flows_waiting.py` 都不进 main（被本批重写替代），旧分支删除。
- 总表：增值税、POS、预扣税 · Cowork 直写 · 本地 half → done（main 2e006a7bc 上 ✅6 → ✅9）。
