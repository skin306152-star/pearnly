"""页面 JS 报错回传（pearnly_core.desk.client_errors）：去个人信息、同一条一小时只记一次、按人 / 按站限频、未登录不收。"""
import importlib
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))


class Cache:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value


def fake_frappe():
    frappe = types.ModuleType("frappe")
    frappe.cache = Cache()
    frappe.session = types.SimpleNamespace(user="staff@x")
    frappe.logged = []
    frappe.log_error = lambda title, message: frappe.logged.append((title, json.loads(message)))
    frappe.whitelist = lambda **kw: (lambda fn: fn)
    return frappe


class ClientErrors(unittest.TestCase):
    def setUp(self):
        self.frappe = fake_frappe()
        sys.modules["frappe"] = self.frappe
        sys.modules.pop("pearnly_core.desk.client_errors", None)
        self.module = importlib.import_module("pearnly_core.desk.client_errors")
        self.addCleanup(sys.modules.pop, "frappe", None)

    def test_personal_data_and_query_strings_are_removed(self):
        self.module.report(message="Cannot read x of undefined for somchai@mail.co.th 0812345678",
                           source="https://erp.example/assets/a.js?v=1#x", line=12, column=3,
                           stack="at f (https://erp.example/desk/pearnly-sales/SD-0105545112901?q=นาย)",
                           route="/desk/pearnly-sales/view")
        title, entry = self.frappe.logged[0]
        self.assertTrue(title.startswith("Browser error: "))
        text = json.dumps(entry, ensure_ascii=False)
        for secret in ("somchai@", "0812345678", "0105545112901", "?v=1", "?q=", "นาย"):
            self.assertNotIn(secret, text)
        self.assertEqual((entry["line"], entry["column"], entry["route"]), (12, 3, "/desk/pearnly-sales/view"))

    def test_the_same_error_is_logged_once_and_each_user_is_capped(self):
        for _ in range(3):
            self.module.report(message="boom", source="/assets/x.js", line=1, route="/desk/pearnly-home")
        self.assertEqual(len(self.frappe.logged), 1)
        for n in range(40):
            self.module.report(message=f"boom {n}", source="/assets/x.js", line=n + 2, route="/desk/pearnly-home")
        self.assertEqual(len(self.frappe.logged), self.module.PER_USER)

    def test_guests_and_empty_reports_are_ignored(self):
        self.assertEqual(self.module.report(message="", stack=""), {"ok": False})
        self.frappe.session.user = "Guest"
        self.assertEqual(self.module.report(message="boom"), {"ok": False})
        self.assertEqual(self.frappe.logged, [])


if __name__ == "__main__":
    unittest.main()
