"""登录回调：零 IO 测试（框架、身份服务换掉）。重复回调续上、LINE 按本站绑定认人、被拒先注销。"""
import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pearnly_core import identity
from pearnly_core.account import line_login

ISSUER = "https://auth.example.test/realms/pearnly-business"
LINE = "U" + "a" * 32


class PermissionError_(Exception):
    pass


class Store:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value

    def delete_value(self, key):
        self.values.pop(key, None)

    def lock(self, *args, **kwargs):
        return contextlib.nullcontext()


class Callback(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.native.PermissionError = PermissionError_
        self.native.throw.side_effect = lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))
        self.native.local = SimpleNamespace(response={}, pearnly_verified_tokens={
            "id_token": "id-token", "refresh_token": "refresh-token"})
        self.native.session = SimpleNamespace(user="Guest")
        self.native.conf = {"pearnly_oidc_issuer": ISSUER}
        self.store = Store()
        self.claims = {"sub": "tenant-subject", "iss": ISSUER, "sid": "kc-sid"}
        self.mocks = {}
        for target, value in (("frappe", self.native), ("_", str),
                              ("_exchange_and_verify", lambda code, saved: dict(self.claims)),
                              ("_resolve_user", MagicMock(return_value="owner@example.test")),
                              ("_login_as", MagicMock()), ("_begin_login", MagicMock()),
                              ("oidc_session", MagicMock()), ("preferences", MagicMock())):
            p = patch.object(identity, target, value)
            self.mocks[target] = p.start()
            self.addCleanup(p.stop)
        p = patch.object(identity.pearnly_state, "store", return_value=self.store)
        p.start()
        self.addCleanup(p.stop)
        self.workspace = MagicMock()
        self.workspace.resolve_after_login.side_effect = lambda target: target or "/app"
        self.profile = MagicMock()
        modules = {"pearnly_core.workspace": self.workspace, "pearnly_core.account.profile": self.profile}
        p = patch.dict("sys.modules", modules)
        p.start()
        self.addCleanup(p.stop)
        for name, value in (("workspace", self.workspace),):
            p = patch(f"pearnly_core.{name}", value, create=True)
            p.start()
            self.addCleanup(p.stop)

    def begin(self, state="s1", redirect_to="/desk/x", idp_hint=None, locale=None, picked=None):
        self.store.values[identity._state_key(state)] = {
            "nonce": "n", "verifier": "v", "redirect_to": redirect_to, "idp_hint": idp_hint, "expected_user": None,
            "locale": locale, "picked": picked}

    def test_language_shown_at_the_entry_reaches_the_language_rule(self):
        # 入口页这次显示的语言与是不是点的都交给 preferences（规则在 ui_language：点的、或这台设备第一次登录才用它）
        self.begin(locale="en", picked=True)
        self.claims["locale"] = "zh-CN"
        identity.callback(code="c", state="s1")
        self.mocks["preferences"].finish_login_locale.assert_called_once_with("owner@example.test", "zh-CN", "en", True)
        self.mocks["preferences"].reset_mock()
        self.begin(state="s2", locale="th")  # 没点，只是按系统语言显示的泰文
        identity.callback(code="c", state="s2")
        self.mocks["preferences"].finish_login_locale.assert_called_once_with("owner@example.test", "zh-CN", "th", False)

    def test_password_login_resolves_by_subject(self):
        self.begin()
        identity.callback(code="c", state="s1")
        self.mocks["_resolve_user"].assert_called_once_with(ISSUER, "tenant-subject")
        self.mocks["_login_as"].assert_called_once_with("owner@example.test")
        self.mocks["oidc_session"].remember.assert_called_once_with(
            self.native.local.pearnly_verified_tokens, "tenant-subject")
        self.profile.apply_login_username.assert_called_once()
        self.assertEqual(self.native.local.response["location"], "/desk/x")

    def test_line_login_resolves_by_this_sites_binding_not_by_subject(self):
        self.begin(idp_hint="line-2010411313")
        self.claims.update(sub="line-person", pearnly_idp="line-2010411313", pearnly_line_user_id=LINE)
        source = {"identity_provider": "line-2010411313", "line_user_id": LINE}
        with patch.object(line_login, "resolve_line_login", return_value=("owner@example.test", source)) as resolve:
            identity.callback(code="c", state="s1")
        resolve.assert_called_once()
        self.mocks["_resolve_user"].assert_not_called()
        self.mocks["_login_as"].assert_called_once_with("owner@example.test")
        self.assertEqual(self.native.local.pearnly_kc["line_user_id"], LINE)
        # 专用身份不是本站账号：不留刷新令牌、不拿它的用户名和语言。
        self.mocks["oidc_session"].remember.assert_called_once_with({"id_token": "id-token"}, "line-person")
        self.profile.apply_login_username.assert_not_called()
        self.mocks["preferences"].finish_login_locale.assert_called_once_with("owner@example.test", None, None, False)

    def test_refused_login_ends_the_identity_session_and_explains(self):
        for line in (False, True):
            self.native.reset_mock()
            self.begin()
            self.mocks["_login_as"].reset_mock()
            if line:
                self.claims.update(pearnly_idp="line-2010411313", pearnly_line_user_id=LINE)
            refusal = PermissionError_("This LINE account is not bound")
            with patch.object(line_login, "resolve_line_login", side_effect=refusal), \
                    patch.object(line_login, "end_session_url", return_value="https://auth/logout?hint"):
                self.mocks["_resolve_user"].side_effect = refusal
                identity.callback(code="c", state="s1")
            self.mocks["_login_as"].assert_not_called()
            args, kwargs = self.native.respond_as_web_page.call_args
            self.assertEqual(args[1], "This LINE account is not bound")
            self.assertEqual((kwargs["http_status_code"], kwargs["primary_action"]), (403, "https://auth/logout?hint"))

    def test_duplicate_callback_resumes_instead_of_failing(self):
        self.begin(idp_hint="line-2010411313")
        identity.callback(code="c", state="s1")
        self.mocks["_login_as"].reset_mock()
        # LINE 内置浏览器把同一个回调又发了一次，而且第一次的登录 cookie 没到手：重新走一遍。
        identity.callback(code="c", state="s1")
        self.mocks["_login_as"].assert_not_called()
        self.mocks["_begin_login"].assert_called_once_with("/desk/x", idp_hint="line-2010411313", locale=None,
                                                           picked=None)
        # 已经登录了：直接去目标页。
        self.native.session.user = "owner@example.test"
        identity.callback(code="c", state="s1")
        self.assertEqual(self.native.local.response["location"], "/desk/x")
        self.mocks["_begin_login"].assert_called_once()

    def test_unknown_or_expired_state_never_logs_in_and_starts_over_once(self):
        """state 查不到（伪造、或在登录页停了超过 10 分钟已过期）：绝不用这次回跳的凭据登录；重新走一遍登录
        （身份服务刚登录过会立刻回来），60 秒内第二次仍然拒绝，免得来回跳（2026-10-11 ris 停了 12 分钟报 417）。"""
        self.native.local.cookie_manager = MagicMock()
        self.native.request = SimpleNamespace(cookies={})
        identity.callback(code="c", state="never-issued")
        self.mocks["_login_as"].assert_not_called()
        self.mocks["_begin_login"].assert_called_once_with(None)
        self.native.local.cookie_manager.set_cookie.assert_called_once()
        self.native.request = SimpleNamespace(cookies={identity.RETRY_COOKIE: "1"})
        with self.assertRaises(Exception):
            identity.callback(code="c", state="never-issued")
        self.mocks["_login_as"].assert_not_called()
        self.mocks["_begin_login"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
