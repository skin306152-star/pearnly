"""多页识别的限流（2026-09-27 压测）：后台任务里短的等待原地等、不整份让出；检查点每次运行只读一次。

离线跑：预算、HTTP、检查点库都用替身，不连站点、不连 Redis、不调 API。
"""
import json
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core.execution import provider, work
from pearnly_core.execution.errors import Deferred


def ok_response():
    response = MagicMock(status_code=200, headers={})
    response.iter_content.return_value = [json.dumps({"ok": True}).encode()]
    response.json.return_value = {"ok": True}
    return response


def fresh_flags(case):
    frappe.local.flags = frappe._dict()
    case.addCleanup(lambda: setattr(frappe.local, "flags", frappe._dict()))


class ProviderWait(unittest.TestCase):
    def setUp(self):
        fresh_flags(self)
        self.saved = {}
        patches = [
            patch.object(work, "checkpoint", side_effect=self.checkpoint),
            patch.object(provider.budget, "feedback"),
            patch.object(provider.time, "sleep"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.sleep = provider.time.sleep
        frappe.flags.pearnly_ocr_work = "work-1"

    def checkpoint(self, key, state=None, body=None):
        if state is None:
            return self.saved.get(key)
        self.saved[key] = {"state": state, "body": body}
        return self.saved[key]

    def permits(self, *delays):
        """前几次拿额度被拒（给出的等待秒数），之后放行。"""
        refusals = list(delays)

        @contextmanager
        def permit(bucket, timeout):
            if refusals:
                raise Deferred("capacity", refusals.pop(0))
            yield
        return patch.object(provider.budget, "permit", side_effect=permit)

    def call(self):
        return provider.post("https://example.invalid", {}, {"x": 1}, 30, "m", "bucket")

    def test_short_waits_are_waited_out_inside_the_job(self):
        with self.permits(1, 2.5, 1), patch.object(provider, "_http_post", return_value=ok_response()) as http:
            self.call()
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1, 2.5, 1])
        self.assertEqual(http.call_count, 1)

    def test_a_web_request_still_yields_at_once(self):
        frappe.flags.pearnly_ocr_work = None
        with self.permits(1), self.assertRaises(Deferred):
            self.call()
        self.sleep.assert_not_called()

    def test_long_or_accumulated_waits_yield(self):
        with self.permits(25), self.assertRaises(Deferred):
            self.call()
        with self.permits(*([10] * 7)), self.assertRaises(Deferred):
            self.call()
        self.assertEqual(sum(c.args[0] for c in self.sleep.call_args_list), 60)

    def test_a_429_is_retried_after_its_cooldown(self):
        busy = MagicMock(status_code=429, headers={"Retry-After": "3"})
        with self.permits(), patch.object(provider, "_http_post", side_effect=[busy, ok_response()]) as http, \
                patch.object(provider.random, "uniform", return_value=0):
            self.assertEqual(self.call().status_code, 200)
        self.assertEqual(http.call_count, 2)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [5])


class CheckpointCache(unittest.TestCase):
    def setUp(self):
        fresh_flags(self)

    def test_one_read_per_run_and_a_fresh_read_next_run(self):
        store = {"checkpoints": json.dumps({"a": {"state": "done", "body": 1}})}
        db = frappe.local.db = MagicMock()
        self.addCleanup(lambda: setattr(frappe.local, "db", None))
        db.get_value.side_effect = lambda *a, **k: store["checkpoints"]
        db.set_value.side_effect = lambda dt, name, field, value, **k: store.update(checkpoints=value)
        with work.context("work-1"):
            for _ in range(5):
                self.assertEqual(work.checkpoint("a")["body"], 1)
            work.checkpoint("b", "done", 2)
            self.assertEqual(work.checkpoint("b")["body"], 2)
        self.assertEqual(db.get_value.call_count, 1)
        self.assertEqual(json.loads(store["checkpoints"])["b"]["body"], 2)
        with work.context("work-1"):
            work.checkpoint("a")
        self.assertEqual(db.get_value.call_count, 2)


if __name__ == "__main__":
    unittest.main()
