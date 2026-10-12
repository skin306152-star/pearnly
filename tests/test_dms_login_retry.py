"""Zero-I/O regressions: a DMS that is slow or unreachable before anything is written.

Old bugs (reproduced 2026-10-12 on the test machine with the unreachable / technical / slow_booking
fault switches):

* the booking and the customer intake kept two retry lists. A booking that hit a network error
  before submitting (``ERR_DMS_TECHNICAL``) got one sentence without a button, the old card answered
  "expired", and 11-16 minutes later the stale-claim job sent "system error, please retry";
* the first and second failure already said "cannot reach the DMS system, contact the
  administrator" - the final sentence - next to a Retry button;
* nothing retried by itself: a DMS that was slow once always cost the operator a tap;
* a job stopped at its 600-second limit was reported as "internal server error".

Now (owner decision 2026-10-13): in the two LINE write jobs (booking, customer create / update) a
login page that never opened (nothing submitted yet) is retried once in the same job with the next
budget; the business function is never retried. Both flows share one retry rule
(``config/pacing.RETRYABLE``), the retry text is the existing network-error sentence, the
administrator sentence is kept for the third attempt, and a job time limit in those two writes is a
retryable network error (unknown, never retried, once the order was registered).

The login tests run the real ``adapter.session`` login step and ``adapter.operations`` over a fake
browser page; the flow tests run the real booking flow over the in-memory Frappe of
``test_dms_booking_outcome`` and the real intake flow over stand-ins. Nothing logs in, posts to DMS,
sends LINE or touches a Site.
"""
from __future__ import annotations

import ast
import csv
import importlib
import logging
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _app in ("pearnly_core", "pearnly_dms"):
    sys.path.insert(0, str(ROOT / "apps" / _app))

from test_dms_booking_outcome import QA, UID, UNKNOWN, Harness, err  # noqa: E402

DMS = ROOT / "apps/pearnly_dms/pearnly_dms"


def _stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _isolate(test):
    guard = patch.dict(sys.modules)
    guard.start()
    test.addCleanup(guard.stop)
    for name in list(sys.modules):
        if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
            del sys.modules[name]


def _table(lang):
    with (DMS / f"translations/{lang}.csv").open(encoding="utf-8", newline="") as handle:
        return {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2}


class JobTimeoutException(Exception):
    """Stands in for ``rq.timeouts.JobTimeoutException`` (recognised by class name)."""


class Page:
    """A Playwright page: ``never`` = the login page does not open; ``silent`` = it opens, the
    sign-in is clicked and DMS never answers; ``ok`` = signed in."""

    def __init__(self, mode):
        self.mode, self.url = mode, "https://dms.invalid/index.php"

    def on(self, event, callback):
        pass

    def remove_listener(self, event, callback):
        pass

    def goto(self, url, wait_until=None, timeout=None):
        if self.mode == "never":
            raise TimeoutError("Timeout %sms exceeded." % timeout)

    def wait_for_selector(self, selector, state=None, timeout=None):
        pass

    def fill(self, selector, value):
        pass

    def click(self, selector, timeout=None):
        if self.mode == "ok":
            self.url = "https://dms.invalid/main.php"

    def wait_for_timeout(self, ms):
        pass


class LoginRetry(unittest.TestCase):
    def setUp(self):
        _isolate(self)
        logging.disable(logging.CRITICAL)  # the code under test logs the failures it handles
        self.addCleanup(logging.disable, logging.NOTSET)
        self.state, self.pages, self.budgets, self.calls = {}, [], [], []
        cache = SimpleNamespace(get_value=lambda key, use_local_cache=False: self.state.get(key),
                                set_value=lambda key, value, expires_in_sec=None: self.state.__setitem__(key, value))
        _stub("frappe", _=lambda text: text, local=SimpleNamespace())
        _stub("pearnly_core.browser", BrowserSession=object)
        _stub("pearnly_core.endpoints", read_password=lambda *a, **kw: "")
        _stub("pearnly_core.session_lock", account_lock=None, dms_login_key=lambda url, user: url + user,
              SessionLockUnavailable=type("SessionLockUnavailable", (Exception,), {}))
        state = _stub("pearnly_core.execution.state", store=lambda: cache)
        _stub("pearnly_core.execution", __path__=[], state=state)
        _stub("pearnly_dms.adapter.read_priority", foreground=None, yield_to_foreground=None,
              wait_for_foreground=None, ReadDeferred=type("ReadDeferred", (Exception,), {}))
        _stub("pearnly_dms.adapter.push", build_adapter=self.build_adapter)
        _stub("pearnly_dms.line.errors", friendly=lambda code, lang=None: {"th": "dms.err." + code})
        self.session = importlib.import_module("pearnly_dms.adapter.session")
        self.ops = importlib.import_module("pearnly_dms.adapter.operations")
        self.pacing = importlib.import_module("pearnly_dms.config.pacing")

    # ── stand-ins ──────────────────────────────────────────────────────────────
    def build_adapter(self, name):
        test, perform = self, self.session.MrerpDmsAdapter._perform_login

        class Adapter:
            DEFAULT_PAGE_TIMEOUT_MS = 30_000
            LOGIN_TIMEOUT_MS = 20_000
            login_url = "https://dms.invalid/index.php"
            has_distinct_admin_creds = concurrent_login_detected = False
            last_dialog = ""
            page_timeout_ms = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def login(self):
                test.budgets.append(self.page_timeout_ms)
                perform(self, Page(test.pages.pop(0)), "sales", "pw")  # the real login step

            _classify_login_body = test.session.MrerpDmsAdapter._classify_login_body

            def client(self):
                return "client"

        return Adapter(), None

    def run_job(self, pages, *, outcome=None, raises=None, **kwargs):
        self.pages = list(pages)

        def business(client, adapter):
            self.calls.append(client)
            if raises:
                raise raises
            return outcome if outcome is not None else {"ok": True, "booking_no": "BK-1"}

        return self.ops.run_logged_in("EP-A", business, **kwargs)

    def failures(self):
        return self.pacing.summary("EP-A")["failures"]

    # ── the automatic retry ────────────────────────────────────────────────────
    def test_login_page_that_never_opened_is_retried_once_in_the_same_job(self):
        done = {"ok": True, "booking_no": "BK-1"}
        result = self.run_job(["never", "ok"], outcome=done, line_write=True)
        self.assertEqual(result, done)  # the business result as it is, no extra keys
        self.assertEqual(self.budgets, [30_000, 60_000])  # the next tier of the shared ladder
        self.assertEqual(len(self.calls), 1)  # the business function ran exactly once

    def test_still_not_open_reports_the_tier_it_reached(self):
        result = self.run_job(["never", "never"], line_write=True)
        self.assertEqual((result["ok"], result["error_code"], result["login_attempt"]),
                         (False, "ERR_DMS_UNREACHABLE", 2))
        self.assertEqual(self.budgets, [30_000, 60_000])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.failures(), 2)

    def test_from_the_second_tier_the_retry_uses_the_third(self):
        result = self.run_job(["never", "never"], line_write=True, attempt=2)
        self.assertEqual(self.budgets, [60_000, 90_000])
        self.assertEqual(result["login_attempt"], 3)

    def test_the_third_tier_is_never_retried(self):
        result = self.run_job(["never"], line_write=True, attempt=3)
        self.assertEqual(self.budgets, [90_000])
        self.assertNotIn("login_attempt", result)

    def test_sign_in_sent_without_an_answer_is_not_retried(self):
        # DMS may already hold a session for it; signing in again at once could count as a duplicate.
        result = self.run_job(["silent"], line_write=True)
        self.assertEqual(result["error_code"], "ERR_DMS_UNREACHABLE")
        self.assertEqual(len(self.budgets), 1)
        self.assertNotIn("login_attempt", result)

    def test_nothing_is_retried_once_the_business_function_started(self):
        # e.g. the admin session's login page timing out inside the write
        inner = self.session.DmsLoginPageUnreachableError("admin login page")
        result = self.run_job(["ok"], raises=inner, line_write=True)
        self.assertEqual(result["error_code"], "ERR_DMS_UNREACHABLE")
        self.assertEqual((len(self.budgets), len(self.calls)), (1, 1))

    def test_other_callers_are_never_retried(self):
        result = self.run_job(["never"])
        self.assertEqual((result["error_code"], self.budgets), ("ERR_DMS_UNREACHABLE", [30_000]))
        self.assertNotIn("login_attempt", result)

    def test_no_retry_when_the_first_failure_opened_the_cooldown(self):
        for _ in range(self.pacing.FAILURE_THRESHOLD - 1):
            self.pacing.note_failure("EP-A", "ERR_DMS_UNREACHABLE")
        result = self.run_job(["never"], line_write=True)
        self.assertEqual(len(self.budgets), 1)
        self.assertNotIn("login_attempt", result)

    def test_no_retry_during_a_cooldown_fast_failure(self):
        for _ in range(self.pacing.FAILURE_THRESHOLD):
            self.pacing.note_failure("EP-A", "ERR_DMS_UNREACHABLE")
        result = self.run_job([], line_write=True)
        self.assertIn("circuit open", result["raw_error"])
        self.assertEqual(self.budgets, [])

    def test_no_retry_when_the_first_try_took_longer_than_90_seconds(self):
        ticks = iter([0.0, self.pacing.AUTO_RETRY_WITHIN_SECONDS + 1.0])
        with patch.object(self.ops, "time", SimpleNamespace(monotonic=lambda: next(ticks))):
            result = self.run_job(["never"], line_write=True)
        self.assertEqual(len(self.budgets), 1)
        self.assertNotIn("login_attempt", result)

    def test_the_normal_path_is_the_same_with_or_without_the_switch(self):
        reads = []
        real = self.pacing.cooldown_left
        with patch.object(self.pacing, "cooldown_left", lambda name: reads.append(name) or real(name)):
            plain = self.run_job(["ok"], outcome={"ok": True, "booking_no": "BK-1"})
            switched = self.run_job(["ok"], outcome={"ok": True, "booking_no": "BK-1"}, line_write=True)
        self.assertEqual(plain, switched)
        self.assertEqual(self.budgets, [30_000, 30_000])
        self.assertEqual(reads, ["EP-A", "EP-A"])  # no extra state read on success

    # ── the 600-second job limit ───────────────────────────────────────────────
    def test_job_time_limit_in_a_write_is_a_retryable_network_error_not_counted(self):
        result = self.run_job(["ok"], raises=JobTimeoutException("Task exceeded maximum timeout value"),
                          line_write=True)
        self.assertEqual(result["error_code"], "ERR_DMS_TECHNICAL")
        self.assertTrue(self.pacing.is_retryable(result["error_code"]))
        self.assertIn("JobTimeoutException", result["raw_error"])
        self.assertEqual(self.failures(), 0)  # never opens the cooldown for other operators
        self.assertEqual(len(self.calls), 1)  # and is never retried automatically

    def test_job_time_limit_elsewhere_keeps_the_unexpected_code(self):
        # The manual snapshot chain retries ERR_UNEXPECTED (snapshots/manual.py); it must not change.
        result = self.run_job(["ok"], raises=JobTimeoutException("Task exceeded maximum timeout value"))
        self.assertEqual(result["error_code"], "ERR_UNEXPECTED")

    def test_other_exceptions_in_a_write_stay_unexpected(self):
        result = self.run_job(["ok"], raises=RuntimeError("bug"), line_write=True)
        self.assertEqual(result["error_code"], "ERR_UNEXPECTED")

    # ── where the switch is used ───────────────────────────────────────────────
    def test_only_the_two_line_write_jobs_pass_the_switch(self):
        users = []
        for path in sorted((ROOT / "apps").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                name = getattr(getattr(node, "func", None), "id", "") or getattr(
                    getattr(node, "func", None), "attr", "")
                if (isinstance(node, ast.Call) and name == "run_logged_in"
                        and any(kw.arg == "line_write" for kw in node.keywords)):
                    users.append(path.relative_to(DMS).as_posix())
        self.assertEqual(sorted(users), ["booking/service.py", "intake/service.py"])
        for path in ("booking/service.py", "intake/service.py"):
            source = (DMS / path).read_text(encoding="utf-8")
            self.assertIn("catch_up=True, line_write=True)", source)


class LoginStep(unittest.TestCase):
    """The real login step tells "the page never opened" apart from "signed in, no answer"."""

    def setUp(self):
        LoginRetry.setUp(self)

    build_adapter = LoginRetry.build_adapter

    def login(self, mode):
        adapter, _ = self.build_adapter("EP-A")
        adapter.page_timeout_ms = 30_000
        self.pages = [mode]
        adapter.login()

    def test_page_that_never_opened_is_its_own_kind_of_unreachable(self):
        with self.assertRaises(self.session.DmsLoginPageUnreachableError):
            self.login("never")

    def test_sign_in_without_an_answer_is_plain_unreachable(self):
        with self.assertRaises(self.session.DmsUnreachableError) as raised:
            self.login("silent")
        self.assertNotIsInstance(raised.exception, self.session.DmsLoginPageUnreachableError)


class BookingRetry(Harness):
    """The booking job over the real session store, flow and stale-claim recovery."""

    def before_any_registration(self, code):
        def book(endpoint, payload, files, failed, on_attempt=None):
            self.posts += 1
            return err(code)  # failed before the order was registered: nothing was posted

        self.flow.book_in_session = book

    def test_network_error_before_submitting_gets_its_retry_card_at_once(self):
        self.before_any_registration("ERR_DMS_TECHNICAL")
        first = self.confirm()
        rearmed = self.session()["payload"]
        self.assertEqual(self.session()["state"], "booking_review")
        self.assertNotEqual(rearmed["nonce"], first["nonce"])  # the new card can be confirmed
        self.assertIsNone(rearmed.get("booking_claim"))
        self.assertEqual(rearmed["qa"], QA)  # every answer is kept
        self.assertEqual(self.cards, [(UID, {"retry": rearmed["nonce"], "text": "dms.err.ERR_DMS_TECHNICAL"})])
        self.assertEqual(self.texts, [])  # old bug: one sentence, no button

    def test_the_stale_claim_job_has_nothing_to_send_later(self):
        # Old bug: the claim stayed open and 11-16 minutes later "system error, please retry" arrived.
        self.before_any_registration("ERR_DMS_TECHNICAL")
        self.confirm()
        claim_recovery = importlib.import_module("pearnly_dms.booking.claim_recovery")
        self.age(minutes=20)
        self.assertFalse(claim_recovery.recover(UID))
        self.assertEqual(len(self.cards), 1)

    def test_unreachable_retry_card_says_the_network_error_not_contact_admin(self):
        self.before_any_registration("ERR_DMS_UNREACHABLE")
        self.confirm()
        (uid, card), = self.cards
        self.assertEqual(card["text"], "dms.err.ERR_DMS_TECHNICAL")

    def test_network_error_or_time_limit_after_the_post_is_unknown_not_retry(self):
        # A job stopped at its limit after the order was registered: never "please try again later".
        self.outcome = lambda number: err("ERR_DMS_TECHNICAL")
        self.confirm()
        self.assertEqual(self.cards, [])
        self.assertEqual(self.texts, [(UID, "dms.err." + UNKNOWN + "\ndms.card.TXT_BOOKING_REF|booking_no=BK-1")])
        record, = self.records()
        self.assertEqual((record["status"], record["booking_no"]), ("Unverified", "BK-1"))
        self.assertIsNone(self.session())
        self.assertEqual(self.posts, 1)

    def test_failures_a_retry_cannot_fix_stay_one_sentence(self):
        for code in ("ERR_DMS_AUTH", "ERR_DMS_ADMIN_AUTH", "ERR_UNEXPECTED"):
            with self.subTest(code=code):
                self.texts.clear()
                self.before_any_registration(code)
                self.confirm()
                self.assertEqual(self.texts, [(UID, "dms.err." + code)])
                self.assertEqual(self.cards, [])

    def test_one_retry_rule_for_booking_and_intake(self):
        pacing = importlib.import_module("pearnly_dms.config.pacing")
        retryable = self.flow._retryable_result
        for code in sorted(pacing.RETRYABLE):
            with self.subTest(code=code):
                self.assertTrue(retryable({"error_code": code}))
                self.assertFalse(retryable({"error_code": code, "booking_id": "R1"}))
                self.assertFalse(retryable({"error_code": code, "response_body": {"submitted": True}}))
        for code in ("ERR_DMS_AUTH", "ERR_DMS_ADMIN_AUTH", "ERR_UNEXPECTED", "ERR_DMS_MASTER_UNMATCHED"):
            self.assertFalse(retryable({"error_code": code}), code)


class IntakeRetry(unittest.TestCase):
    """The customer create / update job: the real `intake.flow` and the real retry text."""

    def setUp(self):
        _isolate(self)
        self.sessions, self.sent, self.pushed, self.queued, self.results = {}, [], [], [], []
        tables = {lang: _table(lang) for lang in ("th", "en", "zh", "ja")}
        self.tables = tables
        _stub("frappe", db=SimpleNamespace(commit=lambda: None))
        _stub("pearnly_core", __path__=[])
        line = _stub("pearnly_core.line", reply=lambda token, messages: None)
        store = _stub("pearnly_core.line_session", get_session=lambda uid: self.sessions.get(uid),
                      set_session=lambda uid, state, payload, **kw: self.sessions.__setitem__(
                          uid, {"state": state, "payload": payload}))
        sys.modules["pearnly_core"].line, sys.modules["pearnly_core"].line_session = line, store
        _stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: tables[lang or "th"].get(key, key),
              line_lang=lambda: "th")
        _stub("pearnly_dms.line.cards", TXT_SAVE_FAIL="TXT_SAVE_FAIL", TXT_EXPIRED="TXT_EXPIRED",
              TXT_NO_ENDPOINT="TXT_NO_ENDPOINT",
              retry_message=lambda text: {"text": text, "quickReply": "retry"})
        for name in ("pearnly_dms.intake.draft", "pearnly_dms.intake.scan", "pearnly_dms.ocr.ocr_review"):
            _stub(name)
        _stub("pearnly_dms.intake.service", push_idcard_fields=lambda endpoint, **kw: (
            self.results.append(kw["attempt"]) or dict(self.result)))
        _stub("pearnly_dms.ocr.ocr_service", resolve_dms_endpoint=lambda user, ep: {"name": "EP-A"})

        class Stage:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        _stub("pearnly_dms.ocr.timing", stage=lambda name: Stage(), traced=lambda name: (lambda fn: fn))
        _stub("pearnly_dms.line.out", start_loading=lambda uid: None,
              push_or_reply_text=lambda uid, text, token="": self.pushed.append(text),
              send=lambda uid, message, token="": self.sent.append(message),
              enqueue=lambda job, fn, *args: self.queued.append((job, fn.__name__, args)))
        _stub("pearnly_dms.line.identity", matches=lambda binding, uid: True)
        self.flow = importlib.import_module("pearnly_dms.intake.flow")
        self.flow._write_push_log = lambda **kw: None
        self.binding = {"tenant_id": "lab", "user_id": "sales-a@example.invalid"}

    def text(self, code):
        return self.tables["th"]["dms.err." + code]

    def write(self, result, attempt=1):
        self.result = result
        draft = {"people_id": "1100000000001", "name": "Local Fixture"}
        payload = {"nonce": None, "draft": draft, "customer_id": "C-77", "endpoint_id": "EP-A"}
        if UID not in self.sessions:
            self.sessions[UID] = {"state": "reviewing", "payload": payload}
        self.flow.write_update(self.binding, UID, payload, attempt)

    def failure(self, code, **extra):
        return {"ok": False, "success": False, "error_code": code,
                "error_friendly": {"th": self.text(code)}, **extra}

    def tap_retry(self):
        self.flow._run_retry(self.binding, UID, "token", self.sessions[UID])
        job, name, args = self.queued.pop()
        return args[3]  # the attempt the retry runs with

    def test_first_failure_offers_retry_with_the_network_error_text(self):
        # Old bug: the first failure already said "... contact the administrator" next to Retry.
        self.write(self.failure("ERR_DMS_UNREACHABLE"))
        self.assertEqual(self.sent, [{"text": self.text("ERR_DMS_TECHNICAL"), "quickReply": "retry"}])
        self.assertNotEqual(self.text("ERR_DMS_TECHNICAL"), self.text("ERR_DMS_UNREACHABLE"))
        self.assertEqual(self.sessions[UID]["payload"]["dms_retry"]["attempt"], 1)
        self.assertEqual(self.tap_retry(), 2)

    def test_the_tier_the_job_reached_is_carried_into_the_ladder(self):
        # The job already retried the login page with the second tier: one tap left, then the end.
        self.write(self.failure("ERR_DMS_UNREACHABLE", login_attempt=2))
        self.assertEqual(self.sessions[UID]["payload"]["dms_retry"]["attempt"], 2)
        self.assertEqual(self.sent[-1]["text"], self.text("ERR_DMS_TECHNICAL"))
        attempt = self.tap_retry()
        self.assertEqual(attempt, 3)
        self.write(self.failure("ERR_DMS_UNREACHABLE"), attempt)
        self.assertEqual(self.pushed, [self.text("ERR_DMS_UNREACHABLE")])  # the final sentence, no button
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("dms_retry", self.sessions[UID]["payload"])
        self.assertEqual(self.results, [1, 3])

    def test_network_error_and_busy_keep_their_own_text(self):
        for code in ("ERR_DMS_TECHNICAL", "ERR_DMS_BUSY"):
            with self.subTest(code=code):
                self.sessions.clear()
                self.write(self.failure(code))
                self.assertEqual(self.sent[-1]["text"], self.text(code))

    def test_a_refusal_is_still_one_sentence(self):
        self.write(self.failure("ERR_DMS_AUTH"))
        self.assertEqual((self.pushed, self.sent), ([self.text("ERR_DMS_AUTH")], []))


class Copy(unittest.TestCase):
    def test_both_sentences_exist_in_all_four_languages_and_differ(self):
        for lang in ("th", "en", "zh", "ja"):
            table = _table(lang)
            with self.subTest(lang=lang):
                technical = table.get("dms.err.ERR_DMS_TECHNICAL", "").strip()
                unreachable = table.get("dms.err.ERR_DMS_UNREACHABLE", "").strip()
                self.assertTrue(technical and unreachable)
                self.assertNotEqual(technical, unreachable)

    def test_retry_text_swaps_only_the_unreachable_sentence(self):
        _isolate(self)
        tables = {lang: _table(lang) for lang in ("th", "en", "zh", "ja")}
        _stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: tables[lang].get(key, key))
        errors = importlib.import_module("pearnly_dms.line.errors")
        for lang in tables:
            self.assertEqual(errors.retry_text("ERR_DMS_UNREACHABLE", "x", lang),
                             tables[lang]["dms.err.ERR_DMS_TECHNICAL"])
            self.assertEqual(errors.retry_text("ERR_DMS_BUSY", "x", lang), "x")
            self.assertEqual(errors.retry_text("", "x", lang), "x")


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
