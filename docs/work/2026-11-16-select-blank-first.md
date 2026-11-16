# Select 存空被补成第一项：销售单收款方式（2026-11-16）

分支 `pearnly/select-blank-first`，基线 origin/main `b30469eda`。来源：验收首跑 `acc.erp.sales.credit.express`
（证据 `~/pearnly-devtools/acceptance-runs/20261116-124458-0309abf12/acc.erp.sales.credit.express/`）。

## 现象与原因
未收款的 ERP 销售单（payment_status=unpaid、paid_amount=0）库里 payment_method=cash。`sales.entry._collection` 对这种单传 None，
但字段是没有空选项、没有默认值的 Select（`cash\ntransfer\npromptpay\nother`），Frappe 保存时把空值补成第一项 cash。
Express 写账不受影响；读单处（CC 工具、LINE 卡片、接口、sales/output、sales/records）会看到未收款单带「现金」。

## 改动
- `erp_sales_document.json`：payment_method 选项首行留空（原生写法）。
- 补丁 `v1_32_unpaid_sales_payment_method`：只把「unpaid 且 paid_amount=0 且 cash」改回空。判据依据：服务端开单 / 改收款对这种单一律写空，
  所以这些 cash 只可能是补出来的；部分收款、已收款的单一个不动。重跑无变化。
- 新闸门 `scripts/check-select-blank.py` + `tests/test_check_select_blank.py`（CI 一步）：非必填、无默认、首行不空的 Select 一律报，
  除非在 `ALLOW` 写明不会存空的依据。

## 全仓同类（208 个 Select，命中 21 个）
确有同样问题的只有销售单这一个。17 个写入点总给值或 validate 自处理（登记在 ALLOW）。3 个「待核」：
ERP LINE Intake.item_kind、Pearnly Express Operator Task Document.doc_group、ERP Purchase Payment.method——本条合入后逐个只读核实，
确有问题另开小分支修，没有的把登记改成有依据的结论。

## 未处理（主会话 11-16 定：这次不改）
- 采购单 Purchase Document.payment_method：字段默认 cash，开单 `purchasing/entry.py:231`（`payload.get("payment_method") or "cash"`）与
  LINE 录入 `integrations/intake.py:316`（`doc.payment_method or "cash"`）对未付也写 cash，赊购会显示成「现金」。
  与用户 10-28 口径「采购单 = 已付 + 到货的进货单」一致；Express 推送已避开（`express/push.py:510` 注释「采购默认现金，不算数」），
  读单处（采购详情、列表、CC 工具、打印）显示的收款方式对赊购不对。等用户真提到赊购再议。

## 验证
见下方（实验栈统计、迁移、流程、真库插单读回、闸门、候选离线）。
