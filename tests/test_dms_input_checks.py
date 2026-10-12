"""Zero-I/O regressions for the small DMS input gaps (G7, reproduced 2026-10-12 on the test machine).

Old bugs and what each case now proves:

* 12A - the booking editor saved any well-formed delivery date (01/01/2026 was accepted and a new
  preview card went out), while the LINE date picker only offers today to two years ahead. Now
  the editor refuses the same out-of-range dates with the existing ``invalid_delivery`` code
  before the draft or the card changes, using the picker's exact range.
* 12B - menu 4 stored a new DMS password without trying it, so a typo only surfaced later as
  "DMS login failed" in front of a customer. Now the password is tried once (log in, log out,
  nothing read or written) before the stored one is replaced; a refusal keeps the old password
  and answers with a status the page can show (never 401/403, which the page reads as "link
  expired").
* #14 - "Unbind LINE" on the roster wrote the binding row directly, so the shared
  ``pearnly_line_binding_changed`` handlers never ran (no draft clean-up, no menu change until
  the 10-minute reconcile). Now it goes through ``pearnly_core.line_binding.unbind`` like
  disable and delete do.
* #18 - two operators could share one DMS username (case-sensitive check against system
  accounts only). Now system accounts and every other operator, disabled ones included, are
  compared case-insensitively on create and on re-enable, before any DMS sign-in.

The real modules under test run over small stand-ins for Frappe, the session store, LINE output
and the DMS sign-in probe. Nothing logs into DMS, sends LINE messages or touches a Site.
"""
from __future__ import annotations

import ast
import copy
from datetime import date, timedelta
import importlib
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

PID = "1101700998118"
TAKEN = "This account is already used by an operator or another system account"


class Thrown(Exception):
    pass


class Failed(Exception):
    """``browser_api._fail``: the code the page maps to copy, and the HTTP status."""

    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def throw(message, exception=None):
    raise Thrown(message)


class Isolated(unittest.TestCase):
    """Each test imports fresh copies of the Pearnly modules over its own stand-ins."""

    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name in ("frappe", "werkzeug") or name.startswith(("frappe.", "werkzeug.", "pearnly_core",
                                                                  "pearnly_dms")):
                del sys.modules[name]
        self.frappe = self.stub("frappe", _=lambda text: text, throw=throw,
                                whitelist=lambda **kw: (lambda function: function),
                                local=types.SimpleNamespace(site="lab.localhost", lang="th"),
                                PermissionError=PermissionError, ValidationError=ValueError)
        self.stub("frappe.utils", now=lambda: "2026-10-13 00:00:00", add_to_date=lambda *a, **k: "later",
                  cint=int, getdate=lambda value: value, now_datetime=lambda: "now")
        self.frappe.utils = sys.modules["frappe.utils"]
        # Real core modules load from their files; the package __init__ (Frappe hooks) does not run.
        self.stub("pearnly_core", __path__=[str(ROOT / "apps/pearnly_core/pearnly_core")])
        network = patch.object(socket, "create_connection", side_effect=AssertionError("No external IO"))
        network.start()
        self.addCleanup(network.stop)

    def stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)
        return module


def frozen(day):
    """``date`` whose ``today()`` is ``day`` (patched into the editor module only)."""

    class Today(date):
        @classmethod
        def today(cls):
            return day

    return Today


def be(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


# ── 12A: editor delivery date ───────────────────────────────────────────────────
SNAPSHOT = {
    "masters": {"place_books": [["PL1", "P", "สาขาหลัก"]], "cars": [["C1", "DMAX", "2.5"]],
                "term_sales": [["T1", "T", "เงินสด"]], "regis_behalfs": [["R1", "R", "ตนเอง"]],
                "company_banks": [], "prefixes": [["17", "", "นาย"]]},
    "prefixes": [["17", "", "นาย"]],
    "geo": {"provinces": [["10", "", "กรุงเทพมหานคร"]], "districts": [["1001", "", "บางรัก"]],
            "subdistricts": [["100101", "", "สีลม"]], "zipcodes": [["10500", "", "10500"]]},
    "paints": {"C1": [["PT1", "", "แดง"]]},
}
CUSTOMER = {"people_id": PID, "prefix_id": "17", "name": "คุณสมชาย", "birthday_be": "15/05/2530",
            "phone": "0811111111", "house_no": "99/1", "province_id": "10", "district_id": "1001",
            "subdistrict_id": "100101", "zipcode_id": "10500"}


class EditorDeliveryDateTests(Isolated):
    TODAY = date(2026, 10, 13)

    def setUp(self):
        super().setUp()
        self.stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key, line_lang=lambda: "th",
                  all_languages=lambda key: {"th": key})
        self.store = self.stub("pearnly_core.line_session", replace_review_payload=Mock(return_value=True))
        self.stub("pearnly_dms.line.out", send=Mock(side_effect=AssertionError("cards go through the seam")))
        self.stub("pearnly_dms.editor.edit_snapshot", read_edit_snapshot=Mock())
        self.stub("pearnly_dms.intake.service", geo_options=Mock())
        self.stub("pearnly_dms.ocr.ocr_service", resolve_dms_endpoint=Mock())
        self.edit = importlib.import_module("pearnly_dms.editor.booking_edit")
        self.cards = importlib.import_module("pearnly_dms.booking.conversation.qa_cards")
        self.payload = {"nonce": "N1", "qa": {
            "endpoint_id": "EP1", "step": "pay_more", "customer": {"id": "CUST1", "name": CUSTOMER["name"]},
            "draft": dict(CUSTOMER), "customer_edit_form": dict(CUSTOMER),
            "answers": {"delivery_date_be": be(self.TODAY + timedelta(days=7))},
            "files": {"id_card_mid": "MID-ID", "slip_mid": None},
            "payments": [{"channel": "cash", "amount": "5000.00", "extra": {}}]}}
        self.before = copy.deepcopy(self.payload)
        for name, value in (("_review", Mock(return_value=({}, self.payload, {"name": "EP1"}))),
                            ("_snapshot", Mock(return_value=copy.deepcopy(SNAPSHOT)))):
            seam = patch.object(self.edit, name, value)
            seam.start()
            self.addCleanup(seam.stop)
        self.card = Mock(return_value=True)
        seam = patch.object(self.edit, "_dispatch_preview", self.card)
        seam.start()
        self.addCleanup(seam.stop)
        self.today(self.TODAY)

    def today(self, day):
        seam = patch.object(self.edit, "date", frozen(day), create=True)
        seam.start()
        self.addCleanup(seam.stop)

    def save(self, delivery):
        return self.edit.save("U1", "N1", {
            "customer": dict(CUSTOMER),
            "answers": {"place_id": "PL1", "car_id": "C1", "paint_id": "PT1", "delivery_date_be": delivery,
                        "term_id": "T1", "regis_id": "R1", "regis_name": CUSTOMER["name"]},
            "payments": [{"channel": "cash", "amount": "5000.00", "extra": {}}],
            "keep_files": {"id_card": True, "slip": True}})

    def refused(self, delivery):
        with self.assertRaises(self.edit.DmsBookingEditError) as raised:
            self.save(delivery)
        self.assertEqual((raised.exception.code, raised.exception.status), ("dms_booking.invalid_delivery", 400))
        self.store.replace_review_payload.assert_not_called()
        self.card.assert_not_called()
        self.assertEqual(self.payload, self.before, "the stored draft is untouched")

    def picker_range(self, day):
        """The min/max the LINE date picker offers on ``day`` (read from the real card)."""
        message = self.cards.ask_date(day)
        found = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "datetimepicker":
                    found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(message)
        self.assertEqual(len(found), 1)
        return date.fromisoformat(found[0]["min"]), date.fromisoformat(found[0]["max"])

    def test_out_of_range_dates_are_refused_before_the_draft_or_card_changes(self):
        # The reproduced input (a Gregorian year typed as Buddhist), yesterday, and one day past the picker.
        for delivery in ("01/01/2026", "15/11/2026", be(self.TODAY - timedelta(days=1)),
                         be(date(2028, 10, 14))):
            with self.subTest(delivery=delivery):
                self.refused(delivery)

    def test_today_and_exactly_two_years_ahead_save_and_send_the_new_card(self):
        for delivery in (be(self.TODAY), be(date(2028, 10, 13))):
            with self.subTest(delivery=delivery):
                self.store.replace_review_payload.reset_mock()
                self.card.reset_mock()
                nonce = self.save(delivery)
                stored = self.store.replace_review_payload.call_args.args
                self.assertEqual(stored[:2], ("U1", "N1"))
                self.assertEqual(stored[2]["qa"]["answers"]["delivery_date_be"], delivery)
                self.assertEqual(stored[2]["nonce"], nonce)
                self.card.assert_called_once()

    def test_editor_range_is_the_line_picker_range_including_leap_days(self):
        for day in (self.TODAY, date(2028, 2, 29), date(2027, 12, 31)):
            with self.subTest(today=day):
                self.today(day)
                low, high = self.picker_range(day)
                for edge in (low, high):
                    self.store.replace_review_payload.reset_mock()
                    self.save(be(edge))
                    self.store.replace_review_payload.assert_called_once()
                for outside in (low - timedelta(days=1), high + timedelta(days=1)):
                    self.store.replace_review_payload.reset_mock()
                    self.card.reset_mock()
                    self.refused(be(outside))

    def test_malformed_date_keeps_its_old_answer(self):
        self.refused("32/01/2569")


# ── 12B: menu 4 password save ───────────────────────────────────────────────────
class AccountPasswordTests(Isolated):
    def setUp(self):
        super().setUp()
        self.events = []
        self.operator = types.SimpleNamespace(name="OP-1", dms_username="sale02", dms_password="old-good")
        self.operator.save = Mock(side_effect=lambda **kw: self.events.append("save"))
        self.rows = {"DMS Operator": ("OP-1", "sale02")}
        endpoint = types.SimpleNamespace(name="EP-OP", owner_user="dmsop-1@operators.local",
                                         system_url="https://dms.example.invalid/dms/", username="sale02")

        def get_value(doctype, filters, fieldname=None, **kw):
            self.assertEqual(filters, {"user": "dmsop-1@operators.local", "status": "active"})
            row = self.rows.get(doctype)
            if fieldname == ["name", "dms_username"]:
                return row
            return row[0] if row else None

        def get_doc(doctype, name=None, **kw):
            if doctype == "Pearnly ERP Endpoint":
                return endpoint
            self.assertEqual((doctype, name, kw), ("DMS Operator", "OP-1", {"for_update": True}))
            return self.operator

        self.frappe.db = types.SimpleNamespace(
            get_value=Mock(side_effect=get_value),
            sql=Mock(side_effect=lambda query, values=None: self.events.append("lock")),
            commit=Mock(side_effect=lambda: self.events.append("commit")))
        self.frappe.get_doc = get_doc
        self.stub("werkzeug", __path__=[])
        self.stub("werkzeug.wrappers", Response=object)
        self.stub("pearnly_core.endpoints", read_password=Mock())
        self.stub("pearnly_dms.editor", __path__=[str(ROOT / "apps/pearnly_dms/pearnly_dms/editor")])
        self.stub("pearnly_dms.editor.browser_api", _fail=self.fail_with,
                  _line_user_from_request=Mock(), _bound_language=Mock())
        self.stub("pearnly_dms.editor.browser_ticket", resolve=lambda ticket: {
            "mode": "account", "user": "dmsop-1@operators.local", "line_user_id": "U1"})
        self.stub("pearnly_dms.ocr.ocr_service", resolve_dms_endpoint=lambda user, endpoint_id: {"name": "EP-OP"})
        self.stub("pearnly_dms.account.portal")
        self.probe = Mock(side_effect=self.sign_in)
        self.probe_result = {"ok": True, "missing": []}
        self.stub("pearnly_dms.adapter.account_probe", probe=self.probe)
        self.api = importlib.import_module("pearnly_dms.account.api")

    @staticmethod
    def fail_with(code, status=400):
        raise Failed(code, status)

    def sign_in(self, *args):
        self.events.append("sign-in")
        return self.probe_result

    def test_wrong_password_is_not_stored_and_names_the_reason(self):
        for code in ("ERR_DMS_AUTH", "ERR_DMS_CONCURRENT_LOGIN"):
            with self.subTest(code=code):
                self.probe_result = {"ok": False, "error_code": code, "error_friendly": {}}
                with self.assertRaises(Failed) as raised:
                    self.api.save_credentials("ticket", "typo")
                self.assertEqual((raised.exception.code, raised.exception.status), ("dms.err." + code, 400))
        self.operator.save.assert_not_called()
        self.assertEqual(self.operator.dms_password, "old-good")
        self.assertNotIn("lock", self.events, "a refused password never takes the row lock")
        self.assertNotIn("commit", self.events, "a refused password commits nothing")

    def test_unreachable_or_broken_dms_stores_nothing_and_never_looks_like_an_expired_link(self):
        for code in ("ERR_DMS_UNREACHABLE", "ERR_DMS_TECHNICAL", "ERR_UNEXPECTED", None):
            with self.subTest(code=code):
                self.probe_result = {"ok": False, "error_code": code}
                with self.assertRaises(Failed) as raised:
                    self.api.save_credentials("ticket", "maybe-right")
                self.assertEqual(raised.exception.code, "dms.err." + (code or "ERR_UNEXPECTED"))
                self.assertEqual(raised.exception.status, 503)
        self.operator.save.assert_not_called()
        self.assertEqual(self.operator.dms_password, "old-good")

    def test_right_password_signs_in_once_outside_the_lock_then_saves_as_before(self):
        self.assertEqual(self.api.save_credentials("ticket", "new-good"), {"ok": True})
        self.probe.assert_called_once_with("https://dms.example.invalid/dms/", "sale02", "new-good", "login")
        # The sign-in can take tens of seconds: the read-only repeatable-read view is ended before the
        # row lock, so the identity re-check under the lock sees an unbind or disable made meanwhile.
        self.assertEqual(self.events, ["sign-in", "commit", "lock", "save"])
        self.assertEqual(self.operator.dms_password, "new-good")
        self.assertIn("for update", self.frappe.db.sql.call_args.args[0])

    def test_old_client_with_the_same_username_still_saves(self):
        self.assertEqual(self.api.save_credentials("ticket", "new-good", username=" sale02 "), {"ok": True})
        self.assertEqual(self.events, ["sign-in", "commit", "lock", "save"])

    def test_rename_or_missing_operator_is_refused_without_signing_in(self):
        with self.assertRaises(Failed) as raised:
            self.api.save_credentials("ticket", "new-good", username="sale03")
        self.assertEqual((raised.exception.code, raised.exception.status), ("dms.account.failed", 409))
        self.rows = {}
        with self.assertRaises(Failed) as raised:
            self.api.save_credentials("ticket", "new-good")
        self.assertEqual((raised.exception.code, raised.exception.status), ("dms_booking.not_bound", 403))
        self.probe.assert_not_called()
        self.operator.save.assert_not_called()

    def test_invalid_password_is_refused_before_any_sign_in(self):
        for password in ("", "x" * 257, "a\nb"):
            with self.assertRaises(Failed) as raised:
                self.api.save_credentials("ticket", password)
            self.assertEqual(raised.exception.code, "dms.account.invalid")
        self.probe.assert_not_called()

    def test_account_page_shows_the_two_sign_in_refusals_with_existing_copy(self):
        page = (ROOT / "frontend/src/pages/dms/AccountPage.vue").read_text(encoding="utf-8")
        self.assertIn('"dms.err.ERR_DMS_AUTH", "dms.err.ERR_DMS_CONCURRENT_LOGIN"', page)
        self.assertEqual(set(self.api.SIGN_IN_REFUSED), {"ERR_DMS_AUTH", "ERR_DMS_CONCURRENT_LOGIN"})
        for lang in ("th", "en", "zh", "ja"):
            pack = (ROOT / f"apps/pearnly_dms/pearnly_dms/translations/{lang}.csv").read_text(encoding="utf-8")
            for code in self.api.SIGN_IN_REFUSED:
                self.assertIn(f"\ndms.err.{code},", pack, lang)


# ── #14 / #18: roster ───────────────────────────────────────────────────────────
def dms_binding_handlers():
    """The ``pearnly_line_binding_changed`` handlers the DMS app really registers."""
    tree = ast.parse((ROOT / "apps/pearnly_dms/pearnly_dms/hooks.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") == "pearnly_line_binding_changed" for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("DMS registers no binding-changed handlers")


class RosterTests(Isolated):
    def setUp(self):
        super().setUp()
        self.events = []
        self.inserted = []

        class Doc(types.SimpleNamespace):
            flags = types.SimpleNamespace()

            def insert(doc, **kw):
                self.inserted.append(doc.doctype)
                doc.name = doc.__dict__.get("name") or doc.doctype + "-NEW"
                if doc.doctype == "DMS Operator":
                    self.operators[doc.name] = doc
                return doc

            def save(doc, **kw):
                self.events.append(("save", doc.name, doc.status))

        self.Doc = Doc
        self.operators = {
            "OP-1": Doc(doctype="DMS Operator", name="OP-1", user="u1", dms_username="sale02", status="active",
                        display_name="A"),
            "OP-2": Doc(doctype="DMS Operator", name="OP-2", user="u2", dms_username="Sale05", status="inactive",
                        display_name="B"),
        }
        self.bindings = {"U-phone-1": "u1"}

        def get_all(doctype, filters=None, fields=None, pluck=None, **kw):
            if doctype == "Pearnly ERP Endpoint":
                return [{"username": "dmstest001", "admin_username": "dmstest001"}]
            if doctype == "DMS System Account":
                return ["dmstest002"]
            if doctype == "DMS Operator":
                skip = (filters or {}).get("name", [None, None])[1]
                return [op.dms_username for op in self.operators.values() if op.name != skip]
            if doctype == "Pearnly LINE Binding":
                self.assertEqual(pluck, "line_user_id")
                return [line for line, user in self.bindings.items() if user == filters["user"]]
            return []

        def get_value(doctype, filters, fieldname=None, **kw):
            if doctype == "Pearnly LINE Binding" and fieldname == "name":
                return "B-" + filters["line_user_id"]
            if doctype == "Pearnly ERP Endpoint":
                return "https://dms.example.invalid/dms/"
            return None

        self.frappe.session = types.SimpleNamespace(user="admin@x")
        self.frappe.get_roles = lambda user: ["DMS Admin"]
        self.frappe.get_all = get_all
        self.frappe.get_doc = lambda arg, name=None, **kw: Doc(**arg) if isinstance(arg, dict) else self.operators[name]
        self.frappe.clear_cache = lambda **kw: None
        self.frappe.db = types.SimpleNamespace(
            exists=lambda *a, **k: True, get_value=get_value,
            delete=Mock(side_effect=lambda doctype, filters: self.events.append(("delete", doctype, filters))),
            set_value=Mock(side_effect=lambda doctype, name, values: self.events.append(("set", doctype, name,
                                                                                        values["status"]))),
            commit=Mock(side_effect=lambda: self.events.append(("commit",))))
        self.handlers = {}
        self.frappe.get_hooks = lambda name: dms_binding_handlers() if name == "pearnly_line_binding_changed" else []
        self.frappe.get_attr = lambda path: self.handlers.setdefault(path, Mock(
            side_effect=lambda line_user_id, path=path: self.events.append(("notify", path, line_user_id))))
        self.stub("pearnly_core.execution", __path__=[])
        self.stub("pearnly_core.execution.state", store=Mock())
        self.stub("pearnly_core.endpoints", read_password=lambda *a: "")
        self.stub("pearnly_dms.operators.advisors", resolve=lambda value: {"id": "", "name": ""})
        self.stub("pearnly_dms.operators.line_account")
        self.probe = Mock(return_value={"ok": True, "missing": []})
        self.stub("pearnly_dms.adapter.account_probe", probe=self.probe)
        self.stub("pearnly_dms.i18n", t=lambda key, **kw: key)
        self.roster = importlib.import_module("pearnly_dms.operators.roster")
        self.roster._row = lambda doc: {"name": doc.name, "status": doc.status}

    # #14
    def test_unbind_goes_through_the_shared_entry_so_menu_and_drafts_follow(self):
        handlers = dms_binding_handlers()
        self.assertIn("pearnly_dms.menu.rich_menu.request_sync", handlers)
        self.assertIn("pearnly_dms.line.identity.binding_changed", handlers)
        self.bindings["U-phone-2"] = "u1"
        self.bindings["U-other"] = "u2"
        self.roster.api_unbind_operator("OP-1")
        unbound = [event for event in self.events if event[0] == "set"]
        self.assertEqual(unbound, [("set", "Pearnly LINE Binding", "B-U-phone-1", "unbound"),
                                   ("set", "Pearnly LINE Binding", "B-U-phone-2", "unbound")])
        for line in ("U-phone-1", "U-phone-2"):
            for path in handlers:
                self.handlers[path].assert_any_call(line)
        notified = {event[2] for event in self.events if event[0] == "notify"}
        self.assertEqual(notified, {"U-phone-1", "U-phone-2"}, "another operator's LINE is left alone")
        self.assertEqual(self.events[0], ("delete", "Pearnly LINE Bind Code", {"user": "u1", "used": 0}))
        self.assertEqual(self.events[-1], ("commit",))
        self.assertFalse([event for event in self.events if event[0] == "save"], "roster row and DMS login kept")

    def test_unbind_without_a_line_still_drops_open_bind_codes(self):
        self.roster.api_unbind_operator("OP-2")
        self.assertEqual(self.events, [("delete", "Pearnly LINE Bind Code", {"user": "u2", "used": 0}),
                                       ("commit",)])

    # #18
    def test_new_operator_cannot_share_another_operators_dms_username(self):
        for username in ("sale02", "SALE02", " Sale02 ", "sale05", "DMSTEST001", "dmstest002"):
            with self.subTest(username=username):
                with self.assertRaises(Thrown) as raised:
                    self.roster.create_operator("New", username, "pw")
                self.assertEqual(str(raised.exception), TAKEN)
        self.probe.assert_not_called()
        self.assertEqual(self.inserted, [])

    def test_unused_username_is_created_after_one_sign_in(self):
        self.roster.create_operator("New", "sale07", "pw")
        self.probe.assert_called_once_with("https://dms.example.invalid/dms/", "sale07", "pw", "login")
        self.assertEqual(self.inserted, ["User", "DMS Operator"])

    def test_re_enable_is_refused_when_someone_else_holds_the_username(self):
        self.operators["OP-3"] = self.Doc(doctype="DMS Operator", name="OP-3", user="u3", dms_username="sale05",
                                          status="active", display_name="C")
        with self.assertRaises(Thrown) as raised:
            self.roster.set_status("OP-2", "active")
        self.assertEqual(str(raised.exception), TAKEN)
        self.assertEqual(self.operators["OP-2"].status, "inactive")
        self.assertEqual(self.events, [], "nothing saved, no commit")

    def test_re_enable_compares_with_others_not_with_itself(self):
        self.assertEqual(self.roster.set_status("OP-2", "active")["status"], "active")
        self.assertIn(("save", "OP-2", "active"), self.events)

    def test_disable_and_repeat_enable_are_not_blocked_by_old_shared_rows(self):
        # Rows shared before this rule (e.g. two active operators on one username) can still be disabled.
        self.operators["OP-3"] = self.Doc(doctype="DMS Operator", name="OP-3", user="u3", dms_username="SALE02",
                                          status="active", display_name="C")
        self.bindings = {}
        self.assertEqual(self.roster.set_status("OP-3", "inactive")["status"], "inactive")
        self.operators["OP-3"].status = "active"
        self.assertEqual(self.roster.set_status("OP-3", "active")["status"], "active")


if __name__ == "__main__":
    unittest.main()
