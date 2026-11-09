#!/usr/bin/env python3
"""逐个跑一批离线单测（各自独立进程），任一失败退出 1。清单里每项写成 `tests/<文件>`，测试覆盖闸门按这个认「CI 已执行」。

11-09 起清点「没人跑的测试」时接进来的存量：
- 本机裸 python3 就能跑的进 `PLAIN`，Development checks 跑（`--plain`）；
- 要 Frappe 镜像里的依赖（frappe / pydantic / openpyxl …）的进 `IMAGE`，候选检查在镜像里跑（`--image`）。
新写的测试照常各自在工作流里加一步，不必往这里塞。

    python3 scripts/run-offline-tests.py --plain
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ("pearnly_core", "pearnly_cowork", "pearnly_dms", "pearnly_erp_portal", "pearnly_control")

PLAIN = [
    "tests/test_cc_company_menu.py",
    "tests/test_cc_company_picker.py",
    "tests/test_cc_lab_agents_ask.py",
    "tests/test_cc_run_ui.py",
    "tests/test_cowork_bank_card_fee.py",
    "tests/test_cowork_bank_groups.py",
    "tests/test_cowork_bank_recon_contracts.py",
    "tests/test_cowork_bank_review.py",
    "tests/test_cowork_buyer_resolve.py",
    "tests/test_cowork_classification.py",
    "tests/test_cowork_client_import.py",
    "tests/test_cowork_client_records.py",
    "tests/test_cowork_companion_queue_contracts.py",
    "tests/test_cowork_companion_queue_http_contracts.py",
    "tests/test_cowork_companion_release.py",
    "tests/test_cowork_connections.py",
    "tests/test_cowork_duplicates.py",
    "tests/test_cowork_engine_work_shares_patch.py",
    "tests/test_cowork_export_actions.py",
    "tests/test_cowork_express_branch.py",
    "tests/test_cowork_express_common_payment.py",
    "tests/test_cowork_express_doc_sanity.py",
    "tests/test_cowork_express_line_items.py",
    "tests/test_cowork_express_mapper.py",
    "tests/test_cowork_express_posting_wire.py",
    "tests/test_cowork_express_purchase_stock.py",
    "tests/test_cowork_express_sales_mapper.py",
    "tests/test_cowork_feedback_store.py",
    "tests/test_cowork_history_contract.py",
    "tests/test_cowork_income_parse.py",
    "tests/test_cowork_income_recon_contracts.py",
    "tests/test_cowork_income_recon_parse_info.py",
    "tests/test_cowork_invoice_policy.py",
    "tests/test_cowork_migration_baseline.py",
    "tests/test_cowork_mrerp_xlsx_source_parity.py",
    "tests/test_cowork_ocr_error_format.py",
    "tests/test_cowork_ocr_runtime_baseline.py",
    "tests/test_cowork_product_units.py",
    "tests/test_cowork_push_lifecycle.py",
    "tests/test_cowork_push_runtime_baseline.py",
    "tests/test_cowork_push_runtime_drift.py",
    "tests/test_cowork_recon_export_names.py",
    "tests/test_cowork_reconciliation_amounts.py",
    "tests/test_cowork_reconciliation_bank_matching.py",
    "tests/test_cowork_reconciliation_bank_model_path.py",
    "tests/test_cowork_reconciliation_bank_parse.py",
    "tests/test_cowork_reconciliation_bank_reread.py",
    "tests/test_cowork_reconciliation_bank_statement.py",
    "tests/test_cowork_reconciliation_contracts.py",
    "tests/test_cowork_stock_acc_group_choice.py",
    "tests/test_cowork_stocktake_contracts.py",
    "tests/test_cowork_stocktake_native_contract.py",
    "tests/test_cowork_stocktake_upload.py",
    "tests/test_cowork_supplier_posting.py",
    "tests/test_cowork_targets.py",
    "tests/test_cowork_vat_check.py",
    "tests/test_cowork_vat_parse.py",
    "tests/test_cowork_vat_void.py",
    "tests/test_cowork_work_boards.py",
    "tests/test_cowork_work_contracts.py",
    "tests/test_document_numbering.py",
    "tests/test_erp_error_details.py",
    "tests/test_erp_portal_access.py",
    "tests/test_listing_sort.py",
    "tests/test_opening_batch.py",
]

IMAGE = [
    "tests/test_cowork_accounting.py",
    "tests/test_cowork_bank_exports.py",
    "tests/test_cowork_bank_recon_export.py",
    "tests/test_cowork_category_memory.py",
    "tests/test_cowork_companion_legacy_wire.py",
    "tests/test_cowork_document_ai_regions.py",
    "tests/test_cowork_document_policy.py",
    "tests/test_cowork_document_tier.py",
    "tests/test_cowork_engine.py",
    "tests/test_cowork_income_recon_export.py",
    "tests/test_cowork_input_runtime.py",
    "tests/test_cowork_intake_preview.py",
    "tests/test_cowork_invoice_checks.py",
    "tests/test_cowork_invoice_grouping.py",
    "tests/test_cowork_invoice_results.py",
    "tests/test_cowork_invoice_schema.py",
    "tests/test_cowork_invoice_validation.py",
    "tests/test_cowork_ocr_attachments.py",
    "tests/test_cowork_ocr_chain_repair.py",
    "tests/test_cowork_ocr_contracts.py",
    "tests/test_cowork_ocr_document_schema.py",
    "tests/test_cowork_ocr_document_validation.py",
    "tests/test_cowork_ocr_magnitude.py",
    "tests/test_cowork_ocr_store_save.py",
    "tests/test_cowork_provider_work_guard.py",
    "tests/test_cowork_recon_intake_fixes.py",
    "tests/test_cowork_recon_ledger_edits.py",
    "tests/test_cowork_recon_reread.py",
    "tests/test_cowork_recon_sheet_dates.py",
    "tests/test_cowork_reconciliation_bank_intake.py",
    "tests/test_cowork_reconciliation_bank_text.py",
    "tests/test_cowork_reread_checks.py",
    "tests/test_cowork_review_reasons.py",
    "tests/test_cowork_roundtrip_export.py",
    "tests/test_cowork_roundtrip_parser.py",
    "tests/test_cowork_scope_queries.py",
    "tests/test_cowork_stocktake_export.py",
    "tests/test_cowork_table_engine.py",
    "tests/test_cowork_table_input_runtime.py",
    "tests/test_cowork_workspace_import.py",
    "tests/test_dms_grouped_snapshots.py",
    "tests/test_dms_ocr_timing.py",
    "tests/test_opening_workbook.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plain", action="store_true")
    group.add_argument("--image", action="store_true")
    args = parser.parse_args()
    tests = PLAIN if args.plain else IMAGE
    path = [str(ROOT / "apps" / app) for app in APPS] + [str(ROOT / "tests")]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(path + [os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
               PYTHONDONTWRITEBYTECODE="1")
    failed = []
    for test in tests:
        start = time.monotonic()
        done = subprocess.run([sys.executable, test], cwd=ROOT, env=env, capture_output=True, text=True)
        if done.returncode:
            failed.append(test)
            print(f"FAIL {test}\n{done.stdout}{done.stderr}", file=sys.stderr)
        else:
            print(f"ok   {test} ({time.monotonic() - start:.1f}s)")
    print(f"{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
