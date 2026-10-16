"""Zero-I/O regressions for the booking receipt review (2026-10-16).

After a booking is written and verified, the form that verification already read is compared item by item
with what was submitted. All equal → the receipt gets one more line; anything different → the receipt becomes
a difference card. These cases pin the comparison rules (ids not display names, money as amounts, digits for
phones), that the review never changes a booking's outcome, and the two receipt shapes.

Real modules: ``booking.booking_review``, ``booking.booking_submit`` and ``line.cards``, over stand-ins for
Frappe and the message catalogue. Nothing logs into DMS, sends LINE or touches a Site.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

# What the native booking form holds once DMS stored our submission (local fixture, no real person).
SUBMITTED = {
    "txtprefixautonum": "BK", "txtdocno": "BKLAB0001", "cusval": "C-1", "usersval": "U-7",
    "txtusers": "Advisor Lab", "placebookval": "1", "txtplacebook": "Showroom",
    "prefixval": "1", "txtprefix": "Mr", "txtcus": "Local  Fixture", "txtpeopleid": "1-1111-11111-11-9",
    "txtbirthday": "01/01/2530", "txttel": "081-000-0001",
    "txthousenum": "9", "txtsoi": "Lab 1", "txtroad": "Test", "provincesval": "1", "txtprovinces": "Province",
    "districtsval": "2", "txtdistricts": "District", "subdistrictsval": "3", "txtsubdistricts": "Subdistrict",
    "zipcodesval": "94", "txtzipcodes": "10000",
    "carval": "437", "txtcar": "CAR1", "carpaintval": "3", "carpaintname": "Silver",
    "txtcardeliverydate": "27/10/2569", "termsaleval": "2", "txttermsale": "Finance",
    "regisbehalfval": "1", "txtregisbehalf": "Person", "txtregisname": "Local Fixture",
    "txtearnestmoney": "999.00", "txtmoneytfmon": "999.00", "banktfmonval": "6",
    "txtbanknametfmon": "Bank A", "txtaccountnumtfmon": "123-4-56789-0",
}


def stored(**changes):
    """The same booking as DMS shows it: DMS formats money and keeps its own display names."""
    form = {**SUBMITTED, "idsel": "77", "txtearnestmoney": "999", "txtmoneytfmon": "999.00",
            "txttermsale": "Finance (DMS name)", "txtcus": "Local Fixture", "txttel": "0810000001",
            "txtpeopleid": "1111111111119"}
    form.update(changes)
    return form


class Isolated(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.stub("frappe", _=lambda text: text)
        self.stub("pearnly_core", __path__=[])
        self.stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key + "".join(
            f"|{k}={v}" for k, v in sorted(kw.items())), line_lang=lambda: "th")
        self.review = importlib.import_module("pearnly_dms.booking.booking_review")

    def stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module


class Compare(Isolated):
    def test_the_stored_booking_matches_despite_dms_formatting(self):
        # 999 vs 999.00, dashes in phone and ID, doubled spaces, and DMS's own name for the sales terms.
        self.assertEqual(self.review.compare(SUBMITTED, stored()), [])

    def test_each_differing_card_item_is_listed_once_in_card_order(self):
        diffs = self.review.compare(SUBMITTED, stored(txttel="0810000002", txtsoi="Lab 2",
                                                      txtearnestmoney="0.00", banktfmonval="9",
                                                      txtbanknametfmon="Bank B"))
        self.assertEqual([d["item"] for d in diffs],
                         ["sum.address", "sum.phone", "qa.pay.transfer", "qa.LBL_TOTAL"])
        phone = diffs[1]
        self.assertEqual((phone["sent"], phone["dms"]), ("081-000-0001", "0810000002"))
        self.assertEqual((diffs[2]["sent"], diffs[2]["dms"]), ("999.00 · Bank A", "999.00 · Bank B"))
        self.assertEqual((diffs[3]["sent"], diffs[3]["dms"]), ("999.00", "0.00"))
        self.assertIn("Lab 1", diffs[0]["sent"])
        self.assertIn("Lab 2", diffs[0]["dms"])

    def test_ids_decide_not_display_names(self):
        self.assertEqual(self.review.compare(SUBMITTED, stored(txtplacebook="Show room", txtcar="CAR 1")), [])
        diffs = self.review.compare(SUBMITTED, stored(placebookval="2", txtplacebook="Branch"))
        self.assertEqual([(d["item"], d["sent"], d["dms"]) for d in diffs],
                         [("qa.LBL_PLACE", "Showroom", "Branch")])

    def test_a_channel_dms_did_not_store_and_a_missing_field_are_differences(self):
        form = stored()
        del form["txtregisname"]
        form["txtmoneycash"] = "500.00"
        items = [d["item"] for d in self.review.compare(SUBMITTED, form)]
        self.assertEqual(items, ["qa.LBL_REGIS", "qa.pay.cash"])

    def test_review_without_a_form_is_none_and_a_failing_comparison_is_swallowed(self):
        self.assertIsNone(self.review.review(None))
        self.assertIsNone(self.review.review({"submitted": SUBMITTED}))
        self.assertEqual(self.review.review({"form": stored(), "submitted": SUBMITTED}), {"diffs": []})
        with patch.object(self.review, "compare", side_effect=RuntimeError("bad form")), \
             self.assertLogs(self.review.logger, "WARNING"):
            self.assertIsNone(self.review.review({"form": stored(), "submitted": SUBMITTED}))


class Receipt(Isolated):
    def setUp(self):
        super().setUp()
        self.cards = importlib.import_module("pearnly_dms.line.cards")

    def message(self, review=None, attach_ok=True):
        return self.cards.booking_receipt_message("BKLAB0001", "CAR1", "27/10/2569", "Advisor Lab",
                                                  review=review, attach_ok=attach_ok)

    def test_without_a_review_the_receipt_is_unchanged(self):
        plain = self.cards.booking_receipt_text("BKLAB0001", "CAR1", "27/10/2569", "Advisor Lab")
        self.assertEqual(self.message(), {"type": "text", "text": plain})

    def test_all_matched_adds_the_check_line(self):
        plain = self.cards.booking_receipt_text("BKLAB0001", "CAR1", "27/10/2569", "Advisor Lab")
        self.assertEqual(self.message({"diffs": []}),
                         {"type": "text", "text": plain + "\ndms.card.TXT_REVIEW_OK"})

    def test_missing_attachments_never_claim_all_matched(self):
        plain = self.cards.booking_receipt_text("BKLAB0001", "CAR1", "27/10/2569", "Advisor Lab")
        self.assertEqual(self.message({"diffs": []}, attach_ok=False), {"type": "text", "text": plain})

    def test_differences_become_one_card_without_buttons(self):
        card = self.message({"diffs": [{"item": "sum.phone", "sent": "0810000001", "dms": "0810000002"}]})
        self.assertEqual((card["type"], card["altText"]), ("flex", "dms.card.TXT_REVIEW_DIFF_HEAD"))
        bubble = card["contents"]
        self.assertNotIn("footer", bubble)
        texts = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    texts.append(node["text"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(bubble)
        self.assertEqual(texts, ["dms.card.TXT_REVIEW_DIFF_HEAD", "dms.card.TXT_REVIEW_BOOKING_NO|booking_no=BKLAB0001",
                                 "dms.card.TXT_DIFF_HEADER", "dms.card.sum.phone",
                                 "0810000001  →  0810000002", "dms.card.TXT_REVIEW_FIX"])


class SubmitHandsOutTheVerifiedForm(Isolated):
    """The review reads nothing new: it gets the form verification already read, and only on success."""

    def setUp(self):
        super().setUp()
        self.submit = importlib.import_module("pearnly_dms.booking.booking_submit")
        self.form = stored()
        self.posts = []
        test = self

        class Client:
            transport = types.SimpleNamespace(post=lambda url, data, timeout_ms: (
                test.posts.append(data), types.SimpleNamespace(status_code=200, text="ok::77"))[1])
            base_url = "https://dms.invalid/"

            def _url(self, path):
                return self.base_url + path

            def _post_text(self, path, data):
                return 'data-val="77"' if path.endswith("showdata.php") else path

            def _parse_form_defaults(self, body):
                return dict(test.form)

            def _resolve_admin_transport(self):
                return None

        self.client = Client()
        self.base = {k: v for k, v in SUBMITTED.items() if k != "txtdocno"}

    def test_success_hands_out_the_form_and_the_submitted_view(self):
        out = {}
        booking_id, number = self.submit.submit_booking(self.client, self.base, "BKLAB0001", readback_out=out)
        self.assertEqual((booking_id, number, len(self.posts)), ("77", "BKLAB0001", 1))
        self.assertEqual(out["form"], self.form)
        self.assertEqual(out["submitted"]["txtdocno"], "BKLAB0001")

    def test_outcome_is_the_same_with_or_without_the_review(self):
        self.assertEqual(self.submit.submit_booking(self.client, self.base, "BKLAB0001"), ("77", "BKLAB0001"))

    def test_a_critical_mismatch_is_still_unknown_and_hands_out_nothing(self):
        self.form = stored(txtearnestmoney="0.00")
        out = {}
        with patch.object(self.submit.time, "sleep"):
            with self.assertRaises(self.submit.DmsBookingOutcomeUnknown):
                self.submit.submit_booking(self.client, self.base, "BKLAB0001", readback_out=out)
        self.assertEqual((out, len(self.posts)), ({}, 1))


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
