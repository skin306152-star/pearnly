"""Explicit offline comparison with the fixed live-source parser, no legacy IO."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from pearnly_cowork.push.export_actions import parse_actions
from pearnly_cowork.push.receipts import _coerce_body
from pearnly_cowork.push.express.stock_acc_group import describe_from_request, stock_acc_group_label


def verify(root):
    raw = (Path(root) / 'services/erp/export_actions.py').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'fcb995d4f03658ad72c8dd8472b8dc878e46f2275fa9481d2d055dc1282a5ec0'
    tree = ast.parse(raw)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_parse_erp_actions', '_stock_acc_group_label'}]
    future = ast.parse('from __future__ import annotations').body
    namespace = {'_coerce_body': _coerce_body, 'describe_from_request': describe_from_request,
                 'stock_acc_group_label': stock_acc_group_label}
    exec(compile(ast.Module(body=future + functions, type_ignores=[]), 'fixed_export_actions', 'exec'), namespace)
    source = namespace['_parse_erp_actions']
    body = {'express_docnum': 'IV001', 'meta': {'created_customer': True, 'party_code': 'C001', 'doc_type': 'sales'},
            'line_modes': [{'seq': 2, 'created': False, 'stkcod': ' B '}, {'seq': 1, 'created': True, 'stkcod': 'A'}]}
    group = [{'acccod': 'DM', 'stock_acc': '1100', 'stock_acc_name': 'Inventory'}]
    cases = [({}, None, None), (None, None, None), ('invalid', None, None),
        (body, None, None), (json.dumps(body), None, None),
        ({'meta': {'created_supplier': True, 'docnum': 'RR001', 'doc_type': 'purchase'}}, None, None),
        ({'express_docnum': 'FIRST', 'meta': {'docnum': 'SECOND'}}, None, None),
        ({'line_modes': [None, {'created': False}, {'seq': '2', 'created': True}]}, None, None),
        (body, {'payload': {'stock_acccod': 'DM'}}, group),
        (body, {'stock_acccod': 'DM'}, [])]
    for index, args in enumerate(cases):
        assert source(*args) == parse_actions(*args), index
    print(f'PASS {len(cases)} fixed-source receipt cases; no legacy application imported')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    verify(parser.parse_args().source)
