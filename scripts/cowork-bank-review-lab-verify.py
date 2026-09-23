"""Guarded native S8 tests; synthetic rows, fake parsers, no paid/external calls.

Only the task-owned internal-network lab is admitted. Setup users persist;
business tests roll back. --install-fresh creates one explicitly allowed Site.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from werkzeug.test import Client
from werkzeug.wrappers import Response

BANK = runpy.run_path(str(Path(__file__).with_name("cowork-bank-recon-lab-verify.py")))
BASE = BANK["NativeBankReconciliation"]
connect = BANK["connect"]


class BankReview(unittest.TestCase):
	setUpClass = classmethod(BASE.setUpClass.__func__)
	setUp, tearDown = BASE.setUp, BASE.tearDown

	def pending(self, *, anchors=None, filename="statement.pdf", **changes):
		from pearnly_cowork.reconciliation.bank_statement import StatementRow
		from pearnly_cowork.reconciliation.bank_matching import GlRow
		statement = {"ok": True, "opening": 100, "closing": 60, "bank_code": "bbl",
			"rows": [StatementRow(None, "payment", 40, 0, 60, confidence="low")],
			"row_count": 1, "source": "model", "balance_warn_count": 0,
			"amount_fixed_count": 0, "low_conf_count": 1, "completeness": {"ok": True},
			"parser_version": "lab", "needs_review": True}
		statement.update(changes)
		ledger = {"ok": True, "opening": 100, "closing": 60, "accounts": ["1000"],
			"rows": [GlRow(None, "GL-1", "1000", "payment", 0, 40)],
			"row_count": 1, "source": "workbook", "needs_review": False}
		with patch.object(self.service.bank_intake, "read_statement_file", return_value=statement), \
				patch.object(self.service.bank_intake, "read_ledger_file", return_value=ledger):
			return self.service.create_from_documents(self.workspace, "Review lab", self.request_id,
				b"fake", filename, b"fake", "gl.xlsx", gl_account_code="1000", anchors=anchors)

	def test_pending_detail_export_and_zero_write_check(self):
		result = self.pending()
		name = result["name"]
		self.assertEqual(result["status"], "needs_review")
		self.assertTrue(result["parsed"]["needs_review"])
		self.assertEqual(self.api.detail(name)["total"], 0)
		self.assertEqual(self.api.records(self.workspace)["tasks"][0]["status"], "needs_review")
		with self.assertRaises(frappe.ValidationError):
			self.service.export(name)
		value = self.api.review(name)
		self.assertEqual(value["diagnostics"]["row_metadata"][0]["confidence"], "low")
		before = frappe.get_doc(BANK["TASK"], name).as_dict()
		self.assertTrue(self.api.check_review(name, value["rows"])["ok"])
		self.assertEqual(before, frappe.get_doc(BANK["TASK"], name).as_dict())
		self.assertTrue(self.pending()["replayed"])

	def test_confirm_audit_replay_and_no_reader_or_model_calls(self):
		name = self.pending()["name"]
		value = self.api.review(name)
		rows = copy.deepcopy(value["rows"])
		rows[0].update(description="human corrected", withdrawal="45.00", balance="55.00")
		with patch.object(self.service.bank_intake, "read_statement_file", side_effect=AssertionError), \
				patch.object(self.service.bank_intake, "read_ledger_file", side_effect=AssertionError):
			result = self.api.confirm_review(name, rows, 1)
			self.assertFalse(result["replayed"])
			self.assertTrue(self.api.confirm_review(name, rows, 1)["replayed"])
		self.assertEqual((result["status"], result["revision"]), ("completed", 2))
		stored = self.api.review(name)
		self.assertEqual(stored["rows"], value["rows"])
		self.assertEqual(stored["confirmation"]["by"], self.actor)
		self.assertEqual(stored["confirmation"]["rows"][0]["withdrawal"], "45.00")
		self.assertEqual(self.api.detail(name)["summary"]["stmt_closing"], 55)
		self.assertTrue(self.service.export(name)["content"].startswith(b"PK"))
		with self.assertRaises(frappe.ValidationError):
			self.api.confirm_review(name, value["rows"], 1)
		with self.assertRaises(frappe.ValidationError):
			self.api.confirm_review(name, rows, 2)
		self.assertEqual(frappe.db.count(BANK["ROW"], {"task": name}), result["row_count"])

	def test_bad_chain_is_not_confirmed(self):
		name = self.pending()["name"]
		rows = self.api.review(name)["rows"]
		rows[0]["balance"] = "59.97"
		self.assertFalse(self.api.check_review(name, rows)["ok"])
		with self.assertRaises(frappe.ValidationError):
			self.api.confirm_review(name, rows, 1)
		self.assertEqual(self.api.review(name)["revision"], 1)
		self.assertEqual(self.api.detail(name)["total"], 0)

	def test_permissions_feature_and_native_write_guard(self):
		name = self.pending()["name"]
		rows = self.api.review(name)["rows"]
		frappe.db.delete("Has Role", {"parent": self.norecon, "role": "Cowork Admin"})
		frappe.clear_cache(user=self.norecon)
		for actor in (self.outsider, self.norecon):
			frappe.set_user(actor)
			for method, args in ((self.api.review, (name,)),
					(self.api.check_review, (name, rows)), (self.api.confirm_review, (name, rows, 1))):
				with self.assertRaises(frappe.PermissionError):
					method(*args)
		frappe.set_user(self.actor)
		frappe.conf.pearnly_features = []
		with self.assertRaises(frappe.PermissionError):
			self.api.confirm_review(name, rows, 1)
		frappe.conf.pearnly_features = ["recon"]
		doc = frappe.get_doc(BANK["TASK"], name)
		doc.status = "completed"
		doc.flags.bank_recon_write = True
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_confirmation_failure_rolls_back_and_retry_completes(self):
		name = self.pending()["name"]
		rows = self.api.review(name)["rows"]
		frappe.db.savepoint("before_review")
		with patch.object(self.service, "_persist_rows", side_effect=RuntimeError("lab failure")):
			with self.assertRaises(RuntimeError):
				self.api.confirm_review(name, rows, 1)
		frappe.db.rollback(save_point="before_review")
		self.assertEqual(self.api.review(name)["revision"], 1)
		self.assertFalse(self.api.confirm_review(name, rows, 1)["replayed"])

	def test_clean_pdf_stays_direct_and_pending_can_be_deleted(self):
		name = self.pending(low_conf_count=0, needs_review=False)["name"]
		self.assertEqual(self.api.detail(name)["task"]["status"], "completed")
		with self.assertRaises(frappe.ValidationError):
			self.api.review(name)
		self.api.delete(name)
		pending = self.pending()["name"]
		self.api.delete_many([pending])
		self.assertFalse(frappe.db.exists(BANK["TASK"], pending))

	def test_manual_anchor_is_preserved_on_confirm(self):
		name = self.pending(anchors={"overrides": {"stmt_closing": "99"}})["name"]
		result = self.api.confirm_review(name, self.api.review(name)["rows"], 1)
		self.assertEqual(result["summary"]["stmt_closing"], 99)

	def test_workbook_diagnostics_do_not_introduce_column_mapping_workflow(self):
		result = self.pending(filename="statement.xlsx")
		self.assertTrue(result["parsed"]["needs_review"])
		self.assertEqual(result["status"], "completed")
		with self.assertRaises(frappe.ValidationError):
			self.api.review(result["name"])

	def test_revoked_scope_product_and_disabled_user_block_replay(self):
		name = self.pending()["name"]
		rows = self.api.review(name)["rows"]
		self.api.confirm_review(name, rows, 1)
		frappe.conf.pearnly_product = "erp"
		with self.assertRaises(frappe.PermissionError):
			self.api.confirm_review(name, rows, 1)
		frappe.conf.pearnly_product = "cowork"
		frappe.db.set_value("User", self.actor, "enabled", 0)
		with self.assertRaises(frappe.PermissionError):
			self.api.confirm_review(name, rows, 1)
		frappe.db.set_value("User", self.actor, "enabled", 1)
		frappe.db.delete("User Permission", {"name": self.permission})
		frappe.set_user(self.actor)
		with self.assertRaises(frappe.PermissionError):
			self.api.confirm_review(name, rows, 1)

	def test_unauthorized_file_entry_does_not_read_or_parse(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation \
			import cowork_bank_reconciliation as controller
		frappe.set_user(self.outsider)
		with patch.object(controller.files_service, "original", side_effect=AssertionError), \
				patch.object(self.service.bank_intake, "read_statement_file", side_effect=AssertionError):
			with self.assertRaises(frappe.PermissionError):
				controller.create_from_sources(self.workspace, "Denied", self.request_id, "x", "y")
			with self.assertRaises(frappe.PermissionError):
				self.service.create_from_documents(self.workspace, "Denied", self.request_id,
					b"fake", "x.pdf", b"fake", "y.xlsx")


def anonymous_http():
	from frappe.app import application
	client = Client(application, Response)
	base = "http://" + frappe.local.site
	path = "/api/method/pearnly_cowork.reconciliation.bank_recon_api."
	statuses = {"review": client.get(path + "review", base_url=base,
		query_string={"task_id": "missing"}).status_code}
	for name in ("check_review", "confirm_review"):
		statuses[name] = client.post(path + name, base_url=base,
			json={"task_id": "missing", "rows": [], "expected_revision": 1}).status_code
	assert all(code in (401, 403) for code in statuses.values()), statuses
	print(json.dumps({"anonymous_http": statuses}))


def snapshot():
	result = {}
	for table in (BANK["TASK"], BANK["ROW"]):
		rows = frappe.get_all(table, fields=["*"], order_by="name", limit_page_length=0)
		# Empty added column is intentionally excluded when comparing old rows.
		for row in rows:
			if not row.get("review_json"):
				row.pop("review_json", None)
		result[table] = {"count": len(rows), "sha256": hashlib.sha256(
			json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
	return result


def fixture():
	frappe.set_user("Administrator")
	BankReview.setUpClass()
	case = BankReview()
	case.setUp()
	name = case.pending()["name"]
	rows = case.api.review(name)["rows"]
	frappe.db.commit()
	return case, name, rows


def http_checks():
	from frappe.app import application
	case, name, rows = fixture()
	frappe.set_user("Administrator")
	account = frappe.get_doc("User", case.actor)
	account.api_key, secret = uuid4().hex, uuid4().hex
	account.api_secret = secret
	account.save()
	site = frappe.local.site
	frappe.db.commit()
	frappe.destroy()
	client = Client(application, Response)
	headers = {"Authorization": f"token {account.api_key}:{secret}"}
	path = "/api/method/pearnly_cowork.reconciliation.bank_recon_api."
	count = 0
	def call(method, endpoint, body):
		nonlocal count
		count += 1
		return client.open(path + endpoint, method=method, base_url="http://" + site,
			headers=headers, **({"query_string": body} if method == "GET" else {"json": body}))
	assert call("GET", "review", {"task_id": name}).json["message"]["revision"] == 1
	assert call("GET", "confirm_review", {"task_id": name}).status_code == 403
	assert call("GET", "check_review", {"task_id": name}).status_code == 403
	assert call("GET", "export", {"task_id": name}).status_code == 417
	assert call("POST", "check_review", {"task_id": name, "rows": rows}).json["message"]["ok"]
	assert call("POST", "confirm_review", {"task_id": name, "rows": [], "expected_revision": 1}).status_code == 417
	body = {"task_id": name, "rows": rows, "expected_revision": 1}
	first = call("POST", "confirm_review", body)
	assert first.status_code == 200, first.get_data(as_text=True)
	assert not first.json["message"]["replayed"]
	assert call("POST", "confirm_review", body).json["message"]["replayed"]
	assert call("GET", "review", {"task_id": name}).json["message"]["revision"] == 2
	assert call("GET", "export", {"task_id": name}).data.startswith(b"PK")
	connect(site)
	print(json.dumps({"authenticated_http": "PASS", "requests": count}))


def concurrency(mode):
	case, name, rows = fixture()
	children, outputs = [], []
	try:
		for index in range(2):
			edited = copy.deepcopy(rows)
			if mode == "different" and index:
				edited[0]["description"] = "another correction"
			payload = {"actor": case.actor, "name": name, "rows": edited,
				"delete": mode == "delete" and index == 1}
			child = subprocess.Popen([sys.executable, __file__, "--worker", json.dumps(payload)],
				stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
			children.append(child)
		for child in children:
			assert child.stdout.readline().strip() == "ready"
		for child in children:
			child.stdin.write("start\n")
			child.stdin.flush()
		for child in children:
			stdout, stderr = child.communicate(timeout=30)
			assert child.returncode == 0, stderr
			outputs.append(json.loads(stdout.strip()))
		frappe.db.rollback()
		if mode == "same":
			assert sum(item.get("replayed", False) for item in outputs) == 1, outputs
			assert all(item.get("status") == "completed" for item in outputs), outputs
		elif mode == "different":
			assert sum(item.get("error") == "ValidationError" for item in outputs) == 1, outputs
			assert sum(item.get("status") == "completed" for item in outputs) == 1, outputs
		else:
			assert not frappe.db.exists(BANK["TASK"], name), outputs
			assert frappe.db.count(BANK["ROW"], {"task": name}) == 0, outputs
		if mode != "delete":
			assert frappe.db.count(BANK["ROW"], {"task": name}) == outputs[
				next(i for i, item in enumerate(outputs) if item.get("replayed") is False)]["row_count"]
		print(json.dumps({"concurrency": mode, "result": "PASS", "outputs": outputs}))
	finally:
		for child in children:
			if child.poll() is None:
				child.kill()
				child.wait()


def worker(payload):
	from pearnly_cowork.reconciliation import bank_recon_api as api
	frappe.set_user(payload["actor"])
	# Establish a repeatable-read snapshot before both connections compete.
	frappe.db.count(BANK["TASK"])
	print("ready", flush=True)
	sys.stdin.readline()
	try:
		result = api.delete(payload["name"]) if payload["delete"] else api.confirm_review(
			payload["name"], payload["rows"], 1)
		frappe.db.commit()
	except (frappe.ValidationError, frappe.DoesNotExistError) as error:
		frappe.db.rollback()
		result = {"error": type(error).__name__}
	print(json.dumps(result))


if __name__ == "__main__":
	connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
	try:
		if "--worker" in sys.argv:
			worker(json.loads(sys.argv[sys.argv.index("--worker") + 1]))
		elif "--concurrency" in sys.argv:
			modes = sys.argv[sys.argv.index("--concurrency") + 1:] or ("same", "different", "delete")
			for mode in modes:
				concurrency(mode)
		elif "--http" in sys.argv:
			http_checks()
		elif "--prepare-upgrade" in sys.argv:
			fixture()
			print(json.dumps(snapshot(), sort_keys=True))
		elif "--snapshot" in sys.argv:
			print(json.dumps(snapshot(), sort_keys=True))
		elif "--install-fresh" in sys.argv:
			helper = runpy.run_path(str(Path(__file__).with_name("cowork-duplicates-lab-verify.py")))
			helper["install_fresh"]("cowork-backend-bank-review.localhost")
		else:
			result = unittest.TextTestRunner(verbosity=2).run(
				unittest.defaultTestLoader.loadTestsFromTestCase(BankReview))
			anonymous_http()
			raise SystemExit(0 if result.wasSuccessful() else 1)
	finally:
		frappe.destroy()
