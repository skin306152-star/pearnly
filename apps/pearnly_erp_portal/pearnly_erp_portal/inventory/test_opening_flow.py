"""Opening-stock integration on the synthetic ERP lab only; no external writes."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.utils import nowdate

from pearnly_erp_portal.access import menu
from pearnly_erp_portal.access.setup import sync_roles
from pearnly_erp_portal.inventory import api, contracts, opening_batch, opening_workbook, openings, report, stock
from pearnly_erp_portal.masters import company
from pearnly_erp_portal.products import service as products


class OpeningFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert frappe.conf.pearnly_erp_pos_lab and frappe.conf.db_host == "db"
        from pearnly_core.provisioning import ensure_feature_roles
        from pearnly_control.features import codes_of, roles_payload
        frappe.set_user("Administrator")
        cls.features = codes_of("erp")
        frappe.conf.pearnly_features = cls.features
        ensure_feature_roles(json.dumps(roles_payload("erp", cls.features)), admin_user="Administrator")
        sync_roles()
        cls.prefix = "openingflow" + uuid4().hex[:9]
        cls.a = company.create_company(company_name=cls.prefix + " A", pos_enabled=False)
        cls.b = company.create_company(company_name=cls.prefix + " B", pos_enabled=False)
        company.set_active_company(cls.a["name"])
        cls.product = products.create_product(product_name=cls.prefix, code=cls.prefix,
            barcode=cls.prefix + "-barcode", unit="pcs", default_cost=99, unit_price=150)["name"]
        cls.other = products.create_product(product_name=cls.prefix + " other", code=cls.prefix + "-other",
            unit="pcs", default_cost=20, unit_price=30)["name"]
        cls.users = {}
        for key, grants in (("read", ["inventory"]), ("in", ["stock-in"]), ("both", ["inventory", "stock-in"])):
            user = cls.prefix + key + "@example.test"
            frappe.get_doc({"doctype": "User", "email": user, "first_name": cls.prefix + key,
                "send_welcome_email": 0, "roles": [{"role": "Desk User"}] + [{"role": menu.role(grant)} for grant in grants]}).insert()
            frappe.get_doc({"doctype": "User Permission", "user": user, "allow": "ERP Company",
                "for_value": cls.a["name"], "apply_to_all_doctypes": 1}).insert()
            cls.users[key] = user
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.conf.pearnly_features = self.features
        company.set_active_company(self.a["name"])
        # 每个用例一个自己的仓（库存只到「物料 × 仓库」，11-19 起不再按仓位隔开用例）
        from pearnly_erp_portal.inventory import warehouses

        self.place = frappe.db.get_value("ERP Warehouse", {"company": self.a["name"],
                                          "warehouse_name": self.prefix + " " + self._testMethodName}, "name") \
            or warehouses.create_warehouse(warehouse_name=self.prefix + " " + self._testMethodName,
                                           company=self.a["name"])["name"]

    def tearDown(self):
        frappe.db.rollback()
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def payload(self, **changes):
        return {"warehouse": self.place, "as_of_date": nowdate(),
            "rows": [{"product": self.product, "qty": "1.123456", "unit_cost": "17.23"}], **changes}

    def post(self, payload):
        preview = api.api_preview_stock_opening(**payload)
        self.assertTrue(preview["valid"], preview)
        return api.api_submit_stock_opening(**payload, review_token=preview["review_token"])

    def qty(self, product=None):
        return stock.current_balance(product or self.product, self.place).qty

    def test_precision_value_balance_report_rebuild_and_replay_after_issue(self):
        payload = self.payload()
        initial = api.api_preview_stock_opening(**payload)
        self.assertEqual(initial["total_value"], 19.36)
        self.assertEqual(self.qty(), 0)
        first = self.post(payload)
        self.assertEqual(first["created"], 1)
        balance = stock.current_balance(self.product, self.place)
        self.assertEqual(balance.qty, Decimal("1.123456"))
        self.assertEqual(balance.value, Decimal("19.36"))
        card = report.card(product=self.product, warehouse=self.place,
                           date_from=nowdate(), date_to=nowdate())
        self.assertEqual(card["closing"]["value"], float(balance.value))
        self.assertEqual(card["closing"]["qty"], float(balance.qty))
        self.assertEqual(card["opening"]["qty"], float(balance.qty))
        self.assertEqual(card["opening"]["value"], float(balance.value))
        self.assertEqual(card["rows"], [])
        summary = report.summary(product=self.product, warehouse=self.place,
                                 date_from=nowdate(), date_to=nowdate())[0]
        self.assertEqual(summary["in_qty"], 0)
        self.assertEqual(summary["opening_qty"], float(balance.qty))
        rebuilt = stock.rebuild_balance(self.product, self.place)
        self.assertEqual(rebuilt["qty_on_hand"], float(balance.qty))
        self.assertEqual(stock.current_balance(self.product, self.place).value, balance.value)
        stock.apply_movement(product=self.product, warehouse=self.place,
                             movement_type="stock_out", qty="0.123456", ref_type="test", internal=True)
        repeat = api.api_submit_stock_opening(**payload, review_token=initial["review_token"])
        self.assertEqual((repeat["created"], repeat["skipped"], self.qty()), (0, 1, Decimal(1)))
        card = report.card(product=self.product, warehouse=self.place,
                           date_from=nowdate(), date_to=nowdate())
        self.assertEqual(len(card["rows"]), 1)
        summary = report.summary(product=self.product, warehouse=self.place,
                                 date_from=nowdate(), date_to=nowdate())[0]
        self.assertEqual(summary["in_qty"], 0)
        self.assertEqual(summary["out_qty"], 0.123456)
        self.assertEqual(card["closing"]["qty"], 1)
        self.assertEqual(frappe.db.count("ERP Stock Movement", {"ref_type": "opening", "ref_id": first["openings"][0]}), 1)
        with self.assertRaises(frappe.ValidationError):
            openings.upsert_opening(product=self.product, warehouse=self.place,
                as_of_date=nowdate(), qty="1.123456", unit_cost=19)

    def test_zero_cost_zero_quantity_and_missing_cost_are_distinct(self):
        payload = self.payload(rows=[{"product": self.product, "qty": 0, "unit_cost": 0}])
        result = self.post(payload)
        self.assertEqual((result["created"], self.qty()), (1, 0))
        self.assertEqual(self.post(payload)["skipped"], 1)
        payload["rows"][0]["qty"] = 1
        self.assertFalse(api.api_preview_stock_opening(**payload)["valid"])
        payload["rows"][0].update(product=self.other, unit_cost="")
        self.assertFalse(api.api_preview_stock_opening(**payload)["valid"])
        payload["rows"][0]["unit_cost"] = 0
        self.post(payload)
        self.assertEqual(stock.current_balance(self.other, self.place).value, Decimal(0))

    def test_batch_write_failure_rolls_back_every_position(self):
        payload = self.payload(rows=[{"product": product, "qty": 3, "unit_cost": 7} for product in (self.product, self.other)])
        review = api.api_preview_stock_opening(**payload)
        original = stock.apply_movement
        def fail_second(**kwargs):
            if kwargs["product"] == self.other:
                raise RuntimeError("synthetic final-row failure")
            return original(**kwargs)
        with patch.object(stock, "apply_movement", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "synthetic final-row"):
                api.api_submit_stock_opening(**payload, review_token=review["review_token"])
        for product in (self.product, self.other):
            self.assertEqual(self.qty(product), 0)
            self.assertFalse(frappe.db.exists("ERP Stock Opening", {"product": product, "warehouse": self.place}))
            self.assertFalse(frappe.db.exists("ERP Stock Movement", {"product": product, "warehouse": self.place}))

    def test_stale_review_and_history_with_zero_balance_do_not_initialize(self):
        payload = self.payload()
        preview = api.api_preview_stock_opening(**payload)
        for movement, qty in (("stock_in", 2), ("stock_out", 2)):
            stock.apply_movement(product=self.product, warehouse=self.place,
                                 movement_type=movement, qty=qty, unit_cost=10, internal=True)
        self.assertEqual(self.qty(), 0)
        with self.assertRaises(frappe.ValidationError):
            api.api_submit_stock_opening(**payload, review_token=preview["review_token"])
        self.assertFalse(frappe.db.exists("ERP Stock Opening", {"product": self.product, "warehouse": self.place}))

    def test_authorization_company_scope_and_token_identity(self):
        payload = self.payload()
        owner_review = api.api_preview_stock_opening(**payload)
        for key in ("read", "in"):
            frappe.set_user(self.users[key])
            with self.assertRaises(frappe.PermissionError):
                api.api_preview_stock_opening(**payload)
        frappe.set_user(self.users["both"])
        review = api.api_preview_stock_opening(**payload)
        self.assertTrue(review["valid"])
        with self.assertRaises(frappe.ValidationError):
            api.api_submit_stock_opening(**payload, review_token=owner_review["review_token"])
        with self.assertRaises(frappe.ValidationError):
            api.api_preview_stock_opening(**{**payload, "warehouse": self.b["warehouse"]})
        result = api.api_submit_stock_opening(**payload, review_token=review["review_token"])
        self.assertEqual(result["created"], 1)
        frappe.set_user("Guest")
        with self.assertRaises(frappe.AuthenticationError):
            api.api_preview_stock_opening(**payload)

    def test_file_matches_existing_master_and_private_file_is_not_shared(self):
        data = opening_workbook.build_bytes([field for field, _ in opening_workbook.FIELD_LABELS], [
            {"product_code": self.prefix, "unit": "pcs", "qty": "4", "unit_cost": "12.50"},
            {"product_code": self.prefix + "-other", "unit": "pcs"},
        ])
        file = frappe.get_doc({"doctype": "File", "file_name": self.prefix + ".xlsx", "content": data, "is_private": 1}).save()
        rows = api.api_read_stock_opening_file(file.file_url)["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["row_number"], 2)
        self.post(self.payload(rows=rows))
        self.assertEqual(self.qty(), 4)
        frappe.set_user(self.users["both"])
        with self.assertRaises(frappe.PermissionError):
            api.api_read_stock_opening_file(file.file_url)

    def test_product_or_warehouse_disabled_after_preview_and_modified_payload(self):
        payload = self.payload()
        review = api.api_preview_stock_opening(**payload)
        payload["rows"][0]["unit_cost"] = 5
        with self.assertRaises(frappe.ValidationError):
            api.api_submit_stock_opening(**payload, review_token=review["review_token"])
        payload = self.payload()
        frappe.db.set_value("ERP Product", self.product, "is_active", 0)
        with self.assertRaises(frappe.ValidationError):
            api.api_submit_stock_opening(**payload, review_token=review["review_token"])
        frappe.db.set_value("ERP Product", self.product, "is_active", 1)
        frappe.db.set_value("ERP Warehouse", self.place, "is_active", 0)
        with self.assertRaises(frappe.ValidationError):
            api.api_submit_stock_opening(**payload, review_token=review["review_token"])
        self.assertEqual(self.qty(), 0)

    def test_two_database_transactions_initialize_same_position_once(self):
        payload, rendezvous = self.payload(), Barrier(2)
        site, sites_path, features = frappe.local.site, frappe.local.sites_path, self.features
        frappe.db.commit()

        def worker():
            frappe.init(site=site, sites_path=sites_path)
            frappe.connect()
            frappe.set_user("Administrator")
            frappe.conf.pearnly_features = features
            try:
                review = api.api_preview_stock_opening(**payload)
                rendezvous.wait(timeout=10)
                result = api.api_submit_stock_opening(**payload, review_token=review["review_token"])
                frappe.db.commit()
                return result
            finally:
                frappe.destroy()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: worker(), range(2)))
        frappe.db.rollback()
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(sum(result["skipped"] for result in results), 1)
        self.assertEqual(self.qty(), Decimal("1.123456"))
        self.assertEqual(frappe.db.count("ERP Stock Opening", {"product": self.product,
            "warehouse": self.place}), 1)

    def test_a_location_column_is_not_read_and_stock_is_per_warehouse(self):
        """库存只到「物料 × 仓库」（11-19 取消仓位）：表里带来的仓位不读；同一物料同一个仓只初始化一次。"""
        payload = self.payload(rows=[{"product": self.product, "location": "Rack-A", "qty": 2, "unit_cost": 12}])
        first = self.post(payload)
        self.assertEqual(first["openings"], [contracts.position_name("OPN", self.product, self.place)])
        self.assertEqual(self.post(payload)["skipped"], 1)
        self.assertEqual(stock.current_balance(self.product, self.place).qty, 2)
        self.assertTrue(frappe.db.exists("ERP Stock Balance", contracts.position_name("BAL", self.product, self.place)))

    def test_a_product_created_with_opening_stock_and_a_sheet_with_a_warehouse_column(self):
        """多仓闭环 P1（G1、G5）：建商品时顺手放期初（两个仓），与商品同一事务、照「每个位置只初始化一次」；任何一行不成整件商品
        不建。期初表每行可写自己的仓（空着 = 表头的仓），别家公司的仓记成这一行的错。"""
        from pearnly_erp_portal.inventory import warehouses

        second = warehouses.create_warehouse(warehouse_name=self.prefix + " second", company=self.a["name"])["name"]
        made = products.create_product(product_name=self.prefix + " with opening", code=self.prefix + "-op", unit="pcs",
                                       opening=[{"warehouse": self.place, "qty": "3", "unit_cost": "10"},
                                                {"warehouse": second, "qty": "2", "unit_cost": "10"},
                                                {"warehouse": second, "qty": ""}])["name"]
        self.assertEqual(stock.current_balance(made, self.place).qty, 3)
        self.assertEqual(stock.current_balance(made, second).qty, 2)
        code = self.prefix + "-bad"
        with self.assertRaises(frappe.ValidationError):
            products.create_product(product_name=self.prefix + " bad", code=code, unit="pcs",
                                    opening=[{"warehouse": self.b["warehouse"], "qty": "1", "unit_cost": "1"}])
        frappe.db.rollback()
        self.assertFalse(frappe.db.exists("ERP Product", {"code": code}))  # 期初不成：商品也没建
        sheet = api.api_preview_stock_opening(**self.payload(rows=[
            {"product": self.other, "qty": "1", "unit_cost": "1"},
            {"product": self.other, "warehouse": second, "qty": "1", "unit_cost": "1"},
            {"product": self.other, "warehouse": self.b["warehouse"], "qty": "1", "unit_cost": "1"}]))
        self.assertEqual([row["status"] for row in sheet["rows"]], ["ready", "ready", "error"])
        self.assertEqual([row.get("warehouse") for row in sheet["rows"][:2]], [self.place, second])
