"""按量计费单价（用户 2026-10-02 定）：每个自然月前 200 页 ฿1.50/页，超出部分 ฿1.00/页（原 ฿0.75），跨 200 页的一笔自动拆段。
文本文件每 50 字符 ฿0.01、银行对账单计费页数口径不变。离线：只算价，不连站点。"""
import unittest
from decimal import Decimal

from pearnly_control import billing


class Price(unittest.TestCase):
    def test_pages_after_the_first_200_of_a_month_cost_one_baht(self):
        self.assertEqual(billing.PDF_TIER2_PRICE, Decimal("1.00"))
        self.assertEqual(billing.pdf_cost_thb(0, 1), Decimal("1.50"))
        self.assertEqual(billing.pdf_cost_thb(200, 1), Decimal("1.00"))
        self.assertEqual(billing.pdf_cost_thb(0, 200), Decimal("300.00"))
        self.assertEqual(billing.pdf_cost_thb(250, 3), Decimal("3.00"))

    def test_a_charge_across_200_pages_is_split(self):
        self.assertEqual(billing.pdf_cost_thb(198, 5), Decimal("6.00"))    # 2 × 1.50 + 3 × 1.00
        self.assertEqual(billing.pdf_cost_thb(0, 201), Decimal("301.00"))  # 200 × 1.50 + 1 × 1.00
        self.assertEqual(billing.pdf_cost_thb(199, 1), Decimal("1.50"))
        self.assertEqual(billing.pdf_cost_thb(5, 0), Decimal("0.00"))

    def test_other_rates_are_unchanged(self):
        self.assertEqual(billing.PDF_TIER1_PRICE, Decimal("1.50"))
        self.assertEqual(billing.PDF_TIER1_LIMIT, 200)


if __name__ == "__main__":
    unittest.main()
