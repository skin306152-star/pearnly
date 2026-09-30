#!/usr/bin/env python3
"""No Frappe: the LINE setup guide page renders with every screenshot embedded."""
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch

MODULE = Path(__file__).resolve().parents[1] / "apps/pearnly_control/pearnly_control/onboarding/line_guide.py"


class LineGuide(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe.whitelist = lambda **kwargs: (lambda fn: fn)
        frappe.only_for = Mock()
        frappe.local = types.SimpleNamespace(lang="th")
        modules = patch.dict(sys.modules, {"frappe": frappe})
        modules.start()
        self.addCleanup(modules.stop)
        self.frappe = frappe
        self.ns = {"__file__": str(MODULE)}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), self.ns)

    def test_every_screenshot_is_embedded(self):
        html = self.ns["render"]("zh")
        self.assertNotIn('src="img/', html)
        self.assertNotIn('src=""', html)  # a referenced file that does not exist
        self.assertGreaterEqual(html.count('src="data:image/jpeg;base64,'), 20)

    def test_every_shipped_screenshot_is_used(self):
        source = (MODULE.parent / "line_guide_content" / "zh.html").read_text()
        used = set(re.findall(r'src="img/([a-z0-9_]+\.jpg)"', source))
        shipped = {p.name for p in (MODULE.parent / "line_guide_content" / "img").glob("*.jpg")}
        self.assertEqual(used, shipped)

    def test_missing_language_falls_back_to_chinese_and_needs_system_manager(self):
        result = self.ns["content"]()
        self.frappe.only_for.assert_called_once_with("System Manager")
        self.assertIn("Webhook", result["html"])


if __name__ == "__main__":
    unittest.main()
