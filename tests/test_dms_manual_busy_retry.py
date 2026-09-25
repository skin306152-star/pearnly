"""A busy upstream session must be retried with a delay, never parked.

2026-09-25 lesson, reproduced against the live tenant: the operator's own action
holds the single shared DMS session, so the refresh registered by the catch-up
always loses the lock. The old code wrote `waiting` and stopped -- and nothing
re-queues a `waiting` request, so the cache stopped updating for good while the
nightly job kept skipping that dataset as "already running".
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))


class ManualBusyRetryTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.db = types.SimpleNamespace(commit=Mock(), rollback=Mock())
        self.frappe.get_attr = Mock()
        self.store = types.ModuleType("pearnly_dms.snapshots.store")
        self.store.DT = "DMS Read Snapshot"
        self.store.SnapshotUnavailable = type("SnapshotUnavailable", (Exception,), {})

        self.modules = patch.dict(sys.modules, {
            "frappe": self.frappe,
            "pearnly_dms.snapshots.store": self.store,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules.pop("pearnly_dms.snapshots.manual", None)
        self.manual = importlib.import_module("pearnly_dms.snapshots.manual")

        self.req = {"id": "req-1", "state": "queued", "retries": 0,
                    "busy_waits": 0, "params": {}}
        self.manual.assert_active = Mock(side_effect=lambda *a, **k: dict(self.req))
        self.manual._collector = Mock(return_value={"method": "pearnly_dms.x.collect"})
        self.manual._update = Mock(return_value=True)
        self.manual._enqueue = Mock()

    def _execute(self, result, *, busy_waits=0):
        self.req = {**self.req, "busy_waits": busy_waits}
        self.frappe.get_attr = Mock(return_value=Mock(return_value=result))
        return self.manual.execute("EP-00001", "vehicles", "authority", "req-1", 0)

    def _values(self):
        return self.manual._update.call_args.args[4]

    def test_busy_schedules_another_attempt_with_a_delay(self):
        out = self._execute({"ok": False, "busy": True, "error_code": "ERR_DMS_SNAPSHOT_BUSY"})

        self.assertEqual(out, {"ok": True, "state": "queued"})
        self.assertEqual(self.manual._enqueue.call_args.kwargs["countdown"],
                         self.manual.BUSY_DELAYS[0])
        self.assertEqual(self._values()["state"], "queued")
        self.assertEqual(self._values()["busy_waits"], 1)
        self.assertEqual(self._values()["error_code"], "",
                         "还在等账号时不该让操作员看见一个并不存在的失败")

    def test_each_busy_attempt_waits_longer(self):
        self._execute({"ok": False, "busy": True}, busy_waits=2)
        self.assertEqual(self.manual._enqueue.call_args.kwargs["countdown"],
                         self.manual.BUSY_DELAYS[2])

    def test_giving_up_fails_loudly_so_the_nightly_can_pick_it_up(self):
        out = self._execute({"ok": False, "busy": True, "error_code": "ERR_DMS_SNAPSHOT_BUSY"},
                            busy_waits=len(self.manual.BUSY_DELAYS))

        self.assertEqual(out, {"ok": False, "state": "failed"})
        self.assertEqual(self._values()["state"], "failed")
        self.assertEqual(self._values()["error_code"], "ERR_DMS_SNAPSHOT_BUSY")
        self.manual._enqueue.assert_not_called()

    def test_a_transport_retry_still_re_enqueues_without_a_delay(self):
        out = self._execute({"ok": False, "error_code": "ERR_DMS_SNAPSHOT_RETRY"})

        self.assertEqual(out, {"ok": True, "state": "queued"})
        self.assertEqual(self.manual._enqueue.call_args.kwargs["countdown"], 0)
        self.assertEqual(self._values()["retries"], 1)


if __name__ == "__main__":
    unittest.main()
