"""团队管理「添加用户」邮箱留空时的占位邮箱（`pearnly_core.account.placeholder_email`，用户 2026-10-27）。

留空补成 `<登录名>.<本站域名前缀>@pearnly.com`（全小写）；填了就必须是合法邮箱（与控制面开户单同一条）。
ERP / Cowork 团队管理走一遍的实站验证见 docs/work/2026-10-27-team-placeholder-email.md。
"""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
frappe = types.ModuleType("frappe")
frappe.local = types.SimpleNamespace(site="polymer.pearnly.com")
sys.modules.setdefault("frappe", frappe)
spec = importlib.util.spec_from_file_location(
    "placeholder_email", ROOT / "apps/pearnly_core/pearnly_core/account/placeholder_email.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.frappe = frappe


class PlaceholderEmail(unittest.TestCase):
    def test_blank_email_becomes_login_name_dot_site_prefix(self):
        frappe.local.site = "Polymer.pearnly.com"
        self.assertEqual(module.for_user("Somchai01"), "somchai01.polymer@pearnly.com")
        frappe.local.site = "erp-nb.localhost"
        self.assertEqual(module.for_user("bob"), "bob.erp-nb@pearnly.com")

    def test_typed_email_must_be_valid(self):
        for good in ("a@b.co", "somchai@polymer.co.th"):
            self.assertTrue(module.valid(good))
        for bad in ("", "a@b", "ab.co", "a b@c.co", "a@@b.co"):
            self.assertFalse(module.valid(bad), bad)

    def test_both_team_services_use_it(self):
        for app in ("pearnly_erp_portal", "pearnly_cowork"):
            source = (ROOT / f"apps/{app}/{app}/team/service.py").read_text(encoding="utf-8")
            self.assertIn("placeholder_email.for_user(username)", source)
            self.assertIn("placeholder_email.valid(email)", source)
            self.assertNotIn('if "@" not in email:', source)
            js = (ROOT / f"apps/{app}/{app}/public/desk/team.js").read_text(encoding="utf-8")
            self.assertIn('{ options: "Email", read_only: is_new ? 0 : 1,', js)  # 邮箱栏非必填


if __name__ == "__main__":
    unittest.main()
