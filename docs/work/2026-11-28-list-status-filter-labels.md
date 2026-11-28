# 产品列表的状态类筛选：下拉显示原码

分支 `pearnly/list-status-filter-labels`，基线 `origin/main` 568f4b7f6。显示修复：只换下拉与条件标签上显示的词，筛选值仍是原码、仍由服务端筛选。

## 现象

ERP 收款列表「状态」筛选下拉显示 queued / writing / written / withdrawn 等原码（既有问题），列表格子里是「录入中 / 成功 / 失败 / 已取消」。

## 盘点（主会话 11-28 定：控制面这次不动）

产品列表都走共用件 `pearnly_core/public/desk/list.js`（`p.list` + `filter_fields` 白名单）。筛选下拉的选项取 DocType 元数据里的原码，按 `__(原码)` 翻译；格子另用各页一张「原码 → 状态词」表。两套不一致的：

| 列表 | 字段 | 修前（zh 下拉） | 格子用的词 |
|---|---|---|---|
| ERP 收款 | status | 草稿、已进入队列、writing、written、失败、withdrawn | `STATUS_WORDS`（录入中 / 成功 / 失败 / 已取消） |
| ERP 收款 | source | line、cc、web | `erp.collection.source.*` |
| ERP 销售 | payment_status | 未付款、partial、已付款 | `p.sales_ui.labels` |
| ERP 销售 | source | web、manual、ocr、line、pos、cc | `p.sales_ui.labels` |
| ERP 采购 | doc_kind | purchase_invoice、expense、货物、服务 | `p.purchase_ui.labels` |
| ERP 采购 | payment_status | 未付款、partial、已付款 | `p.purchase_ui.labels` |
| ERP 采购 | status | 草稿、已提交、已作废 | 格子 void 是「已取消」——**待主会话定词，本分支不动** |
| ERP POS 收据 | sale_type / status | 销售、退货 / 已完成、已作废（en 为小写原码） | `trade.kind.*` / `trade.state.*`（退货退款、Voided…） |

Cowork 识别记录 posting_kind 已一致；DMS 推送记录页头自己的 Select 已是 `{value, label}`；控制面只有管理员本机的原生 Desk 列表（产品代号等），不动。

## 改法

- `list.js`：页面在 `filter_fields` 里可给 `labels: 原码 → 状态词`（就是格子那个函数）。包 `frappe.ui.Filter` 的那段对带 labels 的 Select 字段克隆元数据字段，选项换成官方的 `[{value: 原码, label: 状态词}]`（同 Frappe 自己的 docstatus / Check 筛选写法，产品 Select（Autocomplete）与「在…之中」的 MultiSelect 都认），并给 `formatter`，条件标签「状态 = 录入中」用同一个词。元数据字段不改；`p.filter_values` / `p.filter_conditions` 不变。
- 各页只声明：收款 status / source；销售 doc_kind / payment_status / source；采购 doc_kind / payment_status；POS sale_type / status（逐词核过：下拉原来的「退货」与格子「退货退款」、en 小写原码与格子正式词不一致，加 labels 后与格子同词，不新造词）。
- 「结果不明」新档（分支 `pearnly/web-result-unknown`）只需加进收款的 `STATUS_WORDS`，筛选自动跟上。

## 测试

`tests/test-desk-list-filter.cjs`：labels → 选项 / formatter 形状、没给 labels 的字段原样、元数据不被改；**每个列表**逐档断言「等于」「在…之中」交给服务端的条件有没有 labels 都相同、是原码；采购 status 这次没加 labels。

## 共用件闸门与实测

`check-impact.py`：list.js 是登记的共用件，影响清单覆盖 ERP / Cowork / DMS 全部列表页（见交付说明）。
实测（只读，线上 testerp，e2eerp，`_lang` 切四语，浏览器里把 list.js 与四个页面换成本分支文件）：对照表 `~/pearnly-devtools/handoff-1128/list-filter/四语对照.md`。
改动后真实行为由列车在回归实验室覆盖：`erp.page.collections`、`erp.page.sales`、`erp.page.purchase`、`erp.page.pos-receipts` 及影响清单里其余列表页流程。

## 已知、没改

- 销售来源 web 与 manual 格子里都叫「手动」，cc 格子没有词（显示 cc）：沿用格子现状，下拉同样。

## 单据作废统一「已作废」（用户 11-28 原话：「统一已作废」）

主会话转达用户定词：单据作废（原码 void）统一用 POS 那套 `trade.state.void`——zh「已作废」、en「Voided」，th「ยกเลิกแล้ว」/ ja「取消済み」本来一致不变。只改单据作废这一个状态；收款「已取消」（withdrawn，撤回）与盘点调整单「已撤销」是别的状态，不动。

定词前的对照（同一状态 void 各处叫法）：

| 位置 | 来源 | zh | th | en | ja |
|---|---|---|---|---|---|
| 采购单列表格子 | purchase-fields.js `__("Cancelled")` | 已取消 | ยกเลิกแล้ว | Cancelled | 取消済み |
| 采购单筛选下拉 | 元数据原码 `__("void")` | 已作废 | ยกเลิกแล้ว | void | 取消済み |
| 销售单列表格子 | sales-fields.js `__("Cancelled")` | 已取消 | ยกเลิกแล้ว | Cancelled | 取消済み |
| POS 收据格子 + 筛选 | `trade.state.void`「Voided」 | 已作废 | ยกเลิกแล้ว | Voided | 取消済み |
| 作废按钮 / 提示 | `trade.void`、`trade.voidNotice` | 作废 / 已作废 | ยกเลิก | Void / Voided | 取消 |
| 收款撤回（另一状态） | `erp.collection.status.withdrawn` | 已取消 | ยกเลิกแล้ว | Cancelled | 取消済み |
| 盘点调整单被撤（另一状态） | `stock.count.entryVoided` | 已撤销 | ยกเลิกแล้ว | Voided | 取消済み |

### 改动清单（全仓查过）

| 位置 | 改前 | 改后 |
|---|---|---|
| 销售单列表格子、详情状态（`sales-fields.js` u.labels.void） | `__("Cancelled")` 已取消 / Cancelled | `__("Voided")` 已作废 / Voided |
| 采购单列表格子、详情状态（`purchase-fields.js` u.labels.void） | 同上 | 同上 |
| 采购单「状态」筛选下拉（本分支 labels） | 已作废 / void（原码） | 已作废 / Voided（同格子） |
| 销售单导出（`sales/output.py` ENUM_LABELS.void） | Cancelled → 已取消 | Voided → 已作废 |
| 采购单导出（`purchasing/output.py` ENUM_LABELS.void） | 同上 | 同上 |
| 单据详情「Express 作废进度」（`transactions.js`，Express 里的原单已作废） | Cancelled → 已取消 | Voided → 已作废 |
| 打印作废章（`printing/templates/trade.html`、`goods_receipt.html`、`goods_issue.html`、`thermal.html`） | ยกเลิก / CANCELLED | ยกเลิก / VOIDED |

查过、不改（不是单据作废这个状态）：收款 withdrawn「已取消」；盘点调整单「已撤销」；写账件被撤下的原因（`express/pending.py` / `transactions.js` 的 cancelled / replaced / voided → 已取消，是写账任务被撤，不是单据状态）；LINE / CC 里「Cancelled」都是操作被取消的回复；CC 结果卡没有单据作废状态词。
作废动作（主会话 11-28 定，依据同一句用户原话「统一已作废」：状态与使它变成该状态的按钮是同一件事）：

| 位置 | 改前 | 改后 |
|---|---|---|
| 销售 / 采购详情菜单的作废动作（`sales-form.js` / `purchase-form.js`） | `__("Cancel")` 取消 / Cancel | `__("trade.void")` 作废 / Void（th ยกเลิก、ja 取消 不变） |
| 作废确认框标题 | `__("Cancel")` 取消 / Cancel | `__("trade.void")` 作废 / Void |
| 作废完成提示 | `__("Cancelled")` 已取消 / Cancelled | `__("trade.state.void")` 已作废 / Voided |

| 作废确认框的原因栏（主会话 11-28 补定） | 「Reason for Cancellation」取消原因 | 新词条 `trade.voidReason`：zh「作废原因」、en「Void Reason」；th「เหตุผลในการยกเลิก」、ja「取消理由」与原来相同 |

确认框里关掉对话框的那颗按钮是框架自己的（放弃这次操作），主按钮「Confirm 确认」，都不动。
原因栏不复用已有的「Void Reason」：那条是 POS 收据（ERP Sale）的字段标签，中文是「撤销原因」。

作废原因一词（主会话 11-28 再定，依据同一句「统一已作废」）：

| 位置 | 改前 | 改后 |
|---|---|---|
| POS 收据作废原因（ERP Sale 字段标签 msgid「Void Reason」） | zh 撤销原因、th เหตุผลการยกเลิก | zh 作废原因、th เหตุผลในการยกเลิก（同 trade.voidReason）；en Void Reason、ja 取消理由 本来一致不动 |
| 销售 / 采购详情页的作废原因栏（`sales-fields.js` / `purchase-fields.js`） | 「Reason for Cancellation」取消原因 | `trade.voidReason` 作废原因 / Void Reason |

POS 收据列表、详情（小票查看器）、打印、导出里没有显示作废原因（只记在单据上），核过不用改。销售 / 采购 DocType 元数据里 void_reason 的标签仍是「Reason for Cancellation」：产品页不读它（详情页自己给标签、导出不含这一列），只在原生 Desk 表单里可见，不动。

改的都是代码里引用哪条已有词条（Cancelled → Voided、Cancel → trade.void），词条本身一字没改，`check-copy.py` 记为新增 1（trade.voidReason）、代码引用新增 6、改动 0、拦截 0；打印章是模板里的固定字。
