"""Fake transport only: no credentials, token exchange, Site or network."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_cowork.ocr.engine.contracts import Frame, ModelRequest, ProviderFailure
from pearnly_cowork.ocr.providers.client import ProviderClient, ProviderConfigurationError, ProviderError, vision_page


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.content = b"invalid provider json" if isinstance(body, Exception) else json.dumps(body).encode()

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return deepcopy(self.body)


def key(payload, model):
    return hashlib.sha256((model + "\0" + json.dumps(payload, sort_keys=True)).encode()).hexdigest()


def model_body():
    return {"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True},
             {"text": ' {"invoice_number": "A"} '}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 30, "thoughtsTokenCount": 7},
            "modelVersion": "retained-exact-version"}


class FakeCore:
    """Minimal durable transport behavior; production implementation is Core."""
    def __init__(self):
        self.response = Response(model_body())
        self.calls, self.sent, self.saved, self.rejections, self.feedback = [], [], {}, [], []
        self.terminals = {}
        self.error = None

    def post(self, **values):
        self.calls.append(values)
        if self.error:
            raise self.error
        identity = key(values["payload"], values["model"])
        if identity in self.saved:
            return Response(self.saved[identity])
        self.sent.append(values)
        if self.response.status_code == 200 and isinstance(self.response.body, dict):
            self.saved[identity] = deepcopy(self.response.body)
        return self.response

    def reject(self, identity, body):
        self.saved.pop(identity, None)
        self.rejections.append((identity, body))

    def client(self, **kwargs):
        return ProviderClient(kwargs.pop("environment", {}), project="synthetic-project", post=self.post,
            checkpoint_key=key, reject_checkpoint=self.reject,
            terminal_read=lambda identity: self.terminals.get(identity),
            terminal_write=lambda identity, body: self.terminals.__setitem__(identity, deepcopy(body)),
            feedback=lambda *args: self.feedback.append(args), scope=lambda *args: tuple(args),
            access_token=lambda: "FAKE_TOKEN_NOT_A_CREDENTIAL", require_context=lambda: None, **kwargs)


class ProviderRuntime(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.client = self.core.client()
        self.request = ModelRequest(stage="L2", page_number=1, attempt=0, model="gemini-3.8-flash", prompt="synthetic")

    def test_vertex_payload_usage_raw_and_global_38(self):
        client = self.core.client(environment={"VERTEX_LOCATION": "asia-southeast1", "VERTEX_LOCATION_25": "us-central1"})
        result = client.model_call(replace(self.request, images=((b"synthetic image", "image/png"),)))
        call = self.core.sent[0]
        self.assertIn("https://aiplatform.googleapis.com/v1/projects/synthetic-project/locations/global/", call["url"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer FAKE_TOKEN_NOT_A_CREDENTIAL")
        self.assertEqual(call["payload"]["contents"][0]["role"], "user")
        self.assertEqual(call["payload"]["contents"][0]["parts"][-1], {"text": "synthetic"})
        self.assertEqual(call["payload"]["generationConfig"]["thinkingConfig"], {"thinkingLevel": "LOW"})
        self.assertEqual(result.text, '{"invoice_number": "A"}')
        self.assertEqual((result.input_tokens, result.output_tokens), (20, 37))
        self.assertEqual(result.raw, model_body())
        self.assertEqual(result.model, "gemini-3.8-flash")
        self.assertEqual(call["bucket"], ("vertex", "synthetic-project", "global", "gemini-3.8-flash"))

    def test_exact_stage_page_attempt_identity_and_replay(self):
        first = self.client.model_call(self.request)
        replay = self.client.model_call(self.request)
        self.assertEqual(first.checkpoint_key, replay.checkpoint_key)
        self.assertEqual(len(self.core.sent), 1)
        changed = [self.client.model_call(replace(self.request, **values)).checkpoint_key for values in
                   ({"page_number": 2}, {"stage": "L3"}, {"attempt": 1}, {"model": "gemini-3.1-flash-lite"})]
        self.assertEqual(len(set([first.checkpoint_key] + changed)), 5)
        self.assertEqual(len(self.core.sent), 5)
        self.assertNotIn("stage", self.core.sent[0]["payload"])
        self.assertNotIn("attempt", self.core.sent[0]["payload"])

    def test_structured_thinking_and_plain_text_follow_source(self):
        client = self.core.client(environment={"VERTEX_LOCATION": "asia-southeast1", "VERTEX_LOCATION_25": "us-central1"})
        for model, image, thinking, location in (
            ("gemini-2.5-flash", True, {"thinkingBudget": 0}, "us-central1"),
            ("gemini-3.1-flash-lite", True, {"thinkingLevel": "MINIMAL"}, "us-central1"),
            ("gemini-3.1-flash-lite", False, None, "us-central1"),
            ("gemini-3.5-flash", True, {"thinkingLevel": "MINIMAL"}, "asia-southeast1"),
        ):
            client.model_call(replace(self.request, model=model, images=((b"x", "image/png"),) if image else ()))
            call = self.core.calls[-1]
            self.assertEqual(call["payload"]["generationConfig"].get("thinkingConfig"), thinking)
            self.assertIn("/locations/" + location + "/", call["url"])

    def test_completed_empty_truncated_and_invalid_model_json_are_not_retried_here(self):
        for body in ({}, {"candidates": [{"content": {"parts": [{"text": "{bad"}]}, "finishReason": "MAX_TOKENS"}]}):
            self.core.response = Response(body)
            self.core.saved.clear()
            before = len(self.core.sent)
            result = self.client.model_call(self.request)
            self.assertEqual(result.raw, body)
            self.assertEqual(len(self.core.sent), before + 1)

    def test_core_deferred_and_uncertain_are_same_exception_without_retry(self):
        for error in (Deferred("capacity", 13), Uncertain("transport result unknown")):
            self.core.error = error
            with self.assertRaises(type(error)) as caught:
                self.client.model_call(self.request)
            self.assertIs(caught.exception, error)
        self.assertEqual(len(self.core.calls), 2)

    def test_context_rejection_precedes_token_or_transport(self):
        calls = []
        def reject():
            raise ProviderConfigurationError("ocr.durable_work_required")
        self.client._require_context = reject
        self.client._token = lambda: calls.append("token")
        with self.assertRaisesRegex(ProviderConfigurationError, "durable_work_required"):
            self.client.model_call(self.request)
        self.assertEqual(calls, [])
        self.assertEqual(self.core.calls, [])

    def test_local_configuration_failures_never_authorize_engine_provider_fallback(self):
        self.client._token = lambda: ""
        for request in (self.request, replace(self.request, model="../../bad-model"),
                        replace(self.request, prompt=""), replace(self.request, timeout_s=0)):
            with self.assertRaises(ProviderConfigurationError) as caught:
                self.client.model_call(request)
            self.assertNotIsInstance(caught.exception, ProviderFailure)
        self.assertEqual(self.core.calls, [])

    def test_known_http_auth_quota_and_malformed_envelopes_fail_explicitly(self):
        for response, error in ((Response({}, 403), ProviderError),
            (Response({"error": {"status": "RESOURCE_EXHAUSTED"}}, 403), Deferred),
            (Response(ValueError("invalid json")), ProviderError), (Response([]), ProviderError)):
            self.core.response, self.core.saved, self.core.terminals = response, {}, {}
            with self.assertRaises(error):
                self.client.model_call(self.request)

    def test_completed_refusal_is_replayed_after_later_stage_deferral_without_token_or_http(self):
        for stage, response in (("direct", Response({"error": {"message": "explicit refusal"}}, 400)),
                                ("L3", Response({"error": {"message": "explicit refusal"}}, 500)),
                                ("L3", Response(ValueError("not JSON")))):
            core = FakeCore()
            client = core.client()
            refused = replace(self.request, stage=stage)
            core.response = response
            with self.assertRaises(ProviderError) as original:
                client.model_call(refused)
            core.response = Response(model_body())
            client.model_call(replace(self.request, stage="L2", page_number=1))
            core.error = Deferred("later-page-quota", 7)
            with self.assertRaises(Deferred):
                client.model_call(replace(self.request, stage="L2", page_number=2))
            core.error = None
            before = len(core.sent)
            client._token = lambda: self.fail("terminal replay attempted credential exchange")
            with self.assertRaises(ProviderError) as replay:
                client.model_call(refused)
            self.assertEqual(replay.exception.code, original.exception.code)
            self.assertEqual(replay.exception.http_status, response.status_code)
            self.assertEqual(len(core.sent), before)
            raw = next(iter(core.terminals.values()))["raw"]
            self.assertEqual(raw, {"base64": "aW52YWxpZCBwcm92aWRlciBqc29u"} if isinstance(response.body, Exception)
                             else {"json": response.body})

    def test_deferrals_and_unknown_outcomes_are_never_terminal_refusals(self):
        for error in (Deferred("provider_429", 10), Uncertain("unknown")):
            self.core.error = error
            with self.assertRaises(type(error)):
                self.client.model_call(self.request)
            self.assertEqual(self.core.terminals, {})

    def test_vision_keeps_word_geometry_and_uses_only_l1_downscale(self):
        self.core.response = Response({"responses": [{"fullTextAnnotation": {
            "text": "Thai text\n10", "pages": [{"width": 100, "height": 200,
            "blocks": [{"confidence": .8, "paragraphs": [{"confidence": .7, "words": [
                {"symbols": [{"text": "A"}, {"text": "B"}], "confidence": .9,
                 "boundingBox": {"vertices": [{"x": 1, "y": 2}, {"x": 4}]}},
                {"symbols": [{"text": "10"}], "confidence": .5}]}]}]}]}}]})
        result = self.client.vision_call(Frame(b"original", "image/png", b"smaller", "image/png"), stage="L1", page_number=4)
        self.assertEqual(self.core.sent[0]["payload"]["requests"][0]["image"]["content"], "c21hbGxlcg==")
        self.assertEqual(self.core.sent[0]["payload"]["requests"][0]["imageContext"]["languageHints"], ["th", "en"])
        self.assertEqual(result.page.page_number, 4)
        self.assertEqual(result.page.full_text, "Thai text\n10")
        self.assertAlmostEqual(result.page.avg_confidence, .7)
        paragraph = result.page.blocks[0].paragraphs[0]
        self.assertEqual(paragraph.text, "AB 10")
        self.assertEqual(paragraph.words[0].bbox.vertices, [(1, 2), (4, 0), (4, 0), (4, 0)])
        self.assertEqual(paragraph.words[1].bbox.vertices, [(0, 0)] * 4)

    def test_vision_blank_is_completed_and_malformed_response_is_not_blank(self):
        self.core.response = Response({"responses": [{}]})
        result = self.client.vision_call(Frame(b"img", "image/png"), stage="L1", page_number=1)
        self.assertEqual(result.page.full_text, "")
        self.assertEqual(result.page.avg_confidence, 0)
        self.assertEqual(vision_page({"text": "text only"}, 2).full_text, "text only")
        self.core.response, self.core.saved = Response({}), {}
        with self.assertRaisesRegex(ProviderError, "invalid_response"):
            self.client.vision_call(Frame(b"img", "image/png"), stage="L1", page_number=1)

    def test_vision_embedded_rejection_can_resume_but_partial_result_cannot_repay(self):
        frame = Frame(b"img", "image/png")
        for code in (8, 14):
            self.core.saved.clear()
            self.core.response = Response({"responses": [{"error": {"code": code}}]})
            with self.assertRaises(Deferred):
                self.client.vision_call(frame, stage="L1", page_number=1)
            self.assertFalse(self.core.saved)
            self.assertEqual(self.core.rejections[-1][1]["provider_code"], code)
            self.assertEqual(self.core.feedback[-1][1], 429 if code == 8 else 503)
        self.core.response = Response({"responses": [{"error": {"code": 8}, "fullTextAnnotation": {"text": "useful"}}]})
        rejected = len(self.core.rejections)
        with self.assertRaisesRegex(ProviderError, "partial_response"):
            self.client.vision_call(frame, stage="L1", page_number=1)
        self.assertEqual(len(self.core.rejections), rejected)
        self.assertTrue(self.core.saved)

    def test_factory_refuses_missing_work_or_changed_target_before_token_exchange(self):
        from pearnly_cowork.ocr.providers import core
        identity = {"backend": "vertex", "project": "synthetic-project", "location": "asia-southeast1",
                    "credentials_path": "/no/credential/is/read"}
        with patch.object(core, "frappe", SimpleNamespace(flags={})), patch.multiple(core.ocr,
             backend=lambda: "vertex", project=lambda: "synthetic-project", location=lambda: "asia-southeast1",
             credentials_path=lambda: "/no/credential/is/read"), patch.object(core.ocr, "access_token") as token:
            client = core.build_client({}, core_identity=identity)
            with self.assertRaisesRegex(ProviderConfigurationError, "durable_work_required"):
                client.model_call(self.request)
            token.assert_not_called()
            core.frappe.flags["pearnly_ocr_work"] = "synthetic-work"
            with patch.object(core.ocr, "project", return_value="changed-project"):
                with self.assertRaisesRegex(ProviderConfigurationError, "configuration_changed"):
                    client.model_call(self.request)
            token.assert_not_called()

    def test_core_rejected_crash_window_never_resends_without_product_terminal_record(self):
        from pearnly_cowork.ocr.providers import core
        identity = {"backend": "vertex", "project": "synthetic-project", "location": "asia-southeast1",
                    "credentials_path": "/no/credential/is/read"}
        for status in (400, 500, 200):
            def checkpoint(name):
                return None if name.startswith("cowork.terminal:") else {"state": "rejected", "body": {"http": status}}
            with patch.object(core, "frappe", SimpleNamespace(flags={"pearnly_ocr_work": "synthetic-work"})), patch.multiple(core.ocr,
                 backend=lambda: "vertex", project=lambda: "synthetic-project", location=lambda: "asia-southeast1",
                 credentials_path=lambda: "/no/credential/is/read"), patch.object(core.work, "checkpoint", side_effect=checkpoint), \
                 patch.object(core.ocr, "access_token") as token, patch.object(core.provider, "post") as post:
                client = core.build_client({}, core_identity=identity)
                with self.assertRaises(ProviderError) as refused:
                    client.model_call(self.request)
                self.assertEqual(refused.exception.http_status, status)
                token.assert_not_called()
                post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
