"""单据编号的形状与前缀：这是收发存报表"关联单据"那一列的取值来源。

口径来自旧线上生产版本 `services/erp/document_numbers.py`
（固定源 ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188）：前缀固定、佛历日期、当日流水。
这些断言不需要 Frappe，也不连库。
"""
from datetime import date
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))

from pearnly_erp_portal.numbering import contracts  # noqa: E402


class Prefixes(unittest.TestCase):
    def test_the_four_business_prefixes_are_fixed(self):
        """采购 PE / 销售 SI / 入库 IR / 出库 IS —— 与旧线上逐字相同。"""
        self.assertEqual(contracts.BY_DIRECTION,
                         {"purchase": "PE", "sales": "SI", "in": "IR", "out": "IS"})
        self.assertEqual(contracts.PREFIXES, ("PE", "SI", "IR", "IS"))

    def test_the_old_production_source_pins_the_same_letters(self):
        """把口径钉在旧线上那份实现上：四个字母必须仍然出现在它的映射里。"""
        old = Path("/Users/skin/Developer/Pearnly/pearnly-app")
        if not (old / ".git").exists():
            self.skipTest("旧项目不在本机")
        source = (old / "services/erp/document_numbers.py")
        if not source.is_file():
            self.skipTest("旧项目工作树里没有这个文件")
        text = source.read_text(encoding="utf-8")
        self.assertIn('"purchase": "PE", "sales": "SI", "in": "IR", "out": "IS"', text)
        self.assertIn("year + 543", text, "旧线上日期是佛历年")

    def test_an_unknown_direction_is_refused(self):
        with self.assertRaises(ValueError):
            contracts.prefix_for("returns")
        with self.assertRaises(ValueError):
            contracts.prefix_for(None)


class Shape(unittest.TestCase):
    def test_the_number_is_prefix_buddhist_day_and_serial(self):
        """`IR-25690922-0001`：佛历 2569 = 公历 2026。"""
        self.assertEqual(contracts.format_number("IR", date(2026, 9, 22), 1), "IR-25690922-0001")
        self.assertEqual(contracts.format_number("IS", date(2026, 1, 5), 42), "IS-25690105-0042")
        self.assertEqual(contracts.format_number("PE", date(2027, 12, 31), 9999),
                         "PE-25701231-9999")

    def test_the_serial_is_four_digits_and_pads(self):
        self.assertEqual(contracts.format_number("SI", date(2026, 9, 22), 7), "SI-25690922-0007")
        self.assertTrue(contracts.format_number("SI", date(2026, 9, 22), 7).endswith("0007"))

    def test_the_bucket_is_the_buddhist_day(self):
        """桶就是佛历日；"同前缀同一天共用一个计数器"由序号表名字里的前缀保证。"""
        self.assertEqual(contracts.day_bucket(date(2026, 9, 22)), "25690922")
        self.assertNotEqual(contracts.day_bucket(date(2026, 9, 22)),
                            contracts.day_bucket(date(2026, 9, 23)))
        self.assertEqual(contracts.day_bucket("2026-09-22"), contracts.day_bucket(date(2026, 9, 22)))

    def test_a_number_the_system_did_not_issue_is_not_accepted(self):
        """手输的、别家前缀的、位数不对的，都不算本系统的号。"""
        for value in ("STE-2026-00052", "IR-20260922-0001", "IR-25690922-1",
                      "XX-25690922-0001", "IR-2569122-0001", "IR-25690922-000A", "", None,
                      "IR-25690922-0001-1"):
            with self.subTest(value=value):
                self.assertFalse(contracts.matches(value), value)

    def test_issued_numbers_round_trip(self):
        number = contracts.format_number("IS", date(2026, 9, 22), 12)
        self.assertTrue(contracts.matches(number))
        self.assertTrue(contracts.matches(number, "IS"))
        self.assertFalse(contracts.matches(number, "IR"))
        self.assertEqual(contracts.split(number), ("IS", "25690922", 12))


if __name__ == "__main__":
    unittest.main()
