"""ERP LINE intake regressions (user-approved flow of 2026-09-25).

Runs in the pinned Frappe runtime via unittest. No Site, database, credentials, OCR,
outbound LINE or DMS call is used: collaborators are replaced and network is blocked.
"""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import frappe

from pearnly_erp_portal.integrations import handlers, intake, line_flow


class Base(unittest.TestCase):
    def setUp(self):
        self.replies = []
        self.sessions = {}
        self.roles = {"buyer": {"purchase.write", "integration.read"}}
        self.enterContext(patch.object(frappe, "_", lambda text: text, create=True))
        for module in (handlers, intake, line_flow):
            self.enterContext(patch.object(module, "_", lambda text: text))
        self.enterContext(patch.object(frappe, "session", SimpleNamespace(user="Guest"), create=True))
        self.enterContext(patch.object(frappe, "db", Mock(**{"get_value.return_value": None}), create=True))
        self.enterContext(patch.object(frappe, "set_user", lambda user: setattr(frappe.session, "user", user)))
        self.enterContext(patch("pearnly_core.line.reply", side_effect=lambda token, messages: self.replies.append(messages) or True))
        self.enterContext(patch("pearnly_core.line.reply_or_push",
                                side_effect=lambda to, messages, token="", **kw: self.replies.append(messages) or True))
        self.enterContext(patch("pearnly_core.line_session.get_session", side_effect=lambda uid: self.sessions.get(uid)))
        self.enterContext(patch("pearnly_core.line_session.set_session",
                                side_effect=lambda uid, state, payload, ttl_minutes=None: self.sessions.__setitem__(uid, {"state": state, "payload": dict(payload)})))
        self.enterContext(patch("pearnly_core.line_session.clear_session", side_effect=lambda uid, **kw: self.sessions.pop(uid, None)))
        self.enterContext(patch("pearnly_erp_portal.access.guard.has",
                                side_effect=lambda capability, user=None: capability in self.roles.get(user or frappe.session.user, set())))
        self.network = self.enterContext(patch("requests.sessions.Session.request",
                                               side_effect=AssertionError("External network forbidden")))

    def last_text(self):
        return self.replies[-1][0]["text"]

    def buttons(self):
        return [item["action"] for item in self.replies[-1][0]["quickReply"]["items"]]


class Flow(Base):
    def setUp(self):
        super().setUp()
        self.companies = ["CO-1"]
        self.enterContext(patch("pearnly_erp_portal.masters.company.allowed_companies",
                                side_effect=lambda user=None: list(self.companies)))
        self.enterContext(patch.object(line_flow, "_options", side_effect=self.options))
        self.enterContext(patch.object(line_flow, "_default_warehouse", return_value="WH-1"))

    def options(self, step, payload, user):
        return {"company": [("CO-1", "Alpha"), ("CO-2", "Beta")],
                "party": [(line_flow.FROM_DOCUMENT, "From the document"), ("SUP-1", "Acme")],
                "paid": [("1", "Paid"), ("0", "Unpaid")],
                "method": [("cash", "Cash"), ("transfer", "Transfer")],
                "kind": [("goods", "Stock item"), ("service", "Service")],
                "warehouse": [("WH-1", "Main")]}[step]

    def tap(self, action):
        handlers.handle({"type": "postback", "source": {"userId": "U1"}, "replyToken": "r",
                         "postback": {"data": action["data"]}})

    def test_steps_run_in_order_and_skip_single_company_and_default_warehouse(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.assertEqual(self.buttons()[0]["label"], "1. From the document")
            self.assertEqual(self.buttons()[-1]["data"], "a=erp&s=cancel")
            self.tap(self.buttons()[1])                        # Acme
            self.tap(self.buttons()[0])                        # Paid
            self.tap(next(b for b in self.buttons() if "transfer" in b["data"]))
            self.tap(self.buttons()[0])                        # Stock item
        payload = self.sessions["U1"]["payload"]
        self.assertEqual(payload, {"direction": "purchase", "company": "CO-1", "party": "SUP-1",
                                   "is_paid": 1, "payment_method": "transfer", "item_kind": "goods",
                                   "warehouse": "WH-1"})
        self.assertTrue(line_flow.ready(payload))
        self.assertIn("Please upload a PDF or image", self.last_text())

    def test_several_companies_are_offered_as_numbered_buttons(self):
        self.companies = ["CO-1", "CO-2"]
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode%3Apurchase"})           # the old menu's encoding
        self.assertEqual([b["label"] for b in self.buttons()], ["1. Alpha", "2. Beta", "Cancel"])

    def test_stale_or_forged_answers_do_not_advance(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=kind&v=goods"})         # not the current step
            self.assertEqual(self.last_text(), "This option is no longer current")
            self.tap({"data": "a=erp&s=party&v=SUP-404"})      # not an offered value
            self.assertEqual(self.last_text(), "This option is no longer current")
        self.assertNotIn("party", self.sessions["U1"]["payload"])

    def test_unpaid_skips_method_and_service_skips_warehouse(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            for data in ("a=mode:purchase", "a=erp&s=party&v=%40doc", "a=erp&s=paid&v=0",
                         "a=erp&s=kind&v=service"):
                self.tap({"data": data})
        payload = self.sessions["U1"]["payload"]
        self.assertEqual((payload["is_paid"], payload["payment_method"], payload.get("warehouse")), (0, "", None))
        self.assertTrue(line_flow.ready(payload))

    def test_role_and_binding_are_checked_before_any_step(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:sales"})
        self.assertEqual(self.last_text(), "This account cannot send documents")
        with patch("pearnly_core.line_binding.user_of", return_value=None):
            self.tap({"data": "a=mode:purchase"})
        self.assertEqual(self.last_text(), "Bind this account first")
        self.assertNotIn("U1", self.sessions)

    def test_cancel_clears_the_choices(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=cancel"})
        self.assertNotIn("U1", self.sessions)
        self.assertEqual(self.last_text(), "Cancelled")


class Documents(Base):
    def event(self):
        return {"type": "message", "source": {"userId": "U1"}, "replyToken": "r",
                "message": {"type": "image", "id": "M1"}}

    def test_document_before_choices_is_refused_without_download(self):
        download = self.enterContext(patch("pearnly_core.line.download_message_content"))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        self.assertEqual(self.last_text(), "Choose purchase or sales first")
        download.assert_not_called()

    def test_ready_document_is_received_as_the_bound_user_and_guest_restored(self):
        self.sessions["U1"] = {"state": line_flow.STATE, "payload": {"direction": "purchase", "company": "CO-1",
            "party": "@doc", "is_paid": 0, "payment_method": "", "item_kind": "service"}}
        self.enterContext(patch("pearnly_core.line.download_message_content", return_value=b"file"))
        seen = {}
        receive = self.enterContext(patch.object(intake, "receive",
                                    side_effect=lambda **kw: seen.update(user=frappe.session.user, **kw)))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        receive.assert_called_once()
        self.assertEqual((seen["user"], seen["sent_by"], seen["choices"]["company"]), ("buyer", "buyer", "CO-1"))
        self.assertEqual(frappe.session.user, "Guest")


def _doc(**values):
    base = dict(name="LI-1", direction="purchase", company="CO-1", party="SUP-1", is_paid=1,
                payment_method="transfer", item_kind="goods", warehouse="WH-1", status="ready",
                card_nonce="n1", card_expires_at=datetime.now() + timedelta(minutes=5),
                line_user_id="U1", sent_by="buyer", source_file="/private/files/a.jpg",
                result=json.dumps({"doc_no": "INV-1", "doc_date": "2026-09-25", "supplier_name": "Acme",
                                   "lines": [{"description": "Soap", "qty": 2, "unit_price": 10}]}))
    base.update(values)
    return SimpleNamespace(**base, get=lambda key, default=None: base.get(key, default))


class Payload(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))

    def test_purchase_payload_uses_the_chosen_masters_and_stock_choice(self):
        payload = intake.build_payload(_doc())
        self.assertEqual((payload["company"], payload["supplier"], payload["is_paid"], payload["payment_method"]),
                         ("CO-1", "SUP-1", 1, "transfer"))
        self.assertEqual((payload["update_stock"], payload["auto_create_product"]), (1, 1))
        self.assertEqual(payload["lines"][0]["item_type"], "goods")
        self.assertEqual(payload["lines"][0]["warehouse"], "WH-1")
        self.assertEqual(payload["attachments"], [{"file": "FILE-1", "kind": "bill"}])
        self.assertNotIn("company", json.loads(_doc().result))  # OCR never chooses the company

    def test_purchase_from_document_and_service_does_not_touch_stock(self):
        payload = intake.build_payload(_doc(party="@doc", item_kind="service", warehouse=None))
        self.assertNotIn("supplier", payload)
        self.assertEqual(payload["supplier_name"], "Acme")
        self.assertEqual((payload["update_stock"], payload["lines"][0]["item_type"]), (0, "service"))
        self.assertNotIn("warehouse", payload["lines"][0])

    def test_sales_payload_is_unpaid_and_names_an_unknown_buyer(self):
        doc = _doc(direction="sales", party="@doc", is_paid=0, payment_method="",
                   result=json.dumps({"doc_date": "2026-09-25", "lines": [{"description": "Soap", "qty": 1, "unit_price": 5}]}))
        payload = intake.build_payload(doc)
        self.assertEqual((payload["is_paid"], payload["paid_amount"], payload["buyer_name"]), (0, 0, "Unknown buyer"))
        self.assertEqual(payload["lines"][0]["warehouse"], "WH-1")


class Card(Base):
    def setUp(self):
        super().setUp()
        self.doc = _doc()
        self.enterContext(patch.object(frappe, "db", Mock(), create=True))
        frappe.db.exists.return_value = True
        self.enterContext(patch.object(frappe, "get_doc", side_effect=lambda doctype, name=None: self.doc))
        self.dropped = self.enterContext(patch.object(intake, "_drop"))
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch.object(intake, "now_datetime", datetime.now))
        self.enterContext(patch.object(intake, "now", lambda: "2026-09-25 23:00:00"))
        self.roles["buyer"] |= {"purchase.write"}

    def test_wrong_nonce_other_user_and_expired_cards_are_refused(self):
        self.assertEqual(intake._card("LI-1", "bad", "U1")[1], "This card is no longer valid")
        self.assertEqual(intake._card("LI-1", "n1", "U2")[1], "This card is no longer valid")
        self.doc.card_expires_at = datetime.now() - timedelta(minutes=1)
        self.assertIn("expired", intake._card("LI-1", "n1", "U1")[1])
        self.dropped.assert_called_once_with(self.doc, intake.EXPIRED)

    def test_confirm_posts_once_through_the_web_entry_and_replies_with_text(self):
        seen = {}

        def post(payload, *, request_id, finalize, allow_duplicate):
            seen.update(payload=payload, request_id=request_id, user=frappe.session.user,
                        allow_duplicate=allow_duplicate)
            finalize({"name": "PD-1"})
            return {"name": "PD-1", "system_no": "P-1"}

        self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post", side_effect=post))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.receipt", return_value="Recorded P-1"))
        result = intake.confirm("LI-1", "n1", "U1", "r")
        self.assertTrue(result["ok"])
        self.assertEqual((seen["request_id"], seen["user"], seen["allow_duplicate"]),
                         ("line-intake:LI-1", "buyer", True))
        self.assertEqual(seen["payload"]["company"], "CO-1")
        args = frappe.db.set_value.call_args.args
        self.assertEqual((args[1], args[2]["purchase"], args[2]["status"], args[2]["card_nonce"]),
                         ("LI-1", "PD-1", "confirmed", ""))
        self.assertEqual(self.last_text(), "Recorded P-1")
        self.assertEqual(frappe.session.user, "Guest")

    def test_failed_entry_keeps_the_card_and_explains(self):
        self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post",
                                side_effect=frappe.ValidationError("Supplier Invoice No is required")))
        self.enterContext(patch.object(frappe, "clear_messages"))
        result = intake.confirm("LI-1", "n1", "U1", "r")
        self.assertFalse(result["ok"])
        self.assertIn("Supplier Invoice No is required", self.last_text())
        self.dropped.assert_not_called()
        self.assertEqual(frappe.session.user, "Guest")

    def test_discard_drops_the_card(self):
        intake.discard("LI-1", "n1", "U1", "r")
        self.dropped.assert_called_once_with(self.doc, intake.DISCARDED)
        self.assertEqual(self.last_text(), "Discarded")


class CardButtons(Base):
    def test_card_postbacks_reach_confirm_and_discard_with_nonce(self):
        confirm = self.enterContext(patch.object(intake, "confirm"))
        discard = self.enterContext(patch.object(intake, "discard"))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            for op in ("confirm", "discard"):
                handlers.handle({"type": "postback", "source": {"userId": "U1"}, "replyToken": "r",
                                 "postback": {"data": f"a=card&op={op}&i=LI-1&n=n1"}})
        confirm.assert_called_once_with("LI-1", "n1", "U1", "r")
        discard.assert_called_once_with("LI-1", "n1", "U1", "r")

    def test_unknown_postbacks_are_left_for_other_handlers(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.assertFalse(handlers.handle({"type": "postback", "source": {"userId": "U1"},
                                              "postback": {"data": "action=menu_customer"}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
