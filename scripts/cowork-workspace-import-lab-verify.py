"""Guarded local workspace import checks. Synthetic data only, never external IO.

Per-test writes roll back; setup, HTTP, concurrency and upgrade fixtures persist.
Fresh install refuses an existing Site. No global Redis cleanup or worker start.
"""
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-classification-lab-verify.py")))
connect, user = BASE["connect"], BASE["user"]
FRESH_SITE = "cowork-backend-workspace.localhost"


def tax():
    return str(int(uuid4().hex, 16))[:13]


def snapshot():
    result = BASE["snapshot"]()
    columns = ["name", "display_name", "tax_id", "enabled", "owner", "creation", "modified", "modified_by"]
    rows = frappe.get_all("Cowork Workspace", fields=columns, order_by="name", limit_page_length=0)
    result["workspaces"] = {"count": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
    for name, table in (("workspace_full", "Cowork Workspace"), ("receipts", "Cowork Workspace Import")):
        if frappe.db.table_exists(table):
            rows = frappe.get_all(table, fields=["*"], order_by="name", limit_page_length=0)
            result[name] = {"count": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
    return result


class WorkspaceImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager, cls.reader = [f"cw-workspace-{role}-{uuid4().hex[:10]}@example.invalid" for role in ("manager", "reader")]
        user(cls.manager, ["Cowork Admin"])
        user(cls.reader, ["Cowork Master Data"])
        frappe.db.commit()

    def setUp(self):
        from pearnly_cowork.masters import api, workspace_api, workspace_import, workspaces
        self.api, self.rpc, self.service, self.w = api, workspace_api, workspace_import, workspaces
        self.conf = dict(frappe.conf)
        frappe.set_user(self.manager)
        self.rows = [{"row_index": 1, "name": "  บริษัท ทดสอบ  ", "tax_id": tax(),
            "branch": "18", "phone": "0812345678", "address": "Bangkok"},
            {"row_index": 2, "name": "No tax company"}]

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.clear()
        frappe.conf.update(self.conf)
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def commit(self, rows=None, key=None):
        return self.rpc.commit_workspace_import(self.rows if rows is None else rows, key or uuid4().hex)

    def test_preview_is_zero_write_and_has_valid_skip_error(self):
        self.api.create_workspace("Existing", self.rows[0]["tax_id"])
        before = snapshot()
        rows = self.rows + [{"name": "", "tax_id": tax()}, {"name": "Invalid", "tax_id": "123"}]
        result = self.rpc.preview_workspace_import(rows)
        self.assertEqual([r["status"] for r in result["preview"]], ["skip", "valid", "error", "error"])
        self.assertEqual(before, snapshot())

    def test_commit_preserves_fields_and_does_not_create_buyers_or_endpoints(self):
        counts = [frappe.db.count(table) for table in ("Cowork Client", "Cowork ERP Connection")]
        result = self.commit()
        row = frappe.get_doc("Cowork Workspace", result["results"][0]["id"])
        self.assertEqual((row.display_name, row.tax_id, row.branch, row.phone, row.address),
            ("บริษัท ทดสอบ", self.rows[0]["tax_id"], "18", "0812345678", "Bangkok"))
        self.assertEqual(frappe.db.get_value("Cowork Workspace", result["results"][1]["id"], "branch"), "สำนักงานใหญ่")
        self.assertEqual(counts, [frappe.db.count(table) for table in ("Cowork Client", "Cowork ERP Connection")])
        self.assertEqual(result["created"], 2)

    def test_repeat_key_covers_no_tax_and_different_body_is_rejected(self):
        key = uuid4().hex
        first = self.commit(key=key)
        second = self.commit(key=key)
        self.assertTrue(second.pop("replayed"))
        first.pop("replayed")
        self.assertEqual(first, second)
        with self.assertRaises(frappe.ValidationError):
            self.commit(rows=[{"name": "Different"}], key=key)

    def test_same_tax_in_batch_and_new_request_skips_without_overwriting(self):
        same = dict(self.rows[0], name="Duplicate", tax_id="-".join(self.rows[0]["tax_id"]))
        result = self.commit(rows=[self.rows[0], same])
        self.assertEqual([r["status"] for r in result["results"]], ["created", "skip"])
        self.assertEqual(self.commit(rows=[same])["skipped"], 1)
        self.assertEqual(frappe.db.get_value("Cowork Workspace", result["results"][0]["id"], "display_name"), "บริษัท ทดสอบ")

    def test_confirm_revalidates_changes_since_preview(self):
        self.assertEqual(self.rpc.preview_workspace_import(self.rows)["valid_count"], 2)
        self.api.create_workspace("Created since preview", self.rows[0]["tax_id"])
        self.assertEqual(self.commit()["skipped"], 1)

    def test_single_and_native_writes_share_tax_and_field_validation(self):
        self.api.create_workspace("Single", self.rows[0]["tax_id"])
        with self.assertRaises(self.w.DuplicateWorkspaceTax):
            self.api.create_workspace("Duplicate", "-".join(self.rows[0]["tax_id"]))
        with self.assertRaises(self.w.DuplicateWorkspaceTax):
            frappe.get_doc({"doctype": "Cowork Workspace", "display_name": "Native duplicate",
                "tax_id": self.rows[0]["tax_id"], "enabled": 1}).insert()
        for key, length in (("name", 201), ("phone", 51), ("branch", 121), ("address", 501)):
            row = {"name": "Too long", key: "x" * length}
            self.assertEqual(self.commit(rows=[row])["errors"], 1)
        with self.assertRaises(frappe.ValidationError):
            self.api.create_workspace("Too long", phone="x" * 51)

    def test_inactive_tax_can_be_reused_but_cannot_be_reactivated_as_duplicate(self):
        first = self.api.create_workspace("Inactive", self.rows[0]["tax_id"])
        doc = frappe.get_doc("Cowork Workspace", first["name"])
        doc.enabled = 0
        doc.save()
        self.assertEqual(self.commit(rows=[self.rows[0]])["created"], 1)
        doc.enabled = 1
        with self.assertRaises(self.w.DuplicateWorkspaceTax):
            doc.save()

    def test_expected_row_error_rolls_back_partial_row_and_continues(self):
        original = self.w.create
        def fail(**values):
            row = original(**values)
            if values["tax_id"]:
                raise frappe.ValidationError("synthetic after insert")
            return row
        with patch.object(self.w, "create", side_effect=fail):
            result = self.commit()
        self.assertEqual([r["status"] for r in result["results"]], ["error", "created"])
        self.assertFalse(frappe.db.exists("Cowork Workspace", {"tax_id": self.rows[0]["tax_id"]}))

    def test_native_tax_update_checks_other_workspaces_but_allows_own_identity(self):
        first = self.api.create_workspace("First", self.rows[0]["tax_id"])
        second = self.api.create_workspace("Second")
        doc = frappe.get_doc("Cowork Workspace", second["name"])
        doc.tax_id = self.rows[0]["tax_id"]
        with self.assertRaises(self.w.DuplicateWorkspaceTax):
            doc.save()
        own = frappe.get_doc("Cowork Workspace", first["name"])
        own.tax_id = "-".join(self.rows[0]["tax_id"])
        own.save()
        own.tax_id = ""
        own.save()

    def test_rpc_deadlock_retry_is_bounded_and_permission_is_not_retried(self):
        with patch.object(self.service, "commit", side_effect=[frappe.QueryDeadlockError(), {"ok": True}]) as service:
            self.assertEqual(self.commit(), {"ok": True})
            self.assertEqual(service.call_count, 2)
        for error, attempts in ((frappe.QueryDeadlockError, 3), (frappe.PermissionError, 1)):
            with patch.object(self.service, "commit", side_effect=error()) as service:
                with self.assertRaises(error):
                    self.commit()
                self.assertEqual(service.call_count, attempts)

    def test_unexpected_failure_propagates_instead_of_fake_partial_success(self):
        with patch.object(self.w, "create", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                self.commit()

    def test_permissions_and_feature_revocation_apply_before_receipt_replay(self):
        key = uuid4().hex
        self.commit(key=key)
        for product, features in (("erp", ["master_data"]), ("cowork", [])):
            frappe.conf.pearnly_product, frappe.conf.pearnly_features = product, features
            with self.assertRaises(frappe.PermissionError):
                self.commit(key=key)
        frappe.conf.update(self.conf)
        frappe.set_user(self.reader)
        for call in (lambda: self.commit(key=key), lambda: self.rpc.preview_workspace_import(self.rows)):
            with self.assertRaises(frappe.PermissionError):
                call()

    def test_native_receipts_cannot_be_read_mutated_deleted_or_shared(self):
        self.commit()
        name = frappe.db.get_value(self.service.DOCTYPE, {"owner": self.manager}, "name")
        doc = frappe.get_doc(self.service.DOCTYPE, name)
        doc.flags.cowork_workspace_import = True  # JSON cannot forge the private capability.
        for action in (lambda: doc.check_permission("read"), lambda: frappe.get_list(self.service.DOCTYPE),
                lambda: doc.save(ignore_permissions=True), lambda: frappe.delete_doc(self.service.DOCTYPE, name, ignore_permissions=True),
                lambda: self.service.reject_share(frappe._dict(share_doctype=self.service.DOCTYPE))):
            with self.assertRaises(frappe.PermissionError):
                action()

    def test_bad_envelopes_and_scope_injection_are_rejected(self):
        for rows in ({}, "bad", [None], [{"name": ["bad"]}], [{"name": "Bad", "tenant": "other"}],
                     [{"name": "Bad", "row_index": True}], [{"name": "X"}] * 501,
                     [{"name": "ก" * (3 * 1024 * 1024)}]):
            with self.assertRaises(frappe.ValidationError):
                self.commit(rows=rows)
        for key in ("", " " * 2, "x" * 129):
            with self.assertRaises(frappe.ValidationError):
                self.rpc.commit_workspace_import(self.rows, key)

    def test_old_buyer_import_keeps_destination(self):
        workspace = self.api.create_workspace("Parent")["name"]
        before = frappe.db.count("Cowork Workspace")
        result = self.api.commit_client_import(workspace, self.rows)
        self.assertEqual(result["created"], 2)
        self.assertEqual(frappe.db.count("Cowork Workspace"), before)
        self.assertEqual(frappe.db.count("Cowork Client", {"workspace": workspace}), 2)

    def test_maximum_batch_and_repeat_receipt(self):
        rows = [{"name": "Workspace batch " + str(i), "row_index": i} for i in range(500)]
        key = uuid4().hex
        self.assertEqual(self.rpc.preview_workspace_import(rows)["valid_count"], 500)
        first = self.commit(rows=rows, key=key)
        self.assertEqual(first["created"], 500)
        self.assertTrue(self.commit(rows=rows, key=key)["replayed"])

    def test_receipt_failure_rolled_back_by_caller_can_retry_without_duplicate(self):
        key = uuid4().hex
        with patch.object(self.service.ImportReceipt, "insert", side_effect=RuntimeError("synthetic receipt loss")):
            with self.assertRaises(RuntimeError):
                self.commit(key=key)
        frappe.db.rollback()  # Same boundary as a failed native HTTP request.
        self.assertFalse(frappe.db.exists("Cowork Workspace", {"tax_id": self.rows[0]["tax_id"]}))
        result = self.commit(key=key)
        self.assertEqual(result["created"], 2)


def http_checks():
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from frappe.app import application
    actor = f"cw-workspace-http-{uuid4().hex[:10]}@example.invalid"
    user(actor, ["Cowork Admin"])
    account = frappe.get_doc("User", actor)
    account.api_key, secret = uuid4().hex, uuid4().hex
    account.api_secret = secret
    account.save()
    site = frappe.local.site
    frappe.db.commit()
    frappe.destroy()
    client = Client(application, Response)
    headers = {"Authorization": f"token {account.api_key}:{secret}"}
    rpc = "/api/method/pearnly_cowork.masters.workspace_api."
    count = 0
    def call(path, method="POST", data=None, auth=True, upload=None):
        nonlocal count
        count += 1
        kwargs = {"data": upload} if upload else {"json": data}
        return client.open(path, base_url=f"http://{site}", method=method, headers=headers if auth else {}, **kwargs)
    for endpoint in ("parse_workspace_import", "preview_workspace_import", "commit_workspace_import"):
        assert call(rpc + endpoint, auth=False).status_code == 403
        assert call(rpc + endpoint, method="GET").status_code == 403
    upload = call(rpc + "parse_workspace_import", upload={"file": (io.BytesIO(b"name,tax id,phone\nSynthetic,,081\n"), "roster.csv")})
    assert upload.status_code == 200, upload.get_data(as_text=True)
    assert upload.json["message"]["valid_count"] == 1
    rows = upload.json["message"]["rows"]
    data = {"rows": rows, "request_key": uuid4().hex}
    first = call(rpc + "commit_workspace_import", data=data)
    assert first.status_code == 200, first.get_data(as_text=True)
    assert first.json["message"]["created"] == 1
    second = call(rpc + "commit_workspace_import", data=data)
    assert second.status_code == 200 and second.json["message"]["replayed"]
    assert call("/api/resource/Cowork Workspace Import", method="GET").status_code == 403
    connect()
    print(json.dumps({"http": "PASS", "requests": count, "anonymous": "3 x 403", "get_write": "3 x 403", "multipart_to_commit_replay": "PASS"}))


def concurrency(same_key):
    actor = f"cw-workspace-concurrency-{uuid4().hex[:10]}@example.invalid"
    user(actor, ["Cowork Admin"])
    frappe.db.commit()
    key = uuid4().hex
    row = {"name": "Workspace concurrency " + uuid4().hex, "tax_id": tax() if not same_key else ""}
    children = []
    try:
        for i in range(2):
            payload = {"actor": actor, "rows": [row], "key": key if same_key else key + str(i)}
            child = subprocess.Popen([sys.executable, __file__, "--worker", json.dumps(payload)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(child)
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("start\n")
            child.stdin.flush()
        outputs = []
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            outputs.append(json.loads(stdout.strip()))
        frappe.db.rollback()
        assert frappe.db.count("Cowork Workspace", {"display_name": row["name"]}) == 1
        assert sum(r["replayed"] for r in outputs) == (1 if same_key else 0)
        assert sum(r["skipped"] for r in outputs) == (0 if same_key else 1)
        print(json.dumps({"concurrency": "PASS", "same_key": same_key, "workspace_count": 1}))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    connect()
    try:
        if "--snapshot" in sys.argv:
            print(json.dumps(snapshot(), sort_keys=True))
        elif "--http" in sys.argv:
            http_checks()
        elif "--concurrency" in sys.argv:
            concurrency(False)
            concurrency(True)
        elif "--worker" in sys.argv:
            from pearnly_cowork.masters import workspace_api
            payload = json.loads(sys.argv[-1])
            assert payload["actor"].startswith("cw-workspace-concurrency-")
            frappe.set_user(payload["actor"])
            frappe.db.count("Cowork Workspace")  # establish an old RR snapshot
            print("ready", flush=True)
            assert sys.stdin.readline().strip() == "start"
            result = workspace_api.commit_workspace_import(payload["rows"], payload["key"])
            frappe.db.commit()
            print(json.dumps(result))
        elif "--install-fresh" in sys.argv:
            BASE["BASE"]["install_fresh"](site=FRESH_SITE)
        elif "--prepare-upgrade" in sys.argv:
            WorkspaceImport.setUpClass()
            case = WorkspaceImport()
            case.setUp()
            case.commit()
            frappe.db.commit()
            print(json.dumps(snapshot(), sort_keys=True))
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceImport))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        frappe.destroy()
