"""Full native geo forms -> checked tree -> manual publication, without IO.

The collector/parser/converter and adapter are real. Only the outer PHP
transport, login boundary, Frappe DB and request/store services are substitutes;
these tests make no claims about native DB locking or live nationwide reads.
"""
from contextlib import nullcontext
from copy import deepcopy
from html import escape
import importlib.util
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.client.company_catalogue_schema import GEO_TABLES, REGISTRY
from pearnly_dms.client.catalogue_reader import CatalogueLimits, start_catalogues, advance_catalogues
from pearnly_dms.client.geo_catalogue import geo_from_catalogues
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.geo_matching import complete_tree


def record(kind, identity, **values):
    fields = {key: "" for key in GEO_TABLES[kind]}
    fields.update(values)
    return {"id": identity, "fields": fields,
            "labels": {key: "Synthetic parent " + fields[key] for key in REGISTRY[kind]["selects"]}}


def catalogues():
    return {
        "provinces": {
            "P1": record("provinces", "P1", txtprovincesname="จังหวัดหนึ่ง", txtprovincesengname="Province One"),
            "P2": record("provinces", "P2", txtprovincesname="จังหวัดสอง", txtprovincesengname="Province Two"),
            "P3": record("provinces", "P3", txtprovincesname="จังหวัดว่าง", txtprovincesengname="Empty Province"),
        },
        "districts": {
            "D1": record("districts", "D1", selprovinces="P1", txtdistrictsname="อำเภอหนึ่ง", txtdistrictsengname="District One"),
            "D2": record("districts", "D2", selprovinces="P2", txtdistrictsname="อำเภอสอง", txtdistrictsengname="District Two"),
            "D3": record("districts", "D3", selprovinces="P2", txtdistrictsname="อำเภอว่าง", txtdistrictsengname="Empty District"),
        },
        "subdistricts": {
            "S1": record("subdistricts", "S1", selprovinces="P1", seldistricts="D1", txtsubdistrictsname="ตำบลหนึ่ง", txtsubdistrictsengname="Subdistrict One"),
            "S2": record("subdistricts", "S2", selprovinces="P2", seldistricts="D2", txtsubdistrictsname="ตำบลสอง", txtsubdistrictsengname="Subdistrict Two"),
            "S3": record("subdistricts", "S3", selprovinces="P2", seldistricts="D2", txtsubdistrictsname="ตำบลว่าง", txtsubdistrictsengname="Empty Subdistrict"),
        },
        "zipcodes": {
            "Z1": record("zipcodes", "Z1", selprovinces="P1", seldistricts="D1", selsubdistricts="S1", txtzipcodesname="10000"),
            "Z2": record("zipcodes", "Z2", selprovinces="P2", seldistricts="D2", selsubdistricts="S2", txtzipcodesname="10000"),
        },
    }


class NativeGeo:
    """A strict synthetic native transport, not a substitute collector."""
    def __init__(self, rows=None):
        self.rows = deepcopy(catalogues() if rows is None else rows)
        self.calls = []
        self.fail_path = None

    def _post_text(self, path, params):
        self.calls.append((path, dict(params)))
        if path == self.fail_path:
            return "err::synthetic unavailable"
        kind, suffix = path.split("/", 1)
        if kind not in GEO_TABLES:
            raise AssertionError("Unexpected native table")
        if suffix == "component/showdata.php":
            expected = {"sd": "", "ftd": "1", "selcolsort": "1", "selcolsorttype": "1"}
            if "sdtpage" not in params:
                assert params == expected
                return "dt::" + str(len(self.rows[kind]))
            assert set(params) == set(expected) | {"sdtpage", "sdtamt"}
            assert all(params[key] == value for key, value in expected.items())
            size, page = int(params["sdtamt"]), int(params["sdtpage"])
            identities = list(self.rows[kind])[(page - 1) * size:page * size]
            return "dt::" + "".join(
                '<div data-val="' + escape(identity, quote=True)
                + '" onclick="ctllistdata(this)"></div>' for identity in identities)
        if suffix != "form.php" or set(params) != {"status", "id"} or params["status"] != "e":
            raise AssertionError("Unexpected native path or write request")
        item = self.rows[kind][params["id"]]
        body = '<form><input type="hidden" name="stsel" value="e"><input type="hidden" name="idsel" value="' + escape(item["id"], quote=True) + '">'
        for name, value in item["fields"].items():
            if name in REGISTRY[kind]["selects"]:
                body += '<select name="' + name + '"><option value="' + escape(value, quote=True) + '" selected>' + escape(item["labels"][name]) + '</option></select>'
            else:
                body += '<input name="' + name + '" value="' + escape(value, quote=True) + '">'
        return body + "</form>"


class GeoProjectionTests(unittest.TestCase):
    def reject(self, rows):
        before = deepcopy(rows)
        with self.assertRaises(DmsClientError) as caught:
            geo_from_catalogues(rows)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")
        self.assertEqual(rows, before)

    def test_complete_tree_keeps_english_and_raw_labels_without_aliasing(self):
        rows = catalogues()
        result = geo_from_catalogues(rows)
        self.assertTrue(complete_tree({key: result[key] for key in GEO_TABLES}))
        self.assertEqual(result["provinces"][""][0], ["P1", "จังหวัดหนึ่ง"])
        self.assertEqual(result["districts"]["P1"], [["D1", "อำเภอหนึ่ง"]])
        self.assertEqual(result["catalogues"], rows)
        self.assertEqual(result["catalogues"]["districts"]["D1"]["fields"]["txtdistrictsengname"], "District One")
        rows["districts"]["D1"]["fields"]["txtdistrictsengname"] = "Changed caller"
        self.assertEqual(result["catalogues"]["districts"]["D1"]["fields"]["txtdistrictsengname"], "District One")

    def test_each_parent_has_empty_children_and_repeated_postal_label_is_not_deduped(self):
        result = geo_from_catalogues(catalogues())
        self.assertEqual(result["districts"]["P3"], [])
        self.assertEqual(result["subdistricts"]["D3"], [])
        self.assertEqual(result["zipcodes"]["S3"], [])
        self.assertEqual(result["zipcodes"]["S1"], [["Z1", "10000"]])
        self.assertEqual(result["zipcodes"]["S2"], [["Z2", "10000"]])

    def test_unknown_parent_at_every_level_is_rejected_not_dropped(self):
        for kind, identity, parent in (("districts", "D1", "selprovinces"),
                                       ("subdistricts", "S1", "seldistricts"),
                                       ("zipcodes", "Z1", "selsubdistricts")):
            with self.subTest(kind=kind):
                rows = catalogues(); rows[kind][identity]["fields"][parent] = "UNKNOWN"
                self.reject(rows)

    def test_full_parent_chain_must_match_not_only_immediate_parent(self):
        for kind, identity, field, value in (("subdistricts", "S1", "selprovinces", "P2"),
                ("zipcodes", "Z1", "selprovinces", "P2"), ("zipcodes", "Z1", "seldistricts", "D2")):
            with self.subTest(kind=kind, field=field):
                rows = catalogues(); rows[kind][identity]["fields"][field] = value
                self.reject(rows)

    def test_missing_or_extra_details_names_labels_or_tables_are_rejected(self):
        for mutate in (
            lambda rows: rows.pop("zipcodes"),
            lambda rows: rows.update(extra={}),
            lambda rows: rows["provinces"].clear(),
            lambda rows: rows["provinces"]["P1"]["fields"].pop("txtprovincesengname"),
            lambda rows: rows["provinces"]["P1"]["fields"].update(unknown="field"),
            lambda rows: rows["provinces"]["P1"]["fields"].update(txtprovincesname="  "),
            lambda rows: rows["districts"]["D1"]["labels"].clear(),
            lambda rows: rows["districts"]["D1"].update(id="D2"),
            lambda rows: rows["zipcodes"]["Z1"]["fields"].update(txtzipcodesname=10000),
        ):
            rows = catalogues(); mutate(rows)
            self.reject(rows)

    def test_real_collector_reads_all_full_forms_and_rechecks_each_list_before_done(self):
        client = NativeGeo()
        limits = CatalogueLimits(page_size=2, max_seconds=30, max_total_seconds=18000)
        state = start_catalogues(client, "geo_aux", limits=limits)
        self.assertFalse(client.calls)
        with patch.object(socket, "create_connection", side_effect=AssertionError("No network")):
            while not state["done"]:
                previous = deepcopy(state)
                next_state = advance_catalogues(client, state, limits=limits, max_nodes=2)
                self.assertEqual(state, previous)
                state = next_state
        self.assertEqual(state["processed"], 11)
        self.assertEqual(state["total"], 11)
        self.assertEqual(state["data"]["catalogues"], catalogues())
        self.assertEqual(geo_from_catalogues(state["data"]["catalogues"])["catalogues"], catalogues())
        for kind, rows in catalogues().items():
            forms = [data["id"] for path, data in client.calls if path == kind + "/form.php"]
            self.assertEqual(forms, list(rows))
            counts = [data for path, data in client.calls if path == kind + "/component/showdata.php" and "sdtpage" not in data]
            self.assertEqual(len(counts), 4)  # initial/end pagination, final initial/end

    def test_native_missing_english_control_cannot_be_published_as_complete(self):
        client = NativeGeo()
        client.rows["provinces"]["P1"]["fields"].pop("txtprovincesengname")
        stage = start_catalogues(client, "geo_aux")
        before = deepcopy(stage)
        with self.assertRaises(DmsClientError):
            advance_catalogues(client, stage, max_nodes=100)
        self.assertEqual(stage, before)


class GeoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = NativeGeo()
        self.stage = None
        self.generation = {"old": "complete generation"}
        self.scope = "synthetic-current-credentials"
        self.manual = types.ModuleType("pearnly_dms.snapshots.manual")
        self.manual.REQUEST_AGE = 6 * 3600
        self.manual.assert_active = Mock(return_value={})
        self.manual.publish_guard = Mock(side_effect=lambda *a, **kw: nullcontext())
        self.store = types.ModuleType("pearnly_dms.snapshots.store")
        self.store.scope = Mock(side_effect=lambda endpoint: self.scope)
        self.store.stage = Mock(side_effect=lambda endpoint: deepcopy(self.stage))
        self.store.checkpoint = Mock(side_effect=self.checkpoint)
        self.store.publish = Mock(side_effect=self.publish)
        self.store.failed = Mock()
        self.frappe = types.ModuleType("frappe")
        self.frappe.db = types.SimpleNamespace(commit=Mock())
        self.operations = types.ModuleType("pearnly_dms.adapter.operations")
        self.operations.run_logged_in = Mock(side_effect=self.capture)
        # Load the real adapter with only its framework/login/store boundaries
        # replaced, never substituting the collector or geo conversion logic.
        with patch.dict(sys.modules, {"frappe": self.frappe,
                "pearnly_dms.adapter.operations": self.operations,
                "pearnly_dms.snapshots.manual": self.manual,
                "pearnly_dms.snapshots.store": self.store}):
            import pearnly_dms.snapshots as snapshots
            with patch.object(snapshots, "manual", self.manual, create=True), patch.object(snapshots, "store", self.store, create=True):
                spec = importlib.util.spec_from_file_location("geo_adapter_under_test", ROOT / "apps/pearnly_dms/pearnly_dms/adapter/geo_sync.py")
                self.adapter = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.adapter)

    def capture(self, endpoint, callback, **kwargs):
        self.assertEqual(kwargs, {"authoritative_read": True, "background": True, "read_only_batch": True})
        try:
            return callback(self.client, None)
        except DmsClientError as exc:
            return {"ok": False, "error_code": exc.error_code}

    def checkpoint(self, endpoint, value, *, expected_scope):
        self.assertEqual(expected_scope, self.scope)
        self.stage = deepcopy(value)

    def publish(self, endpoint, dataset, value, stamp, *, expected_scope):
        if expected_scope != self.scope:
            raise DmsClientError("scope changed", "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.assertEqual(dataset, "geo")
        self.assertTrue(complete_tree({key: value[key] for key in GEO_TABLES}))
        self.assertEqual(set(value["catalogues"]), set(GEO_TABLES))
        self.generation = deepcopy(value)

    def test_retired_no_token_and_denied_token_never_read_or_publish(self):
        self.assertEqual(self.adapter.refresh("ep"), {"ok": False, "retired": True})
        self.operations.run_logged_in.assert_not_called()
        self.store.scope.assert_not_called()
        self.manual.assert_active.side_effect = PermissionError("inactive request")
        with self.assertRaises(PermissionError):
            self.adapter.refresh("ep", request_id="request")
        self.operations.run_logged_in.assert_not_called()
        self.store.publish.assert_not_called()

    def test_complete_generation_is_full_detail_and_keeps_guards_and_budget(self):
        result = self.adapter.refresh("ep", request_id="request")
        self.assertTrue(result["complete"])
        self.assertEqual(result["count"], 11)
        self.assertIsNone(self.stage)
        self.assertEqual(self.generation["catalogues"], catalogues())
        self.manual.assert_active.assert_called_once_with("ep", "geo", "request")
        self.manual.publish_guard.assert_called_once_with("ep", "geo", "request")
        self.store.publish.assert_called_once()
        limits = self.adapter.GEO_LIMITS
        self.assertEqual((limits.max_seconds, limits.max_total_seconds, limits.max_requests,
            limits.max_total_bytes, limits.max_rows), (30, 18000, 40000, 1024 ** 3, 50000))

    def test_old_cascade_and_expired_full_checkpoint_restart_before_using_old_tree(self):
        checkpoints = [
            {},
            {"started_at": 1, "tree": {"geo": "old-cascade", "todo": []}},
            {"version": self.adapter.CHECKPOINT_VERSION - 1, "started_at": 1, "tree": {}},
            {"version": self.adapter.CHECKPOINT_VERSION, "started_at": 1, "catalogues": {"old": "expired"}},
            {"version": self.adapter.CHECKPOINT_VERSION, "started_at": float("nan"), "catalogues": {}},
        ]
        for stage in checkpoints:
            with self.subTest(stage=stage):
                self.stage = deepcopy(stage)
                result = self.adapter.refresh("ep", request_id="request")
                self.assertTrue(result["complete"])
                self.assertEqual(self.generation["catalogues"], catalogues())

    def test_partial_form_scan_never_publishes_and_next_batch_resumes_without_rereading_details(self):
        rows = {kind: {} for kind in GEO_TABLES}
        rows["provinces"] = {"P" + str(index): record("provinces", "P" + str(index),
            txtprovincesname="Synthetic Province " + str(index), txtprovincesengname="Province " + str(index))
            for index in range(125)}
        self.client = NativeGeo(rows)
        first = self.adapter.refresh("ep", request_id="request")
        self.assertFalse(first["complete"])
        self.assertEqual(len(self.client.calls), 100)
        self.assertEqual(self.stage["version"], self.adapter.CHECKPOINT_VERSION)
        self.assertLess(first["count"], 125)
        self.assertEqual(self.generation, {"old": "complete generation"})
        self.store.publish.assert_not_called()
        second = self.adapter.refresh("ep", request_id="request")
        self.assertTrue(second["complete"])
        self.assertEqual(second["count"], 125)
        forms = [params["id"] for path, params in self.client.calls if path == "provinces/form.php"]
        self.assertEqual(forms, list(rows["provinces"]))
        self.assertIsNone(self.stage)

    def test_incomplete_or_inconsistent_scan_clears_private_stage_not_old_generation(self):
        for broken in ("form", "parent"):
            self.client = NativeGeo()
            if broken == "form":
                self.client.fail_path = "districts/form.php"
            else:
                self.client.rows["subdistricts"]["S1"]["fields"]["selprovinces"] = "P2"
            result = self.adapter.refresh("ep", request_id="request")
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "ERR_DMS_SNAPSHOT_INCOMPLETE")
            self.assertIsNone(self.stage)
            self.assertEqual(self.generation, {"old": "complete generation"})
            self.store.publish.assert_not_called()
        self.assertEqual(self.store.failed.call_count, 2)

    def test_busy_does_not_publish_fail_or_discard_a_private_checkpoint(self):
        before = {"version": self.adapter.CHECKPOINT_VERSION, "started_at": self.adapter.time.time(),
                  "catalogues": start_catalogues(self.client, "geo_aux", limits=self.adapter.GEO_LIMITS)}
        self.stage = deepcopy(before)
        self.operations.run_logged_in.side_effect = None
        self.operations.run_logged_in.return_value = {"ok": False, "error_code": "ERR_DMS_SNAPSHOT_BUSY"}
        result = self.adapter.refresh("ep", request_id="request")
        self.assertTrue(result["busy"])
        self.assertEqual(self.stage, before)
        self.store.checkpoint.assert_not_called()
        self.store.failed.assert_not_called()
        self.store.publish.assert_not_called()

    def test_fresh_publish_guard_or_rotated_credentials_prevent_old_scope_publication(self):
        self.manual.publish_guard.side_effect = PermissionError("replacement request")
        with self.assertRaises(PermissionError):
            self.adapter.refresh("ep", request_id="request")
        self.store.publish.assert_not_called()
        self.assertEqual(self.generation, {"old": "complete generation"})
        self.manual.publish_guard.side_effect = lambda *a, **kw: nullcontext()
        def rotated(endpoint, callback, **kwargs):
            result = self.capture(endpoint, callback, **kwargs)
            self.scope = "rotated-credentials"
            return result
        self.operations.run_logged_in.side_effect = rotated
        with self.assertRaises(DmsClientError) as caught:
            self.adapter.refresh("ep", request_id="request")
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.assertEqual(self.generation, {"old": "complete generation"})


if __name__ == "__main__":
    unittest.main()
