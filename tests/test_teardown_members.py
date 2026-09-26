"""Cloud member teardown ordering/ownership checks; no Site or external IO."""
import unittest
from unittest.mock import Mock, patch

import frappe
from pearnly_control.onboarding import teardown_members as boundary


class MemberTeardown(unittest.TestCase):
    def test_local_history_never_calls_cloud(self):
        with patch.object(boundary.infra, "is_cloud", return_value=False), \
                patch.object(boundary.billing_remote, "call") as call:
            boundary.prepare("local", [{"target": "sample"}])
            call.assert_not_called()

    def test_cloud_freeze_precedes_cross_tenant_reference_check(self):
        events = []
        def freeze(*args, **kwargs):
            events.append("freeze")
            return {"subjects": ["employee"]}
        def references(*args, **kwargs):
            events.append("check")
            # 两个账号池（业务 / Cowork）的开户身份字段都要查。
            self.assertIn(kwargs["filters"], ({"tenant": ["!=", "T"], "identity_subject": ["in", ["employee"]]},
                                              {"tenant": ["!=", "T"], "cowork_identity_subject": ["in", ["employee"]]}))
            return []
        with patch.object(boundary.infra, "is_cloud", return_value=True), \
                patch.object(boundary.billing_remote, "call", side_effect=freeze), \
                patch.object(frappe, "get_all", side_effect=references):
            boundary.prepare("T", [{"target": "cloud"}])
        self.assertEqual(events, ["freeze", "check", "check"])

    def test_reused_member_and_remote_failure_abort_plan(self):
        with patch.object(boundary.infra, "is_cloud", return_value=True), \
                patch.object(boundary.billing_remote, "call", return_value={"subjects": ["shared"]}), \
                patch.object(frappe, "get_all", return_value=[{"name": "other"}]), \
                patch.object(boundary, "_", lambda value: value), \
                patch.object(frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                boundary.prepare("T", [{"target": "cloud"}])
        with patch.object(boundary.infra, "is_cloud", return_value=True), \
                patch.object(boundary.billing_remote, "call", side_effect=RuntimeError), \
                patch.object(frappe, "get_all") as query:
            with self.assertRaises(RuntimeError):
                boundary.prepare("T", [{"target": "cloud"}])
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
