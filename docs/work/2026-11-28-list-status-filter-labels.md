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
