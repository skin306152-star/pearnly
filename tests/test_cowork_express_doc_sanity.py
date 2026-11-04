# Source assertions ec2e5c957e6b; transport UI/Companion cross-repository checks are outside this pure package.
"""Express 单据防呆体检单测(纯函数 · 无网络 · 无 DB)。

钉死防呆闸命中规范 reason、正常票放行、空信号不误伤。对应全语料真机暴露的
陷阱票(26 外币 / 30 贷项 / 24 未来日期)。买卖方同税号、押金、倒签、对方税号不是 13 位
Express 与 MR.ERP 都不拦,11-04 起我们也不拦(用户:「只要 exp 不拦,mr.erp 不拦,我们就不要拦」)。
"""
from __future__ import annotations
import sys
import unittest
import contextlib
from datetime import date
from datetime import date
from pathlib import Path
from pearnly_core.express_rules.push.doc_sanity import check_document
TODAY = date(2026, 6, 24)
VENDOR_TAX = '0107561000013'
CUSTOMER_TAX = '0105551234567'

def _fields(**over):
    f = {'seller_tax': VENDOR_TAX, 'buyer_tax': CUSTOMER_TAX, 'document_type': 'tax_invoice', 'currency': '', 'notes': '', 'category': '', 'items': [{'name': 'เหล็กเส้น SD40'}]}
    f.update(over)
    return f

def _hist(invoice_date='2025-12-01'):
    return {'invoice_date': invoice_date}

class CleanPassTests(unittest.TestCase):

    def test_normal_purchase_passes(self):
        self.assertIsNone(check_document(_fields(), _hist(), today=TODAY))

    def test_normal_sales_passes(self):
        self.assertIsNone(check_document(_fields(), _hist(), today=TODAY))

    def test_thb_currency_passes(self):
        self.assertIsNone(check_document(_fields(currency='THB'), _hist(), today=TODAY))

    def test_empty_tax_not_flagged(self):
        self.assertIsNone(check_document(_fields(seller_tax=''), _hist(), today=TODAY))

class CurrencyTests(unittest.TestCase):

    def test_usd_blocked(self):
        r = check_document(_fields(currency='USD'), _hist(), today=TODAY)
        self.assertEqual(r, 'currency_not_thb:usd')

    def test_currency_wins_over_other_flags(self):
        r = check_document(_fields(currency='EUR', seller_tax='123'), _hist(), today=TODAY)
        self.assertEqual(r, 'currency_not_thb:eur')

class CurrencyMarkerTests(unittest.TestCase):
    """currency 字段空时的外币记号兜底(对抗票 15:currency=None 但票号 INV-USD-0007)。"""

    def test_usd_in_invoice_number_blocked(self):
        r = check_document(_fields(invoice_number='INV-USD-0007'), _hist(), today=TODAY)
        self.assertEqual(r, 'currency_not_thb:usd')

    def test_dollar_symbol_next_to_amount_blocked(self):
        r = check_document(_fields(notes='Total $400.00 net 30'), _hist(), today=TODAY)
        self.assertEqual(r, 'currency_not_thb:symbol')

    def test_thai_dollar_word_blocked(self):
        r = check_document(_fields(notes='ชำระเป็นดอลลาร์สหรัฐ'), _hist(), today=TODAY)
        self.assertEqual(r, 'currency_not_thb:th-marker')

    def test_explicit_thb_skips_marker_probe(self):
        self.assertIsNone(check_document(_fields(currency='THB', invoice_number='INV-USD-0007'), _hist(), today=TODAY))

    def test_plain_invoice_number_passes(self):
        self.assertIsNone(check_document(_fields(invoice_number='IV69/00179'), _hist(), today=TODAY))

class SameTaxTests(unittest.TestCase):
    """买卖方同税号(对抗票 12)不再由防呆拦;没写明方向时照旧停在「方向判不出」(Express 预检 / MR.ERP 方向闸)。"""

    def test_same_tax_passes(self):
        self.assertIsNone(check_document(_fields(seller_tax=VENDOR_TAX, buyer_tax=VENDOR_TAX), _hist(), today=TODAY))

    def test_distinct_tax_passes(self):
        self.assertIsNone(check_document(_fields(), _hist(), today=TODAY))

    def test_one_side_missing_not_flagged(self):
        self.assertIsNone(check_document(_fields(buyer_tax=''), _hist(), today=TODAY))

class CreditNoteTests(unittest.TestCase):

    def test_credit_note_doctype_blocked(self):
        r = check_document(_fields(document_type='credit_note'), _hist(), today=TODAY)
        self.assertEqual(r, 'credit_note')

    def test_credit_note_wording_blocked(self):
        r = check_document(_fields(notes='ใบลดหนี้ อ้างอิงใบกำกับเดิม'), _hist(), today=TODAY)
        self.assertEqual(r, 'credit_note')

class DepositTests(unittest.TestCase):
    """押金 / 定金收据照常推(Express、MR.ERP 都照收)。"""

    def test_deposit_item_passes(self):
        self.assertIsNone(check_document(_fields(items=[{'name': 'เงินมัดจำค่าสินค้า (10%)'}]), _hist(), today=TODAY))

    def test_security_deposit_note_passes(self):
        self.assertIsNone(check_document(_fields(notes='เงินประกัน — ยังไม่ส่งมอบ'), _hist(), today=TODAY))

class DateTests(unittest.TestCase):

    def test_ancient_date_blocked(self):
        r = check_document(_fields(), _hist('1970-01-01'), today=TODAY)
        self.assertEqual(r, 'date_implausible')
        r2 = check_document(_fields(), _hist('1999-12-31'), today=TODAY)
        self.assertEqual(r2, 'date_implausible')

    def test_year_2000_boundary_passes(self):
        self.assertIsNone(check_document(_fields(), _hist('2000-01-01'), today=TODAY))

    def test_future_date_blocked(self):
        r = check_document(_fields(), _hist('2026-12-31'), today=TODAY)
        self.assertEqual(r, 'date_future')

    def test_past_date_passes(self):
        self.assertIsNone(check_document(_fields(), _hist('2015-12-31'), today=TODAY))

    def test_reissue_note_passes(self):
        # 补开 / 倒签(ใบแทน)是合法的替代税票,Express、MR.ERP 都照收
        self.assertIsNone(check_document(_fields(notes='ออกใบแทน ณ วันที่ 2568'), _hist('2022-01-05'), today=TODAY))

class TaxIdTests(unittest.TestCase):
    """对方税号不是 13 位照常推(Express 的 TAXID 是 C(15) 自由文本;MR.ERP 建档按原数字写)。"""

    def test_short_party_tax_ids_pass(self):
        for fields in (_fields(seller_tax='01055560123'), _fields(buyer_tax='12345'), _fields(seller_tax='999')):
            self.assertIsNone(check_document(fields, _hist(), today=TODAY))
if __name__ == '__main__':
    unittest.main(verbosity=2)
