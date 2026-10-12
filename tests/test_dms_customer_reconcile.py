"""Zero-I/O regressions: a customer write whose result is unknown is checked, not locked forever.

Old bug (reproduced 2026-10-12 with a fault switch on the test machine): DMS had taken the
customer write, only the readback failed once, and from then on every create/edit/booking
for that customer was refused as "unconfirmed" with no way out, background jobs included.

These run the real DMS client, the real ledger marker (``intake.customer_attempt``) and the
real ``intake.service.push_idcard_fields`` over an in-memory stand-in for Frappe's documents
and a fake DMS form transport. Nothing logs in, posts to DMS, sends LINE or touches a Site.
"""
from __future__ import annotations

from contextlib import nullcontext
import copy
import datetime
import importlib
import json
from pathlib import Path
import runpy
import socket
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

identity = runpy.run_path(str(ROOT / "scripts/test-dms-customer-identity.py"))
DmsClient, Transport, record, form = (identity[key] for key in ("DmsClient", "Transport", "record", "form"))
FIELDS, PID, OTHER = (identity[key] for key in ("FIELDS", "PID", "OTHER"))
from pearnly_dms.contracts.client_base import DmsClientError  # noqa: E402

LOG = "Pearnly Push Log"
EP = "EP-reconcile"
USER = "operator@example.invalid"
START = datetime.datetime(2026, 10, 13, 9, 0, 0)


class Row(dict):
    __getattr__ = dict.get


class Ledger:
    """Only what the marker uses from Frappe: one DocType, its rows and the clock."""

    def __init__(self):
        self.now = START
        self.rows = {}
        self.settle_race = None

    def get_value(self, doctype, name, fields="name", as_dict=False):
        if doctype == "Pearnly ERP Endpoint":
            return {"system_url": "https://identity.invalid/dms/"}[fields] if name == EP else None
        if self.settle_race and fields == "status" and name in self.rows:
            race, self.settle_race = self.settle_race, None
            race(self.rows[name])
        row = self.rows.get(name)
        if row is None:
            return None
        if isinstance(fields, str):
            return row.get(fields)
        picked = Row({key: row.get(key) for key in fields})
        return picked if as_dict else tuple(picked.values())

    def exists(self, doctype, name):
        return doctype == LOG and name in self.rows

    def set_value(self, doctype, name, values):
        self.rows[name].update(values)

    def commit(self):
        pass

    def get_all(self, doctype, filters=None, fields=None, limit_page_length=None):
        assert doctype == LOG and filters == {"name": ["like", "CW-%"], "status": "manual"}
        return [Row({key: row.get(key) for key in fields}) for name, row in self.rows.items()
                if name.startswith("CW-") and row.get("status") == "manual"]

    def new_doc(self, doctype):
        ledger = self

        class Doc(dict):
            def insert(self, ignore_permissions=False, set_name=None):
                assert set_name not in ledger.rows
                ledger.rows[set_name] = Row({**self, "name": set_name, "creation": ledger.now})

        return Doc()

    def get_datetime(self, value=None):
        return self.now if value is None else value


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        fake = types.ModuleType("frappe")
        fake.db = self.ledger
        fake.get_all = self.ledger.get_all
        fake.new_doc = self.ledger.new_doc
        fake.utils = types.SimpleNamespace(get_datetime=self.ledger.get_datetime)
        writeback = types.ModuleType("pearnly_dms.adapter.snapshot_writeback")
        self.cached = []
        writeback.capture_scope = lambda endpoint: "scope"
        writeback.customer = lambda endpoint, client, cid, expected_scope: (
            self.cached.append(str(cid)) or {"cuscode": "CACHED"})
        modules = patch.dict(sys.modules, {"frappe": fake, "pearnly_dms.adapter.snapshot_writeback": writeback})
        modules.start()
        self.addCleanup(modules.stop)
        for name in ("pearnly_dms.intake.customer_attempt", "pearnly_dms.intake.service"):
            sys.modules.pop(name, None)
        self.attempt = importlib.import_module("pearnly_dms.intake.customer_attempt")
        self.service = importlib.import_module("pearnly_dms.intake.service")
        lock = types.SimpleNamespace(lock=lambda *a, **kw: nullcontext(), make_key=lambda key: key)
        friendly = lambda code, lang=None: {"en": code}  # noqa: E731  (copy lives in the CSV tables)
        for target, name, value in ((self.attempt, "_state_store", lambda: lock),
                                    (self.attempt, "_snapshot_customer", self.snapshot),
                                    (self.service, "run_logged_in", self.session),
                                    ("pearnly_dms.adapter.operations.friendly", None, friendly),
                                    ("pearnly_dms.line.errors.friendly", None, friendly),
                                    ("pearnly_dms.client.client_intake.time.sleep", None, None)):
            patcher = patch(target, value) if name is None and value else (
                patch(target) if name is None else patch.object(target, name, value))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.local = (True, None)  # local snapshot: usable, this identity absent
        self.transport = Transport({"own": record()})
        self.client = DmsClient(self.transport, "https://identity.invalid/dms/")
        self.baseline = self.client.read_customer("own")  # what the operator's card was built on
        self.logins = 0

    # ── stand-ins ───────────────────────────────────────────────────────────
    def snapshot(self, endpoint, people_id):
        self.assertEqual((endpoint, people_id), (EP, PID))
        return self.local

    def session(self, endpoint, fn, **kwargs):
        """``run_logged_in`` contract: one session, never raises (errors become ``err``)."""
        from pearnly_dms.adapter.operations import err
        self.logins += 1
        try:
            return fn(self.client, None)
        except DmsClientError as exc:
            return err(exc.error_code, str(exc), getattr(exc, "response_body", None))

    def later(self, seconds):
        self.ledger.now += datetime.timedelta(seconds=seconds)

    def push(self, request="line:first", **changes):
        kwargs = dict(fields={"people_id": PID, "name": identity["NAME"], "phone": "0800000999"},
                      mode="update", customer_id="own", baseline=self.baseline)
        kwargs.update(changes)
        self.transport.calls.clear()
        return self.service.push_idcard_fields(EP, request_id=request, actor=USER, **kwargs)

    def row(self, request="line:first"):
        marker = self.attempt.Attempt(EP, request, USER, fields={"people_id": PID}, mode="update",
                                      customer_id="own", addresses=None, baseline=None)
        return self.ledger.rows[marker.name]

    def lose_readback(self):
        """DMS keeps the next edit, but every read after it lands on the login page."""
        post = self.transport.post
        def post_then_kicked(url, **kwargs):
            response = post(url, **kwargs)
            if url.endswith("cus/edit.php"):
                self.transport.form_overrides["own"] = "<html>login</html>"
                self.transport.post = post
            return response
        self.transport.post = post_then_kicked

    def lose_post(self, path):
        """The next post to ``path`` never reaches DMS; the caller only sees a timeout."""
        post = self.transport.post
        def lost(url, **kwargs):
            if url.endswith(path):
                self.transport.post = post
                self.transport.calls.append((path, dict(kwargs["data"])))  # it was sent
                raise TimeoutError("request never reached DMS")
            return post(url, **kwargs)
        self.transport.post = lost

    def restore_reads(self):
        self.transport.form_overrides.pop("own", None)

    def writes(self):
        return [path for path, _ in self.transport.writes()]

    # ── the old bug, end to end through the real service ────────────────────
    def test_written_but_unconfirmed_is_recovered_by_a_read_only_check_on_retry(self):
        self.lose_readback()
        first = self.push()
        self.assertEqual(first["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.writes(), ["cus/edit.php"])
        self.assertEqual(self.transport.records["own"]["txttel"], "0800000999")  # DMS has it
        self.restore_reads()
        retry = self.push()  # same request: the operator's retry button
        self.assertTrue(retry["success"], retry)
        self.assertTrue(retry["recovered"])
        self.assertEqual((retry["customer_id"], retry["cuscode"], retry["mode"]), ("own", "C001", "update"))
        self.assertEqual(self.writes(), [])  # the check never posts
        self.assertEqual(self.cached, ["own"])  # same local write-back as the normal path
        self.assertEqual((self.row()["status"], self.row()["business_key"]), ("success", "own"))

    def test_next_request_for_the_same_customer_is_not_refused_once_checked(self):
        self.lose_readback()
        self.push()
        self.restore_reads()
        other = self.push("line:second", fields={"people_id": PID, "name": identity["NAME"], "phone": "0800000777"},
                          baseline=self.client.read_customer("own"))
        self.assertTrue(other["success"], other)
        self.assertFalse(other.get("recovered"))
        self.assertEqual(self.writes(), ["cus/edit.php"])  # its own, normal write
        self.assertEqual(self.transport.records["own"]["txttel"], "0800000777")
        self.assertEqual(self.row()["status"], "success")
        self.assertEqual(self.logins, 2)  # check and write share one session

    def test_lost_submission_is_released_only_after_the_worker_grace(self):
        self.lose_post("cus/edit.php")
        self.assertEqual(self.push()["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.writes(), ["cus/edit.php"])
        self.later(300)
        early = self.push()
        self.assertEqual(early["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.row()["status"], "manual")
        self.later(self.attempt.WORKER_GRACE_SECONDS)
        late = self.push()
        self.assertEqual(late["reconciled"], "not_written")
        self.assertFalse(late["success"])
        self.assertEqual(self.writes(), [])
        self.assertEqual((self.row()["status"], self.row()["error_msg"]), ("failed", "ERR_DMS_CUSTOMER_SAVE"))
        # A new confirmation (new request) may now write, with live three-way merge as usual.
        fresh = self.push("line:reconfirmed", baseline=self.client.read_customer("own"))
        self.assertTrue(fresh["success"], fresh)
        self.assertEqual(self.writes(), ["cus/edit.php"])

    def test_unreadable_dms_keeps_blocking_without_any_write(self):
        self.lose_readback()
        self.push()
        self.later(3600)  # long past the grace: unreadable still is not "not written"
        for attempt in range(2):
            out = self.push()
            self.assertEqual(out["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
            self.assertTrue(out["response_body"]["submitted"])
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.row()["status"], "manual")

    def test_new_request_blocked_by_unreadable_dispatch_does_not_register_or_post(self):
        self.lose_readback()
        self.push()
        out = self.push("line:other")
        self.assertEqual(out["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.writes(), [])
        self.assertEqual(len(self.ledger.rows), 1)

    # ── the evidence kept at dispatch ───────────────────────────────────────
    def test_ledger_keeps_a_salted_digest_not_plain_identity_or_phone(self):
        self.lose_readback()
        self.push()
        body = self.row()["request_body"]
        verify = json.loads(body)["verify"]
        self.assertEqual((verify["mode"], verify["save_mode"], verify["customer_id"]), ("update", "overwrite", "own"))
        self.assertIn("phone", verify["keys"])
        for secret in (PID, "0800000999", identity["NAME"]):
            self.assertNotIn(secret, body)
        values = {key: "" for key in verify["keys"]}
        self.assertNotEqual(self.attempt._digest(["CW-other", values]), self.attempt._digest([self.row()["name"], values]))

    def test_rows_without_evidence_stay_blocked(self):
        marker = self.attempt.Attempt(EP, "legacy", USER, fields={"people_id": PID}, mode="update",
                                      customer_id="own", addresses=None, baseline=None)
        marker.begin()
        self.later(3600)
        self.transport.calls.clear()
        self.assertEqual(marker.reconcile(self.client), {})
        self.assertEqual(self.ledger.rows[marker.name]["status"], "manual")
        self.assertFalse(self.transport.calls)

    def test_settles_only_a_row_that_is_still_manual(self):
        self.lose_readback()
        self.push()
        self.restore_reads()
        def original_worker_finished(row):
            row["status"] = "failed"
        self.ledger.settle_race = original_worker_finished
        marker = self.attempt.Attempt(EP, "line:first", USER, fields={"people_id": PID}, mode="update",
                                      customer_id="own", addresses=None, baseline=None)
        self.assertEqual(marker.reconcile(self.client), {})
        self.assertEqual(self.row()["status"], "failed")
        self.assertNotIn("reconciled", self.row()["response_body"])

    def test_check_reads_in_the_customer_writer_session(self):
        self.lose_readback()
        self.push()
        self.restore_reads()
        sales = Transport()
        sales.post = lambda url, **kwargs: self.fail("the sales session may not see every customer")
        client = DmsClient(sales, "https://identity.invalid/dms/", admin_transport=self.transport)
        marker = self.attempt.Attempt(EP, "line:first", USER, fields={"people_id": PID}, mode="update",
                                      customer_id="own", addresses=None, baseline=None)
        self.transport.calls.clear()
        self.assertEqual(list(marker.reconcile(client).values()), ["written"])
        self.assertTrue(self.transport.calls)
        self.assertEqual(self.writes(), [])

    # ── creates: search can miss a new customer for minutes ─────────────────
    def create(self, request="line:create"):
        return self.push(request, fields=dict(FIELDS), mode="create", customer_id=None, baseline=None,
                         addresses={suffix: dict(identity["ADDR"]) for suffix in ("", "_ct", "_sd")})

    def create_row(self, request="line:create"):
        marker = self.attempt.Attempt(EP, request, USER, fields={"people_id": PID}, mode="create",
                                      customer_id=None, addresses=None, baseline=None)
        return self.ledger.rows[marker.name]

    def test_created_customer_is_recovered_with_its_customer_code(self):
        self.transport.records.clear()
        self.transport.searches = [[], [], [], []]  # preflight + three readbacks all miss
        self.assertEqual(self.create()["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.writes(), ["cus/new.php"])
        self.transport.searches = None  # search sees it now
        retry = self.create()
        self.assertTrue(retry["success"], retry)
        self.assertEqual((retry["customer_id"], retry["mode"]), ("created", "create"))
        self.assertEqual(retry["cuscode"], "AUTO-NEXT")  # the code DMS gave the new customer
        self.assertEqual(self.writes(), [])

    def test_local_snapshot_reaches_a_customer_the_search_still_hides(self):
        self.transport.records.clear()
        self.transport.searches = [[], [], [], []]
        self.create()
        self.local = (True, "created")
        self.transport.searches = [[]]
        self.assertTrue(self.create()["success"])
        self.assertEqual(self.writes(), [])

    def test_create_is_not_written_only_after_clean_misses_snapshot_absence_and_grace(self):
        self.transport.records.clear()
        self.lose_post("cus/new.php")
        self.transport.searches = [[]]
        self.create()
        self.assertEqual(self.writes(), ["cus/new.php"])
        for local, wait, expected in (((True, None), 60, "manual"), ((False, None), 3600, "manual"),
                                      ((True, None), 0, "failed")):
            with self.subTest(local=local, wait=wait):
                self.local = local
                self.later(wait)
                self.transport.searches = [[], [], []]
                out = self.create()
                self.assertEqual(self.create_row()["status"], expected)
                self.assertEqual(out.get("reconciled"), "not_written" if expected == "failed" else None)
                self.assertEqual(self.writes(), [])
                self.assertEqual(self.transport.searches, [])  # three clean searches, then stop

    def test_search_that_fails_is_not_absence(self):
        self.transport.records.clear()
        self.transport.searches = [[], [], [], []]
        self.create()
        self.later(3600)
        self.transport.searches = ["<html>login</html>"]
        self.assertEqual(self.create()["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.create_row()["status"], "manual")


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
