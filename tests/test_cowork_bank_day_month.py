"""银行流水只印「日/月」的日期与「ยอดคงเหลือยกมา」承前行（2026-10-02 零费用回放：21–40 行 / 页卡在日期的多数是这两类）。
不连站点：只测 `header_bank` 的纯函数。"""
import datetime
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
for name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_cowork", "pearnly_core")]:
    del sys.modules[name]

from pearnly_cowork.ocr.enterprise import header_bank as hb  # noqa: E402

YEAR = 2026


class DayMonth(unittest.TestCase):
    def period(self, *texts):
        return hb.statement_period({1: [SimpleNamespace(text=text) for text in texts]}, YEAR)

    def test_year_comes_from_the_statement_period(self):
        period = self.period("Statement period 01/04/2026 - 30/04/2026", "02/04 TR FR")
        self.assertEqual(period, ("2026-04-01", "2026-04-30"))
        self.assertEqual(hb.parse_date("02/04 TR FR / 4,257.30", YEAR, period), "2026-04-02")

    def test_one_misprinted_year_does_not_widen_the_period(self):
        period = self.period("01/04/2026 - 30/04/2016", "05/04/2026 TR", "20/04/2026 TR")
        self.assertEqual(period, ("2026-04-01", "2026-04-20"))
        self.assertEqual(hb.parse_date("02/04 TR FR", YEAR, period), "2026-04-02")

    def test_a_statement_across_new_year_puts_each_month_in_its_year(self):
        period = self.period("15/12/2568", "14/01/2569")  # 佛历：2025-12-15 至 2026-01-14
        self.assertEqual(hb.parse_date("28/12 SMART", YEAR, period), "2025-12-28")
        self.assertEqual(hb.parse_date("03/01 SMART", YEAR, period), "2026-01-03")

    def test_without_a_period_or_far_outside_it_the_date_stays_unread(self):
        self.assertEqual(hb.parse_date("02/03 CHEQUE", YEAR, None), "")
        period = self.period("01/04/2026", "30/04/2026")
        self.assertEqual(hb.parse_date("02/09 CHEQUE", YEAR, period), "")

    def test_only_the_leading_day_month_counts_not_one_in_the_description(self):
        period = self.period("01/04/2026", "30/04/2026")
        self.assertEqual(hb.parse_date("00 SMT Torty 1/5 ***", YEAR, period), "")
        self.assertEqual(hb.parse_date("31/04 X", YEAR, period), "")  # 不存在的日期

    def test_full_dates_are_read_exactly_as_before(self):
        self.assertEqual(hb.parse_date("25/11/25 98 TR", YEAR), "2025-11-25")
        self.assertEqual(hb.parse_date("01/11/2569", YEAR), "2026-11-01")

    def test_the_spaced_thai_brought_forward_line_is_a_carry_line(self):
        self.assertTrue(hb._carry("** ยอด คงเหลือ ยก มา ** 22,377,586.86"))
        self.assertTrue(hb._carry("ยอดเงินคงเหลือยกมา"))
        self.assertFalse(hb._carry("TR.FR.OTH.BK.AC 7,845.94"))


if __name__ == "__main__":
    unittest.main()
