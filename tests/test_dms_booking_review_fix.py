"""Zero-I/O regressions for the difference card's three buttons (2026-10-16).

Update data / Keep existing data / Edit on the booking receipt's difference card change the **same** booking in
DMS through its native edit form, only when DMS itself allows it (the booking is still a draft, this account may
edit it, nothing reviewed or approved), never resend after the write boundary, and check every item again.

Real modules: ``booking.booking_amend``, ``booking.booking_review``, ``booking.conversation.review_fix`` and
``line.cards``, over stand-ins for Frappe, LINE, the state store and the DMS session. Nothing logs into DMS,
sends LINE or touches a Site.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

BOOKING_ID, BOOKING_NO, UID = "120", "BK000002610000005", "U-sales"
RECORD = "r" * 64
FORM = {"stsel": "e", "idsel": BOOKING_ID, "txtprefixautonum": "BK", "txtdocno": "000002610000005",
        "stcbc": "1", "txttel": "0810000002", "txtcus": "Local Fixture", "fulcurrname[1]": "ID card copy",
        "fulcurridx[1]": "7480"}
ROW = '<div data-val="%s" data-allo="%s" data-cf="%s" onclick="ctllistdata(this);">'


class Store:
    """The non-evicting state store: plain values plus SET NX for claims."""

    def __init__(self):
        self.values = {}

    def make_key(self, key):
        return "site|" + key

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


class Dms:
    """The native booking edit form of one draft booking; `edit.php` replaces the whole document."""

    def __init__(self, test):
        self.test, self.form, self.allo, self.cf = test, dict(FORM), "pvecgu", ""
        self.posts, self.reply, self.fail_post, self.drop_file = [], "", False, False
        self.transport = types.SimpleNamespace(post=self.post)

    def _url(self, path):
        return "https://dms.invalid/" + path

    def _post_text(self, path, data):
        if path.endswith("showdata.php"):
            return ROW % (BOOKING_ID, self.allo, self.cf) + "<p>" + BOOKING_NO + "</p></div>"
        return "form"

    def _parse_form_defaults(self, body):
        return dict(self.form)

    def post(self, url, data, timeout_ms=None, files=None):
        self.test.assertTrue(url.endswith("drfcbc/edit.php"))
        self.test.events.append("post")
        self.posts.append(dict(data))
        if not self.reply.startswith("err::"):
            self.form = {k: v for k, v in data.items() if not (self.drop_file and k.startswith("fulcurr"))}
        if self.fail_post:
            raise TimeoutError("no answer")
        return types.SimpleNamespace(status_code=200, text=self.reply or " ")


class Isolated(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.events = []
        self.stub("frappe", _=lambda text: text)
        self.stub("pearnly_core", __path__=[])
        self.store = Store()
        self.stub("pearnly_core.execution", __path__=[])
        self.stub("pearnly_core.execution.state", store=lambda: self.store)
        self.stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key + "".join(
            f"|{k}={v}" for k, v in sorted(kw.items())), line_lang=lambda: "th")
        self.stub("pearnly_dms.editor.edit_link", url=lambda draft, customer=False, review=False:
                  "https://liff.invalid/?draft=%s&editor=%s" % (draft, "review" if review else ""))
        self.dms = Dms(self)
        self.amend = importlib.import_module("pearnly_dms.booking.booking_amend")
        self.review = importlib.import_module("pearnly_dms.booking.booking_review")

    def stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module


class Amend(Isolated):
    def run_amend(self, writes=None):
        return self.amend.amend(self.dms, booking_id=BOOKING_ID, booking_no=BOOKING_NO,
                                writes=writes or {"txttel": "0810000001"},
                                on_posted=lambda: self.events.append("registered"))

    def test_draft_the_account_may_edit_is_changed_in_place_and_read_back(self):
        result = self.run_amend()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(self.events, ["registered", "post"])  # registered before the write boundary
        posted, = self.dms.posts
        self.assertEqual(posted, {**FORM, "txttel": "0810000001"})  # whole fresh form, files kept
        self.assertEqual(result["form"]["txttel"], "0810000001")

    def test_dms_rules_lock_the_booking_and_nothing_is_written(self):
        for change in ({"form": {**FORM, "stcbc": "2"}}, {"allo": "pvu"}, {"cf": "a"}):
            self.dms = Dms(self)
            for key, value in change.items():
                setattr(self.dms, key, value)
            self.assertEqual(self.run_amend(), {"status": "locked"})
            self.assertEqual(self.dms.posts, [])
        self.assertEqual(self.events, [])

    def test_another_booking_behind_the_id_is_never_written(self):
        self.dms.form = {**FORM, "txtdocno": "000002610000006"}
        with self.assertRaises(self.amend.DmsClientError):
            self.run_amend()
        self.assertEqual(self.dms.posts, [])

    def test_rejection_is_reported_with_dms_words(self):
        self.dms.reply = "err::ข้อมูลไม่ถูกต้อง"
        self.assertEqual(self.run_amend(), {"status": "rejected", "message": "ข้อมูลไม่ถูกต้อง"})

    def test_no_answer_is_read_back_once_and_never_resent(self):
        self.dms.fail_post = True
        with self.assertLogs(self.amend.logger, "WARNING"):
            result = self.run_amend()
        self.assertEqual((result["status"], len(self.dms.posts)), ("updated", 1))

    def test_a_lost_attachment_is_not_reported_as_updated(self):
        self.dms.drop_file = True
        with self.assertLogs(self.amend.logger, "WARNING"):
            self.assertEqual(self.run_amend()["status"], "unverified")


DIFF = {"item": "sum.phone", "sent": "0810000001", "dms": "0810000002", "write": {"txttel": "0810000001"},
        "inputs": [["txttel", "diff.phone"]]}
PLACE = {"item": "qa.LBL_PLACE", "sent": "Showroom", "dms": "Branch",
         "write": {"placebookval": "1", "txtplacebook": "Showroom"}, "inputs": []}


class Buttons(Isolated):
    def setUp(self):
        super().setUp()
        self.replies, self.pushes, self.sends, self.queued, self.audits = [], [], [], [], []
        self.session_result = None
        self.stub("pearnly_dms.line.out", reply_text=lambda token, text: self.replies.append((token, text)),
                  push_or_reply_text=lambda uid, text, token="": self.pushes.append(text),
                  send=lambda uid, msg, token="": self.sends.append(msg),
                  enqueue=lambda job, fn, *args: self.queued.append((job, fn.__name__, args)))
        context = {"binding": {"tenant_id": "lab", "user_id": "sales@example.invalid"}, "endpoint": "EP-A"}
        self.stub("pearnly_dms.booking.delivery",
                  record=lambda name: types.SimpleNamespace(attempt=json.dumps({"context": context})),
                  sales_endpoint=lambda name: {"name": "EP-A"} if self.endpoint_ok else None,
                  endpoint_of=lambda name: "EP-A")
        self.endpoint_ok = True
        self.stub("pearnly_dms.booking.booking_ledger", log_amend=lambda **kw: self.audits.append(kw))
        self.stub("pearnly_dms.adapter.operations", run_logged_in=self.session)
        # The shared retry wording (pinned in test_dms_login_retry): "unreachable" while a retry is still offered
        # says "try again later", never "contact the administrator".
        self.stub("pearnly_dms.line.errors", retry_text=lambda code, text, lang: (
            "dms.err.ERR_DMS_TECHNICAL" if code == "ERR_DMS_UNREACHABLE" else text))
        self.fix = importlib.import_module("pearnly_dms.booking.conversation.review_fix")
        self.cards = importlib.import_module("pearnly_dms.line.cards")
        self.nonce = self.fix.open_card(RECORD, UID, BOOKING_NO, BOOKING_ID, [DIFF])

    def session(self, endpoint, fn, **kwargs):
        self.assertEqual(kwargs, {})  # an operator's own action: interactive priority, no write-retry switch
        return self.session_result if self.session_result is not None else fn(self.dms, None)

    def tap(self, action, nonce=None, token="t"):
        self.fix.handle_postback({}, UID, token, action, {"r": RECORD, "n": nonce or self.nonce})

    def run_queued(self):
        job, name, args = self.queued.pop(0)
        self.assertEqual((job, name), ("review", "apply"))
        self.fix.apply(*args)

    def test_keep_answers_once_and_writes_nothing(self):
        self.tap(self.cards.ACT_REVIEW_KEEP, token="t1")
        self.tap(self.cards.ACT_REVIEW_KEEP, token="t2")
        self.assertEqual(self.replies, [("t1", "dms.card.TXT_REVIEW_KEPT")])
        self.assertEqual((self.queued, self.dms.posts), ([], []))

    def test_update_writes_the_confirmed_value_and_reports_the_check(self):
        self.tap(self.cards.ACT_REVIEW_UPDATE, token="t1")
        self.tap(self.cards.ACT_REVIEW_UPDATE, token="t2")  # second tap while queued: silent
        self.assertEqual(self.replies, [])
        self.run_queued()
        posted, = self.dms.posts
        self.assertEqual(posted["txttel"], "0810000001")
        self.assertEqual(self.pushes, ["dms.card.TXT_REVIEW_UPDATED\ndms.card.TXT_REVIEW_BOOKING_NO|booking_no="
                                       + BOOKING_NO + "\ndms.card.TXT_REVIEW_OK"])
        audit, = self.audits
        self.assertEqual((audit["outcome"], audit["items"], audit["fields"]), ("updated", ["sum.phone"], ["txttel"]))

    def test_a_value_dms_still_shows_differently_comes_back_as_a_new_card(self):
        # DMS accepts the post but keeps showing the old phone.
        self.dms.transport = types.SimpleNamespace(post=lambda url, data, timeout_ms=None, files=None: (
            self.dms.posts.append(data), types.SimpleNamespace(status_code=200, text=" "))[1])
        self.tap(self.cards.ACT_REVIEW_UPDATE)
        self.run_queued()
        card, = self.sends
        self.assertEqual(card["altText"], "dms.card.TXT_REVIEW_DIFF_HEAD")
        fresh = json.loads(self.store.values["pearnly:dms:review-card:" + RECORD])["nonce"]
        self.assertNotEqual(fresh, self.nonce)
        self.tap(self.cards.ACT_REVIEW_KEEP, token="old")  # the replaced card is expired
        self.assertEqual(self.replies, [("old", "dms.card.TXT_EXPIRED")])

    def test_failure_before_sending_returns_the_card(self):
        self.session_result = {"ok": False, "error_code": "ERR_DMS_UNREACHABLE",
                               "error_friendly": {"th": "dms.err.ERR_DMS_UNREACHABLE"}}
        self.tap(self.cards.ACT_REVIEW_UPDATE)
        self.run_queued()
        self.assertEqual((self.pushes, self.audits), (["dms.err.ERR_DMS_TECHNICAL"], []))
        self.session_result = None
        self.tap(self.cards.ACT_REVIEW_UPDATE)  # the same card works again
        self.run_queued()
        self.assertEqual(len(self.dms.posts), 1)

    def test_an_interrupted_write_is_never_resent(self):
        self.tap(self.cards.ACT_REVIEW_UPDATE)
        job = self.queued[0]
        card = json.loads(self.store.values["pearnly:dms:review-card:" + RECORD])
        self.store.values["pearnly:dms:review-card:" + RECORD] = json.dumps({**card, "status": "posted"})
        self.queued[:] = [job]
        self.run_queued()
        self.assertEqual((self.pushes, self.dms.posts), (["dms.card.TXT_REVIEW_UNVERIFIED"], []))

    def test_locked_and_unknown_identity_never_write(self):
        self.dms.cf = "a"
        self.tap(self.cards.ACT_REVIEW_UPDATE)
        self.run_queued()
        self.assertEqual((self.pushes, self.dms.posts), (["dms.card.TXT_REVIEW_LOCKED"], []))
        self.nonce = self.fix.open_card(RECORD, UID, BOOKING_NO, BOOKING_ID, [DIFF])
        self.endpoint_ok = False
        self.tap(self.cards.ACT_REVIEW_UPDATE)
        self.run_queued()
        self.assertEqual((self.pushes[-1], self.dms.posts), ("dms.card.TXT_REVIEW_FAILED", []))

    def test_edit_page_lists_items_and_saves_the_chosen_values(self):
        self.nonce = self.fix.open_card(RECORD, UID, BOOKING_NO, BOOKING_ID, [DIFF, PLACE])
        draft = f"{RECORD}.{self.nonce}"
        page = self.fix.page_load(UID, draft)
        self.assertEqual(page["booking_no"], BOOKING_NO)
        self.assertEqual([(i["key"], i["label"]) for i in page["items"]],
                         [("sum.phone", "dms.card.sum.phone"), ("qa.LBL_PLACE", "dms.card.qa.LBL_PLACE")])
        self.assertEqual(page["items"][0]["inputs"],
                         [{"field": "txttel", "label": "dms.card.diff.phone", "value": "0810000001"}])
        with self.assertRaises(PermissionError):
            self.fix.page_load("U-other", draft)
        self.fix.page_save(UID, draft, {"sum.phone": {"use": "sent", "inputs": {"txttel": " 081 000 0009 "}},
                                        "qa.LBL_PLACE": {"use": "dms"}})
        with self.assertRaises(PermissionError):
            self.fix.page_save(UID, draft, {})  # one save per card
        self.run_queued()
        posted, = self.dms.posts
        self.assertEqual((posted["txttel"], posted.get("placebookval")), ("081 000 0009", None))

    def test_choosing_dms_for_everything_writes_nothing(self):
        self.fix.page_save(UID, f"{RECORD}.{self.nonce}", {"sum.phone": {"use": "dms"}})
        self.run_queued()
        self.assertEqual((self.pushes, self.dms.posts), (["dms.card.TXT_REVIEW_KEPT"], []))


class Card(Isolated):
    def test_the_card_carries_three_buttons_for_this_card_only(self):
        cards = importlib.import_module("pearnly_dms.line.cards")
        card = cards.booking_review_card(BOOKING_NO, [DIFF], record=RECORD, nonce="n1")
        footer = card["contents"]["footer"]["contents"]
        actions = [button["action"] for button in footer]
        self.assertEqual([a["label"] for a in actions],
                         ["dms.card.BTN_UPDATE", "dms.card.BTN_KEEP", "dms.card.BTN_EDIT"])
        self.assertIn("action=review_update", actions[0]["data"])
        self.assertIn("n=n1", actions[1]["data"])
        self.assertLessEqual(max(len(a.get("data", "")) for a in actions), 300)
        self.assertEqual(actions[2]["uri"], f"https://liff.invalid/?draft={RECORD}.n1&editor=review")

    def test_unresolved_reports_only_items_still_different(self):
        targets = [DIFF, PLACE]
        left = self.review.unresolved(targets, {"txttel": "0810000001", "placebookval": "2", "txtplacebook": "Branch"})
        self.assertEqual([(d["item"], d["sent"], d["dms"]) for d in left], [("qa.LBL_PLACE", "Showroom", "Branch")])

    def test_compare_carries_what_to_write_back(self):
        diffs = self.review.compare({"txttel": "081-000-0001", "txtcus": "A"}, {"txttel": "0810000002", "txtcus": "A"})
        phone = next(d for d in diffs if d["item"] == "sum.phone")
        self.assertEqual((phone["write"], phone["inputs"]), ({"txttel": "081-000-0001"}, [["txttel", "diff.phone"]]))


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
