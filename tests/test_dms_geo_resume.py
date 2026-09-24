"""Large synthetic four-level catalogue/resume checks; no external IO.

The full-form reader, checkpoint validation, geo projection and adapter are
real. NativeGeo supplies only PHP responses. Adapter tests reuse the existing
fake login/manual/store boundary and therefore do NOT prove MariaDB atomicity,
queue consumption, nationwide DMS compatibility or external collection speed.
"""
from collections import Counter
from copy import deepcopy
import ast
import importlib.util
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "geo_resume_fixture", Path(__file__).with_name("test_dms_geo_catalogue.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

# Exercise the exact framework-independent storage serializer without importing
# Frappe or replacing its budget. Only these two explicit source declarations
# are compiled; no DB or application module initialization runs.
storage_source = ast.parse((Path(__file__).resolve().parents[1]
    / "apps/pearnly_dms/pearnly_dms/snapshots/store.py").read_text())
storage_nodes = [node for node in storage_source.body
    if (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "MAX_BYTES"
                                            for target in node.targets))
    or (isinstance(node, ast.FunctionDef) and node.name == "_dump")]
assert len(storage_nodes) == 2
storage = {"json": json, "DmsClientError": fixture.DmsClientError}
exec(compile(ast.Module(body=storage_nodes, type_ignores=[]), "snapshot-storage-budget", "exec"), storage)


def synthetic_geo(provinces=3, districts=4, subdistricts=8):
    rows = {kind: {} for kind in fixture.GEO_TABLES}
    for p in range(provinces):
        province = "P" + str(p)
        rows["provinces"][province] = fixture.record("provinces", province,
            txtprovincesname="จังหวัดจำลองทดสอบ " + province,
            txtprovincesengname="English province " + province)
        for d in range(districts):
            district = province + "D" + str(d)
            rows["districts"][district] = fixture.record("districts", district,
                selprovinces=province, txtdistrictsname="อำเภอจำลองทดสอบ " + district,
                txtdistrictsengname="English district " + district)
            for s in range(subdistricts):
                town = district + "S" + str(s)
                rows["subdistricts"][town] = fixture.record("subdistricts", town,
                    selprovinces=province, seldistricts=district,
                    txtsubdistrictsname="ตำบลจำลองทดสอบ " + town,
                    txtsubdistrictsengname="English town " + town)
                postal = town + "Z"
                rows["zipcodes"][postal] = fixture.record("zipcodes", postal,
                    selprovinces=province, seldistricts=district, selsubdistricts=town,
                    txtzipcodesname="10000")
    for kind, records in rows.items():
        for item in records.values():
            for field in item["labels"]:
                parent_kind = field[3:]
                item["labels"][field] = rows[parent_kind][item["fields"][field]]["fields"]["txt" + parent_kind + "name"]
    return rows


def restored(value):
    """JSON is the persistence boundary; preserve native record insertion order."""
    return json.loads(json.dumps(value, ensure_ascii=False))


class ResumeTransport(fixture.NativeGeo):
    def __init__(self, rows, calls, fail_after=None):
        # A new transport per chunk models a fresh authenticated read session;
        # the synthetic upstream itself remains shared and unchanged.
        self.rows, self.calls, self.fail_path = rows, calls, None
        self.fail_after = fail_after
        self.reads = 0

    def _post_text(self, path, params):
        self.reads += 1
        if self.reads == self.fail_after:
            raise TimeoutError("Synthetic interrupted response")
        return super()._post_text(path, params)


class GeoResumeTests(unittest.TestCase):
    def setUp(self):
        for name in ("socket", "create_connection"):
            guard = patch.object(socket, name, side_effect=AssertionError("No network"))
            guard.start()
            self.addCleanup(guard.stop)
        self.limits = fixture.CatalogueLimits(max_seconds=30, max_total_seconds=18000,
            max_requests=40000, max_total_bytes=1024 ** 3, max_rows=50000)

    def advance(self, rows, calls, state, *, fail_after=None):
        before = restored(state)
        client = ResumeTransport(rows, calls, fail_after)
        result = fixture.advance_catalogues(client, state, limits=self.limits, max_nodes=100)
        self.assertLessEqual(client.reads, 100)
        self.assertEqual(state, before, "A chunk mutated the last durable checkpoint")
        return restored(result)

    def test_15785_full_forms_resume_from_json_in_100_read_slices(self):
        # Deliberately synthetic dimensions, not a claim about a live country's
        # current administrative counts: 77 + 924 + 7392 + 7392 = 15785.
        rows, calls = synthetic_geo(77, 12, 8), []
        state = fixture.start_catalogues(None, "geo_aux", limits=self.limits)
        batches, max_checkpoint_bytes = 0, 0
        while not state["done"]:
            old_count = state["processed"]
            state = self.advance(rows, calls, state)
            checkpoint = {"version": 2, "started_at": 1790209380.0, "catalogues": state}
            max_checkpoint_bytes = max(max_checkpoint_bytes, len(storage["_dump"](checkpoint).encode()))
            self.assertGreaterEqual(state["processed"], old_count)
            batches += 1
            self.assertLess(batches, 200)
        self.assertGreater(batches, 150)
        self.assertEqual((state["processed"], state["total"]), (15785, 15785))
        self.assertEqual(state["data"]["catalogues"], rows)
        result = fixture.geo_from_catalogues(state["data"]["catalogues"])
        payload_bytes = len(storage["_dump"](result).encode())
        self.assertLess(max_checkpoint_bytes, storage["MAX_BYTES"])
        self.assertLess(payload_bytes, storage["MAX_BYTES"])
        print("GEO_SYNTHETIC_SIZE", json.dumps({"rows": 15785, "batches": batches,
            "max_checkpoint_bytes": max_checkpoint_bytes, "payload_bytes": payload_bytes,
            "storage_limit_bytes": storage["MAX_BYTES"]}, sort_keys=True))
        self.assertEqual(result["catalogues"], rows)
        self.assertEqual([len(result[key]) for key in ("districts", "subdistricts", "zipcodes")],
                         [77, 924, 7392])
        for kind, records in rows.items():
            forms = [params["id"] for path, params in calls if path == kind + "/form.php"]
            self.assertEqual(forms, list(records), "A resumed chunk repeated or skipped detail forms")
            counts = [1 for path, params in calls
                      if path == kind + "/component/showdata.php" and "sdtpage" not in params]
            self.assertEqual(len(counts), 4, "Each native list must be counted before/after both scans")
        self.assertEqual(state["usage"]["requests"], len(calls))

    def test_interrupted_batch_retries_from_last_durable_checkpoint_without_missing_rows(self):
        rows, calls = synthetic_geo(), []
        state = fixture.start_catalogues(None, "geo_aux", limits=self.limits)
        state = self.advance(rows, calls, state)
        durable = restored(state)
        with self.assertRaises(fixture.DmsClientError) as caught:
            self.advance(rows, calls, state, fail_after=27)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")
        self.assertEqual(state, durable)
        # Responses received in the failed chunk were never checkpointed. They
        # may be reread; completed prior chunks must not be reread.
        committed = {(kind + "/form.php", identity)
                     for kind, values in durable["data"]["catalogues"].items() for identity in values}
        while not state["done"]:
            state = self.advance(rows, calls, restored(state))
        self.assertEqual(state["data"]["catalogues"], rows)
        counts = Counter((path, params["id"]) for path, params in calls if path.endswith("/form.php"))
        self.assertTrue(any(count > 1 for count in counts.values()))
        self.assertTrue(all(counts[key] == 1 for key in committed))

    def harness(self, rows, *, previous=None):
        harness = fixture.GeoAdapterTests("runTest")
        harness.setUp()
        harness.client = ResumeTransport(rows, [])
        if previous is not None:
            harness.stage = restored(previous.stage)
            harness.generation = restored(previous.generation)
        return harness

    def test_fresh_adapter_after_busy_resumes_private_stage_then_publishes_once(self):
        rows = synthetic_geo()
        current = self.harness(rows)
        old_generation = deepcopy(current.generation)
        first = current.adapter.refresh("ep", request_id="request")
        self.assertFalse(first["complete"])
        stage = restored(current.stage)
        current.operations.run_logged_in.return_value = {"ok": False, "error_code": "ERR_DMS_SNAPSHOT_BUSY"}
        current.operations.run_logged_in.side_effect = None
        self.assertTrue(current.adapter.refresh("ep", request_id="request")["busy"])
        self.assertEqual(current.stage, stage)
        current.store.failed.assert_not_called()
        current.store.publish.assert_not_called()
        published = 0
        for _ in range(10):
            current = self.harness(rows, previous=current)
            result = current.adapter.refresh("ep", request_id="request")
            published += current.store.publish.call_count
            if result["complete"]:
                break
            self.assertEqual(current.generation, old_generation)
            self.assertEqual(current.stage["started_at"], stage["started_at"])
            self.assertEqual(published, 0)
        else:
            self.fail("Synthetic catalogue did not complete")
        self.assertEqual(published, 1)
        self.assertIsNone(current.stage)
        self.assertEqual(current.generation["catalogues"], rows)
        self.assertEqual(current.store.publish.call_args.args[3], stage["started_at"])

    def test_final_same_count_id_drift_never_replaces_previous_generation(self):
        rows = synthetic_geo()
        current = self.harness(rows)
        total = sum(map(len, rows.values()))
        original = current.client._post_text
        changed = False
        def drift(path, params):
            nonlocal changed
            forms = sum(path.endswith("/form.php") for path, _ in current.client.calls)
            if not changed and forms == total and path == "provinces/component/showdata.php":
                current.client.rows["provinces"] = dict(reversed(list(rows["provinces"].items())))
                changed = True
            return original(path, params)
        current.client._post_text = drift
        for _ in range(10):
            result = current.adapter.refresh("ep", request_id="request")
            if not result["ok"]:
                break
            self.assertFalse(result["complete"])
            current.stage = restored(current.stage)
        else:
            self.fail("Native list drift was accepted")
        self.assertEqual(result["error_code"], "ERR_DMS_SNAPSHOT_INCOMPLETE")
        self.assertEqual(current.generation, {"old": "complete generation"})
        current.store.publish.assert_not_called()
        self.assertIsNone(current.stage)


if __name__ == "__main__":
    unittest.main(verbosity=2)
