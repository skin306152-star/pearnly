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


if __name__ == "__main__":
	connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
	try:
		suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeClientRoster)
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
		}
		print(json.dumps({"anonymous_http": anonymous}))
		if set(anonymous.values()) != {403}:
			raise SystemExit(1)
		raise SystemExit(0 if result.wasSuccessful() else 1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
