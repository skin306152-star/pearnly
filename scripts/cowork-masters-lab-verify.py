"""Isolated Site verification for the native client roster and buyer memory.

Only the dedicated lab bench is accepted, synthetic users/workspaces are created
inside it, and everything rolls back. No file reading, model, ERP or external call
happens here.
"""

import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from uuid import uuid4

import frappe
from werkzeug.test import Client
from werkzeug.wrappers import Response

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-backend-lab-verify.py")))
connect, user, grant, PAGES = (BASE[key] for key in ("connect", "user", "grant", "PAGES"))
ROLE = "Cowork Client Roster Lab"
MANAGER_ROLES = ("Cowork Admin",)
READER_ROLES = ("Cowork OCR History",)


class NativeClientRoster(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		suffix = uuid4().hex[:10]
		cls.manager = f"cw-roster-manager-{suffix}@example.invalid"
		cls.reader = f"cw-roster-reader-{suffix}@example.invalid"
		cls.outsider = f"cw-roster-outsider-{suffix}@example.invalid"
		user(cls.manager, ["Cowork Master Data", *MANAGER_ROLES])
		user(cls.reader, list(READER_ROLES))
		# A Cowork Admin is exempt from the company grant by design, so a true
		# outsider here is an ordinary Cowork user without that role.
		user(cls.outsider, ["Cowork Intake"])
		from pearnly_cowork.masters import api as masters
		cls.workspace = masters.create_workspace("Roster lab " + suffix)["name"]
		cls.other_workspace = masters.create_workspace("Roster lab other " + suffix)["name"]
		grant(cls.manager, "Cowork Workspace", cls.workspace)
		grant(cls.reader, "Cowork Workspace", cls.workspace)
		grant(cls.outsider, "Cowork Workspace", cls.other_workspace)
		frappe.db.commit()

	def setUp(self):
		from pearnly_cowork.masters import api as masters, clients as clients_service
		self.api, self.service = masters, clients_service
		self.previous = dict(frappe.conf)
		frappe.conf.pearnly_features = ["intake", "ocr_history", "master_data", "push_log",
			"stocktake", "recon"]
		frappe.conf.pearnly_product = "cowork"
		frappe.set_user(self.manager)
		frappe.clear_cache()

	def tearDown(self):
		frappe.db.rollback()
		frappe.conf.clear()
		frappe.conf.update(self.previous)
		frappe.set_user("Administrator")
		frappe.clear_cache()

	def history(self, total_amount="107.00", client=None):
		"""Create a recognition record through the sanctioned intake service.

		The history domain refuses direct writes (a record's client and evidence are
		immutable), so the test goes the same way the product does.
		"""
		from pearnly_cowork.history import api as history_api
		created = history_api.create(self.workspace, PAGES, uuid4().hex, client=client)
		frappe.db.set_value("Cowork OCR History", created["name"], "total_amount", total_amount,
			update_modified=False)
		return created["name"]

	def test_a_client_is_created_cleaned_and_listed_with_its_totals(self):
		created = self.api.save_client(self.workspace, {"name": "  ACME Co., Ltd.  ",
			"short_name": "x" * 200, "tax_id": " 0-1055-48103-91-2 ", "notes": "  first  ",
			"surprise": "dropped"})
		self.assertEqual(created["display_name"], "ACME Co., Ltd.")
		self.assertEqual(len(created["short_name"]), 80)          # deployed limit
		self.assertEqual(created["tax_id"], "0-1055-48103-91-2")
		self.assertEqual(created["color"], "#3b82f6")
		self.assertNotIn("surprise", created)

		second = self.api.save_client(self.workspace, {"name": "Beta Shop"})
		self.history("107.00", client=created["name"])
		self.history("93.50", client=created["name"])
		self.history("10.00")                                     # unassigned

		roster = self.api.roster(self.workspace)
		self.assertEqual([row["name"] for row in roster], [created["name"], second["name"]])
		acme = roster[0]
		self.assertEqual(acme["display_name"], "ACME Co., Ltd.")
		self.assertEqual(acme["invoice_count"], 2)
		self.assertEqual(acme["total_amount"], 200.5)
		self.assertTrue(acme["last_invoice_at"])
		self.assertEqual(roster[1]["invoice_count"], 0)
		self.assertEqual(roster[1]["total_amount"], 0)

	def test_an_update_only_touches_what_it_was_given(self):
		client = self.api.save_client(self.workspace, {"name": "Gamma", "notes": "keep me",
			"branch": "00000"})
		updated = self.api.save_client(self.workspace, {"display_name": "Gamma Ltd."},
			name=client["name"])
		self.assertTrue(updated["updated"])
		self.assertEqual(updated["client"]["display_name"], "Gamma Ltd.")
		self.assertEqual(updated["client"]["notes"], "keep me")     # untouched
		self.assertEqual(updated["client"]["branch"], "00000")

		# An emptied text clears the column; a call with nothing writable does not.
		cleared = self.api.save_client(self.workspace, {"notes": "   "}, name=client["name"])
		self.assertIsNone(cleared["client"]["notes"])
		nothing = self.api.save_client(self.workspace, {"workspace": self.workspace},
			name=client["name"])
		self.assertFalse(nothing["updated"])

	def test_deactivating_hides_a_client_without_deleting_it(self):
		client = self.api.save_client(self.workspace, {"name": "Delta"})
		self.api.save_client(self.workspace, {"is_active": 0}, name=client["name"])
		self.assertEqual([row["name"] for row in self.api.roster(self.workspace)], [])
		self.assertEqual([row["name"] for row in self.api.roster(self.workspace,
			include_inactive=1)], [client["name"]])
		self.api.save_client(self.workspace, {"is_active": True}, name=client["name"])
		self.assertEqual([row["name"] for row in self.api.roster(self.workspace)],
			[client["name"]])

	def test_a_client_with_records_cannot_be_deleted_and_an_empty_one_can(self):
		# The deployed code silently unbound the invoices first; here the record's
		# client is immutable by the history domain's contract, so deletion is refused
		# and says how many records are in the way.
		used = self.api.save_client(self.workspace, {"name": "Epsilon"})
		history = self.history("50.00", client=used["name"])
		with self.assertRaises(frappe.ValidationError):
			self.api.delete_client(self.workspace, used["name"])
		self.assertTrue(frappe.db.exists("Cowork Client", used["name"]))
		self.assertEqual(frappe.db.get_value("Cowork OCR History", history, "client"),
			used["name"])

		unused = self.api.save_client(self.workspace, {"name": "Zeta"})
		self.assertTrue(self.api.delete_client(self.workspace, unused["name"])["deleted"])
		self.assertFalse(frappe.db.exists("Cowork Client", unused["name"]))

	def test_permissions_follow_the_feature_and_the_company(self):
		frappe.set_user(self.reader)
		self.assertTrue(self.api.roster(self.workspace) == [])
		with self.assertRaises(frappe.PermissionError):
			self.api.save_client(self.workspace, {"name": "Reader cannot write"})
		frappe.set_user(self.outsider)
		with self.assertRaises(frappe.PermissionError):
			self.api.roster(self.workspace)
		with self.assertRaises(frappe.PermissionError):
			self.api.save_client(self.workspace, {"name": "Outsider cannot write"})

	def test_a_new_tax_creates_a_client_and_the_second_time_it_is_remembered(self):
		from pearnly_cowork.masters import buyer_resolve
		first = self.api.resolve_buyer(self.workspace, "New Buyer Co., Ltd.",
			"0-9999-11111-22-3")
		self.assertEqual((first["action"], first["reason"]),
			("created", "auto_created_by_tax_id"))
		self.assertTrue(frappe.db.exists("Cowork Client", first["client_id"]))
		second = self.api.resolve_buyer(self.workspace, "new buyer co., ltd.",
			"0-9999-11111-22-3")
		self.assertEqual((second["action"], second["match_source"]), ("assigned", "memory"))
		self.assertEqual(second["client_id"], first["client_id"])
		self.assertEqual(frappe.db.count("Cowork Buyer Memory", {"workspace": self.workspace}), 1)
		# Created once (count 1), re-pointed and counted up on the second resolve.
		self.assertEqual(frappe.db.get_value("Cowork Buyer Memory", {"workspace": self.workspace},
			"use_count"), 2)

	def test_a_name_match_only_suggests_and_a_conflict_only_reviews(self):
		client = self.api.save_client(self.workspace, {"name": "ACME Co., Ltd."})
		suggested = self.api.resolve_buyer(self.workspace, "ACME Co., Ltd. Bangkok Branch")
		self.assertEqual((suggested["action"], suggested["client_id"]),
			("suggest", client["name"]))
		self.assertEqual(frappe.db.count("Cowork Buyer Memory", {"workspace": self.workspace}), 0)
		conflict = self.api.resolve_buyer(self.workspace, "ACME Co., Ltd.",
			buyer_candidates=["ACME Co., Ltd.", "Beta Shop"])
		self.assertEqual((conflict["action"], conflict["reason"]),
			("review", "buyer_candidates_conflict"))
		# Nothing was created for either of them.
		self.assertEqual(frappe.db.count("Cowork Client", {"workspace": self.workspace}), 1)


class NativeProductRoster(unittest.TestCase):
	"""The product store: one default sell unit, unique unit names, exact numbers."""

	@classmethod
	def setUpClass(cls):
		suffix = uuid4().hex[:10]
		cls.manager = f"cw-product-manager-{suffix}@example.invalid"
		cls.reader = f"cw-product-reader-{suffix}@example.invalid"
		user(cls.manager, ["Cowork Master Data", "Cowork Admin"])
		user(cls.reader, ["Cowork OCR History"])
		from pearnly_cowork.masters import api as masters
		cls.workspace = masters.create_workspace("Product lab " + suffix)["name"]
		cls.other_workspace = masters.create_workspace("Product lab other " + suffix)["name"]
		grant(cls.manager, "Cowork Workspace", cls.workspace)
		grant(cls.reader, "Cowork Workspace", cls.workspace)
		# A product of another company, created while the session is still Administrator.
		cls.foreign = masters.save_product(cls.other_workspace, {"display_name": "Foreign"})
		frappe.db.commit()

	def setUp(self):
		from pearnly_cowork.masters import api as masters
		self.api = masters
		self.previous = dict(frappe.conf)
		frappe.conf.pearnly_features = ["intake", "ocr_history", "master_data", "push_log",
			"stocktake", "recon"]
		frappe.conf.pearnly_product = "cowork"
		frappe.set_user(self.manager)
		frappe.clear_cache()

	def tearDown(self):
		frappe.db.rollback()
		frappe.conf.clear()
		frappe.conf.update(self.previous)
		frappe.set_user("Administrator")
		frappe.clear_cache()

	def test_a_product_is_saved_with_its_units_and_one_default(self):
		product = self.api.save_product(self.workspace, {
			"display_name": "  น้ำปลา  ",
			"name_th": "น้ำปลา", "name_en": "Fish Sauce", "name_zh": "鱼露",
			"sku": "FS-001", "min_stock": "6", "default_cost": "12.50",
			"track_batch": True,
			"units": [
				{"unit_name": "ขวด", "factor_to_base": "1", "barcode": "8850000000001",
					"price": "20.00", "is_default_sell": True},
				{"unit_name": "โหล", "factor_to_base": "12", "price": "220.00"},
			]})
		self.assertEqual(product["display_name"], "น้ำปลา")
		self.assertEqual(product["base_unit"], "ชิ้น")           # deployed default
		self.assertTrue(product["track_batch"])
		self.assertEqual(product["min_stock"], 6.0)
		self.assertEqual(product["default_cost"], 12.5)
		self.assertEqual(product["names"]["display"], "น้ำปลา / Fish Sauce / 鱼露")
		self.assertEqual([unit["unit_name"] for unit in product["units"]], ["ขวด", "โหล"])
		self.assertEqual(product["units"][0]["factor_to_base"], 1.0)
		self.assertEqual(product["units"][1]["factor_to_base"], 12.0)
		self.assertEqual(product["units"][0]["is_default_sell"], 1)
		self.assertEqual(product["units"][1]["is_default_sell"], 0)

		listed = self.api.products(self.workspace)
		self.assertEqual([row["display_name"] for row in listed], ["น้ำปลา"])
		self.assertEqual(self.api.product_detail(self.workspace, product["name"])["sku"], "FS-001")

	def test_the_default_sell_unit_is_switched_rather_than_doubled(self):
		product = self.api.save_product(self.workspace, {"display_name": "น้ำตาล", "units": [
			{"unit_name": "ถุง", "factor_to_base": "1", "is_default_sell": True},
			{"unit_name": "ลัง", "factor_to_base": "24"}]})
		updated = self.api.save_product(self.workspace, {"units": [
			{"unit_name": "ถุง", "factor_to_base": "1"},
			{"unit_name": "ลัง", "factor_to_base": "24", "is_default_sell": True}]},
			name=product["name"])
		self.assertEqual(sum(unit["is_default_sell"] for unit in updated["units"]), 1)
		self.assertEqual([unit["is_default_sell"] for unit in updated["units"]], [0, 1])

	def test_two_defaults_or_a_repeated_unit_name_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.api.save_product(self.workspace, {"display_name": "Bad", "units": [
				{"unit_name": "ถุง", "factor_to_base": "1", "is_default_sell": True},
				{"unit_name": "ลัง", "factor_to_base": "24", "is_default_sell": True}]})
		with self.assertRaises(frappe.ValidationError):
			self.api.save_product(self.workspace, {"display_name": "Bad", "units": [
				{"unit_name": "ถุง", "factor_to_base": "1"},
				{"unit_name": " ถุง ", "factor_to_base": "2"}]})
		with self.assertRaises(frappe.ValidationError):
			self.api.save_product(self.workspace, {"display_name": "Bad", "units": [
				{"unit_name": "ถุง"}]})
		self.assertEqual(self.api.products(self.workspace), [])

	def test_an_exact_factor_survives_the_round_trip(self):
		product = self.api.save_product(self.workspace, {"display_name": "นม",
			"name_th": "นมสด", "units": [
				{"unit_name": "กล่อง", "factor_to_base": "0.1", "price": "12.50"}]})
		self.assertEqual(product["units"][0]["factor_to_base"], 0.1)
		self.assertEqual(product["units"][0]["price"], 12.5)
		# The display name is its own field; the three-language rule reads the name_* triple.
		self.assertEqual(product["names"]["display"], "นมสด")
		self.assertEqual(product["display_name"], "นม")

	def test_deactivating_hiding_and_deleting_a_product(self):
		product = self.api.save_product(self.workspace, {"display_name": "ชา"})
		self.api.save_product(self.workspace, {"is_active": 0}, name=product["name"])
		self.assertEqual(self.api.products(self.workspace), [])
		self.assertEqual([row["name"] for row in self.api.products(self.workspace,
			include_inactive=1)], [product["name"]])
		self.assertTrue(self.api.delete_product(self.workspace, product["name"])["deleted"])
		self.assertFalse(frappe.db.exists("Cowork Product", product["name"]))
		self.assertEqual(frappe.db.count("Cowork Product Unit",
			{"parent": product["name"]}), 0)

	def test_permissions_and_a_foreign_company(self):
		frappe.set_user(self.reader)
		self.assertEqual(self.api.products(self.workspace), [])
		with self.assertRaises(frappe.PermissionError):
			self.api.save_product(self.workspace, {"display_name": "Reader cannot write"})
		frappe.set_user(self.manager)
		with self.assertRaises(frappe.PermissionError):
			self.api.product_detail(self.workspace, self.foreign["name"])


if __name__ == "__main__":
	connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
	try:
		suite = unittest.TestSuite(
			unittest.defaultTestLoader.loadTestsFromTestCase(cls)
			for cls in (NativeClientRoster, NativeProductRoster))
		result = unittest.TextTestRunner(verbosity=2).run(suite)
		frappe.db.rollback()
		frappe.destroy()
		connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
		from frappe.app import application
		client = Client(application, Response)
		base = f"http://{frappe.local.site}"
		rpc = "/api/method/pearnly_cowork.masters.api."
		anonymous = {
			"roster": client.get(rpc + "roster", base_url=base,
				query_string={"workspace": "x"}).status_code,
			"client_detail": client.get(rpc + "client_detail", base_url=base,
				query_string={"workspace": "x", "name": "y"}).status_code,
			"save_client": client.post(rpc + "save_client", base_url=base,
				json={"workspace": "x", "values": {}}).status_code,
			"delete_client": client.post(rpc + "delete_client", base_url=base,
				json={"workspace": "x", "name": "y"}).status_code,
			"resolve_buyer": client.post(rpc + "resolve_buyer", base_url=base,
				json={"workspace": "x", "buyer_name": "y"}).status_code,
			"products": client.get(rpc + "products", base_url=base,
				query_string={"workspace": "x"}).status_code,
			"product_detail": client.get(rpc + "product_detail", base_url=base,
				query_string={"workspace": "x", "name": "y"}).status_code,
			"save_product": client.post(rpc + "save_product", base_url=base,
				json={"workspace": "x", "values": {}}).status_code,
			"delete_product": client.post(rpc + "delete_product", base_url=base,
				json={"workspace": "x", "name": "y"}).status_code,
		}
		print(json.dumps({"anonymous_http": anonymous}))
		if set(anonymous.values()) != {403}:
			raise SystemExit(1)
		raise SystemExit(0 if result.wasSuccessful() else 1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
