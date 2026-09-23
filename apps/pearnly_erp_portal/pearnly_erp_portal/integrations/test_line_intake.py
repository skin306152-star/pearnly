"""Signed Guest webhook regressions with real services and fake storage/LINE.

Run in the pinned Frappe runtime via unittest. No Site, real database, credentials,
OCR, outbound LINE or DMS call is used; requests are explicitly blocked.
"""
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frappe

from pearnly_core import line, line_binding
from pearnly_erp_portal.access import guard
from pearnly_erp_portal.integrations import handlers, intake


class GuestIntake(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(user="Guest")
        self.users = {"buyer": ["ERP Purchase"], "seller": ["ERP Sales"], "cashier": ["ERP Cashier"]}
        self.enabled = set(self.users)
        self.bindings = {"U-buyer": "buyer", "U-seller": "seller", "U-cashier": "cashier"}
        self.directions = {"U-seller": "sales"}
        self.docs, self.files, self.replies, self.switches = {}, [], [], []
        self.body = b""
        self.signature = ""
        self.db = Mock()
        self.db.get_value.side_effect = self.get_value
        self.db.exists.side_effect = lambda dt, name: name in self.docs
        self.db.savepoint.side_effect = self.savepoint
        self.db.rollback.side_effect = self.rollback
        self.list = Mock(return_value=[])
        patches = {
            "session": self.session, "db": self.db,
            "conf": {"pearnly_line_channel_secret": "synthetic-webhook-secret"},
            "local": SimpleNamespace(response=SimpleNamespace(http_status_code=200)),
            "request": SimpleNamespace(get_data=lambda: self.body),
            "get_request_header": lambda name: self.signature,
            "set_user": self.set_user,
            "get_roles": lambda user=None: self.users.get(user or self.session.user, []),
            "get_doc": self.get_doc, "get_list": self.list,
            "get_hooks": lambda key: ["pearnly_erp_portal.integrations.handlers.handle"],
            "get_attr": lambda path: handlers.handle,
            "throw": self.reject, "log_error": Mock(), "get_traceback": lambda: "synthetic failure",
        }
        for name, value in patches.items():
            self.enterContext(patch.object(frappe, name, value, create=True))
        for module in (handlers, intake, guard):
            self.enterContext(patch.object(module, "_", lambda text: text))
        self.enterContext(patch.object(frappe.utils, "now", return_value="2026-09-24 00:00:00"))
        self.enterContext(patch.object(line, "_claim_event", return_value=True))
        self.finish = self.enterContext(patch.object(line, "_finish_event"))
        self.enterContext(patch.object(line, "reply", side_effect=self.reply))
        self.download = self.enterContext(patch.object(line, "download_message_content", return_value=b"synthetic file"))
        self.enterContext(patch.object(handlers.line_session, "get_session", side_effect=lambda user:
            {"payload": {"direction": self.directions.get(user, "purchase")}}))
        self.network = self.enterContext(patch("requests.sessions.Session.request",
            side_effect=AssertionError("External network forbidden in this test")))

    @staticmethod
    def reject(message, exception=frappe.ValidationError):
        raise exception(message)

    def set_user(self, user):
        self.switches.append(user)
        self.session.user = user

    def savepoint(self, name):
        self.snapshot = (dict(self.docs), list(self.files))

    def rollback(self, **kwargs):
        self.docs, self.files = self.snapshot

    def get_value(self, doctype, name, field, **kwargs):
        if doctype == "User":
            return int(name in self.enabled) if field == "enabled" else name
        if doctype == line_binding.BINDING:
            user = self.bindings.get(name.get("line_user_id"))
            return frappe._dict(user=user, name="binding-" + user) if user else None
        if doctype == intake.DOCTYPE:
            for doc in self.docs.values():
                if all(doc.get(key) == value or (isinstance(value, list) and doc.get(key) in value[1])
                       for key, value in name.items()):
                    return doc.name
        return None

    def get_doc(self, values, name=None):
        if isinstance(values, str):
            return self.docs[name]
        doc = frappe._dict(values)
        doc.owner = values.get("owner") or self.session.user
        doc.name = "intake-" + str(len(self.docs)) if doc.doctype == intake.DOCTYPE else "file-" + str(len(self.files))
        def insert(**kwargs):
            if doc.doctype == intake.DOCTYPE:
                self.docs[doc.name] = doc
            else:
                doc.file_url = "/private/files/synthetic.pdf"
                self.files.append(doc)
            return doc
        doc.insert = insert
        doc.save = lambda **kwargs: doc
        doc.check_permission = Mock()
        return doc

    def reply(self, token, messages):
        self.replies.append((self.session.user, messages))
        return True

    def webhook(self, user="U-buyer", *, invalid_signature=False):
        self.body = json.dumps({"events": [{"type": "message", "webhookEventId": "synthetic-event",
            "source": {"userId": user}, "replyToken": "synthetic-reply",
            "message": {"type": "file", "id": "synthetic-media", "fileName": "bill.pdf"}}]}).encode()
        self.signature = base64.b64encode(hmac.new(b"synthetic-webhook-secret", self.body, hashlib.sha256).digest()).decode()
        if invalid_signature:
            self.signature = "wrong-signature"
        return line.webhook()

    def assert_restored(self):
        self.assertEqual(self.session.user, "Guest")
        self.assertNotIn("Administrator", self.switches)
        self.assertTrue(all(user == "Guest" for user, _messages in self.replies))
        self.network.assert_not_called()

    def test_signed_guest_purchase_and_sales_are_owned_by_bound_user(self):
        for user in ("buyer", "seller"):
            with self.subTest(user=user):
                self.assertEqual(self.webhook("U-" + user), {"ok": True})
                saved = list(self.docs.values())[-1]
                self.assertEqual((saved.owner, saved.sent_by), (user, user))
                self.assertEqual(saved.direction, "purchase" if user == "buyer" else "sales")
                self.assertEqual(self.files[-1].owner, user)
                self.assertTrue(self.files[-1].is_private)
                self.assert_restored()

    def test_repeat_file_is_not_stored_twice(self):
        self.webhook()
        self.webhook()
        self.assertEqual((len(self.docs), len(self.files)), (1, 1))
        self.assertEqual(self.replies[-1][1][0]["text"], "Already received")
        self.assert_restored()

    def test_bad_signature_never_downloads_or_switches_identity(self):
        self.assertEqual(self.webhook(invalid_signature=True)["error"], "bad_signature")
        self.download.assert_not_called()
        self.assertEqual(self.switches, [])
        self.assertEqual(self.docs, {})

    def test_unbound_disabled_and_wrong_role_do_not_download_or_store(self):
        self.enabled.remove("buyer")
        for user in ("U-unbound", "U-buyer", "U-cashier"):
            with self.subTest(user=user):
                self.webhook(user)
                self.assertEqual(self.docs, {})
                self.assertEqual(self.files, [])
                self.download.assert_not_called()
                self.assert_restored()

    def test_rebinding_or_disabling_during_download_does_not_store(self):
        for change in (lambda: self.bindings.update({"U-buyer": "seller"}),
                       lambda: self.enabled.discard("buyer")):
            self.bindings["U-buyer"] = "buyer"
            self.enabled.add("buyer")
            self.download.side_effect = lambda *args, **kwargs: (change(), b"synthetic file")[1]
            self.webhook()
            self.assertEqual(self.docs, {})
            self.assert_restored()

    def test_storage_exception_restores_guest_before_reply(self):
        with patch.object(intake, "_store_file", side_effect=RuntimeError("synthetic storage failure")):
            self.webhook()
        self.assertEqual(self.replies[-1][1][0]["text"], "Could not store this document")
        self.assertEqual(self.docs, {})
        self.assertEqual(self.files, [])
        self.db.rollback.assert_called_once_with(save_point="erp_line_intake_receive")
        self.assert_restored()

    def test_receive_cannot_impersonate_another_sender(self):
        self.session.user = "seller"
        with self.assertRaises(frappe.PermissionError):
            intake.receive(line_user_id="U-buyer", sent_by="buyer", media_type="file",
                           content=b"file", filename="bill.pdf")
        self.assertEqual(self.docs, {})

    def test_sales_only_user_lists_and_reads_own_sales_without_purchase_role(self):
        self.webhook("U-seller")
        name = next(iter(self.docs))
        self.session.user = "seller"
        intake.list_intakes(direction="sales")
        self.assertEqual(self.list.call_args.kwargs["filters"],
                         {"owner": "seller", "status": "received", "direction": ["in", ["sales"]]})
        self.assertEqual(intake.get_intake(name)["sent_by"], "seller")
        intake.list_intakes()
        self.assertEqual(self.list.call_args.kwargs["filters"]["direction"], ["in", ["sales"]])
        with self.assertRaises(frappe.PermissionError):
            intake.list_intakes(direction="purchase")

    def test_other_employee_cannot_read_someone_elses_draft(self):
        self.webhook()
        self.users["other-buyer"] = ["ERP Purchase"]
        self.session.user = "other-buyer"
        with self.assertRaises(frappe.PermissionError):
            intake.get_intake(next(iter(self.docs)))

    def test_guest_and_unprivileged_cannot_list(self):
        with self.assertRaises(frappe.AuthenticationError):
            intake.list_intakes()
        self.session.user = "cashier"
        with self.assertRaises(frappe.PermissionError):
            intake.list_intakes()
        self.list.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
