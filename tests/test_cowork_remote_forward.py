#!/usr/bin/env python3
"""No Frappe, no network: the server side hands one Cowork invoice to Cloud Run (ADR-0015).

- 只有明确打开、地址是 https://…run.app 时才走 Cloud Run；
- 发出前拿租户 / 全站名额（与本机识别同一套计数），名额在整份读完前一直占着；
- 按行读回复：页进度推给提交人，结果照本机同样的异常抛出（暂缓 / 结果不明 / 明确拒绝 / 程序错误）；
- 令牌拿不到、连不上、回复中断、实例中途没了：都按暂缓处理，检查点在存储桶里，下次接着读、不重复付费。
"""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import requests
from requests.structures import CaseInsensitiveDict

ROOT = Path(__file__).resolve().parents[1]
WORK, BATCH = "a" * 64, "b" * 64


class Deferred(Exception):
    def __init__(self, reason="capacity", delay=5):
        super().__init__(reason)
        self.reason, self.delay = reason, delay


class Uncertain(Exception):
    pass


def load():
    frappe = types.ModuleType("frappe")
    frappe.conf = {}
    frappe.local = types.SimpleNamespace(site="acme.pearnly.com")
    frappe.flags = {}
    errors = types.ModuleType("pearnly_core.execution.errors")
    errors.Deferred, errors.Uncertain = Deferred, Uncertain
    budget = types.ModuleType("pearnly_core.execution.budget")
    budget.permits = []

    @contextmanager
    def permit(bucket, timeout, rpm=None):
        budget.permits.append((bucket, rpm, "in"))
        yield
        budget.permits.append((bucket, rpm, "out"))

    budget.refusals, budget.notes = [], []

    @contextmanager
    def gated(bucket, timeout, rpm=None):
        if budget.refusals:
            raise Deferred("capacity", budget.refusals.pop(0))
        with permit(bucket, timeout, rpm):
            yield

    budget.permit, budget.scope = gated, lambda *a: "bucket:" + ":".join(a)
    budget.note = lambda event, label, amount=1: budget.notes.append((event, label))
    pool = types.ModuleType("pearnly_core.execution.pool")
    pool.released = []
    pool.release_db = lambda: pool.released.append(len(budget.permits))
    work = types.ModuleType("pearnly_core.execution.work")
    work.JOB_TIMEOUT = 600
    provider = types.ModuleType("pearnly_core.execution.provider")
    provider.posted = []
    provider.http_post = lambda url, **kwargs: provider.posted.append(url) or provider.reply()
    replay = types.ModuleType("pearnly_core.execution.replay")
    replay.refuse_paid = lambda label: None  # 回放压测没开（开着时拒绝付费调用，见 pearnly_core/execution/test_replay.py）
    ocr = types.ModuleType("pearnly_core.ocr")
    ocr.credentials_path = lambda: "/run/secrets/ocr.json"
    core = types.ModuleType("pearnly_core")
    core.ocr = ocr
    execution = types.ModuleType("pearnly_core.execution")
    execution.budget, execution.work, execution.pool, execution.provider = budget, work, pool, provider
    execution.replay = replay
    client = types.ModuleType("pearnly_cowork.ocr.providers.client")

    class ProviderError(Exception):
        def __init__(self, code, *, http_status=None, provider_code=None):
            super().__init__(code)
            self.code, self.http_status, self.provider_code = code, http_status, provider_code

    client.ProviderError = ProviderError
    errors_module = types.ModuleType("pearnly_cowork.ocr.engine.errors")

    class EngineError(ValueError):
        def __init__(self, code, *, stage="", page_number=0, detail=""):
            super().__init__(code)
            self.code, self.stage, self.page_number, self.detail = code, stage, page_number, detail

    errors_module.EngineError = EngineError
    errors_module.ContentError = type("ContentError", (EngineError,), {})
    errors_module.DirectFallback = type("DirectFallback", (errors_module.ContentError,), {})
    engine = types.ModuleType("pearnly_cowork.ocr.engine")
    engine.errors = errors_module
    modules = {"frappe": frappe, "pearnly_core": core, "pearnly_core.ocr": ocr, "pearnly_core.execution": execution,
               "pearnly_core.execution.errors": errors, "pearnly_core.execution.budget": budget,
               "pearnly_core.execution.work": work, "pearnly_core.execution.pool": pool,
               "pearnly_core.execution.provider": provider, "pearnly_core.execution.replay": replay,
               "pearnly_cowork.ocr.providers.client": client, "pearnly_cowork.ocr.engine": engine,
               "pearnly_cowork.ocr.engine.errors": errors_module}
    sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
    with patch.dict(sys.modules, modules):
        from pearnly_cowork.ocr.remote import contract
        namespace = {"__name__": "forward"}
        source = ROOT / "apps/pearnly_cowork/pearnly_cowork/ocr/remote/forward.py"
        exec(compile(source.read_text(), str(source), "exec"), namespace)
    namespace["_pool"] = pool
    namespace["_provider"] = provider
    namespace["_errors"] = errors_module
    return namespace, frappe, budget, contract, ProviderError


def ndjson(*events, status=200):
    result = requests.Response()
    result.status_code = status
    result.headers = CaseInsensitiveDict()
    result.raw = io.BytesIO(b"".join((json.dumps(e) + "\n").encode() for e in events))
    return result


DOC = types.SimpleNamespace(work_name=WORK, name=BATCH, page_count=1, anchor_date="2026-09-21")
FROZEN = {"environment": {"GCP_PROJECT": "pearnly", "OCR_LLM_BACKEND": "vertex"}, "policy": {"mode": "economy"}}
ON = {"enabled": 1, "url": "https://pearnly-ocr-abc-as.a.run.app"}


class Forward(unittest.TestCase):
    def setUp(self):
        self.ns, self.frappe, self.budget, self.contract, self.ProviderError = load()
        self.frappe.conf["pearnly_cowork_ocr_remote"] = dict(ON)
        self.pages, self.sent = [], []

    def call(self, *events, status=200, error=None, token=None):
        def post(url, json=None, timeout=None, stream=False, headers=None):
            self.sent.append((url, json, headers))
            if error:
                raise error
            return ndjson(*events, status=status)

        return self.ns["recognize"](DOC, FROZEN, None, b"%PDF-1.7 synthetic", "a.pdf", self.pages.append,
                                    post=post, id_token=token or (lambda audience: "ID_TOKEN"))

    def test_the_default_transport_is_the_shared_tls_post(self):
        # 不传 post 时走共用层的 http_post（共用 TLS 上下文），不是 requests.post
        provider = self.ns["_provider"]
        provider.reply = lambda: ndjson({"event": "result", "status": "done", "pipeline": {}, "evidence": {}})
        try:
            self.ns["recognize"](DOC, FROZEN, None, b"%PDF-1.7 synthetic", "a.pdf", self.pages.append,
                                 id_token=lambda audience: "ID_TOKEN")
        except Exception:
            pass  # 只核对发到哪里；回复内容由其他用例覆盖
        self.assertEqual(provider.posted, [ON["url"] + "/v1/cowork/invoice"])

    def reply(self, status=200, body=None):
        result = requests.Response()
        result.status_code = status
        result.headers = CaseInsensitiveDict()
        result.raw = io.BytesIO(json.dumps(body).encode())
        return result

    def inspect(self, *replies):
        queue = list(replies)

        def post(url, json=None, timeout=None, headers=None):
            self.sent.append((url, json, headers))
            step = queue.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        return self.ns["inspect_original"]("acme.pearnly.com/originals/CWS-" + "c" * 48 + ".pdf", "scan.pdf",
                                           post=post, id_token=lambda audience: "ID_TOKEN")

    def test_inspect_asks_cloud_run_without_a_permit_and_without_holding_the_database(self):
        ok = {"status": "ok", "meta": {"page_count": 2}, "generation": 9, "md5": "m"}
        self.assertEqual(self.inspect(self.reply(body=ok)), ok)
        url, body, headers = self.sent[0]
        self.assertEqual(url, ON["url"] + "/v1/cowork/inspect")
        self.assertEqual((body["kind"], body["original"], body["filename"]),
                         ("cowork.inspect", {"object": "acme.pearnly.com/originals/CWS-" + "c" * 48 + ".pdf"}, "scan.pdf"))
        self.assertEqual(headers["Authorization"], "Bearer ID_TOKEN")
        self.assertEqual(self.budget.permits, [])            # 不是付费调用，不占识别名额
        self.assertEqual(self.ns["_pool"].released, [0])     # 等回复前放开数据库连接
        rejected = {"status": "rejected", "code": "pdf_password"}
        self.assertEqual(self.inspect(self.reply(body=rejected)), rejected)

    def test_inspect_busy_or_unreachable_asks_the_page_to_retry(self):
        for step in (requests.ConnectionError("down"), self.reply(503, {}),
                     self.reply(body={"status": "error", "code": "original_unavailable"})):
            with self.assertRaises(Deferred):
                self.inspect(step)
        for step in (self.reply(403, {}), self.reply(body={"status": "ok", "meta": {}}),
                     self.reply(body={"status": "error", "code": "invalid_request"}), self.reply(body=[1])):
            with self.assertRaises(self.ns["RemoteError"]):
                self.inspect(step)

    def preview(self, reply):
        def post(url, json=None, timeout=None, headers=None):
            self.sent.append((url, json, headers))
            if isinstance(reply, Exception):
                raise reply
            return reply
        return self.ns["preview"]("acme.pearnly.com/originals/CWS-" + "c" * 48 + ".pdf", 9, "d" * 64, "pdf", 2,
                                  post=post, id_token=lambda audience: "ID_TOKEN")

    def image(self, status=200, body=b"PNG", content_type="image/png"):
        result = requests.Response()
        result.status_code = status
        result.headers = CaseInsensitiveDict({"Content-Type": content_type})
        result.raw = io.BytesIO(body)
        return result

    def test_preview_brings_back_only_the_picture(self):
        self.assertEqual(self.preview(self.image()), {"png": b"PNG"})
        url, body, _headers = self.sent[0]
        self.assertEqual(url, ON["url"] + "/v1/cowork/preview")
        self.assertEqual((body["file_kind"], body["page"], body["original"]["generation"]), ("pdf", 2, 9))
        self.assertEqual(self.budget.permits, [])
        self.assertEqual(self.preview(self.reply(422, {"code": "preview_too_large"})), {"code": "preview_too_large"})
        for reply in (requests.ConnectionError("down"), self.reply(503, {}),
                      self.reply(422, {"code": "original_unavailable"})):
            with self.assertRaises(Deferred):
                self.preview(reply)
        for reply in (self.reply(422, {"code": "original_mismatch"}), self.image(content_type="text/html"),
                      self.reply(403, {})):
            with self.assertRaises(self.ns["RemoteError"]):
                self.preview(reply)

    def test_an_original_in_the_bucket_is_sent_by_reference_not_by_bytes(self):
        def post(url, json=None, **kwargs):
            self.sent.append(json)
            return ndjson({"event": "result", "status": "done", "pipeline": "{}", "evidence": []})

        original = self.contract.original(object="acme.pearnly.com/originals/CWS-" + "c" * 48 + ".pdf",
                                          generation=9, sha256="d" * 64)
        self.ns["recognize"](DOC, FROZEN, None, None, "a.pdf", self.pages.append, original=original, post=post,
                             id_token=lambda audience: "T")
        self.assertNotIn("content", self.sent[0])
        self.assertEqual(self.sent[0]["original"], original)

    def test_stored_previews_come_back_only_when_asked_for(self):
        """带了预览规格才让 Cloud Run 存图；读完把存好的交给回调（服务器随绑定原件那一次保存记下）。"""
        stored = {"bucket": "pearnly-cowork-originals-sg", "pages": []}

        def post(url, json=None, **kwargs):
            self.sent.append(json)
            return ndjson({"event": "result", "status": "done", "pipeline": "{}", "evidence": [], "previews": stored})

        spec = self.contract.previews(source="CWS-" + "c" * 48, sha256="d" * 64, kind="pdf")
        original = self.contract.original(object="acme.pearnly.com/originals/CWS-" + "c" * 48 + ".pdf",
                                          generation=9, sha256="d" * 64)
        got = []
        result = self.ns["recognize"](DOC, FROZEN, None, None, "a.pdf", self.pages.append, original=original,
                                      previews=spec, on_previews=got.append, post=post, id_token=lambda audience: "T")
        self.assertEqual(result, ("{}", []))
        self.assertEqual(self.sent[0]["previews"], spec)
        self.assertEqual(got, [stored])
        got.clear()
        self.ns["recognize"](DOC, FROZEN, None, None, "a.pdf", self.pages.append, original=original,
                             on_previews=got.append, post=post, id_token=lambda audience: "T")
        self.assertNotIn("previews", self.sent[1])
        self.assertEqual(got, [])
        with self.assertRaises(ValueError):   # 规格里的哈希必须就是这份原件的
            self.ns["recognize"](DOC, FROZEN, None, None, "a.pdf", self.pages.append, original=original,
                                 previews=dict(spec, sha256="e" * 64), post=post, id_token=lambda audience: "T")

    def test_switch_needs_explicit_https_run_app_url(self):
        settings = self.ns["settings"]
        self.assertIsNotNone(settings())
        for value in ({"enabled": 0, "url": ON["url"]}, {"enabled": 1, "url": "http://x.run.app"},
                      {"enabled": 1, "url": "https://evil.example.com"}, None):
            self.frappe.conf["pearnly_cowork_ocr_remote"] = value
            self.assertIsNone(settings())

    def test_done_returns_result_and_reports_pages_inside_one_permit(self):
        result = self.call({"event": "page", "page": 1}, {"event": "tick"}, {"event": "page", "page": 2},
                           {"event": "result", "status": "done", "pipeline": "{}", "evidence": [{"k": 1}]})
        self.assertEqual(result, ("{}", [{"k": 1}]))
        self.assertEqual(self.pages, [1, 2])
        self.assertEqual([p[2] for p in self.budget.permits], ["in", "out"])
        # 拿到名额后、发请求等回复前先放开数据库连接（等待中不占连接）
        self.assertEqual(self.ns["_pool"].released, [1])
        url, body, headers = self.sent[0]
        self.assertEqual(url, ON["url"] + "/v1/cowork/invoice")
        self.assertEqual(headers["Authorization"], "Bearer ID_TOKEN")
        self.assertEqual((body["site"], body["work"], body["batch"]), ("acme.pearnly.com", WORK, BATCH))

    def test_outcomes_raise_like_local_execution(self):
        with self.assertRaises(Deferred) as caught:
            self.call({"event": "result", "status": "deferred", "reason": "provider_429", "delay": 7})
        self.assertEqual(caught.exception.reason, "provider_429")
        with self.assertRaises(Uncertain):
            self.call({"event": "result", "status": "uncertain", "reason": "x"})
        with self.assertRaises(self.ProviderError) as refused:
            self.call({"event": "result", "status": "refused", "code": "ocr.provider_auth", "http_status": 403})
        self.assertEqual((refused.exception.code, refused.exception.http_status), ("ocr.provider_auth", 403))
        with self.assertRaises(self.ns["RemoteError"]):
            self.call({"event": "result", "status": "error", "code": "KeyError"})

    def test_engine_content_errors_raise_like_local_execution(self):
        errors = self.ns["_errors"]
        with self.assertRaises(errors.ContentError) as caught:
            self.call({"event": "result", "status": "error", "code": "ContentError", "error_code": "ocr.all_pages_unread",
                       "stage": "L2", "page_number": 3, "detail": "x"})
        self.assertEqual((caught.exception.code, caught.exception.stage, caught.exception.page_number),
                         ("ocr.all_pages_unread", "L2", 3))

    def test_send_carries_variant_and_yield(self):
        def post(url, json=None, **kwargs):
            self.sent.append(json)
            return ndjson({"event": "result", "status": "done", "pipeline": "{}", "evidence": []})

        self.ns["send"](work_name=WORK, batch=WORK, filename="a.pdf", content=b"%PDF", page_count=2,
                        anchor_date="2026-09-21", frozen=FROZEN, variant="vat_check", yield_seconds=120,
                        post=post, id_token=lambda audience: "T")
        self.assertEqual((self.sent[-1]["variant"], self.sent[-1]["yield_seconds"], self.sent[-1]["batch"]),
                         ("vat_check", 120, WORK))

    def test_document_events_drive_the_board_like_local_callbacks(self):
        seen = []

        def post(url, json=None, **kwargs):
            self.sent.append((url, json))
            return ndjson({"event": "read", "pages": 2}, {"event": "tick"},
                          {"event": "extracted", "page": 1, "text": "{}", "reread": False},
                          {"event": "extracted", "page": 1, "text": "{}", "reread": True},
                          {"event": "unread", "page": 2},
                          {"event": "result", "status": "done", "legacy": {"pages": []}})

        legacy = self.ns["send_document"](work_name=WORK, filename="b.pdf", content=b"%PDF", page_count=2,
            document_type="bank_statement", frozen=FROZEN, regions=[{"location": "asia-southeast1"}], yield_seconds=90,
            on_read=lambda n: seen.append(("read", n)), on_extracted=lambda p, t: seen.append(("extracted", p)),
            on_unread=lambda p: seen.append(("unread", p)), post=post, id_token=lambda audience: "T")
        self.assertEqual(legacy, {"pages": []})
        self.assertEqual(seen, [("read", 2), ("extracted", 1), ("unread", 2)])  # 自核重读不重复记
        url, body = self.sent[-1]
        self.assertTrue(url.endswith("/v1/cowork/document"))
        self.assertEqual((body["kind"], body["document_type"], body["regions"]),
                         ("cowork.document", "bank_statement", [{"location": "asia-southeast1"}]))

    def test_lost_transport_is_deferred_never_judged_here(self):
        for kwargs in ({"error": requests.ConnectionError("down")}, {"status": 503}, {"status": 429}):
            with self.assertRaises(Deferred):
                self.call(**kwargs)
        with self.assertRaises(Deferred) as cut:
            self.call({"event": "page", "page": 1})  # 实例中途没了：没有结果行
        self.assertEqual(cut.exception.reason, "remote_interrupted")

    def test_no_slot_waits_in_place_without_holding_the_database(self):
        self.budget.refusals[:] = [3, 4]
        with patch.object(self.ns["time"], "sleep") as sleep:
            self.call({"event": "result", "status": "done", "pipeline": "{}", "evidence": []})
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [3.0, 4.0])
        self.assertEqual(self.ns["_pool"].released[:2], [0, 0])  # 每次等之前都放开连接
        self.assertIn(("deferred:capacity", "cloudrun"), self.budget.notes)
        self.assertIn(("remote:done", "cloudrun"), self.budget.notes)

    def test_a_long_wait_for_a_slot_still_yields(self):
        self.budget.refusals[:] = [30]
        with patch.object(self.ns["time"], "sleep") as sleep, self.assertRaises(Deferred):
            self.call({"event": "result", "status": "done", "pipeline": "{}", "evidence": []})
        sleep.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_token_failure_defers_before_any_paid_call(self):
        def broken(audience):
            raise RuntimeError("metadata down")
        with self.assertRaises(Deferred) as caught:
            self.call(token=broken)
        self.assertEqual(caught.exception.reason, "remote_token_unavailable")
        self.assertEqual(self.sent, [])

    def test_unexpected_http_status_is_a_program_error(self):
        with self.assertRaises(self.ns["RemoteError"]):
            self.call(status=403)


if __name__ == "__main__":
    unittest.main()
