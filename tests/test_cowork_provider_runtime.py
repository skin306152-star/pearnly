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
        self.plan = []

    def post(self, **values):
        self.calls.append(values)
        if self.plan:  # 按顺序给出的回复 / 异常（合成的 502、超时、慢响应）
            step = self.plan.pop(0)
            if isinstance(step, Exception):
                raise step
            identity = key(values["payload"], values["model"])
            self.sent.append(values)
            if step.status_code == 200 and isinstance(step.body, dict):
                self.saved[identity] = deepcopy(step.body)
            return step
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

    def test_requested_thinking_level_overrides_the_default(self):
        # ADR-013：复核与带原文再读指定 LOW；2.5 仍按预算关思考，非法档位在发送前拒绝
        client = self.core.client(environment={"VERTEX_LOCATION": "asia-southeast1", "VERTEX_LOCATION_25": "us-central1"})
        for model, image, thinking in (("gemini-3.1-flash-lite", True, {"thinkingLevel": "LOW"}),
                                       ("gemini-3.1-flash-lite", False, {"thinkingLevel": "LOW"}),
                                       ("gemini-2.5-flash", True, {"thinkingBudget": 0})):
            client.model_call(replace(self.request, model=model, thinking="LOW",
                                      images=((b"x", "image/png"),) if image else ()))
            self.assertEqual(self.core.calls[-1]["payload"]["generationConfig"].get("thinkingConfig"), thinking)
        sent = len(self.core.calls)
        with self.assertRaises(Exception):
            client.model_call(replace(self.request, thinking="ULTRA"))
        self.assertEqual(len(self.core.calls), sent)

    def test_completed_empty_truncated_and_invalid_model_json_are_not_retried_here(self):
        for body in ({}, {"candidates": [{"content": {"parts": [{"text": "{bad"}]}, "finishReason": "MAX_TOKENS"}]}):
            self.core.response = Response(body)
            self.core.saved.clear()
            before = len(self.core.sent)
            result = self.client.model_call(self.request)
            self.assertEqual(result.raw, body)
            self.assertEqual(len(self.core.sent), before + 1)

    def test_core_deferred_passes_through_and_an_unknown_outcome_is_resent_once(self):
        # 2026-10-01 用户：传输错误也要重试——录入发票的模型调用结果不明时重发一次（新的检查点），再不明才交出去。
        for error, calls in ((Deferred("capacity", 13), 1), (Uncertain("transport result unknown"), 2)):
            self.core.calls.clear()
            self.core.error = error
            with self.assertRaises(type(error)) as caught:
                self.client.model_call(self.request)
            self.assertIs(caught.exception, error)
            self.assertEqual(len(self.core.calls), calls)
        first, second = self.core.calls
        self.assertEqual(first["payload"], second["payload"])        # 重发内容逐字相同
        self.assertNotEqual(first["model"], second["model"])         # 检查点身份按尝试号区分
        self.assertEqual(self.core.rejections[-1][1]["outcome_unknown"], True)

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

    def test_google_5xx_is_a_temporary_failure_never_a_terminal_refusal(self):
        # 2026-10-01 测试租户：一次 Vision 502 让单页发票直接判失败。5xx 一律稍后重试，失败的请求不计费。
        for status in (500, 502, 504):
            core = FakeCore()
            client = core.client(environment={"VERTEX_OVERFLOW": "off"})
            core.response = Response({"error": {"message": "backend error"}}, status)
            with self.assertRaises(Deferred) as caught:
                client.model_call(self.request)
            self.assertEqual(caught.exception.reason, "provider_5xx")
            self.assertEqual(core.terminals, {})
            self.assertEqual(core.feedback, [])          # 不是额度问题：不放慢别的请求
            core.response = Response(model_body())
            self.assertEqual(client.model_call(self.request).text, '{"invoice_number": "A"}')

    def test_a_5xx_on_the_global_gemini_entry_goes_to_the_us_entry(self):
        self.core.plan = [Response({"error": {"message": "backend error"}}, 502), Response(model_body())]
        result = self.client.model_call(self.request)
        self.assertEqual(result.text, '{"invoice_number": "A"}')
        self.assertIn("aiplatform.googleapis.com", self.core.calls[0]["url"])
        self.assertIn("aiplatform.us.rep.googleapis.com", self.core.calls[1]["url"])

    def vision(self, client):
        frame = Frame(image_bytes=b"synthetic image", mime_type="image/png")
        return client.vision_call(frame, stage="L1", page_number=1)

    def vision_body(self):
        return {"responses": [{"fullTextAnnotation": {"text": "IV-1", "pages": []}}]}

    def test_vision_5xx_is_retried_in_place_and_then_read(self):
        pauses = []
        client = self.core.client(sleep=pauses.append)
        self.core.plan = [Response({"error": {}}, 502), Response({"error": {}}, 500), Response(self.vision_body())]
        self.assertEqual(self.vision(client).page.full_text, "IV-1")
        self.assertEqual(len(self.core.calls), 3)
        self.assertEqual(pauses, [2, 2])
        self.assertEqual(len({json.dumps(c["payload"], sort_keys=True) for c in self.core.calls}), 1)
        self.core.plan = [Response({"error": {}}, 502)] * 5
        with self.assertRaises(Deferred) as caught:     # 三个入口一直 5xx：交出去稍后续跑，不判失败
            self.vision(self.core.client(sleep=pauses.append))
        self.assertEqual(caught.exception.reason, "provider_5xx")

    def test_vision_unavailable_inside_a_200_reply_is_retried_in_place_too(self):
        pauses = []
        unavailable = {"responses": [{"error": {"code": 14, "message": "The service is currently unavailable."}}]}
        self.core.plan = [Response(unavailable), Response(unavailable), Response(self.vision_body())]
        self.assertEqual(self.vision(self.core.client(sleep=pauses.append)).page.full_text, "IV-1")
        self.assertEqual(pauses, [2, 2])

    def test_a_vision_outage_on_the_global_entry_moves_to_the_us_then_eu_entries(self):
        unavailable = {"responses": [{"error": {"code": 14, "message": "The service is currently unavailable."}}]}
        self.core.plan = [Response(unavailable)] * 4 + [Response(self.vision_body())]
        self.assertEqual(self.vision(self.core.client(sleep=lambda s: None)).page.full_text, "IV-1")
        hosts = [call["url"].split("/")[2] for call in self.core.calls]
        self.assertEqual(hosts, ["vision.googleapis.com"] * 3 + ["us-vision.googleapis.com", "eu-vision.googleapis.com"])
        self.assertEqual(len({json.dumps(c["payload"], sort_keys=True) for c in self.core.calls}), 1)
        self.core.calls.clear()
        self.core.plan = [Response(unavailable)] * 5
        with self.assertRaises(Deferred) as caught:     # 三个入口都不行：交回稍后续跑
            self.vision(self.core.client(sleep=lambda s: None))
        self.assertEqual(caught.exception.reason, "vision_14")
        self.core.calls.clear()
        self.core.plan = [Response(unavailable)] * 3
        with self.assertRaises(Deferred):               # 备用入口开关关掉：只用全球入口
            self.vision(self.core.client(sleep=lambda s: None, environment={"VERTEX_OVERFLOW": "off"}))
        self.assertEqual(len(self.core.calls), 3)

    def test_a_slow_vision_call_is_given_up_after_15_seconds_and_resent_with_the_full_limit(self):
        client = self.core.client(environment={"OCR_L1_TIMEOUT_SECONDS": "60"})
        self.core.plan = [Uncertain("read timeout"), Response(self.vision_body())]
        self.assertEqual(self.vision(client).page.full_text, "IV-1")
        first, second = self.core.calls
        self.assertEqual((first["timeout_s"], second["timeout_s"]), (15, 60))
        self.assertEqual(first["payload"], second["payload"])
        # 续跑：上回第一次不明、重发读完 —— 直接取重发那次，不再付费
        resumed = FakeCore()
        resumed.saved, resumed.rejections = dict(self.core.saved), list(self.core.rejections)
        resumed_client = resumed.client(environment={"OCR_L1_TIMEOUT_SECONDS": "60"},
            checkpoint_read=lambda identity: ({"state": "rejected", "body": {"outcome_unknown": True}}
                                              if any(r[0] == identity for r in self.core.rejections) else None))
        self.assertEqual(self.vision(resumed_client).page.full_text, "IV-1")
        self.assertEqual(resumed.sent, [])

    def test_a_vision_answer_within_the_first_limit_is_used_as_is(self):
        self.core.plan = [Response(self.vision_body())]
        self.assertEqual(self.vision(self.core.client()).page.full_text, "IV-1")
        self.assertEqual([c["timeout_s"] for c in self.core.calls], [15])

    def test_a_large_page_image_gets_more_time_before_it_is_resent(self):
        # 扫描 PDF 一页约 3.3MB：上传要几秒到三十秒，第一次按大小多给时间，不在上传途中断开再付费重发（2026-10-02）
        client = self.core.client(environment={"OCR_L1_TIMEOUT_SECONDS": "60"})
        self.core.plan = [Uncertain("read timeout"), Response(self.vision_body())]
        frame = Frame(image_bytes=b"x" * 3_300_000, mime_type="image/png")
        self.assertEqual(client.vision_call(frame, stage="L1", page_number=1).page.full_text, "IV-1")
        self.assertEqual([c["timeout_s"] for c in self.core.calls], [48, 93])   # 重发也按大小多给

    def test_deferrals_and_unknown_outcomes_are_never_terminal_refusals(self):
        for error in (Deferred("provider_429", 10), Uncertain("unknown")):
            self.core.error = error
            with self.assertRaises(type(error)):
                self.client.model_call(self.request)
            self.assertEqual(self.core.terminals, {})

    def test_a_google_rejection_on_global_goes_to_the_us_entry_for_that_call_only(self):
        # 用户 2026-10-01 同意：全球入口被谷歌拒绝时这一次改发美国多区域入口（多付 10%、数据在美国处理）。
        posted = []
        original = self.core.post

        def post(**values):
            posted.append(values)
            if len(posted) == 1:
                raise Deferred("provider_429", 6)
            return original(**values)
        self.core.post = post
        client = self.core.client()
        result = client.model_call(self.request)
        self.assertEqual(result.text, '{"invoice_number": "A"}')
        self.assertIn("https://aiplatform.googleapis.com/v1/projects/synthetic-project/locations/global/", posted[0]["url"])
        self.assertEqual((posted[0]["hand_back"], posted[0]["shared_quota"]), (("provider_",), True))
        self.assertIn("https://aiplatform.us.rep.googleapis.com/v1/projects/synthetic-project/locations/us/", posted[1]["url"])
        self.assertEqual(posted[1]["bucket"], ("vertex", "synthetic-project", "us", "gemini-3.8-flash"))
        self.assertEqual(posted[1]["hand_back"], ("provider_",))   # 美国入口被拒再转欧洲
        self.assertEqual(len(posted), 2)

    def test_us_rejected_too_goes_to_the_eu_entry_last(self):
        # 用户 2026-10-01 同意加欧洲：全球 → 美国 → 欧洲；欧洲是最后一个，再被拒就照原规则原地短等。
        posted = []
        original = self.core.post

        def post(**values):
            posted.append(values)
            if len(posted) <= 2:
                raise Deferred("provider_429", 6)
            return original(**values)
        self.core.post = post
        result = self.core.client().model_call(self.request)
        self.assertEqual(result.text, '{"invoice_number": "A"}')
        self.assertIn("https://aiplatform.eu.rep.googleapis.com/v1/projects/synthetic-project/locations/eu/", posted[2]["url"])
        self.assertEqual(posted[2]["bucket"], ("vertex", "synthetic-project", "eu", "gemini-3.8-flash"))
        self.assertEqual(posted[2]["hand_back"], ())
        core = FakeCore()
        core.error = Deferred("provider_429", 6)
        with self.assertRaises(Deferred):
            core.client(environment={"VERTEX_OVERFLOW": "us"}).model_call(self.request)
        self.assertEqual(len(core.calls), 2)

    def test_an_unknown_outcome_on_the_us_entry_is_resent_once(self):
        posted = []
        original = self.core.post

        def post(**values):
            posted.append(values)
            if len(posted) == 1:
                raise Deferred("provider_429", 6)          # 全球入口被拒 → 转美国
            if len(posted) == 2:
                raise Uncertain("read timed out")          # 美国入口这次结果不明 → 重发一次
            return original(**values)
        self.core.post = post
        result = self.core.client().model_call(self.request)
        self.assertEqual(result.text, '{"invoice_number": "A"}')
        self.assertEqual(len(posted), 3)
        self.assertEqual(posted[2]["hand_back"], ("provider_",))
        self.assertTrue(all("/locations/us/" in values["url"] for values in posted[1:]))
        self.assertNotEqual(posted[1]["model"], posted[2]["model"])   # 重发是新的检查点身份
        self.assertTrue(self.core.rejections[-1][1]["outcome_unknown"])

    def test_when_the_spare_entries_stay_silent_the_global_entry_gets_one_more_try(self):
        posted = []
        original = self.core.post

        def post(**values):
            posted.append(values)
            if len(posted) == 1:
                raise Deferred("provider_429", 6)          # 全球入口被拒 → 转美国
            if len(posted) in (2, 3):
                raise Uncertain("read timed out")          # 美国入口这次、重发都没回音
            return original(**values)
        self.core.post = post
        result = self.core.client().model_call(self.request)
        self.assertEqual(result.text, '{"invoice_number": "A"}')
        self.assertEqual(len(posted), 4)                    # 只多这一次：退回全球入口
        self.assertIn("aiplatform.googleapis.com", posted[3]["url"])
        identities = [json.loads(values["model"]) for values in posted]
        self.assertEqual(identities[3][-1], self.request.attempt + 2)   # 新的尝试号，和全球入口本身、它的重发分开
        self.assertEqual(len({values["model"] for values in posted}), 4)

    def test_a_flash_lite_call_waits_40_seconds_first_then_resends_with_its_full_limit(self):
        lite = replace(self.request, model="gemini-3.1-flash-lite", timeout_s=120)
        self.core.plan = [Uncertain("read timed out"), Response(model_body())]
        result = self.core.client(environment={"VERTEX_OVERFLOW": "off"}).model_call(lite)
        self.assertEqual(result.text, '{"invoice_number": "A"}')
        self.assertEqual([c["timeout_s"] for c in self.core.calls], [40, 120])
        self.core.calls.clear()
        self.core.plan = [Response(model_body())]
        heavy = replace(self.request, model="gemini-3.8-flash", timeout_s=180, stage="L9")
        self.core.client(environment={"VERTEX_OVERFLOW": "off"}).model_call(heavy)
        self.assertEqual([c["timeout_s"] for c in self.core.calls], [180])   # 3.8-flash 照旧给足

    def test_own_capacity_off_switch_and_regional_models_stay_on_their_entry(self):
        for environment, model, error in (({}, "gemini-3.8-flash", Deferred("capacity", 3)),
                                          ({"VERTEX_OVERFLOW": "off"}, "gemini-3.8-flash", Deferred("provider_429", 6)),
                                          ({}, "gemini-3.5-flash", Deferred("provider_429", 6))):
            core = FakeCore()
            core.error = error
            with self.assertRaises(Deferred):
                core.client(environment=environment).model_call(replace(self.request, model=model))
            self.assertEqual(len(core.calls), 1, (environment, model, error.reason))

    def test_resume_takes_a_result_already_read_on_the_us_entry_without_paying_again(self):
        us = json.dumps(["cowork", "vertex", "synthetic-project", "us", "gemini-3.8-flash", "L2", 1, 0])
        client = self.core.client(checkpoint_read=lambda identity: {"state": "done"} if identity == us else None)
        client._key = lambda payload, identity: identity
        client.model_call(self.request)
        self.assertIn("/locations/us/", self.core.calls[0]["url"])
        self.assertEqual(len(self.core.calls), 1)       # 不先去全球入口再付一次
        eu = json.dumps(["cowork", "vertex", "synthetic-project", "eu", "gemini-3.8-flash", "L2", 1, 1])
        core = FakeCore()
        client = core.client(checkpoint_read=lambda identity: {"state": "done"} if identity == eu else None)
        client._key = lambda payload, identity: identity
        client.model_call(self.request)
        self.assertIn("/locations/eu/", core.calls[0]["url"])
        self.assertEqual(len(core.calls), 1)

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
