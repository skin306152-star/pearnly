"""Zero-I/O regressions: an order created without all of its attachments still gets its receipt at once.

Old bug (reproduced 2026-10-12 on the test machine with the attach_fail switch): ``delivery.deliver``
repaired the missing files *before* the receipt and returned without one whenever that repair failed.
The order existed in DMS and the draft was gone, but the operator got nothing on LINE ("view result"
answered "expired, start again", inviting a second order). If the files could never be added, the
receipt never came; after 23 hours the row turned to Review and nobody was told.

Now (owner decision 2026-10-13):

* the receipt goes out at once, word for word the normal one, in the same job (the free reply);
* the round that gets the receipt accepted never repairs; later rounds only add the missing files to
  the same order and send nothing when they are complete;
* the 6th failed repair (any failure, an exception included) stops repairing, fixes one notice
  (booking number + the existing "not all files were attached" text) with its own retry key, and only
  an accepted notice moves the row to Review; a notice LINE did not accept is resent with that key;
* a rebind moves the row to Review without an upload or a message.

These run the real ``delivery``, ``recovery``, ``booking_ledger`` and ``booking_flow`` over the in-memory
Frappe stand-in of ``test_dms_booking_outcome``. DMS, LINE and the LINE content download are replaced.
Nothing logs in, posts to DMS, sends LINE or touches a Site.
"""
from __future__ import annotations

import datetime
import json
import socket
import sys
import types
import unittest
from unittest.mock import patch

from test_dms_booking_outcome import DELIVERY, ENDPOINT, QA, UID, Harness, _dt, err

ID_CARD, SLIP = "สำเนาบัตรประชาชน", "ใบโอนเงินจอง"
FILES_QA = {**QA, "files": {"id_card_mid": "M-id", "slip_mid": "M-slip"}}
NOTICE = "dms.card.TXT_BOOKING_REF|booking_no=BK-1\ndms.card.qa.TXT_ATTACH_FAIL"


class Attachments(Harness):
    def setUp(self):
        super().setUp()
        self.push_ok = True        # LINE accepts the message
        self.uploads_ok = False    # DMS accepts the uploaded files
        self.on_form = set()       # file names on the order's DMS form
        self.uploads, self.logins, self.downloads = [], 0, 0
        self.rebind_on_download = False

        def created(number):
            # The booking job attached the ID card and failed on the slip.
            self.on_form = {ID_CARD}
            return {"ok": True, "booking_id": "R-" + number, "booking_no": number, "attach_ok": False,
                    "attached": 1, "attach_failed": [{"display_name": SLIP, "error": "upload failed"}]}

        self.outcome = created
        sys.modules["pearnly_core.line"].push = self.push
        files = sys.modules["pearnly_dms.booking.attachments"]
        files.download, files.expected_names = self.download, lambda qa: (
            {ID_CARD, SLIP} if (qa.get("files") or {}) else set())

    # ── stand-ins ──────────────────────────────────────────────────────────────
    def push(self, uid, messages, retry_key=None):
        self.pushes.append((uid, messages[0]["text"], retry_key))
        return self.push_ok

    def download(self, qa, missing=None):
        self.downloads += 1
        if self.rebind_on_download:
            self.bound = False  # the operator was rebound while the file was downloading
        return [{"display_name": name, "filename": "f.jpg", "content_type": "image/jpeg", "content": b"x"}
                for name in (ID_CARD, SLIP) if missing is None or name in missing], []

    def dms_session(self, endpoint, fn, **kwargs):
        """A background DMS session on the original sales identity: reads the order's form, uploads files."""
        self.assertTrue(kwargs.get("background"))
        self.logins += 1
        number = "BK-%d" % self.posts
        form = {"txtdocno": number, "idsel": "R-" + number,
                **{"fulcurrname%d" % i: name for i, name in enumerate(sorted(self.on_form), 1)}}
        client = types.SimpleNamespace(_post_text=lambda path, data: "", attach_booking_files=self.attach,
                                       _parse_form_defaults=lambda body: dict(form))
        return fn(client, None)

    def attach(self, *, booking_id, files):
        names = [item["display_name"] for item in files]
        self.uploads.extend(names)
        if not self.uploads_ok:
            return {"ok": False, "attached": 0, "failed": [{"display_name": name} for name in names]}
        self.on_form.update(names)
        return {"ok": True, "attached": len(names), "failed": []}

    # ── helpers ────────────────────────────────────────────────────────────────
    def booked(self):
        self.confirm(qa=FILES_QA)
        return self.record()

    def record(self):
        record, = self.records()
        return record

    def marker(self):
        return json.loads(self.record()["attempt"])

    def state(self):
        record = self.record()
        return record["status"], record["last_error"]

    def run_round(self):
        """The next due pass of the 5-minute scheduler: run exactly what `schedule_due` queues."""
        self.now = max(self.now, _dt(self.record()["next_attempt_at"]))
        self.enqueued.clear()
        self.delivery.schedule_due()
        jobs, self.enqueued[:] = list(self.enqueued), []
        for method, kwargs in jobs:
            self.assertEqual(method, "pearnly_dms.booking.delivery.deliver")
            self.delivery.deliver(kwargs["name"])
        return len(jobs)

    def failed_rounds(self, count):
        for _ in range(count):
            self.assertEqual(self.run_round(), 1)


class ReceiptFirst(Attachments):
    def test_receipt_goes_out_at_once_word_for_word_and_the_order_waits_for_its_files(self):
        record = self.booked()
        # The old code logged in again to repair here, and on failure sent nothing at all.
        self.assertEqual([(uid, text) for uid, text, _ in self.pushes], [(UID, "receipt BK-1")])
        self.assertEqual(self.pushes[0][2], self.marker()["receipt"]["key"])
        self.assertEqual((record["status"], record["last_error"]), ("Pending", "attachments_pending"))
        self.assertTrue(self.marker()["receipt"]["accepted_at"])
        self.assertEqual((self.logins, self.downloads, self.uploads), (0, 0, []))  # this round never repairs
        self.assertIsNone(self.session())
        self.assertEqual(self.posts, 1)
        self.free_menu()
        log, = self.logs()
        self.assertEqual((log["status"], json.loads(log["response_body"])["attach_ok"]), ("success", False))

    def test_files_are_added_later_to_the_same_order_without_another_message(self):
        self.booked()
        self.failed_rounds(1)
        self.assertEqual(self.state(), ("Pending", "attachments_pending"))
        self.assertEqual(self.marker()["delivery"]["repairs"], 1)
        self.uploads_ok = True
        self.assertEqual(self.run_round(), 1)
        self.assertEqual(self.state(), ("Delivered", ""))
        self.assertEqual(self.on_form, {ID_CARD, SLIP})
        self.assertEqual(self.uploads, [SLIP, SLIP])  # only the missing file, never the attached one again
        self.assertEqual(len(self.pushes), 1)          # just the receipt
        self.assertEqual(self.posts, 1)
        log, = self.logs()  # the same row, now with every file attached
        self.assertEqual((log["status"], json.loads(log["response_body"])["attach_ok"]), ("success", True))
        self.assertEqual(self.run_round(), 0)  # Delivered: nothing more is scheduled

    def test_receipt_failure_reuses_its_key_and_the_round_that_sends_it_still_does_not_repair(self):
        self.push_ok = False
        self.booked()
        self.assertEqual(self.state(), ("Pending", "receipt_pending"))
        self.assertNotIn("accepted_at", self.marker()["receipt"])
        # The DMS identity changes before the retry: the receipt still goes (it never needed DMS),
        # and nothing is downloaded or uploaded in that round.
        self.db.tables[ENDPOINT]["EP-A"]["username"] = "someone-else"
        self.push_ok = True
        self.assertEqual(self.run_round(), 1)
        self.assertEqual([key for _, _, key in self.pushes], [self.marker()["receipt"]["key"]] * 2)
        self.assertEqual(self.state(), ("Pending", "attachments_pending"))
        self.assertEqual((self.logins, self.downloads, self.uploads), (0, 0, []))
        self.assertNotIn("repairs", self.marker()["delivery"])


class Notice(Attachments):
    def test_sixth_failed_repair_sends_one_notice_with_the_number_then_stops(self):
        self.booked()
        self.failed_rounds(5)
        self.assertEqual(self.state(), ("Pending", "attachments_pending"))
        self.assertEqual(len(self.pushes), 1)  # failed repairs say nothing
        self.failed_rounds(1)
        self.assertEqual(self.logins, 6)
        self.assertEqual(self.uploads, [SLIP] * 6)
        receipt, notice = self.pushes
        self.assertEqual(notice[:2], (UID, NOTICE))
        self.assertEqual(notice[2], self.marker()["notice"]["key"])
        self.assertNotEqual(notice[2], receipt[2])
        self.assertEqual(self.state(), ("Review", "attachments_failed"))
        self.assertEqual(self.run_round(), 0)  # Review: never scheduled again
        window = datetime.timedelta(seconds=self.delivery.WINDOW_SECONDS)
        self.assertLess(self.now - _dt(self.marker()["completed_at"]), window)  # long before the 23-hour cut-off

    def test_notice_not_accepted_is_resent_with_the_same_key_and_never_repairs_again(self):
        self.booked()
        self.failed_rounds(5)
        self.push_ok = False
        self.failed_rounds(1)
        self.assertEqual(self.state(), ("Pending", "notice_pending"))
        key = self.marker()["notice"]["key"]
        self.push_ok = True
        self.uploads_ok = True  # even if DMS works again, a person has been told: the system stops adding files
        self.assertEqual(self.run_round(), 1)
        self.assertEqual([(text, retry_key) for _, text, retry_key in self.pushes[1:]], [(NOTICE, key)] * 2)
        self.assertEqual(self.state(), ("Review", "attachments_failed"))
        self.assertEqual((self.logins, self.uploads), (6, [SLIP] * 6))

    def test_every_kind_of_repair_failure_counts(self):
        # A changed DMS identity raises before any login; it must not wait silently for 23 hours.
        self.booked()
        self.db.tables[ENDPOINT]["EP-A"]["username"] = "someone-else"
        self.failed_rounds(6)
        self.assertEqual((self.logins, self.downloads, self.uploads), (0, 0, []))
        self.assertEqual([text for _, text, _ in self.pushes], ["receipt BK-1", NOTICE])
        self.assertEqual(self.state(), ("Review", "attachments_failed"))

    def test_line_hiccups_do_not_bring_the_notice_forward(self):
        # Receipt retries and repairs are counted apart: three refused receipts are not three repairs.
        self.push_ok = False
        self.booked()
        self.failed_rounds(2)
        self.push_ok = True
        self.failed_rounds(1)  # the fourth try is accepted; that round does not repair
        self.failed_rounds(5)
        self.assertEqual(len(self.pushes), 4)  # four receipt tries, no notice yet
        self.assertEqual(self.state(), ("Pending", "attachments_pending"))
        self.failed_rounds(1)
        self.assertEqual([text for _, text, _ in self.pushes[4:]], [NOTICE])
        self.assertEqual(self.state(), ("Review", "attachments_failed"))


class Rebind(Attachments):
    def test_rebind_after_the_receipt_goes_to_review_without_upload_or_message(self):
        self.booked()
        self.bound = False
        self.failed_rounds(1)
        self.assertEqual(self.state(), ("Review", "binding_changed"))
        self.assertEqual((self.logins, self.uploads, len(self.pushes)), (0, [], 1))

    def test_rebind_during_the_download_never_uploads_or_notifies(self):
        self.booked()
        self.failed_rounds(5)
        self.rebind_on_download = True
        self.uploads_ok = True
        self.failed_rounds(1)
        self.assertEqual(self.state(), ("Review", "binding_changed"))
        self.assertEqual(self.uploads, [SLIP] * 5)  # the 6th download was never uploaded
        self.assertEqual(len(self.pushes), 1)       # and nobody new is told anything
        self.assertNotIn("notice", self.marker())


class Readback(Attachments):
    def test_unknown_order_found_later_gets_its_receipt_before_its_files(self):
        self.outcome = lambda number: err("ERR_DMS_BOOKING_OUTCOME_UNKNOWN", submitted=True, booking_no=number)
        self.found = "R-BK-1"
        self.confirm(qa=FILES_QA)
        self.on_form = {ID_CARD}
        self.texts.clear()
        self.age(minutes=5)
        self.recovery.reconcile_record(self.record()["name"])
        self.assertEqual(self.logins, 1)  # the read-back only; no repair in the round that sends the receipt
        self.assertEqual([text for _, text, _ in self.pushes], ["receipt BK-1"])
        self.assertEqual(self.state(), ("Pending", "attachments_pending"))
        self.assertEqual(self.uploads, [])
        self.uploads_ok = True
        self.assertEqual(self.run_round(), 1)
        self.assertEqual(self.state(), ("Delivered", ""))
        self.assertEqual((self.uploads, len(self.pushes)), ([SLIP], 1))
        log, = self.logs()
        self.assertEqual(log["status"], "success")


class CompleteFiles(Attachments):
    def test_complete_files_keep_the_unchanged_path(self):
        self.outcome = lambda number: {"ok": True, "booking_id": "R-" + number, "booking_no": number,
                                       "attach_ok": True, "attached": 2, "attach_failed": []}
        record = self.booked()
        self.assertEqual((record["status"], record["last_error"]), ("Delivered", ""))
        self.assertEqual([text for _, text, _ in self.pushes], ["receipt BK-1"])
        marker = self.marker()
        # nothing attachment-related is stored; since 2026-10-16 the receipt keeps the whole message
        # (it may be the review's difference card) instead of only its text
        self.assertEqual(set(marker["receipt"]), {"key", "created_at", "message"})
        self.assertEqual(marker["delivery"], {"attempts": 1})
        writes = [event for event in self.db.events if event[:3] == ("set", DELIVERY, record["name"])]
        self.assertEqual(len(writes), 3)  # retry, receipt key, result: the same three saves as before
        self.assertEqual((self.logins, self.downloads), (0, 0))


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
