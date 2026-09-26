"""Zero-I/O tests for the nightly full re-read (the safety net behind the incremental sync).

假 frappe / 假 manual / 假 pacing。断言的是**判断规则**：没有连接就不动、有人刚用过就让开、
3:00 那一枪不认"够新"一律重读（只让开正在读的组）、4:30 补读只补今晚没读成的组、
两枪是不同的函数（Frappe 按方法名登记，同名会互相覆盖），以及水位基线必须**先于任何一组
入队**（顺序反了会把读取期间的改动整段跳过）。
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))


class NightlyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        # 入队与登记读取共用一条时间线，才能断言真实的入队先后。
        self.timeline = []
        self.frappe.enqueue = Mock(side_effect=lambda method, **kw: self.timeline.append(method))
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
            self.timeline.append(f"request:{ds}")
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

    def read_ago(self, dataset, seconds):
        self.statuses[dataset] = {"state": "succeeded", "as_of": time.time() - seconds}

    def fresh(self, dataset):
        self.read_ago(dataset, 60)

    def stale(self, dataset):
        self.read_ago(dataset, 30 * 3600)

    def test_no_connection_means_nothing_happens(self):
        self.endpoint_rows = []
        for entry in (self.job.run, self.job.retry):
            self.assertEqual(entry(), {"skipped": "no-connection"})
        self.manual.request.assert_not_called()
        self.frappe.enqueue.assert_not_called()

    def test_two_connections_is_not_a_primary_and_is_left_alone(self):
        self.endpoint_rows = ["A", "B"]
        self.assertEqual(self.job.run(), {"skipped": "no-connection"})
        self.manual.request.assert_not_called()

    def test_an_operator_using_the_account_makes_it_stand_down(self):
        self.pacing.used_recently = Mock(return_value=True)
        for entry in (self.job.run, self.job.retry):
            self.assertEqual(entry(), {"skipped": "in-use-by-operator"})
        self.manual.request.assert_not_called()
        self.frappe.enqueue.assert_not_called()
        self.assertEqual(self.pacing.used_recently.call_args.kwargs["within"],
                         self.job.PEOPLE_WINDOW_SECONDS)

    def test_the_first_shot_rereads_every_group_even_if_it_was_just_read(self):
        # 2026-09-26 线上：客户组前一天 19:06 手动读过，夜间按"20 小时内读过"跳过了它。
        for dataset in self.job.READ_ORDER:
            self.fresh(dataset)

        result = self.job.run()

        self.assertEqual(result["requested"], list(self.job.READ_ORDER))
        self.assertEqual([dataset for dataset, _ in self.requests], list(self.job.READ_ORDER))
        self.assertTrue(all(kw["reader"] == "authority" for _, kw in self.requests))
        self.assertTrue(all(kw["actor"] == self.job.SERVICE_ACTOR for _, kw in self.requests))
        self.assertEqual(result["detail"], {})

    def test_the_retry_only_rereads_groups_not_read_tonight(self):
        self.read_ago("vehicles", 80 * 60)       # 第一枪 3:00 读成，补读 4:30 时 90 分钟内
        self.read_ago("customers", 9 * 3600)     # 前一天傍晚手动读的：不算今晚
        self.read_ago("company", 25 * 3600)      # 第一枪失败，还是上一代
        self.statuses["geo"] = {"state": "idle"}  # 从没读成过

        result = self.job.retry()

        self.assertEqual(result["requested"], ["customers", "company", "geo"])
        self.assertEqual(result["detail"], {"vehicles": "read-tonight"})
        self.assertGreater(self.job.RETRY_FRESH_SECONDS, 90 * 60, "补读窗口必须覆盖 3:00 → 4:30")
        self.assertLess(self.job.RETRY_FRESH_SECONDS, 9 * 3600)

    def test_the_retry_does_nothing_when_tonight_already_succeeded(self):
        for dataset in self.job.READ_ORDER:
            self.read_ago(dataset, 80 * 60)
        result = self.job.retry()
        self.assertEqual(result["skipped"], "nothing-to-do")
        self.assertEqual(self.timeline, [], "不用重读就不该动水位（seed 只记位置、不补数据）")

    def test_a_group_already_reading_is_not_queued_twice(self):
        for dataset in self.job.READ_ORDER:
            self.stale(dataset)
        self.statuses["company"] = {"state": "running"}
        result = self.job.run()
        self.assertEqual(result["detail"], {"company": "already-running"})
        self.assertNotIn("company", [dataset for dataset, _ in self.requests])

    def test_a_request_that_stopped_running_is_not_treated_as_in_flight(self):
        # 2026-09-25 实测：busy 会把请求写成 `waiting`，而那时**没有任何东西会重排它**。
        # 这里再把它当"正在跑"永久跳过，这一组就再也回读不了。所以只看它是否还活着。
        for dataset in self.job.READ_ORDER:
            self.read_ago(dataset, 60 * 60)
        self.statuses["company"] = {"state": "waiting", "updated_at": time.time() - 3600,
                                    "as_of": time.time() - 30 * 3600}

        result = self.job.retry()

        self.assertIn("company", result["requested"], "停了一个小时的请求必须重新登记")
        self.assertEqual([dataset for dataset, _ in self.requests], ["company"])

    def test_a_still_running_request_keeps_its_place(self):
        for dataset in self.job.READ_ORDER:
            self.read_ago(dataset, 60 * 60)
        self.statuses["company"] = {"state": "running", "updated_at": time.time() - 30}

        for entry in (self.job.run, self.job.retry):
            self.requests.clear()
            self.assertEqual(entry()["detail"]["company"], "already-running")
            self.assertNotIn("company", [dataset for dataset, _ in self.requests])

    def test_everything_already_reading_does_nothing_at_all(self):
        for dataset in self.job.READ_ORDER:
            self.statuses[dataset] = {"state": "running", "updated_at": time.time() - 30}
        result = self.job.run()
        self.assertEqual(result["skipped"], "nothing-to-do")
        self.assertEqual(self.timeline, [], "不用重读就不该动水位（seed 只记位置、不补数据）")

    def test_the_watermark_baseline_is_queued_before_any_group_read(self):
        # 2026-09-26 线上日志：三组第一步 04:48:07 就开读，seed 到 04:49:09 才跑。
        # `manual.request` 是当场入队的，所以 seed 必须在第一次 request 之前入队。
        for entry in (self.job.run, self.job.retry):
            for dataset in self.job.READ_ORDER:
                self.stale(dataset)
            self.timeline.clear()
            self.frappe.enqueue.reset_mock()
            entry()
            self.assertEqual(self.timeline[0], "pearnly_dms.adapter.change_sync.seed", self.timeline)
            self.assertEqual(self.timeline[1:], [f"request:{ds}" for ds in self.job.READ_ORDER])
            self.assertEqual(self.frappe.enqueue.call_count, 1)
            self.assertEqual(self.frappe.enqueue.call_args.kwargs["queue"], self.manual.QUEUE)
            self.assertEqual(self.frappe.enqueue.call_args.kwargs["actor"], self.job.SERVICE_ACTOR)
            self.assertIs(self.frappe.enqueue.call_args.kwargs["enqueue_after_commit"], False)

    def test_the_two_shots_are_distinct_functions_registered_at_3_and_4_30(self):
        # Frappe 以方法名去重 `Scheduled Job Type`：同一函数挂两条 cron 只留后一条。
        import ast

        hooks = ROOT / "apps/pearnly_dms/pearnly_dms/hooks.py"
        tree = ast.parse(hooks.read_text(encoding="utf-8"))
        cron = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events":
                events = node.value.values[0]
                for key, value in zip(events.keys, events.values):
                    cron[key.value] = [item.value for item in value.elts
                                       if isinstance(item, ast.Constant)]
        self.assertEqual(cron.get("0 3 * * *"), ["pearnly_dms.adapter.nightly_refresh.run"])
        self.assertEqual(cron.get("30 4 * * *"), ["pearnly_dms.adapter.nightly_refresh.retry"])
        self.assertIsNot(self.job.run, self.job.retry)

    def test_it_never_logs_in_and_only_uses_the_registered_collectors(self):
        for dataset in self.job.READ_ORDER:
            self.stale(dataset)
        self.job.run()
        for dataset, kwargs in self.requests:
            self.assertIn(dataset, self.job.READ_ORDER)
            self.assertEqual(set(kwargs), {"reader", "actor"})


if __name__ == "__main__":
    unittest.main()
