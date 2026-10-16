"""Zero-I/O regressions: a submitted booking that DMS rejected, or whose result is unknown, never locks the LINE.

Old bug (reproduced 2026-10-12 on the test machine with the booking_reject / booking_readback fault
switches): the submission evidence lived in the operator's one LINE conversation row, and that row can
be neither replaced nor deleted while it carries the evidence. So

* a plain DMS rejection (``err::``) locked the operator's whole LINE for good (menu, customers and
  queries all got the same sentence) and the scanner logged into DMS every 5 minutes, forever;
* an unknown result locked it for at least 11-16 minutes, and for good after any unbind or rebind
  (readback required the current binding revision) or when the order had never been created;
* the message carried no booking number and the ledger recorded "failed".

These run the real shared session store (``pearnly_core.line_session``) and the real
``booking_attempt``, ``booking_ledger``, ``delivery``, ``recovery`` and ``booking_flow`` over an
in-memory stand-in for Frappe's documents. The DMS session, LINE and the cards are replaced.
Nothing logs in, posts to DMS, sends LINE or touches a Site.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime
import importlib
import json
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _app in ("pearnly_core", "pearnly_dms"):
    sys.path.insert(0, str(ROOT / "apps" / _app))

START = datetime.datetime(2026, 10, 13, 9, 0, 0)
SESSION, DELIVERY, LOG, ENDPOINT = ("Pearnly LINE Session", "DMS Booking Delivery",
                                    "Pearnly Push Log", "Pearnly ERP Endpoint")
UNKNOWN = "ERR_DMS_BOOKING_OUTCOME_UNKNOWN"
UID, OTHER_UID = "U-sales-a", "U-sales-b"
BINDING = {"tenant_id": "lab", "user_id": "sales-a@example.invalid", "binding_revision": "r1"}
OTHER_BINDING = {"tenant_id": "lab", "user_id": "sales-b@example.invalid", "binding_revision": "r1"}
QA = {"endpoint_id": "EP-A", "customer": {"id": "C-77", "name": "Local Fixture"},
      "advisor": {"id": "A1", "name": "Advisor"}, "files": {}, "payments": [],
      "answers": {"car": {"id": "car", "label": "Local car"}, "delivery_date_be": "30/10/2569"}}
REAL = ("pearnly_core.line_session", "pearnly_dms.booking.booking_attempt",
        "pearnly_dms.booking.booking_ledger", "pearnly_dms.booking.delivery",
        "pearnly_dms.booking.recovery", "pearnly_dms.booking.conversation.booking_flow")


class Row(dict):
    __getattr__ = dict.get


def _dt(value):
    return value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value))


def _match(row, filters):
    for key, want in (filters or {}).items():
        have = row.get(key)
        if isinstance(want, list):
            op, value = want
            if op == "in" and have not in value:
                return False
            if op == "<=" and (have is None or _dt(have) > _dt(value)):
                return False
        elif have != want:
            return False
    return True


class Doc:
    """Enough of a Frappe document for the code under test (attributes, insert, save)."""

    def __init__(self, db, doctype, data):
        object.__setattr__(self, "_db", db)
        object.__setattr__(self, "_dt", doctype)
        object.__setattr__(self, "_data", dict(data))
        object.__setattr__(self, "flags", types.SimpleNamespace())

    def __getattr__(self, key):
        return self._data.get(key)

    def __setattr__(self, key, value):
        self._data[key] = value

    def insert(self, ignore_permissions=False, set_name=None, ignore_if_duplicate=False):
        db, doctype = self._db, self._dt
        name = set_name or self._data.get("name") or (
            self._data.get("line_user_id") if doctype == SESSION else None)
        if name is None:
            db.counter += 1
            name = f"{doctype}-{db.counter}"
        if name in db.tables[doctype]:
            if ignore_if_duplicate:
                return self
            raise AssertionError("duplicate document " + name)
        self._data.update(name=name, creation=db.clock())
        db.tables[doctype][name] = Row(self._data)
        db.events.append(("insert", doctype, name))
        return self

    def save(self, ignore_permissions=False):
        self._db.tables[self._dt][self._data["name"]] = Row(self._data)
        self._db.events.append(("save", self._dt, self._data["name"]))


class Db:
    def __init__(self, clock):
        self.clock = clock
        self.tables = {SESSION: {}, DELIVERY: {}, LOG: {}, ENDPOINT: {}}
        self.events = []
        self.counter = 0

    def _name(self, doctype, filters):
        table = self.tables[doctype]
        if isinstance(filters, str):
            return filters if filters in table else None
        return next((name for name, row in table.items() if _match(row, filters)), None)

    def exists(self, doctype, filters):
        return self._name(doctype, filters)

    def get_value(self, doctype, filters, fields="name", as_dict=False):
        name = self._name(doctype, filters)
        if name is None:
            return None
        row = self.tables[doctype][name]
        if isinstance(fields, str):
            return name if fields == "name" else row.get(fields)
        picked = Row({key: name if key == "name" else row.get(key) for key in fields})
        return picked if as_dict else tuple(picked.values())

    def set_value(self, doctype, name, field, value=None):
        changes = field if isinstance(field, dict) else {field: value}
        self.tables[doctype][name].update(changes)
        self.events.append(("set", doctype, name, tuple(sorted(changes))))

    def delete(self, doctype, filters):
        for name in [name for name, row in self.tables[doctype].items() if _match(row, filters)]:
            del self.tables[doctype][name]
            self.events.append(("delete", doctype, name))

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        pass

    def sql(self, query, values=(), as_dict=False):
        sessions = self.tables[SESSION]
        if "for update" in query:
            row = sessions.get(values[0])
            return [Row(state=row["state"], payload=row["payload"], expires_at=row["expires_at"])] if row else []
        if "$.booking_attempt') is not null" in query:
            return [Row(name=name, line_user_id=row["line_user_id"], payload=row["payload"])
                    for name, row in sorted(sessions.items())
                    if name > values[0] and row["state"] == "booking_review"
                    and json.loads(row["payload"] or "{}").get("booking_attempt")][:50]
        raise AssertionError("unexpected SQL: " + query)

    def get_all(self, doctype, filters=None, fields=None, order_by=None, limit_page_length=None, pluck=None):
        rows = [row for row in self.tables[doctype].values() if _match(row, filters)]
        if order_by:
            key = order_by.split()[0]
            rows.sort(key=lambda row: _dt(row[key]))
        if limit_page_length:
            rows = rows[:limit_page_length]
        if pluck:
            return [row.get(pluck) for row in rows]
        return [Row({key: row.get(key) for key in fields}) for row in rows]


class Redis:
    """The shared state store: session lock and recovery leases. Keys in `busy` are held by someone else."""

    def __init__(self):
        self.keys, self.busy = {}, set()

    def make_key(self, key):
        return key

    def set(self, key, value, nx=False, ex=None, px=None):
        if key in self.busy or (nx and key in self.keys):
            return False
        self.keys[key] = value
        return True

    def eval(self, script, count, key, owner):
        if self.keys.get(key) == owner:
            del self.keys[key]
            return 1
        return 0


class Harness(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.now = START
        self.db = Db(lambda: self.now)
        self.redis = Redis()
        self.cache_values, self.enqueued = {}, []
        self.bound = True           # the operator's live LINE binding still matches the submitter
        self.endpoint_live = True   # the submitter's DMS endpoint still resolves
        self.found = "R-1"          # what a read-only readback finds (None = nothing)
        self.posts, self.reads, self.pushes, self.texts, self.cards = 0, 0, [], [], []
        self.replies = []
        self.outcome = lambda number: {"ok": True, "booking_id": "R-%s" % number, "booking_no": number,
                                       "attach_ok": True, "attached": 0, "attach_failed": []}
        self.db.tables[ENDPOINT].update({
            "EP-A": Row(system_url="https://dms.invalid/index.php", username="sales-a"),
            "EP-B": Row(system_url="https://dms.invalid/index.php", username="sales-b"),
            "EP-OTHER": Row(system_url="https://other.invalid/index.php", username="sales-c")})
        self._stubs()
        self.store, self.attempt, self.ledger, self.delivery, self.recovery, self.flow = (
            importlib.import_module(name) for name in REAL)

    # ── stand-ins ──────────────────────────────────────────────────────────────
    def _stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module

    def _stubs(self):
        test, db = self, self.db

        def get_doc(arg, name=None):
            if isinstance(arg, dict):
                return Doc(db, arg["doctype"], {k: v for k, v in arg.items() if k != "doctype"})
            return Doc(db, arg, {**db.tables[arg][name], "name": name})

        def add_to_date(date=None, seconds=0, minutes=0, hours=0, days=0, **kwargs):
            base = self.now if date is None else _dt(date)
            moved = base + datetime.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
            return str(moved) if isinstance(date, str) else moved

        cache = types.SimpleNamespace(
            get_value=lambda key, use_local_cache=False: self.cache_values.get(key),
            set_value=lambda key, value, expires_in_sec=None: self.cache_values.__setitem__(key, value))
        self._stub("frappe", db=db, get_doc=get_doc, new_doc=lambda doctype: Doc(db, doctype, {}),
                   get_all=db.get_all, cache=lambda: cache, QueryDeadlockError=type("Deadlock", (Exception,), {}),
                   enqueue=lambda method, **kw: self.enqueued.append((method, kw)),
                   utils=types.SimpleNamespace(
                       now=lambda: self.now.strftime("%Y-%m-%d %H:%M:%S.%f"),
                       get_datetime=lambda value=None: self.now if value is None else _dt(value),
                       add_to_date=add_to_date))
        self._stub("pearnly_core.execution.state", store=lambda: self.redis)
        self._stub("pearnly_core.line", push=lambda uid, messages, retry_key=None: (
            self.pushes.append((uid, messages[0]["text"], retry_key)), True)[1])

        @contextmanager
        def line_language(uid):
            yield

        self._stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key + "".join(
            f"|{k}={v}" for k, v in sorted(kw.items())), line_lang=lambda: "th", line_language=line_language)
        # retry_text: the sentence next to a Retry button (the real rule is pinned in test_dms_login_retry).
        self._stub("pearnly_dms.line.errors", friendly=lambda code, lang=None: {"th": "dms.err." + code},
                   retry_text=lambda code, text, lang: (
                       "dms.err.ERR_DMS_TECHNICAL" if code == "ERR_DMS_UNREACHABLE" else text))
        self._stub("pearnly_dms.line.identity", matches=lambda binding, uid: self.bound,
                   current=lambda uid: {"user": "x"})
        self._stub("pearnly_dms.line.cards", ACT_CONFIRM_BOOKING="confirm", ACT_CANCEL_BOOKING="cancel",
                   ACT_RETRY_BOOKING="retry", TXT_BOOKING_FAIL="TXT_BOOKING_FAIL", TXT_EXPIRED="TXT_EXPIRED",
                   TXT_SYSTEM_ERROR="TXT_SYSTEM_ERROR", TXT_BOOKING_CANCELLED="TXT_BOOKING_CANCELLED",
                   field_labels=lambda: {},
                   booking_receipt_text=lambda number, car, date, advisor: "receipt " + number,
                   booking_receipt_message=lambda number, car, date, advisor, review=None, attach_ok=True,
                   record="", nonce="": {
                       "type": "text", "text": "receipt " + number + (
                           "" if review is None else " review=%d" % len(review["diffs"])) + (
                           " card=" + nonce if nonce else "")})
        # The difference card's buttons register their own card (``review_fix``); here only that it happens.
        self.review_cards = []
        self._stub("pearnly_dms.booking.conversation.review_fix", open_card=lambda record, uid, number, bid, diffs: (
            self.review_cards.append((uid, number, bid, [d["item"] for d in diffs])), "c%d" % len(self.review_cards))[1])
        self._stub("pearnly_dms.line.out", push_text=lambda uid, text: self.texts.append((uid, text)) or True,
                   reply_text=lambda token, text: self.replies.append((token, text)), enqueue=lambda *a: None,
                   start_loading=lambda uid: None,
                   send=lambda uid, msg, token="": self.cards.append((uid, msg)))
        self._stub("pearnly_dms.booking.conversation.qa_cards", TXT_MASTER_CHANGED="TXT_MASTER_CHANGED",
                   preview_card=lambda qa, nonce, notice="": {"card": nonce, "notice": notice},
                   booking_retry_message=lambda text, nonce, code="": {"retry": nonce, "text": text})
        self._stub("pearnly_dms.booking.attachments", download=lambda qa, missing=None: ([], []),
                   expected_names=lambda qa: set())
        self._stub("pearnly_dms.booking.service", book_in_session=self.book)
        self._stub("pearnly_dms.ocr.ocr_service", resolve_dms_endpoint=lambda user, endpoint_id: (
            {"id": endpoint_id or "EP-A", "name": endpoint_id or "EP-A"} if self.endpoint_live else None))
        self._stub("pearnly_dms.adapter.operations", run_logged_in=self.dms_session)

        def verify(client, number, submitted):
            self.reads += 1
            return self.found

        self._stub("pearnly_dms.booking.booking_submit", verify_created_booking=verify)

    def book(self, endpoint, payload, files, failed, on_attempt=None):
        """The one booking POST: registered before the external write, then the fixture outcome."""
        self.posts += 1
        number = "BK-%d" % self.posts
        on_attempt.capture_submission(number, {"txtdocno": number, "cusval": payload["qa"]["customer"]["id"]})
        return self.outcome(number)

    def dms_session(self, endpoint, fn, **kwargs):
        """A read-only background DMS session; the form shows nothing attached."""
        self.assertTrue(kwargs.get("background"))
        client = types.SimpleNamespace(_post_text=lambda path, data: "", _parse_form_defaults=lambda body: {})
        return fn(client, None)

    # ── helpers ────────────────────────────────────────────────────────────────
    def claimed(self, uid=UID, qa=None):
        nonce = "n-%d" % (len(self.db.events) + 1)
        self.store.set_session(uid, "booking_review", {"nonce": nonce, "qa": qa or QA})
        return self.store.consume_nonce(uid, "booking_review", nonce)

    def confirm(self, uid=UID, binding=BINDING, qa=None):
        payload = self.claimed(uid, qa)
        self.flow._execute_owned_booking(binding, uid, payload)
        return payload

    def session(self, uid=UID):
        return self.store.get_session(uid)

    def records(self, status=None):
        return [row for row in self.db.tables[DELIVERY].values() if status in (None, row["status"])]

    def logs(self):
        return list(self.db.tables[LOG].values())

    def free_menu(self, uid=UID):
        """The old bug's symptom: any other conversation write raised while the evidence sat in the session."""
        self.store.set_session(uid, "collecting", {"mode": "customer"})
        self.assertEqual(self.session(uid)["state"], "collecting")

    def age(self, **delta):
        self.now += datetime.timedelta(**delta)


def err(code, **body):
    return {"ok": False, "error_code": code, "error_friendly": {"th": "dms.err." + code},
            "response_body": {**body, "raw_error": ""}}


class Rejected(Harness):
    def test_rejection_returns_the_original_card_and_frees_the_line(self):
        self.outcome = lambda number: err("ERR_DMS_IMPORT", submitted=False, rejected=True, booking_no=number)
        first = self.confirm()
        payload = self.session()["payload"]
        self.assertEqual(self.session()["state"], "booking_review")
        self.assertNotIn("booking_attempt", payload)
        self.assertNotIn("booking_claim", payload)
        self.assertNotEqual(payload["nonce"], first["nonce"])
        self.assertEqual(payload["qa"], QA)  # every answer is kept
        # One message: the existing failure text on top of the original preview card.
        self.assertEqual(self.cards, [(UID, {"card": payload["nonce"], "notice": "dms.err.ERR_DMS_IMPORT"})])
        self.assertEqual(self.texts, [])
        self.assertEqual(self.records(), [])  # nothing for the background to check: no DMS logins
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"], log["error_msg"]), ("failed", "BK-1", "ERR_DMS_IMPORT"))
        self.assertEqual(json.loads(log["response_body"])["booking_no"], "BK-1")
        # Confirming the returned card is a brand-new submission: a new claim, registered again.
        self.outcome = lambda number: {"ok": True, "booking_id": "R-" + number, "booking_no": number,
                                       "attach_ok": True, "attached": 0, "attach_failed": []}
        payload = self.store.consume_nonce(UID, "booking_review", payload["nonce"])
        self.flow._execute_owned_booking(BINDING, UID, payload)
        self.assertEqual(self.posts, 2)  # one rejected, one created
        self.assertIsNone(self.session())
        self.assertEqual(self.records("Delivered")[0]["booking_no"], "BK-2")
        self.free_menu()

    def test_rejection_with_a_different_number_or_without_the_flag_is_not_released(self):
        self.outcome = lambda number: err("ERR_DMS_IMPORT", submitted=False, rejected=True, booking_no="BK-other")
        self.confirm()
        self.assertIsNone(self.session())  # treated as unknown: held, not handed back
        self.assertEqual(self.records()[0]["status"], "Unverified")
        self.assertEqual(self.cards, [])


class Unknown(Harness):
    def assert_held(self, number="BK-1"):
        record, = self.records()
        self.assertEqual((record["status"], record["booking_no"], record["booking_id"]), ("Unverified", number, ""))
        marker = json.loads(record["attempt"])
        self.assertEqual(marker["context"]["submitted"], {"txtdocno": number, "cusval": "C-77"})
        self.assertEqual(marker["context"]["binding"], BINDING)
        self.assertIsNone(self.session())
        self.free_menu()
        return record

    def test_unknown_result_is_held_with_its_number_and_the_line_is_free(self):
        self.outcome = lambda number: err(UNKNOWN, submitted=True, retry_safe=False, booking_no=number)
        self.confirm()
        record = self.assert_held()
        # Evidence first, conversation second (the order recovery.finish uses too).
        inserted = self.db.events.index(("insert", DELIVERY, record["name"]))
        committed = self.db.events.index(("commit",), inserted)
        deleted = self.db.events.index(("delete", SESSION, UID))
        self.assertLess(committed, deleted)
        self.assertEqual(self.texts, [(UID, "dms.err." + UNKNOWN + "\ndms.card.TXT_BOOKING_REF|booking_no=BK-1")])
        self.assertEqual(self.cards, [])  # never a retry card for an unknown write
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"]), ("manual", "BK-1"))
        self.assertEqual(self.posts, 1)

    def test_kicked_off_after_the_post_is_unknown_not_a_retry(self):
        # operations.run_logged_in turns a late duplicate-login dialog into this code and drops
        # `submitted`; its retry card used to bounce off the session and lock the LINE instead.
        self.outcome = lambda number: err("ERR_DMS_CONCURRENT_LOGIN")
        self.confirm()
        self.assert_held()
        self.assertEqual(self.cards, [])
        log, = self.logs()
        self.assertEqual((log["status"], log["error_msg"]), ("manual", UNKNOWN))
        self.assertEqual(json.loads(log["response_body"])["cause"], "ERR_DMS_CONCURRENT_LOGIN")

    def test_blocked_number_advance_after_a_post_is_unknown(self):
        self.outcome = lambda number: err("ERR_DMS_BOOKING_ATTEMPT_BLOCKED")
        self.confirm()
        self.assert_held()

    def test_failure_before_any_registration_keeps_its_retry_card(self):
        def before(endpoint, payload, files, failed, on_attempt=None):
            self.posts += 1
            return err("ERR_DMS_UNREACHABLE")  # the login page never opened: nothing registered

        self.flow.book_in_session = before
        self.confirm()
        self.assertEqual(self.records(), [])
        self.assertEqual(len(self.cards), 1)
        self.assertIn("retry", self.cards[0][1])

    def test_message_takes_the_number_from_the_response_body(self):
        text = self.attempt.message(err(UNKNOWN, booking_no="BK-9"))
        self.assertTrue(text.endswith("booking_no=BK-9"), text)


class SameCustomer(Harness):
    def hold_one(self):
        self.outcome = lambda number: err(UNKNOWN, submitted=True, booking_no=number)
        self.confirm()
        self.texts.clear()

    def test_same_customer_on_the_same_system_is_held_back_without_touching_dms(self):
        self.hold_one()
        other_qa = {**QA, "endpoint_id": "EP-B"}  # another operator, same DMS system
        payload = self.confirm(OTHER_UID, OTHER_BINDING, other_qa)
        self.assertEqual(self.posts, 1)
        rearmed = self.session(OTHER_UID)["payload"]
        self.assertNotEqual(rearmed["nonce"], payload["nonce"])
        self.assertEqual(self.cards, [(OTHER_UID, {"card": rearmed["nonce"], "notice":
                                       "dms.err." + UNKNOWN + "\ndms.card.TXT_BOOKING_REF|booking_no=BK-1"})])

    def test_other_customers_and_other_systems_go_ahead(self):
        self.hold_one()
        self.confirm(OTHER_UID, OTHER_BINDING, {**QA, "endpoint_id": "EP-B", "customer": {"id": "C-78"}})
        self.confirm("U-sales-c", OTHER_BINDING, {**QA, "endpoint_id": "EP-OTHER"})
        self.assertEqual(self.posts, 3)

    def test_the_hold_ends_when_verified_or_after_23_hours(self):
        self.hold_one()
        self.found = None
        self.age(hours=23)
        name = self.records()[0]["name"]
        self.recovery.reconcile_record(name)
        self.assertEqual(self.records()[0]["status"], "Review")
        self.assertEqual(self.reads, 0)  # expired: no DMS login at all
        self.assertEqual(self.recovery.unverified_booking_no("EP-A", QA), "")
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"]), ("manual", "BK-1"))  # left for a person


class Scanner(Harness):
    def stuck(self, uid=UID, binding=BINDING):
        """A worker that died after its POST: evidence registered, nothing sent, nothing held."""
        payload = self.claimed(uid)
        record = self.attempt.recorder("lab", uid, payload, binding=binding, endpoint="EP-A",
                                       endpoint_fingerprint=self.recovery.endpoint_fingerprint("EP-A"))
        record.capture_submission("BK-7", {"txtdocno": "BK-7", "cusval": "C-77"})
        return payload

    def scan(self):
        self.recovery.reconcile_due()
        jobs = list(self.enqueued)
        self.enqueued.clear()
        for method, kwargs in jobs:
            function = getattr(self.recovery, method.rsplit(".", 1)[1])
            function(**{k: v for k, v in kwargs.items() if k in ("line_user_id", "name")})
        return [method.rsplit(".", 1)[1] for method, _ in jobs]

    def test_crashed_worker_is_held_after_the_grace_and_notified_once(self):
        self.stuck()
        self.assertEqual(self.scan(), [])  # the original worker may still be running
        self.age(seconds=661)
        self.assertEqual(self.scan(), ["reconcile"])
        self.assertIsNone(self.session())
        self.assertEqual(self.reads, 0)  # holding never logs into DMS
        self.assertEqual(self.texts, [(UID, "dms.err." + UNKNOWN + "\ndms.card.TXT_BOOKING_REF|booking_no=BK-7")])
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"]), ("manual", "BK-7"))

    def test_registration_older_than_the_check_window_is_freed_without_a_stale_notice(self):
        self.stuck()
        self.age(hours=24)
        self.scan()
        self.assertIsNone(self.session())
        self.assertEqual(self.texts, [])
        self.assertEqual([log["status"] for log in self.logs()], ["manual"])
        self.scan()  # its first check is already due: it expires without reading DMS
        self.assertEqual((self.records()[0]["status"], self.reads), ("Review", 0))

    def test_crash_after_the_success_row_keeps_that_one_row_success(self):
        # The worker wrote the success row, then died before handing the order over (finish), so the
        # registration carries no result. Holding it must not add a second, "manual" row for that order.
        self.stuck()
        self.ledger.log_booking(endpoint="EP-A", payload={"qa": QA}, actor=BINDING["user_id"],
                                result={"ok": True, "booking_no": "BK-7", "booking_id": "R-1"})
        self.age(seconds=661)
        self.scan()
        self.assertIsNone(self.session())
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"]), ("success", "BK-7"))
        self.age(minutes=5)
        self.assertEqual(self.scan(), ["reconcile_record"])
        log, = self.logs()
        self.assertEqual(log["status"], "success")
        self.assertEqual(self.records()[0]["status"], "Delivered")

    def test_hold_twice_notifies_once(self):
        payload = self.stuck()
        self.age(seconds=661)
        self.redis.busy.add("pearnly:line-session:lock:" + UID)  # clearing loses the session lock
        self.assertFalse(self.recovery.hold(UID, payload["nonce"], notify=True))
        self.assertTrue(self.session()["payload"]["booking_attempt"])
        self.redis.busy.clear()
        self.assertTrue(self.recovery.hold(UID, payload["nonce"], notify=True))
        self.assertIsNone(self.session())
        self.assertEqual(len(self.texts), 1)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.logs()), 1)

    def test_rebound_line_is_freed_and_the_order_is_still_checked(self):
        # Old bug: a rebind changed the binding revision; recovery then skipped the order forever
        # while the conversation kept the evidence, so the LINE never worked again.
        self.stuck()
        self.bound = False
        self.age(seconds=661)
        self.scan()
        self.assertIsNone(self.session())
        self.assertEqual(self.texts, [])  # nothing for the newly bound person
        self.age(minutes=5)
        self.assertEqual(self.scan(), ["reconcile_record"])
        self.assertEqual(self.reads, 1)
        record, = self.records()
        self.assertEqual((record["status"], record["last_error"], record["booking_id"]),
                         ("Review", "binding_changed", "R-1"))
        self.assertEqual(self.pushes, [])
        log, = self.logs()  # the same row, now success
        self.assertEqual((log["status"], log["business_key"], log["http_status"]), ("success", "BK-7", 200))


class RecordCheck(Harness):
    def held(self):
        self.outcome = lambda number: err(UNKNOWN, submitted=True, booking_no=number)
        self.confirm()
        self.texts.clear()
        return self.records()[0]["name"]

    def test_found_sends_the_receipt_and_keeps_one_ledger_row(self):
        name = self.held()
        self.found = "R-1"
        self.age(minutes=5)
        self.assertTrue(self.recovery.reconcile_record(name))
        record, = self.records()
        self.assertEqual((record["status"], record["booking_id"]), ("Delivered", "R-1"))
        self.assertEqual([(uid, text) for uid, text, _ in self.pushes], [(UID, "receipt BK-1")])
        log, = self.logs()
        self.assertEqual((log["status"], log["error_msg"]), ("success", ""))
        self.assertEqual(json.loads(log["response_body"])["booking_id"], "R-1")
        self.assertEqual(self.posts, 1)  # checking never posts

    def test_not_found_backs_off_5_10_20_40_then_hourly(self):
        name = self.held()
        self.found = None
        due = [_dt(self.records()[0]["next_attempt_at"]) - START]
        for _ in range(6):
            self.now = _dt(self.records()[0]["next_attempt_at"])
            self.assertFalse(self.recovery.reconcile_record(name))
            due.append(_dt(self.records()[0]["next_attempt_at"]) - self.now)
        self.assertEqual([int(gap.total_seconds() // 60) for gap in due], [5, 10, 20, 40, 60, 60, 60])
        self.assertEqual(self.reads, 6)
        self.assertEqual(self.records()[0]["status"], "Unverified")
        self.assertEqual(self.posts, 1)

    def test_last_check_lands_on_the_23_hour_mark(self):
        name = self.held()
        self.found = None
        self.age(hours=22, minutes=55)  # the next 10-minute gap would pass the 23-hour mark
        self.recovery.reconcile_record(name)
        self.assertEqual(_dt(self.records()[0]["next_attempt_at"]), START + datetime.timedelta(hours=23))

    def test_changed_endpoint_identity_never_logs_in(self):
        name = self.held()
        self.endpoint_live = False
        self.age(minutes=5)
        self.assertFalse(self.recovery.reconcile_record(name))
        self.assertEqual(self.reads, 0)
        record, = self.records()
        self.assertEqual((record["status"], record["last_error"]), ("Unverified", "endpoint_changed"))

    def test_scan_enqueues_only_due_unverified_records(self):
        name = self.held()
        self.recovery.reconcile_due()
        self.assertEqual(self.enqueued, [])
        self.age(minutes=5)
        self.recovery.reconcile_due()
        (method, kwargs), = self.enqueued
        self.assertEqual((method, kwargs["name"], kwargs["job_id"], kwargs["deduplicate"]),
                         ("pearnly_dms.booking.recovery.reconcile_record", name,
                          "dms-reconcile-record-" + name, True))


class NormalPath(Harness):
    def test_success_is_unchanged(self):
        self.confirm()
        self.assertEqual(self.posts, 1)
        self.assertIsNone(self.session())
        record, = self.records()
        self.assertEqual((record["status"], record["booking_id"]), ("Delivered", "R-BK-1"))
        self.assertEqual([text for _, text, _ in self.pushes], ["receipt BK-1"])
        self.assertEqual((self.texts, self.cards), ([], []))
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"]), ("success", "BK-1"))


class ReceiptReview(Harness):
    """The receipt review rides with the verified result to the durable receipt; the ledger keeps item names only."""

    def reviewed(self, diffs):
        self.outcome = lambda number: {"ok": True, "booking_id": "R-" + number, "booking_no": number,
                                       "attach_ok": True, "attached": 0, "attach_failed": [],
                                       "review": {"diffs": diffs}}
        self.confirm()
        log, = self.logs()
        return json.loads(log["response_body"])

    def test_differences_reach_the_receipt_and_the_ledger_keeps_no_values(self):
        body = self.reviewed([{"item": "sum.phone", "sent": "0810000001", "dms": "0810000002"}])
        self.assertEqual([text for _, text, _ in self.pushes], ["receipt BK-1 review=1 card=c1"])
        self.assertEqual(self.review_cards, [(UID, "BK-1", "R-BK-1", ["sum.phone"])])
        self.assertEqual(body["review_diff_items"], ["sum.phone"])
        self.assertNotIn("0810000002", json.dumps(body))
        record, = self.records()
        self.assertEqual(json.loads(record["attempt"])["result"]["review"]["diffs"][0]["item"], "sum.phone")

    def test_all_matched_is_recorded_as_no_differences(self):
        body = self.reviewed([])
        self.assertEqual([text for _, text, _ in self.pushes], ["receipt BK-1 review=0"])
        self.assertEqual(self.review_cards, [])
        self.assertEqual(body["review_diff_items"], [])


class SecondTap(Harness):
    """2026-10-12: a second tap on Confirm said "expired" before the first tap's booking receipt arrived."""

    def tap(self, nonce, token, action="confirm"):
        self.flow.handle_postback(BINDING, UID, token, action, {"nonce": nonce})

    def test_second_tap_while_the_same_card_is_submitting_says_nothing(self):
        for action in ("confirm", "retry"):
            self.store.set_session(UID, "booking_review", {"nonce": "n-" + action, "qa": QA})
            self.tap("n-" + action, "t1", action)
            self.tap("n-" + action, "t2", action)
            self.assertEqual(self.replies, [])
            self.assertEqual(self.session()["payload"]["booking_claim"], "n-" + action)
            self.store.clear_session(UID)
        self.assertEqual(self.posts, 0)  # the queued job is not run here; nothing was submitted twice

    def test_a_card_that_is_done_or_replaced_is_still_expired(self):
        payload = self.confirm()
        self.tap(payload["nonce"], "t1")
        self.store.set_session(UID, "booking_review", {"nonce": "n-new", "qa": QA})
        self.tap("n-old", "t2", "retry")
        self.assertEqual(self.replies, [("t1", "TXT_EXPIRED"), ("t2", "TXT_EXPIRED")])
        self.assertEqual(self.posts, 1)


class ReleaseFunction(Harness):
    """The one new shared-layer function: every guard must match before the evidence is removed."""

    def registered(self):
        payload = self.claimed()
        self.assertTrue(self.store.record_booking_attempt(UID, payload["nonce"], "BK-1", "owner"))
        return payload

    def test_guards(self):
        payload = self.registered()
        new = {**payload, "nonce": "fresh"}
        for args in ((UID, "wrong", "BK-1", new), (UID, payload["nonce"], "BK-2", new),
                     (UID, payload["nonce"], "BK-1", {**payload, "nonce": None}), (UID, "", "BK-1", new)):
            with self.subTest(args=args[1:3]):
                self.assertFalse(self.store.release_rejected_booking_attempt(*args))
        self.assertTrue(self.store.update_booking_attempt(UID, payload["nonce"], {"result": {"ok": True}}))
        self.assertFalse(self.store.release_rejected_booking_attempt(UID, payload["nonce"], "BK-1", new))
        self.assertTrue(self.session()["payload"]["booking_attempt"])

    def test_lock_contention_never_releases(self):
        payload = self.registered()
        self.redis.busy.add("pearnly:line-session:lock:" + UID)
        self.assertFalse(self.store.release_rejected_booking_attempt(UID, payload["nonce"], "BK-1",
                                                                     {**payload, "nonce": "fresh"}))

    def test_release_strips_every_internal_marker_and_rearms(self):
        payload = self.registered()
        self.assertTrue(self.store.release_rejected_booking_attempt(
            UID, payload["nonce"], "BK-1", {**payload, "nonce": "fresh", "booking_attempt": {"x": 1},
                                             "booking_claim": "x", "booking_claimed_at": "x"}, ttl_minutes=30))
        session = self.session()
        self.assertEqual(session, {"state": "booking_review", "payload": {"nonce": "fresh", "qa": QA}})
        self.assertEqual(_dt(self.db.tables[SESSION][UID]["expires_at"]), START + datetime.timedelta(minutes=30))
        self.assertIsNotNone(self.store.consume_nonce(UID, "booking_review", "fresh"))


class Ledger(Harness):
    def test_unknown_is_manual_with_its_number_and_verified_updates_that_row(self):
        unknown = self.attempt.as_unknown(err("ERR_DMS_CONCURRENT_LOGIN"), "BK-5")
        first = self.ledger.log_booking(endpoint="EP-A", payload={"qa": QA}, result=unknown, actor="a")
        again = self.ledger.log_booking(endpoint={"name": "EP-A"}, payload={"qa": QA}, result=unknown, actor="a")
        self.assertEqual(first, again)
        done = self.ledger.log_booking(endpoint="EP-A", payload={"qa": QA}, actor="a",
                                       result={"ok": True, "booking_no": "BK-5", "booking_id": "R-5"})
        self.assertEqual(done, first)
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"], log["http_status"]), ("success", "BK-5", 200))

    def test_failed_result_keeps_the_number_from_its_body(self):
        self.ledger.log_booking(endpoint="EP-A", payload={"qa": QA}, actor="a",
                                result=err("ERR_DMS_IMPORT", rejected=True, booking_no="BK-6"))
        log, = self.logs()
        self.assertEqual((log["status"], log["business_key"]), ("failed", "BK-6"))
        self.assertEqual(json.loads(log["response_body"])["booking_no"], "BK-6")


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
