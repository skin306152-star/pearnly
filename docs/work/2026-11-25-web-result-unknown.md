# 网页「结果不明」不再显示成「失败」（2026-11-25，cc-coverage-4）

分支 `pearnly/web-result-unknown`，基 `origin/main` f18fa1ec8。主会话 11-25 批第 1、2、4、5 条（按缺陷修：显示错的状态改对，
只用已有词句）；第 3 条（收款撤回）有条件批，先评估「结果不明的了结出口」。

依据：主会话 11-25 定「结果不明不得显示成失败」——写成失败会让人重录、重复入账。

## 判据（一处定、各处用）

- ERP（单据账本状态）：`held` 且原因以 `cc_desktop.result_unknown`（CC 桌面版写到一半断了，`cc_desktop.jobs.LOST`）或
  `mrerp.result_unknown`（MR.ERP 导入发出后查不到 / 出错）开头 → `express.contracts.result_unknown`；网页同表
  `transactions.js` 的 `UNKNOWN_REASONS` / `p.result_unknown`（契约测试比对）。
- Cowork（录入票）：停在转人工且推送记录标了 `result_unknown`，或原因栏是结果不明码 → `push.unknown.is_unknown`；
  录入列表、复核页、公司待处理同一个函数。历史域经钩子拿（`cowork_history_push_marks` / `cowork_history_push_filters`），不导入推送域。

## 改了什么

| # | 位置 | 之前 | 之后 |
|---|---|---|---|
| 1 | ERP 状态词表（文档页、采购 / 销售 / 收银记录 / 出入库列表 Express 列、Express 弹窗「状态」、盘点弹窗、LINE 回执） | 失败 | 结果不明（已有词条 `Result unknown`） |
| 1 | ERP 采购 / 销售导出的账本状态列 | 原码 `held` 等 | 同列表的状态词 |
| 2 | ERP Express 弹窗「原因」、ERP 版 CC 待处理原因 | 空白 / 待处理 | 「结果未确认，请先在 Express 中核对，再决定是否重新推送」（同 Cowork 现成句，ERP 新增同文词条 `express.reason.resultUnknown`） |
| 4 | Cowork 录入列表状态药丸 | 红色失败 | 黄色结果不明；「推送状态」筛选加「结果不明」，「失败」不再含它（列表与计数同一筛选） |
| 5 | Cowork 复核页提示条 | 只认 6 个码，MR.ERP 发出后报错（推送记录已标结果不明、原因是 ERR_TECHNICAL 等）显示红条、没有「核对」 | 按推送记录标志判：黄条「结果不明 · 原因」+「核对」 |

ERP 列表本来没有按 Express 状态筛选，无需拆；ERP 弹窗 Express 结果不明的按钮「重试」实际是钉回原电脑按单号查重再写，不改字（主会话同意）。

## 第 3 条 A：收款 Express 结果不明（11-28 cc-coverage-5，主会话 11-25 批 A）

判据 `collection.contracts.result_unknown`：失败且原因以 `cc_desktop.result_unknown` / `mrerp.result_unknown` 开头；
`retryable` 只认写进 Express 的（原因恰为 `cc_desktop.result_unknown`）。网页 `collections.js` 同表（契约测试比对 `UNKNOWN_REASONS`）。

| 位置 | 之前 | 之后 |
|---|---|---|
| 收款页列表「Express」列、详情「状态」 | 失败（红） | 结果不明（不标红） |
| 收款页详情「原因」 | 原码 `cc_desktop.result_unknown` | 「结果未确认，请先在 Express 中核对，再决定是否重新推送」（`texts.reason`，ERP 已有词条） |
| 收款页详情按钮 | 撤回（变已取消，Express 里可能已有 → 重复） | 只给「重试」（`api_collection_retry` → `service.retry` → `push.retry` → 共用 `cc_desktop.jobs.retry`，钉回原电脑；CC 桌面版按参照号查重） |
| 服务端撤回（网页、LINE【撤回】、CC 撤回工具） | 失败即可撤 | 写进 Express 结果不明的拒（`collection.not_withdrawable`） |
| LINE 收款结果回执 | 失败 · 原码 | 结果不明 · 核对句 |
| CC 收款工具 / 收款引导结果卡 | failed + 「Express 没收下」定式 | `unconfirmed` + 不再录定式；结果卡「结果不明」+ 核对句（状态词来自 cc-status-words） |
| 收款导出「状态」列 | 失败 | 结果不明（其余状态的词不变） |

没做：收款列表「状态」筛选仍是原生 Select（失败档仍含结果不明）——这一档要另加一个不在单据栏位上的筛选值，需要改共用列表筛选，
没有动；MR.ERP 收款结果不明的出口归 B（另评估）。CC 里没有「重试」工具（CC 说明里让用户到网页收款页点重试），若要对等另开。

## 与 `pearnly/cc-line-result-unknown`（21fb47a04）的关系

LINE 回执与网页同一张表（`express.contracts.ledger_word`），本分支改了表后 ERP LINE 回执的状态词也变成「结果不明」。
以本分支的共用表为准；那条分支（LINE 保存回执：结果不明给【查看结果】、不给【编辑】）后合时变基到本分支之上，不各改各的。
真机断言（主会话并进三星上机）：ERP 用 LINE 保存一张采购单，Express 写入结果不明时，回执文字为
「结果不明 · 结果未确认，请先在 Express 中核对，再决定是否重新推送」，不出现「失败」；快捷按钮以那条分支合入后为准。

## 第 3 条与 MR.ERP：结果不明的了结出口（评估已发主会话，未施工）

- 收款 Express 结果不明：网页只有「撤回」（变已取消，Express 里可能已有 → 重复）。收款也走共用写账队列，桌面版写收款前按参照号查重
  （`dbf_receipt.existing`），可复用采购 / 销售同一条了结路径（`cc_desktop.jobs.retry`：钉回原电脑再排）。
- MR.ERP 结果不明（采购 / 销售 / 收款）：ERP 网页没有出口；最小出口建议复用 Cowork 复核页「核对」（已入账填单号 / 没有入账再推）。

## 验证

- 离线：`tests/test_line_receipt.py`（新：结果不明网页与 LINE 同一档）、`tests/test_erp_cc_companies.py`（待处理原因表改期望）、
  `tests/test_cowork_push_unknown.py`（新）、`tests/test_cowork_companies.py`、`tests/test_cowork_reason_language.py`。
- 实验栈实测：Frappe 16.33 的 `not like` 自动套 `IFNULL`，原因为空的票不会从「失败」筛选里漏掉。
- 页面流程：（补）

## 真机记录（11-28 三星，cc-coverage-5）

被测：本地组合 c33ad3576（= `pearnly/cc-line-result-unknown` 7474b324f，其下即本分支 37f728f91，再摘入 `pearnly/mrerp-unknown-check` 38c4e2417），
测试机1 testenv lab-start cowork（线上镜像 4cf9c586，实时源码），testcowork 测试副本；测完 lab-stop、域名回线上、定时任务恢复。截图 `~/pearnly-devtools/handoff-1128/cov5-liff-*.png`。

真机记录 | cowork.scan.line.stocktake.liff-camera | 2026-11-28 | 37f728f91 | 三星 SM-A146P + LINE Cowork-test + 测试机1 | 通过 | 被测组合 c33ad3576；富菜单「库存盘点」经 LIFF 打开盘点页 → 开始盘点 → 扫码 → LINE 原生相机授权 → 真相机对测试机屏幕 EAN-13 8906008420319 读出，认出 P-0016 → 完成不保存 |

## 11-28 变基到列车 + 状态筛选词（cc-coverage-5）

基线：`pearnly/train-1128` 8c9b0fab1 叠 `pearnly/list-status-filter-labels` 50ec83186（共用 list.js 的 `labels`：筛选下拉与条件标签显示格子同一套状态词）。
合上后本分支加的收款「状态」筛选档 `result_unknown` 会显示原码：`collection.contracts.STATUS_WORDS` 与 `collections.js` 的同名表加上
`result_unknown → Result unknown`（四语已有词条，与格子同源），下拉与条件标签显示「结果不明」；筛选值仍是原码，由 `status_filters` 在服务端换成库条件。
断言：`tests/test_line_receipt.py` 收款状态词——网页与契约同表，且 DocType 状态字段每一档都有状态词（不显示原码）。
