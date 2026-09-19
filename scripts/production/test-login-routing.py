"""Routing contracts without a database or identity-provider dependency."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.conf = {"pearnly_product": "dms"}
path = Path(__file__).resolve().parents[2] / "apps/pearnly_core/pearnly_core/workspace.py"
spec = importlib.util.spec_from_file_location("workspace", path)
workspace = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"frappe": frappe}):
    spec.loader.exec_module(workspace)


class Routing(unittest.TestCase):
    def test_product_entry(self):
        for product in ("dms", "erp", "cowork"):
            frappe.conf["pearnly_product"] = product
            for path in (None, "", "/", "/login", "/desk", "/app/"):
                self.assertEqual(workspace.resolve_after_login(path), "/pearnly-app")

    def test_deep_link(self):
        self.assertEqual(workspace.resolve_after_login("/pearnly-app/roster"), "/pearnly-app/roster")
        self.assertEqual(workspace.resolve_after_login("/desk/user/me"), "/desk/user/me")

    def test_no_external_redirect(self):
        for path in ("https://evil.invalid", "//evil.invalid", "/\\evil.invalid", "/x\n"):
            self.assertEqual(workspace.resolve_after_login(path), "/pearnly-app")


if __name__ == "__main__":
    unittest.main()
