"""Synthetic correction-store integration checks; writes only marked Cowork labs.

Run with the dedicated lab's bench Python after the parent has migrated its Site
and connected History.save / permission / DocShare hooks. Never imports legacy
data, invokes a model, sends messages or calls an external service.
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch
import uuid

import frappe

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-backend-lab-verify.py")))
TAX = "0105555555555"
PAGES = deepcopy(BASE["PAGES"])
PAGES[0]["fields"]["seller_tax"] = TAX
# Real recognition persists its first final normalized pages as ai_raw. Keeping
# only date_raw in this fixture would itself be a date correction on every edit.
PAGES[0]["fields"]["date"] = "2026-09-20"


class NativeFeedback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api as masters
        from pearnly_cowork.history import api, storage
        from pearnly_cowork.ocr.feedback import access, model, service
        cls.api, cls.storage, cls.access, cls.model, cls.service = api, storage, access, model, service
        cls.suffix = uuid.uuid4().hex[:10]
        cls.actor = "cw-feedback-" + cls.suffix + "@example.invalid"
        cls.outsider = "cw-feedback-out-" + cls.suffix + "@example.invalid"
        cls.reader = "cw-feedback-read-" + cls.suffix + "@example.invalid"
        for name, roles in [(cls.actor, ["Cowork Intake", "Cowork OCR History"]),
                            (cls.outsider, ["Cowork Intake"]), (cls.reader, ["Cowork OCR History"])]:
            BASE["user"](name, roles)
        cls.workspace = masters.create_workspace("Synthetic feedback " + cls.suffix)["name"]
        cls.foreign = masters.create_workspace("Synthetic foreign feedback " + cls.suffix)["name"]
        cls.client = masters.create_client(cls.workspace, "Synthetic feedback buyer")["name"]
        for actor, company in [(cls.actor, cls.workspace), (cls.reader, cls.workspace),
                               (cls.outsider, cls.foreign)]:
            BASE["grant"](actor, "Cowork Workspace", company)
        cls.client_grant = BASE["grant"](cls.actor, "Cowork Client", cls.client).name
        frappe.db.commit()  # Explicit test fixtures only; service never commits.

    def setUp(self):
        frappe.set_user(self.actor)
        self.features = deepcopy(frappe.conf.pearnly_features)

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.pearnly_features = self.features
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def create(self, client=None):
        row = self.storage.create_recognized(self.workspace, deepcopy(PAGES), uuid.uuid4().hex,
            source="manual", confidence="high", ai_raw=deepcopy(PAGES), client=client)
        # Frappe user defaults must never silently replace an explicit no-client
        # scope with the user's sole permitted client.
        self.assertEqual(row["client"] or "", client or "")
        return row

    def edit(self, row, **fields):
        changed = deepcopy(row["pages"])
        changed[0]["fields"].update(fields or {"vat": "8"})
        return self.api.save(row["name"], changed, row["record_version"])

    def rows(self, history):
        return frappe.get_all(self.access.DOCTYPE, filters={"source_history": history},
                              fields=["*"], order_by="history_revision, field_name")

    def test_first_ai_baseline_and_aggregated_latest_revision(self):
        row = self.create()
        saved = self.edit(row)
        again = self.edit(saved, vat="9")
        self.assertEqual(again["ai_raw"], PAGES)
        rows = self.rows(row["name"])
        self.assertEqual([(r.ai_value, r.corrected_value) for r in rows], [("7", "8"), ("7", "9")])
        frozen = self.service.snapshot_for_scope(self.workspace)
        examples = frozen["body"]["examples_by_tax"][TAX]
        self.assertEqual([(r["use_count"], r["corrected_value"]) for r in examples], [(2, "9")])
        self.assertEqual(self.service.validate_snapshot(frozen), frozen["body"])

    def test_same_saved_revision_replay_does_not_increment(self):
        row = self.create()
        before = frappe.get_doc(self.storage.DOCTYPE, row["name"])
        self.edit(row)
        current = frappe.get_doc(self.storage.DOCTYPE, row["name"])
        current._doc_before_save = before
        self.assertEqual(self.service.capture_saved_edit(current), {"status": "replayed", "count": 0})
        self.assertEqual(len(self.rows(row["name"])), 1)

    def test_unchanged_complete_manual_and_clearing_do_not_learn(self):
        row = self.create()
        saved = self.edit(row, vat="")
        self.api.complete([{"name": row["name"], "record_version": saved["record_version"]}])
        self.assertFalse(self.rows(row["name"]))
        manual = self.api.create(self.workspace, PAGES, uuid.uuid4().hex)
        self.edit(manual)
        self.assertFalse(self.rows(manual["name"]))

    def test_exact_client_scope_has_no_tax_only_fallback(self):
        root = self.create()
        client = self.create(self.client)
        self.edit(root, vat="8")
        self.edit(client, vat="99")
        broad = self.service.snapshot_for_scope(self.workspace)["body"]["examples_by_tax"][TAX]
        restricted = self.service.snapshot_for_scope(self.workspace, self.client)["body"]["examples_by_tax"][TAX]
        self.assertEqual([r["corrected_value"] for r in broad], ["8"])
        self.assertEqual([r["corrected_value"] for r in restricted], ["99"])
        frappe.set_user(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            self.service.snapshot_for_scope(self.workspace)
        self.assertEqual(self.service.snapshot_for_scope(self.foreign)["body"]["examples_by_tax"], {})

    def test_current_feature_role_user_and_client_are_rechecked(self):
        row = self.create(self.client)
        self.edit(row)
        frozen = self.service.snapshot_for_scope(self.workspace, self.client)
        frappe.set_user(self.reader)
        with self.assertRaises(frappe.PermissionError):
            self.service.snapshot_for_scope(self.workspace)
        frappe.set_user(self.actor)
        frappe.conf.pearnly_features = []
        with self.assertRaises(frappe.PermissionError):
            self.service.validate_snapshot(frozen)
        frappe.conf.pearnly_features = self.features
        frappe.db.set_value("User", self.actor, "enabled", 0)
        with self.assertRaises(frappe.PermissionError):
            self.service.validate_snapshot(frozen)
        frappe.db.set_value("User", self.actor, "enabled", 1)
        frappe.db.delete("User Permission", self.client_grant)
        with self.assertRaises(frappe.PermissionError):
            self.service.validate_snapshot(frozen)

    def test_snapshot_stays_frozen_after_a_new_edit(self):
        saved = self.edit(self.create())
        frozen = self.service.snapshot_for_scope(self.workspace)
        self.edit(saved, vat="11")
        validated = self.service.validate_snapshot(frozen)
        self.assertEqual(validated["examples_by_tax"][TAX][0]["corrected_value"], "8")
        self.assertEqual(validated["examples_by_tax"][TAX][0]["use_count"], 1)

    def test_hash_is_not_authority_source_actor_site_and_values_rechecked(self):
        self.edit(self.create())
        original = self.service.snapshot_for_scope(self.workspace)
        for key, value in (("actor", self.outsider), ("site", "foreign.localhost"), ("workspace", self.foreign)):
            body = deepcopy(original["body"])
            body[key] = value
            with self.assertRaises(frappe.PermissionError):
                self.service.validate_snapshot(self.model.snapshot(body))
        body = deepcopy(original["body"])
        body["examples_by_tax"][TAX][0]["corrected_value"] = "injected"
        with self.assertRaises(frappe.PermissionError):
            self.service.validate_snapshot(self.model.snapshot(body))

    def test_missing_or_reassigned_source_history_cannot_authorize_old_samples(self):
        row = self.edit(self.create())
        frozen = self.service.snapshot_for_scope(self.workspace)
        frappe.db.set_value(self.storage.DOCTYPE, row["name"], "workspace", self.foreign)
        with self.assertRaises(frappe.PermissionError):
            self.service.validate_snapshot(frozen)
        self.assertEqual(self.service.snapshot_for_scope(self.workspace)["body"]["examples_by_tax"], {})
        frappe.db.delete(self.storage.DOCTYPE, row["name"])
        with self.assertRaises(frappe.PermissionError):
            self.service.validate_snapshot(frozen)

    def test_ordinary_feedback_failure_rolls_back_only_partial_examples(self):
        row = self.create()
        real_insert = self.access.insert
        count = 0
        def fail_second(body):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("sensitive synthetic payload must never be logged")
            return real_insert(body)
        with patch.object(self.access, "insert", side_effect=fail_second), patch.object(frappe, "logger") as log:
            saved = self.edit(row, vat="8", seller_name="Changed synthetic seller")
        self.assertEqual(saved["record_version"], 2)
        self.assertEqual(saved["pages"][0]["fields"]["vat"], "8")
        self.assertFalse(self.rows(row["name"]))
        self.assertEqual(log.return_value.warning.call_args.args[-1], "RuntimeError")
        self.assertNotIn("sensitive", repr(log.return_value.warning.call_args))
        self.assertEqual(self.api.detail(row["name"])["record_version"], 2)

    def test_enclosing_failure_rolls_back_invoice_edit_and_feedback(self):
        row = self.create()
        frappe.db.savepoint("feedback_outer_test")
        self.edit(row)
        self.assertTrue(self.rows(row["name"]))
        frappe.db.rollback(save_point="feedback_outer_test")
        self.assertFalse(self.rows(row["name"]))
        self.assertEqual(self.api.detail(row["name"])["record_version"], 1)

    def test_transaction_fatal_error_reaches_request_boundary(self):
        row = self.create()
        # Test the internal boundary directly, avoiding the RPC's full retries.
        with patch.object(self.access, "insert", side_effect=frappe.QueryDeadlockError), \
                patch.object(frappe, "logger"):
            with self.assertRaises(frappe.QueryDeadlockError):
                self.storage.update_pages(row["name"], [{**PAGES[0],
                    "fields": {**PAGES[0]["fields"], "vat": "8"}}], 1)

    def test_native_crud_and_forged_flags_are_denied(self):
        from frappe.client import get, set_value
        row = self.edit(self.create())
        event = self.rows(row["name"])[0]
        with self.assertRaises(frappe.PermissionError):
            get(self.access.DOCTYPE, event.name)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list(self.access.DOCTYPE, pluck="name")

        with self.assertRaises(frappe.PermissionError):
            set_value(self.access.DOCTYPE, event.name, "corrected_value", "intrusion")
        doc = frappe.get_doc(self.access.DOCTYPE, event.name)
        doc.flags.ignore_validate = True
        doc.flags.cowork_correction_insert = True
        doc.corrected_value = "intrusion"
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(self.access.DOCTYPE, event.name, ignore_permissions=True)
        new = frappe.get_doc({**event, "name": None, "doctype": self.access.DOCTYPE})
        new.flags.ignore_validate = True
        new.flags.cowork_correction_insert = True
        with self.assertRaises(frappe.PermissionError):
            new.insert(ignore_permissions=True)
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.PermissionError):
            get(self.access.DOCTYPE, event.name)

    def test_legacy_guest_share_cannot_open_native_list(self):
        row = self.edit(self.create())
        event = self.rows(row["name"])[0]
        share = frappe.get_doc({"doctype": "DocShare", "share_doctype": self.access.DOCTYPE,
                                "share_name": event.name, "user": "Guest", "read": 1})
        share.name = uuid.uuid4().hex
        share.db_insert()
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list(self.access.DOCTYPE, pluck="name")

    def test_share_and_legacy_everyone_share_cannot_open_native_access(self):
        from frappe.client import get
        row = self.edit(self.create())
        event = self.rows(row["name"])[0]
        share = frappe.get_doc({"doctype": "DocShare", "share_doctype": self.access.DOCTYPE,
                                "share_name": event.name, "everyone": 1, "read": 1})
        share.flags.ignore_validate = True
        with self.assertRaises(frappe.PermissionError):
            share.insert(ignore_permissions=True)
        # Simulate a pre-existing legacy share without invoking native hooks.
        share.name = uuid.uuid4().hex
        share.db_insert()
        with self.assertRaises(frappe.PermissionError):
            get(self.access.DOCTYPE, event.name)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_list(self.access.DOCTYPE, pluck="name")

    def test_no_native_document_audit_payload_created_for_corrections(self):
        row = self.edit(self.create())
        event = self.rows(row["name"])[0]
        self.assertFalse(frappe.db.exists("Version", {"ref_doctype": self.access.DOCTYPE, "docname": event.name}))
        self.assertFalse(frappe.db.exists("Comment", {"reference_doctype": self.access.DOCTYPE,
                                                     "reference_name": event.name}))

    def test_snapshot_header_bound_prevents_loading_large_values(self):
        with patch.object(self.service, "_candidate_headers", return_value=[frappe._dict(
                name="synthetic", value_bytes=self.model.MAX_SNAPSHOT_BYTES + 1)]), \
                patch.object(self.service, "_event_rows") as read:
            with self.assertRaises(ValueError):
                self.service.snapshot_for_scope(self.workspace)
            read.assert_not_called()


if __name__ == "__main__":
    BASE["connect"]()
    try:
        unittest.main()
    finally:
        frappe.db.rollback()
        frappe.destroy()
