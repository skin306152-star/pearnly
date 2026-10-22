#!/usr/bin/env python3
"""No Frappe: master records synced from Express cannot change any field Express has; ERP-only fields stay free."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "apps/pearnly_erp_portal/pearnly_erp_portal/express/locks.py"


class Refused(Exception):
    pass


class Row(dict):
    __getattr__ = dict.get


class Doc(dict):
    def __init__(self, doctype, values, before=None, new=False, sync=False):
        super().__init__(values)
        self.doctype = doctype
        self._before = before
        self._new = new
        self.flags = types.SimpleNamespace(express_sync=sync)

    def is_new(self):
        return self._new

    def get_doc_before_save(self):
        return self._before


class Locks(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text

        def throw(message):
            raise Refused(message)
        frappe.throw = throw
        modules = patch.dict(sys.modules, {"frappe": frappe})
        modules.start()
        self.addCleanup(modules.stop)
        self.namespace = {}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), self.namespace)
        self.guard = self.namespace["guard"]

    def supplier(self, **changes):
        before = Doc("ERP Supplier", {"supplier_name": "BANGCHAK", "tax_id": "0105536080112", "express_code": "B004",
                                      "phone": "", "is_active": 1})
        return Doc("ERP Supplier", {**before, **changes}, before=before)

    def test_identity_fields_refused(self):
        for change in ({"supplier_name": "X"}, {"tax_id": "0105536080113"}, {"is_active": 0}, {"express_code": ""}):
            with self.assertRaises(Refused):
                self.guard(self.supplier(**change))

    def test_every_express_field_refused(self):
        for change in ({"phone": "02-123"}, {"address": "x"}):
            with self.assertRaises(Refused):
                self.guard(self.supplier(**change))

    def test_erp_only_fields_and_sync_allowed(self):
        self.guard(self.supplier(note="ERP note"))
        doc = self.supplier(supplier_name="X")
        doc.flags.express_sync = True
        self.guard(doc)

    def test_records_without_express_code_are_free(self):
        before = Doc("ERP Supplier", {"supplier_name": "A", "express_code": ""})
        self.guard(Doc("ERP Supplier", {"supplier_name": "B", "express_code": ""}, before=before))
        self.guard(Doc("ERP Supplier", {"supplier_name": "B"}, new=True))

    def test_product_units_locked(self):
        before = Doc("ERP Product", {"product_name": "น้ำแข็ง", "express_code": "ICE", "units": [Row(unit_name="กล่อง",
                                                                                                    factor_to_base=12)]})
        after = Doc("ERP Product", {**before, "units": [Row(unit_name="กล่อง", factor_to_base=10)]}, before=before)
        with self.assertRaises(Refused):
            self.guard(after)
        with self.assertRaises(Refused):
            self.guard(Doc("ERP Product", {**before, "unit_price": 35}, before=before))
        self.guard(Doc("ERP Product", {**before, "image_url": "/files/ice.jpg", "min_stock": 5}, before=before))


if __name__ == "__main__":
    unittest.main()
