#!/usr/bin/env python3
"""Run only audited zero-I/O DMS regressions; old scripts report via RESULT.

Do not glob sample-verify scripts: many other samples perform real business writes.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CASES = ("client", "booking-foundation", "booking-payments", "booking-readback", "retry")

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
    raise SystemExit(failed)
