"""Zero-I/O tests for the nightly full re-read (the safety net behind the incremental sync).

假 frappe / 假 manual / 假 pacing。断言的是**判断规则**：没有连接就不动、有人刚用过就让开、
正在读或今天已经读过的组不再排、四组缺一不可、以及水位基线必须**排在四组之前**
（顺序反了会把读取期间的改动整段跳过）。
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


class NightlyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.enqueue = Mock()
        self.frappe.db = types.SimpleNamespace(commit=Mock())
        self.endpoint_rows = ["PRIMARY-LOCAL"]
        self.frappe.get_all = Mock(side_effect=lambda *a, **k: list(self.endpoint_rows))
        self.manual = types.ModuleType("pearnly_dms.snapshots.manual")
        self.manual.QUEUE = "dms_sync"
        self.statuses = {}
        self.requests = []
        self.manual.status = Mock(side_effect=lambda ep, ds, **kw: dict(self.statuses.get(ds) or {}))

        def request(ep, ds, **kw):
            self.requests.append((ds, kw))
            return {"state": "queued", "request_id": "ab" * 16}

        self.manual.request = Mock(side_effect=request)
        self.pacing = types.ModuleType("pearnly_dms.config.pacing")
        self.pacing.used_recently = Mock(return_value=False)
        self.modules = patch.dict(sys.modules, {
            "frappe": self.frappe,
            "pearnly_dms.snapshots.manual": self.manual,
            "pearnly_dms.config.pacing": self.pacing,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules.pop("pearnly_dms.adapter.nightly_refresh", None)
        self.job = importlib.import_module("pearnly_dms.adapter.nightly_refresh")

    def fresh(self, dataset):
        self.statuses[dataset] = {"state": "succeeded", "as_of": __import__("time").time() - 60}

    def stale(self, dataset):
        self.statuses[dataset] = {"state": "succeeded", "as_of": __import__("time").time() - 30 * 3600}

    def test_no_connection_means_nothing_happens(self):
        self.endpoint_rows = []
        self.assertEqual(self.job.run(), {"skipped": "no-connection"})
        self.manual.request.assert_not_called()
        self.frappe.enqueue.assert_not_called()

    def test_two_connections_is_not_a_primary_and_is_left_alone(self):
        self.endpoint_rows = ["A", "B"]
        self.assertEqual(self.job.run(), {"skipped": "no-connection"})
        self.manual.request.assert_not_called()

    def test_an_operator_using_the_account_makes_it_stand_down(self):
        self.pacing.used_recently = Mock(return_value=True)
        self.assertEqual(self.job.run(), {"skipped": "in-use-by-operator"})
        self.manual.request.assert_not_called()
        self.frappe.enqueue.assert_not_called()
        self.assertEqual(self.pacing.used_recently.call_args.kwargs["within"],
                         self.job.PEOPLE_WINDOW_SECONDS)

    def test_fresh_groups_are_skipped_and_stale_ones_are_requested(self):
        for dataset in self.job.READ_ORDER:
            self.fresh(dataset)
        self.stale("customers")
        self.stale("geo")

        result = self.job.run()

        self.assertEqual(result["requested"], ["customers", "geo"])
        self.assertEqual([dataset for dataset, _ in self.requests], ["customers", "geo"])
        self.assertTrue(all(kw["reader"] == "authority" for _, kw in self.requests))
        self.assertTrue(all(kw["actor"] == self.job.SERVICE_ACTOR for _, kw in self.requests))
        self.assertEqual(result["detail"], {"vehicles": "fresh", "company": "fresh"})

    def test_a_group_already_reading_is_not_queued_twice(self):
        for dataset in self.job.READ_ORDER:
            self.fresh(dataset)
        self.statuses["company"] = {"state": "running"}
        self.assertEqual(self.job.run()["detail"], {"vehicles": "fresh", "customers": "fresh",
                                                    "company": "already-running", "geo": "fresh"})
        self.assertEqual(self.requests, [])

    def test_a_request_that_stopped_running_is_not_treated_as_in_flight(self):
        # 2026-09-25 实测：busy 会把请求写成 `waiting`，而那时**没有任何东西会重排它**。
        # 这里再把它当"正在跑"永久跳过，这一组就再也回读不了。所以只看它是否还活着。
        import time

        for dataset in self.job.READ_ORDER:
            self.fresh(dataset)
        self.statuses["company"] = {"state": "waiting", "updated_at": time.time() - 3600,
                                    "as_of": time.time() - 30 * 3600}

        result = self.job.run()

        self.assertIn("company", result["requested"], "停了一个小时的请求必须重新登记")
        self.assertEqual([dataset for dataset, _ in self.requests], ["company"])

    def test_a_still_running_request_keeps_its_place(self):
        import time

        for dataset in self.job.READ_ORDER:
            self.fresh(dataset)
        self.statuses["company"] = {"state": "running", "updated_at": time.time() - 30}

        self.assertEqual(self.job.run()["detail"]["company"], "already-running")
        self.assertEqual(self.requests, [])

    def test_everything_fresh_does_nothing_at_all(self):
        for dataset in self.job.READ_ORDER:
            self.fresh(dataset)
        result = self.job.run()
        self.assertEqual(result["skipped"], "nothing-to-do")
        self.assertEqual(self.frappe.enqueue.call_count, 0,
                         "不用重读就不该动水位（seed 只记位置、不补数据）")

    def test_the_watermark_baseline_is_queued_before_the_group_reads(self):
        self.stale("vehicles")
        self.frappe.enqueue.reset_mock()
        self.job.run()
        self.assertEqual(self.frappe.enqueue.call_args.args[0],
                         "pearnly_dms.adapter.change_sync.seed")
        self.assertEqual(self.frappe.enqueue.call_args.kwargs["queue"], self.manual.QUEUE)
        self.assertEqual(self.frappe.enqueue.call_args.kwargs["actor"], self.job.SERVICE_ACTOR)
        # 顺序契约：seed 只记"此刻读到哪里"，必须排在四组之前，否则读取期间的改动会被跳过。
        self.assertTrue(self.requests, "至少有一组在被读")

    def test_it_never_logs_in_and_only_uses_the_registered_collectors(self):
        self.stale("vehicles")
        self.job.run()
        for dataset, kwargs in self.requests:
            self.assertIn(dataset, self.job.READ_ORDER)
            self.assertEqual(set(kwargs), {"reader", "actor"})


if __name__ == "__main__":
    unittest.main()
