"""主档按打的字找的规则（`masters/search_rules.py`）：选择框、LINE 选客户 / 供应商、收款选客户同一份（11-15 真机）。

No Frappe needed: the module is pure.
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "pearnly_erp_portal" / "pearnly_erp_portal"
spec = importlib.util.spec_from_file_location("search_rules", APP / "masters" / "search_rules.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


class SearchRules(unittest.TestCase):
    def test_columns_are_the_doctype_search_fields_with_the_title_first(self):
        for doctype, title in rules.TITLES.items():
            folder = doctype.lower().replace(" ", "_")
            meta = json.loads((APP / "pearnly_erp_portal" / "doctype" / folder / f"{folder}.json").read_text())
            fields = [field.strip() for field in meta["search_fields"].split(",") if field.strip()]
            self.assertEqual(rules.columns(doctype), tuple(dict.fromkeys([title, *fields])), doctype)

    def test_several_words_must_all_be_there_in_order(self):
        self.assertEqual(rules.pattern("  rice   co "), "%rice%co%")

    def test_a_phone_is_compared_by_its_digits(self):
        # 11-15 真机：库里 081-234-5678，打 0812345678 搜不到；反过来也一样
        self.assertEqual(rules.phone_digits("0812345678"), "0812345678")
        self.assertEqual(rules.phone_digits(" 081-234 5678 "), "0812345678")
        self.assertEqual(rules.phone_digits("+66 (81) 234.5678"), "66812345678")
        self.assertEqual(rules.phone_digits("ร้าน 081"), "")   # 夹着字的不当电话
        self.assertEqual(rules.phone_digits("12"), "")
        expr = rules.phone_column("contact_phone")
        for mark in ("-", " ", ".", "(", ")"):
            self.assertIn(f"'{mark}', ''", expr)
        self.assertIn("ifnull(`contact_phone`, '')", expr)
        self.assertEqual(set(rules.PHONES), {"ERP Customer", "ERP Supplier"})
        for doctype, columns in rules.PHONES.items():
            self.assertTrue(set(columns) <= set(rules.columns(doctype)), doctype)


if __name__ == "__main__":
    unittest.main()
