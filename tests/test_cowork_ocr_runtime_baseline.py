"""Offline evidence projection checks; never reads secrets or production DB."""
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location("cowork_ocr_runtime_baseline",
    Path(__file__).resolve().parents[1] / "scripts/cowork-ocr-runtime-baseline.py")
runtime = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runtime)


class RuntimeEvidenceTests(unittest.TestCase):
    def envelope(self):
        body = {"schema": "pearnly-cowork-ocr-runtime-v1", "source_sha": "a" * 40,
                "source_baseline_sha256": "b" * 64, "captured_at": "2026-09-21T00:00:00+00:00",
                "components": {name: {"revision": name + "-test", "runtime_secret": {"name": name + "-env", "version": "3"},
                    "configured_environment": {}, "credential_presence": dict.fromkeys(runtime.CREDENTIAL_KEYS, False),
                    "policy": {"present": False}} for name in ("pearnly-web", "pearnly-worker")}}
        return {"evidence": body, "sha256": hashlib.sha256(runtime.canonical(body)).hexdigest()}

    def rehash(self, envelope):
        envelope["sha256"] = hashlib.sha256(runtime.canonical(envelope["evidence"])).hexdigest()

    def test_changed_dynamic_policy_is_not_accepted(self):
        first = self.envelope()
        last = deepcopy(first)
        for component in last["evidence"]["components"].values():
            component["policy"] = {"present": True, "enabled": True, "updated_at": "2026-09-20T12:00:00+00:00",
                "value_type": "object", "mode": "enterprise", "invoice_override": None, "defaults_by_plan": None}
        self.rehash(last)
        with self.assertRaisesRegex(runtime.EvidenceError, "configuration changed"):
            runtime.compare(first, last)
        runtime.compare(first, deepcopy(first))

    def test_incomplete_but_self_hashed_evidence_is_rejected(self):
        for mutation in (lambda body: body.update(source_sha=""), lambda body: body.update(components={}),
                         lambda body: body.pop("captured_at"),
                         lambda body: body["components"]["pearnly-worker"].pop("credential_presence"),
                         lambda body: body["components"]["pearnly-worker"]["runtime_secret"].update(version="latest")):
            value = self.envelope()
            mutation(value["evidence"])
            self.rehash(value)
            with self.assertRaises(runtime.EvidenceError):
                runtime.compare(value, value)

    def test_evidence_requires_ordered_aware_timestamps(self):
        first, last = self.envelope(), self.envelope()
        last["evidence"]["captured_at"] = "2026-09-20T00:00:00+00:00"
        self.rehash(last)
        with self.assertRaisesRegex(runtime.EvidenceError, "timestamps are reversed"):
            runtime.compare(first, last)
        first["evidence"]["captured_at"] = "2026-09-21T00:00:00"
        self.rehash(first)
        with self.assertRaisesRegex(runtime.EvidenceError, "timezone"):
            runtime.validate(first)

    def test_independent_credential_secret_is_rejected(self):
        for name in runtime.CREDENTIAL_KEYS | {"DATABASE_URL", "PGSSLMODE"}:
            with self.assertRaisesRegex(runtime.EvidenceError, "another secret source"):
                runtime.explicit_environment([{"name": name, "valueFrom": {"secretKeyRef": {"name": "ref", "key": "3"}}}])
        self.assertEqual(runtime.explicit_environment([{"name": "OCR_LLM_BACKEND", "value": "vertex"}]),
                         {"OCR_LLM_BACKEND": "vertex"})

    def test_unsafe_or_incomplete_projection_is_rejected(self):
        for key, value in (("credential_presence", {}), ("configured_environment", {"GEMINI_API_KEY": "PRIVATE"}),
                           ("policy", {"present": 0})):
            envelope = self.envelope()
            envelope["evidence"]["components"]["pearnly-worker"][key] = value
            self.rehash(envelope)
            with self.assertRaises(runtime.EvidenceError) as raised:
                runtime.validate(envelope)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_changed_evidence_without_checksum_fails(self):
        first = self.envelope()
        last = deepcopy(first)
        last["evidence"]["source_sha"] = "b" * 40
        with self.assertRaisesRegex(runtime.EvidenceError, "checksum"):
            runtime.compare(first, last)

    def test_credentials_never_projected(self):
        value = runtime.safe_environment({"DATABASE_URL": "PRIVATE_DB", "GEMINI_API_KEY": "PRIVATE_KEY",
            "OCR_LLM_BACKEND": "vertex", "OCR_FEWSHOT_ENABLED": "1", "UNRELATED_VALUE": "PRIVATE"})
        self.assertEqual(value, {"OCR_LLM_BACKEND": "vertex", "OCR_FEWSHOT_ENABLED": "1"})
        self.assertNotIn("PRIVATE", str(value))

    def test_whitespace_is_not_silently_normalized(self):
        self.assertEqual(runtime.safe_environment({"OCR_FEWSHOT_ENABLED": " 1 "}), {"OCR_FEWSHOT_ENABLED": " 1 "})

    def test_allowlisted_field_rejects_unexpected_content_without_echo(self):
        for key in ("OCR_LLM_BACKEND", "OCR_FEWSHOT_ENABLED", "VERTEX_LOCATION"):
            with self.assertRaises(runtime.EvidenceError) as raised:
                runtime.safe_environment({key: "https://PRIVATE_KEY@example.test"})
            self.assertNotIn("PRIVATE_KEY", str(raised.exception))

    def test_policy_contains_no_other_row_or_actor_fields(self):
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        value = runtime.safe_policy((False, now, "object", "economy", "economy", {"S": "enterprise"}))
        self.assertFalse(value["enabled"])
        self.assertEqual(value["mode"], "economy")
        self.assertEqual(value["defaults_by_plan"], {"S": "enterprise"})
        self.assertEqual(value["updated_at"], now.isoformat())

    def test_absent_policy_distinguishable_from_defaults(self):
        self.assertEqual(runtime.safe_policy(None), {"present": False})

    def test_unknown_policy_content_fails_closed(self):
        now = datetime.now(timezone.utc)
        for mode, invoice, plans in (("PRIVATE", None, None), (None, "PRIVATE", None),
                                     (None, None, {"PRIVATE_TENANT": "economy"})):
            with self.assertRaises(runtime.EvidenceError) as raised:
                runtime.safe_policy((True, now, "object", mode, invoice, plans))
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_provider_errors_do_not_expose_subprocess_output(self):
        from subprocess import CalledProcessError
        failure = CalledProcessError(1, ["gcloud"], output="PRIVATE", stderr="PRIVATE")
        with patch.object(runtime.subprocess, "run", side_effect=failure):
            with self.assertRaises(runtime.EvidenceError) as raised:
                runtime.command(["gcloud"])
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_changed_traffic_is_rejected(self):
        class Gate:
            SERVICE_FORMAT = "json"

            @staticmethod
            def active_revision(value):
                return value["revision"]

        body = {"project": "local-test", "region": "test", "components": {
            "worker": {"revision": {"metadata": {"name": "old"}}}}}
        with patch.object(runtime, "command", return_value='{"revision":"new"}'):
            with self.assertRaisesRegex(runtime.EvidenceError, "traffic changed"):
                runtime.verify_traffic(body, Gate)


if __name__ == "__main__":
    unittest.main()
