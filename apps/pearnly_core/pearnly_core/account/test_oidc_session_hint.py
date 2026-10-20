"""Offline: where the OIDC logout hint lives (no Site, Redis or network).

10-20：退出时没带 id_token_hint，身份服务就出「您确定要注销吗？」确认页。会话里那份提示原来写在 `frappe.session` 顶层，
框架只把 `frappe.session.data` 写进 Sessions 表、下次请求也只恢复这一层，所以那份从来没留下来；Redis 那份一丢就掉到确认页。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pearnly_core.account import oidc_session


class _Dict(dict):
    __getattr__ = dict.get


class HintTests(unittest.TestCase):
    def setUp(self):
        self.session_obj = MagicMock()
        self.native = SimpleNamespace(
            session=_Dict(user="preview@example.test", sid="session-a", data=_Dict()),
            local=SimpleNamespace(session_obj=self.session_obj),
        )
        self.store = MagicMock()
        self.store.get_value.return_value = None
        p = patch.object(oidc_session, "frappe", self.native)
        p.start()
        self.addCleanup(p.stop)
        p = patch("pearnly_core.execution.state.store", return_value=self.store)
        p.start()
        self.addCleanup(p.stop)
        p = patch("frappe.sessions.get_expiry_in_seconds", return_value=3600)
        p.start()
        self.addCleanup(p.stop)

    def test_hint_is_kept_in_the_persisted_session_data(self):
        oidc_session.remember({"id_token": "id-1", "refresh_token": "r-1"}, "subject-a")
        self.assertEqual(self.native.session.data[oidc_session.SESSION_HINT], "id-1")
        self.assertNotIn(oidc_session.SESSION_HINT, {k for k in self.native.session if k != "data"})
        self.session_obj.update.assert_called_once_with(force=True)   # 会话记录已插入，必须写回一次

    def test_unchanged_hint_does_not_rewrite_the_session(self):
        self.native.session.data[oidc_session.SESSION_HINT] = "id-1"
        oidc_session.remember({"id_token": "id-1", "refresh_token": "r-2"}, "subject-a")
        self.session_obj.update.assert_not_called()

    def test_logout_uses_the_session_copy_when_redis_lost_it(self):
        self.native.session.data[oidc_session.SESSION_HINT] = "id-1"
        self.store.get_value.return_value = None
        self.assertEqual(oidc_session.logout_hint(), "id-1")

    def test_logout_falls_back_to_redis(self):
        self.store.get_value.return_value = {"id_token": "id-redis"}
        self.assertEqual(oidc_session.logout_hint(), "id-redis")

    def test_forget_clears_both_copies(self):
        self.native.session.data[oidc_session.SESSION_HINT] = "id-1"
        oidc_session.forget()
        self.store.delete_value.assert_called_once_with("pearnly_oidc_session:session-a")
        self.assertNotIn(oidc_session.SESSION_HINT, self.native.session.data)

    def test_guest_session_is_never_written(self):
        self.native.session["user"] = "Guest"
        oidc_session.remember({"id_token": "id-1"}, "subject-a")
        self.session_obj.update.assert_not_called()


class SignOutHintTests(unittest.TestCase):
    ISSUER = "https://auth.example.test/realms/pearnly-business"

    def token(self, **claims):
        import jwt
        body = {"iss": self.ISSUER, "aud": "site-client", "azp": "site-client", "exp": 1, "sid": "s"}
        body.update(claims)
        return jwt.encode(body, "test-only-key-not-used-for-anything-0000", algorithm="HS256")

    def usable(self, hint, client="site-client"):
        from pearnly_core import identity
        with patch.object(identity, "issuer", return_value=self.ISSUER):
            return identity._usable_logout_hint(hint, client)

    def test_this_realms_hint_is_sent_even_when_expired(self):
        hint = self.token()          # exp=1：早就过期，身份服务照收
        self.assertEqual(self.usable(hint), hint)

    def test_hint_from_another_pool_is_dropped(self):
        self.assertIsNone(self.usable(self.token(iss="https://auth.example.test/realms/pearnly-cowork")))

    def test_hint_for_another_client_is_dropped(self):
        self.assertIsNone(self.usable(self.token(aud="other", azp="other")))
        hint = self.token(aud=["other", "site-client"], azp="other")
        self.assertEqual(self.usable(hint), hint)

    def test_missing_or_broken_hint_is_dropped(self):
        self.assertIsNone(self.usable(None))
        self.assertIsNone(self.usable("not-a-token"))


if __name__ == "__main__":
    unittest.main()
