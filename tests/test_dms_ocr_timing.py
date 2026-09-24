"""Pinned Frappe, in-memory OCR timing/flow checks. No Site or network calls.

No image recognition, billing, DMS or LINE request is made. Timings below are
synthetic and prove attribution/privacy, not real model performance.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import inspect
import json
import socket
import unittest
from unittest.mock import Mock, patch

import frappe
import requests
from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_dms.ocr import timing, ocr_service, ocr_id_card


class TimingHarness(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = 10.0
        self.replace(timing.time, "monotonic", side_effect=lambda: self.clock)
        frappe.local.flags = frappe._dict()
        frappe.local.db = Mock()
        self.logger = self.replace(frappe, "logger", return_value=Mock()).return_value
        self.replace(requests.sessions.Session, "request", side_effect=AssertionError("No HTTP"))
        self.replace(requests, "post", side_effect=AssertionError("No paid/provider/LINE call"))
        self.replace(socket.socket, "connect", side_effect=AssertionError("No network"))

    def replace(self, owner, name, **kwargs):
        return self.stack.enter_context(patch.object(owner, name, **kwargs))

    def record(self):
        self.logger.info.assert_called_once()
        return json.loads(self.logger.info.call_args.args[0])


class TimingTests(TimingHarness):
    def test_stage_accumulates_and_nested_trace_emits_once_without_arguments(self):
        @timing.traced("recognize_id_card")
        def inner(secret):
            for _ in range(2):
                with timing.stage("recognition"):
                    self.clock += 1.5
            timing.completed_checkpoint(True)
            return {"result": secret}

        @timing.traced("line_id_card")
        def outer(secret):
            return inner(secret)

        self.assertEqual(outer("PRIVATE customer photo/phone/credential"),
                         {"result": "PRIVATE customer photo/phone/credential"})
        record = self.record()
        self.assertEqual(record["stages_ms"], {"recognition": 3000})
        self.assertEqual(record["stage_calls"], {"recognition": 2})
        self.assertEqual(record["attempt_ms"], 3000)
        self.assertTrue(record["completed_checkpoint"])
        self.assertEqual(record["kind"], "line_id_card")
        self.assertNotIn("PRIVATE", json.dumps(record))
        self.logger.setLevel.assert_called_once_with(timing.logging.INFO)
        self.assertIsNone(timing._active.get())
        self.assertEqual(str(inspect.signature(outer)), "(secret)")

    def test_each_failure_is_preserved_and_context_does_not_leak(self):
        for error, outcome in ((Deferred("private reason", 15), "deferred"),
                               (Uncertain("private error"), "uncertain"),
                               (ValueError("private value"), "error")):
            with self.subTest(outcome=outcome):
                self.logger.reset_mock()

                @timing.traced("line_id_card")
                def run():
                    with timing.stage("recognition"):
                        self.clock += 2
                        raise error

                with self.assertRaises(type(error)) as caught:
                    run()
                self.assertIs(caught.exception, error)
                record = self.record()
                self.assertEqual(record["outcome"], outcome)
                self.assertEqual(record["stages_ms"]["recognition"], 2000)
                self.assertNotIn("private", json.dumps(record))
                self.assertIsNone(timing._active.get())

    def test_cancelled_failed_and_untraced_spans(self):
        with timing.stage("download"):
            self.clock += 1
        self.logger.info.assert_not_called()
        for outcome in ("cancelled", "failed"):
            self.logger.reset_mock()
            result = {outcome: "private message", "notified": True}
            run = timing.traced("line_id_card")(lambda: result)
            self.assertIs(run(), result)
            self.assertEqual(self.record()["outcome"], outcome)

    def test_metadata_and_log_failures_cannot_change_result_or_exception(self):
        self.replace(timing, "_queue_metadata", side_effect=RuntimeError("db unavailable"))
        self.logger.info.side_effect = OSError("disk full")
        self.assertEqual(timing.traced("line_id_card")(lambda: "original")(), "original")
        error = Deferred("same retry", 30)

        @timing.traced("line_id_card")
        def run():
            raise error

        with self.assertRaises(Deferred) as caught:
            run()
        self.assertIs(caught.exception, error)
        self.assertIsNone(timing._active.get())

    def test_queue_metadata_uses_utc_eligibility_and_same_work_attempt(self):
        frappe.flags.pearnly_ocr_work = "a" * 64
        frappe.db.get_value.return_value = {
            "attempts": 2,
            "next_run": (datetime.now(timezone.utc) - timedelta(seconds=5)).replace(tzinfo=None),
        }
        timing.traced("line_id_card")(lambda: None)()
        record = self.record()
        self.assertEqual(record["attempt"], 2)
        self.assertEqual(record["work"], "a" * 64)
        self.assertGreaterEqual(record["ready_wait_ms"], 5000)
        self.assertLess(record["ready_wait_ms"], 6000)
        self.assertNotIn("creation", str(frappe.db.get_value.call_args))
        frappe.db.get_value.reset_mock()
        frappe.flags.pearnly_ocr_work = "private external identifier"
        self.assertEqual(timing._queue_metadata(), {})
        frappe.db.get_value.assert_not_called()

    def test_only_fixed_stage_names_can_be_logged(self):
        @timing.traced("line_id_card")
        def run():
            with timing.stage("private phone/customer name"):
                self.clock += 1

        run()
        self.assertEqual(self.record()["stages_ms"], {})
        with self.assertRaises(ValueError):
            timing.traced("private user")


class WorkflowTimingTests(TimingHarness):
    def service_fakes(self, *, completed=False):
        from pearnly_core.execution import work
        self.replace(work, "has_completed_call", return_value=completed)
        self.replace(ocr_service, "resolve_dms_endpoint", return_value={"id": "test-endpoint"})
        gate = self.replace(ocr_service, "_billing_gate", return_value={"allowed": True})
        self.replace(ocr_service, "ensure_image_bytes", return_value=b"synthetic image")
        self.replace(ocr_service, "_flag_bad_id_checksum")
        self.replace(ocr_service, "_record_ocr")
        self.replace(ocr_service, "_charge_id_card", return_value={"ok": True, "charged_thb": "1"})
        def extract(content):
            self.clock += 3
            return {"id_card": {"people_id": "synthetic", "first_name": "private name"}}
        self.replace(ocr_id_card, "extract", side_effect=extract)
        return gate

    def test_real_service_records_stages_without_changing_result_or_billing_reference(self):
        gate = self.service_fakes()
        endpoint, result, elapsed = ocr_service.recognize_id_card(
            "private user", b"private image", "private file", billing_reference="stable-reference")
        self.assertEqual(endpoint, {"id": "test-endpoint"})
        self.assertEqual(result["id_card"]["first_name"], "private name")
        self.assertEqual(elapsed, 3000)
        gate.assert_called_once_with()
        ocr_service._charge_id_card.assert_called_once_with(reference="stable-reference")
        record = self.record()
        self.assertEqual(set(record["stages_ms"]), {
            "endpoint", "billing_gate", "prepare_image", "recognition", "billing_charge", "record"})
        self.assertEqual(record["stages_ms"]["recognition"], 3000)
        self.assertNotIn("private", json.dumps(record))

    def test_completed_checkpoint_retry_keeps_billing_gate_skip_and_deferred_result(self):
        gate = self.service_fakes(completed=True)
        ocr_service._charge_id_card.return_value = {"ok": False}
        with self.assertRaises(Deferred) as caught:
            ocr_service.recognize_id_card("private user", b"image", "private file", billing_reference="same")
        self.assertEqual(caught.exception.reason, "billing_unavailable")
        gate.assert_not_called()
        ocr_service._record_ocr.assert_not_called()
        record = self.record()
        self.assertTrue(record["completed_checkpoint"])
        self.assertEqual(record["outcome"], "deferred")

    def test_line_download_model_and_reply_share_one_attempt_trace(self):
        from pearnly_core.execution import inputs
        from pearnly_dms.intake import flow
        self.service_fakes()
        self.replace(flow, "_image_draft", return_value={"payload": {"mode": "customer"}})
        self.replace(flow.store, "replace_session_snapshot", return_value=True)
        self.replace(flow.out, "start_loading", side_effect=lambda *a: setattr(self, "clock", self.clock + 0.2))
        self.replace(flow.out, "push_or_reply_text", side_effect=lambda *a: setattr(self, "clock", self.clock + 0.5))
        def download(*args):
            self.clock += 1
            return b"synthetic image"
        self.replace(inputs, "read", side_effect=download)
        flow.process_image({"user_id": "private user"}, "private LINE id", "private message", "private token")
        record = self.record()
        self.assertEqual(record["kind"], "line_id_card")
        self.assertEqual(record["stages_ms"]["download"], 1000)
        self.assertEqual(record["stages_ms"]["recognition"], 3000)
        self.assertEqual(record["stages_ms"]["line_reply"], 500)
        self.assertEqual(record["attempt_ms"], 4700)
        self.assertNotIn("private", json.dumps(record))
        self.assertEqual(list(inspect.signature(flow.process_image).parameters),
                         ["binding", "line_user_id", "message_id", "reply_token"])


if __name__ == "__main__":
    unittest.main()
