# Source assertions ec2e5c957e6b; transport UI/Companion cross-repository checks are outside this pure package.
"""Express 销项映射器单测 · 确定性纯函数(无 DB/网络)。

钉死:正常销项→借应收=贷收入+贷销项税三行平衡、doctype IV/HS、客户块、缺映射/数不
自洽→manual、0 VAT 两行。Companion 跨仓契约验证不在本纯模块的验收范围。
"""
from __future__ import annotations
import os
import sys
import unittest
import contextlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from pearnly_core.express_rules.push.common import PAYLOAD_VERSION, SRC_BANK, SRC_MANUAL
from pearnly_core.express_rules.push.sales_mapper import build_express_sales_payload
_CONFIG = {'account_set': 'DATAT', 'revenue_acc': '41-01-00-00', 'vat_output_acc': '11-05-04-02', 'ar_acc': '11-02-01-00'}

def _sales_history(**over):
    fields = {'buyer_name': 'บริษัท ลูกค้า จำกัด', 'buyer_tax': '0105551234567', 'subtotal': '23456.00', 'vat': '1641.92', 'invoice_number': 'SO-9001'}
    fields.update(over.pop('fields', {}))
    h = {'id': 'hist-sale', 'invoice_date': '2015-12-15', 'invoice_no': 'SO-9001', 'total_amount': '25097.92', 'fields': fields}
    h.update(over)
    return h

class ExpressSalesMapperTests(unittest.TestCase):

    def test_normal_sales_balanced(self):
        r = build_express_sales_payload(_sales_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        p = r.payload
        self.assertEqual(p['direction'], 'sales')
        self.assertEqual(p['doctype'], 'IV')
        self.assertEqual(p['account_set'], 'DATAT')
        self.assertEqual(p['base_amount'], '23456.00')
        self.assertEqual(p['vat_amount'], '1641.92')
        self.assertEqual(p['total_amount'], '25097.92')
        self.assertEqual(p['docdate_be'], '581215')
        dr = [ln for ln in p['lines'] if ln['side'] == 'D']
        cr = [ln for ln in p['lines'] if ln['side'] == 'C']
        self.assertEqual(len(dr), 1)
        self.assertEqual(dr[0]['acc'], '11-02-01-00')
        self.assertEqual(dr[0]['amount'], '25097.92')
        self.assertEqual(sum((Decimal(ln['amount']) for ln in dr)), sum((Decimal(ln['amount']) for ln in cr)))
        self.assertEqual(len(p['lines']), 3)
        self.assertEqual(p['account_source'], 'config_default')
        self.assertTrue(p['account_review'])
        self.assertEqual(p['payload_version'], PAYLOAD_VERSION)

    def test_customer_new_when_unmapped(self):
        r = build_express_sales_payload(_sales_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.payload['customer']['customer_new'])
        self.assertEqual(r.payload['customer']['tax_id'], '0105551234567')
        self.assertEqual(r.payload['customer']['prename'], 'บริษัท')

    def test_customer_address_carried_for_b2b(self):
        addr = 'เลขที่ 99 หมู่ 2 ถนนสุขุมวิท แขวงคลองเตย กรุงเทพฯ 10110'
        r = build_express_sales_payload(_sales_history(fields={'buyer_addr': addr}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['customer']['address'], addr)

    def test_customer_address_garbled_dropped(self):
        r = build_express_sales_payload(_sales_history(fields={'buyer_addr': '###'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['customer']['address'], '')

    def test_customer_code_from_mapping(self):
        mappings = {'accounts': [], 'clients': [{'client_id': 7, 'erp_type': 'express', 'erp_code': 'ย001'}]}
        r = build_express_sales_payload(_sales_history(client_id=7), config=_CONFIG, mappings=mappings, today=date(2026, 9, 21))
        self.assertEqual(r.payload['customer']['code'], 'ย001')
        self.assertFalse(r.payload['customer']['customer_new'])

    def test_cash_buyer_maps_to_cash_customer(self):
        r = build_express_sales_payload(_sales_history(fields={'buyer_name': 'เงินสด', 'buyer_tax': ''}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        cust = r.payload['customer']
        self.assertEqual(cust['code'], 'เงินสด')
        self.assertEqual(cust['name'], 'เงินสด')
        self.assertEqual(cust['address'], '')
        self.assertFalse(cust['customer_new'])
        dr = [ln for ln in r.payload['lines'] if ln['side'] == 'D']
        self.assertEqual(dr[0]['desc'], 'เงินสด')

    def test_paid_routes_to_hs(self):
        r = build_express_sales_payload(_sales_history(fields={'payment_status': 'paid'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok)
        self.assertEqual(r.payload['doctype'], 'HS')

    def test_receipt_doc_type_without_explicit_field_routes_to_hs(self):
        r = build_express_sales_payload(_sales_history(fields={'document_type': 'receipt'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'HS')

    def test_doctype_src_carried_config_default(self):
        r = build_express_sales_payload(_sales_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype_src'], 'config_default')

    def test_posting_payment_manual_wins_over_doc_type(self):
        h = _sales_history(fields={'document_type': 'receipt', 'posting_payment_manual': 'credit'})
        r = build_express_sales_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'IV')
        self.assertEqual(r.payload['doctype_src'], SRC_MANUAL)

    def test_bank_index_from_mappings_drives_doctype(self):
        mappings = {'accounts': [], 'clients': [], '_bank_index': [{'tx_date': '2015-12-15', 'direction': 'IN', 'amount': '25097.92'}]}
        r = build_express_sales_payload(_sales_history(), config=_CONFIG, mappings=mappings, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['doctype'], 'HS')
        self.assertEqual(r.payload['doctype_src'], SRC_BANK)

    def test_missing_accounts_manual(self):
        r = build_express_sales_payload(_sales_history(), config={'account_set': 'DATAT'}, today=date(2026, 9, 21))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'no_revenue_account')

    def test_inconsistent_amounts_manual(self):
        h = _sales_history(fields={'subtotal': '100.00', 'vat': '7.00'}, total_amount='999.00')
        r = build_express_sales_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'amounts_not_consistent')

    def test_zero_vat_two_line(self):
        h = _sales_history(fields={'subtotal': '1000.00', 'vat': '0'}, total_amount='1000.00')
        r = build_express_sales_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(len(r.payload['lines']), 2)
        self.assertEqual(r.payload['vat_rate'], 0.0)
        dr = sum((Decimal(ln['amount']) for ln in r.payload['lines'] if ln['side'] == 'D'))
        cr = sum((Decimal(ln['amount']) for ln in r.payload['lines'] if ln['side'] == 'C'))
        self.assertEqual(dr, cr)

    def test_items_in_payload_when_lines_reconcile(self):
        items = [{'name': 'สินค้า A', 'qty': '2', 'price': '10000', 'subtotal': '20000.00'}, {'name': 'สินค้า B', 'qty': '1', 'price': '3456', 'subtotal': '3456.00'}]
        r = build_express_sales_payload(_sales_history(fields={'items': items}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['items_status'], 'ok')
        self.assertEqual(len(r.payload['items']), 2)
        self.assertEqual(r.payload['items_account'], '41-01-00-00')

    def test_vat_inclusive_items_reconcile_to_total(self):
        items = [{'name': '58AUTOMAT', 'qty': '1', 'subtotal': '170.00'}, {'name': '58หัวเชื้อ', 'qty': '7', 'subtotal': '231.00'}, {'name': '58น้ำกลั่น', 'qty': '7', 'subtotal': '70.00'}, {'name': '58PERFORMA', 'qty': '1', 'subtotal': '625.00'}, {'name': '58PTT V120D', 'qty': '4', 'subtotal': '360.00'}, {'name': '58V120B', 'qty': '1', 'subtotal': '90.00'}, {'name': '58DYNAMIC', 'qty': '3', 'subtotal': '405.00'}, {'name': '58SEMISYN', 'qty': '2', 'subtotal': '390.00'}, {'name': '58COMMONRAIL', 'qty': '1', 'subtotal': '160.00'}, {'name': '58NGV', 'qty': '3', 'subtotal': '585.00'}, {'name': '58BRAKE', 'qty': '1', 'subtotal': '100.00'}, {'name': '58ลดความร้อน', 'qty': '1', 'subtotal': '175.00'}]
        h = _sales_history(fields={'buyer_name': 'เงินสด', 'subtotal': '3141.12', 'vat': '219.88', 'items': items}, total_amount='3361.00')
        r = build_express_sales_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['items_status'], 'ok')
        line_sum = sum((Decimal(it['amount']) for it in r.payload['items']))
        self.assertEqual(line_sum, Decimal('3141.12'))

    def test_thai_pua_tone_mark_made_cp874_safe(self):
        items = [{'name': 'ความร\uf70bอน', 'qty': '1', 'subtotal': '3141.12'}]
        h = _sales_history(fields={'buyer_name': 'เงินสด', 'subtotal': '3141.12', 'vat': '219.88', 'items': items}, total_amount='3361.00')
        r = build_express_sales_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        nm = r.payload['items'][0]['name']
        self.assertNotIn('\uf70b', nm)
        self.assertIn('้', nm)
        nm.encode('cp874')

    def test_whole_payload_cp874_safe_covers_all_fields(self):
        pua = chr(63248) + chr(63249) + chr(63250)
        items = [{'name': 'SINKHA' + pua, 'qty': '1', 'subtotal': '3141.12'}]
        h = _sales_history(fields={'buyer_name': 'LUKKHA' + pua, 'invoice_number': 'INV' + chr(63243), 'subtotal': '3141.12', 'vat': '219.88', 'items': items}, total_amount='3361.00')
        r = build_express_sales_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)

        def _all_strings(obj):
            if isinstance(obj, str):
                yield obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield from _all_strings(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from _all_strings(v)
        for s in _all_strings(r.payload):
            s.encode('cp874')
        self.assertIn(chr(3661), r.payload['items'][0]['name'])

    def test_items_truly_unreconcilable_still_blocks(self):
        items = [{'name': 'X', 'qty': '1', 'subtotal': '12.34'}]
        h = _sales_history(fields={'buyer_name': 'เงินสด', 'subtotal': '3141.12', 'vat': '219.88', 'items': items}, total_amount='3361.00')
        r = build_express_sales_payload(h, config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['items_status'], 'mismatch')

    def test_items_mismatch_falls_back_honestly(self):
        items = [{'name': 'X', 'qty': '1', 'price': '5', 'subtotal': '5.00'}]
        r = build_express_sales_payload(_sales_history(fields={'items': items}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['items_status'], 'mismatch')
        self.assertEqual(len(r.payload['lines']), 3)

    def test_no_items_empty_status(self):
        r = build_express_sales_payload(_sales_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['items_status'], 'empty')
        self.assertEqual(r.payload['items'], [])

def _history_with_items():
    """行合计 = 税前 100.00 → items_status=ok(有可落明细行供断言 item_mode)。"""
    return _sales_history(fields={'subtotal': '100.00', 'vat': '7.00', 'items': [{'name': 'สินค้า A', 'qty': '1', 'subtotal': '100.00'}]}, total_amount='107.00')

class ExpressSalesStockToggleTests(unittest.TestCase):
    """本批「库存 vs 销售·服务」开关(录入向导 step① · 仅 Express 销项)· item_mode=stock_sale 接线。"""

    def test_stock_kind_sets_stock_sale_on_goods_lines(self):
        r = build_express_sales_payload(_history_with_items(), config=_CONFIG, posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.payload['items_status'], 'ok')
        self.assertTrue(r.payload['items'])
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'stock_sale')

    def test_service_kind_unchanged_non_stock(self):
        r = build_express_sales_payload(_history_with_items(), config=_CONFIG, posting_kind='service', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_absent_kind_unchanged_non_stock(self):
        r = build_express_sales_payload(_history_with_items(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_unknown_kind_defaults_to_service(self):
        r = build_express_sales_payload(_history_with_items(), config=_CONFIG, posting_kind='banana', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_line_service_declarations_override_legacy_stock_kind(self):
        history = _sales_history(fields={'subtotal': '100.00', 'vat': '7.00', 'items': [{'name': '服务 A', 'qty': '1', 'subtotal': '50.00', 'posting_kind': 'service'}, {'name': '服务 B', 'qty': '1', 'subtotal': '50.00', 'posting_kind': 'service'}]}, total_amount='107.00')
        config = {**_CONFIG, 'stock_acccod': '12-01-01-00'}
        r = build_express_sales_payload(history, config=config, posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        self.assertNotIn('stock_acccod', r.payload)
        self.assertTrue(all((it['item_mode'] == 'non_stock_item' for it in r.payload['items'])))

    def test_explicit_kind_bypasses_perpetual_escalation(self):
        cfg = {**_CONFIG, 'catalog_fingerprint': {'stock_master_count': 672, 'stcrd_lines': 9300, 'stcrd_lines_moving_stock': 8102}}
        r = build_express_sales_payload(_history_with_items(), config=cfg, posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'stock_sale')
        r2 = build_express_sales_payload(_history_with_items(), config=cfg, posting_kind='service', today=date(2026, 9, 21))
        self.assertTrue(r2.ok, r2.reason)
        for it in r2.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')
        r3 = build_express_sales_payload(_history_with_items(), config=cfg, today=date(2026, 9, 21))
        self.assertFalse(r3.ok)
        self.assertTrue(r3.reason.startswith('posting_needs_review'), r3.reason)

class ExpressSalesStockMasterPreflightTests(unittest.TestCase):
    """选「库存」+ 账套零库存主档 + 端点没选存货科目组 → 当场拦(2026-07-23 真实事故)。

    小助手要么照抄账套里现有的 STKTYP=0 当模板,要么靠 ISACC 存货科目组从零建;两样都没有
    就 DBF_WRITE_FAILED。心跳的 catalog_fingerprint.stock_master_count 已经把主档为零这个
    事实报上来了,不该让票入队、被领走、烧完 3 次重试才转人工。
    """

    def _cfg(self, **fingerprint):
        return {**_CONFIG, 'catalog_fingerprint': fingerprint}

    def test_zero_stock_masters_blocks_stock_kind(self):
        r = build_express_sales_payload(_history_with_items(), config=self._cfg(stock_master_count=0, stcrd_lines=8, stcrd_lines_moving_stock=0), posting_kind='stock', today=date(2026, 9, 21))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'stock_acc_group_missing')
        self.assertTrue(r.items, '库存闸失败必须带出票面商品行,否则补救卡渲染不出来')
        self.assertTrue(all((i.get('name') for i in r.items)))

    def test_zero_stock_masters_does_not_block_service_kind(self):
        r = build_express_sales_payload(_history_with_items(), config=self._cfg(stock_master_count=0, stcrd_lines=8, stcrd_lines_moving_stock=0), posting_kind='service', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'non_stock_item')

    def test_unreported_count_passes_through(self):
        r = build_express_sales_payload(_history_with_items(), config=_CONFIG, posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
        for it in r.payload['items']:
            self.assertEqual(it['item_mode'], 'stock_sale')

    def test_dirty_count_value_passes_through(self):
        r = build_express_sales_payload(_history_with_items(), config=self._cfg(stock_master_count='ไม่ทราบ'), posting_kind='stock', today=date(2026, 9, 21))
        self.assertTrue(r.ok, r.reason)
if __name__ == '__main__':
    unittest.main(verbosity=2)

class CustomerBranchTests(unittest.TestCase):

    def test_buyer_branch_flows_to_customer_block(self):
        r = build_express_sales_payload(_sales_history(fields={'buyer_branch': '18'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['customer']['branch'], '00018')

    def test_branch_defaults_empty_not_hq(self):
        r = build_express_sales_payload(_sales_history(), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['customer']['branch'], '')

    def test_cash_buyer_branch_empty(self):
        r = build_express_sales_payload(_sales_history(fields={'buyer_name': 'เงินสด', 'buyer_branch': '18'}), config=_CONFIG, today=date(2026, 9, 21))
        self.assertEqual(r.payload['customer']['branch'], '')
