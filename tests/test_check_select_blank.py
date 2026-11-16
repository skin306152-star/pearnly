"""Select 空值闸门（`scripts/check-select-blank.py`）：非必填、无默认、首行不空的 Select 存空会被 Frappe 补成第一项。

11-16 验收：未收款的销售单「收款方式」存成了 cash。纯文件检查，不需要 Frappe。
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_select_blank", ROOT / "scripts/check-select-blank.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
SALES = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype/erp_sales_document/erp_sales_document.json"


class SelectBlank(unittest.TestCase):
    def test_the_repository_passes(self):
        self.assertEqual(check.main(), 0)

    def test_the_sales_payment_method_can_stay_empty(self):
        field = next(f for f in json.loads(SALES.read_text(encoding="utf-8"))["fields"] if f["fieldname"] == "payment_method")
        self.assertTrue(field["options"].startswith("\n"))  # 首行留空：未收款的单不再被补成 cash
        self.assertFalse(field.get("default"))
        self.assertNotIn(("ERP Sales Document", "payment_method"), check.ALLOW)

    def test_every_exception_says_why(self):
        for key, reason in check.ALLOW.items():
            with self.subTest(field=key):
                self.assertTrue(reason.strip())

    def test_a_new_field_of_this_kind_fails(self):
        risks = check.blank_risks()
        self.assertTrue(all((doctype, field) in check.ALLOW for doctype, field, _first in risks))
        original = check.blank_risks
        check.blank_risks = lambda: risks + [("ERP Demo", "kind", "first")]
        try:
            self.assertEqual(check.main(), 1)
        finally:
            check.blank_risks = original


if __name__ == "__main__":
    unittest.main()
