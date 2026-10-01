#!/usr/bin/env python3
"""No Frappe, no network, no credentials: Cowork invoice recognition on Cloud Run (ADR-0015).

- 同一张原件、同样的谷歌回复：Cloud Run 执行与本机执行得出一字不差的结果和计费证据（读法不变）。
- 存储桶检查点：已完成的直接取回不再付费；残留「started」判结果不明；两个实例抢同一调用只有一个能发出。
- 同一份单据同时只有一个实例在跑（租约），租约过期后可接手。
- 谷歌限流交回暂缓、不原地等；各种异常都变成结果行，不会让连接无结果断开。
"""
from copy import deepcopy
from datetime import date
import io
import json
import threading
import unittest

import requests
from PIL import Image
from requests.structures import CaseInsensitiveDict

from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_core.ocr_models.orient import orient
from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers.client import ProviderClient
from pearnly_cowork.ocr.remote import contract, runner

SITE, WORK, BATCH = "acme.pearnly.com", "a" * 64, "b" * 64
ENVIRONMENT = {"OCR_LLM_BACKEND": "vertex", "GCP_PROJECT": "synthetic-project", "VERTEX_LOCATION": "global"}
POLICY = {"mode": "economy"}
GOOD = {"document_type": "receipt", "invoice_number": "IV69/100", "subtotal": "100", "vat": "7",
        "total_amount": "107", "date": "2026-09-21"}
TEXT = "Receipt IV69/100 subtotal 100 VAT 7 total 107 2026-09-21"


def reading(pipeline):
    """结果里除了计时（``*_ms``，随机器快慢变化）以外的全部内容：读法相同就必须逐字相同。"""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if not k.endswith("_ms")}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return strip(json.loads(pipeline))


def pdf(pages):
    out = io.BytesIO()
    images = [Image.new("RGB", (640, 900), "white") for _ in range(pages)]
    images[0].save(out, "PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


def png():
    out = io.BytesIO()
    Image.new("RGB", (640, 900), "white").save(out, "PNG")
    return out.getvalue()


def answer(url):
    if "vision.googleapis.com" in url:
        return {"responses": [{"fullTextAnnotation": {"text": TEXT, "pages": []}}]}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 40}, "modelVersion": "synthetic"}


def response(status, body, headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers = CaseInsensitiveDict(headers or {})
    result.raw = io.BytesIO(json.dumps(body).encode())
    return result


class Google:
    """假的谷歌：按地址回 Vision / Gemini 的回复；可以指定一次 429 或连接中断。记下每次真正发出的请求。"""

    def __init__(self, plan=None):
        self.plan, self.sent = list(plan or []), []
        self.lock = threading.Lock()

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        with self.lock:
            self.sent.append(url)
            step = self.plan.pop(0) if self.plan else None
        if step == "429":
            return response(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
        if step == "drop":
            raise requests.ConnectionError("synthetic reset")
        return response(200, answer(url))


class Bucket:
    """内存版存储桶：与 gcs.Bucket 同样的 get / create / put / delete 与 generation 前置条件。"""

    def __init__(self):
        self.objects, self.lock = {}, threading.Lock()

    def get(self, name):
        with self.lock:
            found = self.objects.get(name)
            return (deepcopy(found[0]), found[1]) if found else None

    def create(self, name, value):
        with self.lock:
            if name in self.objects:
                return None
            self.objects[name] = (deepcopy(value), 1)
            return 1

    def put(self, name, value, if_generation=None):
        with self.lock:
            current = self.objects.get(name)
            if if_generation is not None and (current[1] if current else 0) != if_generation:
                return None
            generation = (current[1] if current else 0) + 1
            self.objects[name] = (deepcopy(value), generation)
            return generation

    def delete(self, name):
        with self.lock:
            self.objects.pop(name, None)


def remote(bucket, google, content=None, filename="invoice.png", pages=1, threads=1, **body):
    events = []
    request = contract.request(site=SITE, work=WORK, batch=BATCH, filename=filename, content=content or png(),
                               page_count=pages, anchor_date="2026-09-21", environment=dict(ENVIRONMENT),
                               policy=dict(POLICY), **body)
    runner.recognize(request, bucket, lambda: "token", events.append, orient=orient, page_source=PageSource,
                     http=google, threads=threads)
    return events


def local(google, content=None, filename="invoice.png", pages=1):
    """本机执行同一流程（workflow._recognize 的引擎部分），付费调用走同样的假谷歌。"""
    saved = {}

    def post(url, headers, payload, timeout_s, model, bucket, **_):
        identity = runner.checkpoint_key(payload, model)
        if identity in saved:
            return response(200, saved[identity])
        result = google.post(url, json=payload)
        body = json.loads(result.raw.getvalue())
        saved[identity] = body
        return response(200, body)

    client = ProviderClient(dict(ENVIRONMENT), project="synthetic-project", post=post,
        checkpoint_key=runner.checkpoint_key, reject_checkpoint=lambda *a: None, terminal_read=lambda key: None,
        terminal_write=lambda *a: None, feedback=lambda *a: None, scope=lambda *a: a,
        access_token=lambda: "token", require_context=lambda: None)
    selected = invoice_policy(ENVIRONMENT, POLICY)
    config = config_from_environment(ENVIRONMENT, selected)
    with PageSource(content or png(), filename, expected_page_count=pages, enable_text_path=selected.text_fast_path,
                    image_max_long_edge=2400, orient=orient) as pages:
        result = run_invoice(pages, client.model_call, client.vision_call, config=config, anchor_year=2026,
                             today=date(2026, 9, 21))
    return result.pipeline.model_dump_json(), contract.evidence(result.calls)


class SameReading(unittest.TestCase):
    def test_cloud_run_reads_exactly_like_the_server(self):
        here, there = Google(), Google()
        pipeline, evidence = local(here)
        events = remote(Bucket(), there)
        final = events[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(reading(final["pipeline"]), reading(pipeline))
        self.assertEqual(final["evidence"], evidence)
        self.assertEqual(here.sent, there.sent)
        self.assertEqual([e for e in events if e["event"] == "page"], [{"event": "page", "page": 1}])

    def test_pages_read_at_the_same_time_match_reading_one_by_one(self):
        content = pdf(4)
        pipeline, evidence = local(Google(), content, "file.pdf", 4)
        events = remote(Bucket(), Google(), content, "file.pdf", 4, threads=4)
        final = events[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(reading(final["pipeline"]), reading(pipeline))
        self.assertEqual(final["evidence"], evidence)
        self.assertEqual(sorted(e["page"] for e in events if e["event"] == "page"), [2, 3, 4, 5])

    def test_a_rerun_takes_completed_calls_back_without_paying_again(self):
        bucket, google = Bucket(), Google()
        first = remote(bucket, google)[-1]
        paid = len(google.sent)
        again = remote(bucket, google)[-1]
        self.assertEqual(len(google.sent), paid)
        self.assertEqual(reading(again["pipeline"]), reading(first["pipeline"]))

    def test_evidence_matches_the_workflow_digest(self):
        self.assertEqual(contract.encode({"b": 1, "a": "ก"}), '{"a":"ก","b":1}')


class PaidCallGuard(unittest.TestCase):
    def test_a_rate_limited_global_entry_switches_to_the_us_entry_at_once(self):
        google = Google(["429"])
        final = remote(Bucket(), google)[-1]
        self.assertEqual(final["status"], "done")
        self.assertIn("aiplatform.googleapis.com", google.sent[0])
        self.assertIn("aiplatform.us.rep.googleapis.com", google.sent[1])

    def test_google_rate_limit_on_both_entries_is_handed_back_not_waited(self):
        bucket, google = Bucket(), Google(["429", "429"])
        final = remote(bucket, google)[-1]
        self.assertEqual(final["status"], "deferred")
        self.assertTrue(final["reason"].startswith("provider_"))
        self.assertEqual(len(google.sent), 2)
        retry = remote(bucket, google)[-1]
        self.assertEqual(retry["status"], "done")

    def test_a_dropped_connection_is_uncertain_and_never_replayed(self):
        bucket, google = Bucket(), Google(["drop"])
        self.assertEqual(remote(bucket, google)[-1]["status"], "uncertain")
        paid = len(google.sent)
        self.assertEqual(remote(bucket, google)[-1]["status"], "uncertain")
        self.assertEqual(len(google.sent), paid)

    def test_only_one_instance_can_start_the_same_call(self):
        bucket = Bucket()
        one, two = runner.Store(bucket, SITE, WORK), runner.Store(bucket, SITE, WORK)
        self.assertIsNone(one.get("k"))
        self.assertIsNone(two.get("k"))
        one.put("k", "started")
        with self.assertRaises(Uncertain):
            two.put("k", "started")

    def test_a_rejected_call_can_be_started_again_by_its_owner(self):
        store = runner.Store(Bucket(), SITE, WORK)
        store.put("k", "started")
        store.put("k", "rejected", {"http": 429})
        store.put("k", "started")
        self.assertEqual(store.get("k")["state"], "started")


class Lease(unittest.TestCase):
    def test_a_second_run_of_the_same_work_waits(self):
        bucket = Bucket()
        runner.Store(bucket, SITE, WORK).acquire(now=lambda: 1000)
        with self.assertRaises(runner.Busy):
            runner.Store(bucket, SITE, WORK).acquire(now=lambda: 1001)

    def test_an_expired_lease_is_taken_over(self):
        bucket = Bucket()
        runner.Store(bucket, SITE, WORK).acquire(now=lambda: 1000)
        runner.Store(bucket, SITE, WORK).acquire(now=lambda: 1000 + runner.LEASE_SECONDS + 1)

    def test_a_busy_work_is_deferred(self):
        bucket = Bucket()
        runner.Store(bucket, SITE, WORK).acquire()
        final = remote(bucket, Google())[-1]
        self.assertEqual((final["status"], final["reason"]), ("deferred", "remote_busy"))

    def test_the_lease_is_released_after_a_run(self):
        bucket = Bucket()
        remote(bucket, Google())
        self.assertNotIn(f"{SITE}/{WORK}/lease", bucket.objects)


class Requests(unittest.TestCase):
    def test_bad_requests_are_refused_as_a_result_line(self):
        events = []
        runner.recognize({"version": 99}, Bucket(), lambda: "t", events.append, orient=orient,
                         page_source=PageSource)
        self.assertEqual(events[-1]["status"], "error")

    def test_request_shape_is_checked(self):
        with self.assertRaises(ValueError):
            contract.request(site=SITE, work="short", batch=BATCH, filename="x.png", content=b"x", page_count=1,
                             anchor_date="2026-09-21", environment={}, policy={})
        with self.assertRaises(ValueError):
            contract.request(site=SITE, work=WORK, batch=BATCH, filename="x.png", content=b"",
                             page_count=1, anchor_date="2026-09-21", environment={}, policy={})

    def test_stream_always_ends_with_a_result(self):
        request = contract.request(site=SITE, work=WORK, batch=BATCH, filename="invoice.png", content=png(),
                                   page_count=1, anchor_date="2026-09-21", environment=dict(ENVIRONMENT),
                                   policy=dict(POLICY))
        lines = [contract.parse_line(raw) for raw in runner.stream(request, Bucket(), lambda: "t", orient=orient,
                                                                    page_source=PageSource, http=Google())]
        self.assertEqual(lines[-1]["event"], "result")
        self.assertEqual(lines[-1]["status"], "done")


if __name__ == "__main__":
    unittest.main()
