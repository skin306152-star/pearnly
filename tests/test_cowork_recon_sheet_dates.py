"""对账复核表里打的日期（`recon_center.sheets._bank_date`）：年份与识别同一规则。"""
import unittest

from datetime import date, datetime

from pearnly_cowork.recon_center import sheets
from pearnly_cowork.reconciliation.export.income_workbook import show_date
from pearnly_cowork.reconciliation.normalization import parse_date


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


    def test_real_spreadsheet_date_cells_are_dates(self):
        # 2026-09-28 录制：税表日期格子显示成「2026-01-05 00:00:00」，.xls 是序列号
        self.assertEqual(parse_date(datetime(2026, 1, 5)), date(2026, 1, 5))
        self.assertEqual(parse_date("2026-01-05 00:00:00"), date(2026, 1, 5))
        self.assertEqual(parse_date(46027.0), date(2026, 1, 5))
        self.assertEqual(parse_date("46027"), date(2026, 1, 5))
        self.assertIsNone(parse_date(12345))  # 不在日期序列号那一段的数不当日期
        self.assertIsNone(parse_date(True))

    def test_the_income_sheet_shows_one_date_style(self):
        # 同一张表里「30/09/2569」与「2026-09-18」混排 → 一律 日/月/年（公历）
        self.assertEqual((show_date("30/09/2569"), show_date("2026-09-18"), show_date("ไม่ระบุ")),
                         ("30/09/2026", "18/09/2026", "ไม่ระบุ"))


if __name__ == "__main__":
    unittest.main()
