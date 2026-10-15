"""产品站点登录保持 30 天（`pearnly_core.account.session_length`，用户 2026-10-15「30天吧」）：只改空值或框架默认值，
管理员改过的保留，控制面不动；挂在 after_migrate。不连库、不依赖 Frappe（最小替身）。"""
import importlib.util
import pathlib
import sys
import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
