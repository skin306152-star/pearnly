"""Explicit snapshots and final preparation gates, with no store or transport."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import ast
from pathlib import Path
import unittest

from pearnly_core.express_rules.contracts.push import PushContractError
from pearnly_core.express_rules.push.preflight import CHECK_KEYS, ExpressContext, preflight_express
from pearnly_core.express_rules.push.prepare import prepare_express
from pearnly_core.express_rules.push.prior_doc import prior_history_id
from pearnly_core.express_rules.push.common import amounts
from pearnly_core.express_rules.push.account_rules import account_set_allowed, stock_lane_enabled
from pearnly_core.express_rules.push.stock_acc_group import resolve_stock_acc_group


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.context = ExpressContext(True, date(2026, 9, 21), '0994000333444', 'tenant', {})
        self.endpoint = {'id': 'ep', 'config': {'account_set': 'DATAT', 'fallback_acc': 'A',
            'vat_input_acc': 'V', 'ap_acc': 'P', 'revenue_acc': 'S', 'vat_output_acc': 'O', 'ar_acc': 'R'}}
        self.fields = {'seller_name': 'Vendor Company', 'seller_tax': '0107561000013',
            'buyer_tax': '0994000333444', 'buyer_name': 'Buyer Company', 'subtotal': '100',
            'vat': '7', 'document_type': 'tax_invoice', 'invoice_number': 'INV1'}
        self.history = {'id': 'h', 'workspace_client_id': 'client-native-name', 'tenant_id': 'tenant',
            'invoice_no': 'INV1', 'invoice_date': '2026-09-01', 'total_amount': '107',
            'confidence': 'high', 'pages': [{'fields': self.fields}]}

    def pf(self, **kw):
        return preflight_express(self.endpoint, self.history, context=self.context, **kw)

    def test_ready_full_order_and_no_queue_claim(self):
        result = prepare_express(self.endpoint, self.history, context=self.context)
        self.assertEqual(result.disposition, 'ready')
        self.assertEqual([c.key for c in result.preflight.checks], list(CHECK_KEYS))
        self.assertTrue(all(c.status == 'ok' for c in result.preflight.checks))
        self.assertNotIn('success', result.payload)
        self.assertNotIn('queued', result.payload)

    def test_missing_account_stops_later_gates(self):
        self.endpoint['config']['account_set'] = ''
        pf = self.pf()
        self.assertEqual(pf.reason, 'account_set_not_allowed:none')
        self.assertEqual([c.status for c in pf.checks], ['ok', 'blocked'] + ['pending'] * 7)

    def test_legacy_wire_preserves_selected_directory_case_for_both_directions(self):
        for directory in (r'\\Accserver\d$\ACCOUNT\70EXP\TEST', r'D:\EXP\Test'):
            for direction in ('purchase', 'sales'):
                with self.subTest(directory=directory, direction=direction):
                    self.endpoint['config'].update(account_set=directory.lower(), account_dir=directory)
                    self.history['direction'] = direction
                    before = deepcopy(self.endpoint)
                    result = self.pf()
                    self.assertTrue(result.ready, result.reason)
                    self.assertEqual(result.payload['account_set'], directory)
                    self.assertEqual(result.payload['account_dir'], directory)
                    self.assertEqual(self.endpoint, before)

    def test_wire_directory_cannot_replace_another_account_identity(self):
        self.endpoint['config'].update(account_set=r'D:\EXP\TEST', account_dir=r'D:\EXP\OTHER')
        result = self.pf()
        self.assertEqual(result.payload['account_set'], r'D:\EXP\TEST')
        self.assertFalse(account_set_allowed(r'D:\EXP\OTHER', self.endpoint))

    def test_disabled_still_guards_internal_source(self):
        for source in ['erp_web', 'line_erp']:
            self.history['source'] = source
            with self.assertRaises(PushContractError) as cm:
                prepare_express(self.endpoint, self.history, context=replace(self.context, enabled=False))
            self.assertEqual(cm.exception.code, 'erp.internal_only')

    def test_disabled_does_not_prepare_payload(self):
        result = prepare_express(self.endpoint, self.history, context=replace(self.context, enabled=False))
        self.assertEqual((result.disposition, result.reason), ('disabled', 'ERR_EXPRESS_DISABLED'))
        self.assertEqual(result.payload, {'adapter': 'express'})

    def test_explicit_direction_precedes_company_tax(self):
        self.history['direction'] = 'sales'
        self.context = replace(self.context, own_tax_id='')
        result = self.pf()
        self.assertTrue(result.ready)
        self.assertEqual(result.payload['direction'], 'sales')

    def test_missing_and_ambiguous_company_reasons(self):
        for tax, reason in [('', 'subject_unbound'), ('1111111111111', 'ambiguous')]:
            self.context = replace(self.context, own_tax_id=tax)
            pf = self.pf()
            self.assertEqual(pf.reason, 'direction_unknown')
            self.assertEqual(pf.checks[3].reason, reason)

    def test_the_account_set_tax_decides_when_the_workspace_tax_cannot(self):
        # 2026-10-15：事务所工作空间税号不是客户账套的，别家开的无买方简易税票按推往的账套税号判成进项
        for key in ('buyer_tax', 'buyer_name'):
            self.fields.pop(key)
        self.context = replace(self.context, own_tax_id='0107536000188', account_tax_id='0105551234567')
        result = self.pf()
        self.assertTrue(result.ready, result.reason)
        self.assertEqual(result.payload['direction'], 'purchase')
        self.fields['seller_tax'] = '0105551234567'  # 卖方就是这份账套：我方开出的销售简易税票
        self.assertEqual(self.pf().payload['direction'], 'sales')
        self.fields['seller_tax'] = ''  # 两边都读不出：照旧判不出，有锚点算 ambiguous
        pf = self.pf()
        self.assertEqual((pf.reason, pf.checks[3].reason), ('direction_unknown', 'ambiguous'))
        self.context = replace(self.context, own_tax_id='', account_tax_id='')
        self.assertEqual(self.pf().checks[3].reason, 'subject_unbound')

    def test_missing_company_anchor_does_not_use_unrelated_tax_snapshot(self):
        self.history.pop("workspace_client_id")
        result = self.pf()
        self.assertEqual(result.reason, "direction_unknown")
        self.assertEqual(result.checks[3].reason, "subject_unbound")

    def test_document_block_precedes_mapping(self):
        self.fields['currency'] = 'USD'
        self.endpoint['config']['fallback_acc'] = ''
        self.assertEqual(self.pf().reason, 'currency_not_thb:usd')

    def test_chart_missing_and_bad_account_differ(self):
        self.assertTrue(self.pf().ready)
        self.endpoint['config']['reported_accounts'] = [{'code': 'V'}, {'code': 'P'}]
        self.assertEqual(self.pf().reason, 'account_not_in_chart:A')

    def test_recognition_confidence_never_blocks_a_reviewed_push(self):
        # 用户 2026-09-27：识别质量不拦推送（复核后按票推）；缺票号这类数据缺失照旧拦。
        for band in ('low', 'medium', 'needs_review', ''):
            self.history['confidence'] = band
            self.assertTrue(self.pf().ready, band)
        self.history['confidence'] = 'low'
        self.fields['invoice_number'] = ''
        self.history['invoice_no'] = ''
        result = self.pf()
        self.assertTrue(result.blocked)
        self.assertIn('invoice_no_missing', result.reason)

    def test_item_review_is_after_preflight(self):
        for items, reason in [([{'name': 'A', 'subtotal': '10'}], 'items_mismatch'),
                              ([{'subtotal': '100'}], 'items_incomplete')]:
            self.fields['items'] = items
            pf = self.pf()
            self.assertTrue(pf.ready)
            prepared = prepare_express(self.endpoint, self.history, context=self.context)
            self.assertEqual((prepared.disposition, prepared.reason), ('manual', reason))

    def test_valid_inclusive_items_rescale_and_balance(self):
        self.fields['items'] = [{'name': 'A', 'subtotal': '53.50'}, {'name': 'B', 'subtotal': '53.50'}]
        result = prepare_express(self.endpoint, self.history, context=self.context)
        self.assertEqual(result.disposition, 'ready')
        self.assertEqual(sum(Decimal(i['amount']) for i in result.payload['items']), Decimal('100.00'))

    def test_prior_docnum_and_opening_stock_are_detached(self):
        self.history['merged_fields'] = {'opening_stock': [{'name': 'A', 'qty': '1'}]}
        originals = deepcopy((self.endpoint, self.history, self.context.mappings))
        result = preflight_express(self.endpoint, self.history, context=replace(self.context, prior_docnum='RR0001'))
        self.assertEqual(result.payload['prior_docnum'], 'RR0001')
        result.payload['opening_stock'][0]['qty'] = '999'
        self.assertEqual((self.endpoint, self.history, self.context.mappings), originals)

    def test_prior_lookup_key_never_grants_scope(self):
        self.assertEqual(prior_history_id({'id': 'new'}, {'history_id': ' old '}), 'old')
        self.assertEqual(prior_history_id({'id': 'new'}, {'history_id': ''}), 'new')

    def test_payload_version_is_decided_when_a_computer_leases_not_when_preparing(self):
        # ADR-021：准备时不知道由哪台电脑写，载荷版本在领取时按领取的那台判（`companion.route_contracts`）
        for reported in ('dirty', 0, 1):
            self.endpoint['config']['max_payload_version'] = reported
            self.assertTrue(self.pf().ready, reported)

    def test_explicit_stock_does_not_enable_auto_stock_lane(self):
        self.assertFalse(stock_lane_enabled({'stock_enabled': True}))
        self.fields['items'] = [{'name': 'A', 'subtotal': '100'}]
        self.history['posting_kind'] = 'stock'
        result = prepare_express(self.endpoint, self.history, context=self.context)
        self.assertEqual(result.disposition, 'ready')
        self.assertEqual(result.payload['items'][0]['item_mode'], 'stock_item')

    def test_profile_and_bank_explicit_snapshot_priority(self):
        self.fields['document_type'] = 'unknown'
        self.fields['posting_item_type_manual'] = 'goods'
        self.context = replace(self.context, supplier_profile={'default_payment': 'cash'})
        self.assertEqual(self.pf().payload['doctype_src'], 'profile')
        self.context = replace(self.context, supplier_profile=None,
            bank_index=[{'direction': 'OUT', 'amount': '107.00', 'tx_date': '2026-09-08'}])
        self.assertEqual(self.pf().payload['doctype_src'], 'bank')
        self.context = replace(self.context, bank_index=[{'direction': 'OUT', 'amount': '107.00', 'tx_date': '2026-09-09'}])
        self.assertEqual(self.pf().payload['doctype'], 'RR')

    def test_decimal_half_even_and_tolerance_preserved(self):
        self.assertEqual(amounts({'subtotal': '2.675', 'vat': '0'}, {'total_amount': '2.675'}),
                         (Decimal('2.68'), Decimal('0.00'), Decimal('2.68')))
        self.assertIsNotNone(amounts({'subtotal': '100', 'vat': '7'}, {'total_amount': '107.02'}))
        self.assertIsNone(amounts({'subtotal': '100', 'vat': '7'}, {'total_amount': '107.03'}))

    def test_stock_groups_never_pick_majority(self):
        config = {'reported_stock_acc_groups': [{'acccod': 'A', 'fit': True, 'used_by': 999}, {'acccod': 'B', 'fit': True}]}
        self.assertEqual(resolve_stock_acc_group(config).fail_reason, 'stock_acc_group_required')
        config['stock_acccod'] = 'B'
        self.assertEqual(resolve_stock_acc_group(config).acccod, 'B')

    def test_reported_other_account_requires_same_reported_root(self):
        self.endpoint['config']['reported_account_sets'] = [{'path': r'C:\EXP\DATA2', 'writable': True, 'express_root': r'C:\EXP'}]
        self.assertTrue(account_set_allowed(r'C:\EXP\DATA2', self.endpoint, r'C:\EXP'))
        self.assertFalse(account_set_allowed(r'C:\EXP\DATA2', self.endpoint, r'D:\EXP'))

    def test_pure_package_has_no_framework_store_or_environment_imports(self):
        directory = Path(__file__).resolve().parents[1] / 'apps/pearnly_cowork/pearnly_cowork/push/express'
        for path in directory.glob('*.py'):
            tree = ast.parse(path.read_text())
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    modules = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom):
                    modules = [n.module or '']
                else:
                    continue
                self.assertFalse(any(x.split('.')[0] in {'frappe', 'requests', 'httpx', 'os', 'socket', 'services', 'core'} for x in modules), path)

if __name__ == '__main__':
    unittest.main()
