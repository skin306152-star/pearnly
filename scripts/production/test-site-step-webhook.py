#!/usr/bin/env python3
"""No network: the provisioning step points the tenant's LINE OA at its own webhook and checks it."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / "infrastructure/production/site-step.py"
spec = importlib.util.spec_from_file_location("site_step", path)
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)

SITE = "acme-cowork.pearnly.com"
URL = "https://acme-cowork.pearnly.com/api/method/pearnly_core.line.webhook"


class FakeLine:
    """Stands in for api.line.me: records calls and keeps the endpoint like LINE does."""

    def __init__(self, endpoint="", active=True, test=None):
        self.endpoint, self.active, self.calls = endpoint, active, []
        self.test = test or {"success": True, "statusCode": 200, "reason": "OK"}

    def __call__(self, request, timeout):
        body = json.loads(request.data) if request.data else None
        path = request.full_url.rsplit("/", 1)[-1]
        self.calls.append((request.get_method(), path, body, request.get_header("Authorization")))
        if request.get_method() == "PUT":
            self.endpoint, answer = body["endpoint"], {}
        elif path == "test":
            answer = self.test
        else:
            answer = {"endpoint": self.endpoint, "active": self.active} if self.endpoint else {}
        return io.BytesIO(json.dumps(answer).encode())


class SetWebhook(unittest.TestCase):
    def test_sets_the_tenant_endpoint_with_its_own_token_and_reads_it_back(self):
        line = FakeLine()
        with patch.object(step.urllib.request, "urlopen", line):
            step.set_webhook(SITE, URL, " token \n")
        self.assertEqual(line.endpoint, URL)
        self.assertEqual([c[:2] for c in line.calls], [("PUT", "endpoint"), ("GET", "endpoint")])
        self.assertEqual({c[3] for c in line.calls}, {"Bearer token"})
        # Before the site is published LINE could not reach it, so no test push here.
        self.assertNotIn("test", [c[1] for c in line.calls])

    def test_refuses_an_endpoint_that_is_not_this_tenant(self):
        line = FakeLine()
        with patch.object(step.urllib.request, "urlopen", line), self.assertRaises(AssertionError):
            step.set_webhook(SITE, "https://other.pearnly.com/api/method/pearnly_core.line.webhook", "t")
        self.assertEqual(line.calls, [])


class CheckWebhook(unittest.TestCase):
    def check(self, line):
        with patch.object(step.urllib.request, "urlopen", line):
            step.check_webhook(SITE, "t")

    def test_passes_when_endpoint_is_ours_switched_on_and_test_push_returns_200(self):
        line = FakeLine(endpoint=URL)
        self.check(line)
        self.assertEqual(line.calls[-1][:3], ("POST", "test", {"endpoint": URL}))

    def test_missing_endpoint_fails(self):
        with self.assertRaisesRegex(AssertionError, "does not point"):
            self.check(FakeLine())

    def test_use_webhook_switched_off_fails_with_the_console_instruction(self):
        with self.assertRaisesRegex(AssertionError, "Use webhook"):
            self.check(FakeLine(endpoint=URL, active=False))

    def test_failed_test_push_fails(self):
        line = FakeLine(endpoint=URL, test={"success": False, "statusCode": 404, "reason": "NOT_FOUND"})
        with self.assertRaisesRegex(AssertionError, "404"):
            self.check(line)


if __name__ == "__main__":
    unittest.main()
