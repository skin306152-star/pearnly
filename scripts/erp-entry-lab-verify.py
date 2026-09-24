"""Dedicated synthetic Site B only. Adds test fixtures; no deletion or external calls."""
import runpy
import sys
import unittest
from pathlib import Path

lab = runpy.run_path(str(Path(__file__).with_name("erp-purchase-lab-verify.py")))
lab["connect"]("erp-pos-b.localhost")
sys.path.insert(0, "/home/frappe/frappe-bench/apps/pearnly_control")
suite = unittest.defaultTestLoader.loadTestsFromName("pearnly_erp_portal.access.test_entry_permissions")
result = unittest.TextTestRunner(verbosity=2).run(suite)
import frappe
frappe.destroy()
raise SystemExit(0 if result.wasSuccessful() else 1)
