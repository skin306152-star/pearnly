# Source regression assertions: ec2e5c957e6b; only imports and explicit date/IO injection adapted.
"""Express 映射器单测 · 确定性纯函数(无 DB/网络)。

钉死:PTT 样例(税前 375347.20 / 7% / 含税 401621.50 · 完整税票 + 买方税号 → 货道)→
三行借贷平衡、佛历日期、RR/HP 按付款分流;数不自洽/缺日期/缺科目 → ok=False(留人工);
VAT=0 走两行。费用车道(doc_lane=expense):进项税不可抵扣、VAT 折进成本,单行明细收尾。
"""
from __future__ import annotations
import sys
import unittest
import contextlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from pearnly_core.express_rules.push.common import PAYLOAD_VERSION, SRC_BANK, SRC_MANUAL, SRC_PROFILE
from pearnly_core.express_rules.push.mapper import build_express_payload
_CONFIG = {'account_set': 'DATAT', 'fallback_acc': '11-04-02-00', 'vat_input_acc': '11-05-04-01', 'ap_acc': '21-02-01-00'}
_BUYER_TAX = '1234567890123'

def _ptt_history(**over):
    fields = {'seller_name': 'บริษัท ปตท จำกัด (มหาชน)', 'seller_tax': '0107561000013', 'subtotal': '375347.20', 'vat': '26274.30', 'invoice_number': 'RR581231-002', 'posting_item_type_manual': 'goods'}
    fields.update(over.pop('fields', {}))
    h = {'id': 'hist-ptt', 'invoice_date': '2015-12-31', 'invoice_no': 'RR581231-002', 'total_amount': '401621.50', 'fields': fields}
    h.update(over)
    return h

class ExpressMapperTests(unittest.TestCase):

    def test_ptt_sample_balanced_entry(self):
        r = build_express_payload(_ptt_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        p = r.payload
        self.assertEqual(p['account_set'], 'DATAT')
        self.assertEqual(p['base_amount'], '375347.20')
        self.assertEqual(p['vat_amount'], '26274.30')
        self.assertEqual(p['total_amount'], '401621.50')
        self.assertEqual(p['docdate_be'], '581231')
        self.assertEqual(p['vat_period_be'], '581201')
        self.assertEqual(p['doctype'], 'RR')
        dr = sum((Decimal(ln['amount']) for ln in p['lines'] if ln['side'] == 'D'))
        cr = sum((Decimal(ln['amount']) for ln in p['lines'] if ln['side'] == 'C'))
        self.assertEqual(dr, cr)
        self.assertEqual(len(p['lines']), 3)
        accs = {ln['acc'] for ln in p['lines']}
        self.assertEqual(accs, {'11-04-02-00', '11-05-04-01', '21-02-01-00'})
        self.assertEqual(p['account_source'], 'config_default')
        self.assertTrue(p['account_review'])
        self.assertEqual(p['doc_lane'], 'goods')
        self.assertEqual(p['item_src'], SRC_MANUAL)
        self.assertNotIn('vat_capitalized', p)
        self.assertEqual(p['payload_version'], PAYLOAD_VERSION)

    def test_verified_official_name_preferred(self):
        r = build_express_payload(_ptt_history(seller_name_official='บริษัท ปตท จำกัด (มหาชน) [OFFICIAL]', seller_name_verified=True), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['lines'][0]['desc'], 'บริษัท ปตท จำกัด (มหาชน) [OFFICIAL]')

    def test_unverified_official_name_ignored(self):
        r = build_express_payload(_ptt_history(seller_name_official='บริษัท ปตท จำกัด (มหาชน) [OFFICIAL]', seller_name_verified=False), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['lines'][0]['desc'], 'บริษัท ปตท จำกัด (มหาชน)')

    def test_paid_routes_to_hp(self):
        r = build_express_payload(_ptt_history(fields={'payment_status': 'paid'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok)
        self.assertEqual(r.payload['doctype'], 'HP')

    def test_receipt_doc_type_without_explicit_field_routes_to_hp(self):
        r = build_express_payload(_ptt_history(fields={'document_type': 'receipt'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'HP')

    def test_tax_invoice_doc_type_without_explicit_field_stays_rr(self):
        r = build_express_payload(_ptt_history(fields={'document_type': 'tax_invoice'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'RR')

    def test_supplier_new_when_unmapped(self):
        r = build_express_payload(_ptt_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.payload['supplier']['supplier_new'])
        self.assertEqual(r.payload['supplier']['tax_id'], '0107561000013')
        self.assertEqual(r.payload['supplier']['prename'], 'บริษัท')

    def test_supplier_address_carried(self):
        addr = '555 ถนนวิภาวดีรังสิต แขวงจตุจักร กรุงเทพฯ 10900'
        r = build_express_payload(_ptt_history(fields={'seller_addr': addr}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['supplier']['address'], addr)

    def test_supplier_code_from_mapping(self):
        mappings = {'accounts': [], 'clients': [{'client_id': 42, 'erp_type': 'express', 'erp_code': 'ก005'}]}
        r = build_express_payload(_ptt_history(client_id=42), config=_CONFIG, mappings=mappings, today=date(2026, 9, 21))
        self.assertEqual(r.payload['supplier']['code'], 'ก005')
        self.assertFalse(r.payload['supplier']['supplier_new'])

    def test_account_from_mappings_bundle(self):
        mappings = {'accounts': [{'erp_type': 'express', 'pearnly_category': 'fuel', 'erp_code': '11-04-09-00'}, {'erp_type': 'express', 'pearnly_category': 'input_vat', 'erp_code': '11-05-04-01'}, {'erp_type': 'express', 'pearnly_category': 'accounts_payable', 'erp_code': '21-02-01-00'}], 'clients': []}
        r = build_express_payload(_ptt_history(), config={'account_set': 'DATAT'}, mappings=mappings, category='fuel', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        purchase = [ln for ln in r.payload['lines'] if ln['side'] == 'D'][0]
        self.assertEqual(purchase['acc'], '11-04-09-00')
        self.assertEqual(r.payload['account_source'], 'category_map')
        self.assertFalse(r.payload['account_review'])

    def test_inconsistent_amounts_manual(self):
        h = _ptt_history(fields={'subtotal': '100.00', 'vat': '7.00'}, total_amount='999.00')
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'amounts_not_consistent')

    def test_missing_date_manual(self):
        h = _ptt_history(invoice_date='')
        h['fields'].pop('date', None)
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'bad_or_missing_date')

    def test_missing_purchase_account_manual(self):
        r = build_express_payload(_ptt_history(), config={'account_set': 'DATAT'}, today=date(2026, 9, 21))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'no_purchase_account')

    def test_no_account_set_manual(self):
        r = build_express_payload(_ptt_history(), config={'fallback_acc': '11-04-02-00'}, today=date(2026, 9, 21))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'no_account_set')

    def test_vat_derived_from_subtotal(self):
        h = _ptt_history(fields={'subtotal': '375347.20', 'vat': ''})
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['vat_amount'], '26274.30')

    def test_doctype_src_carried_config_default(self):
        r = build_express_payload(_ptt_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype_src'], 'config_default')

    def test_doctype_src_reflects_explicit_field(self):
        r = build_express_payload(_ptt_history(fields={'payment_status': 'paid'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype_src'], 'explicit')

    def test_posting_payment_manual_wins_over_doc_type(self):
        h = _ptt_history(fields={'document_type': 'receipt', 'posting_payment_manual': 'credit'})
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'RR')
        self.assertEqual(r.payload['doctype_src'], SRC_MANUAL)

    def test_payment_remembered_for_the_supplier_is_recorded_as_remembered(self):
        # 验收 F19：这家记过现购（CC 按用户「จ่ายเงินสด」存下的）→ HP，留痕写 remembered，不与本票人工裁决混记
        r = build_express_payload(_ptt_history(), config={**_CONFIG, 'choices': {'payment': 'cash', 'payment_from': 'remembered'}},
                                  today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual((r.payload['doctype'], r.payload['doctype_src']), ('HP', 'remembered'))
        r = build_express_payload(_ptt_history(), config={**_CONFIG, 'choices': {'payment': 'credit', 'payment_from': 'task_default'}},
                                  today=date(2026, 9, 21))
        self.assertEqual((r.payload['doctype'], r.payload['doctype_src']), ('RR', 'task_default'))
        # 本票人工裁决仍高于记过的，留痕仍是 manual
        h = _ptt_history(fields={'posting_payment_manual': 'credit'})
        r = build_express_payload(h, config={**_CONFIG, 'choices': {'payment': 'cash', 'payment_from': 'remembered'}},
                                  today=date(2026, 9, 21))
        self.assertEqual((r.payload['doctype'], r.payload['doctype_src']), ('RR', SRC_MANUAL))

    def test_the_chosen_account_keeps_where_it_came_from(self):
        # 测试窗口第 2 轮（F19 后续）：科目的来源细分写进 account_source（人这次选的 / 记过的 / 任务默认 / Express 历史）
        for source in ('chosen', 'remembered', 'task_default', 'express_history'):
            r = build_express_payload(_ptt_history(), config={**_CONFIG, 'choices': {'account': '53-05-02-00',
                                      'account_from': source}}, today=date(2026, 9, 21))
            self.assertTrue(r.ok, r.reason)
            self.assertEqual((r.payload['items_account'], r.payload['account_source']), ('53-05-02-00', source))
        r = build_express_payload(_ptt_history(), config={**_CONFIG, 'choices': {'account': '53-05-02-00'}},
                                  today=date(2026, 9, 21))
        self.assertEqual(r.payload['account_source'], 'choice')  # 没给来源照旧笼统记 choice

    def test_supplier_profile_from_mappings_drives_doctype(self):
        mappings = {'accounts': [], 'clients': [], '_supplier_profile': {'default_payment': 'cash'}}
        r = build_express_payload(_ptt_history(), config=_CONFIG, mappings=mappings, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'HP')
        self.assertEqual(r.payload['doctype_src'], SRC_PROFILE)

    def test_bank_index_from_mappings_drives_doctype(self):
        mappings = {'accounts': [], 'clients': [], '_bank_index': [{'tx_date': '2015-12-31', 'direction': 'OUT', 'amount': '401621.50'}]}
        r = build_express_payload(_ptt_history(), config=_CONFIG, mappings=mappings, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'HP')
        self.assertEqual(r.payload['doctype_src'], SRC_BANK)

    def test_vat_zero_two_line_entry(self):
        h = _ptt_history(fields={'subtotal': '100.00', 'vat': '0'}, total_amount='100.00')
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(len(r.payload['lines']), 2)
        self.assertEqual(r.payload['vat_rate'], 0.0)
        dr = sum((Decimal(ln['amount']) for ln in r.payload['lines'] if ln['side'] == 'D'))
        cr = sum((Decimal(ln['amount']) for ln in r.payload['lines'] if ln['side'] == 'C'))
        self.assertEqual(dr, cr)

class ExpressExpenseLaneTests(unittest.TestCase):
    """费用车道(doc_lane=expense):进项税不可抵扣、VAT 折进成本(拍板口径)。"""

    def test_receipt_no_buyer_capitalizes_vat(self):
        h = _ptt_history(fields={'posting_item_type_manual': '', 'document_type': 'receipt', 'buyer_tax': '', 'subtotal': '100.00', 'vat': '7.00'}, total_amount='107.00')
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        p = r.payload
        self.assertEqual(p['doc_lane'], 'expense')
        self.assertEqual(p['item_src'], 'judge_direction=expense')
        self.assertEqual(p['vat_amount'], '0.00')
        self.assertEqual(p['vat_rate'], 0.0)
        self.assertEqual(p['base_amount'], '107.00')
        self.assertEqual(p['total_amount'], '107.00')
        self.assertEqual(p['vat_capitalized'], '7.00')
        self.assertNotIn('ภาษีซื้อ', [ln['desc'] for ln in p['lines']])
        self.assertEqual(len(p['lines']), 2)
        purchase_line = [ln for ln in p['lines'] if ln['side'] == 'D'][0]
        self.assertEqual(purchase_line['amount'], '107.00')
        self.assertEqual(p['items_status'], 'ok')
        self.assertEqual(len(p['items']), 1)
        self.assertEqual(p['items'][0]['amount'], '107.00')
        self.assertEqual(p['items'][0]['name'], 'ค่าใช้จ่าย')
        self.assertNotEqual(p['items'][0]['name'], p['supplier']['name'])

    def test_manual_expense_overrides_full_tax_invoice(self):
        h = _ptt_history(fields={'document_type': 'tax_invoice', 'buyer_tax': _BUYER_TAX, 'posting_item_type_manual': 'expense'})
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doc_lane'], 'expense')
        self.assertEqual(r.payload['item_src'], 'manual')
        self.assertEqual(r.payload['vat_amount'], '0.00')

    def test_manual_goods_overrides_judge_direction_expense(self):
        h = _ptt_history(fields={'document_type': 'receipt', 'buyer_tax': '', 'posting_item_type_manual': 'goods', 'subtotal': '100.00', 'vat': '7.00'}, total_amount='107.00')
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        p = r.payload
        self.assertEqual(p['doc_lane'], 'goods')
        self.assertEqual(p['item_src'], 'manual')
        self.assertEqual(p['vat_amount'], '7.00')
        self.assertEqual(len(p['lines']), 3)
        self.assertNotIn('vat_capitalized', p)

    def test_full_tax_invoice_with_buyer_stays_goods_lane(self):
        h = _ptt_history(fields={'document_type': 'tax_invoice', 'buyer_tax': _BUYER_TAX, 'posting_item_type_manual': ''})
        r = build_express_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        p = r.payload
        self.assertEqual(p['doc_lane'], 'goods')
        self.assertEqual(p['item_src'], 'judge_direction=purchase_invoice')
        self.assertEqual(len(p['lines']), 3)
        self.assertEqual(p['vat_amount'], '26274.30')
        self.assertIn('ภาษีซื้อ', [ln['desc'] for ln in p['lines']])
        self.assertNotIn('vat_capitalized', p)
_PERPETUAL_FP = {'stock_master_count': 672, 'stcrd_lines': 9300, 'stcrd_lines_moving_stock': 8102}

def _history_with_items(**over):
    """带一行可信明细的采购票(行合计==税前额 → 过对账闸,item_mode 才有得看)。"""
    fields = {'items': [{'name': 'เหล็กเส้น', 'qty': '10', 'subtotal': '375347.20'}]}
    fields.update(over.pop('fields', {}))
    return _ptt_history(fields=fields, **over)

class ExpressPurchaseStockToggleTests(unittest.TestCase):
    """本批「库存 vs 服务」开关(录入向导 step①)在进项侧的接线 · item_mode=stock_item。"""

    def test_stock_kind_sets_stock_item_on_goods_lines(self):
        r = build_express_payload(_history_with_items(), config=_CONFIG, posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['items_status'], 'ok')
        self.assertTrue(r.payload['items'])
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'stock_item')

    def test_service_kind_stays_non_stock(self):
        r = build_express_payload(_history_with_items(), config=_CONFIG, posting_kind='service', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_absent_kind_unchanged_non_stock(self):
        r = build_express_payload(_history_with_items(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_unknown_kind_never_becomes_stock(self):
        r = build_express_payload(_history_with_items(), config=_CONFIG, posting_kind='banana', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_expense_lane_ignores_stock_kind(self):
        h = _history_with_items(fields={'posting_item_type_manual': 'expense', 'document_type': 'receipt', 'buyer_tax': ''})
        r = build_express_payload(h, config=_CONFIG, posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doc_lane'], 'expense')
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_explicit_kind_bypasses_perpetual_escalation(self):
        cfg = {**_CONFIG, 'catalog_fingerprint': _PERPETUAL_FP}
        r = build_express_payload(_history_with_items(), config=cfg, posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'stock_item')
        r2 = build_express_payload(_history_with_items(), config=cfg, posting_kind='service', today=date(2026, 9, 21))
        self.assertTrue(r2.ok, r2.reason)
        for it in r2.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')
        r3 = build_express_payload(_history_with_items(), config=cfg, today=date(2026, 9, 21))
        self.assertFalse(r3.ok)
        self.assertTrue(r3.reason.startswith('posting_needs_review'), r3.reason)
if __name__ == '__main__':
    unittest.main(verbosity=2)
