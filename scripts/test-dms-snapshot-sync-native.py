"""Native background snapshot integration in the internal, disposable lab only.

Real collectors/parsers, permissions, MariaDB checkpoints/transactions, Redis
locks and serialized RQ execute_job consumption. Only authenticated login and
outer DMS HTTP are synthetic. No order/customer/attachment write is allowed.
The RQ SimpleWorker keeps these outer transport substitutes in-process; the
separate queue-native test also exercises an unmodified native burst process.
"""
import copy
from pathlib import Path
import runpy
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import frappe
from frappe.utils.background_jobs import get_queue
from rq import SimpleWorker

from pearnly_dms.adapter import geo_sync, snapshot_sync
from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.snapshots import schedule, store

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))
protocol = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-reader.py")))
METHODS = {"pearnly_dms.adapter.geo_sync.refresh", "pearnly_dms.adapter.snapshot_sync.refresh",
           "pearnly_dms.query.snapshot_sync.refresh"}


class BusinessTransport(protocol["Transport"]):
    def post(self, url, *, data, **kwargs):
        path = url.removeprefix("https://snapshot.invalid/dms/")
        if path in ("bank/view.php", "bank/component/showdata.php"):
            self.calls.append((path, dict(data)))
            body = "synthetic bank view" if path.endswith("view.php") else (
                '<div data-val="1" onclick="ctllistdata(this)"><p>-</p></div>')
            return SimpleNamespace(status_code=200, text=body)
        return super().post(url, data=data, **kwargs)


class NationTransport:
    """101 synthetic branches: 303 complete parent blocks require two chunks."""
    def __init__(self):
        self.calls, self.forbidden = [], []
        self.fail = False

    def post(self, url, *, data, **kwargs):
        path = url.removeprefix("https://snapshot.invalid/dms/")
        self.calls.append((path, dict(data)))
        if path == "cus/form.php" and data == {"status": "n"}:
            rows = [["P" + str(i), "SYNTHETIC Province " + str(i)] for i in range(101)]
            body = '<select name="selprovinces">' + protocol["options"](rows) + '</select>'
        elif path in ("cus/component/listdistricts.php", "cus/component/listsubdistricts.php",
                      "cus/component/listzipcodes.php"):
            if self.fail:
                return SimpleNamespace(status_code=503, text="SYNTHETIC FAILURE")
            spec = {"cus/component/listdistricts.php": ("selprovinces", "D"),
                    "cus/component/listsubdistricts.php": ("seldistricts", "S"),
                    "cus/component/listzipcodes.php": ("selsubdistricts", "Z")}
            field, prefix = spec[path]
            assert set(data) == {field}
            key = prefix + data[field][1:]
            body = protocol["options"]([[key, "SYNTHETIC " + key]])
        else:
            self.forbidden.append(path)
            raise AssertionError("Unexpected DMS endpoint, no writes allowed")
        return SimpleNamespace(status_code=200, text=body)


class NativeSnapshotSync(lab["SnapshotLabCase"]):
    def setUp(self):
        super().setUp()
        self.jobs = set()
        self.logins = []
        self.transport = NationTransport()
        self.assertEqual(get_queue("dms_sync").count, 0, "Refusing foreign queued work")

    def owns(self, job):
        return (job.func_name == "frappe.utils.background_jobs.execute_job"
                and job.kwargs.get("site") == lab["SITE"]
                and job.kwargs.get("method") in METHODS
                and job.kwargs.get("kwargs", {}).get("endpoint") in {*self.endpoints, self.template})

    def remember_jobs(self):
        queue = get_queue("dms_sync")
        for job_id in queue.get_job_ids():
            job = queue.fetch_job(job_id)
            self.assertTrue(self.owns(job), "Refusing to consume or clean a foreign job")
            self.jobs.add(job_id)
        return queue

    def tearDown(self):
        if not getattr(frappe.local, "site", None):
            lab["connect"]()
        queue = self.remember_jobs()
        for job_id in self.jobs:
            job = queue.fetch_job(job_id)
            if job:
                self.assertTrue(self.owns(job))
                queue.remove(job_id)
                job.delete()
        self.assertEqual(getattr(self.transport, "forbidden", []), [])
        super().tearDown()

    def authenticated(self, endpoint, callback, **kwargs):
        self.assertEqual(endpoint, self.endpoint)
        self.assertEqual(kwargs, {"authoritative_read": True, "background": True})
        self.logins.append(endpoint)
        try:
            return callback(DmsClient(self.transport, "https://snapshot.invalid/dms/"), SimpleNamespace())
        except DmsClientError as exc:
            return {"ok": False, "error_code": exc.error_code}

    def consume(self):
        queue = self.remember_jobs()
        self.assertTrue(self.jobs)
        frappe.db.commit()
        frappe.destroy()
        try:
            worker = SimpleWorker([queue], connection=queue.connection,
                                  name="snapshot-native-" + uuid.uuid4().hex)
            self.assertTrue(worker.work(burst=True, logging_level="ERROR", with_scheduler=False))
        finally:
            lab["connect"]()
        for job_id in self.jobs:
            job = queue.fetch_job(job_id)
            self.assertEqual(job.get_status(refresh=True), "finished", job.latest_result())
        self.assertEqual(queue.count, 0)

    def test_business_background_collects_full_native_data_without_geo_cascade(self):
        self.key()
        self.transport = BusinessTransport()
        with patch.object(snapshot_sync, "run_logged_in", self.authenticated):
            result = snapshot_sync.refresh(self.endpoint)
        self.assertTrue(result["ok"], result)
        data = store.read(self.endpoint)["data"]
        self.assertEqual(set(data), {"masters", "paints", "customers"})
        self.assertEqual(set(data["customers"]), {"C1", "C2", "C3"})
        self.assertEqual(set(data["paints"]), {"CAR1", "CAR2"})
        for key in protocol["BANK_KEYS"]:
            self.assertTrue(data["masters"][key])
        self.assertFalse(any(path in {"cus/component/listdistricts.php", "cus/component/listsubdistricts.php",
                                     "cus/component/listzipcodes.php"} for path, _ in self.transport.calls))
        self.assertEqual(len(self.logins), 1)

    def test_business_failure_keeps_last_native_generation(self):
        self.key()
        self.transport = BusinessTransport()
        with patch.object(snapshot_sync, "run_logged_in", self.authenticated):
            self.assertTrue(snapshot_sync.refresh(self.endpoint)["ok"])
            before = store.read(self.endpoint)
            self.transport.overrides["cus/component/showdata.php"] = "err::SYNTHETIC_FAILURE"
            self.assertFalse(snapshot_sync.refresh(self.endpoint)["ok"])
        after = store.read(self.endpoint)
        for key in ("data", "generation", "as_of", "completed_at"):
            self.assertEqual(after[key], before[key])
        self.assertTrue(after["stale"])

    def test_geo_continuation_uses_real_serialized_rq_then_publishes_complete_tree(self):
        self.key()
        with patch.object(geo_sync, "run_logged_in", self.authenticated):
            first = geo_sync.refresh(self.endpoint)
            self.assertTrue(first["ok"])
            self.assertFalse(first["complete"])
            self.assertEqual(first["processed"], 300)
            stage = store.stage(self.endpoint)
            self.assertEqual(len(stage["tree"]["todo"]), 3)
            self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "geo")
            self.assertEqual(get_queue("dms_sync").count, 1)
            self.consume()
            result = store.read(self.endpoint, "geo", max_age=3 * 86400, refresh_interval=86400)
            self.assertAlmostEqual(result["as_of"], stage["started_at"], delta=0.000001)
            self.assertEqual(len(result["data"]["provinces"][""]), 101)
            for level in ("districts", "subdistricts", "zipcodes"):
                self.assertEqual(len(result["data"][level]), 101)
            self.assertIsNone(store.stage(self.endpoint))
            self.assertEqual(len(self.logins), 2)
            # A fresh daily generation does no login or new queue work.
            self.assertEqual(geo_sync.refresh(self.endpoint), {"ok": True, "fresh": True})
            self.assertEqual(len(self.logins), 2)

    def test_geo_failed_second_chunk_retains_old_tree_and_resumes_private_checkpoint(self):
        self.publish(lab["geo"](), dataset="geo", start=time.time() - 90000)
        frappe.db.commit()
        old = store.read(self.endpoint, "geo", max_age=3 * 86400, refresh_interval=86400)
        with patch.object(geo_sync, "run_logged_in", self.authenticated):
            self.assertFalse(geo_sync.refresh(self.endpoint)["complete"])
            stage = copy.deepcopy(store.stage(self.endpoint))
            self.transport.fail = True
            self.consume()
            self.assertEqual(store.stage(self.endpoint), stage)
            retained = store.read(self.endpoint, "geo", max_age=3 * 86400, refresh_interval=86400)
            self.assertEqual(retained["generation"], old["generation"])
            self.assertEqual(retained["data"], old["data"])
            self.transport.fail = False
            self.assertTrue(geo_sync.refresh(self.endpoint)["complete"])
            fresh = store.read(self.endpoint, "geo", max_age=3 * 86400, refresh_interval=86400)
            self.assertNotEqual(fresh["generation"], old["generation"])
            self.assertAlmostEqual(fresh["as_of"], stage["started_at"], delta=0.000001)
            self.assertIsNone(store.stage(self.endpoint))

    def test_native_schedule_deduplicates_authority_scope_and_separates_employee_scopes(self):
        self.key()
        for endpoint in self.endpoints:
            self.key(endpoint, "sales")
        result = schedule.sweep()
        frappe.db.commit()
        queue = self.remember_jobs()
        self.assertEqual(result["queued"], 4)
        self.assertEqual(queue.count, 4)
        methods = [queue.fetch_job(job_id).kwargs["method"] for job_id in queue.get_job_ids()]
        self.assertEqual(methods.count("pearnly_dms.adapter.snapshot_sync.refresh"), 1)
        self.assertEqual(methods.count("pearnly_dms.adapter.geo_sync.refresh"), 1)
        self.assertEqual(methods.count("pearnly_dms.query.snapshot_sync.refresh"), 2)
        schedule.sweep()
        frappe.db.commit()
        self.assertEqual(set(queue.get_job_ids()), self.jobs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
