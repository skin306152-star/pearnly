"""Cowork 识别：3.1-flash-lite 被谷歌限流时这一页立刻改用 3.8-flash（用户 2026-10-01）。离线，不连站点、不调 API。"""
import json
import unittest

from pearnly_core.execution.errors import Deferred
from pearnly_cowork.ocr.engine.contracts import ModelRequest
from pearnly_cowork.ocr.providers.client import ProviderClient


class Response:
    status_code = 200

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}


class LiteFallback(unittest.TestCase):
    def client(self, answers, saved=None):
        self.calls = []
        saved = saved or {}

        def post(**values):
            model = json.loads(values["model"])[4]
            config = values["payload"]["generationConfig"].get("thinkingConfig")
            self.calls.append((model, values.get("wait", True), config))
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return Response()

        return ProviderClient({}, project="p", post=post, checkpoint_key=lambda payload, identity: identity,
            reject_checkpoint=lambda *_a: None, terminal_read=lambda _k: None, terminal_write=lambda *_a: None,
            feedback=lambda *_a: None, scope=lambda *args: "|".join(map(str, args)), access_token=lambda: "FAKE",
            require_context=lambda: None, checkpoint_read=lambda key: saved.get(key))

    def request(self, **values):
        return ModelRequest(stage="direct", page_number=1, attempt=0, model="gemini-3.1-flash-lite", prompt="p",
            images=((b"img", "image/jpeg"),), **values)

    def test_a_google_rate_limit_reads_the_page_with_the_bigger_model(self):
        client = self.client([Deferred("provider_429", 12), "ok"])
        response = client.model_call(self.request())
        self.assertEqual(response.model, "gemini-3.8-flash")
        self.assertEqual(self.calls, [("gemini-3.1-flash-lite", False, {"thinkingLevel": "MINIMAL"}),
                                      ("gemini-3.8-flash", True, {"thinkingLevel": "LOW"})])

    def test_our_own_budget_waits_on_the_same_model(self):
        client = self.client([Deferred("capacity", 2), "ok"])
        self.assertEqual(client.model_call(self.request()).model, "gemini-3.1-flash-lite")
        self.assertEqual([(model, wait) for model, wait, _c in self.calls],
                         [("gemini-3.1-flash-lite", False), ("gemini-3.1-flash-lite", True)])

    def test_a_page_already_read_by_the_bigger_model_is_taken_back(self):
        identity = json.dumps(["cowork", "vertex", "p", "global", "gemini-3.8-flash", "direct", 1, 0])
        client = self.client(["ok"], saved={identity: {"state": "done"}})
        self.assertEqual(client.model_call(self.request()).model, "gemini-3.8-flash")
        self.assertEqual([model for model, _w, _c in self.calls], ["gemini-3.8-flash"])

    def test_a_requested_higher_thinking_level_is_kept(self):
        client = self.client([Deferred("provider_429", 5), "ok"])
        client.model_call(self.request(thinking="MEDIUM"))
        self.assertEqual(self.calls[1][2], {"thinkingLevel": "MEDIUM"})


if __name__ == "__main__":
    unittest.main()
