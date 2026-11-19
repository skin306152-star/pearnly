# 识别数字各入口一致（主会话 11-19，部署前条件）

分支 `pearnly/recognised-numbers-1119`，基于 main d2e9396e2。盘点：ERP 界面窗口 `识别数字规整-盘点-1119.md`（共享笔记，行号按集成分支 948c90b42）。
集成分支 948c90b42 的 `intake._rates` 止血改的也是 `build_payload`，合车时冲突以本分支的函数为准（主会话 11-19 同意）。

## 改了什么

| 入口 | 原来 | 现在 |
|---|---|---|
| 共用 `tax_documents.amounts` | `money` 只认逗号、฿、括号 | `number()` 再认 %、THB / บาท / Baht、各种空格、全角 / 印刷体减号，读不懂回 None（不当 0）；`tidy()` 规整一行；`blank()` 给网页 |
| ERP 采购识别出口 `clean_result` | 「1,234.50」数量 / 单价、凑整入账报值无效；读不懂的折扣当 0 | 出口规整成数字；金额读不懂原样留着让入账报错；税率读不懂去掉按默认 |
| ERP 销售识别出口 `map_recognised` | 「฿100」单价、整单折扣静默变 0；数量、折扣报错 | 同上；折扣格「10%」进 `discount_pct` |
| LINE 卡 / CC 收单 `build_payload` | 旧记录同上；销售行的 `discount_pct` 被丢掉 | 旧记录再规整一次；带上 `discount_pct` |
| 网页预填、LINE 卡「编辑」打开的页 | 「฿100」「(183.82)」显示成 0 | 读得懂的已是数字；读不懂的那格给 null，页面那格空着、原生必填，不填提交不了 |
| Cowork 识别（谷歌 / Claude） | 谷歌只规整单头金额，Claude 完全没规整 | 两条路线都规整单头金额、预扣税率、每行数量 / 单价 / 金额 |
| Cowork → MR.ERP 导入表 | 「1,234.50」读不懂当 0；预扣税「3%」丢掉；VAT「0%」落成 7% | 同一个 `number()`；`_decimal` 读不懂报错；货品行单价读不懂又没行金额时停下 |

口径（主会话 11-19）：明写「没有」的记号（-、n/a、ไม่มี）照旧按 0；真读不懂的不许静默变 0。

## 测试（先红后绿）

退回 main 的产品代码跑新测试：test_tax_documents 22 处、test_erp_recognised_numbers 7 处、test_claude_ocr_products 1 处、test_mrerp_import_amounts 4 处不过；修后全过。
另有站点级 `integrations.test_line_intake`（旧记录带逗号、销售百分比折扣），LINE / CC 收单站点单测 119 个全过。

本机同一 SHA：`gate.sh` GATE EXIT=0；候选离线 LANE EXIT 0（0 FAIL）；`--changed --base origin/main` 26 条流程 84/84 通过。
第一轮里 `erp.cc.operator.intake` new/desktop 一格多出一条前端上报「CC rapid_clicks: cc-askq-next 3x」（main d2e9396e2 新加的连点上报碰上流程连点），重跑通过，与本分支无关。

## 只读统计（主会话审、主会话跑）

`~/pearnly-devtools/prod-ro-1119/count-recognised-numbers-erp.sh`、`count-recognised-numbers-cowork.sh`（本体 `count-recognised-numbers.py`）。实验栈上按同一方式试跑过。

## 共用件连带回归（tax-documents、mrerp-rules、erp-transactions，57 个入口）

本机流程覆盖到的（上面 84/84 里的格）照实写哪条流程过了、入口自己的检查还差什么；其余要测试机、付费识别、LINE 真机或 MR.ERP 测试账号，记「没测到」并写要测什么，由主会话排。

| 入口 | 结果 |
|---|---|
| erp.mrerp.direct、erp.mrerp.masters | 通过（候选离线） |
| erp.trade.line-stock | 通过（流程 erp.trade.line-stock、erp.trade.line-stock-sales） |
| erp.page.purchase、erp.detail.purchase.* | 部分：流程 erp.purchase.create、erp.purchase.actions 通过（新建、提交、操作）；入口自己的自动冒烟（页面巡检、三种状态的详情）没跑 |
| erp.page.sales、erp.detail.sales.* | 部分：流程 erp.sales.actions、erp.sales.scan 通过；自动冒烟没跑 |
| erp.page.stock-in / stock-out / stock-transfer / stock-adjust，erp.detail.stock-* | 部分：流程 erp.stock-in.units、erp.stock-out.new、erp.stock-transfer、erp.stock-adjust、erp.stock-entry.scan 通过；自动冒烟没跑 |
| erp.products.new-dialog.opening、erp.products.find-by-code、erp.scan.pick-product | 部分：流程 erp.products.opening、erp.products.create、erp.products.clash 通过；入口自己的自动冒烟没跑 |
| erp.line.intake.submit（编辑页） | 部分：流程 erp.line.card-lines、erp.line.edit-gone、erp.line.edit-other-company 通过；LINE 真机确认那一半没测到 |
| erp.ocr、erp.execution、erp.tax-documents、cowork.ocr、cowork.execution、cowork.tax-documents、shared.tax-documents、shared.claude-ocr.* | 没测到：要付费识别小样本（采购、销售各一张带「฿」「1,234.50」「10%」的票），测试租户上传 → 预填 / 卡片的数对、读不懂的格空着必填；Claude 两项另跑金标回放 |
| cowork.mrerp.push、cowork.mrerp.catalog、cowork.cc.mrerp-setup | 没测到：要 MR.ERP 测试账号（test01 · TEST2019），Cowork 推一张带「3%」预扣税、带逗号明细的票，读回行金额与预扣税 |
| erp.cc.write、cowork.express.push、erp.cc.troubleshooting | 没测到：要测试机 Express 真写（自动冒烟 express / cowork_express） |
| erp.line.chat、erp.line.binding、erp.line.item-questions | 没测到：LINE 真机（三星 adb） |
| 其余 erp-transactions 入口（erp.page.inventory / pos-* / stock-card / stock-count、erp.place.header、erp.print.*、erp.purchase.voucher、erp.report.stock-count-variance、erp.serials.dialog、erp.*.item-scan、erp.inventory.opening.row-warehouse、erp.warehouses.express-new-hidden、erp.entry.choices） | 没测到：本分支只在共用明细组件里加了「值为 null 的数字格设必填」，不影响这些页面的其它行为；在测试租户自动冒烟（页面巡检）跑一遍即可 |
