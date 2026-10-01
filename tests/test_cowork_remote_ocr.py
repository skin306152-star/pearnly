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
from unittest.mock import patch

import requests
from PIL import Image
from requests.structures import CaseInsensitiveDict

from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_core.ocr_models.orient import orient
from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr import recon_reading
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


VAT_PAGE = {"seller_name": "ACME", "seller_tax": "", "period_year": "2026", "period_month": "06", "total_subtotal": "",
            "total_vat": "", "total_total": "", "entries": [
                {"seq_no": "1", "transaction_date": "2026-06-01", "invoice_no": "IV001", "customer_name": "B Co",
                 "customer_tax": "0105556012345", "customer_branch": "00000", "subtotal": "1000.00", "vat": "70.00",
                 "total": "1070.00"}]}
DOCUMENT_MODE = threading.local()


def docai_payload(text):
    return {"document": {"text": text, "pages": [{"pageNumber": 1, "layout": {"textAnchor": {"textSegments": [
        {"startIndex": "0", "endIndex": str(len(text))}]}}}]}}


def answer(url):
    if "documentai.googleapis.com" in url:
        return docai_payload("vat report text")
    if getattr(DOCUMENT_MODE, "on", False) and "aiplatform" in url:
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(VAT_PAGE)}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 30}}
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


def local(google, content=None, filename="invoice.png", pages=1, variant="intake"):
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
    model_call, vision_call = (recon_reading.vat_callbacks(client) if variant == "vat_check"
                               else (client.model_call, client.vision_call))
    with PageSource(content or png(), filename, expected_page_count=pages, enable_text_path=selected.text_fast_path,
                    image_max_long_edge=2400, orient=orient) as pages:
        result = run_invoice(pages, model_call, vision_call, config=config, anchor_year=2026,
                             today=date(2026, 9, 21))
    return result.pipeline.model_dump_json(), contract.evidence(result.calls)


class SameReading(unittest.TestCase):
    def test_cloud_run_reads_exactly_like_the_server(self):
        here, there = Google(), Google()
        pipeline, evidence = local(here)
        events = remote(Bucket(), there)
        final = events[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(final["throttled"], 0)
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

    def test_vat_check_reads_with_the_same_recon_wrappers_as_the_server(self):
        here, there = Google(), Google()
        pipeline, evidence = local(here, variant="vat_check")
        final = remote(Bucket(), there, variant="vat_check", yield_seconds=120)[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(reading(final["pipeline"]), reading(pipeline))
        self.assertEqual(final["evidence"], evidence)
        self.assertEqual(here.sent, there.sent)

    def test_unreadable_input_comes_back_as_an_error_line(self):
        self.assertEqual(remote(Bucket(), Google(), content=b"not an image at all", filename="x.png")[-1]["status"],
                         "error")

    def test_a_rerun_takes_completed_calls_back_without_paying_again(self):
        bucket, google = Bucket(), Google()
        first = remote(bucket, google)[-1]
        paid = len(google.sent)
        again = remote(bucket, google)[-1]
        self.assertEqual(len(google.sent), paid)
        self.assertEqual(reading(again["pipeline"]), reading(first["pipeline"]))

    def test_evidence_matches_the_workflow_digest(self):
        self.assertEqual(contract.encode({"b": 1, "a": "ก"}), '{"a":"ก","b":1}')


class PreviewBucket:
    """内存版原件桶：预览图只新建不覆盖（与 gcs.Bucket.create_bytes 同样的前置条件）。"""

    name = "pearnly-cowork-originals-sg"

    def __init__(self, fail=()):
        self.objects, self.lock, self.fail, self.created = {}, threading.Lock(), set(fail), []

    def metadata(self, name):
        if name in self.fail:
            raise requests.ConnectionError("synthetic")
        with self.lock:
            found = self.objects.get(name)
            return (found[1], found[2]) if found else None

    def create_bytes(self, name, data, content_type):
        with self.lock:
            if name in self.objects:
                return None
            self.objects[name] = (data, len(self.objects) + 100, content_type)
            self.created.append(name)
            return self.objects[name][1]


SOURCE = "CWS-" + "c" * 48


def with_previews(originals, content, filename, pages, kind, google=None, **body):
    import hashlib

    events = []
    spec = contract.previews(source=SOURCE, sha256=hashlib.sha256(content).hexdigest(), kind=kind)
    request = contract.request(site=SITE, work=WORK, batch=BATCH, filename=filename, content=content, page_count=pages,
                               anchor_date="2026-09-21", environment=dict(ENVIRONMENT), policy=dict(POLICY),
                               previews=spec, **body)
    runner.recognize(request, Bucket(), lambda: "token", events.append, orient=orient, page_source=PageSource,
                     http=google or Google(), threads=1, originals=originals)
    return events, spec


class StoredPreviews(unittest.TestCase):
    """识别时顺手把各页预览图存进原件桶：与服务器现画同一份 render_page（字节一致），识别结果与计费一字不差。"""

    def test_every_page_is_stored_with_the_same_bytes_the_server_would_draw(self):
        from pearnly_cowork.ocr import originals as original_files

        content = pdf(3)
        store = PreviewBucket()
        events, spec = with_previews(store, content, "file.pdf", 3, "pdf")
        final = events[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(final["previews"]["bucket"], PreviewBucket.name)
        self.assertEqual([item["page"] for item in final["previews"]["pages"]], [1, 2, 3])
        for item in final["previews"]["pages"]:
            data, generation, kind = store.objects[item["object"]]
            self.assertEqual(item["object"], contract.preview_object(SITE, SOURCE, spec["sha256"], item["page"]))
            self.assertEqual(data, original_files.render_page("pdf", content, item["page"]))
            self.assertEqual((item["generation"], item["type"]), (generation, original_files.preview_type(data)))
        bucket, pages = contract.parse_previews(final["previews"], site=SITE, source=SOURCE, sha256=spec["sha256"],
                                                page_count=3)
        self.assertEqual((bucket, sorted(pages)), (PreviewBucket.name, [1, 2, 3]))

    def test_storing_previews_does_not_change_the_reading_or_the_paid_calls(self):
        here, there = Google(), Google()
        pipeline, evidence = local(here)
        events, _spec = with_previews(PreviewBucket(), png(), "invoice.png", 1, "image", google=there)
        final = events[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(reading(final["pipeline"]), reading(pipeline))
        self.assertEqual(final["evidence"], evidence)
        self.assertEqual(here.sent, there.sent)

    def test_a_rerun_after_yielding_does_not_draw_the_pages_again(self):
        content, store = pdf(2), PreviewBucket()
        with_previews(store, content, "file.pdf", 2, "pdf")
        self.assertEqual(len(store.created), 2)
        events, _spec = with_previews(store, content, "file.pdf", 2, "pdf")
        self.assertEqual(len(store.created), 2)
        self.assertEqual([item["page"] for item in events[-1]["previews"]["pages"]], [1, 2])

    def test_a_page_that_cannot_be_stored_is_left_for_the_server_to_draw(self):
        import hashlib

        content = pdf(2)
        broken = contract.preview_object(SITE, SOURCE, hashlib.sha256(content).hexdigest(), 2)
        events, _spec = with_previews(PreviewBucket(fail={broken}), content, "file.pdf", 2, "pdf")
        self.assertEqual(events[-1]["status"], "done")
        self.assertEqual([item["page"] for item in events[-1]["previews"]["pages"]], [1])

    def test_a_broken_bucket_never_breaks_the_recognition(self):
        class Broken:
            @property
            def name(self):
                raise RuntimeError("synthetic")

            def metadata(self, name):
                raise RuntimeError("synthetic")

        events, _spec = with_previews(Broken(), png(), "invoice.png", 1, "image")
        self.assertEqual(events[-1]["status"], "done", events[-1])
        self.assertNotIn("previews", events[-1])

    def test_no_spec_or_no_bucket_stores_nothing(self):
        store = PreviewBucket()
        final = remote(Bucket(), Google())[-1]
        self.assertNotIn("previews", final)
        events = []
        request = contract.request(site=SITE, work=WORK, batch=BATCH, filename="invoice.png", content=png(), page_count=1,
                                   anchor_date="2026-09-21", environment=dict(ENVIRONMENT), policy=dict(POLICY))
        runner.recognize(request, Bucket(), lambda: "token", events.append, orient=orient, page_source=PageSource,
                         http=Google(), originals=store)
        self.assertNotIn("previews", events[-1])
        self.assertEqual(store.objects, {})

    def test_the_spec_must_match_the_content(self):
        spec = contract.previews(source=SOURCE, sha256="0" * 64, kind="image")
        for value in (spec, dict(spec, source="CWS-x"), dict(spec, kind="document"), dict(spec, extra=1)):
            with self.assertRaises(ValueError):
                contract.request(site=SITE, work=WORK, batch=BATCH, filename="x.png", content=png(), page_count=1,
                                 anchor_date="2026-09-21", environment={}, policy={}, previews=value)

    def test_returned_previews_are_checked_page_by_page(self):
        sha = "d" * 64
        good = {"page": 1, "object": contract.preview_object(SITE, SOURCE, sha, 1), "generation": 5, "type": "image/png"}
        check = lambda pages: contract.parse_previews({"bucket": "b", "pages": pages}, site=SITE, source=SOURCE,
                                                      sha256=sha, page_count=2)
        self.assertEqual(check([good])[1][1]["generation"], 5)
        for pages in ([dict(good, page=3)], [good, good], [dict(good, object=good["object"].replace("p1", "p2"))],
                      [dict(good, generation=0)], [dict(good, type="text/html")],
                      [dict(good, object=contract.preview_object("other.site", SOURCE, sha, 1))]):
            with self.assertRaises(ValueError):
                check(pages)


class DocumentReading(unittest.TestCase):
    """对账文档（企业档管线）：Cloud Run 与本机同一份读法，看板事件与本机回调同一时机。"""

    def setUp(self):
        DOCUMENT_MODE.on = True
        self.addCleanup(setattr, DOCUMENT_MODE, "on", False)

    def local_document(self, google):
        from functools import partial

        saved = {}

        def post(url, headers, payload, timeout_s, model, bucket, **_):
            identity = runner.checkpoint_key(payload, model)
            if identity not in saved:
                saved[identity] = json.loads(google.post(url, json=payload).raw.getvalue())
            return response(200, saved[identity])

        client = ProviderClient(dict(ENVIRONMENT), project="synthetic-project", post=post,
            checkpoint_key=runner.checkpoint_key, reject_checkpoint=lambda *a: None, terminal_read=lambda key: None,
            terminal_write=lambda *a: None, feedback=lambda *a: None, scope=lambda *a: a,
            access_token=lambda: "token", require_context=lambda: None)
        events = []
        with PageSource(png(), "report.png", expected_page_count=1, enable_text_path=False,
                        image_max_long_edge=2400, orient=orient) as source:
            def docai(*args, **kwargs):
                result = partial(client.document_ai_call, retry_unknown=True)(*args, **kwargs)
                events.append(("read", int(kwargs.get("expected_pages") or 1)))
                return result

            def model(request, *args, **kwargs):
                result = recon_reading.document_page_failure(partial(client.model_call, retry_unknown=True))(request)
                events.append(("extracted", request.page_number))
                return result

            legacy = recon_reading.read_document(source, png(), False, recon_reading.image_mime(png()), "vat_report",
                                                 docai, model)
        return legacy, events

    def remote_document(self, bucket, google):
        events = []
        request = contract.document_request(site=SITE, work=WORK, filename="report.png", content=png(), page_count=1,
                                            document_type="vat_report", environment=dict(ENVIRONMENT), yield_seconds=90)
        runner.recognize(request, bucket, lambda: "token", events.append, orient=orient, page_source=PageSource,
                         http=google)
        return events

    def test_cloud_run_reads_a_recon_document_exactly_like_the_server(self):
        here, there = Google(), Google()
        legacy, local_events = self.local_document(here)
        events = self.remote_document(Bucket(), there)
        final = events[-1]
        self.assertEqual(final["status"], "done", final)
        strip = lambda value: reading(json.dumps(value))
        self.assertEqual(strip(final["legacy"]), strip(legacy))
        self.assertEqual(here.sent, there.sent)
        self.assertEqual([(e["event"], e.get("pages") or e.get("page")) for e in events if e["event"] in ("read", "extracted")],
                         local_events)
        self.assertTrue(any(e["event"] == "extracted" and json.loads(e["text"])["entries"] for e in events))

    def test_a_rerun_of_a_document_pays_nothing_again(self):
        bucket, google = Bucket(), Google()
        self.remote_document(bucket, google)
        paid = len(google.sent)
        self.assertEqual(self.remote_document(bucket, google)[-1]["status"], "done")
        self.assertEqual(len(google.sent), paid)

    def test_document_requests_are_checked(self):
        for extra in ({"document_type": "invoice"}, {"yield_seconds": 5}):
            with self.assertRaises(ValueError):
                contract.document_request(**{"site": SITE, "work": WORK, "filename": "r.png", "content": b"x",
                    "page_count": 1, "document_type": "vat_report", "environment": {}, **extra})


class PagedGoogle(Google):
    """假的谷歌（对账文档）：Document AI 按送来的那一块有几页就回几页的字；记下每次真正发出的请求内容（摘要）。"""

    def __init__(self, plan=None):
        super().__init__(plan)
        self.payloads = []

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        import base64
        import hashlib
        import json as _json

        import pymupdf

        with self.lock:
            self.payloads.append((url.split("/")[2], hashlib.sha256(_json.dumps(json, sort_keys=True).encode()).hexdigest()))
        if "documentai.googleapis.com" in url:
            with self.lock:
                self.sent.append(url)
            raw = json["rawDocument"]
            count = 1
            if raw["mimeType"] == "application/pdf":
                with pymupdf.open(stream=base64.b64decode(raw["content"]), filetype="pdf") as document:
                    count = len(document)
            text, pages = "", []
            for number in range(1, count + 1):
                start, piece = len(text), f"vat report page text {number} "
                text += piece
                pages.append({"pageNumber": number, "layout": {"textAnchor": {"textSegments": [
                    {"startIndex": str(start), "endIndex": str(len(text))}]}}})
            return response(200, {"document": {"text": text, "pages": pages}})
        return super().post(url, headers=headers, json=json, timeout=timeout, stream=stream)


def staged(bucket, request, dispatch="d" * 32, path="/v1/cowork/document", progress=False):
    name = contract.task_object(SITE, WORK, dispatch, "request.json")
    generation = bucket.create(name, request)
    return contract.task(site=SITE, work=WORK, dispatch=dispatch, path=path, generation=generation,
                         callback=contract.callback_url(SITE), progress=progress)


def document_body(content, pages, filename="statement.pdf"):
    return contract.document_request(site=SITE, work=WORK, filename=filename, content=content, page_count=pages,
                                     document_type="vat_report", environment=dict(ENVIRONMENT), yield_seconds=90)


class TaskDelivery(unittest.TestCase):
    """交给 Cloud Tasks 的一份：读完写结果、回调站点；回调没送到让 Cloud Tasks 重试，重试只补回调、不重读不付费。"""

    def setUp(self):
        DOCUMENT_MODE.on = True
        self.addCleanup(setattr, DOCUMENT_MODE, "on", False)
        self.calls = []

    def post(self, statuses):
        def send(url, body, token):
            self.calls.append((url, body, token))
            return statuses.pop(0) if statuses else 200
        return send

    def task(self, bucket, envelope, google, statuses=None):
        return runner.task(envelope, bucket, lambda: "token", id_token=lambda audience: "ID:" + audience,
                           post=self.post(statuses or []), orient=orient, page_source=PageSource, http=google)

    def test_the_result_is_the_same_as_reading_line_by_line_and_comes_back_by_callback(self):
        content = pdf(3)
        streamed = []
        runner.recognize(document_body(content, 3), Bucket(), lambda: "token", streamed.append, orient=orient,
                         page_source=PageSource, http=PagedGoogle())
        bucket = Bucket()
        envelope = staged(bucket, document_body(content, 3))
        self.assertEqual(self.task(bucket, envelope, PagedGoogle()), 200)
        reply_name = contract.task_object(SITE, WORK, "d" * 32, "reply.json")
        final, events = contract.parse_reply(bucket.objects[reply_name][0])
        strip = lambda value: reading(json.dumps(value))
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(strip(final["legacy"]), strip(streamed[-1]["legacy"]))
        self.assertEqual([{k: v for k, v in e.items() if k != "seq"} for e in events],
                         [e for e in streamed if e["event"] not in ("result", "tick")])
        self.assertEqual([e["seq"] for e in events], list(range(len(events))))
        url, body, token = self.calls[-1]
        self.assertEqual((url, token), (contract.callback_url(SITE), "ID:" + contract.callback_url(SITE)))
        self.assertEqual(contract.parse_callback(body)["reply"], {"object": reply_name, "generation": 1})

    def test_a_lost_callback_is_retried_without_reading_or_paying_again(self):
        bucket, google = Bucket(), PagedGoogle()
        envelope = staged(bucket, document_body(pdf(2), 2))
        self.assertEqual(self.task(bucket, envelope, google, statuses=[0]), 503)
        paid = len(google.payloads)
        self.assertEqual(self.task(bucket, envelope, google), 200)
        self.assertEqual(len(google.payloads), paid)
        self.assertEqual([contract.parse_callback(c[1])["type"] for c in self.calls], ["reply", "reply"])
        # 只有 200（已记下）、404（站点已关开关）算送达；401 / 403 / 429 / 5xx 都让 Cloud Tasks 稍后重试，不悄悄丢掉结果
        for status, expected in ((404, 200), (401, 503), (403, 503), (429, 503), (500, 503)):
            self.assertEqual(self.task(bucket, envelope, google, statuses=[status]), expected, status)
        self.assertEqual(len(google.payloads), paid)

    def test_a_second_delivery_while_the_first_is_reading_writes_nothing_and_asks_to_retry(self):
        bucket, google = Bucket(), PagedGoogle()
        envelope = staged(bucket, document_body(pdf(1), 1))
        runner.Store(bucket, SITE, WORK).acquire()          # 第一次投递还在读（占着租约）
        self.assertEqual(self.task(bucket, envelope, google), 503)
        self.assertNotIn(contract.task_object(SITE, WORK, "d" * 32, "reply.json"), bucket.objects)
        self.assertEqual(google.payloads, [])
        self.assertEqual(self.calls, [])

    def test_a_bucket_or_token_hiccup_asks_to_retry_instead_of_failing(self):
        class Flaky(Bucket):
            def get(self, name):
                raise requests.ConnectionError("synthetic")

        self.assertEqual(self.task(Flaky(), staged(Bucket(), document_body(pdf(1), 1)), PagedGoogle()), 503)

    def test_a_workflow_that_gave_up_is_reported_so_the_site_dispatches_again(self):
        bucket = Bucket()
        envelope = staged(bucket, document_body(pdf(1), 1))
        self.assertEqual(runner.report_failed(envelope, bucket, id_token=lambda audience: "T",
                                              post=self.post([200])), 200)
        self.assertEqual(contract.parse_callback(self.calls[-1][1])["type"], "failed")
        reply = contract.task_object(SITE, WORK, "d" * 32, "reply.json")
        bucket.create(reply, contract.reply(contract.result("done", legacy={}), []))
        self.assertEqual(runner.report_failed(envelope, bucket, id_token=lambda audience: "T",
                                              post=self.post([503])), 503)
        self.assertEqual(contract.parse_callback(self.calls[-1][1])["type"], "reply")   # 其实读完了：改送结果

    def test_a_missing_or_foreign_request_comes_back_as_an_error_result(self):
        bucket = Bucket()
        envelope = staged(bucket, document_body(pdf(1), 1))
        bucket.objects.clear()
        self.assertEqual(self.task(bucket, envelope, PagedGoogle()), 200)
        reply = bucket.objects[contract.task_object(SITE, WORK, "d" * 32, "reply.json")][0]
        self.assertEqual(reply["result"]["code"], "task_request_missing")
        other = Bucket()
        foreign = dict(document_body(pdf(1), 1), work="f" * 64)
        envelope = staged(other, foreign)
        self.assertEqual(self.task(other, envelope, PagedGoogle()), 200)
        reply = other.objects[contract.task_object(SITE, WORK, "d" * 32, "reply.json")][0]
        self.assertEqual(reply["result"]["code"], "task_request_invalid")

    def test_an_invalid_envelope_is_refused(self):
        self.assertEqual(self.task(Bucket(), {"version": 1, "kind": "cowork.task"}, PagedGoogle()), 400)

    def test_task_delivery_may_read_longer_before_yielding_but_streaming_may_not(self):
        body = document_body(pdf(1), 1)
        envelope = staged(Bucket(), body)
        self.assertEqual(contract.task_request(dict(envelope, yield_seconds=1200), body)["yield_seconds"], 1200)
        with self.assertRaises(ValueError):
            contract.parse_request(dict(body, yield_seconds=1200))
        with self.assertRaises(ValueError):
            contract.parse_request(dict(body, delivery="task", yield_seconds=contract.TASK_YIELD_SECONDS + 1))

    def test_a_long_task_holds_its_lease_for_the_whole_reading(self):
        bucket = Bucket()
        envelope = staged(bucket, document_body(pdf(1), 1))
        runner.Store(bucket, SITE, WORK).acquire(now=lambda: 1000.0, seconds=envelope["yield_seconds"] + 240)
        with self.assertRaises(runner.Busy):   # 原来的 660 秒租约这时已过期，会被别的实例接手
            runner.Store(bucket, SITE, WORK).acquire(now=lambda: 1000.0 + runner.LEASE_SECONDS + 1)
        runner.Store(bucket, SITE, WORK).acquire(now=lambda: 1000.0 + envelope["yield_seconds"] + 241)

    def test_progress_goes_out_in_numbered_batches(self):
        sent = []
        envelope = staged(Bucket(), document_body(pdf(1), 1), progress=True)
        sender = runner.ProgressSender(envelope, lambda body: sent.append(body) or 200)
        for page in (1, 2, 3):
            sender.add({"event": "read", "pages": 1})
        sender._flush()
        sender.add({"event": "unread", "page": 3})
        sender._flush()
        sender._flush()   # 没有新事件不发
        self.assertEqual([(b["seq"], len(b["events"])) for b in sent], [(0, 3), (3, 1)])

    def test_progress_that_did_not_get_through_is_sent_again(self):
        sent, statuses = [], [0, 200]
        envelope = staged(Bucket(), document_body(pdf(1), 1), progress=True)
        sender = runner.ProgressSender(envelope, lambda body: sent.append(body) or statuses.pop(0))
        sender.add({"event": "read", "pages": 1})
        sender._flush()
        sender.add({"event": "unread", "page": 1})
        sender._flush()
        self.assertEqual([(b["seq"], len(b["events"])) for b in sent], [(0, 1), (0, 2)])


class WarmThenWhole(unittest.TestCase):
    """多页对账文档（Workflows）：各块读字、各页第一遍抽取分开预热，再原封不动读整份——结果与一次读完逐字相同，
    整份那一遍的第一遍调用全部从检查点取回（付费调用只剩按页序的再读），总共发出的请求与一次读完完全相同。"""

    def setUp(self):
        DOCUMENT_MODE.on = True
        self.addCleanup(setattr, DOCUMENT_MODE, "on", False)

    def warm(self, bucket, envelope, google, stage, index):
        return runner.warm(contract.warm(envelope, stage, index), bucket, lambda: "token", orient=orient,
                           page_source=PageSource, http=google)

    def test_warming_then_reading_whole_matches_one_reading_and_pays_nothing_twice(self):
        from pearnly_cowork.ocr.enterprise.pages import MAX_PAGES_PER_READ

        pages, content = 5, pdf(5)
        direct = PagedGoogle()
        streamed = []
        runner.recognize(document_body(content, pages), Bucket(), lambda: "token", streamed.append, orient=orient,
                         page_source=PageSource, http=direct)
        legacy = streamed[-1]["legacy"]
        rereads = sum(bool((p["extraction_audit"].get("self_check") or {}).get("reread")) for p in legacy["pages"])

        bucket, google = Bucket(), PagedGoogle()
        envelope = staged(bucket, document_body(content, pages))
        chunks = -(-pages // MAX_PAGES_PER_READ)
        for index in range(1, chunks + 1):
            self.assertEqual(self.warm(bucket, envelope, google, "read", index), {"status": "done"})
        for index in range(1, pages + 1):
            self.assertEqual(self.warm(bucket, envelope, google, "extract", index), {"status": "done"})
        warmed = len(google.payloads)
        calls = []
        status = runner.task(envelope, bucket, lambda: "token", id_token=lambda audience: "T",
                             post=lambda url, body, token: calls.append(body) or 200, orient=orient,
                             page_source=PageSource, http=google)
        self.assertEqual(status, 200)
        final, _events = contract.parse_reply(bucket.objects[contract.task_object(SITE, WORK, "d" * 32, "reply.json")][0])
        strip = lambda value: reading(json.dumps(value))
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(strip(final["legacy"]), strip(legacy))
        # 整份那一遍：读字与第一遍抽取全部从检查点取回，付费的只有按页序的再读
        self.assertEqual(len(google.payloads) - warmed, rereads)
        # 总共发出的请求与一次读完逐字相同（同样的内容、同样的次数）
        self.assertEqual(sorted(google.payloads), sorted(direct.payloads))

    def test_each_warm_step_tells_the_site_the_cloud_is_still_reading(self):
        bucket, google, sent = Bucket(), PagedGoogle(), []
        envelope = staged(bucket, document_body(pdf(2), 2))
        runner.warm(contract.warm(envelope, "read", 1), bucket, lambda: "token", orient=orient, page_source=PageSource,
                    http=google, id_token=lambda audience: "T", post=lambda url, body, token: sent.append(body) or 200)
        self.assertEqual([contract.parse_callback(body)["type"] for body in sent], ["alive"])

    def test_extracting_before_the_reads_are_done_waits_for_the_whole_reading(self):
        bucket, google = Bucket(), PagedGoogle()
        envelope = staged(bucket, document_body(pdf(3), 3))
        self.assertEqual(self.warm(bucket, envelope, google, "extract", 1), {"status": "not_ready"})
        self.assertEqual(google.payloads, [])

    def test_warming_a_call_that_is_already_in_flight_pays_nothing(self):
        import hashlib

        bucket, google = Bucket(), PagedGoogle()
        envelope = staged(bucket, document_body(pdf(2), 2))
        # 另一路正在读第 1 块（检查点「started」，还没回来）
        original = runner.Store.put

        def started_elsewhere(store, key, state, body=None):
            if state == "started":
                raise Uncertain("Provider call already started elsewhere")
            return original(store, key, state, body)

        with patch.object(runner.Store, "put", started_elsewhere):
            self.assertTrue(self.warm(bucket, envelope, google, "read", 1)["status"].startswith("error"))
        self.assertEqual(google.payloads, [])

    def test_pages_beyond_the_document_are_skipped(self):
        bucket, google = Bucket(), PagedGoogle()
        envelope = staged(bucket, document_body(pdf(2), 2))
        self.assertEqual(self.warm(bucket, envelope, google, "read", 9), {"status": "skipped"})
        self.assertEqual(google.payloads, [])
        self.assertEqual(runner.warm({"version": 1}, bucket, lambda: "t", orient=orient, page_source=PageSource)["status"],
                         "invalid")


class StorageRetries(unittest.TestCase):
    """谷歌存储读写遇连接重置 / 超时 / 429、5xx 原地再试（2026-10-01 半量压测：写「已完成」检查点时连接被重置）。"""

    class Session:
        def __init__(self, plan):
            self.plan, self.calls = list(plan), []

        def _next(self, method, url, **kwargs):
            self.calls.append((method, kwargs.get("params", {}).get("ifGenerationMatch")))
            step = self.plan.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        def get(self, url, **kwargs):
            return self._next("get", url, **kwargs)

        def post(self, url, **kwargs):
            return self._next("post", url, **kwargs)

        def delete(self, url, **kwargs):
            return self._next("delete", url, **kwargs)

    @staticmethod
    def reply(status, body=None, generation=None):
        result = requests.Response()
        result.status_code = status
        result._content = json.dumps(body if body is not None else {}).encode()
        result.headers = CaseInsensitiveDict({"x-goog-generation": str(generation)} if generation else {})
        return result

    def bucket(self, plan):
        from pearnly_cowork.ocr.remote import gcs

        session = self.Session(plan)
        return gcs.Bucket("b", lambda: "token", http=session, sleep=lambda s: None), session

    def test_a_reset_write_is_written_again(self):
        bucket, session = self.bucket([requests.ConnectionError("reset by peer"), self.reply(200, {"generation": "7"})])
        self.assertEqual(bucket.put("k", {"state": "done"}), 7)
        self.assertEqual(len(session.calls), 2)

    def test_a_conditional_write_that_landed_before_the_reset_is_recognised(self):
        value = {"state": "started"}
        bucket, _session = self.bucket([requests.ConnectionError("reset"), self.reply(412),
                                        self.reply(200, value, generation=9)])
        self.assertEqual(bucket.create("k", value), 9)            # 前一次其实写进去了：读回一致
        bucket, _session = self.bucket([requests.ConnectionError("reset"), self.reply(412),
                                        self.reply(200, {"state": "other"}, generation=9)])
        self.assertIsNone(bucket.create("k", value))              # 别人写的：照旧当已存在
        bucket, _session = self.bucket([self.reply(412)])
        self.assertIsNone(bucket.create("k", value))              # 没断过：412 就是已存在，不读回

    def test_reads_ride_out_resets_and_server_errors_but_not_forever(self):
        bucket, _session = self.bucket([requests.Timeout("slow"), self.reply(503), self.reply(200, {"a": 1}, 3)])
        self.assertEqual(bucket.get("k"), ({"a": 1}, 3))
        bucket, _session = self.bucket([requests.ConnectionError("reset")] * 3)
        with self.assertRaises(requests.ConnectionError):
            bucket.get("k")

    def test_a_store_that_stays_down_defers_the_reading_instead_of_failing_it(self):
        class Down(Bucket):
            def put(self, name, value, if_generation=None):
                if "/cp/" in name:
                    raise requests.ConnectionError("reset by peer")
                return super().put(name, value, if_generation)

            def create(self, name, value):
                if "/cp/" in name:
                    raise requests.ConnectionError("reset by peer")
                return super().create(name, value)

        final = remote(Down(), Google())[-1]
        self.assertEqual((final["status"], final["reason"]), ("deferred", "remote_store_unavailable"))


class PaidCallGuard(unittest.TestCase):
    def test_a_rate_limited_global_entry_switches_to_the_us_entry_at_once(self):
        google = Google(["429"])
        final = remote(Bucket(), google)[-1]
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["throttled"], 1)          # 挨了几次 429 随结果交回（服务器据此调各池派发上限）
        self.assertEqual(final["calls"], 2)              # 发给谷歌几次（窗口内算 429 比例）
        self.assertIn("aiplatform.googleapis.com", google.sent[0])
        self.assertIn("aiplatform.us.rep.googleapis.com", google.sent[1])

    def test_global_and_us_rate_limited_go_on_to_the_eu_entry_without_waiting(self):
        google = Google(["429", "429"])
        with patch.object(runner.time, "sleep") as sleep:
            final = remote(Bucket(), google)[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(sleep.call_count, 0)
        self.assertIn("aiplatform.us.rep.googleapis.com", google.sent[1])
        self.assertIn("aiplatform.eu.rep.googleapis.com", google.sent[2])

    def test_all_entries_rate_limited_wait_in_place_like_the_server(self):
        # 与本机 provider.post 同一规则：全球、美国被拒立刻转下一个（不等），最后的欧洲入口被拒才原地短等再发
        google = Google(["429", "429", "429"])
        with patch.object(runner.time, "sleep") as sleep:
            final = remote(Bucket(), google)[-1]
        self.assertEqual(final["status"], "done", final)
        self.assertEqual(sleep.call_count, 1)
        self.assertIn("aiplatform.eu.rep.googleapis.com", google.sent[3])

    def test_a_long_rate_limit_is_handed_back_after_the_wait_budget(self):
        bucket, google = Bucket(), Google(["429"] * 40)
        with patch.object(runner.time, "sleep") as sleep:
            final = remote(bucket, google)[-1]
        self.assertEqual(final["status"], "deferred")
        self.assertTrue(final["reason"].startswith("provider_"))
        self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), runner.WAIT_PER_CALL)

    def test_a_dropped_connection_is_resent_once_and_never_replayed(self):
        # 2026-10-01 用户：传输错误也要重试——结果不明重发一次（新的检查点），续跑时直接取回，不再付费。
        bucket, google = Bucket(), Google(["drop"])
        self.assertEqual(remote(bucket, google)[-1]["status"], "done")
        paid = len(google.sent)
        self.assertEqual(remote(bucket, google)[-1]["status"], "done")
        self.assertEqual(len(google.sent), paid)
        # 重发那次又断：判结果不明，之后也不再重放付费调用
        bucket, google = Bucket(), Google(["drop", "drop"])
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

    def test_a_lease_taken_over_by_another_instance_is_not_released_by_the_old_one(self):
        class Versioned(Bucket):
            def delete_generation(self, name, generation):
                with self.lock:
                    if name in self.objects and self.objects[name][1] == generation:
                        self.objects.pop(name)

        bucket = Versioned()
        old = runner.Store(bucket, SITE, WORK)
        old.acquire(now=lambda: 1000)
        new = runner.Store(bucket, SITE, WORK)
        new.acquire(now=lambda: 1000 + runner.LEASE_SECONDS + 1)   # 过期后被接手
        old.release()
        self.assertIn(f"{SITE}/{WORK}/lease", bucket.objects)     # 新实例的租约还在
        new.release()
        self.assertNotIn(f"{SITE}/{WORK}/lease", bucket.objects)

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

    def test_variant_and_yield_are_checked(self):
        for extra in ({"variant": "other"}, {"yield_seconds": 5}, {"yield_seconds": 900}):
            with self.assertRaises(ValueError):
                contract.request(site=SITE, work=WORK, batch=BATCH, filename="x.png", content=b"x", page_count=1,
                                 anchor_date="2026-09-21", environment={}, policy={}, **extra)

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


class Originals:
    """内存版原件桶（ADR-016）：对象 → (字节, 版本号)；与 gcs.Bucket.read_bytes / delete_generation 同样的行为。"""

    def __init__(self, **objects):
        self.objects = {name: (content, 7) for name, content in objects.items()}
        self.deleted = []

    def read_bytes(self, name, generation=None):
        found = self.objects.get(name)
        if found is None or (generation is not None and found[1] != generation):
            return None
        return found

    def delete_generation(self, name, generation):
        self.deleted.append((name, generation))
        if self.objects.get(name, (None, None))[1] == generation:
            del self.objects[name]


OBJECT = SITE + "/originals/CWS-" + "c" * 48 + ".png"


class DirectOriginals(unittest.TestCase):
    """浏览器直传到原件桶的原件（ADR-016）：读一遍核对、识别时按对象读，结果与带字节发来一字不差。"""

    def inspect(self, originals, filename="invoice.png", name=OBJECT):
        return runner.inspect(contract.inspect_request(site=SITE, object=name, filename=filename), originals)

    def test_inspect_gives_the_same_facts_as_the_server_check(self):
        import hashlib
        from pearnly_cowork.ocr import originals as checks

        content = pdf(3)
        name = OBJECT.replace(".png", ".pdf")
        result = self.inspect(Originals(**{name: content}), "scan.pdf", name)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["meta"], checks.inspect("scan.pdf", content))
        self.assertEqual(result["meta"]["page_count"], 3)
        self.assertEqual(result["generation"], 7)
        self.assertEqual(result["md5"], hashlib.md5(content).hexdigest())

    def test_a_bad_original_is_rejected_with_the_server_code_and_removed(self):
        for filename, content, code in (("scan.pdf", b"%PDF-1.7 broken", "pdf_unreadable"),
                                        ("photo.png", b"not an image", "image_unreadable")):
            name = OBJECT.replace(".png", "." + filename.rsplit(".", 1)[1])
            originals = Originals(**{name: content})
            result = self.inspect(originals, filename, name)
            self.assertEqual((result["status"], result["code"]), ("rejected", code))
            self.assertEqual(originals.deleted, [(name, 7)])   # 删掉后页面才能用同一个键重传
            self.assertNotIn(name, originals.objects)

    def test_a_missing_upload_and_foreign_objects_are_refused(self):
        self.assertEqual(self.inspect(Originals())["code"], "original_missing")
        for name in ("other.pearnly.com/originals/CWS-" + "c" * 48 + ".png", SITE + "/work/cp/x.json",
                     SITE + "/originals/../x.png"):
            with self.assertRaises(ValueError):
                contract.inspect_request(site=SITE, object=name, filename="invoice.png")

    def test_recognizing_from_the_bucket_reads_exactly_like_sending_the_bytes(self):
        import hashlib

        content = png()
        inline = remote(Bucket(), Google(), content)[-1]
        original = contract.original(object=OBJECT, generation=7, sha256=hashlib.sha256(content).hexdigest())
        request = contract.request(site=SITE, work=WORK, batch=BATCH, filename="invoice.png", page_count=1,
            anchor_date="2026-09-21", environment=dict(ENVIRONMENT), policy=dict(POLICY), original=original)
        self.assertNotIn("content", request)
        events = []
        runner.recognize(request, Bucket(), lambda: "token", events.append, orient=orient, page_source=PageSource,
                         http=Google(), originals=Originals(**{OBJECT: content}))
        self.assertEqual(events[-1]["status"], "done", events[-1])
        self.assertEqual(reading(events[-1]["pipeline"]), reading(inline["pipeline"]))
        self.assertEqual(events[-1]["evidence"], inline["evidence"])

    def test_a_preview_on_cloud_run_is_the_same_picture_the_server_draws(self):
        import hashlib
        from pearnly_cowork.ocr import originals as checks

        content = pdf(2)
        name = OBJECT.replace(".png", ".pdf")
        body = contract.preview_request(site=SITE, object=name, generation=7,
                                        sha256=hashlib.sha256(content).hexdigest(), kind="pdf", page=2)
        picture, code = runner.preview(body, Originals(**{name: content}))
        self.assertIsNone(code)
        self.assertEqual(picture, checks.render_page("pdf", content, 2))
        self.assertEqual(runner.preview(dict(body, page=1, original=dict(body["original"], sha256="0" * 64)),
                                        Originals(**{name: content})), (None, "original_mismatch"))
        photo = png()
        body = contract.preview_request(site=SITE, object=OBJECT, generation=7,
                                        sha256=hashlib.sha256(photo).hexdigest(), kind="image", page=2)
        self.assertEqual(runner.preview(body, Originals(**{OBJECT: photo})), (None, "preview_unsupported"))

    def test_a_large_photo_preview_is_sent_as_jpeg_and_text_pages_stay_png(self):
        import os
        from pearnly_cowork.ocr import originals as checks

        noisy = Image.frombytes("RGB", (1600, 1200), os.urandom(1600 * 1200 * 3))
        out = io.BytesIO()
        noisy.save(out, "JPEG", quality=95)
        picture = checks.render_page("image", out.getvalue(), 1)
        self.assertEqual(checks.preview_type(picture), "image/jpeg")
        self.assertLess(len(picture), len(out.getvalue()))
        page = checks.render_page("pdf", pdf(1), 1)
        self.assertEqual(checks.preview_type(page), "image/png")
        self.assertLessEqual(len(page), checks.PREVIEW_PNG_LIMIT)

    def test_a_changed_or_missing_original_is_never_read(self):
        google = Google()
        original = contract.original(object=OBJECT, generation=7, sha256="0" * 64)
        request = contract.request(site=SITE, work=WORK, batch=BATCH, filename="invoice.png", page_count=1,
            anchor_date="2026-09-21", environment=dict(ENVIRONMENT), policy=dict(POLICY), original=original)
        for originals, code in ((Originals(**{OBJECT: png()}), "original_mismatch"), (Originals(), "original_missing")):
            events = []
            runner.recognize(request, Bucket(), lambda: "token", events.append, orient=orient,
                             page_source=PageSource, http=google, originals=originals)
            self.assertEqual((events[-1]["status"], events[-1]["code"]), ("error", code))
        self.assertEqual(google.sent, [])   # 没付费


if __name__ == "__main__":
    unittest.main()
