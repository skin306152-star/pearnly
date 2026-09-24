"""Opening stock checks in existing synthetic Site B only; no external calls/deletion."""
import runpy
import sys
import unittest
from pathlib import Path

lab = runpy.run_path(str(Path(__file__).with_name("erp-purchase-lab-verify.py")))
lab["connect"]("erp-pos-b.localhost")
sys.path.insert(0, "/home/frappe/frappe-bench/apps/pearnly_control")
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in (
    "pearnly_erp_portal.access.test_opening_native",
    "pearnly_erp_portal.inventory.test_opening_flow",
    "pearnly_erp_portal.access.test_native_boundaries.NativeBoundaries.test_openings_and_native_exports_respect_company",
    "pearnly_erp_portal.access.test_entry_flow_acceptance.EntryFlowAcceptance.test_stock_card_leaf_writes_opening_and_reads_card_without_duplicate_stock",
))
result = unittest.TextTestRunner(verbosity=2).run(suite)
import frappe
frappe.destroy()
raise SystemExit(0 if result.wasSuccessful() else 1)
