#!/usr/bin/env python3
"""No Frappe: ERP purchase lines remember their category by supplier + item name."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/category_memory.py"


class Row(dict):
    __getattr__ = dict.get


class FakeDb:
    """Just the reads and writes category_memory makes, over two in-memory tables."""

    def __init__(self):
        self.tables = {"ERP Purchase Category Memory": {}, "ERP Purchase Category": {}}

    def get_value(self, doctype, name, fields, as_dict=False):
        row = self.tables[doctype].get(name)
        return Row({key: row.get(key) for key in fields}) if row else None

    def exists(self, doctype, name):
        return name in self.tables[doctype]

    def set_value(self, doctype, name, values):
        self.tables[doctype][name].update(values)


class CategoryMemory(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        frappe = types.ModuleType("frappe")
        frappe.db = self.db
        frappe.ValidationError = type("ValidationError", (Exception,), {})

        def get_all(doctype, filters, fields, distinct, limit_page_length):
            rows = [row for row in self.db.tables[doctype].values()
                    if all(row.get(k) == v for k, v in filters.items())]
            seen = []
            for row in rows:
                pick = Row({key: row.get(key) for key in fields})
                if pick not in seen:
                    seen.append(pick)
            return seen[:limit_page_length]

        def get_doc(values):
            doc = types.SimpleNamespace()

            def insert(ignore_permissions=False):
                name = self.namespace["memory_name"](values["supplier"], values["item_key"])
                self.db.tables[values["doctype"]][name] = dict(values)
            doc.insert = insert
            return doc

        frappe.get_all, frappe.get_doc = get_all, get_doc
        modules = patch.dict(sys.modules, {"frappe": frappe})
        modules.start()
        self.addCleanup(modules.stop)
        self.namespace = {}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), self.namespace)
        categories = self.db.tables["ERP Purchase Category"]
        categories.update({
            "FOOD": {"is_active": 1, "parent_category": None},
            "FOOD-DRINK": {"is_active": 1, "parent_category": "FOOD"},
            "OFFICE": {"is_active": 1, "parent_category": None},
            "OLD": {"is_active": 0, "parent_category": None},
        })

    def learn(self, supplier, lines):
        self.namespace["learn"](supplier, lines)

    def fill(self, supplier, lines):
        return self.namespace["fill"](supplier, lines)

    def test_same_supplier_and_item_gets_the_remembered_category(self):
        self.learn("SUP-1", [{"description": "Coca-Cola 325ml", "category": "FOOD", "subcategory": "FOOD-DRINK"}])
        lines = [{"description": "coca cola 325 ml"}]
        self.assertEqual(self.fill("SUP-1", lines), 1)
        self.assertEqual(lines[0], {"description": "coca cola 325 ml", "category": "FOOD", "subcategory": "FOOD-DRINK"})

    def test_item_memory_wins_over_the_supplier_fallback(self):
        self.learn("SUP-1", [{"description": "Paper A4", "category": "OFFICE"},
                             {"description": "Water", "category": "FOOD"}])
        lines = [{"description": "Paper A4"}, {"description": "Water"}]
        self.fill("SUP-1", lines)
        self.assertEqual([row["category"] for row in lines], ["OFFICE", "FOOD"])

    def test_new_item_uses_the_supplier_only_when_it_has_one_category(self):
        self.learn("SUP-1", [{"description": "Water", "category": "FOOD"}, {"description": "Juice", "category": "FOOD"}])
        lines = [{"description": "Something new"}]
        self.fill("SUP-1", lines)
        self.assertEqual(lines[0]["category"], "FOOD")
        self.learn("SUP-2", [{"description": "Water", "category": "FOOD"}, {"description": "Paper", "category": "OFFICE"}])
        lines = [{"description": "Something new"}]
        self.assertEqual(self.fill("SUP-2", lines), 0)
        self.assertNotIn("category", lines[0])

    def test_never_overrides_a_chosen_category_and_ignores_other_suppliers(self):
        self.learn("SUP-1", [{"description": "Water", "category": "FOOD"}])
        lines = [{"description": "Water", "category": "OFFICE"}, {"description": "Water"}, {"description": ""}]
        self.assertEqual(self.fill("SUP-1", lines), 1)
        self.assertEqual(lines[0]["category"], "OFFICE")
        other = [{"description": "Water"}]
        self.assertEqual(self.fill("SUP-9", other), 0)
        self.assertEqual(self.fill(None, other), 0)

    def test_latest_choice_replaces_the_previous_one(self):
        self.learn("SUP-1", [{"description": "Water", "category": "FOOD", "subcategory": "FOOD-DRINK"}])
        self.learn("SUP-1", [{"description": "WATER", "category": "OFFICE"}])
        lines = [{"description": "water"}]
        self.fill("SUP-1", lines)
        self.assertEqual(lines[0], {"description": "water", "category": "OFFICE"})
        self.assertEqual(len(self.db.tables["ERP Purchase Category Memory"]), 1)

    def test_disabled_category_is_not_applied_and_stale_subcategory_is_dropped(self):
        self.learn("SUP-1", [{"description": "Old thing", "category": "OLD"},
                             {"description": "Tea", "category": "OFFICE", "subcategory": "FOOD-DRINK"}])
        lines = [{"description": "Old thing"}, {"description": "Tea"}]
        self.fill("SUP-1", lines)
        self.assertNotIn("category", lines[0])
        self.assertEqual(lines[1], {"description": "Tea", "category": "OFFICE"})

    def test_matched_supplier_is_none_when_ambiguous(self):
        selection = types.ModuleType("pearnly_erp_portal.masters.selection")

        def match_party(*args, **kwargs):
            raise sys.modules["frappe"].ValidationError("Several matches found")
        selection.match_party = match_party
        with patch.dict(sys.modules, {"pearnly_erp_portal": types.ModuleType("pearnly_erp_portal"),
                                      "pearnly_erp_portal.masters": types.ModuleType("pearnly_erp_portal.masters"),
                                      "pearnly_erp_portal.masters.selection": selection}):
            self.assertIsNone(self.namespace["matched_supplier"]({"supplier_name": "A"}))


if __name__ == "__main__":
    unittest.main()
