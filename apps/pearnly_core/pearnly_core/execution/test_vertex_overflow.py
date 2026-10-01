"""DMS / ERP 的 Gemini 调用：全球入口被谷歌拒绝时这一次改发美国多区域入口（用户 2026-10-01 同意）。离线：不连站点、不调 API。"""
import unittest
from unittest.mock import patch

import frappe

from pearnly_core import ocr
from pearnly_core.execution import budget, provider
from pearnly_core.execution.errors import Deferred


class Overflow(unittest.TestCase):
    def setUp(self):
        frappe.local.conf = frappe._dict(pearnly_ocr_backend="vertex", pearnly_ocr_project="p")
        self.addCleanup(lambda: delattr(frappe.local, "conf"))
        scope = patch.object(budget, "scope", side_effect=lambda *args: "|".join(args))
        scope.start()
        self.addCleanup(scope.stop)

    def post(self, *outcomes, model="gemini-3.1-flash-lite"):
        calls, queue = [], list(outcomes)

        def fake(url, headers, payload, timeout_s, model_name, bucket, **kwargs):
            calls.append((url, bucket, kwargs))
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        with patch.object(provider, "post", side_effect=fake):
            try:
                result = ocr._post_with_backoff(ocr.vertex_url(model), {}, {"x": 1}, 30, model)
            except Deferred as error:
                result = error
        return result, calls

    def test_entry_points_and_hosts(self):
        self.assertEqual(ocr.vertex_host("global"), "aiplatform.googleapis.com")
        self.assertEqual(ocr.vertex_host("us"), "aiplatform.us.rep.googleapis.com")
        self.assertEqual(ocr.vertex_host("asia-southeast1"), "asia-southeast1-aiplatform.googleapis.com")
        self.assertIn("aiplatform.us.rep.googleapis.com/v1/projects/p/locations/us/publishers/google/models/gemini-3.8-flash",
                      ocr.vertex_url("gemini-3.8-flash", "us"))

    def test_a_google_rejection_on_global_goes_to_the_us_entry_once(self):
        result, calls = self.post(Deferred("provider_429", 6), "ok")
        self.assertEqual(result, "ok")
        self.assertIn("//aiplatform.googleapis.com/", calls[0][0])
        self.assertEqual(calls[0][2]["hand_back"], ("provider_",))
        self.assertTrue(calls[0][2]["shared_quota"])
        self.assertIn("//aiplatform.us.rep.googleapis.com/", calls[1][0])
        self.assertEqual(calls[1][1], "vertex|p|us|gemini-3.1-flash-lite")
        self.assertNotIn("hand_back", calls[1][2])     # 美国入口再被拒就照原规则退避，不再转

    def test_an_unknown_outcome_on_the_us_entry_is_resent_once_under_a_new_checkpoint(self):
        from pearnly_core.execution import work
        from pearnly_core.execution.errors import Uncertain
        with patch.object(work, "checkpoint") as checkpoint:
            result, calls = self.post(Deferred("provider_429", 6), Uncertain("read timed out"), "ok")
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        self.assertIn("//aiplatform.us.rep.googleapis.com/", calls[2][0])
        checkpoint.assert_called_once()
        self.assertEqual(checkpoint.call_args.args[1], "rejected")
        self.assertTrue(checkpoint.call_args.args[2]["outcome_unknown"])

    def test_own_capacity_waits_and_off_switch_and_regional_models_do_not_overflow(self):
        result, calls = self.post(Deferred("capacity", 2))
        self.assertIsInstance(result, Deferred)
        self.assertEqual(len(calls), 1)
        frappe.local.conf.pearnly_ocr_vertex_overflow = "off"
        result, calls = self.post(Deferred("provider_429", 6))
        self.assertEqual((len(calls), calls[0][2]), (1, {"shared_quota": True}))
        del frappe.local.conf["pearnly_ocr_vertex_overflow"]
        result, calls = self.post("ok", model="gemini-3.5-flash")  # 就近区域的模型：没有备用入口
        self.assertEqual((len(calls), calls[0][2]), (1, {"shared_quota": True}))


if __name__ == "__main__":
    unittest.main()
