"""Boundary tests using real service functions and fake identity/storage adapters.

Run with Frappe installed. No Site, database, credentials, network or tenant writes.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from pearnly_control import issues, provisioning
from pearnly_control.onboarding import targets


class Rejected(Exception):
    pass


class CloudOnly(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.exists.side_effect = lambda doctype, name: name in {"cloud", "local"}
        self.db.get_value.return_value = "cloud"
        self.conf = {"pearnly_target_runtime": {"cloud": {"executor": "production"},
                                              "local": {"executor": "sample"}}}
        self.stack = []
        for attribute, value in {"db": self.db, "conf": self.conf,
                                 "throw": Mock(side_effect=Rejected), "_": lambda x: x,
                                 "session": SimpleNamespace(user="Administrator"),
                                 "only_for": Mock()}.items():
            p = patch.object(frappe, attribute, value, create=True)
            p.start(); self.addCleanup(p.stop)
        # Frappe translation is unrelated to this boundary test and needs a Site.
        for mod in (targets, issues, provisioning):
            p = patch.object(mod, "_", lambda x: x); p.start(); self.addCleanup(p.stop)

    def test_default_cloud_and_explicit_cloud(self):
        self.assertEqual(targets.require_cloud_target(), "cloud")
        self.assertEqual(targets.require_cloud_target("cloud"), "cloud")

    def test_local_unknown_missing_configuration_and_local_default_rejected(self):
        for name in ("local", "unknown"):
            with self.subTest(name=name), self.assertRaises(Rejected):
                targets.require_cloud_target(name)
        self.conf.clear()
        with self.assertRaises(Rejected): targets.require_cloud_target("cloud")
        self.db.get_value.return_value = "local"
        with self.assertRaises(Rejected): targets.require_cloud_target()

    def test_existing_request_without_target_is_not_retargeted_to_default(self):
        with self.assertRaises(Rejected):
            targets.require_cloud_target(None, use_default=False)

    def test_tunnel_address_does_not_change_cloud_ownership(self):
        self.conf["pearnly_target_api_urls"] = {"cloud": "http://127.0.0.1:18180"}
        self.assertEqual(targets.require_cloud_target("cloud"), "cloud")

    def test_api_local_rejected_before_identity_or_document_write(self):
        with patch.object(issues.kc, "client_for_target") as identity, patch.object(frappe, "get_doc") as doc:
            with self.assertRaises(Rejected):
                issues.issue("Tenant", "user@example.test", [{"app": "dms"}],
                             target="local", account_username="user", account_password="fake")
            identity.assert_not_called(); doc.assert_not_called()

    def test_desk_and_side_effect_boundary_reject_local(self):
        doc = SimpleNamespace(status="draft", target="local")
        with patch.object(frappe, "get_doc", return_value=doc), patch.object(issues.kc, "client_for_target") as identity:
            with self.assertRaises(Rejected): issues.issue_from_doc("ISSUE")
            with self.assertRaises(Rejected): issues._activate_locked(doc, "Administrator", target_name="local")
            identity.assert_not_called()

    def test_cloud_activation_reaches_configured_identity_without_local_fallback(self):
        doc = SimpleNamespace(status="draft", target="cloud", name="ISSUE", apps=[SimpleNamespace(app="dms")])
        with patch.object(frappe, "get_doc", return_value=doc), \
             patch.object(issues.kc, "client_for_target", return_value=(Mock(), "cloud")) as identity, \
             patch.object(issues, "_activate", return_value={"issue": "ISSUE"}) as activate:
            self.assertEqual(issues.issue_from_doc("ISSUE"), {"issue": "ISSUE"})
            # DMS 在业务账号池（以 ERP 作该池代表）；Cowork 有自己的池，这张单没开就不查。
            identity.assert_called_once_with("cloud", "erp")
            activate.assert_called_once_with(doc, "Administrator", target_name="cloud")

    def test_historical_local_result_is_readable_without_identity_calls(self):
        doc = SimpleNamespace(status="issued", identity_subject="existing", target="local")
        with patch.object(frappe, "get_doc", return_value=doc), patch.object(issues, "_result", return_value={"existing": True}), patch.object(issues.kc, "client_for_target") as identity:
            self.assertEqual(issues.issue_from_doc("OLD"), {"existing": True})
            identity.assert_not_called()

    def test_local_plan_rejected_before_resuming_old_steps(self):
        doc = SimpleNamespace(target="local", name="ISSUE")
        with patch.object(frappe, "get_doc", return_value=doc), patch.object(provisioning, "_sync_steps") as sync:
            with self.assertRaises(Rejected): provisioning.build_plan("ISSUE")
            sync.assert_not_called()

    def test_pending_local_jobs_do_not_escape_to_either_runner(self):
        req = SimpleNamespace(issue="ISSUE", idempotency_key="ISSUE", steps=[])
        self.db.get_value.side_effect = lambda dt, key, field: "local" if field == "target" else None
        with patch.object(frappe, "get_all", return_value=["REQUEST"]), patch.object(frappe, "get_doc", return_value=req):
            self.assertEqual(provisioning.next_steps(), [])
            self.assertEqual(provisioning.next_steps(target="local"), [])
            self.assertEqual(provisioning.next_steps(target="cloud"), [])

    def test_cloud_runner_receives_only_its_cloud_job(self):
        step = SimpleNamespace(name="STEP", step_key="cloud:new_site", status="pending",
                               action="new_site", payload='{"site":"example.test"}', deployment=None)
        req = SimpleNamespace(name="REQUEST", issue="ISSUE", idempotency_key="ISSUE", steps=[step])
        self.db.get_value.side_effect = lambda dt, key, field: "cloud" if field == "target" else None
        with patch.object(frappe, "get_all", return_value=["REQUEST"]), patch.object(frappe, "get_doc", return_value=req):
            self.assertEqual(provisioning.next_steps(), [])
            result = provisioning.next_steps(target="cloud")
            self.assertEqual(result[0]["action"], "new_site")
            self.assertEqual(result[0]["payload"], {"site": "example.test"})

    def test_retired_host_teardown_cannot_escape_to_local_or_new_runner(self):
        self.conf["pearnly_target_runtime"]["retired"] = {"executor": "retired"}
        step = SimpleNamespace(name="STEP", step_key="old:drop_site", status="pending",
                               action="drop_site", payload='{}', deployment=None)
        req = SimpleNamespace(name="REQUEST", issue="ISSUE", idempotency_key="teardown:OLD", steps=[step])
        self.db.get_value.side_effect = lambda dt, key, field: "retired" if field == "target" else None
        with patch.object(frappe, "get_all", return_value=["REQUEST"]), patch.object(frappe, "get_doc", return_value=req):
            for target in (None, "retired", "cloud"):
                self.assertEqual(provisioning.next_steps(target=target), [])

    def test_production_bridge_requires_matching_connected_target(self):
        from pearnly_control import runner_bridge
        with patch.object(provisioning, "next_steps", return_value=[{"job": "new"}]) as jobs:
            self.assertEqual(runner_bridge.dispatch({"action": "next", "args": {"target": "cloud"}}), [])
            self.conf["pearnly_production_target"] = "cloud"
            self.assertEqual(runner_bridge.dispatch({"action": "next", "args": {"target": "retired"}}), [])
            jobs.assert_not_called()
            self.assertEqual(runner_bridge.dispatch({"action": "next", "args": {"target": "cloud"}}), [{"job": "new"}])
            jobs.assert_called_once_with(limit=1, target="cloud")

    def test_native_document_validation_rejects_local_before_account_validation(self):
        from pearnly_control.pearnly_control.doctype.pearnly_issue.pearnly_issue import PearnlyIssue
        # 开户单另有智能体设置（ADR-019，`onboarding.assistant`）：这里三项都空，即不开通
        doc = SimpleNamespace(target="local", status="draft", is_new=lambda: True,
                              _validate_account=Mock(), _validate_apps=Mock(), _fill_placeholder_email=Mock(),
                              _summarize_features=Mock(),
                              assistant_engine="", assistant_model="", assistant_api_key="",
                              flags=SimpleNamespace(), has_value_changed=lambda field: False)
        with self.assertRaises(Rejected): PearnlyIssue.validate(doc)
        doc._validate_account.assert_not_called()
        doc.target = "cloud"
        PearnlyIssue.validate(doc)
        doc._validate_account.assert_called_once()

    def test_desk_search_filters_cloud_and_keeps_permissions(self):
        import inspect
        with patch.object(frappe, "get_list", return_value=[]) as query:
            inspect.unwrap(targets.search)("Pearnly Target", "", "name", 0, 20, {})
            self.assertEqual(query.call_args.kwargs["filters"]["name"], ["in", ["cloud"]])
            frappe.only_for.assert_called_with("System Manager")

    def test_guest_cannot_list_targets(self):
        frappe.session.user = "Guest"
        with patch.object(frappe, "get_all") as query:
            with self.assertRaises(Rejected): issues.list_targets()
            query.assert_not_called()

    def test_selector_query_and_api_only_offer_configured_cloud(self):
        with patch.object(frappe, "get_all", return_value=[]) as query:
            issues.list_targets()
            self.assertEqual(query.call_args.kwargs["filters"], {"name": ["in", ["cloud"]]})


class PlaceholderEmail(unittest.TestCase):
    """邮箱留空 → 第一行产品的前缀@pearnly.com（用户 2026-10-27）。"""

    def setUp(self):
        from pearnly_control.pearnly_control.doctype.pearnly_issue import pearnly_issue
        self.mod = pearnly_issue
        self.db = Mock()
        self.db.get_value.return_value = None
        for attribute, value in {"db": self.db, "throw": Mock(side_effect=Rejected), "_": lambda x: x}.items():
            p = patch.object(frappe, attribute, value, create=True)
            p.start(); self.addCleanup(p.stop)
        for attribute, value in {"_": lambda x: x, "base_domain": lambda target=None: "pearnly.com"}.items():
            p = patch.object(pearnly_issue, attribute, value); p.start(); self.addCleanup(p.stop)

    def doc(self, email, prefixes=("polymer",)):
        rows = [SimpleNamespace(app=app, domain_prefix=prefix, line_liff_id="", line_channel_id="",
                                line_channel_secret="", line_access_token="")
                for app, prefix in zip(("erp", "cowork", "dms"), prefixes)]
        return SimpleNamespace(account_email=email, account_username="polymerpearnly", account_password="300441",
                               tenant_name="Polymer", target="cloud", name="ISS-NEW", apps=rows,
                               is_new=lambda: True, status="draft", _validate_line_credentials=Mock())

    def validate(self, doc):
        Issue = self.mod.PearnlyIssue
        Issue._validate_account(doc)
        Issue._validate_apps(doc)
        Issue._fill_placeholder_email(doc)
        return doc

    def test_blank_email_uses_first_product_prefix(self):
        self.assertEqual(self.validate(self.doc("  ")).account_email, "polymer@pearnly.com")
        self.assertEqual(self.validate(self.doc(None, ("Gize", "gize-cw"))).account_email, "gize@pearnly.com")

    def test_given_email_is_kept(self):
        self.assertEqual(self.validate(self.doc(" Boss@Example.com ")).account_email, "boss@example.com")

    def test_malformed_email_still_rejected(self):
        with self.assertRaises(Rejected):
            self.validate(self.doc("not-an-email"))

    def test_official_sender_prefix_is_reserved(self):
        with self.assertRaises(Rejected):
            self.validate(self.doc("", ("hello",)))

    def test_no_product_row_does_not_build_half_an_address(self):
        doc = self.doc("", ())
        with self.assertRaises(Rejected):
            self.validate(doc)
        self.assertEqual(doc.account_email, "")


if __name__ == "__main__":
    unittest.main()
