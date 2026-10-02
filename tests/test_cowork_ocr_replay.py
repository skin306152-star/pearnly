"""Cowork 录入的回放识别（ocr/execution/replay.py）。离线：不连站点、不连 Redis、不调 API；检查点与库用替身。"""
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_core.execution import replay as core_replay
from pearnly_core.execution.errors import Deferred
from pearnly_cowork.ocr.execution import replay
from pearnly_cowork.ocr.providers.client import ProviderError

PIPELINE = json.dumps({"engine": "invoice", "pages": []})
EVIDENCE = json.dumps([{"checkpoint_key": "k1", "stage": "layer1", "model": "gemini-3.1-flash-lite"}])


class Replay(unittest.TestCase):
    def setUp(self):
        self.points, self.cached, self.queries = {}, {}, []
        self.clock = [1000.0]
        self.doc = SimpleNamespace(name="B-new", page_count=2)
        self.source = SimpleNamespace(sha256="a" * 64)
        self.rows = {"B-old": frappe._dict(result_json=PIPELINE, provider_evidence=EVIDENCE)}
        self.matches = [("B-old",)]

        def checkpoint(key, state=None, body=None):
            if state is None:
                return self.points.get(key)
            self.points[key] = {"state": state, "body": body}
            return self.points[key]

        def sql(query, values):
            self.queries.append((query, values))
            return self.matches

        cache = SimpleNamespace(get_value=self.cached.get,
                                set_value=lambda key, value, expires_in_sec=None: self.cached.__setitem__(key, value))
        db = SimpleNamespace(sql=sql, get_value=lambda doctype, name, fields, as_dict=False: self.rows.get(name))
        for target in (mock.patch.object(replay.core_work, "checkpoint", side_effect=checkpoint),
                       mock.patch.object(replay.frappe, "db", db, create=True),
                       mock.patch.object(replay.frappe, "cache", cache, create=True),
                       mock.patch.object(core_replay, "settings", return_value={"median": 5.8, "p95": 19.7}),
                       mock.patch.object(core_replay, "delay", return_value=7.5)):
            target.start()
            self.addCleanup(target.stop)

    def recognize(self, progress=None):
        return replay.recognize(self.doc, self.source, progress, now=lambda: self.clock[0])

    def test_first_entry_waits_like_a_dispatch_then_returns_the_recorded_real_result(self):
        with self.assertRaises(Deferred) as waiting:
            self.recognize()
        self.assertEqual((waiting.exception.reason, waiting.exception.delay), ("replay_waiting", 7.5))
        self.assertEqual(self.points[replay.KEY], {"state": "external", "body": {"from": "batch:B-old", "due": 1007.5}})
        self.clock[0] = 1004.0
        with self.assertRaises(Deferred) as early:             # 提前被放回队列：等剩下的
            self.recognize()
        self.assertEqual(early.exception.delay, 3.5)
        self.clock[0] = 1008.0
        pages = []
        pipeline, evidence = self.recognize(pages.append)
        self.assertEqual(pipeline, PIPELINE)
        self.assertEqual(evidence, [*json.loads(EVIDENCE), {"replay": "batch:B-old"}])
        self.assertEqual(pages, [2])                            # 多页：读到最后一页

    def test_only_real_recognitions_of_the_same_content_and_page_count_are_used(self):
        with self.assertRaises(Deferred):
            self.recognize()
        query, values = self.queries[0]
        self.assertEqual(values, ("a" * 64, 2, "B-new", '%"replay"%'))
        self.assertIn("NOT LIKE", query)                        # 回放出来的单不当来源
        self.assertIn("phase IN ('saved', 'not_invoice')", query)
        # 同一内容第二次直接用缓存，不再查库
        self.points.clear()
        self.doc = SimpleNamespace(name="B-other", page_count=2)
        with self.assertRaises(Deferred):
            self.recognize()
        self.assertEqual(len(self.queries), 1)

    def test_other_test_tenants_replay_from_the_shared_library(self):
        import tempfile
        self.matches = []                                       # 本租户没有真识别过
        with tempfile.TemporaryDirectory() as sites:
            entry = "a" * 64 + "-2"
            os.makedirs(os.path.join(sites, replay.LIBRARY))
            with open(os.path.join(sites, replay.LIBRARY, entry + ".json"), "w") as handle:
                json.dump({"result_json": PIPELINE, "provider_evidence": EVIDENCE, "site": "testcowork.pearnly.com",
                           "batch": "B-real"}, handle)
            with mock.patch.object(replay.frappe.local, "sites_path", sites, create=True):
                with self.assertRaises(Deferred):
                    self.recognize()
                self.assertEqual(self.points[replay.KEY]["body"]["from"], "library:" + entry)
                self.clock[0] = 2000.0
                pipeline, evidence = self.recognize()
        self.assertEqual(pipeline, PIPELINE)
        self.assertEqual(evidence[-1], {"replay": "library:" + entry})
        self.assertIsNone(replay._load("library:../../etc/passwd"))   # 只认库里的条目名

    def test_content_never_recognized_for_real_fails_without_charging_or_calling_google(self):
        self.matches = []
        with mock.patch.object(replay.frappe.local, "sites_path", "/nonexistent", create=True), \
                self.assertRaises(ProviderError) as refused:
            self.recognize()
        self.assertEqual(refused.exception.code, "ocr.replay_missing")
        self.assertNotIn(replay.KEY, self.points)
        self.assertIsNone(replay.recorded("", 1, "B-new"))

    def test_a_started_replay_finishes_as_a_replay_after_the_switch_goes_off(self):
        with mock.patch.object(core_replay, "active", return_value=False):
            self.assertFalse(replay.active())
            with self.assertRaises(Deferred):
                self.recognize()
            self.assertTrue(replay.active())                    # 已开始回放：照样回放完，不转去真识别



class RecognizeBranch(unittest.TestCase):
    """开着回放时 ``workflow._recognize`` 取回放结果、照常校验与存结果；Cloud Run 与本机付费读都不走。"""

    def test_replay_replaces_cloud_run_and_local_reading(self):
        from pearnly_cowork.ocr.execution import workflow

        doc = SimpleNamespace(name="B-new", page_count=1, anchor_date="2026-10-02", work_name="W1")
        source = SimpleNamespace(kind="image", sha256="a" * 64, filename="x.jpg")
        frozen = {"environment": {}, "policy": {}, "billing_tier": "standard", "input_engine": ""}
        frozen_calls = []
        with mock.patch.object(workflow.replay, "active", return_value=True), \
                mock.patch.object(workflow.replay, "recognize", return_value=(PIPELINE, [{"replay": "B-old"}])) as replayed, \
                mock.patch.object(workflow.forward, "recognize", side_effect=AssertionError("不应交给 Cloud Run")), \
                mock.patch.object(workflow.forward, "settings", return_value=None), \
                mock.patch.object(workflow, "read_bytes", side_effect=AssertionError("回放不读原件字节")), \
                mock.patch.object(workflow, "run_invoice", side_effect=AssertionError("不应在本机付费读")), \
                mock.patch.object(workflow.service, "billing_basis", return_value=("image", 1)), \
                mock.patch.object(workflow.service, "balance"), \
                mock.patch.object(workflow, "stored_original", return_value=None), \
                mock.patch.object(workflow, "preview_spec", return_value=None), \
                mock.patch.object(workflow.PipelineResult, "model_validate_json"), \
                mock.patch.object(workflow, "_freeze_result",
                                  side_effect=lambda d, result, f, e, remote=None: frozen_calls.append(remote) or d):
            workflow._recognize(doc, frozen, None, source)
        replayed.assert_called_once()
        self.assertEqual(frozen_calls, [(PIPELINE, [{"replay": "B-old"}])])


if __name__ == "__main__":
    unittest.main()
