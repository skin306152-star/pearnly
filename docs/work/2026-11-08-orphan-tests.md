# 没人跑的测试清点（2026-11-08）

- 主会话 local_e395dace 派；分支 `pearnly/orphan-tests`，基线 `b29b11c84`。
- 起因：期初报表测试 `test_opening_report.py` 在 main 上 20 个错误却没人发现（不在任何 CI 步骤里，11-01 接口改了没跟）。

## 1. 口径与分类（第 1 步，只读）

「被 CI 执行」= `.github/workflows/*.yml` 的 run 行、`scripts/ci-candidate-checks.sh` 点名的（文件名或模块名），加上这些 run 行执行的脚本里按路径
`tests/<文件>` 列出的（`scripts/check-dms-offline.py` 的清单）。CI 里没有按目录或 discover 收测试的写法。只被别的测试 `import`
借样例（`*_source_parity` 借基础测试的数据）不算：被借的那个测试自己的用例没有跑。

| 类 | 个数 | 处理 |
|---|---|---|
| 工作流 / 候选检查直接点名 | 249 | 不动 |
| 经 `check-dms-offline.py` 清单执行（CI 跑它） | 28 | 不动 |
| 迁移对照工具（要 `--source` 传入旧仓 pearnly-app 固定提交的源码） | 44 | 写进 `scripts/tests/not-in-ci.tsv`，原因逐条写；迁移复核时手动跑 |
| 普通单测，从没被 CI 跑过 | 110 | 进 `scripts/tests/orphan-baseline.txt`（只许减少），第 2 步在本机与生产镜像各跑一遍再分：能过 / 过期 / 作废 / 真缺陷 |

合计 431 个（本分支另加闸门自测 1 个）。

## 2. 防再犯（第 4 步，随第 1 批合）

`scripts/check-test-coverage.py`（Development checks）：`tests/` 下每个 `test_*.py` 要么被 CI 执行，要么登记在 `not-in-ci.tsv` 并写原因；
清点中的存量在 `orphan-baseline.txt`，相对 merge-base 新加条目判失败，已被 CI 跑到 / 已删 / 已豁免的条目要同一提交删掉。
自测 `tests/test_check_test_coverage.py`：新加没人跑的测试被拦、只被 import 不算、豁免没写原因被拦、过期登记被拦、基线新加被拦、本仓通过。

## 3. 待清点的 110 个（第 2 步结果待补）

- `test_cc_company_menu.py`
- `test_cc_company_picker.py`
- `test_cc_lab_agents_ask.py`
- `test_cc_run_ui.py`
- `test_cowork_accounting.py`
- `test_cowork_bank_card_fee.py`
- `test_cowork_bank_exports.py`
- `test_cowork_bank_groups.py`
- `test_cowork_bank_recon_contracts.py`
- `test_cowork_bank_recon_export.py`
- `test_cowork_bank_review.py`
- `test_cowork_buyer_resolve.py`
- `test_cowork_category_memory.py`
- `test_cowork_classification.py`
- `test_cowork_client_import.py`
- `test_cowork_client_records.py`
- `test_cowork_companion_legacy_wire.py`
- `test_cowork_companion_queue_contracts.py`
- `test_cowork_companion_queue_http_contracts.py`
- `test_cowork_companion_release.py`
- `test_cowork_connections.py`
- `test_cowork_document_ai_regions.py`
- `test_cowork_document_policy.py`
- `test_cowork_document_tier.py`
- `test_cowork_duplicates.py`
- `test_cowork_engine.py`
- `test_cowork_engine_work_shares_patch.py`
- `test_cowork_export_actions.py`
- `test_cowork_express_branch.py`
- `test_cowork_express_common_payment.py`
- `test_cowork_express_doc_sanity.py`
- `test_cowork_express_line_items.py`
- `test_cowork_express_mapper.py`
- `test_cowork_express_posting_wire.py`
- `test_cowork_express_purchase_stock.py`
- `test_cowork_express_sales_mapper.py`
- `test_cowork_feedback_store.py`
- `test_cowork_history_contract.py`
- `test_cowork_income_parse.py`
- `test_cowork_income_recon_contracts.py`
- `test_cowork_income_recon_export.py`
- `test_cowork_income_recon_parse_info.py`
- `test_cowork_input_runtime.py`
- `test_cowork_intake_preview.py`
- `test_cowork_invoice_checks.py`
- `test_cowork_invoice_grouping.py`
- `test_cowork_invoice_policy.py`
- `test_cowork_invoice_results.py`
- `test_cowork_invoice_schema.py`
- `test_cowork_invoice_validation.py`
- `test_cowork_migration_baseline.py`
- `test_cowork_mrerp_xlsx_source_parity.py`
- `test_cowork_ocr_attachments.py`
- `test_cowork_ocr_chain_repair.py`
- `test_cowork_ocr_contracts.py`
- `test_cowork_ocr_document_schema.py`
- `test_cowork_ocr_document_validation.py`
- `test_cowork_ocr_error_format.py`
- `test_cowork_ocr_magnitude.py`
- `test_cowork_ocr_runtime_baseline.py`
- `test_cowork_ocr_store_save.py`
- `test_cowork_product_units.py`
- `test_cowork_provider_work_guard.py`
- `test_cowork_push_lifecycle.py`
- `test_cowork_push_runtime_baseline.py`
- `test_cowork_push_runtime_drift.py`
- `test_cowork_recon_export_names.py`
- `test_cowork_recon_intake_fixes.py`
- `test_cowork_recon_ledger_edits.py`
- `test_cowork_recon_reread.py`
- `test_cowork_recon_sheet_dates.py`
- `test_cowork_reconciliation_amounts.py`
- `test_cowork_reconciliation_bank_intake.py`
- `test_cowork_reconciliation_bank_matching.py`
- `test_cowork_reconciliation_bank_model_path.py`
- `test_cowork_reconciliation_bank_parse.py`
- `test_cowork_reconciliation_bank_reread.py`
- `test_cowork_reconciliation_bank_statement.py`
- `test_cowork_reconciliation_bank_text.py`
- `test_cowork_reconciliation_contracts.py`
- `test_cowork_reread_checks.py`
- `test_cowork_review_reasons.py`
- `test_cowork_roundtrip_export.py`
- `test_cowork_roundtrip_parser.py`
- `test_cowork_scope_queries.py`
- `test_cowork_stock_acc_group_choice.py`
- `test_cowork_stocktake_contracts.py`
- `test_cowork_stocktake_export.py`
- `test_cowork_stocktake_native_contract.py`
- `test_cowork_stocktake_upload.py`
- `test_cowork_supplier_posting.py`
- `test_cowork_table_engine.py`
- `test_cowork_table_input_runtime.py`
- `test_cowork_targets.py`
- `test_cowork_vat_check.py`
- `test_cowork_vat_parse.py`
- `test_cowork_vat_void.py`
- `test_cowork_work_boards.py`
- `test_cowork_work_contracts.py`
- `test_cowork_workspace_import.py`
- `test_dms_grouped_snapshots.py`
- `test_dms_ocr_timing.py`
- `test_document_numbering.py`
- `test_erp_error_details.py`
- `test_erp_portal_access.py`
- `test_listing_sort.py`
- `test_opening_batch.py`
- `test_opening_report.py`
- `test_opening_workbook.py`
- `test_release_member_identity.py`

## 4. 暂停交接（11-08 20:2x，本机 21:00 睡眠）

已做（本地提交，未推送）：第 1 步分类、第 4 步闸门 + 两张表 + 自测 + CI 一步、本记录。本地 `python3 scripts/check-test-coverage.py` 通过、自测 6 个通过；还没跑 gate.sh。
静态预查：110 个导入的本仓模块都还在；只有 `test_cowork_companion_release.py` 引用的 `companion/heartbeat.py` 已不存在，疑似作废，第 2 步实跑确认。

下一步（11-09 白天，按主会话）：
1. 先看 `pearnly/opening-report-test`（bd144b92b，已推送未合）合了没有。它合入后 `test_opening_report.py` 已由 CI 执行，本分支变基时要从 `orphan-baseline.txt` 和上面清单里删掉这一行（闸门会提示）。
2. 第 2 步：110 个在本机 `python3` 和生产镜像（`ghcr.io/skin306152-star/pearnly/frappe@sha256:26a2b0e8dfeec93a10560e2be8b6ea15aea27b9725b68547a5dc2b362b829add`，`docker run --rm --network none`，只读挂工作树）各跑一遍，按「能过 / 过期 / 作废 / 真缺陷」分类；先确认本机没有别的窗口在跑全套流程。分类清单先发主会话，真缺陷只列不改业务代码。
3. 分 3 批合：第 1 批 = 闸门 + 能过的直接接进 CI（同一提交从基线删掉）；第 2 批 = 修好的过期测试；第 3 批 = 删作废测试（写原因）+ 剩余登记。每批 gate.sh、推分支等 CI（推完 15 分钟以上再查）、按排号快进合入。
