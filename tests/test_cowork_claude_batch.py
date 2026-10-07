"""Claude 批处理识别（ADR-018）：收集的请求键 = 实时检查点键；结果预填后照常识别不再付费；出不了批的退回实时。

Run with Frappe installed. No Site, database, network or provider: Frappe collaborators, the store, the work queue and
HTTP are replaced by fakes.
"""
from datetime import date
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from pearnly_core.execution.errors import QueueFull
from pearnly_cowork.ocr.engine import Frame, run_invoice
from pearnly_cowork.ocr.engine.config import CLAUDE_MODEL, config_from_environment
from pearnly_cowork.ocr.engine.cost import THB_PER_USD, response_cost_thb
from pearnly_cowork.ocr.execution import batch, service
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers import anthropic, anthropic_batch
from pearnly_cowork.ocr.providers.client import ProviderError

TODAY = date(2026, 10, 7)
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16
TARGET = {"base_url": "https://api.anthropic.com", "auth": "x-api-key", "models": {}}
CLAUDE_ENV = {"OCR_LLM_BACKEND": "vertex", "OCR_ENGINE": "claude"}
GOOD = {"document_type": "tax_invoice", "invoice_number": "IV69/100", "subtotal": "100", "vat": "7",
        "total_amount": "107", "date": "2026-10-07", "seller_tax": "0105546015062"}


def key_of(payload, identity):
    import hashlib
    return hashlib.sha256((identity + "\0" + json.dumps(payload, sort_keys=True)).encode()).hexdigest()


def message(text):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 2000, "output_tokens": 300, "cache_read_input_tokens": 2135,
                      "cache_creation_input_tokens": 0, "service_tier": "batch"}}


class Reply:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def client(post=None, store=None):
    """AnthropicClient 与站点同样的注入：检查点键 = Core 的算法；``store`` 里有的键当成已完成的检查点。"""
    store = {} if store is None else store
    sent = []

    def fake_post(*, url, headers, payload, timeout_s, model, bucket, label):
        key = key_of(payload, model)
        if key in store:  # Core post：检查点已完成就直接回存的结果，不发出
            return Reply(200, store[key])
        sent.append(key)
        if post is None:
            raise AssertionError("a paid call was sent although its result was prefilled")
        return post(payload)

    made = anthropic.AnthropicClient(TARGET, post=fake_post, checkpoint_key=key_of,
        reject_checkpoint=lambda key, body: None, terminal_read=lambda key: None,
        terminal_write=lambda key, body: None, feedback=lambda *a: None, scope=lambda *a: "bucket",
        api_key=lambda: "sk-test", require_context=lambda: None, clock=lambda: 0.0)
    return made, sent


class Source:
    def __init__(self, count):
        self.page_count, self.text_pages = count, None

    def frame(self, number):
        return Frame(PNG + bytes([number]), "image/png")


def config():
    return config_from_environment(CLAUDE_ENV, invoice_policy(CLAUDE_ENV, {"mode": "economy"}))


def no_vision(frame, *, stage, page_number):
    raise AssertionError("Claude reads never call Vision")


class EntryTests(unittest.TestCase):
    def test_a_batch_entry_key_is_the_realtime_checkpoint_key(self):
        request = anthropic.ModelRequest("direct", 2, 0, CLAUDE_MODEL, "SYSTEM", ((PNG, "image/png"),), 8192, 60)
        made, sent = client(post=lambda payload: Reply(200, message(json.dumps(GOOD))))
        key, body = made.batch_entry(request)
        self.assertEqual(body, made.payload(request))
        made.model_call(request)
        self.assertEqual(sent, [key])

    def test_collected_requests_are_answered_from_prefilled_checkpoints_without_paying(self):
        made, _ = client()
        entries = {}

        class Collected(Exception):
            pass

        def take(request):
            key, body = made.batch_entry(request)
            entries[key] = body
            raise Collected()

        def every_page(fn, numbers):
            for number in numbers:
                try:
                    fn(number)
                except Collected:
                    pass
            raise Collected()

        with self.assertRaises(Collected):
            run_invoice(Source(3), take, no_vision, config=config(), anchor_year=2026, today=TODAY,
                        map_pages=every_page)
        self.assertEqual(len(entries), 3)
        prefilled = {key: message(json.dumps({**GOOD, "invoice_number": f"IV69/{n}"}))
                     for n, key in enumerate(sorted(entries), 1)}
        paying, sent = client(store=prefilled)
        result = run_invoice(Source(3), paying.model_call, no_vision, config=config(), anchor_year=2026, today=TODAY,
                             map_pages=lambda fn, numbers: [fn(n) for n in numbers])
        self.assertEqual(sent, [])  # 每页都从检查点取回
        self.assertEqual(len(result.pipeline.pages), 3)

    def test_a_page_without_a_batch_result_is_read_in_realtime(self):
        made, _ = client()
        request = anthropic.ModelRequest("direct", 1, 0, CLAUDE_MODEL, "SYSTEM", ((PNG, "image/png"),), 8192, 60)
        key, _body = made.batch_entry(request)
        paying, sent = client(post=lambda payload: Reply(200, message(json.dumps(GOOD))), store={})
        paying.model_call(request)
        self.assertEqual(sent, [key])

    def test_batch_usage_is_priced_at_half(self):
        reply = anthropic.parse_response(message("{}"), CLAUDE_MODEL)
        full = dict(message("{}"), usage={**message("{}")["usage"], "service_tier": "standard"})
        self.assertAlmostEqual(response_cost_thb(reply) * 2,
                               response_cost_thb(anthropic.parse_response(full, CLAUDE_MODEL)), places=5)
        self.assertGreater(THB_PER_USD, 0)


class ProtocolTests(unittest.TestCase):
    def test_create_sends_checkpoint_keys_as_custom_ids(self):
        calls = []

        def post(url, headers, json, timeout):
            calls.append((url, json))
            return Reply(200, {"id": "msgbatch_1", "processing_status": "in_progress"})

        created = anthropic_batch.create(post, TARGET, {"x-api-key": "k"}, [("a" * 64, {"model": "m"})])
        self.assertEqual(created["id"], "msgbatch_1")
        self.assertEqual(calls[0][0], "https://api.anthropic.com/v1/messages/batches")
        self.assertEqual(calls[0][1], {"requests": [{"custom_id": "a" * 64, "params": {"model": "m"}}]})
        for bad in ([], [("x" * 65, {})], [("a", {}), ("a", {})]):
            with self.assertRaises(ValueError):
                anthropic_batch.create(post, TARGET, {}, bad)

    def test_refusals_and_bad_replies_are_provider_errors(self):
        with self.assertRaises(ProviderError) as refused:
            anthropic_batch.create(lambda *a, **k: Reply(401, {"type": "error"}), TARGET, {}, [("a", {})])
        self.assertEqual(refused.exception.code, "ocr.provider_auth")
        with self.assertRaises(ProviderError):
            anthropic_batch.retrieve(lambda *a, **k: Reply(200, None), TARGET, {}, "msgbatch_1")

    def test_results_keep_only_recognised_lines(self):
        text = "\n".join([
            json.dumps({"custom_id": "k1", "result": {"type": "succeeded", "message": message("{}")}}),
            json.dumps({"custom_id": "k2", "result": {"type": "errored", "error": {"type": "invalid_request"}}}),
            json.dumps({"custom_id": "k3", "result": {"type": "expired"}}),
            "not json", json.dumps({"result": {}}), ""])
        found = anthropic_batch.parse_results(text)
        self.assertEqual({k: v[0] for k, v in found.items()}, {"k1": "succeeded", "k2": "errored", "k3": "expired"})
        batch_ = {"id": "msgbatch_1", "processing_status": "ended", "results_url": "https://x/results"}
        got = anthropic_batch.results(lambda url, **k: Reply(200, text=text), TARGET, {}, batch_)
        self.assertIn("k1", got)
        with self.assertRaises(ValueError):
            anthropic_batch.results(lambda *a, **k: None, TARGET, {}, {"processing_status": "in_progress"})


class Doc(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)


def doc(**values):
    base = dict(name="B1", phase=service.BATCH_WAITING, provider_batch="", settings_json=json.dumps({"anthropic": TARGET}),
                examples_json="", actor="a@x", work_name="w" * 64, creation="2026-10-07 00:00:00", delivery="batch")
    base.update(values)
    return Doc(**base)


class Flow(unittest.TestCase):
    def setUp(self):
        self.saved = []
        for target, value in ((frappe, "db"), ):
            p = patch.object(frappe, "db", SimpleNamespace(commit=lambda: None, rollback=lambda: None), create=True)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(batch.store, "save", side_effect=lambda d: self.saved.append((d.phase, d.provider_batch)))
        p.start(); self.addCleanup(p.stop)
        p = patch.object(batch.store, "actor", side_effect=lambda user: _null())
        p.start(); self.addCleanup(p.stop)

    def fake_claude(self):
        made, _ = client()
        return made

    def test_collect_submits_every_page_and_records_the_batch(self):
        current = doc()
        claude = self.fake_claude()

        def run_local(d, frozen, examples, source, take, vision, every_page):
            requests_ = [anthropic.ModelRequest("direct", n, 0, CLAUDE_MODEL, "S", ((PNG + bytes([n]), "image/png"),),
                                                8192, 60) for n in (1, 2)]
            every_page(lambda n: take(requests_[n - 1]), [1, 2])

        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch.workflow, "verify", return_value=SimpleNamespace(kind="image")), \
             patch.object(batch.workflow, "local_client", return_value=SimpleNamespace(claude=claude)), \
             patch.object(batch.workflow, "run_local", side_effect=run_local), \
             patch.object(batch.anthropic_batch, "create", return_value={"id": "msgbatch_9"}) as create:
            outcome = batch.collect("B1")
        self.assertEqual(outcome, {"batch": "msgbatch_9", "requests": 2})
        state = json.loads(current.provider_batch)
        self.assertEqual((state["id"], len(state["keys"]), "submitting" in state), ("msgbatch_9", 2, False))
        self.assertEqual(len(create.call_args.args[3]), 2)

    def test_collect_falls_back_to_realtime_when_a_page_needs_google(self):
        current = doc()

        def run_local(d, frozen, examples, source, take, vision, every_page):
            take(anthropic.ModelRequest("layer2", 1, 0, "gemini-3.1-flash-lite", "S", (), 8192, 60))

        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch.workflow, "verify", return_value=None), \
             patch.object(batch.workflow, "local_client", return_value=SimpleNamespace(claude=self.fake_claude())), \
             patch.object(batch.workflow, "run_local", side_effect=run_local), \
             patch.object(batch, "release", return_value="released") as release:
            self.assertEqual(batch.collect("B1"), "released")
        self.assertEqual(release.call_args.args[1], "not_batchable")

    def test_an_unknown_submission_is_never_resubmitted(self):
        current = doc(provider_batch=json.dumps({"submitting": 1}))
        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch, "release", return_value="released") as release:
            batch.collect("B1")
        self.assertEqual(release.call_args.args[1], "submission_unknown")

    def test_an_ended_batch_prefills_successes_and_releases_the_work(self):
        keys = ["a" * 64, "b" * 64]
        current = doc(provider_batch=json.dumps({"id": "msgbatch_9", "keys": keys}))
        found = {keys[0]: ("succeeded", message("{}")), keys[1]: ("expired", None), "c" * 64: ("succeeded", {})}
        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch, "_now", return_value=frappe.utils.get_datetime("2026-10-07 01:00:00").timestamp()), \
             patch.object(batch, "_claude", return_value=self.fake_claude()), \
             patch.object(batch.anthropic_batch, "retrieve", return_value={"processing_status": "ended"}), \
             patch.object(batch.anthropic_batch, "results", return_value=found), \
             patch.object(batch, "prefill", return_value=1) as prefill, \
             patch.object(batch, "release", return_value="released") as release:
            self.assertEqual(batch.advance("B1"), "released")
        self.assertEqual(prefill.call_args.args, ("w" * 64, {keys[0]: message("{}")}))  # 不是这一批的键不填
        self.assertEqual(release.call_args.kwargs, {"filled": 1, "missing": 1})

    def test_a_running_batch_is_left_waiting(self):
        current = doc(provider_batch=json.dumps({"id": "msgbatch_9", "keys": ["a" * 64]}))
        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch, "_now", return_value=frappe.utils.get_datetime("2026-10-07 01:00:00").timestamp()), \
             patch.object(batch, "_claude", return_value=self.fake_claude()), \
             patch.object(batch.anthropic_batch, "retrieve", return_value={"processing_status": "in_progress"}), \
             patch.object(batch, "release") as release:
            self.assertIsNone(batch.advance("B1"))
        release.assert_not_called()

    def test_a_batch_older_than_a_day_goes_realtime(self):
        current = doc(provider_batch=json.dumps({"id": "msgbatch_9", "keys": ["a" * 64]}))
        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch, "_now", return_value=frappe.utils.get_datetime("2026-10-08 03:00:00").timestamp()), \
             patch.object(batch, "release", return_value="released") as release:
            batch.advance("B1")
        self.assertEqual(release.call_args.args[1], "batch_timeout")

    def test_release_builds_the_work_without_a_permission_projection(self):
        current = doc()
        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch.service, "enqueue_work", return_value="w" * 64) as enqueue, \
             patch.object(batch.store, "project", side_effect=AssertionError("no projection in the system tick")):
            self.assertEqual(batch.release(current, None, filled=1), {"queued": True, "reason": None})
        enqueue.assert_called_once()
        state = json.loads(current.provider_batch)
        self.assertEqual((current.phase, "ready" in state, "released" in state, state["filled"]), ("admitted", False, True, 1))

    def test_release_waits_again_when_the_queue_is_full(self):
        current = doc()
        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch.service, "enqueue_work", side_effect=QueueFull("full")):
            self.assertEqual(batch.release(current, "x"), {"queued": False, "reason": "queue_full"})
        self.assertEqual(current.phase, service.BATCH_WAITING)
        self.assertTrue(json.loads(current.provider_batch)["ready"])
        with patch.object(batch.store, "read", return_value=current), \
             patch.object(batch, "release", return_value="released") as release:
            batch.advance("B1")
        release.assert_called_once()

    def test_prefill_writes_through_core_checkpoints_and_restores_the_context(self):
        written = []
        flags = frappe._dict(pearnly_ocr_work="outer", pearnly_ocr_checkpoints=("outer", {}))
        p = patch.object(frappe, "flags", flags, create=True); p.start(); self.addCleanup(p.stop)

        def checkpoint(key, state=None, body=None):
            if state is None:
                return {"state": "done"} if key == "old" else None
            written.append((frappe.flags.pearnly_ocr_work, key, state))

        with patch.object(batch.core_work, "checkpoint", side_effect=checkpoint):
            self.assertEqual(batch.prefill("w" * 64, {"new": {}, "old": {}}), 1)
        self.assertEqual(written, [("w" * 64, "new", "done")])
        self.assertEqual((flags.pearnly_ocr_work, flags.pearnly_ocr_checkpoints), ("outer", ("outer", {})))


class Eligibility(unittest.TestCase):
    def test_only_claude_images_and_pdfs_batch(self):
        frozen = {"anthropic": TARGET, "environment": CLAUDE_ENV}
        self.assertTrue(service.batch_eligible(frozen, SimpleNamespace(kind="pdf")))
        self.assertFalse(service.batch_eligible(frozen, SimpleNamespace(kind="document")))
        self.assertFalse(service.batch_eligible({**frozen, "environment": {}}, SimpleNamespace(kind="image")))
        self.assertFalse(service.batch_eligible({"environment": CLAUDE_ENV}, SimpleNamespace(kind="image")))


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
