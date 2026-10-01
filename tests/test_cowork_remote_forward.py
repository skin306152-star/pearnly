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
    errors = types.ModuleType("pearnly_core.execution.errors")
    errors.Deferred, errors.Uncertain = Deferred, Uncertain
    budget = types.ModuleType("pearnly_core.execution.budget")
    budget.permits = []

    @contextmanager
    def permit(bucket, timeout, rpm=None):
        budget.permits.append((bucket, rpm, "in"))
        yield
        budget.permits.append((bucket, rpm, "out"))

    budget.permit, budget.scope = permit, lambda *a: "bucket:" + ":".join(a)
    pool = types.ModuleType("pearnly_core.execution.pool")
    pool.released = []
    pool.release_db = lambda: pool.released.append(len(budget.permits))
    work = types.ModuleType("pearnly_core.execution.work")
    work.JOB_TIMEOUT = 600
    ocr = types.ModuleType("pearnly_core.ocr")
    ocr.credentials_path = lambda: "/run/secrets/ocr.json"
    core = types.ModuleType("pearnly_core")
    core.ocr = ocr
    execution = types.ModuleType("pearnly_core.execution")
    execution.budget, execution.work, execution.pool = budget, work, pool
    client = types.ModuleType("pearnly_cowork.ocr.providers.client")

    class ProviderError(Exception):
        def __init__(self, code, *, http_status=None, provider_code=None):
            super().__init__(code)
            self.code, self.http_status, self.provider_code = code, http_status, provider_code

    client.ProviderError = ProviderError
    modules = {"frappe": frappe, "pearnly_core": core, "pearnly_core.ocr": ocr, "pearnly_core.execution": execution,
               "pearnly_core.execution.errors": errors, "pearnly_core.execution.budget": budget,
               "pearnly_core.execution.work": work, "pearnly_core.execution.pool": pool,
               "pearnly_cowork.ocr.providers.client": client}
    sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
    with patch.dict(sys.modules, modules):
        from pearnly_cowork.ocr.remote import contract
        namespace = {"__name__": "forward"}
        source = ROOT / "apps/pearnly_cowork/pearnly_cowork/ocr/remote/forward.py"
        exec(compile(source.read_text(), str(source), "exec"), namespace)
    namespace["_pool"] = pool
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
            self.call({"event": "result", "status": "error", "code": "EngineError"})

    def test_lost_transport_is_deferred_never_judged_here(self):
        for kwargs in ({"error": requests.ConnectionError("down")}, {"status": 503}, {"status": 429}):
            with self.assertRaises(Deferred):
                self.call(**kwargs)
        with self.assertRaises(Deferred) as cut:
            self.call({"event": "page", "page": 1})  # 实例中途没了：没有结果行
        self.assertEqual(cut.exception.reason, "remote_interrupted")

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
