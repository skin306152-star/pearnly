# 2026-09-30 税号补 0、分类按"卖方 + 品名"记忆

状态：已合入 main，未部署（随统一部署）。分支 `codex/tax-zero-category-memory`，基线 `2eb54512`（= 当时 `origin/main`）。

## 起因（erpcowork 推送的 IV69/08-001…030 实测）

- 13 张里 3 张（002 / 010 / 011）卖方、买方税号都丢了开头的 0（读成 12 位），第二批 17 张里又有 4 张（014 / 016 / 019 / 022）。
  `011` 买方没人改到 → Express 推送被 `tax_id_invalid` 拦下。
- 用户："那个税号我记得上次修过，怎么没生效"：上次 `714d2627` 只在**对账中心**（`recon_center/invoice_ocr.py::_tax`）补 0，
  录入工作台的识别（`ocr/schema/invoice.py`）与推送（`contracts/push.clean_tax_id`）从没有这道处理。
- 分类：已有"按卖方记"的记忆（`history/categories.py`，`Cowork Supplier Category Memory`）——第一批改了 11 次后记住，
  第二批 17 张全部自动套用（该批没有任何人工更正记录）。但只按卖方记：销项票的卖方永远是自己公司，所有销项会被套成同一个分类。
  另：few-shot 示例（`OCR_FEWSHOT_ENABLED`）默认关闭，更正记录从未进过提示词，本次不动（开它会加 token 成本）。

## 修改

- 共用 `pearnly_core.tax_documents.amounts.repaired_tax_id(value)`：12 位纯数字且补 0 后通过 MOD-11 才补，否则原样返回
  （None / 空 / 13 位 / 带分隔符 / 补了也不过校验的都不动；整数返回补好的字符串）。
- Cowork 识别：`ThaiInvoice` 的 `seller_tax` / `buyer_tax` 字段校验调用它（所有识别阶段都经过这里）。
- Cowork 推送：`contracts/push.clean_tax_id`、`masters/field_clean.clean_tax_id` 先补再判 13 位 —— 已识别未改的旧单（如 011）推送时也自动修好。
- 分类记忆：学习时同时记"卖方 + 第一行品名"与"卖方"两条；套用时先品名、再卖方。记忆表加只读字段 `item_name`（DocType `modified` 已更新）。

## 与「旧项目迁移后的用户体验问题」窗口的分工（2026-09-30 对齐，选 A）

- 本窗口：共用函数 + Cowork 识别 / 推送 / 分类记忆（上面这些文件，与其分支零重叠）。
- 对方：本分支合入 main 后变基，在 ERP `purchasing/recognition.py`、`sales/recognition.py` 对 `supplier_tax_id` / `buyer_tax_id` 调用
  `repaired_tax_id`；顺手把 `recon_center/invoice_ocr.py::_tax` 换成共用函数（加上校验位）。
- ERP 采购明细按"供应商 + 品名"的分类记忆：等对方合入后由本窗口做（要改 ERP 识别的套用点）。

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| 工作树 | 本机 python 3.14 | `test_tax_documents.RepairedTaxId`（erpcowork 真实读错的 4 个号 + 不该补的情况）、`test_cowork_push_contract` | PASS |
| 工作树 | 本机 | development.yml 25 条闸门 | PASS |
| 工作树（五个 App 源码只读覆盖） | 生产 Cowork 镜像 `4ff689c1`，断网、2G | `test_cowork_invoice_schema`（含 TaxIdRepair）、`test_cowork_category_memory`（新）、`test_cowork_push_contract`、`test_cowork_classification`、`test_tax_documents`；`ci-candidate-checks.sh offline` 全部 | PASS |

未覆盖：真实站点上的一次识别 + 推送（随统一部署后在 erpcowork 重推 011 验证）；ERP 两处调用由对方窗口补。
