"""Real report/valuation code with read-only fake Site queries; no runtime or writes."""
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


DOMAIN = Path(__file__).resolve().parents[1] / "apps/pearnly_erp_portal/pearnly_erp_portal/inventory"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class OpeningReport(unittest.TestCase):
    def setUp(self):
        self.movements, self.queries, self.required = [], [], []
        self.cost_visible = True
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        self.contracts = load("opening_report_contracts", DOMAIN / "contracts.py")
        frappe = ModuleType("frappe")
        frappe._ = lambda value: value
        frappe.get_list = self.get_list
        frappe.db = SimpleNamespace(get_value=lambda doctype, name, field: "Warehouse " + name)
        utils = ModuleType("frappe.utils")
        utils.getdate = lambda value: date.fromisoformat(str(value))
        utils.nowdate = lambda: "2026-09-24"
        access = ModuleType("pearnly_erp_portal.access")
        access.guard = SimpleNamespace(require=self.required.append, cost_visible=lambda: self.cost_visible)
        inventory = ModuleType("pearnly_erp_portal.inventory")
        inventory.contracts = self.contracts
        inventory.warehouses = SimpleNamespace(resolve=lambda value, **kwargs: value,
            scope_filter=lambda: {"warehouse": ["in", ["W1", "W2"]]})
        sys.modules.update({"frappe": frappe, "frappe.utils": utils,
                            "pearnly_erp_portal.access": access, "pearnly_erp_portal.inventory": inventory})
        self.report = load("opening_report_service", DOMAIN / "report.py")

    def get_list(self, doctype, filters, fields, **kwargs):
        self.queries.append((doctype, filters, kwargs))
        if doctype == "ERP Product":
            return [{"name": "P1", "product_name": "Product", "unit": "pcs", "base_unit": "pcs"}]
        self.assertEqual(doctype, "ERP Stock Movement")
        rows = self.movements
        for key, wanted in filters.items():
            if key == "location":
                # Emulate a collation returning a superset of exact location names.
                continue
            if isinstance(wanted, list):
                operation, value = wanted
                if operation == "<=":
                    rows = [row for row in rows if str(row[key]) <= value]
                else:
                    self.assertEqual(operation, "in")
                    rows = [row for row in rows if row[key] in value]
            else:
                rows = [row for row in rows if row.get(key) == wanted]
        return sorted(rows, key=lambda row: (str(row["posting_date"]), row["posting_time"], row["index"]))

    def add(self, name, day, qty, cost=None, kind="stock_in", ref=None, warehouse="W1", location=""):
        self.movements.append({"name": name, "product": "P1", "warehouse": warehouse, "location": location,
            "posting_date": date.fromisoformat(day), "posting_time": "00:00:00", "index": len(self.movements),
            "movement_type": kind, "ref_type": ref, "ref_id": name + "-source", "doc_no": name,
            "qty": qty, "qty_base": qty, "unit": "pcs", "unit_cost": cost or 0,
            "cost_known": int(cost is not None), "reason": name})

    def card(self, start="2026-09-01", end="2026-09-24", **kwargs):
        return self.report.card(product="P1", date_from=start, date_to=end, **kwargs)

    def summary(self, start="2026-09-01", end="2026-09-24", **kwargs):
        return self.report.summary(product="P1", date_from=start, date_to=end, **kwargs)

    def assert_continuous(self, card):
        qty = Decimal(str(card["opening"]["qty"]))
        value = Decimal(str(card["opening"]["value"] or 0))
        for row in card["rows"]:
            moved = Decimal(str(row["qty"]))
            qty += moved
            value += row["amount"] if moved > 0 else -row["amount"]
            self.assertEqual(Decimal(str(row["bal_qty"])), qty)
            self.assertEqual(row["bal_value"], value)
        self.assertEqual(Decimal(str(card["closing"]["qty"])), qty)
        self.assertEqual(Decimal(str(card["closing"]["value"])), value)

    def test_opening_on_first_day_is_baseline_and_not_period_receipt(self):
        self.add("opening", "2026-09-01", 10, 10, kind="opening")
        self.add("purchase", "2026-09-02", 5, 20, kind="purchase_in", ref="purchase")
        self.add("sale", "2026-09-03", -3, kind="sale_out", ref="sale")
        card, summary = self.card(), self.summary()[0]
        self.assertEqual(card["opening"], {"date": "2026-09-01", "qty": 10, "unit_cost": 10, "value": 100})
        self.assertEqual([row["name"] for row in card["rows"]], ["purchase", "sale"])
        self.assertEqual((summary["opening_qty"], summary["in_qty"], summary["out_qty"], summary["closing_qty"]),
                         (10, 5, 3, 12))
        self.assertEqual(card["closing"], {"qty": 12, "unit_cost": 13.33, "value": 160.01})
        self.assert_continuous(card)

    def test_first_initialization_inside_period_uses_the_same_baseline(self):
        self.add("opening", "2026-09-05", 10, 10, kind="opening", ref="opening")
        self.add("sale", "2026-09-06", -2, kind="sale_out")
        for start in ("1900-01-01", "2026-09-01", "2026-09-05"):
            with self.subTest(start=start):
                card, summary = self.card(start), self.summary(start)[0]
                self.assertEqual(card["opening"]["qty"], 10)
                self.assertEqual([row["name"] for row in card["rows"]], ["sale"])
                self.assertEqual(summary["in_qty"], 0)
                self.assert_continuous(card)

    def test_previous_business_movements_stay_in_period_carry(self):
        self.add("opening", "2026-09-01", 10, 10, kind="opening")
        self.add("previous-sale", "2026-09-02", -2, kind="sale_out")
        self.add("sale", "2026-09-05", -1, kind="sale_out")
        card, summary = self.card("2026-09-05"), self.summary("2026-09-05")[0]
        self.assertEqual(card["opening"], {"date": "2026-09-05", "qty": 8, "unit_cost": 10, "value": 80})
        self.assertEqual([row["name"] for row in card["rows"]], ["sale"])
        self.assertEqual((summary["opening_qty"], summary["in_qty"], summary["out_qty"]), (8, 0, 1))
        self.assert_continuous(card)

    def test_future_opening_and_revision_do_not_affect_date_limit(self):
        self.add("opening", "2026-09-01", 10, 10, kind="opening")
        self.add("sale", "2026-09-02", -2, kind="sale_out")
        self.add("revision", "2026-09-05", 7, 30, kind="adjust", ref="opening")
        self.add("future-position", "2026-09-06", 99, 99, kind="opening", location="later")
        card, summaries = self.card(end="2026-09-03"), self.summary(end="2026-09-03")
        self.assertEqual((card["opening"]["qty"], card["closing"]["qty"]), (10, 8))
        self.assertEqual(card["closing"]["value"], 80)
        self.assertEqual(len(summaries), 1)
        self.assert_continuous(card)
        self.assertEqual(self.card(end="2026-08-31")["rows"], [])
        self.assertEqual(self.card(end="2026-08-31")["opening"]["qty"], 0)

    def test_legacy_revisions_keep_real_trade_prices_and_continuous_display(self):
        self.add("opening", "2026-09-01", 10, 10, kind="opening")
        self.add("purchase", "2026-09-02", 10, 30, kind="purchase_in")
        self.add("first-sale", "2026-09-03", -5, kind="sale_out")
        self.add("positive-revision", "2026-09-04", 10, 10, kind="adjust", ref="opening")
        self.add("second-sale", "2026-09-05", -5, kind="sale_out")
        self.add("negative-revision", "2026-09-06", -2, kind="adjust", ref="opening")
        card, summary = self.card(), self.summary()[0]
        self.assertEqual([row["name"] for row in card["rows"]], ["purchase", "first-sale", "second-sale"])
        self.assertEqual([row["unit_price"] for row in card["rows"]], [Decimal(30), Decimal(20), Decimal(16)])
        self.assertEqual([row["amount"] for row in card["rows"]], [Decimal(300), Decimal(100), Decimal(80)])
        self.assertEqual((card["opening"]["qty"], card["opening"]["value"]), (18, 168))
        self.assertEqual(card["closing"], {"qty": 18, "unit_cost": 16, "value": 288})
        self.assertEqual((summary["opening_qty"], summary["in_qty"], summary["out_qty"]), (18, 10, 10))
        self.assert_continuous(card)
        self.assert_continuous(self.card(start="2026-09-03"))

    def test_positions_and_warehouses_keep_separate_weighted_costs(self):
        self.add("open-A", "2026-09-01", 10, 10, kind="opening", location="A")
        self.add("sale-A", "2026-09-02", -2, kind="sale_out", location="A")
        self.add("open-B", "2026-09-05", 5, 20, kind="opening", location="B")
        self.add("sale-B", "2026-09-06", -1, kind="sale_out", location="B")
        self.add("open-W2", "2026-09-07", 2, 70, kind="opening", warehouse="W2", location="A")
        self.add("sale-W2", "2026-09-08", -1, kind="sale_out", warehouse="W2", location="A")
        self.add("out-of-scope", "2026-09-01", 100, 100, kind="opening", warehouse="OTHER")
        card = self.card()
        self.assertEqual((card["opening"]["qty"], card["opening"]["value"]), (17, 340))
        self.assertEqual((card["closing"]["qty"], card["closing"]["value"]), (13, 230))
        self.assertEqual([(row["name"], row["bal_qty"], row["unit_price"]) for row in card["rows"]],
                         [("sale-A", 8, Decimal(10)), ("sale-B", 4, Decimal(20)), ("sale-W2", 1, Decimal(70))])
        summary = self.summary()
        self.assertEqual(len(summary), 3)
        self.assertEqual(sum(row["opening_qty"] for row in summary), 17)
        self.assertEqual(sum(row["in_qty"] for row in summary), 0)
        self.assertEqual(sum(row["closing_value"] for row in summary), card["closing"]["value"])
        self.assertEqual(self.card(end="2026-09-03")["opening"]["qty"], 10)
        self.assert_continuous(self.card(warehouse="W1", location="A"))

    def test_trailing_revision_keeps_exact_closing_cost_despite_rounding_residue(self):
        self.add("opening", "2026-09-01", 3, Decimal("10.01"), kind="opening")
        self.add("purchase", "2026-09-02", 1, 10, kind="purchase_in")
        self.add("sale", "2026-09-03", -1, kind="sale_out")
        self.add("revision", "2026-09-04", -2, kind="adjust", ref="opening")
        card = self.card()
        self.assertEqual(card["closing"], {"qty": 1, "unit_cost": 10.01, "value": 10})
        self.assertEqual(card["rows"][-1]["bal_unit_cost"], Decimal("10.01"))
        self.assert_continuous(card)

    def test_only_initialization_is_hidden_other_adjustments_and_returns_remain(self):
        self.add("old-opening-reference", "2026-09-01", 10, 10, kind="adjust", ref="opening")
        sources = (("purchase", "purchase_in", "purchase", 2, 15),
                   ("sale", "sale_out", "sale", -2, None),
                   ("return", "adjust", "sale_void", 1, None),
                   ("count", "count", "stock_count", -1, None),
                   ("in", "stock_in", "stock_entry", 1, 12),
                   ("out", "stock_out", "stock_entry", -1, None))
        for name, kind, ref, qty, cost in sources:
            self.add(name, "2026-09-02", qty, cost, kind=kind, ref=ref)
        self.assertEqual([row["name"] for row in self.card()["rows"]], [row[0] for row in sources])
        summary = self.summary()[0]
        self.assertEqual((summary["opening_qty"], summary["in_qty"], summary["out_qty"]), (10, 4, 4))
        self.assert_continuous(self.card())

    def test_selected_location_does_not_merge_case_or_accent_variants(self):
        for number, location in enumerate(("Rack-A", "rack-a", "e", "é"), 1):
            self.add("open-" + location, "2026-09-01", number * 10, number, kind="opening", location=location)
            self.add("sale-" + location, "2026-09-02", -1, kind="sale_out", location=location)
        for number, location in enumerate(("Rack-A", "rack-a", "e", "é"), 1):
            with self.subTest(location=location):
                card, summary = self.card(location=location), self.summary(location=location)
                self.assertEqual(card["opening"]["qty"], number * 10)
                self.assertEqual([row["name"] for row in card["rows"]], ["sale-" + location])
                self.assertEqual(len(summary), 1)
                self.assertEqual(summary[0]["location"], location)
                self.assert_continuous(card)

    def test_new_business_position_does_not_mask_other_opening_costs(self):
        self.add("opening-A", "2026-09-01", 10, 10, kind="opening", location="A")
        self.add("purchase-B", "2026-09-05", 5, 20, kind="purchase_in", location="B")
        card = self.card()
        self.assertEqual(card["opening"], {"date": "2026-09-01", "qty": 10, "unit_cost": 10, "value": 100})
        self.assertEqual((card["closing"]["qty"], card["closing"]["value"]), (15, 200))

    def test_cost_masking_and_unknown_cost_still_hold(self):
        self.add("opening", "2026-09-01", 10, 10, kind="opening")
        self.add("sale", "2026-09-02", -1, kind="sale_out")
        self.cost_visible = False
        card = self.card()
        self.assertEqual(card["opening"]["qty"], 10)
        self.assertIsNone(card["opening"]["value"])
        self.assertIsNone(card["opening"]["unit_cost"])
        for key in ("unit_price", "amount", "bal_value", "bal_unit_cost"):
            self.assertIsNone(card["rows"][0][key])
        self.assertIsNone(self.summary()[0]["closing_value"])
        self.assertIsNone(card["closing"]["value"])
        self.assertTrue(all(capability == "report.read" for capability in self.required))
        self.movements.clear()
        self.cost_visible = True
        self.add("zero-opening", "2026-09-01", 0, kind="opening")
        self.add("unknown-out", "2026-09-02", -2, kind="stock_out")
        card = self.card()
        self.assertIsNone(card["opening"]["value"])
        self.assertIsNone(card["rows"][0]["amount"])
        self.assertIsNone(card["closing"]["unit_cost"])
        self.assertEqual(card["closing"]["qty"], -2)

    def test_no_initialization_does_not_rederive_rounded_running_unit_cost(self):
        self.add("receive", "2026-09-01", 3, Decimal("33.333333"))
        self.add("issue", "2026-09-02", -2, kind="stock_out")
        card = self.card()
        self.assertEqual(card["rows"][-1]["bal_value"], Decimal("33.34"))
        self.assertEqual(card["rows"][-1]["bal_unit_cost"], Decimal("33.33"))
        self.assertEqual(card["closing"]["unit_cost"], 33.33)
        self.assert_continuous(card)


if __name__ == "__main__":
    unittest.main()
