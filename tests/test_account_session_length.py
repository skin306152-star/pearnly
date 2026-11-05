"""产品站点登录保持 30 天（`pearnly_core.account.session_length`，用户 2026-10-15「30天吧」）：只改空值或框架默认值，
管理员改过的保留，控制面不动；挂在 after_migrate。不连库、不依赖 Frappe（最小替身）。

Keycloak 这边（用户 2026-11-03）：产品 realm 勾「记住我」的会话也保持 30 天（`infrastructure/production/bootstrap-auth.py`
的 REMEMBER_ME），不勾的普通会话不动。用假 Keycloak 跑脚本本身，不连真服务。"""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import sys
import types
import unittest
from unittest import mock
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"


def _load(settings, product):
    frappe = types.ModuleType("frappe")
    frappe.conf = {"pearnly_product": product} if product else {}
    frappe.db = types.SimpleNamespace(
        get_single_value=lambda doctype, field: settings.get((doctype, field)),
        set_single_value=lambda doctype, field, value: settings.__setitem__((doctype, field), value))
    sys.modules["frappe"] = frappe
    spec = importlib.util.spec_from_file_location("session_length_under_test", CORE / "account/session_length.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionLengthTests(unittest.TestCase):
    KEY = ("System Settings", "session_expiry")

    def run_with(self, value, product="cowork"):
        settings = {self.KEY: value}
        _load(settings, product).ensure_session_expiry()
        return settings[self.KEY]

    def test_product_sites_default_to_thirty_days(self):
        for product in ("cowork", "erp", "dms"):
            self.assertEqual(self.run_with("170:00", product), "720:00", product)
        self.assertEqual(self.run_with(None), "720:00")
        self.assertEqual(self.run_with(""), "720:00")

    def test_an_administrators_own_value_and_the_control_plane_are_kept(self):
        self.assertEqual(self.run_with("48:00"), "48:00")
        self.assertEqual(self.run_with("720:00"), "720:00")
        self.assertEqual(self.run_with("170:00", product=None), "170:00")

    def test_it_runs_on_every_migrate(self):
        hooks = (CORE / "hooks.py").read_text(encoding="utf-8")
        after = hooks.split("after_migrate = [", 1)[1].split("]", 1)[0]
        self.assertIn('"pearnly_core.account.session_length.ensure_session_expiry"', after)



BOOTSTRAP = ROOT / "infrastructure/production/bootstrap-auth.py"
THIRTY_DAYS = 30 * 86400
REMEMBER_ME_KEYS = ("ssoSessionIdleTimeoutRememberMe", "ssoSessionMaxLifespanRememberMe")


class _FakeKeycloak:
    """管理接口的最小替身：记下每个请求；realm 的 PUT 照 Keycloak 只改带来的字段。"""

    def __init__(self, realm):
        # Keycloak 默认：普通会话空闲 30 分钟、最长 10 小时；记住我两项 0（= 跟普通会话一样）
        self.realm = {"realm": realm, "rememberMe": True, "ssoSessionIdleTimeout": 1800,
                      "ssoSessionMaxLifespan": 36000, REMEMBER_ME_KEYS[0]: 0, REMEMBER_ME_KEYS[1]: 0}
        self.calls = []

    def __call__(self, request, timeout=None):
        method, path = request.get_method(), urlsplit(request.full_url).path
        body = json.loads(request.data) if request.data and "json" in request.get_header("Content-type") else None
        self.calls.append((method, path, body))
        if path.endswith("/openid-connect/token"):
            answer = {"access_token": "t"}
        elif path == "/admin/realms/" + self.realm["realm"] and method == "PUT":
            self.realm.update(body)
            answer = None
        elif path == "/admin/realms/" + self.realm["realm"]:
            answer = dict(self.realm)
        else:
            raise AssertionError(f"unexpected {method} {path}")
        return mock.MagicMock(**{"__enter__.return_value.read.return_value":
                                 json.dumps(answer).encode() if answer is not None else b""})


def _run_bootstrap(argv, keycloak):
    code = compile(BOOTSTRAP.read_text(encoding="utf-8"), str(BOOTSTRAP), "exec")
    with mock.patch("urllib.request.urlopen", keycloak), \
            mock.patch.object(pathlib.Path, "read_text", lambda self, *a, **k: "KEYCLOAK_ADMIN_PASSWORD=x\n"), \
            mock.patch.object(sys, "argv", ["bootstrap-auth.py", *argv]), \
            mock.patch("sys.stdout", io.StringIO()) as out:
        with unittest.TestCase().assertRaises(SystemExit) as stop:
            exec(code, {"__name__": "__main__"})
    return stop.exception.code, out.getvalue()


class KeycloakRememberMeTests(unittest.TestCase):
    def test_remember_me_only_sets_the_two_fields_to_thirty_days(self):
        for realm in ("pearnly-business", "pearnly-cowork"):
            keycloak = _FakeKeycloak(realm)
            argv = ["--remember-me"] + (["--realm", realm] if realm != "pearnly-business" else [])
            code, out = _run_bootstrap(argv, keycloak)
            self.assertIn(code, (0, None), out)
            puts = [body for method, _, body in keycloak.calls if method == "PUT"]
            self.assertEqual(puts, [{"realm": realm, REMEMBER_ME_KEYS[0]: THIRTY_DAYS,
                                     REMEMBER_ME_KEYS[1]: THIRTY_DAYS}])
            self.assertEqual((keycloak.realm["ssoSessionIdleTimeout"], keycloak.realm["ssoSessionMaxLifespan"]),
                             (1800, 36000))                      # 不勾记住我的普通会话不变
            self.assertEqual([method for method, _, _ in keycloak.calls], ["POST", "GET", "PUT", "GET"])

    def test_remember_me_refuses_a_realm_without_the_checkbox(self):
        keycloak = _FakeKeycloak("pearnly-business")
        keycloak.realm["rememberMe"] = False
        code, _ = _run_bootstrap(["--remember-me"], keycloak)
        self.assertIn("Remember me is off", str(code))
        self.assertFalse([call for call in keycloak.calls if call[0] == "PUT"])

    def test_full_bootstrap_and_the_sample_copy_carry_the_same_fields(self):
        source = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn('REMEMBER_ME = {"ssoSessionIdleTimeoutRememberMe": 2592000, '
                      '"ssoSessionMaxLifespanRememberMe": 2592000}', source)
        # 导入不连 Keycloak、不读密钥（部署前站外设置对比 check-external-config.py 照同一份核对）
        loader = importlib.machinery.SourceFileLoader("bootstrap_auth", str(BOOTSTRAP))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader("bootstrap_auth", loader))
        loader.exec_module(module)
        settings = module.realm_settings("pearnly-cowork")
        self.assertEqual(settings["realm"], "pearnly-cowork")
        for key in REMEMBER_ME_KEYS:
            self.assertEqual(settings[key], THIRTY_DAYS)
            self.assertIn(key, module.READBACK_KEYS)
        self.assertNotIn("ssoSessionIdleTimeout", settings)     # 普通会话不在完整初始化里设
        self.assertNotIn("ssoSessionMaxLifespan", settings)
        self.assertIn("assert all(readback.get(key) == settings[key] for key in READBACK_KEYS)", source)
        sample = (ROOT / "scripts/sample-cowork-realm.py").read_text(encoding="utf-8")
        copy = sample.split("COPY = (", 1)[1].split(")", 1)[0]
        for key in REMEMBER_ME_KEYS:
            self.assertIn(f'"{key}"', copy)

if __name__ == "__main__":
    unittest.main()
