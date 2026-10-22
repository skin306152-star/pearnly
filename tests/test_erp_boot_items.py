#!/usr/bin/env python3
"""No Frappe: one failing ERP boot item must not take the others (LINE, warehouse) with it."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

MODULE = Path(__file__).resolve().parents[1] / "apps/pearnly_erp_portal/pearnly_erp_portal/masters/boot.py"


class Boot(dict):
    __getattr__ = dict.get


class ErpBootItems(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.conf = {"pearnly_product": "erp"}
        self.frappe.log_error = Mock()
        self.companies = types.ModuleType("pearnly_erp_portal.masters.company")
        self.companies.company_options = Mock(return_value=[{"name": "CO-00001"}])
        self.companies.active_company = Mock(return_value="CO-00001")
        self.companies.selling_warehouse = Mock(return_value="WH-1")
        self.guard = types.ModuleType("pearnly_erp_portal.access.guard")
        self.guard.has = Mock(return_value=True)
        masters = types.ModuleType("pearnly_erp_portal.masters")
        masters.company = self.companies
        access = types.ModuleType("pearnly_erp_portal.access")
        access.guard = self.guard
        self.gateway = types.ModuleType("pearnly_erp_portal.express.gateway")
        self.gateway.configured = Mock(return_value=True)
        express = types.ModuleType("pearnly_erp_portal.express")
        express.gateway = self.gateway
        modules = patch.dict(sys.modules, {
            "frappe": self.frappe, "pearnly_erp_portal": types.ModuleType("pearnly_erp_portal"),
            "pearnly_erp_portal.masters": masters, "pearnly_erp_portal.masters.company": self.companies,
            "pearnly_erp_portal.access": access, "pearnly_erp_portal.access.guard": self.guard,
            "pearnly_erp_portal.express": express, "pearnly_erp_portal.express.gateway": self.gateway})
        modules.start()
        self.addCleanup(modules.stop)
        self.namespace = {}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), self.namespace)

    def test_all_items_present_when_everything_works(self):
        boot = Boot()
        self.namespace["boot_session"](boot)
        self.assertEqual(boot, {"pearnly_companies": [{"name": "CO-00001"}], "pearnly_company": "CO-00001",
                                "pearnly_warehouse": "WH-1", "pearnly_line": True, "pearnly_express": True})
        self.frappe.log_error.assert_not_called()

    def test_tenant_without_company_still_gets_line(self):
        # mrerp 2026-09-30: no company yet → active_company raises; LINE must still show.
        self.companies.company_options.side_effect = PermissionError("No company")
        self.companies.active_company.side_effect = PermissionError("No company")
        self.companies.selling_warehouse.side_effect = PermissionError("No company")
        boot = Boot()
        self.namespace["boot_session"](boot)
        self.assertEqual(boot["pearnly_line"], True)
        self.assertEqual((boot["pearnly_companies"], boot["pearnly_company"], boot["pearnly_warehouse"]),
                         ([], "", ""))
        self.assertEqual(self.frappe.log_error.call_count, 3)

    def test_line_check_failure_defaults_to_hidden(self):
        self.guard.has.side_effect = RuntimeError("fixture")
        boot = Boot()
        self.namespace["boot_session"](boot)
        self.assertIs(boot["pearnly_line"], False)
        self.assertEqual(boot["pearnly_company"], "CO-00001")

    def test_express_check_failure_defaults_to_not_linked(self):
        self.gateway.configured.side_effect = RuntimeError("fixture")
        boot = Boot()
        self.namespace["boot_session"](boot)
        self.assertIs(boot["pearnly_express"], False)
        self.assertEqual(boot["pearnly_line"], True)

    def test_non_product_site_is_untouched(self):
        self.frappe.conf = {}
        boot = Boot()
        self.namespace["boot_session"](boot)
        self.assertEqual(boot, {})


if __name__ == "__main__":
    unittest.main()
