"""Hash-bound legacy token primitives; no legacy imports or database/network.

Only the generated-token/hash/lookup-decision seam is compared. Native roles,
Site ownership, lifecycle, idempotency and concurrency have separate DB tests.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import secrets
import sys

from pearnly_cowork.companion.contracts import parse_token, token_matches

PATH = 'services/erp/express_push/agent_store.py'
HASH = '03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb'


def verify(source):
    data = source.encode('utf-8')
    assert hashlib.sha256(data).hexdigest() == HASH, 'Fixed source mismatch'
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'hash_token', 'authenticate'}]
    assert len(functions) == 2
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
        names=[ast.alias(name='annotations')], level=0), *functions], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {'hashlib': hashlib, 'secrets': secrets}
    exec(compile(module, PATH, 'exec'), namespace)
    rng = random.Random(20260921)
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'
    count = 0
    for index in range(80):
        endpoint = f'CWEC-{index:032x}' if index % 2 else f'{index:08x}-1234-5678-9abc-0123456789ab'
        # The source issues token_urlsafe(32): 43 chars, underscores included.
        token = 'exp_' + endpoint + '_' + ''.join(rng.choice(alphabet) for _ in range(43))
        parsed = parse_token(token)
        assert parsed is not None and parsed.connection == endpoint
        source_digest = namespace['hash_token'](token)
        assert parsed.digest == source_digest
        for exists, matched in ((True, True), (True, False), (False, False)):
            row = {'id': endpoint, 'config': {'agent_token_hash': source_digest if matched else '0' * 64}}
            namespace['get_express_endpoint'] = lambda name: row if exists and name == endpoint else None
            old = namespace['authenticate'](token)
            new = exists and token_matches(row['config']['agent_token_hash'], parsed.digest)
            assert bool(old) == new
            count += 1
    for token in ('', 'bad', 'EXP_a_b', 'exp_missing'):
        assert namespace['authenticate'](token) is None and parse_token(token) is None
        count += 1
    print(json.dumps({'companion_token_source_parity': 'PASS', 'cases': count,
                      'source_sha': 'ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188',
                      'scope': 'hash/generated-token/lookup-decision; synthetic endpoint lookup'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source')
    parser.add_argument('--source-stdin', action='store_true')
    args = parser.parse_args()
    if bool(args.source) == args.source_stdin:
        parser.error('Provide exactly one fixed source input')
    verify(json.load(sys.stdin)[PATH] if args.source_stdin else (Path(args.source) / PATH).read_text())
