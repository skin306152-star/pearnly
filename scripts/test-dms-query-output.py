"""Query -> real DMS output -> real LINE transport regression; no Site or network IO.

Run with the pinned Frappe Python runtime. Only the read/identity/storage boundaries
and outer HTTP request are replaced; query functions, cards and message routing run
unchanged. This catches incompatible output aliases as well as lost reply tokens.
"""
import copy
import json
from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

import frappe
import requests
from pearnly_core import line
from pearnly_dms.query import query_cards, query_flow


class QueryOutput(unittest.TestCase):
    binding = {"user_id": "query-user@example.test", "binding_revision": "fixture-revision"}
    line_user = "U-query-fixture"
    token = "fixture-reply-token"
    private = "PRIVATE-RESULT-MUST-NOT-LEAK"
    record_params = {"field": "customer", "query": "fixture", "status": "",
                     "limit": 10, "page": 1}
    top_params = {"group": "model", "metric": "quantity", "date_from": "01/09/2569",
                  "date_to": "24/09/2569", "limit": 10, "page": 1}

    def setUp(self):
        stack = self.stack = ExitStack()
        self.addCleanup(stack.close)
        self.http_calls = []
        self.http_statuses = []
        stack.enter_context(patch.object(requests.sessions.Session, "request", self._http))
        stack.enter_context(patch.object(frappe, "conf", new={
            "pearnly_line_channel_secret": "fixture-channel-secret",
            "pearnly_line_channel_access_token": "fixture-access-token",
        }))
        self.db = stack.enter_context(patch.object(frappe, "db", new=Mock()))
        stack.enter_context(patch.object(frappe, "flags", new=frappe._dict()))
        cache = Mock()
        cache.make_key.side_effect = lambda key: "fixture:" + key
        stack.enter_context(patch.object(frappe, "cache", return_value=cache))
        stack.enter_context(patch.object(frappe.utils, "now", return_value="2026-09-24 10:00:00"))
        self.log_error = stack.enter_context(patch.object(frappe, "log_error"))
        self.allowed = stack.enter_context(patch.object(query_flow.query_access, "can_query", return_value=True))
        self.binding_matches = stack.enter_context(patch.object(query_flow, "current_binding", return_value=True))
        self.session = stack.enter_context(patch.object(query_flow.store, "get_session", return_value={"state": "query_sales"}))
        self.reads = {
            "dimensions": stack.enter_context(patch.object(query_flow.sales_readback, "query_capabilities")),
            "records": stack.enter_context(patch.object(query_flow.sales_readback, "fetch_sales_records")),
            "top": stack.enter_context(patch.object(query_flow.sales_readback, "fetch_top_sales")),
        }

    def _http(self, method, url, **kwargs):
        # A request to any other service is a test failure, never an external call.
        self.http_calls.append((method.upper(), url, copy.deepcopy(kwargs)))
        self.assertEqual(method.upper(), "POST")
        self.assertIn(url, ("https://api.line.me/v2/bot/message/reply",
                            "https://api.line.me/v2/bot/message/push"))
        status = self.http_statuses.pop(0) if self.http_statuses else 200
        return Mock(status_code=status, text="Invalid reply token" if status != 200 else "{}", headers={})

    def _reset_case(self):
        self.http_calls.clear()
        self.http_statuses.clear()
        self.db.reset_mock()
        self.allowed.side_effect = None
        self.allowed.return_value = True
        self.binding_matches.return_value = True
        self.session.return_value = {"state": "query_sales"}
        for read in self.reads.values():
            read.reset_mock()

    def _success(self, kind):
        if kind == "dimensions":
            return {"ok": True, "top_sales": True}
        if kind == "records":
            return {"ok": True, "page": 1, "limit": 10,
                    "rows": [{"booking_no": self.private, "customer_name": "Fixture customer"}]}
        return {"ok": True, **self.top_params, "rows": [{"label": self.private, "value": 2}]}

    def _run(self, kind, result, token=token):
        self.reads[kind].return_value = result
        if kind == "dimensions":
            query_flow._run_dimensions(self.binding, self.line_user, token)
        elif kind == "records":
            self.session.return_value = {"state": "query_results", "payload": {**self.record_params, "query_kind": "records"}}
            query_flow._run_records(self.binding, self.line_user, self.record_params, token)
        else:
            self.session.return_value = {"state": "query_results", "payload": {**self.top_params, "query_kind": "top"}}
            query_flow._run_top(self.binding, self.line_user, self.top_params, token)

    def _assert_route(self, routes):
        self.assertEqual([url.rsplit("/", 1)[-1] for _, url, _ in self.http_calls], routes)
        for _, url, options in self.http_calls:
            self.assertEqual(options["headers"]["Authorization"], "Bearer fixture-access-token")
            self.assertEqual(options["timeout"], line.TIMEOUT)
            payload = options["json"]
            self.assertEqual(len(payload["messages"]), 1)
            if url.endswith("/reply"):
                self.assertEqual(payload["replyToken"], self.token)
                self.assertNotIn("to", payload)
            else:
                self.assertEqual(payload["to"], self.line_user)
                self.assertNotIn("replyToken", payload)
        if routes == ["reply", "push"]:
            self.assertEqual(self.http_calls[0][2]["json"]["messages"],
                             self.http_calls[1][2]["json"]["messages"])

    def _assert_text(self, text):
        for _, _, options in self.http_calls:
            self.assertEqual(options["json"]["messages"], [{"type": "text", "text": text}])
            self.assertNotIn(self.private, json.dumps(options["json"]))

    def test_success_uses_real_cards_and_free_reply(self):
        for kind in self.reads:
            with self.subTest(kind=kind):
                self._reset_case()
                self._run(kind, self._success(kind))
                self._assert_route(["reply"])
                message = self.http_calls[0][2]["json"]["messages"][0]
                self.assertEqual(message["type"], "text" if kind == "dimensions" else "flex")
                if kind != "dimensions":
                    self.assertIn(self.private, json.dumps(message))
                self.db.commit.assert_called_once_with()

    def test_missing_credentials_all_three_failure_branches_reply(self):
        for kind in self.reads:
            with self.subTest(kind=kind):
                self._reset_case()
                self._run(kind, {"ok": False, "error_code": "ERR_NO_CREDS", "raw_error": self.private})
                self._assert_route(["reply"])
                self._assert_text(query_cards.TXT_NO_ENDPOINT)

    def test_raw_failure_details_are_not_exposed(self):
        for kind in self.reads:
            with self.subTest(kind=kind):
                self._reset_case()
                self._run(kind, {"ok": False, "error_code": "ERR_READ", "raw_error": self.private,
                                 "password": self.private, "rows": [{"label": self.private}]})
                self._assert_route(["reply"])
                self._assert_text(query_cards.TXT_QUERY_FAILED)

    def test_friendly_failure_keeps_existing_thai_message(self):
        for kind in self.reads:
            with self.subTest(kind=kind):
                self._reset_case()
                friendly = "กรุณาลองใหม่อีกครั้ง"
                self._run(kind, {"ok": False, "error_friendly": {"th": friendly}, "raw_error": self.private})
                self._assert_route(["reply"])
                self._assert_text(friendly)

    def test_permission_revoked_after_read_sends_denial_without_result(self):
        for kind in ("records", "top"):
            with self.subTest(kind=kind):
                self._reset_case()
                self.allowed.side_effect = [True, False]
                self._run(kind, self._success(kind))
                self._assert_route(["reply"])
                self._assert_text(query_cards.TXT_DENIED)

    def test_permission_denied_before_read_does_not_read_or_send(self):
        for kind in ("records", "top"):
            with self.subTest(kind=kind):
                self._reset_case()
                self.allowed.return_value = False
                self._run(kind, self._success(kind))
                self.reads[kind].assert_not_called()
                self.assertEqual(self.http_calls, [])

    def test_rebound_identity_never_receives_previous_users_results(self):
        for kind in self.reads:
            with self.subTest(kind=kind):
                self._reset_case()
                self.binding_matches.return_value = False
                self._run(kind, self._success(kind))
                self.assertEqual(self.http_calls, [])

    def test_dimensions_obsolete_session_sends_nothing(self):
        self.session.return_value = {"state": "idle"}
        self._run("dimensions", self._success("dimensions"))
        self.assertEqual(self.http_calls, [])

    def test_all_five_text_branches_fall_back_only_after_reply_rejected(self):
        for kind, revoked in (("dimensions", False), ("records", False), ("top", False),
                              ("records", True), ("top", True)):
            with self.subTest(kind=kind, revoked=revoked):
                self._reset_case()
                self.http_statuses = [400, 200]
                if revoked:
                    self.allowed.side_effect = [True, False]
                result = self._success(kind) if revoked else {"ok": False, "raw_error": self.private}
                self._run(kind, result)
                self._assert_route(["reply", "push"])
                self._assert_text(query_cards.TXT_DENIED if revoked else query_cards.TXT_QUERY_FAILED)

    def test_success_cards_also_fall_back_when_token_has_expired(self):
        for kind in self.reads:
            with self.subTest(kind=kind):
                self._reset_case()
                self.http_statuses = [400, 200]
                self._run(kind, self._success(kind))
                self._assert_route(["reply", "push"])

    def test_legacy_jobs_without_token_push_failure_text_directly(self):
        for kind in self.reads:
            with self.subTest(kind=kind):
                self._reset_case()
                self._run(kind, {"ok": False, "raw_error": self.private}, token="")
                self._assert_route(["push"])
                self._assert_text(query_cards.TXT_QUERY_FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
