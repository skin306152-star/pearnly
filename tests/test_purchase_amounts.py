"""Offline parity vectors from the fixed legacy purchase amount contract."""
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/amounts.py'
spec = importlib.util.spec_from_file_location('purchase_amounts', PATH)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class PurchaseAmounts(unittest.TestCase):
    def test_mixed_rates_precision_and_half_even(self):
        result = a.calculate([
            {'qty': '0.125', 'unit_price': '80', 'discount': '0.005', 'vat_rate': 7, 'wht_rate': 3},
            {'qty': 3, 'unit_price': '2.675', 'discount': 0, 'vat_rate': 0, 'wht_rate': 0}],
            discount_total='1.02', rounding='-0.01')
        self.assertEqual(result['subtotal'], Decimal('18.02'))
        self.assertEqual(result['vat_amount'], Decimal('0.70'))
        self.assertEqual(result['wht_amount'], Decimal('0.30'))
        self.assertEqual(result['grand_total'], Decimal('17.69'))
        self.assertEqual(result['net_payable'], Decimal('17.39'))

    def test_inclusive_tax_and_line_discount(self):
        result = a.calculate([{'qty': 2, 'unit_price': 107, 'discount': '10.70', 'vat_rate': 7, 'wht_rate': 3}], price_mode='inclusive')
        self.assertEqual(result['subtotal'], Decimal('190.00'))
        self.assertEqual(result['vat_amount'], Decimal('13.30'))
        self.assertEqual(result['net_payable'], Decimal('197.60'))

    def test_no_tax_invoice_overrides_line_vat(self):
        result = a.calculate([{'qty': 1, 'unit_price': 100, 'vat_rate': 7, 'wht_rate': 3}], has_vat=False)
        self.assertEqual(result['vat_amount'], 0)
        self.assertEqual(result['net_payable'], 97)

    def test_invoice_override_only_one_cent_rounding(self):
        lines = [{'qty': 1, 'unit_price': 100, 'vat_rate': 7}]
        override = {'subtotal': 200, 'discount_total': 10, 'vat_amount': 14, 'grand_total': '204.01'}
        result = a.calculate(lines, amount_override=override)
        self.assertEqual(result['grand_total'], Decimal('204.01'))
        with self.assertRaisesRegex(ValueError, 'must equal the total'):
            a.calculate(lines, amount_override={**override, 'grand_total': '204.02'})

    def test_version_3_takes_the_bill_discount_before_vat(self):
        # MR.ERP `allform.js:702-720`：beforevat = total − discount，vat = beforevat × rate。
        result = a.calculate([{'qty': 1, 'unit_price': 1000, 'vat_rate': 7}], discount_total=100,
                             discount_before_vat=True)
        self.assertEqual((result['vat_amount'], result['grand_total']), (Decimal('63.00'), Decimal('963.00')))
        # 09-26 Giffarine 含税票：12,810 − 折扣 3,162 = 9,648，VAT 631.18（版本 2 算成 838.04）。
        lines = [{'qty': 40, 'unit_price': 300, 'vat_rate': 7}, {'qty': 6, 'unit_price': 135, 'vat_rate': 7}]
        v3 = a.calculate(lines, price_mode='inclusive', discount_total=3162, discount_before_vat=True)
        self.assertEqual((v3['discount_total'], v3['vat_amount'], v3['grand_total']),
                         (Decimal('3162.00'), Decimal('631.18'), Decimal('9648.00')))
        self.assertEqual(sum(row['vat_amount'] for row in v3['lines']), v3['vat_amount'])
        # 存的是输入的折扣：拿结果再算一遍，金额不变（每次保存都会重算）。
        again = a.calculate([dict(row) for row in lines], price_mode='inclusive',
                            discount_total=v3['discount_total'], discount_before_vat=True)
        self.assertEqual(again['grand_total'], v3['grand_total'])
        # 版本 2 的旧单据照旧：先算 VAT、后扣折扣。
        self.assertEqual(a.calculate(lines, price_mode='inclusive', discount_total=3162)['vat_amount'],
                         Decimal('838.04'))

    def test_version_3_rounds_vat_once_per_document(self):
        # 三行各 10.05：逐行 0.70×3 = 2.10；票面按整单 30.15 × 7% = 2.11。
        lines = [{'qty': 1, 'unit_price': '10.05', 'vat_rate': 7} for _ in range(3)]
        v3 = a.calculate(lines, discount_before_vat=True)
        self.assertEqual((v3['vat_amount'], v3['grand_total']), (Decimal('2.11'), Decimal('32.26')))
        self.assertEqual(sum(row['vat_amount'] for row in v3['lines']), Decimal('2.11'))
        self.assertEqual(a.calculate(lines)['vat_amount'], Decimal('2.10'))  # 版本 2 照旧逐行

    def test_version_4_takes_vat_out_of_the_inclusive_total(self):
        # 09-26 กิจสยามยานยนต์ 含税票 6,400 / 750 / 350：票面 7,009.35 + 490.65 = 7,500.00；
        # 版本 3 逐行除 1.07 取整，算成 7,009.34 + 490.65 = 7,499.99。
        lines = [{'qty': 1, 'unit_price': price, 'vat_rate': 7} for price in (6400, 750, 350)]
        v4 = a.calculate(lines, price_mode='inclusive', discount_before_vat=True, vat_from_total=True)
        self.assertEqual((v4['subtotal'], v4['vat_amount'], v4['grand_total']),
                         (Decimal('7009.35'), Decimal('490.65'), Decimal('7500.00')))
        self.assertEqual(sum(row['line_total'] for row in v4['lines']), v4['subtotal'])
        self.assertEqual(sum(row['vat_amount'] for row in v4['lines']), v4['vat_amount'])
        v3 = a.calculate(lines, price_mode='inclusive', discount_before_vat=True)
        self.assertEqual(v3['grand_total'], Decimal('7499.99'))  # 版本 3 的旧单据保存时金额不变
        # 整单折扣（含税口径）+ 行折扣 + 两种税率：合计正好是含税金额之和 − 折扣。
        mixed = [{'qty': 2, 'unit_price': 1070, 'discount': 107, 'vat_rate': 7, 'wht_rate': 3},
                 {'qty': 1, 'unit_price': 535, 'vat_rate': 0}]
        v4 = a.calculate(mixed, price_mode='inclusive', discount_total=100, discount_before_vat=True,
                         vat_from_total=True)
        self.assertEqual((v4['vat_amount'], v4['grand_total'], v4['rounding']),
                         (Decimal('127.82'), Decimal('2468.00'), Decimal('0.00')))
        self.assertEqual(v4['wht_amount'], Decimal('57.00'))  # 1,900.00 × 3%（折前不含税）
        # Giffarine 含税票与版本 3 结果相同。
        giffarine = [{'qty': 40, 'unit_price': 300, 'vat_rate': 7}, {'qty': 6, 'unit_price': 135, 'vat_rate': 7}]
        v4 = a.calculate(giffarine, price_mode='inclusive', discount_total=3162, discount_before_vat=True,
                         vat_from_total=True)
        self.assertEqual((v4['vat_amount'], v4['grand_total']), (Decimal('631.18'), Decimal('9648.00')))
        # 不含税票与版本 3 一样。
        plain = [{'qty': 1, 'unit_price': '10.05', 'vat_rate': 7} for _ in range(3)]
        self.assertEqual(a.calculate(plain, discount_before_vat=True, vat_from_total=True),
                         a.calculate(plain, discount_before_vat=True))
        with self.assertRaises(ValueError):
            a.calculate(lines, price_mode='inclusive', discount_total=7501, discount_before_vat=True,
                        vat_from_total=True)

    def test_version_3_discount_cannot_exceed_the_amount(self):
        with self.assertRaises(ValueError):
            a.calculate([{'qty': 1, 'unit_price': 100, 'vat_rate': 7}], discount_total=101,
                        discount_before_vat=True)

    def test_invalid_numbers_and_discount(self):
        for row in [{'qty': 0, 'unit_price': 1}, {'qty': 1, 'unit_price': 'NaN'},
                    {'qty': 1, 'unit_price': '-1'}, {'qty': 1, 'unit_price': 1, 'discount': 2},
                    {'qty': 1, 'unit_price': 1, 'wht_rate': 101}]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                a.calculate([row])


if __name__ == '__main__':
    unittest.main()
