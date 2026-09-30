"""3.1-flash-lite 被谷歌限流时立刻改用 3.8-flash（用户 2026-10-01）。离线：不连站点、不调 API。"""
import unittest
from unittest.mock import patch

from pearnly_core import ocr
from pearnly_core.execution import work
from pearnly_core.execution.errors import Deferred

LITE, FULL = "gemini-3.1-flash-lite", "gemini-3.8-flash"
PAYLOAD = {"contents": [], "generationConfig": {"temperature": 0, "thinkingConfig": {"thinkingLevel": "MINIMAL"}}}


class LiteFallback(unittest.TestCase):
    def run_post(self, answers, saved=None):
        calls = []

        def post(url, headers, payload, timeout_s, model, wait=True):
            calls.append((model, wait, payload["generationConfig"].get("thinkingConfig")))
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(ocr, "backend", return_value="vertex"), \
                patch.object(ocr, "vertex_url", side_effect=lambda model: "https://x/" + model), \
                patch.object(ocr, "_post_with_backoff", side_effect=post), \
                patch.object(work, "checkpoint", return_value=saved), \
                patch.object(ocr.frappe, "logger", create=True):
            result = ocr._post_with_lite_fallback("https://x/" + LITE, {}, PAYLOAD, 60, LITE)
        return result, calls

    def test_a_google_rate_limit_reads_the_page_with_the_bigger_model_at_once(self):
        result, calls = self.run_post([Deferred("provider_429", 12), "ok"])
        self.assertEqual(result, "ok")
        self.assertEqual(calls, [(LITE, False, {"thinkingLevel": "MINIMAL"}), (FULL, True, {"thinkingLevel": "LOW"})])

    def test_our_own_budget_still_waits_on_the_same_model(self):
        result, calls = self.run_post([Deferred("capacity", 2), "ok"])
        self.assertEqual([(model, wait) for model, wait, _t in calls], [(LITE, False), (LITE, True)])

    def test_a_page_already_read_by_the_bigger_model_is_taken_back(self):
        result, calls = self.run_post(["ok"], saved={"state": "done"})
        self.assertEqual([model for model, _w, _t in calls], [FULL])

    def test_other_models_are_untouched(self):
        with patch.object(ocr, "backend", return_value="vertex"), \
                patch.object(ocr, "_post_with_backoff", return_value="ok") as post:
            ocr._post_with_lite_fallback("u", {}, PAYLOAD, 60, FULL)
        post.assert_called_once_with("u", {}, PAYLOAD, 60, FULL)


if __name__ == "__main__":
    unittest.main()
