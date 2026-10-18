"""产品站点出错不回堆栈（`pearnly_core.desk.error_detail`，用户 2026-10-18「关掉」）：系统设置 allow_error_traceback 在产品站点
每次 migrate 都关掉，控制面不动；挂在 after_migrate。不连库、不依赖 Frappe（最小替身）。"""
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"


def _load(settings, product, writes):
    frappe = types.ModuleType("frappe")
    frappe.conf = {"pearnly_product": product} if product else {}

    def set_single_value(doctype, field, value):
        writes.append((doctype, field, value))
        settings[(doctype, field)] = value

    frappe.db = types.SimpleNamespace(get_single_value=lambda doctype, field: settings.get((doctype, field)),
                                      set_single_value=set_single_value)
    sys.modules["frappe"] = frappe
    spec = importlib.util.spec_from_file_location("error_detail_under_test", CORE / "desk/error_detail.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ErrorDetailTests(unittest.TestCase):
    KEY = ("System Settings", "allow_error_traceback")

    def run_with(self, value, product="cowork"):
        settings, writes = {self.KEY: value}, []
        _load(settings, product, writes).ensure_traceback_hidden()
        return settings[self.KEY], writes

    def test_product_sites_hide_the_traceback(self):
        for product in ("cowork", "erp", "dms"):
            self.assertEqual(self.run_with(1, product)[0], 0, product)
        self.assertEqual(self.run_with("1")[0], 0)

    def test_already_off_is_not_rewritten_and_the_control_plane_is_kept(self):
        self.assertEqual(self.run_with(0), (0, []))
        self.assertEqual(self.run_with(None), (None, []))
        self.assertEqual(self.run_with(1, product=None), (1, []))

    def test_it_runs_on_every_migrate(self):
        hooks = (CORE / "hooks.py").read_text(encoding="utf-8")
        after = hooks.split("after_migrate = [", 1)[1].split("]", 1)[0]
        self.assertIn('"pearnly_core.desk.error_detail.ensure_traceback_hidden"', after)


if __name__ == "__main__":
    unittest.main()
