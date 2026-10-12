#!/usr/bin/env python3
"""Run only audited zero-I/O DMS regressions; old scripts report via RESULT.

Do not glob sample-verify scripts: many other samples perform real business writes.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CASES = ("client", "booking-foundation", "booking-payments", "booking-readback", "retry", "quota")
# Explicitly audited stdlib suites use unittest's exit status, not the legacy
# RESULT marker. Frappe-dependent snapshot suites run in the candidate image.
UNIT_TESTS = (
    "scripts/test-dms-snapshot-reader.py",
    "tests/test_dms_catalogue_reader.py",
    "tests/test_dms_company_catalogue_schema.py",
    "tests/test_dms_geo_list_reader.py",
    "tests/test_dms_eventlog_reader.py",
    "tests/test_dms_change_sync.py",
    "tests/test_dms_nightly_refresh.py",
    "tests/test_dms_connection_snapshots.py",
    "tests/test_dms_operator_advisors.py",
    "tests/test_dms_snapshot_storage.py",
    "tests/test_dms_manual_busy_retry.py",
    "tests/test_dms_read_priority_wait.py",
    "tests/test_dms_recording_fixes.py",
    "tests/test_dms_menu_manual.py",
    "tests/test_dms_customer_update_rules.py",
    "tests/test_dms_read_scope.py",
    "tests/test_dms_geo_near_match.py",
    "scripts/test-dms-customer-conflicts.py",
    "tests/test_dms_customer_reconcile.py",
    "tests/test_dms_booking_customer_gapfill.py",
)

if __name__ == "__main__":
    env = dict(os.environ, PYTHONPATH=str(ROOT / "apps/pearnly_dms"))
    failed = False
    for case in CASES:
        result = subprocess.run([sys.executable, str(ROOT / f"scripts/sample-verify-dms-{case}.py")],
                                env=env, capture_output=True, text=True)
        passed = result.returncode == 0 and "RESULT: ALL PASS" in result.stdout and "RESULT: FAIL" not in result.stdout
        print(f"{case}: {'PASS' if passed else 'FAIL'}")
        print("\n".join(line for line in result.stdout.splitlines() if line.startswith("合计:")))
        if not passed:
            print(result.stdout); print(result.stderr)
            failed = True
    for script in UNIT_TESTS:
        result = subprocess.run([sys.executable, str(ROOT / script)],
                                env=env, capture_output=True, text=True)
        passed = result.returncode == 0
        print(f"{script}: {'PASS' if passed else 'FAIL'}")
        print("\n".join(line for line in result.stderr.splitlines() if line.startswith("Ran ")))
        if not passed:
            print(result.stdout); print(result.stderr)
            failed = True
    raise SystemExit(failed)
