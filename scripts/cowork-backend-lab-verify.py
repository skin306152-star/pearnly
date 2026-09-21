"""Real Site verification. Only dedicated, isolated lab Sites are accepted.

Creates synthetic users/companies, keeps labelled fixtures in the lab volume.
No model, ERP, LINE, identity provider or production configuration is contacted.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import frappe

SITE = os.environ.get("PEARNLY_COWORK_TEST_SITE", "cowork-backend-a.localhost")
ALLOWED_SITES = {"cowork-backend-preparation.localhost", "cowork-backend-heartbeat.localhost", "cowork-backend-companion.localhost", "cowork-backend-push.localhost", "cowork-backend-roundtrip.localhost", "cowork-backend-table.localhost", "cowork-backend-execution.localhost", "cowork-backend-a.localhost", "cowork-backend-b.localhost",
                 "cowork-backend-fresh.localhost", "cowork-backend-files.localhost"}
OTHER_SITE = "cowork-backend-b.localhost" if SITE != "cowork-backend-b.localhost" else "cowork-backend-a.localhost"
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")


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


PAGES = [{"page_index": 1, "fields": {"invoice_number": "CW-001", "seller_name": "Synthetic",
          "date_raw": "20/09/2569", "subtotal": "100", "vat": "7",
          "total_amount": "107.00", "wht_amount": "3.00", "direction": "purchase",
          "unknown_evidence": {"retain": True}, "items": [{"name": "sample", "qty": "1"}]}}]


def user(name, roles):
    frappe.get_doc({"doctype": "User", "email": name, "first_name": "Cowork lab",
                    "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
                    "roles": [{"role": role} for role in roles]}).insert()


def grant(user_name, doctype, name):
    return frappe.get_doc({"doctype": "User Permission", "user": user_name,
                          "allow": doctype, "for_value": name, "apply_to_all_doctypes": 1}).insert()


def snapshot():
    tables = ("Cowork Workspace", "Cowork Client", "Cowork OCR History", "User Permission",
              "Cowork Source File", "Cowork Stocktake Task", "Cowork Stocktake Item",
              "Cowork Stocktake Entry", "Cowork Stocktake Operation", "Cowork OCR Batch",
              "Cowork OCR Correction", "Pearnly OCR Work")
    state = {table: frappe.get_all(table, fields=["*"], order_by="name", limit_page_length=0)
             for table in tables}
    # Include the eighth-batch ledger when rehearsing later upgrades. Older
    # guarded Sites may not have installed these DocTypes yet.
    for table in ("Cowork ERP Connection", "Cowork ERP Snapshot", "Cowork ERP Refresh",
                  "Cowork Push Record", "Cowork Push Attempt", "Cowork Push Writer Lock"):
        if frappe.db.exists("DocType", table):
            state[table] = frappe.get_all(table, fields=["*"], order_by="name", limit_page_length=0)
    state["users"] = frappe.get_all("User", filters={"name": ["like", "cw-%@example.invalid"]},
        fields=["name", "language", "time_zone", "enabled", "user_type"], order_by="name",
        limit_page_length=0)
    state["roles"] = frappe.get_all("Has Role", filters={"parent": ["like", "cw-%@example.invalid"]},
        fields=["parent", "role"], order_by="parent, role", limit_page_length=0)
    from pearnly_cowork.files.service import read_bytes
    state["source_bytes"] = [{"source": row.name, "sha256": hashlib.sha256(read_bytes(
        frappe.get_doc("Cowork Source File", row.name))).hexdigest()}
        for row in state["Cowork Source File"]]
    digest = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True,
                                       default=str).encode()).hexdigest()
    return {"site": SITE, "sha256": digest, "counts": {key: len(value) for key, value in state.items()}}


def http_smoke():
    """Exercise the native WSGI API in-process; no listening port or external IO."""
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from frappe.app import application
    from pearnly_cowork.masters import api as masters
    suffix = uuid.uuid4().hex
    actor = f"cw-http-{suffix[:10]}@example.invalid"
    user(actor, ["Cowork Intake"])
    workspace = masters.create_workspace("Synthetic HTTP " + suffix)["name"]
    grant(actor, "Cowork Workspace", workspace)
    account = frappe.get_doc("User", actor)
    account.api_key, secret = uuid.uuid4().hex, uuid.uuid4().hex
    account.api_secret = secret
    account.save()
    headers = {"Authorization": f"token {account.api_key}:{secret}"}
    frappe.db.commit()
    frappe.destroy()
    client = Client(application, Response)
    base = f"http://{SITE}"
    rpc = "/api/method/pearnly_cowork.history.api."
    def call(path, method="GET", data=None, authenticated=True):
        return client.open(path, base_url=base, method=method,
                           headers=headers if authenticated else {}, json=data)
    response = call(rpc + "create", "POST", {"workspace": workspace, "pages": PAGES, "request_key": suffix})
    if response.status_code != 200:
        raise AssertionError(f"HTTP create failed: {response.status_code}: {response.get_data(as_text=True)}")
    row = response.json["message"]
    detail = call(rpc + "detail?name=" + row["name"])
    assert detail.status_code == 200 and detail.json["message"]["invoice_no"] == "CW-001"
    changed = copy.deepcopy(PAGES)
    changed[0]["fields"]["invoice_number"] = "MUST-NOT-REPLACE"
    rejected = call("/api/resource/Cowork OCR History/" + row["name"], "PUT",
                    {"pages": json.dumps(changed)})  # No caller revision, exactly the native bypass attempt.
    assert rejected.status_code == 417 and rejected.json["exc_type"] == "ValidationError", rejected.status_code
    readback = call(rpc + "detail?name=" + row["name"])
    assert readback.status_code == 200 and readback.json["message"]["invoice_no"] == "CW-001"
    anonymous = call(rpc + "detail?name=" + row["name"], authenticated=False)
    assert anonymous.status_code == 403, anonymous.status_code
    assert call(rpc + "create").status_code == 403
    completed = call(rpc + "complete", "POST", {"records": [
        {"name": row["name"], "record_version": row["record_version"]}]})
    assert completed.status_code == 200 and completed.json["message"][0]["staged"] is False
    connect()
    print(json.dumps({"http_create_detail_complete": "PASS",
                      "native_put_without_revision": rejected.status_code,
                      "anonymous_read": anonymous.status_code}))


class NativeBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api as masters
        from pearnly_cowork.history import api
        cls.api = api
        suffix = uuid.uuid4().hex[:10]
        cls.actor = f"cw-{suffix}@example.invalid"
        cls.outsider = f"cw-other-{suffix}@example.invalid"
        cls.reader = f"cw-reader-{suffix}@example.invalid"
        cls.system_manager = f"cw-system-{suffix}@example.invalid"
        for name, roles in [(cls.actor, ["Cowork Intake", "Cowork OCR History"]),
                            (cls.outsider, ["Cowork Intake"]),
                            (cls.reader, ["Cowork OCR History"]),
                            (cls.system_manager, ["System Manager"])]:
            user(name, roles)
        cls.workspace = masters.create_workspace("Synthetic A " + suffix)["name"]
        cls.foreign = masters.create_workspace("Synthetic B " + suffix)["name"]
        cls.client = masters.create_client(cls.workspace, "Synthetic buyer")["name"]
        grant(cls.actor, "Cowork Workspace", cls.workspace)
        grant(cls.reader, "Cowork Workspace", cls.workspace)
        grant(cls.outsider, "Cowork Workspace", cls.foreign)
        cls.client_grant = grant(cls.actor, "Cowork Client", cls.client).name
        from pearnly_cowork.history.storage import create_recognized
        cls.reference = create_recognized(cls.workspace, PAGES, "admin-reference",
            source="manual", confidence="high", ai_raw={"first_snapshot": PAGES})["name"]
        cls.foreign_reference = api.create(cls.foreign, PAGES, "admin-reference")["name"]
        cls.buyer_reference = api.create(cls.workspace, PAGES, "buyer-reference", cls.client)["name"]
        frappe.db.commit()

    def setUp(self):
        frappe.set_user(self.actor)
        self.original_features = copy.deepcopy(frappe.conf.pearnly_features)
        self.original_product = frappe.conf.pearnly_product

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.pearnly_features = self.original_features
        frappe.conf.pearnly_product = self.original_product
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def create(self, key=None, **kwargs):
        return self.api.create(self.workspace, PAGES, key or uuid.uuid4().hex, **kwargs)

    def test_full_pages_summary_and_manual_provenance(self):
        row = self.create()
        self.assertEqual(row["total_amount"], "107.00")
        self.assertEqual(str(row["invoice_date"]), "2026-09-20")
        self.assertEqual(row["pages"][0]["fields"]["wht_amount"], "3.00")
        self.assertEqual(row["pages"][0]["fields"]["unknown_evidence"], {"retain": True})
        self.assertEqual(row["source"], "manual")
        self.assertIsNone(row["ai_raw"])
        self.assertEqual(row["quality_status"], "pending")
        self.assertEqual(row["record_version"], 1)
        self.assertIsNone(row["posting_kind"])

    def test_recognized_evidence_survives_edits(self):
        from pearnly_cowork.history import storage
        raw = {"pages": PAGES, "receipt": "original"}
        row = storage.create_recognized(self.workspace, PAGES, uuid.uuid4().hex,
                                        source="cowork_line", confidence="high", ai_raw=raw)
        changed = copy.deepcopy(PAGES)
        changed[0]["fields"]["total_amount"] = "200.00"
        saved = self.api.save(row["name"], changed, row["record_version"])
        self.assertEqual(saved["ai_raw"], raw)
        self.assertEqual(saved["total_amount"], "200.00")
        self.assertEqual(saved["edit_count"], 1)
        self.assertEqual(saved["record_version"], 2)
        self.assertEqual(saved["quality_status"], "confirmed")
        self.assertTrue(saved["staged"])

    def test_idempotency_and_different_payload_rejection(self):
        first = self.create("same-request")
        second = self.create("same-request")
        self.assertEqual(first["name"], second["name"])
        changed = copy.deepcopy(PAGES)
        changed[0]["fields"]["invoice_number"] = "DIFFERENT"
        with self.assertRaises(frappe.ValidationError):
            self.api.create(self.workspace, changed, "same-request")
        self.assertEqual(self.api.detail(first["name"])["invoice_no"], "CW-001")

    def test_stale_save_rejected(self):
        row = self.create()
        changed = copy.deepcopy(PAGES)
        changed[0]["fields"]["seller_name"] = "Revised"
        self.api.save(row["name"], changed, 1)
        with self.assertRaises(frappe.TimestampMismatchError):
            self.api.save(row["name"], PAGES, 1)
        self.assertEqual(self.api.detail(row["name"])["seller_name"], "Revised")

    def test_complete_is_atomic_and_repeated_completion_is_noop(self):
        row = self.create()
        with self.assertRaises(frappe.PermissionError):
            self.api.complete([{"name": row["name"], "record_version": 1},
                               {"name": self.foreign_reference, "record_version": 1}])
        self.assertTrue(self.api.detail(row["name"])["staged"])
        first = self.api.complete([{"name": row["name"], "record_version": 1}])[0]
        second = self.api.complete([{"name": row["name"], "record_version": 1}])[0]
        self.assertFalse(first["already_completed"])
        self.assertTrue(second["already_completed"])
        self.assertEqual(first["record_version"], second["record_version"])
        self.assertEqual(self.api.detail(row["name"])["confirmed_by"], self.actor)
        self.assertIn(row["name"], {r.name for r in self.api.records(self.workspace)})
        self.assertNotIn(row["name"], {r.name for r in self.api.records(self.workspace, staged=1)})
        self.assertFalse(frappe.db.count("Pearnly Push Log"))

    def test_late_batch_save_failure_rolls_back_the_first_save(self):
        from pearnly_cowork.pearnly_cowork.doctype.cowork_ocr_history.cowork_ocr_history import CoworkOCRHistory
        rows = sorted([self.create(), self.create()], key=lambda row: row["name"])
        original_save = CoworkOCRHistory.save
        def fail_second(doc, *args, **kwargs):
            if doc.name == rows[1]["name"]:
                raise frappe.ValidationError("Synthetic second-record failure")
            return original_save(doc, *args, **kwargs)
        with patch.object(CoworkOCRHistory, "save", fail_second):
            with self.assertRaises(frappe.ValidationError):
                self.api.complete([{"name": row["name"], "record_version": 1} for row in rows])
        for row in rows:
            current = self.api.detail(row["name"])
            self.assertTrue(current["staged"])
            self.assertEqual(current["record_version"], 1)
            self.assertIsNone(current["confirmed_at"])

    def test_cross_workspace_detail_list_write_denied(self):
        for operation in (lambda: self.api.detail(self.foreign_reference),
                          lambda: self.api.records(self.foreign, staged=1),
                          lambda: self.api.save(self.foreign_reference, PAGES, 1),
                          lambda: self.api.create(self.foreign, PAGES, "forbidden")):
            with self.assertRaises(frappe.PermissionError):
                operation()
        rows = frappe.get_list("Cowork OCR History", pluck="name")
        self.assertIn(self.reference, rows)
        self.assertNotIn(self.foreign_reference, rows)

    def test_client_grants_are_separate_and_revocation_is_immediate(self):
        self.assertEqual(self.api.detail(self.buyer_reference)["client"], self.client)
        frappe.db.delete("User Permission", {"name": self.client_grant})
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.buyer_reference)
        names = {r.name for r in self.api.records(self.workspace, staged=1)}
        self.assertNotIn(self.buyer_reference, names)
        self.assertIn(self.reference, names)

    def test_guest_reader_and_system_manager_do_not_gain_write_scope(self):
        for actor in ("Guest", self.system_manager, self.outsider):
            frappe.set_user(actor)
            with self.assertRaises(frappe.PermissionError):
                self.api.detail(self.reference)
        frappe.set_user(self.reader)
        self.assertEqual(self.api.detail(self.reference)["name"], self.reference)
        with self.assertRaises(frappe.PermissionError):
            self.create()

    def test_entitlement_and_product_are_enforced_on_native_reads(self):
        from frappe.client import get
        frappe.conf.pearnly_features = []
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.reference)
        with self.assertRaises(frappe.PermissionError):
            get("Cowork OCR History", self.reference)
        self.assertFalse(frappe.get_list("Cowork OCR History", pluck="name"))
        frappe.conf.pearnly_features = self.original_features
        frappe.conf.pearnly_product = "dms"
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.reference)

    def test_native_update_without_revision_and_forged_flags_rejected(self):
        from frappe.client import save
        doc = frappe.get_doc("Cowork OCR History", self.reference)
        payload = doc.as_dict()
        payload["pages"] = json.dumps(PAGES)
        payload["flags"] = {"cowork_history_update": True}
        with self.assertRaises(frappe.ValidationError):
            save(payload)
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "Cowork OCR History", "workspace": self.workspace,
                "source": "manual", "pages": json.dumps(PAGES), "ai_raw": "null",
                "request_key": "forged", "staged": 1,
                "flags": {"cowork_history_create": True}}).insert()

    def test_legacy_share_cannot_restore_revoked_scope(self):
        from frappe.client import get
        # Seed an old share directly in this test transaction to exercise upgrade safety.
        share = frappe.get_doc({"doctype": "DocShare", "share_doctype": "Cowork OCR History",
                "share_name": self.foreign_reference, "user": self.actor, "read": 1})
        share.name = uuid.uuid4().hex
        share.db_insert()
        with self.assertRaises(frappe.PermissionError):
            get("Cowork OCR History", self.foreign_reference)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list("Cowork OCR History", pluck="name")

    def test_new_docshare_is_rejected(self):
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "DocShare", "share_doctype": "Cowork OCR History",
                "share_name": self.foreign_reference, "user": self.actor, "read": 1}).insert()

    def test_everyone_share_is_also_rejected(self):
        from frappe.client import get
        share = frappe.get_doc({"doctype": "DocShare", "share_doctype": "Cowork OCR History",
                "share_name": self.foreign_reference, "everyone": 1, "read": 1})
        share.name = uuid.uuid4().hex
        share.db_insert()
        with self.assertRaises(frappe.PermissionError):
            get("Cowork OCR History", self.foreign_reference)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list("Cowork OCR History", pluck="name")

    def test_disabled_clients_disappear_from_native_lists(self):
        from frappe.client import get
        frappe.db.set_value("Cowork Client", self.client, "enabled", 0)
        self.assertNotIn(self.client, frappe.get_list("Cowork Client", pluck="name"))
        with self.assertRaises(frappe.PermissionError):
            get("Cowork Client", self.client)
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.buyer_reference)

    def test_unrelated_user_permission_does_not_grant_company_membership(self):
        frappe.db.set_value("User Permission",
            {"user": self.actor, "allow": "Cowork Workspace", "for_value": self.workspace},
            {"apply_to_all_doctypes": 0, "applicable_for": "Cowork Client"})
        with self.assertRaises(frappe.PermissionError):
            self.api.detail(self.reference)
        self.assertFalse(frappe.get_list("Cowork OCR History", pluck="name"))

    def test_master_pagination_can_reach_record_201(self):
        from pearnly_cowork.masters import api as masters
        frappe.set_user("Administrator")
        # db_insert keeps this fixture fast; the production API still validates masters.
        for index in range(201):
            doc = frappe.get_doc({"doctype": "Cowork Client", "workspace": self.workspace,
                                  "display_name": "Pagination", "enabled": 1})
            doc.name = uuid.uuid4().hex
            doc.db_insert()
        pages = [masters.clients(self.workspace, start=offset, limit=100) for offset in (0, 100, 200)]
        names = [r.name for page in pages for r in page]
        self.assertEqual(len(names), 202)
        self.assertEqual(len(set(names)), 202)

    def test_empty_update_and_internal_source_claim_are_rejected(self):
        row = self.create()
        with self.assertRaises(frappe.ValidationError):
            self.api.save(row["name"], [], 1)
        with self.assertRaises(TypeError):
            self.api.create(self.workspace, PAGES, "source", source="erp_web")

    def test_bad_json_dates_revisions_and_batch_names_return_validation_errors(self):
        row = self.create()
        for pages in ("{", [{"fields": {"date_raw": "not-a-date"}}], [{"fields": []}]):
            with self.assertRaises(frappe.ValidationError):
                self.api.save(row["name"], pages, 1)
        for revision in (None, True, -1, "1.5", "x"):
            with self.assertRaises(frappe.ValidationError):
                self.api.save(row["name"], PAGES, revision)
        for name in (None, [], "", 1):
            with self.assertRaises(frappe.ValidationError):
                self.api.complete([{"name": name, "record_version": 1}])

    def test_two_sites_have_independent_databases(self):
        frappe.db.rollback()
        frappe.destroy()
        try:
            connect(OTHER_SITE)
            self.assertFalse(frappe.db.exists("Cowork OCR History", self.reference))
            self.assertFalse(frappe.db.exists("Cowork Workspace", self.workspace))
            self.assertFalse(frappe.db.exists("User", self.actor))
        finally:
            frappe.destroy()
            connect()

    def test_concurrent_same_request_returns_one_record(self):
        key = uuid.uuid4().hex
        commands = [sys.executable, __file__, "--worker-create", self.actor, self.workspace, key]
        workers = [subprocess.Popen(commands, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
                   for _ in range(2)]
        for process in workers:
            self.assertEqual(process.stdout.readline().strip(), "READY")
        for process in workers:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = [process.communicate(timeout=45) for process in workers]
        for process, (out, error) in zip(workers, results):
            self.assertEqual(process.returncode, 0, out + error)
        names = [json.loads(out.strip().splitlines()[-1])["name"] for out, _ in results]
        self.assertEqual(names[0], names[1])
        self.assertEqual(frappe.db.count("Cowork OCR History", {"name": names[0]}), 1)


if __name__ == "__main__":
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--worker-create":
            from pearnly_cowork.history.api import create
            frappe.set_user(sys.argv[2])
            # Both connections establish a snapshot before either inserts.
            frappe.db.count("Cowork OCR History")
            print("READY", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise SystemExit("Missing concurrency barrier")
            result = create(sys.argv[3], PAGES, sys.argv[4])
            frappe.db.commit()
            print(json.dumps({"name": result["name"]}))
        elif len(sys.argv) > 1 and sys.argv[1] == "--snapshot":
            print(json.dumps(snapshot(), sort_keys=True))
        elif len(sys.argv) > 1 and sys.argv[1] == "--http-smoke":
            http_smoke()
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeBackend))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        frappe.destroy()
