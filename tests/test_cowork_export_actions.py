"""Bounded pure receipt contracts; the Site suite verifies authorization."""
import unittest

from pearnly_cowork.push.export_actions import parse_actions


class ExportActions(unittest.TestCase):
    def test_docnum_fallback_order_and_stock_group(self):
        value = parse_actions({'meta': {'docnum': ' IV001 ', 'created_customer': False, 'doc_type': ' SALES '},
            'line_modes': [{'seq': '2', 'created': False, 'stkcod': ' B '},
                           {'seq': 1, 'created': True, 'stkcod': 'A'}]},
            {'payload': {'stock_acccod': 'DM'}},
            [{'acccod': 'DM', 'stock_acc': '1100', 'stock_acc_name': 'Inventory'}])
        self.assertEqual(value, {'customer': False, 'items': [True, False], 'item_codes': ['A', 'B'],
            'docnum': 'IV001', 'party_code': '', 'direction': 'sales', 'created_party': False,
            'stock_acc_group': 'DM · 1100 · Inventory'})

    def test_unusable_order_does_not_shift_codes_or_drop_reference(self):
        value = parse_actions({'express_docnum': 'IV001',
            'line_modes': [{'seq': 'bad', 'created': True, 'stkcod': 'WRONG'}, {'seq': 2, 'stkcod': 'B'}]})
        self.assertEqual((value['docnum'], value['items'], value['item_codes']), ('IV001', [], []))

    def test_creation_flags_need_booleans_and_non_objects_are_empty(self):
        value = parse_actions({'meta': {'created_customer': 'false', 'created_supplier': 1},
                              'line_modes': [{'seq': 1, 'created': 'false', 'stkcod': 'B'}]})
        self.assertEqual((value['customer'], value['created_party'], value['items']), (None, False, [None]))
        self.assertEqual(parse_actions('not json')['items'], [])
