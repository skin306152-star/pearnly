"""Native stocktake verification on the dedicated Cowork backend lab only.

Writes labelled synthetic users, workspaces and stocktakes to the lab volume.
No photos, model/OCR, LINE, ERP, inventory ledger or external network is used.
Concurrency uses separate processes/connections to the same isolated lab DB.
"""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

SITE = os.environ.get("PEARNLY_COWORK_TEST_SITE", "cowork-backend-a.localhost")
ALLOWED_SITES = {"cowork-backend-a.localhost", "cowork-backend-b.localhost",
                 "cowork-backend-fresh.localhost", "cowork-backend-files.localhost"}
OTHER_SITE = "cowork-backend-b.localhost" if SITE != "cowork-backend-b.localhost" else "cowork-backend-a.localhost"
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")
HEADER = ["product_code", "product_name", "barcode", "warehouse", "location", "unit", "book_qty"]


def rows():
    return [[{"value": value, "data_type": "s"} for value in row] for row in (
        ["0012", "Synthetic A", "000012", "A", "01", "EA", "2.500001"],
        ["0013", "Synthetic B", "000013", "", "", "EA", "-1"]) ]


def connect(site=SITE):
    if os.environ.get("PEARNLY_COWORK_BACKEND_LAB") != "1":
        raise SystemExit("Dedicated lab environment required")
    if site not in ALLOWED_SITES or not (SITE_ROOT / ".cowork-backend-lab").is_file():
        raise SystemExit("Dedicated lab Site marker required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_backend_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True


def user(name, roles):
    frappe.get_doc({"doctype": "User", "email": name, "first_name": "Stocktake lab",
        "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
        "roles": [{"role": role} for role in roles]}).insert()


def grant(actor, workspace):
    return frappe.get_doc({"doctype": "User Permission", "user": actor,
        "allow": "Cowork Workspace", "for_value": workspace, "apply_to_all_doctypes": 1}).insert()


class NativeStocktake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api as masters
        from pearnly_cowork.stocktake import api
        cls.api = api
        suffix = uuid4().hex[:10]
        cls.actor = f"cw-stock-{suffix}@example.invalid"
        cls.outsider = f"cw-stock-other-{suffix}@example.invalid"
        cls.manager = f"cw-stock-system-{suffix}@example.invalid"
        for name, roles in ((cls.actor, ["Cowork Stocktake"]), (cls.outsider, ["Cowork Stocktake"]),
                            (cls.manager, ["System Manager"])):
            user(name, roles)
        cls.workspace = masters.create_workspace("Synthetic stocktake A " + suffix)["name"]
        cls.foreign = masters.create_workspace("Synthetic stocktake B " + suffix)["name"]
        cls.grant = grant(cls.actor, cls.workspace).name
        grant(cls.outsider, cls.foreign)
        cls.reference = api.create(cls.workspace, "Synthetic stocktake reference", str(uuid4()), HEADER, rows())["id"]
        cls.foreign_reference = api.create(cls.foreign, "Synthetic other reference", str(uuid4()), HEADER, rows())["id"]
        frappe.db.commit()

    def setUp(self):
        self.original_features = copy.deepcopy(frappe.conf.pearnly_features)
        self.original_product = frappe.conf.pearnly_product
        frappe.set_user(self.actor)

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.pearnly_features = self.original_features
        frappe.conf.pearnly_product = self.original_product
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def create(self, request_id=None, data=None):
        result = self.api.create(self.workspace, "Synthetic stocktake", request_id or str(uuid4()), HEADER,
                                 rows() if data is None else data)
        return self.api.detail(result["id"])

    def add(self, task, value="2.500001", operation=None):
        return self.api.add_entry(task["id"], task["items"][0]["id"], operation or str(uuid4()), value, "A", "01")

    def error(self, code):
        # The API displays translated messages while pure contracts keep codes.
        return self.assertRaisesRegex(frappe.ValidationError, re.escape(frappe._(code)))

    def test_decoded_create_replay_conflict_and_native_list(self):
        key = str(uuid4())
        first, repeated = self.create(key), self.create(key)
        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(first["items"][0]["product_code"], "0012")
        self.assertEqual(first["items"][0]["book_qty"], "2.500001")
        self.assertIsNone(first["items"][0]["actual_qty"])
        self.assertEqual(first["count_mode"], "scan")
        altered = rows()
        altered[0][-1]["value"] = "3"
        with self.error("stocktake.import_conflict"):
            self.create(key, altered)
        listed = next(row for row in self.api.records(self.workspace)["tasks"] if row["id"] == first["id"])
        self.assertEqual((listed["total"], listed["counted"], listed["differences"]), (2, 0, 0))

    def test_scan_edit_void_and_report_exact_totals(self):
        task = self.create()
        first = self.add(task)
        self.assertEqual(first["version"], 0)
        second = self.add(task, "0.000001")
        current = self.api.detail(task["id"])
        self.assertEqual(current["items"][0]["actual_qty"], "2.500002")
        self.assertEqual(current["items"][0]["version"], 2)
        updated = self.api.edit_entry(task["id"], second["entry_id"], str(uuid4()), "0", "B", 0, "02")
        self.assertEqual(updated["version"], 1)
        report = self.api.report(task["id"])
        self.assertEqual(report["summary"][0]["count_result"], "matched")
        self.assertEqual(report["summary"][0]["warehouse_comparison"], "mismatched")
        self.assertEqual(report["summary"][1]["count_result"], "not_counted")
        self.api.edit_entry(task["id"], first["entry_id"], str(uuid4()), "2.500001", "A", 0, "01", True)
        self.assertEqual(self.api.detail(task["id"])["items"][0]["actual_qty"], "0.000000")
        self.api.edit_entry(task["id"], second["entry_id"], str(uuid4()), "0", "B", 1, "02", True)
        self.assertIsNone(self.api.detail(task["id"])["items"][0]["actual_qty"])
        self.assertEqual(self.api.report(task["id"])["summary"][0]["count_result"], "not_counted")
        self.assertEqual(self.api.entries(task["id"])["total"], 2)

    def test_receipt_replay_survives_close_and_payload_actor_conflict(self):
        task, operation = self.create(), str(uuid4())
        first = self.add(task, operation=operation)
        self.assertEqual(self.add(task, operation=operation), first)
        with self.error("stocktake.operation_conflict"):
            self.add(task, "3", operation)
        self.assertEqual(self.api.close(task["id"]), {"ok": True})
        closed = self.api.detail(task["id"])
        self.api.close(task["id"])
        self.assertEqual(self.api.detail(task["id"])["closed_at"], closed["closed_at"])
        self.assertEqual(self.add(task, operation=operation), first)
        with self.error("stocktake.closed"):
            self.add(task)
        frappe.set_user("Administrator")
        with self.error("stocktake.operation_conflict"):
            self.add(task, operation=operation)
        self.assertEqual(frappe.db.count("Cowork Stocktake Operation", {"task": task["id"]}), 1)
        self.assertFalse(frappe.db.count("Pearnly Push Log"))

    def test_version_conflict_and_wrong_target_do_not_write(self):
        task = self.create()
        receipt = self.add(task)
        with self.error("stocktake.conflict"):
            self.api.edit_entry(task["id"], receipt["entry_id"], str(uuid4()), "9", "A", 1)
        other = self.create()
        with self.error("stocktake.not_found"):
            self.api.add_entry(task["id"], other["items"][0]["id"], str(uuid4()), "1", "A")
        self.assertEqual(self.api.detail(task["id"])["items"][0]["actual_qty"], "2.500001")
        with self.error("stocktake.photos_not_supported"):
            self.api.add_entry(task["id"], task["items"][0]["id"], str(uuid4()), "1", "A", photos=["synthetic"])

    def test_total_overflow_rolls_back_entry_receipt_and_item(self):
        task = self.create()
        self.add(task, "99999999999999.999999")
        rejected = str(uuid4())
        with self.error("stocktake.quantity_invalid"):
            self.add(task, "0.000001", rejected)
        self.assertFalse(frappe.db.exists("Cowork Stocktake Entry", rejected))
        self.assertFalse(frappe.db.exists("Cowork Stocktake Operation", rejected))
        item = self.api.detail(task["id"])["items"][0]
        self.assertEqual(item["actual_qty"], "99999999999999.999999")
        self.assertEqual(item["version"], 1)

    def test_late_import_failure_rolls_back_whole_snapshot(self):
        from pearnly_cowork.stocktake import service
        original, key, count = service.persist, str(uuid4()), 0
        def fail_second(doc):
            nonlocal count
            if doc.doctype == "Cowork Stocktake Item":
                count += 1
                if count == 2:
                    raise frappe.ValidationError("synthetic late failure")
            return original(doc)
        with patch.object(service, "persist", fail_second):
            with self.assertRaisesRegex(frappe.ValidationError, "synthetic late failure"):
                self.create(key)
        self.assertFalse(frappe.db.exists("Cowork Stocktake Task", key))
        self.assertFalse(frappe.db.count("Cowork Stocktake Item", {"task": key}))

    def test_generic_native_create_save_delete_and_flag_forgery_rejected(self):
        from frappe.client import save
        task = self.create()
        entry = self.add(task)
        for doctype, name in (("Cowork Stocktake Task", task["id"]),
                              ("Cowork Stocktake Item", task["items"][0]["id"]),
                              ("Cowork Stocktake Entry", entry["entry_id"]),
                              ("Cowork Stocktake Operation", entry["entry_id"])):
            payload = frappe.get_doc(doctype, name).as_dict()
            payload["flags"] = {"stocktake_write": True}
            with self.error("stocktake.dedicated_api_required"):
                save(payload)
            payload["flags"].update(ignore_validate=True, ignore_permissions=True)
            with self.error("stocktake.dedicated_api_required"):
                save(payload)
            with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
                frappe.delete_doc(doctype, name)
        payload = frappe.get_doc("Cowork Stocktake Task", task["id"]).as_dict()
        payload.pop("name")
        payload["record_id"] = str(uuid4())
        payload["flags"] = {"stocktake_write": True}
        with self.error("stocktake.dedicated_api_required"):
            frappe.get_doc(payload).insert()

    def test_scope_entitlement_product_guest_and_revocation(self):
        from frappe.client import get
        for action in (lambda: self.api.detail(self.foreign_reference),
                       lambda: self.api.close(self.foreign_reference),
                       lambda: self.api.records(self.foreign)):
            with self.assertRaises(frappe.PermissionError):
                action()
        self.assertNotIn(self.foreign_reference, frappe.get_list("Cowork Stocktake Task", pluck="name"))
        for actor in ("Guest", self.manager, self.outsider):
            frappe.set_user(actor)
            with self.assertRaises(frappe.PermissionError):
                self.api.detail(self.reference)
        frappe.set_user(self.actor)
        frappe.conf.pearnly_features = []
        with self.assertRaises(frappe.PermissionError):
            get("Cowork Stocktake Task", self.reference)
        self.assertFalse(frappe.get_list("Cowork Stocktake Task", pluck="name"))
        frappe.conf.pearnly_features = self.original_features
        frappe.conf.pearnly_product = "dms"
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.reference)
        frappe.conf.pearnly_product = self.original_product
        frappe.db.delete("User Permission", {"name": self.grant})
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.reference)
        self.assertFalse(frappe.get_list("Cowork Stocktake Item", pluck="name"))

    def test_disabled_workspace_and_legacy_share_do_not_restore_scope(self):
        from frappe.client import get
        share = frappe.get_doc({"doctype": "DocShare", "share_doctype": "Cowork Stocktake Task",
            "share_name": self.foreign_reference, "user": self.actor, "read": 1})
        share.name = uuid4().hex
        share.db_insert()
        with self.assertRaises(frappe.PermissionError):
            get("Cowork Stocktake Task", self.foreign_reference)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list("Cowork Stocktake Task", pluck="name")
        frappe.db.set_value("Cowork Workspace", self.workspace, "enabled", 0)
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.reference)

    def test_new_share_rejected_even_for_admin(self):
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "DocShare", "share_doctype": "Cowork Stocktake Task",
                "share_name": self.reference, "everyone": 1, "read": 1}).insert()

    def test_legacy_task_does_not_accept_scan_writes(self):
        task = self.create()
        # Only a synthetic lab fixture uses the DB boundary to represent old data.
        # Production import/migration of legacy tasks is not provided by this batch.
        frappe.db.set_value("Cowork Stocktake Task", task["id"], "count_mode", "legacy")
        with self.error("stocktake.legacy_task"):
            self.add(task)
        with self.error("stocktake.legacy_task"):
            self.api.report(task["id"])
        self.assertNotIn("entries", self.api.detail(task["id"]))
        self.api.close(task["id"])

    def test_same_operation_on_another_task_is_atomic_conflict(self):
        first, second, operation = self.create(), self.create(), str(uuid4())
        self.add(first, operation=operation)
        with self.error("stocktake.operation_conflict"):
            self.add(second, operation=operation)
        self.assertEqual(self.api.entries(second["id"])["total"], 0)

    def test_concurrent_same_operation_and_distinct_entries_have_exact_total(self):
        task = self.create()
        frappe.db.commit()
        same = str(uuid4())
        for operations, expected in (([same, same], "1.000000"),
                                     ([str(uuid4()), str(uuid4())], "3.000000")):
            workers = [subprocess.Popen([sys.executable, __file__, "--worker", self.actor, task["id"],
                        task["items"][0]["id"], operation], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True) for operation in operations]
            try:
                for worker in workers:
                    self.assertEqual(worker.stdout.readline().strip(), "READY")
                for worker in workers:
                    worker.stdin.write("go\n")
                    worker.stdin.flush()
                outputs = [worker.communicate(timeout=45) for worker in workers]
                for worker, (output, error) in zip(workers, outputs):
                    self.assertEqual(worker.returncode, 0, output + error)
                if operations[0] == operations[1]:
                    self.assertEqual(*[json.loads(output.strip().splitlines()[-1]) for output, _ in outputs])
                frappe.db.rollback()
                self.assertEqual(self.api.detail(task["id"])["items"][0]["actual_qty"], expected)
                frappe.db.rollback()  # Release detail's task lock before next workers.
            finally:
                for worker in workers:
                    if worker.poll() is None:
                        worker.kill()
                        worker.communicate()

    def test_sites_do_not_share_task_rows(self):
        frappe.db.rollback()
        frappe.destroy()
        try:
            connect(OTHER_SITE)
            self.assertFalse(frappe.db.exists("Cowork Stocktake Task", self.reference))
            self.assertFalse(frappe.db.exists("Cowork Workspace", self.workspace))
        finally:
            frappe.destroy()
            connect()


def http_smoke():
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from frappe.app import application
    from pearnly_cowork.masters import api as masters
    suffix, actor = uuid4().hex, f"cw-stock-http-{uuid4().hex[:10]}@example.invalid"
    user(actor, ["Cowork Stocktake"])
    workspace = masters.create_workspace("Synthetic stocktake HTTP " + suffix)["name"]
    grant(actor, workspace)
    account = frappe.get_doc("User", actor)
    account.api_key, secret = uuid4().hex, uuid4().hex
    account.api_secret = secret
    account.save()
    headers = {"Authorization": f"token {account.api_key}:{secret}"}
    frappe.db.commit()
    frappe.destroy()
    client = Client(application, Response)
    rpc = "/api/method/pearnly_cowork.stocktake.api."
    def call(path, method="GET", data=None, authenticated=True):
        return client.open(path, base_url=f"http://{SITE}", method=method,
                           headers=headers if authenticated else {}, json=data)
    created = call(rpc + "create", "POST", {"workspace": workspace, "title": "Synthetic HTTP stocktake",
        "request_id": str(uuid4()), "header": HEADER, "rows": rows()})
    assert created.status_code == 200, created.get_data(as_text=True)
    task_id = created.json["message"]["id"]
    detail = call(rpc + "detail?task_id=" + task_id)
    assert detail.status_code == 200, detail.get_data(as_text=True)
    item_id = detail.json["message"]["items"][0]["id"]
    counted = call(rpc + "add_entry", "POST", {"task_id": task_id, "item_id": item_id,
        "request_id": str(uuid4()), "quantity": "2.500001", "warehouse": "A", "location": "01"})
    assert counted.status_code == 200, counted.get_data(as_text=True)
    rejected = call("/api/resource/Cowork Stocktake Item/" + item_id, "PUT",
        {"actual_qty": "999", "flags": {"stocktake_write": True}})
    assert rejected.status_code >= 400, rejected.get_data(as_text=True)
    actual = call(rpc + "detail?task_id=" + task_id).json["message"]["items"][0]["actual_qty"]
    assert actual == "2.500001", actual
    assert call(rpc + "detail?task_id=" + task_id, authenticated=False).status_code == 403
    assert call(rpc + "close?task_id=" + task_id).status_code == 403
    closed = call(rpc + "close", "POST", {"task_id": task_id})
    assert closed.status_code == 200, closed.get_data(as_text=True)
    report = call(rpc + "report?task_id=" + task_id)
    assert report.status_code == 200 and report.json["message"]["status"] == "closed"
    connect()
    print(json.dumps({"stocktake_http_create_count_close_report": "PASS", "native_put_rejected": rejected.status_code}))


if __name__ == "__main__":
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--worker":
            from pearnly_cowork.stocktake.api import add_entry
            frappe.set_user(sys.argv[2])
            frappe.db.count("Cowork Stocktake Entry")  # Deliberately establish an older MVCC snapshot.
            print("READY", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise SystemExit("Missing concurrency barrier")
            result = add_entry(sys.argv[3], sys.argv[4], sys.argv[5], "1", "A", "01")
            frappe.db.commit()
            print(json.dumps(result))
        elif len(sys.argv) > 1 and sys.argv[1] == "--http-smoke":
            http_smoke()
        else:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeStocktake)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        frappe.destroy()
