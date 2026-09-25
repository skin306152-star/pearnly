"""Manual request/queue/authorization regressions in the isolated native lab.

Real DB, Redis and native queue; synthetic collectors only. HTTP is denied by
the inherited fixture. No worker consumes a real DMS collector. Cleanup removes
only jobs whose exact request IDs were created by this fixture.
"""
from pathlib import Path
import runpy
import time
import unittest
from unittest.mock import patch

import frappe
from frappe.utils.background_jobs import get_job, get_queue
from pearnly_dms.snapshots import manual, store

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))


class ManualNative(lab["SnapshotLabCase"]):
    def setUp(self):
        super().setUp()
        self.requests = []
        self.key()
        self.baseline = get_queue("dms_sync").get_job_ids()

    def tearDown(self):
        frappe.db.rollback()
        queue = get_queue("dms_sync")
        for request_id in self.requests:
            for step in range(4):
                job = get_job(f"dms-manual-{request_id}-{step}")
                if job:
                    self.assertEqual(job.kwargs["site"], lab["SITE"])
                    self.assertEqual(job.kwargs["method"], "pearnly_dms.snapshots.manual.execute")
                    self.assertEqual(job.kwargs["kwargs"]["request_id"], request_id)
                    self.assertEqual(job.get_status(refresh=True), "queued")
                    queue.remove(job.id)
                    job.delete()
        self.assertEqual(queue.get_job_ids(), self.baseline)
        super().tearDown()

    def request(self, dataset="vehicles"):
        value = manual.request(self.endpoint, dataset)
        self.requests.append(value["request_id"])
        return value["request_id"]

    def execute(self, request_id, value, *, step=0, dataset="vehicles"):
        frappe.db.rollback()
        with patch.object(frappe, "get_attr", return_value=lambda *a, **kw: value) as callback:
            result = manual.execute(self.endpoint, dataset, "authority", request_id, step=step)
        frappe.db.rollback()
        return result, callback

    def test_status_never_enqueues_and_cold_is_not_empty_success(self):
        with patch.object(frappe, "enqueue", side_effect=AssertionError("Status cannot enqueue")):
            result = manual.status(self.endpoint, "vehicles")
        self.assertEqual(result["state"], "idle")
        self.assertIsNone(result["count"])
        self.assertEqual(result["as_of"], 0)

    def test_retired_masters_never_registers_or_enqueues(self):
        from pearnly_dms.adapter import snapshot_sync
        with patch.object(frappe, "enqueue", side_effect=AssertionError("Retired group cannot enqueue")):
            for call in (manual.request, manual.status):
                with self.subTest(call=call.__name__), self.assertRaises(ValueError):
                    call(self.endpoint, "masters")
            self.assertEqual(snapshot_sync.refresh(self.endpoint, dataset="masters", request_id="obsolete"),
                             {"ok": False, "retired": True})
        self.assertEqual(get_queue("dms_sync").get_job_ids(), self.baseline)
        self.assertFalse(frappe.db.exists(store.DT, {"scope_key": self.key(), "dataset": "masters"}))

    def test_explicit_request_is_deduplicated_and_survives_rpc_rollback(self):
        request_id = self.request()
        job = get_job(f"dms-manual-{request_id}-0")
        self.assertIsNotNone(job)
        self.assertEqual(job.timeout, 420)
        frappe.db.rollback()
        self.assertEqual(manual.status(self.endpoint, "vehicles")["request_id"], request_id)
        self.assertEqual(manual.request(self.endpoint, "vehicles")["request_id"], request_id)
        self.assertEqual(len(get_queue("dms_sync").get_job_ids()), len(self.baseline) + 1)

    def test_enqueue_failure_is_not_left_queued_forever(self):
        with patch.object(frappe, "enqueue", side_effect=RuntimeError("Synthetic queue failure")):
            with self.assertRaises(RuntimeError):
                manual.request(self.endpoint, "vehicles")
        frappe.db.rollback()
        self.assertEqual(manual.status(self.endpoint, "vehicles")["state"], "failed")

    def test_continuation_is_part_of_same_manual_request_not_recurring_scan(self):
        request_id = self.request()
        result, _ = self.execute(request_id, {"ok": True, "complete": False, "processed": 20, "total": 50})
        self.assertEqual(result["state"], "queued")
        self.assertIsNotNone(get_job(f"dms-manual-{request_id}-1"))
        stale, callback = self.execute(request_id, {"ok": True}, step=0)
        self.assertTrue(stale["obsolete"])
        callback.assert_not_called()
        self.execute(request_id, {"ok": True, "count": 50}, step=1)
        value = manual.status(self.endpoint, "vehicles")
        self.assertEqual(value["state"], "succeeded")
        self.assertEqual(value["count"], 50)
        self.assertIsNone(get_job(f"dms-manual-{request_id}-2"))

    def test_a_busy_session_is_retried_instead_of_parked(self):
        # 2026-09-25 用户报的就是这条：`waiting` 不会被任何东西重排，操作员录入之后的
        # 改动再也回不到缓存，夜间任务还把 `waiting` 当成"正在跑"而跳过。
        # 现在忙碌就再排一次（每次尝试自带一次有界等待），次数用完才明确失败。
        request_id = self.request()

        self.execute(request_id, {"ok": False, "busy": True, "error_code": "ERR_DMS_SNAPSHOT_BUSY"})

        status = manual.status(self.endpoint, "vehicles")
        self.assertEqual(status["state"], "queued", "忙碌必须重排，不能停在 waiting")
        self.assertEqual(status["request_id"], request_id)
        self.assertIsNotNone(get_job(f"dms-manual-{request_id}-1"))
        self.assertEqual(status["error_code"], "",
                         "还在等账号时不该让操作员看见一个并不存在的失败")

    def test_a_busy_session_gives_up_loudly_after_the_budget(self):
        request_id = self.request()
        # 第一次尝试 + BUSY_ATTEMPTS 次重排 = 把预算用完
        for step in range(manual.BUSY_ATTEMPTS + 1):
            self.execute(request_id, {"ok": False, "busy": True,
                                      "error_code": "ERR_DMS_SNAPSHOT_BUSY"}, step=step)

        status = manual.status(self.endpoint, "vehicles")
        self.assertEqual(status["state"], "failed", "用完次数必须明确失败，好让夜间任务接手")
        self.assertEqual(status["error_code"], "ERR_DMS_SNAPSHOT_BUSY")
        self.assertIsNone(get_job(f"dms-manual-{request_id}-{manual.BUSY_ATTEMPTS + 1}"))

    def test_old_job_replay_cannot_change_success_or_queued(self):
        for value, expected in (({"ok": True, "count": 12}, "succeeded"),
                                ({"ok": False, "busy": True}, "queued")):
            request_id = self.request()
            self.execute(request_id, value)
            before = manual.status(self.endpoint, "vehicles")
            replay, callback = self.execute(request_id, {"ok": False})
            self.assertFalse(replay["ok"])
            callback.assert_not_called()
            self.assertEqual(manual.status(self.endpoint, "vehicles"), before)
            self.assertEqual(before["state"], expected)

    def test_publish_fence_rejects_replaced_token_and_preserves_payload(self):
        data = {"masters": {}, "paints": {}, "catalogues": {"fixture": {}}}
        self.publish(data, dataset="vehicles")
        frappe.db.commit()
        request_id = self.request()
        manual._update(self.endpoint, "vehicles", "authority", request_id, {"state": "failed"})
        self.request()
        frappe.db.rollback()
        with self.assertRaises(store.SnapshotUnavailable):
            with manual.publish_guard(self.endpoint, "vehicles", request_id):
                self.fail("Obsolete collector entered publication")
        frappe.db.rollback()
        self.assertEqual(store.read(self.endpoint, "vehicles")["data"], data)

    def test_new_request_obsoletes_old_and_revoked_actor_does_no_io(self):
        request_id = self.request()
        manual._update(self.endpoint, "vehicles", "authority", request_id, {"state": "failed"})
        replacement = self.request()
        self.assertNotEqual(replacement, request_id)
        old, callback = self.execute(request_id, {"ok": True})
        self.assertFalse(old["ok"])
        callback.assert_not_called()
        # The persisted actor is checked again, even if the worker uses Administrator.
        manual._update(self.endpoint, "vehicles", "authority", replacement, {"actor": self.users[0]})
        new, callback = self.execute(replacement, {"ok": True})
        self.assertFalse(new["ok"])
        callback.assert_not_called()

    def test_known_auth_denial_blocks_other_dataset_until_verified_collection(self):
        data = {"masters": {}, "paints": {}, "catalogues": {"fixture": {}}}
        self.publish(data, dataset="vehicles")
        self.publish(dataset="customers")
        store.failed(self.endpoint, "geo", "ERR_DMS_AUTH")
        self.unavailable("ERR_DMS_AUTH", store.read, self.endpoint, "vehicles")
        self.unavailable("ERR_DMS_AUTH", store.read, self.endpoint, "customers")
        store.failed(self.endpoint, "customers", "ERR_DMS_UNREACHABLE")
        self.unavailable("ERR_DMS_AUTH", store.read, self.endpoint, "vehicles")
        self.publish(data, dataset="vehicles", start=time.time())
        self.assertTrue(store.read(self.endpoint, "customers")["stale"])

    def test_invalid_customer_or_geo_checkpoint_is_discarded_not_good_generation(self):
        from pearnly_dms.adapter import snapshot_sync, geo_sync
        for dataset, collector, data in (("customers", snapshot_sync, lab["directory"]()),
                                         ("geo", geo_sync, lab["geo"]())):
            self.publish(data, dataset=dataset)
            store.checkpoint(self.endpoint, {"started_at": time.time(), "tree": {"invalid": True}},
                             dataset=dataset, expected_scope=self.key())
            frappe.db.commit()
            before = store.read(self.endpoint, dataset)
            request_id = self.request(dataset)
            frappe.db.rollback()
            with patch.object(collector, "run_logged_in", return_value={
                    "ok": False, "error_code": "ERR_DMS_SNAPSHOT_INCOMPLETE"}):
                result = manual.execute(self.endpoint, dataset, "authority", request_id)
            self.assertFalse(result["ok"])
            frappe.db.rollback()
            self.assertIsNone(store.stage(self.endpoint, dataset))
            after = store.read(self.endpoint, dataset)
            self.assertEqual(after["generation"], before["generation"])
            self.assertEqual(after["data"], before["data"])

    def test_foreground_marker_prevents_background_and_cleanup_cannot_reverse_write(self):
        from pearnly_dms.adapter import read_priority
        accounts = [self.uid + "-priority"]
        read_priority.yield_to_foreground(accounts)
        with read_priority.foreground(accounts):
            with self.assertRaises(read_priority.ReadDeferred):
                read_priority.yield_to_foreground(accounts)
        read_priority.yield_to_foreground(accounts)
        fake = unittest.mock.Mock()
        fake.zrem.side_effect = RuntimeError("synthetic cleanup failure")
        with patch.object(frappe, "cache", return_value=fake):
            with read_priority.foreground(accounts):
                verified = True
        self.assertTrue(verified)


if __name__ == "__main__":
    unittest.main(verbosity=2)
