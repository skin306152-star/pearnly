"""No-I/O roster advisor regressions: real service/client, fake outer boundaries.

Cached dialog choices and live save-time selector reads stay separate. No Site,
credential, external request or business write is performed. Native permission/
transaction integration is covered separately by the isolated lab.
"""
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.contracts.client_base import DmsClientError


class Invalid(Exception):
    pass


class Denied(Exception):
    pass


class Unsigned(Exception):
    pass


def throws(message, exception=Invalid):
    raise exception(message)


class OperatorAdvisorTests(unittest.TestCase):
    def setUp(self):
        self.primary = ["primary-endpoint"]
        self.current_primary = list(self.primary)
        self.roles = ["DMS Admin"]
        self.current_roles = list(self.roles)
        self.enabled = self.current_enabled = True
        self.scope = self.current_scope = "same-synthetic-credential-scope"
        self.cache = {"data": {"masters": {"advisors": [["335", "SALE", "Cached Advisor", "private phone"]]}}}
        self.live = [["335", "LIVE", "Current Advisor", "private phone"]]
        self.http = []
        self.after_read = lambda: None
        self.body = None
        self.frappe = ModuleType("frappe")
        self.frappe._ = lambda value: value
        self.frappe.throw = throws
        self.frappe.PermissionError, self.frappe.AuthenticationError = Denied, Unsigned
        self.frappe.session = SimpleNamespace(user="admin@example.invalid")
        self.frappe.get_roles = Mock(side_effect=lambda actor: self.roles)
        self.frappe.get_all = Mock(side_effect=lambda *a, **kw: self.primary)
        self.frappe.db = SimpleNamespace(
            get_value=Mock(side_effect=lambda doctype, name, field: self.enabled if doctype == "User" else self.fail("unexpected DB lookup")),
            sql=Mock(side_effect=self.current_sql), commit=Mock(side_effect=AssertionError("No caller commit")))
        self.database = SimpleNamespace(sql=Mock(side_effect=lambda *a, **kw: [(name,) for name in self.current_primary]))
        self.store = ModuleType("pearnly_dms.snapshots.store")
        self.store.read = Mock(side_effect=lambda *a, **kw: deepcopy(self.cache))
        self.store.scope = Mock(side_effect=lambda *a, **kw: self.current_scope if kw.get("current") else self.scope)
        self.store._cache_connection = Mock(side_effect=lambda: nullcontext(self.database))
        self.operations = ModuleType("pearnly_dms.adapter.operations")
        self.operations.run_logged_in = Mock(side_effect=self.logged_in)
        with patch.dict(sys.modules, {"frappe": self.frappe,
                "pearnly_dms.snapshots.store": self.store,
                "pearnly_dms.adapter.operations": self.operations}):
            import pearnly_dms.snapshots as snapshots
            with patch.object(snapshots, "store", self.store, create=True):
                spec = importlib.util.spec_from_file_location("operator_advisors_under_test",
                    ROOT / "apps/pearnly_dms/pearnly_dms/operators/advisors.py")
                self.module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.module)
        network_guard = patch.object(socket, "create_connection", side_effect=AssertionError("No external IO"))
        network_guard.start()
        self.addCleanup(network_guard.stop)

    def current_sql(self, statement, actor):
        self.assertEqual(actor, self.frappe.session.user)
        self.assertIn("LOCK IN SHARE MODE", statement)
        if "`tabUser`" in statement:
            return [(int(self.current_enabled),)]
        if "`tabHas Role`" in statement:
            self.assertIn("parenttype='User'", statement)
            return [(role,) for role in self.current_roles]
        self.fail("Unexpected permission query")

    def post(self, url, *, data):
        self.assertEqual(url, "https://synthetic.invalid/dms/drfcbc/component/bshsd.php")
        self.assertEqual(data["elemname"], "txtusers")
        self.assertEqual(data["sdt"], "")
        self.assertEqual(set(data), {"elemname", "sdt", "bshsdamt", "bshsdcurrpage"})
        self.http.append(dict(data))
        size, page = int(data["bshsdamt"]), int(data["bshsdcurrpage"])
        body = self.body(data) if callable(self.body) else self.body
        if body is None:
            body = json.dumps(self.live[(page - 1) * size:page * size])
        return SimpleNamespace(status_code=200, text=body)

    def logged_in(self, endpoint, callback, **kwargs):
        self.assertEqual(endpoint, "primary-endpoint")
        self.assertEqual(kwargs, {"authoritative_read": True})
        client = DmsClient(SimpleNamespace(post=self.post), "https://synthetic.invalid/dms/")
        try:
            result = callback(client, None)
        except DmsClientError as exc:
            result = {"ok": False, "error_code": exc.error_code}
        self.after_read()
        return result

    def test_dialog_reads_only_company_cache_never_logs_in_or_reads_other_groups(self):
        self.assertEqual(self.module.read_options(), ([{"id": "335", "code": "SALE", "name": "Cached Advisor"}], ""))
        self.store.read.assert_called_once_with("primary-endpoint", "company")
        self.operations.run_logged_in.assert_not_called()
        self.store.scope.assert_not_called()
        self.assertFalse(self.http)
        self.assertEqual(self.frappe.get_all.call_args.kwargs,
            {"filters": self.module.PRIMARY_FILTERS, "pluck": "name", "limit_page_length": 2})

    def test_missing_cache_or_expired_identity_never_falls_back_to_live(self):
        for code in ("ERR_DMS_SNAPSHOT_PENDING", "ERR_DMS_SNAPSHOT_SCOPE_CHANGED", "ERR_NO_CREDS", "ERR_DMS_AUTH"):
            with self.subTest(code=code):
                self.store.read.side_effect = DmsClientError("Synthetic snapshot failure", code)
                self.assertEqual(self.module.read_options(), ([], "roster.advisor.loadError"))
        self.operations.run_logged_in.assert_not_called()
        self.assertFalse(self.http)

    def test_empty_valid_cache_is_empty_but_missing_or_malformed_is_error(self):
        self.cache["data"]["masters"]["advisors"] = []
        self.assertEqual(self.module.read_options(), ([], ""))
        for bad in (None, [["335", "code"]], [["", "code", "Name"]], [["335", "code", ""]],
                    [["335", "code", "Name"], ["335", "code", "Other"]]):
            with self.subTest(bad=bad):
                self.cache["data"]["masters"]["advisors"] = bad
                self.assertEqual(self.module.read_options(), ([], "roster.advisor.loadError"))
        self.operations.run_logged_in.assert_not_called()

    def test_missing_or_ambiguous_primary_never_chooses_first_endpoint(self):
        for names in ([], ["primary-endpoint", "other-primary"]):
            self.primary = names
            self.assertEqual(self.module.read_options(), ([], "roster.advisor.noConnection"))
            with self.assertRaisesRegex(Invalid, "roster.advisor.noConnection"):
                self.module.resolve("335")
        self.store.read.assert_not_called()
        self.operations.run_logged_in.assert_not_called()

    def test_save_reads_only_live_advisor_selector_and_resolves_current_name(self):
        self.assertEqual(self.module.resolve("335"), {"id": "335", "code": "LIVE", "name": "Current Advisor"})
        self.store.read.assert_not_called()
        self.assertEqual(len(self.http), 1)
        self.assertNotIn("private phone", str(self.module.resolve("335")))
        self.frappe.db.commit.assert_not_called()
        self.store.scope.assert_any_call("primary-endpoint", current=True, _database=self.database)

    def test_save_supports_native_advisor_pagination_not_only_first_page(self):
        self.live = [[str(index), "C", "Synthetic " + str(index)] for index in range(200)] + [["335", None, "Page Two"]]
        self.assertEqual(self.module.resolve("335"), {"id": "335", "code": "", "name": "Page Two"})
        self.assertEqual([row["bshsdcurrpage"] for row in self.http], ["1", "2"])
        self.assertTrue(all(row["bshsdamt"] == "200" for row in self.http))

    def test_deleted_selection_is_rejected_despite_cached_old_option(self):
        self.live = [["other", "code", "Other Advisor"]]
        with self.assertRaisesRegex(Invalid, "roster.advisor.invalid"):
            self.module.resolve("335")
        self.store.read.assert_not_called()
        self.assertEqual(len(self.http), 1)

    def test_failed_malformed_or_duplicate_live_list_never_uses_cached_name(self):
        for body in ("<html>login</html>", "null", "{}", '[["335","C"]]',
                     '[["335","C","One"],["335","C","Two"]]'):
            with self.subTest(body=body):
                self.body = body
                with self.assertRaisesRegex(Invalid, "roster.advisor.loadError"):
                    self.module.resolve("335")
        self.store.read.assert_not_called()

    def test_truncated_pagination_is_rejected_even_when_selected_id_was_seen(self):
        self.body = lambda data: json.dumps([["335" if index == 0 else str(index), "C", "Name"] for index in range(200)])
        with self.assertRaisesRegex(Invalid, "roster.advisor.loadError"):
            self.module.resolve("335")
        self.assertEqual(len(self.http), 20)

    def test_empty_assignment_clears_without_cache_or_external_request(self):
        self.assertEqual(self.module.resolve(""), {"id": "", "name": ""})
        self.store.read.assert_not_called()
        self.operations.run_logged_in.assert_not_called()

    def test_guest_nonadmin_and_disabled_admin_refused_before_cache_or_live_io(self):
        for actor, roles, enabled, error in (("Guest", ["DMS Admin"], True, Unsigned),
                ("staff", ["DMS User"], True, Denied), ("disabled", ["DMS Admin"], False, Denied)):
            self.frappe.session.user, self.roles, self.enabled = actor, roles, enabled
            for action in (self.module.read_options, lambda: self.module.resolve("335"), lambda: self.module.resolve("")):
                with self.subTest(actor=actor), self.assertRaises(error):
                    action()
        self.store.read.assert_not_called()
        self.operations.run_logged_in.assert_not_called()

    def test_revoked_role_disabled_admin_or_switched_actor_cannot_return_assignment(self):
        for mutation in (lambda: setattr(self, "current_roles", []),
                         lambda: setattr(self, "current_enabled", False),
                         lambda: setattr(self.frappe.session, "user", "other-admin")):
            self.current_roles = ["DMS Admin"]; self.current_enabled = True
            self.frappe.session.user = "admin@example.invalid"
            self.after_read = mutation
            with self.assertRaises(Denied):
                self.module.resolve("335")
        self.store.read.assert_not_called()

    def test_changed_authority_credentials_or_primary_rejected_after_live_read(self):
        for mutation in (lambda: setattr(self, "current_scope", "rotated-secret-scope"),
                         lambda: setattr(self, "current_primary", []),
                         lambda: setattr(self, "current_primary", ["other-primary"])):
            self.current_scope = self.scope; self.current_primary = list(self.primary)
            self.after_read = mutation
            with self.assertRaisesRegex(Invalid, "roster.advisor.loadError"):
                self.module.resolve("335")
        self.frappe.db.commit.assert_not_called()

    def test_native_administrator_does_not_require_explicit_has_role_rows(self):
        self.frappe.session.user = "Administrator"
        self.roles = ["System Manager"]  # Native get_roles grants all roles.
        self.current_roles = []
        self.assertEqual(self.module.resolve("335")["name"], "Current Advisor")
        self.assertEqual(len(self.http), 1)

    def test_native_administrator_must_still_be_enabled_after_live_read(self):
        self.frappe.session.user = "Administrator"
        self.roles = ["System Manager"]
        self.current_roles = []
        self.after_read = lambda: setattr(self, "current_enabled", False)
        with self.assertRaises(Denied):
            self.module.resolve("335")


if __name__ == "__main__":
    unittest.main()
