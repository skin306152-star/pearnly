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
        frappe.clear_messages = lambda: None
        # 已登记的台（按公司 + 主号）：期初表里的号不能重
        self.registered_keys, self.registered = set(), []
        frappe.db = SimpleNamespace(sql=self.sql, savepoint=self.savepoint, rollback=self.rollback,
                                    exists=lambda doctype, filters: filters.get("serial_key") in self.registered_keys)
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
        inventory.warehouses = SimpleNamespace(required=self.warehouse, remember=lambda kind, warehouse, company=None: None)
        # 库位清单（`positions.check`）：W3 建了清单只有 A-01，其余仓不限
        inventory.positions = SimpleNamespace(check=self.check_location)
        inventory.serials = SimpleNamespace(
            DOCTYPE="ERP Serial No", OPENING_REF="serial_opening", MAX_NO=40, MAX_COLOR=60,
            DUPLICATE="This unit number is already registered", QTY_MISMATCH="The number of units does not match the quantity",
            clean=lambda value, limit: str(value or "").strip()[:limit], key=lambda company, number: f"{company}:{number}",
            register=lambda **values: self.registered.append(values))
        masters = ModuleType("pearnly_erp_portal.masters")
        # 公司类型：默认普通公司；按台的用例把 C1 改成汽车公司
        self.unit_company = False
        masters.company = SimpleNamespace(active_company=lambda: "C1", unit_tracking=lambda company: self.unit_company,
                                          company_type=lambda company: "vehicle" if self.unit_company else "general")
        # 接了 Express 的公司不在 ERP 录期初（出入库第一期）：默认没接
        self.express_linked = set()
        express = ModuleType("pearnly_erp_portal.express")
        express.push = SimpleNamespace(linked=lambda company: company in self.express_linked)
        frappe.PermissionError = PermissionError
        # 商品的码与车架号 / 发动机号同一个命名空间：已被占用的码
        self.taken_codes = set()
        products = ModuleType("pearnly_erp_portal.products")
        products.codes = SimpleNamespace(assert_free=self.assert_free)
        modules = {"frappe": frappe, "frappe.utils": utils, "pearnly_core": core, "pearnly_erp_portal.products": products,
                   "pearnly_erp_portal": ModuleType("pearnly_erp_portal"),
                   "pearnly_erp_portal.access": access, "pearnly_erp_portal.inventory": inventory,
                   "pearnly_erp_portal.masters": masters, "pearnly_erp_portal.express": express}
        sys.modules.update(modules)
        self.service = load("opening_test_service", DOMAIN / "opening_batch.py")
        self.args = {"warehouse": "W1", "as_of_date": "2026-09-01", "note": "opening test",
                     "rows": [{"product": "P1", "qty": "4", "unit_cost": "10"}]}

    def assert_free(self, value, owner=None):
        if value in self.taken_codes:
            raise ValueError("That code is used by another product")

    @staticmethod
    def throw(message, *args):
        raise ValueError(message)

    def require(self, capability):
        self.calls.append(("require", capability))
        if capability not in self.allowed:
            raise PermissionError(capability)

    def warehouse(self, value, company=None):
        # 本公司启用的仓：W1、W3（按名称「Shop」也认 W3）；W2 / X1 是别家的或停用的，认不到
        resolved = {"W1": "W1", "W3": "W3", "Shop": "W3"}.get(value)
        if not resolved or company != "C1":
            raise ValueError("Warehouse not found")
        return resolved

    @staticmethod
    def check_location(warehouse, location):
        location = str(location or "").strip()
        if warehouse == "W3" and location and location != "A-01":
            raise RuntimeError("This location is not in the location list of the warehouse: " + location)
        return location

    def sql(self, query, parameters=None, as_dict=False):
        self.queries.append(query)
        if "tabERP Product" in query:
            if "names" in parameters:
                return [p for p in self.products if p.name in parameters["names"]]
            return [p for p in self.products if any(p.get(column) in parameters.get(field, ()) for field, column in
                    (("product", "name"), ("product_code", "code"), ("product_code", "internal_code"),
                     ("product_code", "express_code"), ("internal_code", "internal_code"), ("internal_code", "express_code"),
                     ("barcode", "barcode")))]
        if "tabERP Warehouse" in query:
            return [Row(name=parameters[0], company="C1", is_active=1)] if parameters[0] in ("W1", "W3") else []
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

    def test_a_company_linked_to_express_takes_its_opening_from_express(self):
        # 出入库第一期（10-25）：期初就是 Express 的（同步按各仓结存对齐），ERP 里预览、提交、旧接口都拒，什么都不写
        self.express_linked.add("C1")
        for call in (lambda: self.service.preview(**self.args),
                     lambda: self.service.upsert_one(product="P1", warehouse="W1", as_of_date="2026-09-01", qty="4")):
            with self.assertRaisesRegex(ValueError, "Opening stock is read from Express"):
                call()
        self.assertEqual((self.state["openings"], self.state["movements"]), ([], []))

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

    def test_a_unit_company_opens_tracked_products_one_unit_per_row(self):
        """期初表并入按台（岳父 10-25）：按台商品一台一行、数量 1；同一位置的几台合成一笔期初，提交时逐台登记。"""
        self.unit_company = True
        self.products[0].track_serial = 1
        car = {"product": "P1", "qty": "1", "unit_cost": "500000"}
        rows = [{**car, "serial_no": " vin-a ", "secondary_no": "E1", "color": "红"}, {**car, "serial_no": "VIN-B"},
                {"product": "P2", "qty": "4", "unit_cost": "10", "serial_no": "ignored"}]
        review = self.preview(rows=rows)
        self.assertTrue(review["valid"], review["rows"])
        self.assertEqual([row["serial_no"] for row in review["rows"]], ["VIN-A", "VIN-B", ""])  # 不按台的商品号码不看
        self.assertEqual(review["total_value"], 1000040)
        result = self.submit(review, rows=rows)
        self.assertEqual(result, {"created": 2, "skipped": 0, "openings": ["OPN-P1-W1", "OPN-P2-W1"]})
        self.assertEqual([row.qty for row in self.state["openings"]], [Decimal(2), Decimal(4)])
        self.assertEqual([row.qty for row in self.state["movements"]], [Decimal(2), Decimal(4)])
        self.assertEqual([(unit["entry"]["serial_no"], unit["ref_id"], unit["action"]) for unit in self.registered],
                         [("VIN-A", "OPN-P1-W1", "opening"), ("VIN-B", "OPN-P1-W1", "opening")])
        self.assertEqual(self.registered[0]["entry"]["color"], "红")

    def test_unit_rows_need_a_new_number_one_each_and_one_cost_per_place(self):
        self.unit_company = True
        self.products[0].track_serial = 1
        car = {"product": "P1", "qty": "1", "unit_cost": "500000"}
        cases = (([{**car}], ["error"]),  # 没写号
                 ([{**car, "serial_no": "V1"}, {**car, "serial_no": "v1"}], ["error", "error"]),  # 表里重号
                 ([{**car, "serial_no": "V1", "qty": "2"}], ["error"]),  # 一台一行
                 ([{**car, "serial_no": "V1"}, {**car, "serial_no": "V2", "unit_cost": "1"}], ["error", "error"]),  # 同一位置成本不一
                 ([{**car, "serial_no": "V1"}, {**car, "serial_no": "V2", "location": "A"}], ["ready", "ready"]))  # 不同库位各一笔
        for rows, statuses in cases:
            with self.subTest(rows=rows):
                self.assertEqual([row["status"] for row in self.preview(rows=rows)["rows"]], statuses)
        self.taken_codes.add("E-USED")  # 发动机号撞到已有的码：预览就报在这一行
        self.assertEqual(self.preview(rows=[{**car, "serial_no": "V8", "secondary_no": "E-USED"}])["rows"][0]["error"],
                         "That code is used by another product")
        self.registered_keys.add("C1:V9")
        self.assertEqual(self.preview(rows=[{**car, "serial_no": "V9"}])["rows"][0]["error"], "This unit number is already registered")
        self.unit_company = False  # 普通公司：同一件商品照普通行（号码不看）
        self.assertTrue(self.preview(rows=[{**car, "qty": "3", "serial_no": "V9"}])["valid"])

    def test_code_and_barcode_resolve_and_token_survives_numeric_normalization(self):
        self.args["rows"] = [{"product_code": "code-P1", "barcode": "00P1", "qty": "4.000000", "unit_cost": "10.00"}]
        review = self.preview()
        self.assertEqual(review["rows"][0]["product"], "P1")
        self.assertEqual(self.submit(review, rows=[{**review["rows"][0], "qty": 4.0, "unit_cost": 10.0}])["created"], 1)

    def test_the_code_column_also_takes_the_internal_code(self):
        # 编码一列认系统编码，也认内部编码（主会话 10-26）；系统编码对上的优先，不与别件的内部编码算歧义
        self.products[0].internal_code = "A-01"
        self.products[1].internal_code = "code-P1"
        self.args["rows"] = [{"product_code": "A-01", "qty": "4", "unit_cost": "10"}]
        self.assertEqual(self.preview()["rows"][0]["product"], "P1")
        self.args["rows"] = [{"product_code": "code-P1", "qty": "4", "unit_cost": "10"}]
        self.assertEqual(self.preview()["rows"][0]["product"], "P1")

    def test_the_internal_code_column_matches_first(self):
        # 内部编码单独一列（岳父 10-25）：先认它（含 Express 编码）；与编码一列指向不同商品算冲突
        self.products[0].internal_code = "A-01"
        self.args["rows"] = [{"internal_code": "A-01", "qty": "4", "unit_cost": "10"}]
        self.assertEqual(self.preview()["rows"][0]["product"], "P1")
        self.args["rows"] = [{"internal_code": "A-01", "product_code": "code-P1", "qty": "4", "unit_cost": "10"}]
        self.assertEqual(self.preview()["rows"][0]["product"], "P1")
        self.args["rows"] = [{"internal_code": "A-01", "product_code": self.products[1].code, "qty": "4", "unit_cost": "10"}]
        self.assertTrue(self.preview()["rows"][0].get("error"))

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

    def test_each_row_can_name_its_own_warehouse_and_the_preview_checks_its_locations(self):
        """多仓闭环 P1（G5）：期初表加仓库列，空着 = 表头的仓，写了按名称或编号在本公司里认；同一件商品不同仓各算一个位置；
        库位按这一行的仓的清单在预览就核（原来提交才整批失败）；别家 / 认不到的仓记成这一行的错。"""
        rows = [{"product": "P1", "qty": "4", "unit_cost": "10"},
                {"product": "P1", "warehouse": "Shop", "location": "A-01", "qty": "2", "unit_cost": "10"},
                {"product": "P1", "warehouse": "W3", "location": "B-09", "qty": "1", "unit_cost": "10"},
                {"product": "P1", "warehouse": "X1", "qty": "1", "unit_cost": "10"}]
        result = self.preview(rows=rows)
        self.assertEqual([(row["warehouse"] if row["status"] != "error" else None, row["status"]) for row in result["rows"]],
                         [("W1", "ready"), ("W3", "ready"), (None, "error"), (None, "error")])
        self.assertIn("location list", result["rows"][2]["error"])
        self.assertIn("Warehouse not found", result["rows"][3]["error"])
        self.assertFalse(result["valid"])
        done = self.submit(rows=rows[:2])
        self.assertEqual(done["created"], 2)
        self.assertEqual(sorted((row.warehouse, row.location or "") for row in self.state["openings"]), [("W1", ""), ("W3", "A-01")])

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
