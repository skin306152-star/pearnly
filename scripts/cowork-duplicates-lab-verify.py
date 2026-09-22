"""Duplicate reminders in guarded local Sites, synthetic data and no external IO.

Tests roll back per case; labelled setup/HTTP fixtures persist in the lab volume.
--snapshot hashes pre-existing evidence + preferences to verify schema upgrades.
--prepare-upgrade persists one synthetic user's disabled setting before migrate.
No models, ERP, LINE, production secrets, listeners, or background workers.
"""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-backend-lab-verify.py")))
connect, user, grant, PAGES = (BASE[key] for key in ("connect", "user", "grant", "PAGES"))
FRESH_SITE = "cowork-backend-duplicates.localhost"


def install_fresh(site=FRESH_SITE):
    """Called only after connect() proves the existing bench is our marked lab."""
    root = Path("/home/frappe/frappe-bench")
    if site not in {FRESH_SITE, "cowork-backend-classification.localhost"}:
        raise SystemExit("Unrecognized fresh test Site")
    if (root / "sites" / site).exists():
        raise SystemExit("Fresh test Site already exists; refusing to overwrite it")
    frappe.destroy()
    subprocess.run(["bench", "new-site", site, "--db-host", "db",
        "--db-root-password", "cowork-backend-lab-only", "--admin-password", "cowork-backend-lab-only",
        "--mariadb-user-host-login-scope", "%"], cwd=root, check=True)
    subprocess.run(["bench", "--site", site, "install-app", "pearnly_core", "pearnly_cowork"], cwd=root, check=True)
    # Frappe's own site-config writer; the exact new lab Site is task-owned.
    frappe.init(site=site, sites_path=str(root / "sites"))
    from frappe.installer import update_site_config
    config = {"pearnly_product": "cowork", "pearnly_backend_lab": True,
        "pearnly_features": ["intake", "ocr_history", "master_data", "push_log", "stocktake", "recon"],
        "pause_scheduler": 1, "disable_scheduler": 1, "mute_emails": 1}
    for key, value in config.items():
        update_site_config(key, value)
    frappe.destroy()
    print(json.dumps({"fresh_site": site, "installation": "PASS"}))


def snapshot():
    rows = frappe.get_all("Cowork OCR History", fields=["name", "owner", "workspace", "client", "pages",
        "ai_raw", "record_version", "staged", "request_fingerprint", "last_push_status"], order_by="name",
        limit_page_length=0)
    prefs = frappe.get_all("DefaultValue", filters={"defkey": "cowork_duplicate_check_enabled"},
        fields=["parent", "defvalue"], order_by="parent", limit_page_length=0)
    value = json.dumps([rows, prefs], sort_keys=True, default=str).encode()
    return {"history_count": len(rows), "preference_count": len(prefs), "sha256": hashlib.sha256(value).hexdigest()}


class DuplicateChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api
        suffix = uuid4().hex[:10]
        cls.actor, cls.other, cls.reader = [f"cw-duplicate-{role}-{suffix}@example.invalid"
            for role in ("actor", "other", "reader")]
        user(cls.actor, ["Cowork Intake"])
        user(cls.other, ["Cowork Intake"])
        user(cls.reader, ["Cowork OCR History"])
        cls.workspace = api.create_workspace("Duplicate lab " + suffix)["name"]
        cls.foreign = api.create_workspace("Duplicate other " + suffix)["name"]
        cls.client = api.create_client(cls.workspace, "Buyer A")["name"]
        cls.client_b = api.create_client(cls.workspace, "Buyer B")["name"]
        for actor in (cls.actor, cls.other, cls.reader):
            grant(actor, "Cowork Workspace", cls.workspace)
        grant(cls.actor, "Cowork Workspace", cls.foreign)
        cls.client_grant = grant(cls.actor, "Cowork Client", cls.client).name
        grant(cls.actor, "Cowork Client", cls.client_b)
        frappe.db.commit()

    def setUp(self):
        from pearnly_cowork.history import api, storage, duplicates
        self.api, self.storage, self.duplicates = api, storage, duplicates
        self.conf = dict(frappe.conf)
        frappe.set_user(self.actor)

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.clear()
        frappe.conf.update(self.conf)
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def create(self, fields=None, *, client=None, workspace=None, key=None):
        pages = copy.deepcopy(PAGES)
        if fields:
            pages[0]["fields"].update(fields)
        return self.storage.create_recognized(workspace or self.workspace, pages, key or uuid4().hex,
            client=client, source="manual", confidence="high", ai_raw={"synthetic": True})

    def test_exact_match_is_advisory_and_survives_save_and_complete(self):
        first = self.create()
        second = self.create({"invoice_number": "cw-001", "seller_name": "Different", "total_amount": "200"})
        self.assertEqual(second["duplicate_check_status"], "matched")
        self.assertEqual(second["duplicate_warning"]["level"], "exact")
        self.assertEqual(second["duplicate_warning"]["match"]["name"], first["name"])
        pages = copy.deepcopy(PAGES)
        pages[0]["fields"]["invoice_number"] = "edited"
        saved = self.api.save(second["name"], pages, 1)
        self.assertTrue(saved["duplicate_warning"]["at_creation"])
        self.api.complete([{"name": second["name"], "record_version": saved["record_version"]}])
        self.assertFalse(self.api.detail(second["name"])["staged"])
        self.assertEqual(self.api.detail(second["name"])["duplicate_warning"]["match"]["name"], first["name"])

    def test_likely_fallback_runs_even_when_number_is_different(self):
        first = self.create()
        second = self.create({"invoice_number": "different", "seller_name": "SYNTHETIC", "total_amount": "107.000"})
        self.assertEqual(second["duplicate_warning"]["level"], "likely")
        self.assertEqual(second["duplicate_warning"]["match"]["name"], first["name"])

    def test_exact_older_record_wins_over_newer_likely_match(self):
        first = self.create()
        self.create({"invoice_number": "different"})
        result = self.create()
        self.assertEqual(result["duplicate_warning"]["level"], "exact")
        self.assertEqual(result["duplicate_warning"]["match"]["name"], first["name"])

    def test_no_match_incomplete_and_zero_values(self):
        self.create()
        result = self.create({"invoice_number": "", "seller_name": ""})
        self.assertEqual(result["duplicate_check_status"], "clear")
        self.assertIsNone(result["duplicate_warning"])
        self.create({"invoice_number": "", "total_amount": "0"})
        self.assertEqual(self.create({"invoice_number": "", "total_amount": "0.00"})["duplicate_warning"]["level"], "likely")

    def test_company_client_author_and_unassigned_boundaries(self):
        self.create(workspace=self.foreign)
        self.assertIsNone(self.create(client=self.client)["duplicate_warning"])
        self.assertIsNone(self.create(client=self.client_b)["duplicate_warning"])
        frappe.set_user(self.other)
        self.assertIsNone(self.create()["duplicate_warning"])
        frappe.set_user(self.actor)
        # No client selected permits authorized clients in this company.
        self.assertIsNotNone(self.create()["duplicate_warning"])
        # Assigned records also see this author's unassigned records.
        self.assertIsNotNone(self.create(client=self.client_b)["duplicate_warning"])

    def test_revoked_client_is_neither_a_candidate_nor_exposed_in_warning(self):
        first = self.create(client=self.client)
        second = self.create()
        self.assertEqual(second["duplicate_warning"]["match"]["name"], first["name"])
        frappe.set_user("Administrator")
        frappe.delete_doc("User Permission", self.client_grant)
        frappe.set_user(self.actor)
        frappe.clear_cache()
        self.assertIsNone(self.api.detail(second["name"])["duplicate_warning"]["match"])
        # Change the visible record's number/amount so it cannot match the next invoice.
        pages = copy.deepcopy(PAGES)
        pages[0]["fields"].update(invoice_number="changed", total_amount="1")
        self.api.save(second["name"], pages, 1)
        self.assertIsNone(self.create()["duplicate_warning"])

    def test_setting_is_default_on_per_user_strict_and_idempotent(self):
        self.assertTrue(self.api.duplicate_setting()["enabled"])
        self.api.save_duplicate_setting(False)
        self.api.save_duplicate_setting("0")
        self.assertFalse(self.api.duplicate_setting()["enabled"])
        self.assertEqual(frappe.db.count("DefaultValue", {"parent": self.actor, "defkey": self.duplicates.PREFERENCE}), 1)
        self.assertEqual(self.create()["duplicate_check_status"], "disabled")
        frappe.set_user(self.other)
        self.assertTrue(self.api.duplicate_setting()["enabled"])
        for value in (None, "", "nope", 2, [], {}):
            with self.assertRaises(frappe.ValidationError):
                self.api.save_duplicate_setting(value)
        with self.assertRaises(TypeError):
            self.api.save_duplicate_setting(False, user=self.actor)

    def test_replayed_request_does_not_recheck_or_change_warning(self):
        self.create()
        key = uuid4().hex
        first = self.create(key=key)
        self.api.save_duplicate_setting(False)
        with patch.object(self.duplicates, "check", side_effect=AssertionError("must not recheck")):
            again = self.create(key=key)
        self.assertEqual(first["name"], again["name"])
        self.assertEqual(first["duplicate_warning"], again["duplicate_warning"])

    def test_manual_creation_does_not_check_or_accept_forged_warning(self):
        self.create()
        with patch.object(self.duplicates, "check", side_effect=AssertionError("manual")):
            row = self.api.create(self.workspace, PAGES, uuid4().hex)
        self.assertEqual(row["duplicate_check_status"], "not_checked")
        doc = frappe.get_doc("Cowork OCR History", row["name"])
        doc.duplicate_check_status = "matched"
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_query_failure_is_unavailable_not_clear_and_permission_errors_propagate(self):
        with patch.object(self.duplicates, "_find", side_effect=RuntimeError("synthetic")):
            row = self.create()
        self.assertEqual(row["duplicate_check_status"], "unavailable")
        self.assertTrue(frappe.db.exists("Cowork OCR History", row["name"]))
        for error in (frappe.PermissionError, frappe.QueryDeadlockError):
            with patch.object(self.duplicates, "_find", side_effect=error):
                with self.assertRaises(error):
                    self.create()

    def test_feature_role_product_and_company_denials(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            self.api.duplicate_setting()
        with self.assertRaises(frappe.PermissionError):
            self.api.save_duplicate_setting(False)
        frappe.set_user(self.reader)
        self.assertTrue(self.api.duplicate_setting()["enabled"])
        with self.assertRaises(frappe.PermissionError):
            self.create()
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError):
            self.create(workspace=self.foreign)
        frappe.set_user(self.actor)
        frappe.conf.pearnly_features = []
        with self.assertRaises(frappe.PermissionError):
            self.api.duplicate_setting()
        frappe.conf.pearnly_features = self.conf["pearnly_features"]
        frappe.conf.pearnly_product = "erp"
        with self.assertRaises(frappe.PermissionError):
            self.api.save_duplicate_setting(False)

    def test_mariadb_collation_does_not_create_false_accent_or_precision_matches(self):
        self.create({"invoice_number": "CAFÉ", "seller_name": "Café", "total_amount": "9007199254740993.0000000000000000001"})
        self.assertIsNone(self.create({"invoice_number": "CAFE", "seller_name": "Cafe"})["duplicate_warning"])
        self.assertIsNone(self.create({"invoice_number": "other", "seller_name": "Café",
            "total_amount": "9007199254740993.0000000000000000002"})["duplicate_warning"])

    def test_batch_persistence_reports_same_batch_duplicate_and_replays_once(self):
        import pymupdf
        from pearnly_cowork.files import service
        from pearnly_cowork.history import recognition
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.new_page()
            content = pdf.tobytes()
        source = service.create(self.workspace, "duplicate-lab.pdf", content, uuid4().hex)
        groups = []
        for index in (1, 2):
            pages = copy.deepcopy(PAGES)
            pages[0]["page_index"] = index
            groups.append({"workspace": self.workspace, "pages": pages, "source": "manual",
                "confidence": "high", "ai_raw": {}, "source_page_indices": [index]})
        first = recognition.persist_batch(source["name"], groups, "batch")
        self.assertEqual(first[0]["duplicate_check_status"], "clear")
        self.assertEqual(first[1]["duplicate_warning"]["match"]["name"], first[0]["name"])
        replay = recognition.persist_batch(source["name"], groups, "batch")
        # In-memory dates are strings before insertion and date objects after
        # reload; compare the actual JSON contract exposed by Frappe.
        self.assertEqual(json.dumps(replay, default=str, sort_keys=True),
                         json.dumps(first, default=str, sort_keys=True))


def http_checks():
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from frappe.app import application
    from pearnly_cowork.masters import api as masters
    from pearnly_cowork.history import storage
    actor = f"cw-duplicate-http-{uuid4().hex[:10]}@example.invalid"
    user(actor, ["Cowork Intake"])
    workspace = masters.create_workspace("Duplicate HTTP " + uuid4().hex)["name"]
    grant(actor, "Cowork Workspace", workspace)
    account = frappe.get_doc("User", actor)
    account.api_key, secret = uuid4().hex, uuid4().hex
    account.api_secret = secret
    account.save()
    headers = {"Authorization": f"token {account.api_key}:{secret}"}
    frappe.set_user(actor)
    storage.create_recognized(workspace, PAGES, "first", source="manual", confidence="high", ai_raw={})
    row = storage.create_recognized(workspace, PAGES, "second", source="manual", confidence="high", ai_raw={})
    site = frappe.local.site
    frappe.db.commit()
    frappe.destroy()
    client = Client(application, Response)
    rpc = "/api/method/pearnly_cowork.history.api."

    def call(path, method="GET", data=None, authenticated=True):
        return client.open(path, base_url=f"http://{site}", method=method,
            headers=headers if authenticated else {}, json=data)

    assert call(rpc + "duplicate_setting").json["message"] == {"enabled": True}
    assert call(rpc + "save_duplicate_setting", "POST", {"enabled": False}).status_code == 200
    assert call(rpc + "duplicate_setting").json["message"] == {"enabled": False}
    assert call(rpc + "save_duplicate_setting?enabled=1").status_code == 403
    assert call(rpc + "duplicate_setting", authenticated=False).status_code == 403
    assert call(rpc + "save_duplicate_setting", "POST", {"enabled": True}, False).status_code == 403
    detail = call(rpc + "detail?name=" + row["name"])
    assert detail.status_code == 200 and detail.json["message"]["duplicate_warning"]["level"] == "exact"
    assert "duplicate_match" not in detail.json["message"]
    resource = call("/api/resource/Cowork OCR History/" + row["name"])
    assert resource.status_code == 200, resource.status_code
    assert not resource.json["data"].get("duplicate_match"), resource.json["data"].keys()
    assert call("/api/resource/Cowork OCR History/" + row["name"], "PUT", {"duplicate_check_status": "clear"}).status_code == 417
    listed = call(rpc + "records?workspace=" + workspace + "&staged=1")
    assert listed.status_code == 200 and any(r["duplicate_level"] == "exact" for r in listed.json["message"])
    connect()
    print(json.dumps({"http": "PASS", "anonymous": [403, 403], "get_write": 403,
        "native_write": 417, "private_reference_hidden": True}))


if __name__ == "__main__":
    connect()
    try:
        if "--install-fresh" in sys.argv:
            install_fresh()
        elif "--prepare-upgrade" in sys.argv:
            from pearnly_cowork.history.duplicates import save_setting
            actor = f"cw-duplicate-upgrade-{uuid4().hex[:10]}@example.invalid"
            user(actor, ["Cowork Intake"])
            frappe.set_user(actor)
            save_setting(False)
            frappe.db.commit()
            print(json.dumps(snapshot(), sort_keys=True))
        elif "--snapshot" in sys.argv:
            print(json.dumps(snapshot(), sort_keys=True))
        elif "--http" in sys.argv:
            http_checks()
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DuplicateChecks))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        frappe.destroy()
