#!/usr/bin/env python3
"""Offline snapshot protocol tests: real DmsClient, fake outer transport only.

No Site, credentials, network, browser, third-party write or disk fixtures. Synthetic
PHP responses exercise the native count-backed protocol separately verified on DMS-TEST.
Run: python3 scripts/test-dms-snapshot-reader.py
"""
from copy import deepcopy
from dataclasses import replace
from html import escape
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/pearnly_dms"))

from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.client.client_intake import _ADDR_MAP, _ADDR_SUFFIXES, _IDENTITY_MAP
from pearnly_dms.client.snapshot_reader import (
    BANK_KEYS, ERROR, GEO_PATHS, SnapshotLimits, advance_geo, collect_business,
    collect_catalog, collect_customers, collect_directory, read_customer_complete, start_geo,
)
from pearnly_dms.contracts.client_base import DmsClientError


BANKS = {key: [["1", "TEST", "Synthetic bank"]] for key in BANK_KEYS}
LIMITS = SnapshotLimits(page_size=2, max_pages=8, max_seconds=60)


def options(rows):
    return '<option value="">--</option>' + "".join(
        f'<option value="{escape(str(key), quote=True)}">{escape(label)}</option>' for key, label in rows
    )


def customer_form(cid):
    values = {name: "" for name in _IDENTITY_MAP.values()}
    values.update({name + suffix: "" for name in _ADDR_MAP.values() for suffix in _ADDR_SUFFIXES})
    values.update(txtcuscode="TEST-" + cid, txtcusname="Synthetic " + cid,
                  txtpeopleid="1000000730317", txttaxid="1000000730317", selprefix="17",
                  branchcusval="B1", txtbranchcus="TEST", txttel="0800000000")
    for suffix in _ADDR_SUFFIXES:
        values.update({"selprovinces" + suffix: "P1", "seldistricts" + suffix: "D1",
                       "selsubdistricts" + suffix: "S1", "selzipcodes" + suffix: "Z1"})
    parts = ["<form>"]
    for name, value in values.items():
        if name.startswith("sel"):
            parts.append(f'<select name="{name}"><option value="{value}" selected>TEST</option></select>')
        else:
            parts.append(f'<input name="{name}" value="{escape(value, quote=True)}">')
    return "".join(parts) + "</form>"


def customer_page(ids):
    if not ids:
        return "ndt::"
    return "dt::" + "".join(
        f'<div data-val="{cid}" onclick="ctllistdata(this);"><div class="detaildata">'
        f'<div><p>TEST-{cid}</p><p>Synthetic {cid}</p></div><div><p>-</p></div></div></div>'
        for cid in ids
    )


class Transport:
    def __init__(self, ids=("C1", "C2", "C3")):
        self.ids = list(ids)
        self.calls = []
        self.overrides = {}
        self.list_pass = 0
        self.count_override = None
        self.count_calls = 0

    def post(self, url, *, data, **kwargs):
        path = url.removeprefix("https://snapshot.invalid/dms/")
        self.calls.append((path, dict(data)))
        if path == "cus/component/showdata.php" and "sdtamt" not in data:
            self.count_calls += 1
            assert data == {"sd": ""}
            body = (self.count_override() if callable(self.count_override) else self.count_override)
            return SimpleNamespace(status_code=200, text=body if body is not None else f"dt::{len(self.ids)}")
        if path in self.overrides:
            override = self.overrides[path]
            body = override(data) if callable(override) else override
            return SimpleNamespace(status_code=200, text=body)
        if path == "drfcbc/component/bshsd.php":
            elem = data["elemname"]
            if elem == "txtcar":
                rows = [["CAR1", "T1", "Synthetic car"], ["CAR2", "T2", "Synthetic car two"]]
            elif elem == "txtcarpaint":
                rows = [["COLOR-" + data["idcar"], "TEST", "Synthetic paint"]]
            else:
                rows = [["1", "TEST", "Synthetic master"]]
            body = json.dumps(rows)
        elif path == "car/view.php":
            body = "<div>synthetic view</div>"
        elif path == "car/component/showdata.php":
            body = "dt::" + "".join(
                f'<div data-val="{cid}" onclick="ctllistdata(this)"><p>-</p></div>'
                for cid in ("CAR1", "CAR2"))
        elif path == "users/component/showdata.php":
            body = '<div data-val="U1"><p>T1</p><p>synthetic</p><p>Test employee</p></div>'
        elif path in ("cus/form.php", "drfcbc/form.php"):
            if data["status"] == "e":
                body = customer_form(data["id"])
            elif path == "drfcbc/form.php":
                body = '<select name="selprefix_fs">' + options([["18", "Synthetic prefix"]]) + '</select>'
            else:
                body = ('<form><select name="selprefix">' + options([["17", "Test prefix"]]) + '</select>'
                        '<select name="selprovinces">' + options([["P1", "Test province"], ["P2", "Test two"]])
                        + '</select></form>')
        elif path == "cus/component/listdistricts.php":
            body = options([["D" + data["selprovinces"][-1], "Test district"]])
        elif path == "cus/component/listsubdistricts.php":
            body = options([["S" + data["seldistricts"][-1], "Test subdistrict"]])
        elif path == "cus/component/listzipcodes.php":
            body = options([["Z" + data["selsubdistricts"][-1], "10000"]])
        elif path == "cus/component/showdata.php":
            assert data["sd"] == ""
            size, page = int(data["sdtamt"]), int(data["sdtpage"])
            if page == 1:
                self.list_pass += 1
            body = customer_page(self.ids[(page - 1) * size:page * size])
        else:
            raise AssertionError("Unplanned or write endpoint: " + path)
        return SimpleNamespace(status_code=200, text=body)


class SnapshotReaderTests(unittest.TestCase):
    def setUp(self):
        self.transport = Transport()
        self.client = DmsClient(self.transport, "https://snapshot.invalid/dms/")

    def collect(self, **changes):
        return collect_directory(self.client, bank_masters=deepcopy(BANKS), limits=replace(LIMITS, **changes))

    def reject(self, call):
        with self.assertRaises(DmsClientError) as caught:
            call()
        self.assertEqual(caught.exception.error_code, ERROR)
        self.assertNotIn("1000000730317", str(caught.exception))
        self.assertNotIn("Synthetic C", str(caught.exception))
        self.assertNotIn("_post_text", self.client.__dict__)

    def test_combined_real_client_native_shape(self):
        result = self.collect()
        self.assertEqual(set(result), {"masters", "paints", "geo", "customers"})
        self.assertEqual(set(result["paints"]), {"CAR1", "CAR2"})
        self.assertEqual(result["geo"]["districts"]["P2"], [["D2", "Test district"]])
        self.assertEqual(result["geo"]["zipcodes"]["S2"], [["Z2", "10000"]])
        self.assertEqual(set(result["customers"]), {"C1", "C2", "C3"})
        customer = result["customers"]["C1"]
        self.assertEqual(customer["customer_id"], "C1")
        self.assertEqual(customer["people_id"], "1000000730317")
        self.assertEqual(customer["province_id_sd"], "P1")
        self.assertEqual(customer["branch_id"], "B1")
        pages = [int(data["sdtpage"]) for path, data in self.transport.calls
                 if path == "cus/component/showdata.php" and "sdtpage" in data]
        self.assertEqual(pages, [1, 2, 1, 2])
        self.assertEqual(self.transport.count_calls, 4)
        self.assertNotIn("_post_text", self.client.__dict__)

    def test_explicit_empty_customers_is_complete(self):
        self.transport.ids = []
        self.assertEqual(self.collect()["customers"], {})
        self.assertEqual(self.transport.list_pass, 0)
        self.assertEqual(self.transport.count_calls, 4)

    def test_directory_failure_never_returns_catalogue_only(self):
        self.transport.overrides["cus/component/showdata.php"] = "<html>login</html>"
        self.reject(self.collect)

    def test_customer_partial_or_unknown_list_rejected(self):
        for body in ("", "err::denied", "dt::", "ndt::<html>login</html>",
                     customer_page(["C1"]) + '<div data-val="C2">',
                     customer_page(["C1"]) + '</div>'):
            with self.subTest(body=body):
                self.transport.overrides["cus/component/showdata.php"] = body
                self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_customer_page_duplicates_rejected(self):
        for page in (["C1", "C1"], ["C1"]):
            with self.subTest(page=page):
                self.transport.overrides["cus/component/showdata.php"] = customer_page(page)
                self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_customer_page_and_count_bounds(self):
        for changes in ({"max_pages": 1}, {"max_customers": 2}, {"page_size": 1, "max_pages": 2}):
            with self.subTest(changes=changes):
                self.reject(lambda: collect_customers(self.client, limits=replace(LIMITS, **changes)))

    def test_server_page_exceeds_requested_size_rejected(self):
        self.transport.overrides["cus/component/showdata.php"] = customer_page(["C1", "C2", "C3"])
        self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_customer_count_shape_and_early_empty_never_mean_complete(self):
        for body in ("", "40", "ndt::", "dt::", "dt::-1", "dt::01", "dt::3<html>", "dt::999999999"):
            with self.subTest(count=body):
                self.transport.count_override = body
                self.reject(lambda: collect_customers(self.client, limits=LIMITS))
        self.transport.count_override = None
        for body in ("", "ndt::", customer_page(["C1"])):
            with self.subTest(early_page=body):
                self.transport.overrides["cus/component/showdata.php"] = body
                self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_count_change_during_each_pass_rejected(self):
        for change_at in (2, 3, 4):
            with self.subTest(change_at=change_at):
                self.transport.count_calls = 0
                self.transport.count_override = lambda: "dt::" + ("2" if self.transport.count_calls == change_at else "3")
                self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_native_exact_boundary_needs_no_unknown_extra_page(self):
        self.transport.ids = ["C1", "C2"]
        result = collect_customers(self.client, limits=replace(LIMITS, max_pages=1))
        self.assertEqual(set(result), {"C1", "C2"})
        self.assertTrue(all(data.get("sdtpage", "1") == "1" for path, data in self.transport.calls
                            if path == "cus/component/showdata.php"))

    def test_extra_unlisted_customer_gets_fresh_full_form_once(self):
        result = collect_directory(self.client, bank_masters=BANKS, limits=LIMITS,
                                   extra_customer_ids=["C2", "HIDDEN", "HIDDEN"])
        self.assertEqual(set(result["customers"]), {"C1", "C2", "C3", "HIDDEN"})
        self.assertEqual(result["customers"]["HIDDEN"]["name"], "Synthetic HIDDEN")
        details = [data["id"] for path, data in self.transport.calls
                   if path == "cus/form.php" and data.get("status") == "e"]
        self.assertEqual(details, ["C1", "C2", "C3", "HIDDEN"])
        self.assertEqual(self.transport.list_pass, 2)

    def test_extra_unlisted_customer_failure_rejects_whole_batch(self):
        self.transport.overrides["cus/form.php"] = lambda data: (
            "<form></form>" if data.get("id") == "HIDDEN" else customer_form(data["id"]))
        self.reject(lambda: collect_customers(self.client, limits=LIMITS, extra_customer_ids=["HIDDEN"]))

    def test_extra_only_customer_with_empty_native_list_is_retained(self):
        self.transport.ids = []
        result = collect_customers(self.client, limits=LIMITS, extra_customer_ids=["HIDDEN"])
        self.assertEqual(set(result), {"HIDDEN"})
        self.assertEqual(result["HIDDEN"]["customer_id"], "HIDDEN")

    def test_extra_identity_and_union_bounds(self):
        for ids in (None, "C1", [""], [1], [" C1"], ["x" * 129], (str(i) for i in range(4))):
            with self.subTest(ids=type(ids).__name__):
                self.reject(lambda: collect_customers(self.client, limits=LIMITS, extra_customer_ids=ids))
        self.reject(lambda: collect_customers(self.client, limits=replace(LIMITS, max_customers=3),
                                             extra_customer_ids=["HIDDEN"]))
        self.reject(lambda: collect_customers(self.client, limits=replace(LIMITS, max_customers=3),
                                             extra_customer_ids=["X1", "X2", "X3", "X4"]))

    def test_frequent_business_has_all_colours_customers_without_geo_cascade(self):
        with patch("pearnly_dms.client.snapshot_reader._province_rows", side_effect=AssertionError("No geo")):
            result = collect_business(self.client, bank_masters=BANKS, extra_customer_ids=["HIDDEN"], limits=LIMITS)
        self.assertEqual(set(result), {"masters", "paints", "customers"})
        self.assertEqual(set(result["paints"]), {"CAR1", "CAR2"})
        self.assertIn("HIDDEN", result["customers"])
        self.assertFalse(set(path for path, _ in self.transport.calls) & {path for path, _ in GEO_PATHS.values()})

    def test_geo_batches_finish_same_tree_as_one_shot_without_repeated_parent(self):
        expected = self.collect()["geo"]
        self.transport.calls.clear()
        stage = start_geo(self.client, limits=LIMITS)
        self.assertEqual(stage["processed"], 0)
        self.assertEqual(stage["todo"], [["districts", "P1"], ["districts", "P2"]])
        while stage["todo"]:
            previous = deepcopy(stage)
            next_stage = advance_geo(self.client, stage, max_nodes=1, limits=LIMITS)
            self.assertEqual(stage, previous)
            self.assertEqual(next_stage["processed"], stage["processed"] + 1)
            stage = next_stage
        self.assertEqual(stage["geo"], expected)
        self.assertEqual(stage["processed"], 6)
        calls = [(path, tuple(data.items())) for path, data in self.transport.calls if path in {p for p, _ in GEO_PATHS.values()}]
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(set(calls)), 6)
        self.assertEqual(sum(path == "cus/form.php" for path, _ in self.transport.calls), 2)

    def test_geo_batch_native_failure_discards_candidate_not_input(self):
        stage = start_geo(self.client, limits=LIMITS)
        before = deepcopy(stage)
        self.transport.overrides["cus/component/listdistricts.php"] = lambda data: (
            "<html>failed</html>" if data["selprovinces"] == "P2" else options([["D1", "District"]]))
        self.reject(lambda: advance_geo(self.client, stage, limits=LIMITS))
        self.assertEqual(stage, before)

    def test_geo_batch_refuses_duplicate_child_and_bad_checkpoints(self):
        stage = start_geo(self.client, limits=LIMITS)
        self.transport.overrides["cus/component/listdistricts.php"] = options([["DUP", "District"]])
        self.reject(lambda: advance_geo(self.client, stage, limits=LIMITS))
        self.transport.overrides.clear()
        variants = []
        bad = deepcopy(stage); bad["todo"].append(bad["todo"][0]); variants.append(bad)
        bad = deepcopy(stage); bad["todo"].pop(); variants.append(bad)
        bad = deepcopy(stage); bad["processed"] = 1; variants.append(bad)
        bad = deepcopy(stage); bad["geo"]["districts"]["FOREIGN"] = []; variants.append(bad)
        bad = deepcopy(stage); bad["todo"][0][0] = "unknown"; variants.append(bad)
        for bad in variants:
            with self.subTest(stage=bad):
                self.transport.calls.clear()
                self.reject(lambda: advance_geo(self.client, bad, limits=LIMITS))
                self.assertEqual(self.transport.calls, [])

    def test_geo_batch_rejects_root_change_before_completion(self):
        stage = start_geo(self.client, limits=LIMITS)
        self.transport.overrides["cus/form.php"] = ('<select name="selprovinces">'
            + options([["P1", "Changed name"], ["P2", "Test two"]]) + '</select>')
        original = deepcopy(stage)
        self.reject(lambda: advance_geo(self.client, stage, limits=LIMITS))
        self.assertEqual(stage, original)

    def test_geo_batch_safely_pauses_near_deadline(self):
        stage = start_geo(self.client, limits=LIMITS)
        clock = [0]
        original = self.transport.post
        def slow(*args, **kwargs):
            response = original(*args, **kwargs)
            clock[0] += 0.95
            return response
        with patch("pearnly_dms.client.snapshot_reader.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.transport, "post", side_effect=slow):
            result = advance_geo(self.client, stage, limits=replace(LIMITS, max_seconds=1))
        self.assertEqual(result["processed"], 1)
        self.assertTrue(result["todo"])
        self.assertEqual(stage["processed"], 0)

    def test_geo_batch_request_bound_retains_pending_queue(self):
        stage = start_geo(self.client, limits=LIMITS)
        while stage["todo"]:
            previous = stage["processed"]
            stage = advance_geo(self.client, stage, limits=replace(LIMITS, max_requests=2))
            self.assertEqual(stage["processed"], previous + 1)
        self.assertEqual(stage["processed"], 6)
        for value in (0, -1, True, 1.5, 1001):
            self.reject(lambda: advance_geo(self.client, stage, max_nodes=value, limits=LIMITS))

    def test_geo_postcode_can_have_multiple_parents_but_not_duplicate_in_one(self):
        self.transport.overrides["cus/component/listzipcodes.php"] = options([["Z", "10000"]])
        stage = advance_geo(self.client, start_geo(self.client, limits=LIMITS), limits=LIMITS)
        self.assertEqual(stage["todo"], [])
        self.assertEqual(stage["geo"]["zipcodes"], {"S1": [["Z", "10000"]], "S2": [["Z", "10000"]]})
        self.transport.overrides["cus/component/listzipcodes.php"] = options([["Z", "10000"], ["Z", "10000"]])
        self.reject(lambda: advance_geo(self.client, start_geo(self.client, limits=LIMITS), limits=LIMITS))

    def test_missing_customer_control_not_silent_blank(self):
        self.transport.overrides["cus/form.php"] = customer_form("C1").replace('<input name="txttel" value="0800000000">', '')
        self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_full_writeback_read_has_same_controls_and_keeps_explicit_source_id(self):
        result = read_customer_complete(self.client, "TARGET", limits=LIMITS)
        self.assertEqual(result["customer_id"], "TARGET")
        self.assertEqual(result["province_id_sd"], "P1")
        self.assertEqual(self.transport.calls, [("cus/form.php", {"status": "e", "id": "TARGET"})])
        for cid in (None, 1, "", " X"):
            self.transport.calls.clear()
            self.reject(lambda: read_customer_complete(self.client, cid, limits=LIMITS))
            self.assertEqual(self.transport.calls, [])

    def test_writeback_truncation_and_duplicate_nonidentity_control_rejected(self):
        forms = ("", "<form></form>", customer_form("TARGET").replace('<input name="txttel" value="0800000000">', ''),
                 customer_form("TARGET") + '<input name="txttel" value="">')
        for form in forms:
            with self.subTest(form_length=len(form)):
                self.transport.overrides["cus/form.php"] = form
                self.reject(lambda: read_customer_complete(self.client, "TARGET", limits=LIMITS))

    def test_writeback_response_budget_is_enforced(self):
        self.reject(lambda: read_customer_complete(self.client, "TARGET",
                                                  limits=replace(LIMITS, max_response_bytes=30)))

    def test_duplicate_identity_control_rejected(self):
        self.transport.overrides["cus/form.php"] = customer_form("C1") + '<input name="txtpeopleid" value="other">'
        self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_list_changed_during_detail_reads_rejected(self):
        def changed(data):
            size, page = int(data["sdtamt"]), int(data["sdtpage"])
            if page == 1:
                self.transport.list_pass += 1
            ids = self.transport.ids if self.transport.list_pass == 1 else ["C2", "C1", "C3"]
            return customer_page(ids[(page - 1) * size:page * size])
        self.transport.overrides["cus/component/showdata.php"] = changed
        self.reject(lambda: collect_customers(self.client, limits=LIMITS))

    def test_geo_malformed_empty_and_duplicate_options_rejected(self):
        for body in ("", '<html>login</html>', '<option value="X">broken',
                     options([["D", "one"], ["D", "two"]])):
            with self.subTest(body=body):
                self.transport.overrides["cus/component/listdistricts.php"] = body
                self.reject(self.collect)

    def test_geo_same_child_under_different_parents_rejected(self):
        self.transport.overrides["cus/component/listdistricts.php"] = options([["D", "same"]])
        self.reject(self.collect)

    def test_explicit_placeholder_only_geo_can_be_empty(self):
        self.transport.overrides["cus/component/listdistricts.php"] = options([])
        result = self.collect()
        self.assertEqual(result["geo"]["districts"], {"P1": [], "P2": []})
        self.assertEqual(result["geo"]["subdistricts"], {})

    def test_bank_missing_and_duplicate_rejected(self):
        for banks in ({}, {**BANKS, "card_banks": None}, {**BANKS, "card_banks": [["1", "x"], ["1", "y"]]}):
            with self.subTest(banks=banks):
                self.reject(lambda: collect_catalog(self.client, bank_masters=banks, limits=LIMITS))

    def test_read_budget_rejects_before_next_http(self):
        self.reject(lambda: collect_customers(self.client, limits=replace(LIMITS, max_requests=2)))
        self.assertEqual(len(self.transport.calls), 2)

    def test_response_and_row_bounds(self):
        for changes in ({"max_response_bytes": 10}, {"max_total_bytes": 10}, {"max_rows": 1}):
            with self.subTest(changes=changes):
                self.reject(lambda: self.collect(**changes))

    def test_deadline_checked_before_io(self):
        with patch("pearnly_dms.client.snapshot_reader.time.monotonic", side_effect=[0, 100]):
            self.reject(lambda: collect_customers(self.client, limits=LIMITS))
        self.assertEqual(self.transport.calls, [])

    def test_native_http_error_fails_closed(self):
        with patch.object(self.transport, "post", return_value=SimpleNamespace(status_code=503, text="PRIVATE")):
            self.reject(self.collect)

    def test_caught_prefix_source_failure_still_poisons_batch(self):
        original = self.transport.post
        def reject_prefix(url, **kwargs):
            if url.endswith("drfcbc/form.php"):
                return SimpleNamespace(status_code=503, text="PRIVATE")
            return original(url, **kwargs)
        with patch.object(self.transport, "post", side_effect=reject_prefix):
            self.reject(self.collect)

    def test_existing_client_override_restored(self):
        original = self.client._post_text
        self.client._post_text = original
        self.collect()
        self.assertIs(self.client.__dict__["_post_text"], original)
        del self.client._post_text

    def test_write_endpoint_not_granted(self):
        with patch.object(self.client, "fetch_masters", side_effect=lambda **kw:
                          self.client._post_text("cus/new.php", {"status": "n"})):
            self.reject(self.collect)
        self.assertEqual(self.transport.calls, [])

    def test_missing_colour_page_fails_whole_batch(self):
        original = self.transport.post
        def reject_colour(url, **kwargs):
            if (url.endswith("bshsd.php") and kwargs["data"].get("elemname") == "txtcarpaint"
                    and kwargs["data"].get("idcar") == "CAR2"):
                return SimpleNamespace(status_code=503, text="PRIVATE")
            return original(url, **kwargs)
        with patch.object(self.transport, "post", side_effect=reject_colour):
            self.reject(self.collect)

    def test_unknown_json_not_empty_catalogue(self):
        for body in ("", "null", "{}", "<html>login</html>"):
            with self.subTest(body=body):
                self.transport.overrides["drfcbc/component/bshsd.php"] = body
                self.reject(self.collect)

    def test_invalid_limits_rejected(self):
        for changes in ({"max_seconds": float("nan")}, {"max_pages": 1.5}, {"max_requests": 0},
                        {"page_size": 501}, {"max_customers": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(LIMITS, **changes)


if __name__ == "__main__":
    with patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden")), \
            patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
        unittest.main(verbosity=2)
