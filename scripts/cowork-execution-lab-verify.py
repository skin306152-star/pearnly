"""Run the real Core queue/checkpoints and Cowork engine against fake HTTP/billing.

Requires guarded internal-network Cowork lab, uses synthetic fixtures only.
Creates labelled local records, never starts a worker or contacts a provider.
Only transport/token/billing delivery are replaced. Native MariaDB and Redis
admission, budget, permissions, history, feedback and transaction paths run.
"""
from contextlib import ExitStack
import copy
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import frappe
import requests
import pymupdf
from PIL import Image

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-files-lab-verify.py")))
connect, user, grant, pdf = (BASE[k] for k in ("connect", "user", "grant", "pdf"))
GOOD = {"document_type": "receipt", "invoice_number": "IV69/100", "subtotal": "100",
        "vat": "7", "total_amount": "107", "date": "2026-09-21", "seller_name": "Synthetic seller"}


def response(value=GOOD, status=200):
    raw = {"candidates": [{"content": {"parts": [{"text": json.dumps(value)}]}, "finishReason": "STOP"}],
           "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}}
    result = requests.Response()
    result.status_code, result._content, result._content_consumed = status, json.dumps(raw).encode(), True
    return result


class NativeExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api
        suffix = uuid.uuid4().hex[:10]
        cls.actor, cls.other = [f"cw-execution-{role}-{suffix}@example.invalid" for role in ("actor", "other")]
        for actor in (cls.actor, cls.other):
            user(actor, ["Cowork Intake", "Cowork OCR History", "System Manager"])
        cls.workspace = api.create_workspace("Synthetic Execution " + suffix)["name"]
        cls.grant = grant(cls.actor, "Cowork Workspace", cls.workspace).name
        frappe.db.commit()

    def setUp(self):
        from pearnly_cowork.files import service
        from pearnly_cowork.ocr import api
        from pearnly_core.execution import work
        self.api, self.work = api, work
        frappe.set_user(self.actor)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.old_conf = copy.deepcopy(dict(frappe.conf))
        frappe.conf.update(pearnly_ocr_credentials="/synthetic/credential-reference.json",
            pearnly_tenant="synthetic-execution-" + uuid.uuid4().hex,
            pearnly_ocr_backend="vertex", pearnly_ocr_project="synthetic-" + uuid.uuid4().hex[:10],
            pearnly_ocr_location="asia-southeast1", pearnly_cowork_ocr_environment={
                "OCR_LLM_BACKEND": "vertex", "OCR_FEWSHOT_ENABLED": "1"},
            pearnly_cowork_ocr_policy={"mode": "economy"},
            pearnly_ocr_budget={"rpm": 600000})
        self.stack.enter_context(patch("pearnly_core.execution.work.kick", return_value=True))
        self.stack.enter_context(patch("pearnly_core.execution.dispatch.later"))
        self.stack.enter_context(patch("pearnly_core.ocr.access_token", return_value="synthetic-token"))
        self.status = self.stack.enter_context(patch("pearnly_core.billing_client.status", return_value={"allowed": True}))
        self.charge = self.stack.enter_context(patch("pearnly_core.billing_client.charge", return_value={"ok": True, "charged_thb": 1}))
        self.http = self.stack.enter_context(patch("pearnly_core.execution.provider.requests.post", side_effect=lambda *a, **kw: response()))
        image = io.BytesIO()
        Image.new("RGB", (80, 60), "white").save(image, format="PNG")
        self.files = service
        self.source = service.create(self.workspace, "synthetic-invoice.png", image.getvalue(), uuid.uuid4().hex)
        self.key = uuid.uuid4().hex
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        # Unknown fake HTTP outcomes intentionally retain Core permits. Remove
        # only this test's unique synthetic tenant's exact lease members; no real
        # request can still be running, and no other test/Site keys are cleared.
        from pearnly_core.execution import budget
        who, client = budget.tenant(), budget.redis()
        for member in client.zrange(budget.PREFIX + "active", 0, -1):
            if member.startswith(who + ":"):
                client.zrem(budget.PREFIX + "active", member)
                client.zrem(budget.PREFIX + "active:" + who, member.split(":", 1)[1])
        frappe.conf.clear()
        frappe.conf.update(self.old_conf)
        frappe.set_user("Administrator")
        # Revocation fixtures use native grants; restore only this suite's grant.
        if not frappe.db.exists("User Permission", self.grant):
            type(self).grant = grant(self.actor, "Cowork Workspace", self.workspace).name
            frappe.db.commit()
        frappe.clear_cache()

    def admit(self):
        result = self.api.submit(self.source["name"], self.key, direction="purchase")
        self.assertTrue(result["accepted"], result)
        return frappe.get_doc("Cowork OCR Batch", result["name"])

    def run_work(self, doc, retry=False):
        if retry:
            frappe.db.set_value("Pearnly OCR Work", doc.work_name, "next_run", None, update_modified=False)
            frappe.db.commit()
        self.work.run(doc.work_name)
        return self.api.detail(doc.name)

    def test_success_seals_history_and_stable_billing_once(self):
        doc = self.admit()
        result = self.run_work(doc)
        self.assertEqual((result["state"], result["billing_state"]), ("saved", "charged"))
        self.assertEqual(len(result["history_records"]), 1)
        history = frappe.get_doc("Cowork OCR History", result["history_records"][0])
        self.assertTrue(history.staged)
        self.assertTrue(frappe.db.get_value("Cowork Source File", self.source["name"], "binding_complete"))
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self.charge.call_count, 1)
        self.assertEqual(self.charge.call_args.kwargs["reference"], doc.billing_reference)
        self.assertEqual(self.charge.call_args.args, ("cowork", "pdf", 1))
        again = self.admit()
        self.assertEqual(doc.name, again.name)
        self.run_work(doc)
        self.api.retry_billing(doc.name)
        self.assertEqual((self.http.call_count, self.charge.call_count), (1, 1))
        saved = frappe.get_doc("Cowork OCR Batch", doc.name)
        evidence = json.loads(saved.provider_evidence)
        self.assertEqual((evidence[0]["input_tokens"], evidence[0]["output_tokens"]), (10, 20))
        self.assertNotIn("raw", evidence[0])
        self.assertNotIn("settings_json", result)

    def test_lost_billing_receipt_retries_only_accounting(self):
        charges, references = set(), []
        def lost(*args, **kwargs):
            ref = kwargs["reference"]
            references.append(ref)
            if ref not in charges:
                charges.add(ref)
                raise requests.Timeout("synthetic lost receipt")
            return {"ok": True, "duplicate": True, "charged_thb": 1}
        self.charge.side_effect = lost
        doc = self.admit()
        first = self.run_work(doc)
        self.assertEqual((first["state"], first["billing_state"]), ("saved", "pending"))
        self.assertEqual(frappe.db.get_value("Pearnly OCR Work", doc.work_name, "status"), "deferred")
        self.status.side_effect = AssertionError("saved receipt retry must skip balance/provider")
        final = self.run_work(doc, retry=True)
        self.assertEqual(final["history_records"], first["history_records"])
        self.assertEqual(final["billing_state"], "charged")
        self.assertEqual((len(charges), len(set(references)), self.http.call_count), (1, 1, 1))

    def test_saved_receipt_retry_after_work_expires(self):
        self.charge.return_value = {"ok": False}
        doc = self.admit()
        self.run_work(doc)
        frappe.db.set_value("Pearnly OCR Work", doc.work_name, "expires_at", "2000-01-01")
        self.run_work(doc, retry=True)
        self.assertEqual(frappe.db.get_value("Pearnly OCR Work", doc.work_name, "status"), "failed")
        self.charge.return_value = {"ok": True}
        result = self.api.retry_billing(doc.name)
        self.assertEqual((result["state"], result["billing_state"], self.http.call_count), ("saved", "charged", 1))

    def test_completed_first_page_survives_quota_without_repay(self):
        self.source = self.files.create(self.workspace, "synthetic-two.pdf", pdf(), uuid.uuid4().hex)
        self.http.side_effect = [response(), response(status=429), response()]
        doc = self.admit()
        first = self.run_work(doc)
        self.assertEqual(first["state"], "deferred")
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 0)
        # Delayed budget is tested by Core independently; this suite moves only
        # this unique synthetic project's cooldown to exercise resumed work now.
        with patch("pearnly_core.execution.budget.permit", side_effect=lambda *a, **kw: ExitStack()):
            final = self.run_work(doc, retry=True)
        self.assertEqual(final["state"], "saved")
        self.assertEqual(self.http.call_count, 3)
        self.assertEqual(self.charge.call_args.args[-1], 2)
        self.assertEqual(len(final["history_records"]), 1)

    def test_transport_unknown_never_replays(self):
        self.http.side_effect = requests.Timeout("synthetic")
        doc = self.admit()
        result = self.run_work(doc)
        self.assertEqual(result["state"], "uncertain")
        self.admit()
        self.run_work(doc)
        self.assertEqual(self.http.call_count, 1)
        self.charge.assert_not_called()

    def test_all_non_invoice_does_not_create_history_or_charge(self):
        self.http.side_effect = lambda *a, **kw: response({"is_not_invoice": True})
        doc = self.admit()
        result = self.run_work(doc)
        self.assertEqual((result["state"], result["billing_state"]), ("not_invoice", "not_billable"))
        self.assertEqual(result["history_records"], [])
        self.charge.assert_not_called()

    def test_empty_text_ocr_is_not_saved_or_billed(self):
        from pearnly_cowork.ocr.results import OcrResultError
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((40, 40), "Synthetic unreadable document without any retained invoice fields.\n" * 8)
            content = document.tobytes()
        self.source = self.files.create(self.workspace, "synthetic-empty.pdf", content, uuid.uuid4().hex)
        frappe.conf.pearnly_cowork_ocr_environment["OCR_FAST_PATH_ENABLED"] = "true"
        self.http.side_effect = lambda *a, **kw: response({})
        doc = self.admit()
        with self.assertRaises(OcrResultError):
            self.work.run(doc.work_name)
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 0)
        self.charge.assert_not_called()

    def test_fifty_physical_pages_have_complete_manifest_and_single_charge(self):
        with pymupdf.open() as document:
            for _ in range(50):
                page = document.new_page(width=120, height=120)
                page.insert_text((10, 20), "Synthetic page")
            content = document.tobytes()
        self.source = self.files.create(self.workspace, "synthetic-fifty.pdf", content, uuid.uuid4().hex)
        text = "Receipt IV69/100 subtotal 100 VAT 7 total 107 2026-09-21"
        vision = {"responses": [{"fullTextAnnotation": {"text": text, "pages": [{"width": 333, "height": 333,
            "blocks": [{"paragraphs": [{"words": [{"confidence": 1.0, "symbols": [{"text": c} for c in text]}]}]}]}]}}]}
        def http(url, **kwargs):
            result = response()
            if "vision.googleapis.com" in url:
                result._content = json.dumps(vision).encode()
            return result
        self.http.side_effect = http
        doc = self.admit()
        final = self.run_work(doc)
        self.assertEqual(final["state"], "saved", final)
        self.assertEqual(self.http.call_count, 100)
        self.assertEqual(self.charge.call_args.args[-1], 50)
        self.assertEqual(self.charge.call_count, 1)
        history = frappe.get_doc("Cowork OCR History", final["history_records"][0])
        self.assertEqual(json.loads(history.source_page_indices), list(range(1, 51)))
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 1)

    def test_concurrent_admission_has_one_batch_and_one_work(self):
        payload = json.dumps({"actor": self.actor, "source": self.source["name"], "key": self.key,
            "conf": {key: value for key, value in frappe.conf.items() if key.startswith("pearnly_")}})
        peers = [subprocess.Popen([sys.executable, __file__, "--admit-child"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for peer in peers:
            peer.stdin.write(payload)
            peer.stdin.close()
            peer.stdin = None
        results = []
        for peer in peers:
            stdout, stderr = peer.communicate(timeout=45)
            self.assertEqual(peer.returncode, 0, stderr[-3000:])
            results.append(json.loads(stdout))
        self.assertTrue(all(row["accepted"] for row in results))
        self.assertEqual(results[0]["name"], results[1]["name"])
        frappe.db.rollback()
        self.assertEqual(frappe.db.count("Cowork OCR Batch", {"source_file": self.source["name"]}), 1)
        self.assertEqual(frappe.db.count("Pearnly OCR Work", {"reference": results[0]["name"]}), 1)

    def test_http_boundary_and_safe_status_projection(self):
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        from frappe.app import application
        doc = self.admit()
        self.run_work(doc)
        frappe.set_user("Administrator")
        account = frappe.get_doc("User", self.actor)
        account.api_key, secret = uuid.uuid4().hex, uuid.uuid4().hex
        account.api_secret = secret
        account.save()
        headers = {"Authorization": f"token {account.api_key}:{secret}"}
        site = frappe.local.site
        frappe.db.commit()
        frappe.destroy()
        client = Client(application, Response)
        rpc = "/api/method/pearnly_cowork.ocr.api."
        try:
            detail = client.get(rpc + "detail?name=" + doc.name, base_url="http://" + site, headers=headers)
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json["message"]["state"], "saved")
            self.assertNotIn("settings_json", detail.json["message"])
            anonymous = client.get(rpc + "detail?name=" + doc.name, base_url="http://" + site)
            self.assertEqual(anonymous.status_code, 403)
            invalid_method = client.get(rpc + "submit", base_url="http://" + site, headers=headers)
            self.assertEqual(invalid_method.status_code, 403)
            native = client.get("/api/resource/Pearnly OCR Work/" + doc.work_name,
                                base_url="http://" + site, headers=headers)
            self.assertEqual(native.status_code, 403)
        finally:
            connect(site)

    def test_permission_revoke_before_execution_stops_provider(self):
        doc = self.admit()
        frappe.set_user("Administrator")
        frappe.delete_doc("User Permission", self.grant)
        frappe.db.commit()
        with self.assertRaises(frappe.PermissionError):
            self.work.run(doc.work_name)
        self.http.assert_not_called()
        self.charge.assert_not_called()
        self.assertEqual(frappe.session.user, "Administrator")

    def test_revoke_between_pages_keeps_checkpoint_but_no_more_calls(self):
        self.source = self.files.create(self.workspace, "synthetic-two.pdf", pdf(), uuid.uuid4().hex)
        def revoke(*args, **kwargs):
            old = frappe.session.user
            frappe.set_user("Administrator")
            frappe.delete_doc("User Permission", self.grant)
            frappe.db.commit()
            frappe.set_user(old)
            return response()
        self.http.side_effect = revoke
        doc = self.admit()
        with self.assertRaises(frappe.PermissionError):
            self.work.run(doc.work_name)
        self.assertEqual(self.http.call_count, 1)
        self.charge.assert_not_called()
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 0)

    def test_configuration_change_and_invalid_mode_reject_before_provider(self):
        doc = self.admit()
        frappe.conf.pearnly_ocr_project = "different-synthetic"
        with self.assertRaises(frappe.ValidationError):
            self.work.run(doc.work_name)
        self.http.assert_not_called()

    def test_auto_and_unknown_modes_do_not_silently_choose_b_tier(self):
        for mode in ("auto", "typo", "direct35"):
            frappe.conf.pearnly_cowork_ocr_policy = {"mode": mode, "defaults_by_plan": {"L": "enterprise"}}
            with self.assertRaises(frappe.ValidationError):
                self.api.submit(self.source["name"], self.key)
        self.status.assert_not_called()
        self.http.assert_not_called()

    def test_history_transaction_interruption_resumes_frozen_result(self):
        from pearnly_cowork.history import storage
        from pearnly_core.execution.errors import Deferred
        self.source = self.files.create(self.workspace, "synthetic-two.pdf", pdf(), uuid.uuid4().hex)
        self.http.side_effect = [response(), response(dict(GOOD, invoice_number="IV69/101"))]
        doc = self.admit()
        original = storage._create
        created = []
        def stop_second(*args, **kwargs):
            if created:
                raise Deferred("synthetic_storage_retry", 1)
            row = original(*args, **kwargs)
            created.append(row["name"])
            return row
        with patch.object(storage, "_create", side_effect=stop_second):
            first = self.run_work(doc)
        self.assertEqual(first["state"], "deferred")
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 0)
        saved = frappe.get_doc("Cowork OCR Batch", doc.name)
        self.assertEqual(saved.phase, "recognized")
        self.assertTrue(saved.result_json)
        self.charge.assert_not_called()
        final = self.run_work(doc, retry=True)
        self.assertEqual(final["state"], "saved")
        self.assertEqual(len(final["history_records"]), 2)
        self.assertEqual(self.http.call_count, 2)
        self.assertIn(created[0], final["history_records"])

    def test_local_role_cache_is_refreshed_before_next_paid_page(self):
        # Simulate another request's native User.save role/cache effects without
        # resetting this worker's already populated local roles cache.
        self.source = self.files.create(self.workspace, "synthetic-two.pdf", pdf(), uuid.uuid4().hex)
        self.assertIn("Cowork Intake", frappe.get_roles(self.actor))
        role = frappe.db.get_value("Has Role", {"parent": self.actor, "role": "Cowork Intake"}, "name")
        def revoke(*args, **kwargs):
            frappe.db.delete("Has Role", {"name": role})
            frappe.db.commit()
            frappe.cache.hdel("roles", self.actor)
            return response()
        self.http.side_effect = revoke
        doc = self.admit()
        try:
            with self.assertRaises(frappe.PermissionError):
                self.work.run(doc.work_name)
            self.assertEqual(self.http.call_count, 1)
            self.charge.assert_not_called()
        finally:
            frappe.set_user("Administrator")
            account = frappe.get_doc("User", self.actor)
            account.append("roles", {"role": "Cowork Intake"})
            account.save()
            frappe.db.commit()

    def test_frozen_authorized_feedback_reaches_text_prompt(self):
        from pearnly_cowork.history import api as history_api
        from pearnly_cowork.history.storage import create_recognized
        raw = [{"page_index": 1, "fields": dict(GOOD, seller_tax="0105559001671", invoice_number="BAD-OLD")}]
        row = create_recognized(self.workspace, raw, uuid.uuid4().hex, ai_raw=raw, source="manual", confidence="high")
        edited = copy.deepcopy(row["pages"])
        edited[0]["fields"]["invoice_number"] = "CORRECTED-OLD"
        history_api.save(row["name"], edited, row["record_version"])
        with pymupdf.open() as pdf_doc:
            page = pdf_doc.new_page()
            text = "Receipt IV69/100 subtotal 100 VAT 7 total 107 2026-09-21 0105559001671\n" * 6
            page.insert_text((40, 40), text)
            content = pdf_doc.tobytes()
        self.source = self.files.create(self.workspace, "synthetic-text.pdf", content, uuid.uuid4().hex)
        frappe.conf.pearnly_cowork_ocr_environment["OCR_FAST_PATH_ENABLED"] = "true"
        doc = self.admit()
        before = doc.examples_json
        self.run_work(doc)
        payload = self.http.call_args.kwargs["json"]
        self.assertIn("CORRECTED-OLD", json.dumps(payload))
        self.assertEqual(frappe.db.get_value("Cowork OCR Batch", doc.name, "examples_json"), before)
        self.assertEqual(self.http.call_count, 1)

    def test_balance_lookup_vs_insufficient_and_no_admission(self):
        for code in ("lookup_error", "insufficient_balance"):
            self.status.return_value = {"allowed": False, "error_code": code}
            result = self.api.submit(self.source["name"], self.key)
            self.assertEqual(result, {"accepted": False, "code": code})
            self.assertEqual(frappe.db.count("Cowork OCR Batch", {"source_file": self.source["name"]}), 0)
        self.http.assert_not_called()

    def test_foreign_guest_and_generic_evidence_reads_denied(self):
        doc = self.admit()
        for actor in (self.other, "Guest"):
            frappe.set_user(actor)
            with self.assertRaises(frappe.PermissionError):
                self.api.detail(doc.name)
        frappe.set_user(self.actor)
        for doctype, name in (("Cowork OCR Batch", doc.name), ("Pearnly OCR Work", doc.work_name)):
            with self.assertRaises(frappe.PermissionError):
                frappe.get_doc(doctype, name).check_permission("read")
            with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
                frappe.get_doc({"doctype": "DocShare", "share_doctype": doctype, "share_name": name,
                                "user": self.other, "read": 1}).insert(ignore_permissions=True)
        from pearnly_cowork.ocr.execution import access
        self.assertTrue(access.work_permission(frappe._dict(product="dms", kind="dms.identity")))

    def test_admin_cannot_reset_or_delete_paid_work_through_native_crud(self):
        doc = self.admit()
        frappe.set_user("Administrator")
        work_doc = frappe.get_doc("Pearnly OCR Work", doc.work_name)
        with self.assertRaises(frappe.PermissionError):
            work_doc.check_permission("read")
        work_doc.status, work_doc.checkpoints = "queued", "{}"
        work_doc.product, work_doc.kind = "dms", "dms.identity"
        with self.assertRaises(frappe.PermissionError):
            work_doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc("Pearnly OCR Work", doc.work_name, ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("Pearnly OCR Work", doc.work_name, "product"), "cowork")

    def test_legacy_share_upgrade_retains_rows_and_other_products(self):
        from pearnly_cowork.patches.v0_0_1_revoke_cowork_work_shares import execute
        doc = self.admit()
        frappe.set_user("Administrator")
        other = frappe.get_doc({"doctype": "Pearnly OCR Work", "name": uuid.uuid4().hex,
            "kind": "dms.synthetic", "product": "dms", "reference": "synthetic", "user": self.actor,
            "status": "queued", "arguments": "{}"}).insert(ignore_permissions=True)
        shares = []
        for name in (doc.work_name, other.name):
            share = frappe.get_doc({"doctype": "DocShare", "name": uuid.uuid4().hex,
                "share_doctype": "Pearnly OCR Work", "share_name": name,
                "everyone": 1, "read": 1, "write": 1, "share": 1, "submit": 1})
            share.db_insert()  # Explicitly simulate pre-upgrade invalid data.
            shares.append(share.name)
        execute()
        execute()
        fields = ["read", "write", "share", "submit"]
        self.assertEqual(frappe.db.get_value("DocShare", shares[0], fields), (0, 0, 0, 0))
        self.assertEqual(frappe.db.get_value("DocShare", shares[1], fields), (1, 1, 1, 1))
        self.assertTrue(all(frappe.db.exists("DocShare", name) for name in shares))

    def test_omitted_client_is_not_replaced_by_native_user_default(self):
        from pearnly_cowork.masters import api as masters
        frappe.set_user("Administrator")
        client = masters.create_client(self.workspace, "Synthetic Default " + uuid.uuid4().hex[:6])["name"]
        permission = grant(self.actor, "Cowork Client", client)
        frappe.db.commit()
        frappe.set_user(self.actor)
        try:
            self.source = self.files.create(self.workspace, "synthetic-no-client.pdf", pdf(), uuid.uuid4().hex)
            self.assertFalse(self.source["client"])
            doc = self.admit()
            self.assertFalse(doc.client)
            result = self.run_work(doc)
            for name in result["history_records"]:
                self.assertFalse(frappe.db.get_value("Cowork OCR History", name, "client"))
        finally:
            frappe.set_user("Administrator")
            frappe.delete_doc("User Permission", permission.name)
            frappe.db.commit()


if __name__ == "__main__":
    connect()
    try:
        if "--admit-child" in sys.argv:
            value = json.loads(sys.stdin.read())
            frappe.conf.update(value["conf"])
            frappe.set_user(value["actor"])
            from pearnly_cowork.ocr import api
            with patch("pearnly_core.billing_client.status", return_value={"allowed": True}), patch("pearnly_core.execution.work.kick"):
                print(json.dumps(api.submit(value["source"], value["key"], direction="purchase")))
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeExecution))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.destroy()
