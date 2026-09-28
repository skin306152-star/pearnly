"""对账复核表里打的日期（`recon_center.sheets._bank_date`）：年份与识别同一规则。"""
import unittest

from pearnly_cowork.recon_center import sheets


class SheetDates(unittest.TestCase):
    def test_two_digit_years_are_buddhist_like_everywhere_else(self):
        # 2026-09-28 录制：01/06/69 变成 2069 年
        self.assertEqual(sheets._bank_date("01/06/69"), "2026-06-01")
        self.assertEqual(sheets._bank_date("1.6.69"), "2026-06-01")
        self.assertEqual(sheets._bank_date("01/06/2569"), "2026-06-01")
        self.assertEqual(sheets._bank_date("01-06-2026"), "2026-06-01")

    def test_what_is_not_a_date_is_left_for_the_contract_to_refuse(self):
        self.assertEqual(sheets._bank_date("31/02/69"), "31/02/69")
        self.assertEqual(sheets._bank_date("2026-06-01"), "2026-06-01")


if __name__ == "__main__":
    unittest.main()
