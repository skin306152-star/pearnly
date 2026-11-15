"""主档按打的字找的规则（`masters/search_rules.py`）：选择框、LINE 选客户 / 供应商、收款选客户同一份（11-15 真机）。

No Frappe needed: the module is pure.
"""
import importlib.util
import json
import re
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

    def test_a_prefix_typed_together_with_the_name_still_finds_the_full_name(self):
        # 主档显示名是「前缀 + 空格 + 核心名」（CC 桌面版 1.12.22 报 PRENAM）；人常连着打
        self.assertEqual(rules.pattern("ร้านลีฟ"), "%ร้าน%ลีฟ%")
        self.assertEqual(rules.pattern("บริษัทเอบีซี จำกัด"), "%บริษัท%เอบีซี%จำกัด%")
        self.assertEqual(rules.pattern("หจก.ทอง"), "%หจก.%ทอง%")
        self.assertEqual(rules.pattern("ร้าน"), "%ร้าน%")  # 只打前缀不拆
        stored = ["ร้าน ลีฟทดสอบ 1115", "ร้านลีฟทดสอบ 1115"]
        for typed in ("ร้านลีฟ", "ร้าน ลีฟ", "ลีฟทดสอบ"):
            like = ".*".join(map(re.escape, rules.pattern(typed).strip("%").split("%")))  # ``like`` 的「%」= 任意串
            for name in stored:
                self.assertRegex(name, like, typed)

    def test_or_filters_cover_the_record_name_and_every_column(self):
        found = rules.or_filters("ERP Customer", "0105599210021")
        self.assertEqual([row[0] for row in found], ["name", "customer_name", "express_code", "tax_id", "contact_phone"])
        self.assertTrue(all(row[1:] == ["like", "%0105599210021%"] for row in found))
        self.assertEqual(rules.or_filters("ERP Customer", "   "), [])


if __name__ == "__main__":
    unittest.main()
