"""Execute opening service against a transactional fake; no Site or external IO."""
import ast
from copy import deepcopy
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/inventory"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Row(dict):
    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    __setattr__ = dict.__setitem__


class OpeningBatch(unittest.TestCase):
    def setUp(self):
        self.state = {"openings": [], "movements": [], "balances": []}
        self.products = [Row(name=name, product_name=name + " name", code="code-" + name,
                             barcode="00" + name, base_unit="pcs", unit="pcs", is_active=1) for name in ("P1", "P2")]
        self.allowed = {"inventory.read", "stock.in", "report.write"}
        self.queries, self.calls, self.snapshots = [], [], {}
        self.before_lock = lambda: None
        self.fail_product = None
        self.old_snapshot = None
        self.saved_modules = patch.dict(sys.modules)
        self.saved_modules.start()
        self.addCleanup(self.saved_modules.stop)
        contracts = load("opening_test_contracts", DOMAIN / "contracts.py")
        workbook = load("opening_test_workbook", DOMAIN / "opening_workbook.py")
        self.contracts = contracts
        frappe = ModuleType("frappe")
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe._ = lambda value: value
        frappe.throw = self.throw
        frappe.conf = {"encryption_key": "synthetic-local-test-key"}
        frappe.local = SimpleNamespace(site="opening.test")
        frappe.session = SimpleNamespace(user="opening@example.test")
        frappe.generate_hash = lambda length: "a" * length
        frappe.get_doc = lambda values: Row(values)
        frappe.get_all = lambda *args, **kwargs: self.products
        frappe.parse_json = __import__("json").loads
        frappe.db = SimpleNamespace(sql=self.sql, savepoint=self.savepoint, rollback=self.rollback)
        utils = ModuleType("frappe.utils")
        utils.nowdate = lambda: "2026-09-24"
        core = ModuleType("pearnly_core")
        core.browser_text = SimpleNamespace(label=lambda value: value, all_languages=lambda key: {"en": key})
        access = ModuleType("pearnly_erp_portal.access")
        access.guard = SimpleNamespace(require=self.require)
        inventory = ModuleType("pearnly_erp_portal.inventory")
        inventory.contracts = contracts
        inventory.opening_workbook = workbook
        inventory.access = SimpleNamespace(write_opening=self.write_opening)
        inventory.stock = SimpleNamespace(lock_products=self.lock_products, apply_movement=self.apply_movement)
        inventory.warehouses = SimpleNamespace(required=self.warehouse)
        masters = ModuleType("pearnly_erp_portal.masters")
        masters.company = SimpleNamespace(active_company=lambda: "C1")
        modules = {"frappe": frappe, "frappe.utils": utils, "pearnly_core": core,
                   "pearnly_erp_portal.access": access, "pearnly_erp_portal.inventory": inventory,
                   "pearnly_erp_portal.masters": masters}
        sys.modules.update(modules)
        self.service = load("opening_test_service", DOMAIN / "opening_batch.py")
        self.args = {"warehouse": "W1", "as_of_date": "2026-09-01", "note": "opening test",
                     "rows": [{"product": "P1", "qty": "4", "unit_cost": "10"}]}

    @staticmethod
    def throw(message, *args):
        raise ValueError(message)

    def require(self, capability):
        self.calls.append(("require", capability))
        if capability not in self.allowed:
            raise PermissionError(capability)

    def warehouse(self, value, company=None):
        if value != "W1" or company != "C1":
            raise ValueError("Warehouse not found")
        return value

    def sql(self, query, parameters=None, as_dict=False):
        self.queries.append(query)
        if "tabERP Product" in query:
            if "names" in parameters:
                return [p for p in self.products if p.name in parameters["names"]]
            return [p for p in self.products if any(p.get(column) in parameters[field] for field, column in
                    (("product", "name"), ("product_code", "code"), ("barcode", "barcode")))]
        if "tabERP Warehouse" in query:
            return [Row(name="W1", company="C1", is_active=1)] if parameters[0] == "W1" else []
        state = self.state if "for update" in query or self.old_snapshot is None else self.old_snapshot
        table = next(key for key, text in (("openings", "Opening"), ("movements", "Movement"), ("balances", "Balance")) if "tabERP Stock " + text in query)
        return [row for row in state[table] if (row.product, row.warehouse, row.location or "") == tuple(parameters)]

    def lock_products(self, products):
        self.calls.append(("lock", sorted(set(products))))
        self.before_lock()

    def savepoint(self, name):
        self.snapshots[name] = deepcopy(self.state)

    def rollback(self, save_point):
        self.state = self.snapshots[save_point]
        self.calls.append(("rollback", save_point))

    def write_opening(self, doc, method):
        self.assertEqual(method, "insert")
        doc.name = self.contracts.position_name("OPN", doc.product, doc.warehouse, doc.location)
        self.state["openings"].append(doc)

    def apply_movement(self, **values):
        if values["product"] == self.fail_product:
            raise RuntimeError("simulated movement failure")
        row = Row(values)
        self.state["movements"].append(row)
        self.state["balances"].append(Row(product=row.product, warehouse=row.warehouse, location=row.location,
            qty_on_hand=row.qty, value=row.qty * row.unit_cost, cost_known=bool(row.qty)))

    def preview(self, **changes):
        return self.service.preview(**{**self.args, **changes})

    def submit(self, review=None, **changes):
        return self.service.submit(**{**self.args, **changes}, review_token=(review or self.preview(**changes))["review_token"])

    def test_preview_is_read_only_and_success_creates_one_voucher_and_movement(self):
        review = self.preview()
        self.assertTrue(review["valid"])
        self.assertEqual((review["rows"][0]["status"], review["total_value"]), ("ready", 40))
        self.assertEqual(self.state, {"openings": [], "movements": [], "balances": []})
        self.assertFalse(any("for update" in query for query in self.queries))
        self.assertEqual(self.submit(review), {"created": 1, "skipped": 0, "openings": ["OPN-P1-W1"]})
        self.assertEqual((len(self.state["openings"]), len(self.state["movements"])), (1, 1))
        self.assertEqual(self.state["movements"][0].unit_cost, Decimal("10.00"))
        for name in ("Product", "Stock Opening", "Stock Movement", "Stock Balance", "Warehouse"):
            self.assertTrue(any("tabERP " + name in query and "for update" in query for query in self.queries), name)

    def test_exact_replay_after_sale_keeps_existing_balance_and_note(self):
        review = self.preview()
        self.submit(review)
        self.state["movements"].append(Row(product="P1", warehouse="W1", location="", qty=-2, movement_type="sale_out"))
        self.state["balances"][0].qty_on_hand = 2
        self.state["balances"][0].value = 20
        before = deepcopy(self.state)
        result = self.submit(review, rows=review["rows"])
        self.assertEqual((result["created"], result["skipped"]), (0, 1))
        self.assertEqual(self.state, before)

    def test_reinitialization_changes_and_previous_zero_are_rejected(self):
        self.args["rows"][0].update(qty="0", unit_cost="0")
        self.submit()
        self.assertEqual(len(self.state["movements"]), 1)
        for row, date_value in (({"qty": "1", "unit_cost": "0"}, "2026-09-01"),
                                ({"qty": "0", "unit_cost": "1"}, "2026-09-01"),
                                ({"qty": "0", "unit_cost": "0"}, "2026-09-02")):
            review = self.preview(rows=[{"product": "P1", **row}], as_of_date=date_value)
            self.assertFalse(review["valid"])
            self.assertIn("already initialized", review["rows"][0]["error"])

    def test_required_and_precision_rules_do_not_turn_blanks_into_zero(self):
        for field, bad in (("qty", ""), ("qty", None), ("unit_cost", ""), ("unit_cost", None),
                           ("qty", "-1"), ("unit_cost", "NaN"), ("qty", "Infinity"),
                           ("qty", True), ("qty", "0.0000001"), ("unit_cost", "1.001"),
                           ("qty", "999999999999.000001")):
            with self.subTest(field=field, bad=bad):
                row = {**self.args["rows"][0], field: bad}
                review = self.preview(rows=[row])
                self.assertFalse(review["valid"])
                self.assertIsNone(review["review_token"])
        self.assertTrue(self.preview(rows=[{"product": "P1", "qty": 0, "unit_cost": 0}])["valid"])

    def test_strict_date_and_active_company_warehouse(self):
        for value in ("2026-9-01", "2026-02-30", "2026-09-25", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.preview(as_of_date=value)
        with self.assertRaisesRegex(ValueError, "Warehouse"):
            self.preview(warehouse="W2")

    def test_identifier_conflict_unknown_disabled_unit_and_duplicate_positions(self):
        for extra in ({"product": "missing"}, {"product_code": "code-P2"}, {"barcode": "bad"}, {"unit": "box"}):
            row = {**self.args["rows"][0], **extra, "row_number": 7}
            review = self.preview(rows=[row])
            self.assertFalse(review["valid"])
            self.assertEqual(review["rows"][0]["row_number"], 7)
            self.assertEqual(review["rows"][0]["unit"], row.get("unit"))
        self.products[0].is_active = 0
        self.assertFalse(self.preview()["valid"])
        self.products[0].is_active = 1
        duplicate = self.preview(rows=[self.args["rows"][0], {"product_code": "code-P1", "qty": 4, "unit_cost": 10}])
        self.assertEqual([row["status"] for row in duplicate["rows"]], ["error", "error"])
        distinct = self.preview(rows=[self.args["rows"][0], {**self.args["rows"][0], "location": "A"}])
        self.assertTrue(distinct["valid"])

    def test_code_and_barcode_resolve_and_token_survives_numeric_normalization(self):
        self.args["rows"] = [{"product_code": "code-P1", "barcode": "00P1", "qty": "4.000000", "unit_cost": "10.00"}]
        review = self.preview()
        self.assertEqual(review["rows"][0]["product"], "P1")
        self.assertEqual(self.submit(review, rows=[{**review["rows"][0], "qty": 4.0, "unit_cost": 10.0}])["created"], 1)

    def test_location_queries_use_exact_bytes_like_position_identity(self):
        rows = [{**self.args["rows"][0], "location": location} for location in ("Rack-A", "rack-a", "e", "é")]
        review = self.preview(rows=rows)
        self.assertTrue(review["valid"])
        self.assertEqual(self.submit(review, rows=rows)["created"], 4)
        self.assertEqual(self.submit(review, rows=rows)["skipped"], 4)
        location_queries = [query for query in self.queries if "coalesce(location" in query]
        self.assertTrue(location_queries)
        self.assertTrue(all("binary coalesce(location, '')=binary %s" in query for query in location_queries))

    def test_numeric_zero_location_is_not_the_unnamed_position(self):
        review = self.preview(rows=[{**self.args["rows"][0], "location": 0}, self.args["rows"][0]])
        self.assertTrue(review["valid"])
        self.assertEqual([row["location"] for row in review["rows"]], ["0", ""])

    def test_all_new_paths_require_both_capabilities_and_legacy_stays_report_write(self):
        for allowed in ({"inventory.read"}, {"stock.in"}, {"report.write"}, set()):
            self.allowed = allowed
            for method, values in ((self.service.preview, self.args),
                (self.service.submit, {**self.args, "review_token": "bad"}),
                (self.service.template, {"warehouse": "W1"}),
                (self.service.read_file, {"file_url": "/private/files/test.xlsx"})):
                with self.subTest(allowed=allowed, method=method.__name__), self.assertRaises(PermissionError):
                    method(**values)
        self.allowed = {"report.write"}
        self.assertEqual(self.service.upsert_one(product="P1", warehouse="W1", as_of_date="2026-09-01", qty=4, unit_cost=10)["qty"], 4)

    def test_any_invalid_row_prevents_all_writes_and_any_write_failure_rolls_back(self):
        self.args["rows"].append({"product": "P2", "qty": 1, "unit_cost": ""})
        with self.assertRaises(ValueError):
            self.submit()
        self.assertFalse(self.state["openings"])
        self.args["rows"][1]["unit_cost"] = 2
        self.fail_product = "P2"
        with self.assertRaisesRegex(RuntimeError, "movement failure"):
            self.submit()
        self.assertEqual(self.state, {"openings": [], "movements": [], "balances": []})
        self.assertTrue(any(call[0] == "rollback" for call in self.calls))

    def test_current_read_catches_concurrent_movement_behind_old_snapshot(self):
        review = self.preview()
        self.old_snapshot = deepcopy(self.state)
        self.before_lock = lambda: self.state["movements"].append(Row(product="P1", warehouse="W1", location="", qty=1))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.submit(review)
        self.assertFalse(self.state["openings"])

    def test_concurrent_identical_opening_is_skipped_under_current_read(self):
        review = self.preview()
        self.old_snapshot = deepcopy(self.state)
        self.before_lock = lambda: self.state["openings"].append(Row(product="P1", warehouse="W1", location="", name="OPN-P1-W1",
                as_of_date=date(2026, 9, 1), qty=4, posted_qty=4, unit_cost=10))
        self.assertEqual(self.submit(review)["skipped"], 1)
        self.assertFalse(self.state["movements"])

    def test_unit_change_between_validation_and_lock_requires_another_review(self):
        review = self.preview()
        self.before_lock = lambda: self.products[0].update(base_unit="box")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.submit(review)
        self.assertFalse(self.state["openings"])

    def test_review_token_rejects_changed_input_or_identity(self):
        review = self.preview()
        for changes in ({"rows": [{"product": "P1", "qty": 5, "unit_cost": 10}]}, {"note": "changed"}):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.submit(review, **changes)
        self.service.frappe.session.user = "other@example.test"
        with self.assertRaisesRegex(ValueError, "changed"):
            self.submit(review)
        self.assertFalse(self.state["openings"])

    def transaction_rpc(self):
        tree = ast.parse((DOMAIN / "api.py").read_text())
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "_opening_transaction")
        namespace = {"frappe": self.service.frappe}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(DOMAIN / "api.py"), "exec"), namespace)
        return namespace["_opening_transaction"]

    def test_rpc_restarts_snapshot_conflict_but_never_prior_caller_writes(self):
        error_type = self.service.frappe.QueryDeadlockError
        for previous_writes in (0, 1, None):
            attempts, rollbacks = [], []
            self.service.frappe.db.transaction_writes = previous_writes
            self.service.frappe.db.rollback = lambda: rollbacks.append(True)
            def operation():
                attempts.append(True)
                if len(attempts) == 1:
                    raise error_type("snapshot conflict")
                return {"created": 0, "skipped": 1}
            if previous_writes == 0:
                self.assertEqual(self.transaction_rpc()(operation)["skipped"], 1)
                self.assertEqual((len(attempts), len(rollbacks)), (2, 1))
            else:
                with self.assertRaises(error_type):
                    self.transaction_rpc()(operation)
                self.assertEqual((len(attempts), len(rollbacks)), (1, 0))

    def test_rpc_conflicts_are_bounded_and_service_savepoint_preserves_snapshot_error(self):
        error_type = self.service.frappe.QueryDeadlockError
        self.service.frappe.db.transaction_writes = 0
        retries = []
        self.service.frappe.db.rollback = lambda: retries.append(True)
        def conflict():
            raise error_type("snapshot conflict")
        with self.assertRaises(error_type):
            self.transaction_rpc()(conflict)
        self.assertEqual(len(retries), 3)
        self.service.frappe.db.rollback = self.rollback
        self.service.stock.apply_movement = lambda **kwargs: conflict()
        with self.assertRaises(error_type):
            self.submit()
        self.assertFalse(self.state["openings"])

    def test_file_permission_and_remote_url_are_checked_before_reading(self):
        with self.assertRaisesRegex(ValueError, "local"):
            self.service.read_file("https://example.test/file.xlsx")
        self.service.frappe.db.get_value = lambda *args, **kwargs: "FILE1"
        reads = []
        doc = SimpleNamespace(file_name="test.xlsx", file_size=1, check_permission=lambda action: self.throw("file denied"),
                              get_content=lambda: reads.append(True))
        self.service.frappe.get_doc = lambda *args: doc
        with self.assertRaisesRegex(ValueError, "file denied"):
            self.service.read_file("/private/files/test.xlsx")
        self.assertFalse(reads)


if __name__ == "__main__":
    unittest.main()
