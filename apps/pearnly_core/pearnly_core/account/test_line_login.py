"""LINE 绑定即登录：站点侧零 IO 测试（框架与建号服务调用均替换）。"""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pearnly_core.account import line_login

LINE_A = "U" + "a" * 32
LINE_B = "U" + "b" * 32
ALIAS = "line-2010411313"


class PermissionError_(Exception):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.native.PermissionError = PermissionError_
        self.native.throw.side_effect = lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))
        self.native.conf = {"pearnly_product": "erp", "pearnly_tenant": "T-2026-0283",
                            "pearnly_line_login_idp": ALIAS, "pearnly_oidc_client_id": "testerp.pearnly.com",
                            "pearnly_line_liff_id": "2010411313-pBXFYD2S"}
        self.native.local = SimpleNamespace(response={}, site="testerp.pearnly.com", request=object())
        self.native.session = SimpleNamespace(user="Guest")
        self.users = {"owner@example.test": 1, "gone@example.test": 0}
        self.bindings = [SimpleNamespace(name="B1", user="owner@example.test", line_user_id=LINE_A,
                                         status="active", login_link="")]
        self.sessions = []
        self.native.db.get_value.side_effect = self.get_value
        self.native.get_all.side_effect = self.get_all
        for target, value in (("frappe", self.native), ("now", lambda: "2026-09-26 12:00:00"), ("_", str)):
            p = patch.object(line_login, target, value)
            p.start()
            self.addCleanup(p.stop)

    def get_value(self, doctype, filters, field=None, **kwargs):
        if doctype == "User":
            return self.users.get(filters)
        if doctype == line_login.BINDING:
            row = next((b for b in self.bindings if all(getattr(b, k) == v for k, v in filters.items())), None)
            return getattr(row, field) if row else None
        if doctype == line_login.IDENTITY:
            return "kc-subject" if filters.get("user") == "owner@example.test" else None

    def get_all(self, doctype, filters=None, fields=None, pluck=None, **kwargs):
        rows = self.bindings if doctype == line_login.BINDING else self.sessions
        found = []
        for row in rows:
            ok = True
            for key, value in (filters or {}).items():
                if isinstance(value, tuple):
                    ok = ok and bool(getattr(row, key, None))
                else:
                    ok = ok and getattr(row, key, None) == value
            if ok:
                found.append(row)
        return [getattr(r, pluck) for r in found] if pluck else found


class TestConfigAndEntry(Base):
    def test_alias_must_match_computed_value(self):
        self.assertEqual(line_login.enabled_alias(), ALIAS)
        self.native.conf["pearnly_line_login_idp"] = "line-999999999"
        self.assertEqual(line_login.enabled_alias(), "")
        self.native.conf["pearnly_line_login_idp"] = ALIAS
        # 站点的 LIFF 换到别的 LINE Login 频道（别的 Provider）：旧入口不再算开通。
        self.native.conf["pearnly_line_liff_id"] = "2099999999-other"
        self.assertEqual(line_login.enabled_alias(), "")
        self.native.conf["pearnly_line_liff_id"] = ""
        self.assertEqual(line_login.enabled_alias(), "")

    def test_unsafe_targets_fall_back_to_root(self):
        for bad in ("https://evil.test/", "//evil.test", "/\\evil", "/x\r\nSet-Cookie: a=b", None):
            self.assertEqual(line_login._local_path(bad), "/")
        self.assertEqual(line_login._local_path("/desk/purchase-invoice/line/LI-1"),
                         "/desk/purchase-invoice/line/LI-1")

    def test_enter_only_hints_line_for_guests_inside_line(self):
        identity = MagicMock()
        with patch.dict(sys.modules, {"pearnly_core.identity": identity}), \
                patch("pearnly_core.identity", identity, create=True):
            self.native.get_request_header.return_value = "Mozilla/5.0 Safari Line/14.9.0"
            line_login.enter("/desk/x")
            identity.begin_login_via.assert_called_once_with("/desk/x", idp_hint=ALIAS)

            identity.reset_mock()
            self.native.get_request_header.return_value = "Mozilla/5.0 Safari"
            line_login.enter("/desk/x")
            identity.begin_login_via.assert_not_called()
            self.assertEqual(self.native.local.response["location"], "/desk/x")

            self.native.get_request_header.return_value = "Mozilla/5.0 Line/14.9.0"
            self.native.session.user = "owner@example.test"
            line_login.enter("https://evil.test")
            identity.begin_login_via.assert_not_called()
            self.assertEqual(self.native.local.response["location"], "/")


class TestLoginSource(Base):
    def claims(self, idp=ALIAS, line=LINE_A):
        return {"sub": "kc-subject", "pearnly_idp": idp, "pearnly_line_user_id": line}

    def test_password_login_is_untouched(self):
        self.assertEqual(line_login.verify_login_source({"sub": "kc-subject"}, "owner@example.test"), {})

    def test_bound_line_passes(self):
        self.assertEqual(line_login.verify_login_source(self.claims(), "owner@example.test"),
                         {"identity_provider": ALIAS, "line_user_id": LINE_A})

    def test_other_site_other_line_unbound_disabled_or_not_enabled_are_refused(self):
        cases = [(self.claims(idp="line-999999999"), "owner@example.test"),
                 (self.claims(line=LINE_B), "owner@example.test"),
                 (self.claims(line="garbage"), "owner@example.test")]
        for claims, user in cases:
            with self.assertRaises(PermissionError_):
                line_login.verify_login_source(claims, user)
        self.bindings[0].status = "unbound"
        with self.assertRaises(PermissionError_):
            line_login.verify_login_source(self.claims(), "owner@example.test")
        self.bindings[0].status = "active"
        self.users["owner@example.test"] = 0
        with self.assertRaises(PermissionError_):
            line_login.verify_login_source(self.claims(), "owner@example.test")
        self.users["owner@example.test"] = 1
        self.native.conf.pop("pearnly_line_login_idp")
        with self.assertRaises(PermissionError_):
            line_login.verify_login_source(self.claims(), "owner@example.test")


class TestSessionsAndSync(Base):
    def test_only_stale_line_sessions_are_revoked(self):
        self.sessions = [
            SimpleNamespace(name="S1", user="owner@example.test", status="active", identity_provider=ALIAS,
                            line_user_id=LINE_A, session_id="sid-1"),
            SimpleNamespace(name="S2", user="owner@example.test", status="active", identity_provider=ALIAS,
                            line_user_id=LINE_B, session_id="sid-2"),
            SimpleNamespace(name="S3", user="owner@example.test", status="active", identity_provider=None,
                            line_user_id=None, session_id="sid-3"),
        ]
        with patch("frappe.sessions.delete_session") as delete:
            self.assertEqual(line_login.revoke_stale_line_sessions(["owner@example.test"]), 1)
            delete.assert_called_once_with("sid-2", user="owner@example.test", reason="LINE binding changed")
            self.bindings[0].status = "unbound"
            delete.reset_mock()
            self.assertEqual(line_login.revoke_stale_line_sessions(["owner@example.test"]), 2)

    def test_sync_links_active_binding_and_unlinks_after_unbind(self):
        client = MagicMock()
        with patch("pearnly_core.account.member_client", client, create=True), \
                patch.dict(sys.modules, {"pearnly_core.account.member_client": client}), \
                patch.object(line_login, "subject_of", return_value="kc-subject"), \
                patch("frappe.sessions.delete_session"):
            line_login.sync_user("owner@example.test")
            client.link_line.assert_called_once_with("kc-subject", LINE_A, "2010411313")
            self.native.db.set_value.assert_called_with(line_login.BINDING, "B1", {
                "login_link": "linked", "login_link_at": "2026-09-26 12:00:00"}, update_modified=False)
            self.bindings[0].status = "unbound"
            line_login.sync_user("owner@example.test")
            client.unlink_line.assert_called_once_with("kc-subject", "2010411313")
            self.assertEqual(self.native.db.set_value.call_args[0][2]["login_link"], "unlinked")

    def test_failed_sync_is_marked_for_reconcile(self):
        client = MagicMock()
        client.link_line.side_effect = RuntimeError("unavailable")
        with patch.dict(sys.modules, {"pearnly_core.account.member_client": client}), \
                patch("pearnly_core.account.member_client", client, create=True), \
                patch.object(line_login, "subject_of", return_value="kc-subject"), \
                patch("frappe.sessions.delete_session"):
            line_login.sync_user("owner@example.test")
        self.assertEqual(self.native.db.set_value.call_args[0][2]["login_link"], "failed")
        self.native.db.rollback.assert_called_once()

    def test_reconcile_picks_only_rows_out_of_step(self):
        row = lambda status, link, enabled: SimpleNamespace(status=status, login_link=link, enabled=enabled)
        self.assertTrue(line_login._needs_sync(row("active", "", 1)))  # 上线时已有绑定
        self.assertTrue(line_login._needs_sync(row("active", "failed", 1)))
        self.assertTrue(line_login._needs_sync(row("active", "linked", 0)))  # 账号停用
        self.assertTrue(line_login._needs_sync(row("unbound", "linked", 1)))
        self.assertFalse(line_login._needs_sync(row("active", "linked", 1)))
        self.assertFalse(line_login._needs_sync(row("unbound", "", 1)))
        self.native.conf.pop("pearnly_line_login_idp")
        line_login.reconcile()
        self.native.db.sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
