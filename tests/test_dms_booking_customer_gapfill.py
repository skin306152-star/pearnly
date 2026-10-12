"""Zero-I/O regressions: an old customer missing a booking field no longer fails at the very end.

Old bug (reproduced 2026-10-12 on the test machine: customer 128 with an empty birthday):
the dedup card said "same data", the operator answered every booking question, and only the
final confirmation failed with ``ERR_DMS_CUSTOMER_LOOKUP`` ("check customer data and account
access") plus a Retry that re-read the same incomplete DMS record and failed again.

Now (G4): the required-field rule is unchanged but reported as its own code; an operator who
may update customers gets the empty fields filled once from this draft's own values, then the
booking goes ahead; values the system does not have go back to the preview card with the
missing fields listed; without update permission the operator gets the list and a Retry that
works once an admin has filled DMS.

The booking service runs for real over the real DMS client, the real dispatch ledger marker
(``intake.customer_attempt``) and a fake DMS form transport; the LINE routing runs the real
``booking_flow`` over small stand-ins. Nothing logs in, posts to DMS, sends LINE or touches a Site.
"""
from __future__ import annotations

from contextlib import nullcontext
import dataclasses
import importlib
from html import escape
from pathlib import Path
import runpy
import socket
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

identity = runpy.run_path(str(ROOT / "scripts/test-dms-customer-identity.py"))
reconcile = runpy.run_path(str(ROOT / "tests/test_dms_customer_reconcile.py"))
DmsClient, Transport, PID, NAME = (identity[key] for key in ("DmsClient", "Transport", "PID", "NAME"))
Ledger, EP, USER = (reconcile[key] for key in ("Ledger", "EP", "USER"))
from pearnly_dms.booking import booking_customer  # noqa: E402
from pearnly_dms.contracts.client_base import DmsClientError  # noqa: E402

BASE = "https://identity.invalid/dms/"
OPTIONS = {"selprefix": [("17", "นาย"), ("18", "นาง")],
           "selprovinces": [("P", "จังหวัด"), ("P2", "จังหวัดอื่น")],
           "seldistricts": [("D", "อำเภอ")], "selsubdistricts": [("S", "ตำบล")],
           "selzipcodes": [("Z", "10110")]}
TEXT = ("txthousenum", "txtbuilding", "txtfloor", "txtroom", "txtvillage", "txtmoo", "txtsoi", "txtroad")
DRAFT = {"people_id": PID, "name": NAME, "birthday_be": "15/06/2530", "prefix_id": "17",
         "prefix_name": "นาย", "phone": "0812345678", "house_no": "99", "moo": "3", "soi": "", "road": "",
         "province_id": "P", "district_id": "D", "subdistrict_id": "S", "zipcode_id": "Z", "zipcode": "10110"}


def complete(**extra):
    """A complete native customer form: every booking field present."""
    data = dict(txtcuscode="C001", txtpeopleid=PID, txttaxid=PID, txtcusname=NAME,
                txtbirthday="01/01/2530", txttel="0800000001", selprefix="17")
    for suffix in ("", "_ct", "_sd"):
        data.update({"selprovinces" + suffix: "P", "seldistricts" + suffix: "D",
                     "selsubdistricts" + suffix: "S", "selzipcodes" + suffix: "Z"})
        data.update({field + suffix: "" for field in TEXT})
        data["txthousenum" + suffix] = "12"
    data.update(extra)
    return data


def render(data):
    """Native form: selects carry their real option lists; an id DMS cannot name has no label."""
    parts = ["<form>"]
    for key, value in data.items():
        if key.startswith("sel"):
            options = [("", "")] + OPTIONS[key.split("_")[0]]
            if value and value not in dict(options):
                options.append((value, ""))
            parts.append(f'<select name="{key}">' + "".join(
                f'<option value="{escape(v)}"{" selected" if v == value else ""}>{escape(label)}</option>'
                for v, label in options) + "</select>")
        else:
            parts.append(f'<input name="{key}" value="{escape(value, quote=True)}">')
    return "".join(parts) + "</form>"


class Dms(Transport):
    on_write = None

    def post(self, url, *, data, **kwargs):
        path = url.removeprefix(BASE)
        if path in ("cus/new.php", "cus/edit.php") and self.on_write:
            self.on_write(path)
        if path == "cus/form.php" and data.get("status") == "e" and data["id"] not in self.form_overrides:
            self.calls.append((path, dict(data)))
            return SimpleNamespace(status_code=200, text=render(self.records[data["id"]]))
        return super().post(url, data=data, **kwargs)

    def reads(self):
        return sum(path == "cus/form.php" for path, _ in self.calls)


def fields_of(**changes):
    """``read_customer`` output for a complete customer, with ``changes`` applied."""
    return {**DmsClient(Dms({"own": complete()}), BASE).read_customer("own"), **changes}


# ── the required rule and the fill rule (pure) ──────────────────────────────────
def old_rule_incomplete(fields):
    """The pre-G4 check in ``booking_customer._card_from_fields``, verbatim in substance."""
    required = (fields.get("name"), fields.get("people_id"), fields.get("birthday_be"),
                fields.get("prefix_id"), fields.get("prefix_name"), fields.get("phone"),
                *(str(fields.get(key) or "") for key in (
                    "house_no", "province_id", "province_name", "district_id", "district_name",
                    "subdistrict_id", "subdistrict_name", "zipcode_id", "zipcode_name")))
    return not all(str(value or "").strip() for value in required)


class RequiredRule(unittest.TestCase):
    def test_required_rule_is_unchanged_for_every_combination(self):
        base = fields_of()
        keys = booking_customer.REQUIRED
        self.assertEqual(len(keys), 15)
        for mask in range(1 << len(keys)):
            fields = dict(base)
            for index, key in enumerate(keys):
                if mask >> index & 1:
                    fields[key] = ("", "  ", None)[index % 3]
            self.assertEqual(bool(booking_customer.missing_required(fields)), old_rule_incomplete(fields), mask)

    def card(self, fields, people_id=PID):
        client = Mock()
        client.read_customer.return_value = fields
        with patch.object(M.admin_read, "authoritative_read_client", side_effect=lambda c: c):
            return booking_customer.card_from_customer(client, customer_id="own", people_id=people_id)

    def test_incomplete_has_its_own_code_with_the_fields_read_and_the_card_labels(self):
        fields = fields_of(birthday_be="", prefix_name="", zipcode_name=" ")
        with self.assertRaises(booking_customer.CustomerIncomplete) as caught:
            self.card(fields)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_INCOMPLETE")
        self.assertEqual(caught.exception.missing, ["birthday_be", "prefix_id", "zipcode_id"])
        self.assertEqual(caught.exception.fields, fields)
        self.assertNotIn(PID, str(caught.exception))
        self.assertEqual(self.card(fields_of()).birthday_be, "01/01/2530")

    def test_identity_problems_keep_the_lookup_code(self):
        for fields in (fields_of(people_id="1000001205453", birthday_be=""), fields_of(people_id="")):
            with self.subTest(fields=fields["people_id"]), self.assertRaises(DmsClientError) as caught:
                self.card(fields)
            self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_LOOKUP")

    def test_incomplete_is_judged_on_the_final_authoritative_read(self):
        sales, admin = Mock(), Mock()
        sales.read_customer.return_value = fields_of(phone="")  # the sales account sees less
        admin.read_customer.return_value = fields_of()
        with patch.object(M.admin_read, "authoritative_read_client", return_value=admin):
            card = booking_customer.card_from_customer(sales, customer_id="own", people_id=PID)
        self.assertEqual(card.phone, "0800000001")
        admin.read_customer.return_value = fields_of(birthday_be="")
        with patch.object(M.admin_read, "authoritative_read_client", return_value=admin), \
             self.assertRaises(booking_customer.CustomerIncomplete) as caught:
            booking_customer.card_from_customer(sales, customer_id="own", people_id=PID)
        self.assertEqual(caught.exception.missing, ["birthday_be"])
        self.assertEqual(caught.exception.fields["phone"], "0800000001")  # the admin's read is the baseline


class FillRule(unittest.TestCase):
    fill = staticmethod(booking_customer.gap_fill)

    def test_complete_needs_nothing(self):
        self.assertEqual(self.fill(fields_of(), DRAFT), {})

    def test_only_the_empty_fields_are_filled_from_the_draft(self):
        current = fields_of(birthday_be="", prefix_id="", prefix_name="", phone="")
        self.assertEqual(self.fill(current, DRAFT),
                         {"birthday_be": "15/06/2530", "prefix_id": "17", "phone": "0812345678"})
        # a value DMS already has is never part of the write, whatever the draft says
        self.assertEqual(self.fill(fields_of(birthday_be=""), {**DRAFT, "phone": "0899999999"}),
                         {"birthday_be": "15/06/2530"})

    def test_birthday_must_be_a_valid_buddhist_date(self):
        for value in ("", "2530-06-15", "31/02/2530", "15/6/2530"):
            with self.subTest(value=value):
                self.assertIsNone(self.fill(fields_of(birthday_be=""), {**DRAFT, "birthday_be": value}))

    def test_title_fills_the_id_only_and_never_replaces_an_id_dms_cannot_name(self):
        self.assertEqual(self.fill(fields_of(prefix_id="", prefix_name=""), DRAFT), {"prefix_id": "17"})
        self.assertIsNone(self.fill(fields_of(prefix_id="", prefix_name=""), {**DRAFT, "prefix_id": ""}))
        self.assertIsNone(self.fill(fields_of(prefix_id="99", prefix_name=""), DRAFT))

    def test_placeholders_from_the_card_payload_are_never_used(self):
        draft = {key: value for key, value in DRAFT.items() if key not in ("phone", "prefix_id")}
        self.assertIsNone(self.fill(fields_of(phone=""), draft))
        self.assertIsNone(self.fill(fields_of(prefix_id="", prefix_name=""), draft))

    def empty_address(self, **extra):
        blank = {key: "" for key in ("house_no", "building", "floor", "room", "village", "moo", "soi", "road",
                                     "province_id", "province_name", "district_id", "district_name",
                                     "subdistrict_id", "subdistrict_name", "zipcode_id", "zipcode_name")}
        return fields_of(**{**blank, **extra})

    def test_a_wholly_empty_id_card_address_takes_the_whole_draft_address(self):
        self.assertEqual(self.fill(self.empty_address(), DRAFT),
                         {"house_no": "99", "moo": "3", "province_id": "P", "district_id": "D",
                          "subdistrict_id": "S", "zipcode_id": "Z"})
        self.assertIsNone(self.fill(self.empty_address(), {**DRAFT, "zipcode_id": ""}))
        self.assertIsNone(self.fill(self.empty_address(), {**DRAFT, "house_no": ""}))

    def test_same_address_missing_only_the_house_number_gets_only_the_house_number(self):
        self.assertEqual(self.fill(fields_of(house_no=""), DRAFT), {"house_no": "99"})

    def test_mixed_addresses_are_never_stitched_together(self):
        self.assertIsNone(self.fill(fields_of(house_no=""), {**DRAFT, "province_id": "P2"}))
        self.assertIsNone(self.fill(self.empty_address(road="ถนนเดิม"), DRAFT))  # partly used block
        self.assertIsNone(self.fill(fields_of(house_no="", district_name=""), DRAFT))

    def test_nothing_is_written_when_any_one_field_cannot_be_filled(self):
        self.assertIsNone(self.fill(fields_of(birthday_be="", house_no=""), {**DRAFT, "house_no": ""}))
        self.assertIsNone(self.fill(fields_of(birthday_be="", name=""), {**DRAFT, "name": ""}))


# ── the booking service, end to end over a fake DMS ─────────────────────────────
@dataclasses.dataclass(frozen=True)
class Booking:
    delivery_date_be: str = "22/10/2569"
    regis_name: str = ""
    payments: tuple = ()


class Recorder:
    """The order-attempt recorder the LINE flow passes as ``on_attempt``."""

    def __init__(self, timeline):
        self.timeline = timeline

    def __call__(self, info):
        self.timeline.append("order-registered")

    def assert_current(self):
        self.timeline.append("guard")


#: The code under test is imported once over these stand-ins (``setUpModule``), so every
#: patch lands on the very module objects the booking service uses.
M = SimpleNamespace()
_MODULES = None


def setUpModule():
    global _MODULES
    stubs = {}
    for name, attrs in (
            ("frappe", {}),
            ("pearnly_core", {"__path__": []}),
            ("pearnly_core.session_lock", {"account_lock": None, "dms_booking_key": None}),
            ("pearnly_dms.snapshots.store", {"SnapshotUnavailable": type("SnapshotUnavailable", (Exception,), {})}),
            ("pearnly_dms.snapshots.directory", {"Directory": lambda endpoint: SimpleNamespace(endpoint=endpoint)}),
            ("pearnly_dms.adapter.snapshot_writeback", {"capture_scope": None, "customer": None}),
            ("pearnly_dms.operators.roster", {"get_profile": None})):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        stubs[name] = module
    _MODULES = patch.dict(sys.modules, stubs)
    _MODULES.start()
    for key, name in (("frappe", "frappe"), ("roster", "pearnly_dms.operators.roster"),
                      ("writeback", "pearnly_dms.adapter.snapshot_writeback"),
                      ("attempt", "pearnly_dms.intake.customer_attempt"),
                      ("service", "pearnly_dms.booking.service"), ("draft", "pearnly_dms.intake.draft"),
                      ("operations", "pearnly_dms.adapter.operations"), ("errors", "pearnly_dms.line.errors"),
                      ("push", "pearnly_dms.adapter.push"), ("preflight", "pearnly_dms.booking.booking_preflight"),
                      ("org", "pearnly_dms.booking.booking_org"), ("payload", "pearnly_dms.booking.booking_payload"),
                      ("banks", "pearnly_dms.masters.company_banks"), ("intake", "pearnly_dms.client.client_intake"),
                      ("admin_read", "pearnly_dms.adapter.admin_read"), ("models", "pearnly_dms.contracts.models")):
        setattr(M, key, importlib.import_module(name))


def tearDownModule():
    _MODULES.stop()


class BookingService(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.has_admin, self.role = False, "admin"
        self.cached = []
        lock = SimpleNamespace(lock=lambda *a, **kw: nullcontext(), make_key=lambda key: key)
        friendly = lambda code, lang=None: {"en": code}  # noqa: E731  (copy lives in the CSV tables)
        for module, name, value in (
                (M.frappe, "db", self.ledger), (M.frappe, "get_all", self.ledger.get_all),
                (M.frappe, "new_doc", self.ledger.new_doc),
                (M.frappe, "utils", SimpleNamespace(get_datetime=self.ledger.get_datetime)),
                (M.roster, "get_profile", lambda user: {"dms_role": self.role}),
                (M.writeback, "capture_scope", lambda endpoint: "scope"),
                (M.writeback, "customer",
                 lambda endpoint, client, cid, expected_scope: self.cached.append(cid)),
                (M.operations, "run_logged_in", self.session),
                (M.operations, "friendly", friendly), (M.errors, "friendly", friendly),
                (M.push, "booking_defaults_from_endpoint", lambda endpoint: M.models.BookingDefaults()),
                (M.preflight, "local_preflight", self.preflight),
                (M.org, "resolve_booking_org", lambda *a: "org"),
                (M.payload, "build_booking_payload", lambda *a, **kw: Booking()),
                (M.banks, "validate_company_bank_payments", lambda adapter, payments, **kw: payments),
                (M.intake.time, "sleep", lambda seconds: None),
                (M.attempt, "_state_store", lambda: lock),
                (M.attempt, "_snapshot_customer", lambda endpoint, pid: (False, None)),
                (M.service, "booking_lock", lambda endpoint: nullcontext(True)),
                (M.draft, "has_admin_creds", lambda endpoint: self.has_admin)):
            patcher = patch.object(module, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.attempt, self.service = M.attempt, M.service
        self.timeline = []
        self.dms = Dms({"own": complete()})
        self.dms.on_write = self.timeline.append
        self.client = DmsClient(self.dms, BASE)
        self.client.create_booking_via_form = self.create_booking

    # ── stand-ins ───────────────────────────────────────────────────────────
    def session(self, endpoint, fn, **kwargs):
        """``run_logged_in`` contract: one session, never raises (errors become ``err``)."""
        from pearnly_dms.adapter.operations import err
        self.assertEqual((endpoint, kwargs), (EP, {"catch_up": True}))
        try:
            return fn(self.client, None)
        except DmsClientError as exc:
            return err(exc.error_code, str(exc), getattr(exc, "response_body", None))

    def preflight(self, qa, directory):
        return {"status": "ok", "qa": dict(qa), "masters": {}, "paints": []}

    def create_booking(self, *, customer_id, booking, card, on_attempt):
        self.timeline.append(("booking", card.birthday_be, card.prefix_id, card.address.house_no))
        on_attempt({"booking_no": "BK-1"})
        return "booking-1", "BK-1"

    def book(self, nonce="n-1", **draft):
        payload = {"nonce": nonce, "qa": {
            "endpoint_id": EP, "user_id": USER, "customer": {"id": "own", "name": NAME},
            "draft": {**DRAFT, **draft}, "advisor": {"id": "a", "name": "A"},
            "answers": {}, "payments": []}}
        return self.service.book_in_session(EP, payload, on_attempt=Recorder(self.timeline))

    def writes(self):
        return [(path, data) for path, data in self.dms.writes()]

    def fill_rows(self, nonce="n-1"):
        marker = self.attempt.Attempt(EP, "booking-customer-fill:" + nonce, USER, fields={"people_id": PID},
                                      mode="overwrite", customer_id="own", addresses=None, baseline=None)
        return marker.name, self.ledger.rows.get(marker.name)

    # ── normal path: identical ──────────────────────────────────────────────
    def test_complete_customer_is_read_once_and_never_written(self):
        result = self.book()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.dms.reads(), 1)
        self.assertEqual(self.ledger.rows, {})
        self.assertEqual(self.timeline, ["guard", ("booking", "01/01/2530", "17", "12"), "order-registered"])

    # ── the reproduced bug ──────────────────────────────────────────────────
    def test_empty_birthday_is_filled_once_from_the_card_then_the_booking_goes_ahead(self):
        self.dms.records["own"]["txtbirthday"] = ""
        result = self.book()
        self.assertTrue(result["ok"], result)  # before G4: ERR_DMS_CUSTOMER_LOOKUP, and Retry failed again
        self.assertEqual(self.timeline, ["guard", "guard", "cus/edit.php",
                                         ("booking", "15/06/2530", "17", "12"), "order-registered"])
        (_, sent), = self.writes()
        self.assertEqual(sent["txtbirthday"], "15/06/2530")
        self.assertEqual(sent["txttel"], "0800000001")  # what DMS had stays as it was
        self.assertEqual(self.dms.records["own"]["txtbirthday"], "15/06/2530")
        name, row = self.fill_rows()
        self.assertEqual(row["status"], "success")
        self.assertEqual(list(self.ledger.rows), [name])  # its own request id, not the edit page's
        self.assertNotEqual(name, self.attempt.Attempt(
            EP, "booking-customer:n-1", USER, fields={"people_id": PID}, mode="overwrite",
            customer_id="own", addresses=None, baseline=None).name)
        self.assertEqual(self.cached, ["own"])  # same local write-back as an edited customer

    def test_title_and_house_number_are_filled_together_in_one_write(self):
        self.dms.records["own"].update(selprefix="", txthousenum="")
        self.assertTrue(self.book()["ok"])
        (_, sent), = self.writes()
        self.assertEqual((sent["selprefix"], sent["txthousenum"]), ("17", "99"))
        self.assertEqual(self.timeline[-2], ("booking", "01/01/2530", "17", "99"))

    def test_the_writer_session_and_its_read_are_the_baseline(self):
        sales = Dms({"own": complete(txttel="")})  # the sales account sees no phone
        self.dms.records["own"]["txtbirthday"] = ""
        self.client = DmsClient(sales, BASE, admin_transport=self.dms)
        self.client.create_booking_via_form = self.create_booking
        self.has_admin = True
        self.role = "sales"
        self.assertTrue(self.book()["ok"])
        self.assertEqual([path for path, _ in sales.writes()], [])
        (_, sent), = self.writes()  # written in the admin writer session
        self.assertEqual((sent["txtbirthday"], sent["txttel"]), ("15/06/2530", "0800000001"))

    # ── the system cannot fill: back to the preview card ────────────────────
    def test_value_the_draft_does_not_have_is_not_written_and_not_booked(self):
        self.dms.records["own"]["txtbirthday"] = ""
        result = self.book(birthday_be="")  # "use old data": the draft is the DMS record
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_INCOMPLETE")
        self.assertEqual(result["response_body"]["missing"], ["birthday_be"])
        self.assertFalse(result["response_body"]["submitted"])
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.ledger.rows, {})
        self.assertNotIn("order-registered", self.timeline)

    def test_form_check_refusing_the_value_before_sending_goes_back_to_the_card(self):
        self.dms.records["own"]["selprefix"] = ""
        result = self.book(prefix_id="99")  # not a title in this DMS
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_INCOMPLETE")
        self.assertEqual(result["response_body"]["missing"], ["prefix_id"])
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.ledger.rows, {})

    def test_still_missing_after_the_write_is_not_written_twice(self):
        empty = {key: "" for key in ("selprovinces", "seldistricts", "selsubdistricts", "selzipcodes") + TEXT}
        self.dms.records["own"].update(empty)  # the whole ID-card address is empty in DMS
        OPTIONS["selzipcodes"] = [("Z", "")]  # ...and DMS shows no name for the zip code once set
        self.addCleanup(OPTIONS.__setitem__, "selzipcodes", [("Z", "10110")])
        result = self.book()
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_INCOMPLETE")
        self.assertEqual(result["response_body"]["missing"], ["zipcode_id"])
        (_, sent), = self.writes()  # the whole draft address, once
        self.assertEqual((sent["txthousenum"], sent["selprovinces"], sent["selzipcodes"]), ("99", "P", "Z"))
        self.assertNotIn("order-registered", self.timeline)
        again = self.book("n-2")  # a new confirmation: nothing more the system can fill
        self.assertEqual(again["error_code"], "ERR_DMS_CUSTOMER_INCOMPLETE")
        self.assertEqual(len(self.writes()), 1)

    # ── no permission to update customers ───────────────────────────────────
    def test_without_update_permission_nothing_is_written_and_retry_rereads_live(self):
        self.role = "sales"
        self.dms.records["own"]["txtbirthday"] = ""
        result = self.book()
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN")
        self.assertEqual(result["response_body"]["missing"], ["birthday_be"])
        self.assertEqual(self.writes(), [])
        self.assertNotIn("order-registered", self.timeline)
        self.dms.records["own"]["txtbirthday"] = "02/02/2531"  # the admin fixed it in DMS
        retry = self.book("n-2")
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.timeline[-2], ("booking", "02/02/2531", "17", "12"))

    # ── protections kept ────────────────────────────────────────────────────
    def test_unknown_write_outcome_never_books_and_is_not_resent(self):
        self.dms.records["own"]["txtbirthday"] = ""
        post = self.dms.post
        def lost(url, **kwargs):
            if url.endswith("cus/edit.php"):
                self.dms.calls.append(("cus/edit.php", dict(kwargs["data"])))
                self.timeline.append("cus/edit.php")
                raise TimeoutError("never reached DMS")
            return post(url, **kwargs)
        self.dms.post = lost
        result = self.book()
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertNotIn("order-registered", self.timeline)
        self.assertEqual(self.fill_rows()[1]["status"], "manual")
        self.dms.post = post
        again = self.book()  # a redelivery of the same job: checked read-only, never re-sent
        self.assertEqual(again["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(len(self.writes()), 1)
        self.assertNotIn("order-registered", self.timeline)

    def test_someone_filling_dms_between_read_and_write_gets_the_conflict_review(self):
        reads = []
        def changed_meanwhile():
            reads.append(1)
            if len(reads) > 1:
                self.dms.records["own"]["txtbirthday"] = "02/02/2531"
            return render(self.dms.records["own"])
        self.dms.records["own"]["txtbirthday"] = ""
        self.dms.form_overrides["own"] = changed_meanwhile
        result = self.book()
        conflict = result["response_body"]["customer_conflict"]
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_CHANGED")
        self.assertEqual([c["field"] for c in conflict["conflicts"]], ["birthday_be"])
        self.assertEqual(self.writes(), [])
        self.assertNotIn("order-registered", self.timeline)

    def test_the_order_guard_runs_before_the_customer_fill_is_registered(self):
        self.dms.records["own"]["txtbirthday"] = ""
        self.book()
        index = self.timeline.index("cus/edit.php")
        self.assertEqual(self.timeline[index - 1], "guard")


# ── LINE routing of the new results ─────────────────────────────────────────────
class Routing(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.armed, self.sent, self.pushed = [], [], []
        self.result = {}
        session = {"state": "booking_review", "payload": {"nonce": None, "booking_claim": "n-1"}}

        def replace(line_user_id, claimed, state, payload, ttl_minutes=None):
            self.armed.append((claimed, state, payload["nonce"], ttl_minutes))
            return True

        def stub(name, **attrs):
            module = ModuleType(name)
            module.__dict__.update(attrs)
            sys.modules[name] = module
            return module

        stub("frappe")
        stub("pearnly_core", __path__=[])
        stub("pearnly_core.line_session", get_session=lambda uid: session,
             replace_claimed_booking_payload=replace)
        stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key, line_lang=lambda: "th",
             all_languages=lambda key: {"th": key})
        stub("pearnly_dms.booking.booking_attempt", UNKNOWN="ERR_DMS_BOOKING_UNKNOWN",
             pending=lambda *a: None, recorder=lambda *a, **kw: None)
        stub("pearnly_dms.booking.booking_ledger", log_booking=lambda **kw: None)
        stub("pearnly_dms.booking.recovery", endpoint_fingerprint=lambda endpoint: "fp",
             unverified_booking_no=lambda endpoint, qa: "")
        stub("pearnly_dms.booking.attachments", download=lambda qa: ([], []))
        stub("pearnly_dms.line.out", push_text=lambda uid, text: self.pushed.append(text),
             reply_text=Mock(), enqueue=Mock(), send=lambda uid, msg, token=None: self.sent.append(msg),
             start_loading=lambda uid: None)
        stub("pearnly_dms.booking.service", book_in_session=lambda *a, **kw: self.result)
        stub("pearnly_dms.ocr.ocr_service", resolve_dms_endpoint=lambda user, ep: {"name": "EP"})
        stub("pearnly_dms.booking.conversation.qa_cards",
             preview_card=lambda qa, nonce, notice="": {"card": nonce, "notice": notice},
             booking_retry_message=lambda text, nonce, code="": {"retry": nonce, "text": text, "code": code})
        self.flow = importlib.import_module("pearnly_dms.booking.conversation.booking_flow")

    def run_result(self, code, missing):
        self.result = {"ok": False, "error_code": code,
                       "error_friendly": {"th": "dms.err." + code},
                       "response_body": {"submitted": False, "missing": missing}}
        self.flow._execute_owned_booking({"tenant_id": "t", "user_id": USER}, "U1",
                                         {"nonce": "n-1", "qa": {"customer": {"id": "own"}}})

    def test_system_cannot_fill_returns_the_preview_card_with_the_missing_fields(self):
        self.run_result("ERR_DMS_CUSTOMER_INCOMPLETE", ["birthday_be", "prefix_id"])
        (claimed, state, nonce, ttl), = self.armed
        self.assertEqual((claimed, state, ttl), ("n-1", "booking_review", 30))
        self.assertEqual(self.sent, [{"card": nonce, "notice": "\n".join([
            "dms.err.ERR_DMS_CUSTOMER_INCOMPLETE", "dms.card.diff.birthday_be", "dms.card.diff.prefix_id"])}])
        self.assertEqual(self.pushed, [])  # one message, not a text plus a card

    def test_no_update_permission_gets_the_list_and_a_retry_that_rereads(self):
        self.run_result("ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN", ["house_no"])
        (claimed, state, nonce, ttl), = self.armed
        self.assertEqual(self.sent, [{"retry": nonce, "code": "ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN",
                                      "text": "dms.err.ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN\ndms.card.diff.house_no"}])
        self.assertEqual(self.pushed, [])

    def test_retry_rules(self):
        retryable = self.flow._retryable_result
        self.assertTrue(retryable({"error_code": "ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN"}))
        self.assertTrue(retryable({"error_code": "ERR_DMS_CUSTOMER_LOOKUP"}))
        self.assertFalse(retryable({"error_code": "ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN", "booking_id": "R1"}))

    def test_other_retry_messages_are_unchanged(self):
        self.result = {"ok": False, "error_code": "ERR_DMS_UNREACHABLE",
                       "error_friendly": {"th": "unreachable"}, "response_body": {}}
        self.flow._execute_owned_booking({"tenant_id": "t", "user_id": USER}, "U1",
                                         {"nonce": "n-1", "qa": {}})
        self.assertEqual(self.sent[0]["text"], "unreachable")


class Copy(unittest.TestCase):
    def test_both_new_messages_exist_in_all_four_languages(self):
        import csv
        for lang in ("th", "en", "zh", "ja"):
            path = ROOT / f"apps/pearnly_dms/pearnly_dms/translations/{lang}.csv"
            with path.open(encoding="utf-8", newline="") as handle:
                table = {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2}
            for code in ("ERR_DMS_CUSTOMER_INCOMPLETE", "ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN"):
                with self.subTest(lang=lang, code=code):
                    self.assertTrue(table.get("dms.err." + code, "").strip())


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
