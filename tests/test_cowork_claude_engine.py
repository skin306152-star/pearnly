#!/usr/bin/env python3
"""No Frappe site, no network, no credentials: the Claude recognition engine (ADR-017).

- 供应商适配层：请求体形状（提示词缓存、图在前、档位、模型名映射）、回复解析、错误语义与谷歌适配层一致
  （认证 / 终局拒绝冻结、过载与 5xx 暂缓、结果不明换尝试号重发一次、续跑不再付费）、检查点身份带入口主机。
- 路由：Claude 模型交给 Claude，其余与读字交给谷歌。
- 引擎档位：OCR_ENGINE=claude → 只读一遍；没过闸只标红、不复核、不叫 Vision、写满上限不重读；谷歌档位不变。
- 远程执行：请求带非密连接、Cloud Run 连接不一致就拒绝、按模型计数认得 Claude、Claude 单独一个派发池。
"""
from datetime import date
import json
import os
import unittest
from unittest.mock import patch

import requests

from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_cowork.ocr.engine import EngineConfig, Frame, ModelResponse, VisionResponse, run_invoice
from pearnly_cowork.ocr.engine.config import CLAUDE_MODEL, config_from_environment
from pearnly_cowork.ocr.engine.contracts import ModelRequest, ProviderFailure
from pearnly_cowork.ocr.engine.cost import model_cost_thb
from pearnly_cowork.ocr.engine.errors import ContentError, EngineError
from pearnly_cowork.ocr.engine.prompts import IMAGE_SYSTEM, image_system
from pearnly_cowork.ocr.engine.runner import SINGLE_READ_PREFIX
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers import anthropic
from pearnly_cowork.ocr.providers.client import ProviderConfigurationError, ProviderError
from pearnly_cowork.ocr.providers.route import ModelRouter
from pearnly_cowork.ocr.remote import contract, runner, tasks
from pearnly_cowork.ocr.schema import Page

TODAY = date(2026, 10, 7)
GOOD = {"document_type": "tax_invoice", "invoice_number": "IV69/100", "subtotal": "100", "vat": "7",
        "total_amount": "107", "date": "2026-10-07", "seller_tax": "0105546015062"}
OFF_BY_ONE_TOTAL = {**GOOD, "total_amount": "170"}  # 金额勾稽不平：现行读法会复核，只读一遍只标红
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16
TARGET = {"base_url": "https://api.anthropic.com", "auth": "x-api-key", "models": {}}
RELAY = {"base_url": "https://openrouter.ai/api", "auth": "bearer",
         "models": {"claude-sonnet-5-5": "anthropic/claude-sonnet-5.5"}}
CLAUDE_ENV = {"OCR_LLM_BACKEND": "vertex", "OCR_ENGINE": "claude"}


def claude_config(environment=CLAUDE_ENV):
    return config_from_environment(environment, invoice_policy(environment, {"mode": "economy"}))


def request(**overrides):
    values = dict(stage="direct", page_number=1, attempt=0, model=CLAUDE_MODEL, prompt="SYSTEM PROMPT",
                  images=((PNG, "image/png"),))
    values.update(overrides)
    return ModelRequest(**values)


class Response:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def content(self):
        return json.dumps(self._body).encode() if not isinstance(self._body, Exception) else b"not json"


def message(text, *, stop="end_turn", usage=None):
    return {"type": "message", "content": [{"type": "text", "text": text}], "stop_reason": stop,
            "usage": usage or {"input_tokens": 3000, "output_tokens": 400, "cache_read_input_tokens": 1500,
                               "cache_creation_input_tokens": 0}}


class Harness:
    """注入能力的假实现：记录发出的请求、检查点、终局拒绝、额度回报。"""

    def __init__(self, replies, target=TARGET, key="sk-test"):
        self.replies, self.sent, self.checkpoints, self.terminal, self.feedback = list(replies), [], {}, {}, []
        self.context_checks = 0
        self.key = key
        self.client = anthropic.AnthropicClient(
            target, post=self.post, checkpoint_key=lambda payload, identity: identity,
            reject_checkpoint=lambda key, body: self.checkpoints.__setitem__(key, {"state": "rejected", "body": body}),
            terminal_read=lambda key: self.terminal.get(key), terminal_write=self.terminal.__setitem__,
            feedback=lambda *args: self.feedback.append(args), scope=lambda *args: "bucket",
            api_key=lambda: self.key, require_context=self.require_context, clock=lambda: 0.0,
            checkpoint_read=self.checkpoints.get)

    def require_context(self):
        self.context_checks += 1

    def post(self, *, url, headers, payload, timeout_s, model, bucket, label):
        self.sent.append({"url": url, "headers": headers, "payload": payload, "identity": json.loads(model)})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class AdapterTests(unittest.TestCase):
    def test_request_puts_the_prompt_in_cached_system_and_the_image_first(self):
        harness = Harness([Response(200, message(json.dumps(GOOD)))])
        response = harness.client.model_call(request())
        sent = harness.sent[0]
        self.assertEqual(sent["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(sent["headers"]["x-api-key"], "sk-test")
        self.assertNotIn("Authorization", sent["headers"])
        body = sent["payload"]
        self.assertEqual(body["model"], CLAUDE_MODEL)
        self.assertEqual(body["system"], [{"type": "text", "text": "SYSTEM PROMPT", "cache_control": {"type": "ephemeral"}}])
        content = body["messages"][0]["content"]
        self.assertEqual([part["type"] for part in content], ["image", "text"])
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        self.assertEqual(content[1]["text"], anthropic.READ_INSTRUCTION)
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", body)  # 新一代模型非默认采样参数会被拒
        self.assertEqual(response.text, json.dumps(GOOD))
        self.assertEqual((response.input_tokens, response.output_tokens), (4500, 400))
        self.assertEqual(response.model, CLAUDE_MODEL)
        self.assertEqual(sent["identity"][:5], ["cowork", "anthropic", "api.anthropic.com", "global", CLAUDE_MODEL])

    def test_relay_entry_maps_the_model_and_uses_a_bearer_token(self):
        harness = Harness([Response(200, message("{}"))], target=RELAY, key=" sk-or-x ")
        harness.client.model_call(request())
        sent = harness.sent[0]
        self.assertEqual(sent["url"], "https://openrouter.ai/api/v1/messages")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer sk-or-x")
        self.assertNotIn("x-api-key", sent["headers"])
        self.assertEqual(sent["payload"]["model"], "anthropic/claude-sonnet-5.5")
        # 检查点身份带入口主机：测试入口与正式入口互不复用对方已付费的结果
        self.assertEqual(sent["identity"][2], "openrouter.ai")

    def test_max_tokens_maps_to_the_engine_runaway_signal(self):
        harness = Harness([Response(200, message('{"invoice_number": "0000', stop="max_tokens"))])
        self.assertEqual(harness.client.model_call(request()).finish_reason, "MAX_TOKENS")

    def test_requests_it_cannot_express_fail_before_any_paid_call(self):
        for bad in (request(model="gemini-3.8-flash"), request(response_schema=["not", "a", "schema"]),
                    request(images=((PNG, "application/pdf"),)), request(prompt="")):
            harness = Harness([])
            with self.assertRaises(ProviderConfigurationError):
                harness.client.model_call(bad)
            self.assertEqual(harness.sent, [])

    def test_missing_key_fails_before_any_paid_call(self):
        harness = Harness([], key="")
        with self.assertRaises(ProviderConfigurationError):
            harness.client.model_call(request())
        self.assertEqual(harness.sent, [])

    def test_auth_and_bad_request_are_frozen_as_terminal_refusals(self):
        for status, code in ((401, "ocr.provider_auth"), (400, "ocr.provider_http_error")):
            harness = Harness([Response(status, {"type": "error", "error": {"type": "x"}})])
            with self.assertRaises(ProviderError) as caught:
                harness.client.model_call(request())
            self.assertEqual((caught.exception.code, caught.exception.http_status), (code, status))
            self.assertEqual(len(harness.terminal), 1)
            # 续跑：冻结的终局拒绝直接抛出，不再付费
            with self.assertRaises(ProviderError):
                harness.client.model_call(request())
            self.assertEqual(len(harness.sent), 1)

    def test_overloaded_and_server_errors_defer_without_freezing(self):
        for status, reason in ((529, "provider_overloaded"), (500, "provider_5xx"), (502, "provider_5xx")):
            harness = Harness([Response(status, {"type": "error"})])
            with self.assertRaises(Deferred) as caught:
                harness.client.model_call(request())
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(harness.terminal, {})
        self.assertEqual(Harness([]).feedback, [])

    def test_an_unknown_outcome_is_resent_once_under_a_new_attempt(self):
        harness = Harness([Uncertain("timeout"), Response(200, message("{}"))])
        with patch("pearnly_cowork.ocr.providers.anthropic._record_unknown"):
            harness.client.model_call(request())
        self.assertEqual([sent["identity"][7] for sent in harness.sent], [0, 1])
        first = json.dumps(harness.sent[0]["identity"])
        self.assertTrue(harness.checkpoints[first]["body"]["outcome_unknown"])
        # 续跑：已重发过的直接走重发那次，不再发第一次
        harness.replies.append(Response(200, message("{}")))
        harness.client.model_call(request())
        self.assertEqual(harness.sent[-1]["identity"][7], 1)

    def test_a_second_unknown_outcome_is_raised(self):
        harness = Harness([Uncertain("a"), Uncertain("b")])
        with patch("pearnly_cowork.ocr.providers.anthropic._record_unknown"), self.assertRaises(Uncertain):
            harness.client.model_call(request())

    def test_context_is_rechecked_before_every_paid_call(self):
        harness = Harness([Response(200, message("{}"))])
        harness.client.model_call(request())
        self.assertEqual(harness.context_checks, 1)

    def test_connection_accepts_only_nonsecret_https_settings(self):
        self.assertEqual(anthropic.connection(RELAY)["auth"], "bearer")
        for bad in ({**TARGET, "base_url": "http://api.anthropic.com"}, {**TARGET, "auth": "basic"},
                    {**TARGET, "api_key": "sk"}, {**TARGET, "models": {"gemini-3.8-flash": "x"}},
                    {**TARGET, "base_url": "https://x.example?y=1"}):
            with self.assertRaises(ProviderConfigurationError):
                anthropic.connection(bad)

    def test_a_strict_result_shape_goes_to_structured_outputs(self):
        # 对账抽取带严格结果格式：交给官方结构化输出（回复一定合格式），档位不变。
        from pearnly_cowork.ocr.enterprise import contract as enterprise_contract

        schema = enterprise_contract.SCHEMAS["bank"]
        body = anthropic.build_payload(request(response_schema=schema, max_output_tokens=16384))
        self.assertEqual(body["output_config"], {"effort": "low", "format": {"type": "json_schema", "schema": schema}})
        self.assertIsNot(body["output_config"]["format"]["schema"], schema)

    def test_batch_requests_reuse_the_same_payload_and_parser(self):
        # 以后接批处理（同价减半）：组请求与解析是纯函数，与实时调用同一份。
        built = anthropic.build_payload(request(), RELAY["models"])
        self.assertEqual(built, Harness([], target=RELAY).client.payload(request()))
        parsed = anthropic.parse_response(message("{}", stop="max_tokens"), CLAUDE_MODEL)
        self.assertEqual((parsed.finish_reason, parsed.input_tokens), ("MAX_TOKENS", 4500))


class RouterTests(unittest.TestCase):
    def test_claude_models_go_to_claude_and_everything_else_stays_google(self):
        calls = []

        class Google:
            def model_call(self, request, **kwargs):
                calls.append(("google", request.model))

            def vision_call(self, *args, **kwargs):
                calls.append(("vision",))

        class Claude:
            def model_call(self, request, **kwargs):
                calls.append(("claude", request.model))

        router = ModelRouter(Google(), Claude())
        router.model_call(request())
        router.model_call(request(model="gemini-3.1-flash-lite"))
        router.vision_call(None)
        self.assertEqual(calls, [("claude", CLAUDE_MODEL), ("google", "gemini-3.1-flash-lite"), ("vision",)])
        with self.assertRaises(ProviderConfigurationError):
            ModelRouter(Google()).model_call(request())


class Source:
    def __init__(self, count=1):
        self.page_count, self.text_pages = count, None

    def frame(self, number):
        return Frame(PNG + bytes([number]), "image/png")


class Model:
    def __init__(self, outputs):
        self.outputs, self.requests = list(outputs), []

    def __call__(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, ModelResponse):
            return output
        return ModelResponse(json.dumps(output) if not isinstance(output, str) else output, {}, 10, 20, request.model,
                             3, checkpoint_key=f"{request.stage}:{request.page_number}:{request.attempt}")


def no_vision(frame, *, stage, page_number):
    raise AssertionError("single read never calls Vision")


def read(model, source=None, config=None):
    return run_invoice(source or Source(), model, no_vision, config=config or claude_config(), anchor_year=2026,
                       today=TODAY)


class EngineProfileTests(unittest.TestCase):
    def test_claude_profile_reads_once_with_the_tested_prompt(self):
        config = claude_config({**CLAUDE_ENV, "OCR_DIRECT_MODEL": "gemini-3.8-flash", "OCR_LAYER3_ENABLED": "1"})
        self.assertEqual((config.direct_model, config.single_read, config.enable_layer3), (CLAUDE_MODEL, True, False))
        # 全部交给 Claude：表格的文字也给 Claude，每页都看图直读（含电子 PDF、页数多的文件）
        self.assertEqual((config.layer2_model, config.direct_enabled, config.max_direct_pages), (CLAUDE_MODEL, True, 50))
        self.assertEqual(image_system(CLAUDE_MODEL), image_system("gemini-3.8-flash"))
        self.assertNotEqual(image_system(CLAUDE_MODEL), IMAGE_SYSTEM)

    def test_gemini_profile_is_unchanged(self):
        environment = {"OCR_LLM_BACKEND": "vertex", "OCR_DIRECT_MODEL": "gemini-3.8-flash"}
        for value in ("", "gemini", "GEMINI"):
            config = claude_config({**environment, "OCR_ENGINE": value})
            self.assertEqual((config.direct_model, config.single_read), ("gemini-3.8-flash", False))
            self.assertEqual(config.layer2_model, "gemini-3.1-flash-lite")
        self.assertEqual(image_system("gemini-3.1-flash-lite"), IMAGE_SYSTEM)

    def test_an_unknown_engine_is_a_configuration_error(self):
        with self.assertRaises(EngineError):
            claude_config({**CLAUDE_ENV, "OCR_ENGINE": "gpt"})

    def test_cost_estimate_knows_claude(self):
        self.assertEqual(model_cost_thb(CLAUDE_MODEL, 1_000_000, 0), 70.0)

    def test_claude_cost_is_priced_from_the_reply_usage_with_cache_rates(self):
        from pearnly_cowork.ocr.engine.cost import THB_PER_USD, calls_cost_thb, response_cost_thb

        usage = {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
                 "cache_creation_input_tokens": 1_000_000, "output_tokens": 1_000_000}
        reply = anthropic.parse_response(message("{}", usage=usage), CLAUDE_MODEL)
        usd = 2.0 + 0.2 + 2.5 + 10.0  # 未缓存 + 读缓存 0.1 倍 + 写缓存 1.25 倍 + 输出
        self.assertAlmostEqual(response_cost_thb(reply), usd * THB_PER_USD)
        gemini = ModelResponse("{}", {}, 1_000_000, 0, "gemini-3.8-flash", 0)
        self.assertAlmostEqual(calls_cost_thb([gemini]), 0.75 * THB_PER_USD)  # 谷歌照旧按 token 估


class SingleReadTests(unittest.TestCase):
    def test_a_clean_read_is_one_call_and_no_vision(self):
        model = Model([GOOD])
        result = read(model)
        page = result.pipeline.pages[0]
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.requests[0].model, CLAUDE_MODEL)
        self.assertEqual(page.layer_chain, ["ID"])
        self.assertFalse(page.needs_manual_review)
        self.assertEqual(result.pipeline.engine, "image_direct")

    def test_a_failed_gate_is_flagged_not_reread(self):
        model = Model([OFF_BY_ONE_TOTAL])
        page = read(model).pipeline.pages[0]
        self.assertEqual(len(model.requests), 1)
        self.assertTrue(page.needs_manual_review)
        self.assertTrue(any(w.startswith(SINGLE_READ_PREFIX) for w in page.validation_warnings))
        self.assertEqual(page.extraction_audit["direct_fallback"]["code"], "ocr.direct_gate_failed")

    def test_output_at_the_limit_is_not_reread(self):
        runaway = ModelResponse('{"invoice_number": "000', {}, 10, 8192, CLAUDE_MODEL, 3, "MAX_TOKENS", "k")
        model = Model([runaway])
        with self.assertRaises(ContentError):
            read(model)
        self.assertEqual(len(model.requests), 1)

    def test_broken_json_is_not_retried(self):
        model = Model(["not json"])
        with self.assertRaises(ContentError):
            read(model)
        self.assertEqual(len(model.requests), 1)

    def test_an_unreadable_page_does_not_fail_a_multi_page_file(self):
        model = Model([GOOD, "not json", GOOD])
        pages = read(model, Source(3)).pipeline.pages
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(pages[1].layer_chain, ["PAGE_UNREAD"])
        self.assertTrue(pages[1].needs_manual_review)

    def test_a_definite_refusal_becomes_an_unread_page_but_auth_still_raises(self):
        model = Model([GOOD, ProviderFailure("ocr.provider_http_error", http_status=400)])
        pages = read(model, Source(2)).pipeline.pages
        self.assertEqual(pages[1].layer_chain, ["PAGE_UNREAD"])
        with self.assertRaises(ProviderFailure):
            read(Model([ProviderFailure("ocr.provider_auth", http_status=401)]))

    def test_text_layer_pdfs_and_long_files_are_read_by_image_too(self):
        source = Source(5)
        source.text_pages = [Page(page_number=n, width=1, height=1, blocks=[], full_text="TAX INVOICE text layer " * 3, avg_confidence=1) for n in range(1, 6)]
        model = Model([GOOD] * 5)
        result = read(model, source)
        self.assertEqual([(r.stage, r.model, len(r.images)) for r in model.requests], [("direct", CLAUDE_MODEL, 1)] * 5)
        self.assertEqual(result.pipeline.engine, "image_direct")

    def test_tables_go_to_claude_as_text(self):
        from pearnly_cowork.ocr.engine.runner import run_text_pages

        model = Model([GOOD])
        run_text_pages([Page(page_number=1, width=1, height=1, blocks=[], full_text="TAX INVOICE IV69/100 total 107 " * 2,
                             avg_confidence=1)], model,
                       config=claude_config(), anchor_year=2026, today=TODAY)
        self.assertEqual([(r.stage, r.model, r.images) for r in model.requests], [("layer2", CLAUDE_MODEL, ())])

    def test_the_gemini_path_still_rechecks(self):
        model = Model([OFF_BY_ONE_TOTAL, GOOD])
        config = claude_config({"OCR_LLM_BACKEND": "vertex"})
        page = run_invoice(Source(), model, lambda *a, **k: VisionResponse(
            Page(page_number=1, width=1, height=1, blocks=[], full_text="", avg_confidence=1), {}, 1, "v"),
            config=config, anchor_year=2026, today=TODAY).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "recheck"])
        self.assertIn("RC", page.layer_chain)


class RemoteTests(unittest.TestCase):
    BODY = dict(site="t.pearnly.com", work="a" * 64, batch="b" * 64, filename="a.png", content=PNG, page_count=1,
                anchor_date="2026-10-07", environment=CLAUDE_ENV, policy={"mode": "economy"})

    def test_request_carries_only_the_nonsecret_connection(self):
        body = contract.request(**self.BODY, anthropic=RELAY)
        self.assertEqual(body["anthropic"], RELAY)
        for bad in ({**RELAY, "api_key": "sk"}, {"base_url": "x"}, {**RELAY, "models": {"a": 1}}):
            with self.assertRaises(ValueError):
                contract.request(**self.BODY, anthropic=bad)
        self.assertNotIn("anthropic", contract.request(**self.BODY))

    def test_cloud_run_refuses_a_connection_that_differs_from_the_frozen_one(self):
        store = runner.Store(type("Backend", (), {"get": lambda *a: None, "create": lambda *a: 1,
                                                  "put": lambda *a, **k: 1, "delete": lambda *a: None})(), "s", "w")
        config = json.dumps({**TARGET, "api_key": "sk"})
        with patch.dict(os.environ, {runner.ANTHROPIC_ENV: config}):
            self.assertEqual(runner.claude_config(), (anthropic.connection(TARGET), "sk"))
            client = runner.build_client({"GCP_PROJECT": "p"}, store, lambda: "t", claude_target=RELAY)
            self.assertIsInstance(client, ModelRouter)
            with self.assertRaises(ProviderConfigurationError):
                client.model_call(request())  # 冻结的是测试入口，Cloud Run 配的是正式入口
        with patch.dict(os.environ, {runner.ANTHROPIC_ENV: ""}):
            self.assertEqual(runner.claude_config(), (None, ""))
        self.assertIsInstance(runner.build_client({"GCP_PROJECT": "p"}, store, lambda: "t"),
                              runner.ProviderClient)

    def test_counts_and_pools_know_claude(self):
        identity = json.dumps(["cowork", "anthropic", "api.anthropic.com", "global", CLAUDE_MODEL, "direct", 1, 0])
        self.assertEqual(runner.model_name(identity), CLAUDE_MODEL)
        body = contract.request(**self.BODY, anthropic=TARGET)
        self.assertEqual(tasks.pool_of("/v1/cowork/invoice", body), tasks.CLAUDE)
        self.assertIn(tasks.CLAUDE, tasks.ADAPTIVE_FLOOR)
        self.assertNotEqual(tasks.pool_of("/v1/cowork/invoice", contract.request(**self.BODY)), tasks.CLAUDE)


class DocumentTests(unittest.TestCase):
    """对账文档（银行 / 总账 / 税报）：同一份提示词与结果格式换 Claude 读一遍，自核不过不再读（ADR-017 第二步）。"""

    def test_the_engine_picks_the_extraction_model_and_reread_limit(self):
        from pearnly_cowork.ocr.engine.config import document_reading
        from pearnly_cowork.ocr.enterprise import contract as enterprise_contract, request as enterprise_request

        self.assertEqual(document_reading(CLAUDE_ENV), (CLAUDE_MODEL, 0, False))
        self.assertEqual(document_reading({"OCR_LLM_BACKEND": "vertex"}), (None, None, True))
        claude = enterprise_request.build_request("bank", "TRANSCRIPT", model=CLAUDE_MODEL)
        current = enterprise_request.build_request("bank", "TRANSCRIPT")
        self.assertEqual((claude["model"], current["model"]), (CLAUDE_MODEL, enterprise_contract.MODEL))
        self.assertEqual({k: v for k, v in claude.items() if k != "model"},
                         {k: v for k, v in current.items() if k != "model"})  # 提示词、结果格式不随模型变

    def run_bank(self, model_call, **engine):
        from pearnly_cowork.ocr.enterprise import pipeline

        transcript = "01/10/2026 deposit 100 balance 1100"
        with patch.object(pipeline, "_local", return_value=None), \
             patch.object(pipeline, "_transcripts", return_value=[transcript]):
            return pipeline.run("bank", page_count=1, reads=[(PNG, "image/png", 1)],
                                render=lambda number: (PNG, "image/png"),
                                document_ai_call=lambda *a, **k: ({}, 1, "docai"), model_call=model_call, **engine)

    def test_claude_skips_document_ai_and_reads_every_page_from_the_image(self):
        from pearnly_cowork.ocr.engine.config import document_reading
        from pearnly_cowork.ocr.enterprise import pipeline
        from pearnly_cowork.ocr import recon_reading

        row = {"transaction_date": "2026-10-01", "transaction_date_raw": "01/10/2026", "description": "x",
               "reference": "", "deposit": "100", "withdrawal": "", "amount": "100", "direction": "deposit"}
        pages = [{"opening_balance": "1000", "closing_balance": "1100", "entries": [{**row, "balance": "1100"}]},
                 {"opening_balance": "1100", "closing_balance": "1200", "entries": [{**row, "balance": "1200"}]}]

        def no_document_ai(*args, **kwargs):
            raise AssertionError("Claude never calls Document AI")

        class Doc:
            page_count = 2

            def frame(self, number):
                return Frame(PNG + bytes([number]), "image/png")

        for category, document_type in (("bank", "bank_statement"), ("gl", "general_ledger"), ("vat", "vat_report")):
            model = Model(pages if category != "vat" else [{"rows": []}, {"rows": []}])
            with patch.object(pipeline, "_local", side_effect=AssertionError("no local rebuild without a transcript")):
                legacy = recon_reading.read_document(Doc(), b"%PDF-not-read", True, "application/pdf", document_type,
                                                     no_document_ai, model, environment=CLAUDE_ENV)
            self.assertEqual([(r.model, r.stage) for r in model.requests], [(CLAUDE_MODEL, "enterprise_schema")] * 2)
            self.assertTrue(all("the only source" in r.prompt and "Enterprise OCR transcript:" not in r.prompt
                                for r in model.requests), category)
            self.assertEqual(legacy["timings"]["read_ms"], [])
        self.assertFalse(pipeline.first_pass("bank", page_count=2, reads=[], render=lambda n: (PNG, "image/png"),
                                             document_ai_call=no_document_ai, model_call=Model([]), stage="read",
                                             index=1, transcribe=False))
        warm = Model([pages[0]])
        self.assertTrue(recon_reading.warm_document(Doc(), b"%PDF-not-read", True, "application/pdf",
                                                    "bank_statement", no_document_ai, warm, "extract", 2,
                                                    environment=CLAUDE_ENV))
        self.assertEqual(warm.requests[0].page_number, 2)
        self.assertEqual(document_reading({})[2], True)

    def test_claude_reads_each_page_once_even_when_the_balance_breaks(self):
        broken = {"opening_balance": "1000", "closing_balance": "1500", "entries": [
            {"transaction_date": "2026-10-01", "transaction_date_raw": "01/10/2026", "description": "x",
             "reference": "", "deposit": "100", "withdrawal": "", "amount": "100", "direction": "deposit",
             "balance": "1500"}]}
        model = Model([broken, broken])
        self.run_bank(model, model=CLAUDE_MODEL, max_rereads=0)
        self.assertEqual([r.model for r in model.requests], [CLAUDE_MODEL])
        self.assertIsNotNone(model.requests[0].response_schema)
        current = Model([broken, broken])
        self.run_bank(current)
        self.assertEqual(len(current.requests), 2)  # 现行读法：余额链断了再读一次

    def test_requests_and_pools_carry_claude_for_documents(self):
        body = contract.document_request(site="t.pearnly.com", work="a" * 64, filename="s.pdf", content=b"%PDF",
                                         page_count=1, document_type="bank_statement", environment=CLAUDE_ENV,
                                         anthropic=RELAY)
        self.assertEqual(body["anthropic"], RELAY)
        self.assertEqual(tasks.pool_of("/v1/cowork/document", body), tasks.CLAUDE)
        del body["anthropic"]
        self.assertEqual(tasks.pool_of("/v1/cowork/document", body), tasks.DOCUMENT_POOL)
        with self.assertRaises(ValueError):
            contract.document_request(site="t.pearnly.com", work="a" * 64, filename="s.pdf", content=b"%PDF",
                                      page_count=1, document_type="bank_statement", environment=CLAUDE_ENV,
                                      anthropic={"base_url": "x"})

class SiteTests(unittest.TestCase):
    """站点本机：站点配置里的连接只冻结非密部分；Claude 档位受理时冻结连接、执行前核对；没配连接就不受理。"""

    def setUp(self):
        import frappe

        self.frappe = frappe
        self.conf = {"pearnly_anthropic": {**RELAY, "api_key": "sk-secret"}, "pearnly_ocr_backend": "vertex",
                     "pearnly_ocr_project": "pearnly", "pearnly_ocr_location": "asia-southeast1",
                     "pearnly_ocr_credentials": "/run/secrets/ocr.json",
                     "pearnly_cowork_ocr_environment": dict(CLAUDE_ENV),
                     "pearnly_cowork_ocr_policy": {"mode": "economy"}}
        for name, value in (("conf", self.conf), ("flags", SimpleFlags(pearnly_ocr_work="WORK-1"))):
            patcher = patch.object(frappe, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (("frappe.throw", RuntimeError), ("pearnly_cowork.ocr.execution.settings._", None)):
            patcher = (patch(target, side_effect=value) if value else patch(target, lambda text: text))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_site_connection_freezes_only_nonsecret_settings(self):
        from pearnly_cowork.ocr.providers import core

        self.assertEqual(core.anthropic_target(), anthropic.connection(RELAY))
        self.assertEqual(core.anthropic_key(), "sk-secret")
        self.conf["pearnly_anthropic"] = None
        self.assertIsNone(core.anthropic_target())

    def test_claude_admission_freezes_the_connection_and_execution_checks_it(self):
        from pearnly_cowork.ocr.execution import settings
        from pearnly_cowork.ocr.providers import core

        frozen = settings.snapshot()
        self.assertEqual(frozen["anthropic"], anthropic.connection(RELAY))
        self.assertNotIn("sk-secret", json.dumps(frozen))
        settings.revalidate(frozen)
        client = core.build_client(frozen["environment"], core_identity={k: frozen["target"][k] for k in
                                   ("backend", "project", "location", "credentials_path")},
                                   claude_target=frozen["anthropic"])
        self.assertIsInstance(client, ModelRouter)
        self.conf["pearnly_anthropic"] = {**TARGET, "api_key": "sk-other"}  # 受理后换了入口：执行前拒绝，不拿新入口的密钥去读
        with self.assertRaises(RuntimeError):
            settings.revalidate(frozen)
        with self.assertRaises(ProviderConfigurationError):
            client.model_call(request())

    def test_claude_without_a_connection_is_not_admitted_and_gemini_freezes_none(self):
        from pearnly_cowork.ocr.execution import settings

        self.conf["pearnly_anthropic"] = None
        with self.assertRaises(RuntimeError):
            settings.snapshot()
        self.conf["pearnly_cowork_ocr_environment"] = {"OCR_LLM_BACKEND": "vertex"}
        self.assertNotIn("anthropic", settings.snapshot())


class SimpleFlags(dict):
    def __getattr__(self, name):
        return self.get(name)


if __name__ == "__main__":
    unittest.main()
