"""Zero-I/O regressions (G8): a new customer whose code is held by another person's old record.

Old bug (DMS-TEST, 2026-10-12): the new-customer form leaves the customer code empty, so the
code is this person's ID number; an older record already held that code while its ID was
someone else's (a mistaken claim, or an ID edited in DMS). DMS refused "code duplicate", the
ID research found no one, and the operator only got "save failed" with no way out -- every
retry and re-scan failed the same way.

These run the real DMS client, the real ledger marker (``intake.customer_attempt``) and the real
``intake.service.push_idcard_fields`` over the in-memory Frappe stand-in of
``test_dms_customer_reconcile.py`` and a fake DMS that keeps customer codes unique. Nothing logs
in, posts to DMS, sends LINE or touches a Site.
"""
from __future__ import annotations

import copy
from pathlib import Path
import runpy
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

# Only the stand-ins are borrowed; that module's own test class stays out of this suite.
_reconcile = runpy.run_path(str(ROOT / "tests/test_dms_customer_reconcile.py"))
identity = runpy.run_path(str(ROOT / "scripts/test-dms-customer-identity.py"))
DmsClient, Transport, record = (identity[key] for key in ("DmsClient", "Transport", "record"))
FIELDS, PID, OTHER = (identity[key] for key in ("FIELDS", "PID", "OTHER"))
EP, USER = _reconcile["EP"], _reconcile["USER"]
from pearnly_dms.contracts.client_base import DmsClientError  # noqa: E402


class CodeTaken(unittest.TestCase):
    snapshot = _reconcile["Reconcile"].snapshot
    session = _reconcile["Reconcile"].session

    def setUp(self):
        _reconcile["Reconcile"].setUp(self)
        self.transport = Transport({"wrong": record(OTHER, txtcuscode=PID)})
        self.transport.new_code, self.transport.unique_codes, self.transport.code_maxlength = "", True, 20
        self.client = DmsClient(self.transport, "https://identity.invalid/dms/")
        self.holder = copy.deepcopy(self.transport.records["wrong"])
        self.checks = 0
        self.fail_check = None  # which recheck (1 = before the first send) raises

    def current(self):
        """The LINE flow's binding/session check (``intake.flow.execute.assert_current``)."""
        self.checks += 1
        if self.checks == self.fail_check:
            raise DmsClientError("Customer draft was replaced before dispatch", "ERR_DMS_CUSTOMER_SAVE")

    def create(self, request="line:first"):
        self.transport.calls.clear()
        return self.service.push_idcard_fields(EP, fields=dict(FIELDS), mode="create", customer_id=None,
                                               request_id=request, actor=USER, assert_current=self.current)

    def rows(self):
        return [row for name, row in self.ledger.rows.items() if name.startswith("CW-")]

    def codes(self):
        return [data["txtcuscode"] for _, data in self.transport.writes()]

    def second_post(self, change):
        post = self.transport.post

        def changed(url, *, data, **kwargs):
            if url.endswith("cus/new.php") and len(self.transport.writes()) == 1:
                self.transport.calls.append(("cus/new.php", dict(data)))  # it was sent
                return change()
            return post(url, data=data, **kwargs)

        self.transport.post = changed

    def test_code_held_by_another_person_gets_the_normal_success_and_one_ledger_row(self):
        out = self.create()
        self.assertTrue(out["success"], out)
        self.assertEqual((out["customer_id"], out["mode"]), ("created", "create"))
        self.assertEqual(self.codes(), [PID, PID + "-1"])
        self.assertEqual(self.transport.records["created"]["txtpeopleid"], PID)
        self.assertEqual(self.transport.records["wrong"], self.holder)  # never written
        self.assertEqual(self.checks, 2)  # before the first send, and again before the second
        [row] = self.rows()
        self.assertEqual((row["status"], row["business_key"]), ("success", "created"))
        # The same request delivered again recovers the verified result; nothing is posted.
        again = self.create()
        self.assertTrue(again["recovered"])
        self.assertEqual(self.codes(), [])

    def test_unclear_holder_is_a_settled_rejection_not_a_lock(self):
        self.transport.records["wrong"]["txtpeopleid"] = ""  # may be this very person
        out = self.create()
        self.assertEqual(out["error_code"], "ERR_DMS_CUSTOMER_SAVE")
        self.assertEqual(self.codes(), [PID])
        [row] = self.rows()
        self.assertEqual(row["status"], "failed")  # nothing was written, nothing stays blocked

    def test_holder_check_that_raises_is_still_a_settled_rejection(self):
        post, searches = self.transport.post, []

        def broken_search(url, *, data, **kwargs):
            if url.endswith("showdata.php"):
                searches.append(data["sd"])
                if len(searches) == 5:  # preflight, three ID re-searches, then the holder check
                    raise ConnectionError("search dropped")
            return post(url, data=data, **kwargs)

        self.transport.post = broken_search
        out = self.create()
        self.assertEqual(out["error_code"], "ERR_DMS_CUSTOMER_SAVE")
        self.assertEqual(self.codes(), [PID])
        self.assertEqual([row["status"] for row in self.rows()], ["failed"])

    def test_binding_changed_before_the_second_send_is_a_rejection_without_it(self):
        self.fail_check = 2
        out = self.create()
        self.assertEqual(out["error_code"], "ERR_DMS_CUSTOMER_SAVE")
        self.assertEqual(self.codes(), [PID])
        self.assertEqual([row["status"] for row in self.rows()], ["failed"])

    def test_lost_second_send_stays_unconfirmed_and_is_never_sent_again(self):
        def lost():
            raise TimeoutError("lost acknowledgement")

        self.second_post(lost)
        out = self.create()
        self.assertEqual(out["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.codes(), [PID, PID + "-1"])
        self.assertEqual([row["status"] for row in self.rows()], ["manual"])
        retry = self.create()  # the operator's retry: read-only check, never a third send
        self.assertEqual(retry["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.codes(), [])


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
