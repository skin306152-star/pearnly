"""Zero-I/O regressions: claiming a same-name legacy DMS customer from the LINE ID-card flow.

Old bug (ris, 2026-10-29 11:45-11:50): DMS held an old record with the same name but no ID number,
customer code, title or address. The LINE card listed it as a similar customer. Claiming it sent
only ID number + name and was stopped before DMS with "the selected item is no longer in the DMS
master data"; choosing "new customer" was refused by DMS because two customers may not share a
name, and the operator got "check the fields and retry". Neither could ever succeed.

Now: a claim (or an update) fills the title and the ID-card address block that are empty in DMS
from this ID card, and touches nothing DMS already has; what cannot be filled is reported as a
missing title / address (edit page) instead of a vanished master item.

The writes run the real DMS client, the real ledger marker and the real
``intake.service.push_idcard_fields`` over the in-memory Frappe stand-in of
``test_dms_customer_reconcile.py`` and a fake DMS form transport. Nothing logs in, posts to DMS,
sends LINE or touches a Site.
"""
from __future__ import annotations

import copy
from html import escape
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
PID, NAME = identity["PID"], identity["NAME"]
EP, USER = _reconcile["EP"], _reconcile["USER"]

ADDRESS_KEYS = ("txthousenum", "txtbuilding", "txtfloor", "txtroom", "txtvillage", "txtmoo", "txtsoi", "txtroad")
SELECTS = ("selprovinces", "seldistricts", "selsubdistricts", "selzipcodes")
#: What the LINE draft carries for this ID card (prefix and the four levels resolved on the live masters).
DRAFT = {"people_id": PID, "name": NAME, "prefix_id": "17", "prefix_name": "นาย", "tax_id": PID,
         "birthday_be": "01/01/2530", "phone": "0800000999", "house_no": "9/9", "moo": "4", "soi": "",
         "road": "", "province_id": "P", "district_id": "D", "subdistrict_id": "S", "zipcode_id": "Z"}


def legacy(**extra):
    """A same-name record left without ID, tax ID, code, title or any address (ris customer 6062)."""
    old = record("", txtcuscode="", txttaxid="", selprefix="", txttel="")
    for suffix in ("", "_ct", "_sd"):
        for field in SELECTS + ADDRESS_KEYS:
            old[field + suffix] = ""
    old.update(extra)
    return old


def native_form(data):
    """The edit form the way DMS renders it: every select lists its options, the stored one selected
    (an empty select has no selected option, so the native placeholder ``""`` comes first)."""
    options = {"selprefix": ("17", "3")}
    options.update({name + suffix: ("P",) for name in ("selprovinces",) for suffix in ("", "_ct", "_sd")})
    parts = ["<form>"]
    for key, value in data.items():
        if key.startswith("sel"):
            values = [""] + [v for v in options.get(key, ()) if v != value] + ([value] if value else [])
            parts.append(f'<select name="{key}">' + "".join(
                f'<option value="{escape(v)}"{" selected" if v == value and v else ""}>label</option>'
                for v in values) + "</select>")
        else:
            parts.append(f'<input name="{key}" value="{escape(value, quote=True)}">')
    return "".join(parts) + "</form>"


class ClaimLegacy(unittest.TestCase):
    snapshot = _reconcile["Reconcile"].snapshot
    session = _reconcile["Reconcile"].session

    def setUp(self):
        _reconcile["Reconcile"].setUp(self)
        from pearnly_dms.intake import draft
        self.draft = draft
        self.setup_dms(legacy())

    def setup_dms(self, old):
        self.transport = Transport({"old": old})
        self.transport.form_overrides["old"] = lambda: native_form(self.transport.records["old"])
        self.client = DmsClient(self.transport, "https://identity.invalid/dms/")
        self.baseline = self.client.read_customer("old")  # the candidate's baseline on the LINE card

    def payload(self, **changes):
        """The reviewing payload after the operator tapped candidate 1 (``flow`` ``ACT_PICK``)."""
        return {"draft": dict(DRAFT), "field_diffs": [], "customer_id": "old",
                "customer_baseline": copy.deepcopy(self.baseline), **changes}

    def claim(self, payload=None, request="line:claim"):
        payload = payload or self.payload()
        self.transport.calls.clear()
        return self.service.push_idcard_fields(
            EP, fields=self.draft.update_fields(payload), mode="update", customer_id="old",
            baseline=payload["customer_baseline"], request_id=request, actor=USER)

    def writes(self):
        return [path for path, _ in self.transport.writes()]

    def dms(self):
        return self.transport.records["old"]

    # ── the ris case, end to end through the real service ────────────────────
    def test_claiming_the_legacy_record_fills_its_empty_title_and_address_once(self):
        out = self.claim()
        self.assertTrue(out["success"], out)
        self.assertEqual((out["customer_id"], out["mode"]), ("old", "update"))
        self.assertEqual(self.writes(), ["cus/edit.php"])
        dms = self.dms()
        self.assertEqual((dms["txtpeopleid"], dms["txttaxid"], dms["txtcusname"]), (PID, PID, NAME))
        self.assertEqual(dms["selprefix"], "17")
        for suffix in ("", "_ct", "_sd"):  # contact / delivery blocks were empty too: filled from it
            self.assertEqual([dms[f + suffix] for f in SELECTS], ["P", "D", "S", "Z"], suffix)
            self.assertEqual((dms["txthousenum" + suffix], dms["txtmoo" + suffix]), ("9/9", "4"))
        self.assertEqual(dms["txtcuscode"], "")  # the claim never invents a customer code
        self.assertEqual(dms["txttel"], "")  # the phone rule is unchanged: a claim does not write it
        [row] = [row for name, row in self.ledger.rows.items() if name.startswith("CW-")]
        self.assertEqual((row["status"], row["business_key"]), ("success", "old"))

    def test_without_the_fill_the_claim_is_a_missing_title_before_anything_is_sent(self):
        # The pre-fix request (ID number + name only) is stopped by the form check with its own code.
        out = self.claim(self.payload(write_fields={"people_id": PID, "name": NAME}))
        self.assertEqual(out["error_code"], "ERR_DMS_PREFIX_MISSING")
        self.assertEqual(self.writes(), [])
        self.assertFalse([name for name in self.ledger.rows if name.startswith("CW-")])  # nothing dispatched

    def test_title_alone_is_then_a_missing_address_not_a_vanished_master_item(self):
        out = self.claim(self.payload(write_fields={"people_id": PID, "name": NAME, "prefix_id": "17"}))
        self.assertEqual(out["error_code"], "ERR_DMS_ADDRESS_INCOMPLETE")
        self.assertEqual(self.writes(), [])

    def test_values_dms_already_has_are_never_overwritten(self):
        kept = legacy(selprefix="3")
        for suffix, value in (("", "P"), ("_ct", "P")):
            kept.update({"selprovinces" + suffix: value, "seldistricts" + suffix: "D",
                         "selsubdistricts" + suffix: "S", "selzipcodes" + suffix: "Z",
                         "txthousenum" + suffix: "1 old road"})
        self.setup_dms(kept)
        fields = self.draft.update_fields(self.payload())
        self.assertEqual(fields, {"people_id": PID, "name": NAME})
        out = self.claim()
        self.assertTrue(out["success"], out)
        dms = self.dms()
        self.assertEqual((dms["selprefix"], dms["txthousenum"], dms["txthousenum_ct"]), ("3", "1 old road", "1 old road"))
        self.assertEqual([dms[f + "_sd"] for f in SELECTS], ["P", "D", "S", "Z"])  # the empty block only

    def test_a_title_set_in_dms_after_the_card_is_a_conflict_not_an_overwrite(self):
        self.dms()["selprefix"] = "3"  # someone fixed it in the DMS web page meanwhile
        out = self.claim()
        self.assertEqual(out["error_code"], "ERR_DMS_CUSTOMER_CHANGED")
        conflicts = out["response_body"]["customer_conflict"]["conflicts"]
        self.assertIn({"field": "prefix_id", "old": "3", "new": "17"}, conflicts)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.dms()["selprefix"], "3")


class MasterGaps(unittest.TestCase):
    """``draft.master_gaps``: what a write to a DMS customer record still lacks (pure)."""

    snapshot = _reconcile["Reconcile"].snapshot
    session = _reconcile["Reconcile"].session

    def setUp(self):
        _reconcile["Reconcile"].setUp(self)
        from pearnly_dms.intake import draft
        self.gaps = draft.master_gaps

    def test_empty_record_is_filled_from_the_draft(self):
        fill, missing = self.gaps({}, DRAFT)
        self.assertEqual(missing, "")
        self.assertEqual(fill, {"prefix_id": "17", "house_no": "9/9", "moo": "4", "province_id": "P",
                                "district_id": "D", "subdistrict_id": "S", "zipcode_id": "Z"})
        self.assertEqual(self.gaps(None, DRAFT), (fill, ""))  # a new customer: nothing in DMS yet

    def test_complete_record_needs_nothing(self):
        current = {"prefix_id": "3", "province_id": "P1", "district_id": "D1", "subdistrict_id": "S1",
                   "zipcode_id": "Z1"}
        self.assertEqual(self.gaps(current, {}), ({}, ""))

    def test_title_that_cannot_be_resolved_goes_to_the_edit_page(self):
        self.assertEqual(self.gaps({}, {**DRAFT, "prefix_id": ""}), ({}, "ERR_DMS_PREFIX_MISSING"))

    def test_address_without_all_four_levels_goes_to_the_edit_page(self):
        for key in ("province_id", "district_id", "subdistrict_id", "zipcode_id"):
            with self.subTest(key=key):
                self.assertEqual(self.gaps({"prefix_id": "3"}, {**DRAFT, key: ""}),
                                 ({}, "ERR_DMS_ADDRESS_INCOMPLETE"))

    def test_half_filled_address_is_never_mixed_with_the_draft(self):
        self.assertEqual(self.gaps({"prefix_id": "3", "house_no": "1 old road"}, DRAFT),
                         ({}, "ERR_DMS_ADDRESS_INCOMPLETE"))

    def test_address_is_reported_before_title(self):
        self.assertEqual(self.gaps({}, {**DRAFT, "prefix_id": "", "zipcode_id": ""})[1],
                         "ERR_DMS_ADDRESS_INCOMPLETE")


class ClaimLookup(unittest.TestCase):
    """``recognize_lookup(claim=...)``: the operator picked a candidate; compare the card with that record."""

    snapshot = _reconcile["Reconcile"].snapshot
    session = _reconcile["Reconcile"].session

    def setUp(self):
        _reconcile["Reconcile"].setUp(self)
        import types
        sys.modules["pearnly_dms.adapter.snapshot_writeback"].with_customer_detail = lambda endpoint, fn: fn()
        resolved = types.SimpleNamespace(province_id="P", district_id="D", subdistrict_id="S", zipcode_id="Z")
        test = self

        class Directory:
            def __init__(self, endpoint):
                test.assertEqual(endpoint, EP)

            def lookup_customer(self, *args, **kwargs):
                raise AssertionError("a picked candidate is not looked up by ID again")

            def search_customers_detailed(self, *args, **kwargs):
                raise AssertionError("a picked candidate is not searched by name again")

            list_prefixes = staticmethod(lambda: [["17", "นาย"]])
            resolve_address = staticmethod(lambda addr: resolved)
            list_geo = staticmethod(lambda level, parent="": [[{"provinces": "P", "districts": "D",
                                                                 "subdistricts": "S", "zipcodes": "Z"}[level], level]])
            metadata = staticmethod(lambda: {})

        fake = types.ModuleType("pearnly_dms.snapshots.directory")
        fake.Directory = Directory
        modules = patch.dict(sys.modules, {"pearnly_dms.snapshots.directory": fake})
        modules.start()
        self.addCleanup(modules.stop)

    def lookup(self, current):
        address = {"house_no": "9/9", "moo": "4", "province": "p", "district": "d", "subdistrict": "s", "zipcode": "z"}
        return self.service.recognize_lookup(EP, people_id=PID, name=NAME, ocr_address=address, phone="",
                                             claim={"customer_id": "6062", "fields": current})

    def test_picked_record_without_id_lists_the_id_and_its_empty_address(self):
        current = {"name": NAME, "people_id": "", "prefix_id": "", "house_no": "", "province_id": "",
                   "district_id": "", "subdistrict_id": "", "zipcode_id": ""}
        res = self.lookup(current)
        self.assertEqual((res["scenario"], res["match"]["customer_id"], res["match"]["current_fields"]),
                         ("exact", "6062", current))
        self.assertEqual(res["field_diffs"][0], {"field": "people_id", "old": "", "new": PID})
        self.assertEqual({d["field"] for d in res["field_diffs"][1:]},
                         {"house_no", "moo", "province_id", "district_id", "subdistrict_id", "zipcode_id"})

    def test_picked_record_with_another_id_shows_that_id_being_replaced(self):
        res = self.lookup({"name": NAME, "people_id": identity["OTHER"], "province_id": "P", "district_id": "D",
                           "subdistrict_id": "S", "zipcode_id": "Z", "house_no": "9/9", "moo": "4"})
        self.assertEqual(res["field_diffs"], [{"field": "people_id", "old": identity["OTHER"], "new": PID}])


class NoNetwork(unittest.TestCase):
    def test_suite_uses_no_network(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network used")):
            suite = unittest.TestSuite()
            loader = unittest.TestLoader()
            for case in (ClaimLegacy, MasterGaps, ClaimLookup):
                suite.addTests(loader.loadTestsFromTestCase(case))
            result = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w")).run(suite)
        self.assertTrue(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main(verbosity=2)
