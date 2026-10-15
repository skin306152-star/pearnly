"""Pure value/receipt contracts. Native database paths live in the lab script."""
import ast
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from pearnly_cowork.companion import contracts as c

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / 'apps/pearnly_cowork/pearnly_cowork/companion'


class CredentialContracts(unittest.TestCase):
    def test_native_and_legacy_token_connection_names_and_secret_underscores(self):
        for connection in ('CWEC-01234567-89ab', '42', '01234567-89ab-cdef'):
            token = f'exp_{connection}_Ab-C_d012'
            value = c.parse_token(token)
            self.assertEqual(value.connection, connection)
            self.assertEqual(value.digest, hashlib.sha256(token.encode()).hexdigest())
            self.assertNotIn(value.digest, repr(value))

    def test_token_rejects_header_query_unicode_missing_or_oversized_values(self):
        for token in (None, 4, '', 'Bearer exp_1_abc', 'exp_1_', 'exp__abc', 'EXP_1_abc',
                      'exp_1_abc ', 'exp_账_abc', 'exp_1_é', 'exp_1_' + 'a' * 129,
                      'exp_1_a?x=1', 'exp_1_abc\n'):
            self.assertIsNone(c.parse_token(token), repr(token))

    def test_digest_comparison_is_constant_time_and_case_sensitive(self):
        digest = c.token_digest('exp_42_synthetic')
        with patch.object(c.hmac, 'compare_digest', wraps=c.hmac.compare_digest) as called:
            self.assertTrue(c.token_matches(digest, digest))
            self.assertEqual(called.call_count, 1)
        self.assertFalse(c.token_matches(digest.upper(), digest))
        self.assertFalse(c.token_matches(None, digest))
        self.assertFalse(c.token_matches(digest, c.token_digest('different')))

    def test_generation_cas_requires_exact_positive_integer(self):
        for value in (1, '1', 2147483647):
            self.assertEqual(c.generation(value), int(value))
        for value in (True, False, 0, -1, 1.0, ' 1', '1.0', '๑', None, 2147483648):
            with self.assertRaises(c.CredentialError): c.generation(value)

    def test_issue_requires_explicit_reset_with_expected_generation(self):
        self.assertEqual(c.issue_intent(False, None), {'operation': 'issue', 'reset': False, 'expected_generation': None})
        self.assertEqual(c.issue_intent(True, '2')['expected_generation'], 2)
        for values in ((False, 1), (True, None), ('true', 1), (1, 1)):
            with self.assertRaises(c.CredentialError): c.issue_intent(*values)

    def test_wire_flag_only_maps_exact_form_booleans(self):
        # 网页经表单提交，`reset` 到服务端是字符串；此前「重新生成」因此总是 request_invalid。
        for raw, expected in (('1', True), ('true', True), ('0', False), ('false', False), (True, True), (False, False)):
            self.assertIs(c.wire_flag(raw), expected)
        for raw in ('True', 'yes', ' 1', '', 1, None):
            self.assertEqual(c.wire_flag(raw), raw)
        self.assertEqual(c.issue_intent(c.wire_flag('1'), '3')['expected_generation'], 3)
        with self.assertRaises(c.CredentialError): c.issue_intent(c.wire_flag('yes'), 1)

    def test_revoke_normalizes_reason_but_does_not_drop_generation(self):
        self.assertEqual(c.revoke_intent('2', ' synthetic '),
                         {'operation': 'revoke', 'expected_generation': 2, 'reason': 'synthetic'})
        with self.assertRaises(c.CredentialError): c.revoke_intent(1, 'x' * 241)

    def test_operation_scope_includes_site_actor_connection_and_request(self):
        values = ['site', 'actor', 'connection', 'key']
        base = c.operation_identity(*values)
        for index in range(4):
            changed = list(values); changed[index] += 'other'
            self.assertNotEqual(base, c.operation_identity(*changed))
        self.assertEqual(len(base), 64)

    def test_case_sensitive_keys_are_not_collation_identifiers(self):
        self.assertNotEqual(c.active_identity('s', 'C'), c.active_identity('s', 'c'))
        self.assertNotEqual(c.generation_identity('s', 'C', 1), c.generation_identity('s', 'C', 2))

    def test_receipt_replay_is_canonical_and_conflicting_intent_is_rejected(self):
        op = c.fingerprint('request'); intent = c.issue_intent(False, None)
        raw = c.append_receipt({}, op, intent, 'created')
        self.assertEqual(raw, c.append_receipt(raw, op, intent, 'created'))
        with self.assertRaisesRegex(c.CredentialError, 'companion.request_conflict'):
            c.append_receipt(raw, op, c.issue_intent(True, 1), 'created')

    def test_receipts_store_only_hashes_and_result_not_token_or_request(self):
        op = c.operation_identity('s', 'a', 'c', 'synthetic-private-request')
        raw = c.append_receipt({}, op, c.issue_intent(False, None), 'created')
        self.assertNotIn('synthetic-private-request', raw)
        self.assertEqual(set(json.loads(raw)[op]), {'intent_hash', 'result'})

    def test_receipt_limit_always_reserves_room_for_first_revocation(self):
        with patch.object(c, 'MAX_RECEIPT_BYTES', 2048):
            raw = '{}'; index = 0
            while True:
                try: raw = c.append_receipt(raw, c.fingerprint(index), {}, 'exists')
                except c.CredentialError as error:
                    self.assertEqual(error.code, 'companion.receipt_limit'); break
                index += 1
            revoked = c.append_receipt(raw, c.fingerprint('revoke'), c.revoke_intent(1, ''), 'revoked')
            self.assertGreater(len(revoked), len(raw))
            self.assertLessEqual(len(revoked), 2048)
            self.assertEqual(c.append_receipt(revoked, c.fingerprint('revoke'), c.revoke_intent(1, ''), 'revoked'), revoked)

    def test_receipt_rejects_malformed_containers_and_non_finite_values(self):
        for raw in ('[]', '{', '[1]'):
            with self.assertRaises(c.CredentialError): c.append_receipt(raw, c.fingerprint('r'), {}, 'exists')
        with self.assertRaises(c.CredentialError): c.encode({'value': float('nan')})

    def test_context_is_immutable_and_plain_constructor_has_no_authority(self):
        value = c.AgentContext('s', 'c', 'w', '', 'u', 'id', 1)
        self.assertIsNone(value._capability)
        with self.assertRaises(FrozenInstanceError): value.issuer = 'other'

    def test_schema_has_unique_active_generation_and_request_keys_and_no_versioning(self):
        schema = json.loads((DOMAIN.parent / 'pearnly_cowork/doctype/cowork_express_agent_credential/cowork_express_agent_credential.json').read_text())
        fields = {f['fieldname']: f for f in schema['fields']}
        for field in ('active_key', 'generation_key', 'issued_request_digest', 'token_digest'):
            self.assertEqual(fields[field]['unique'], 1)
        self.assertFalse(schema.get('track_changes'))
        self.assertFalse(any(f['fieldtype'] == 'Password' for f in fields.values()))
        # 报到时间（2026-10-07 起在密钥行上）、所属电脑与「整份重报」（ADR-021）都不是身份：不唯一、不进 _IDENTITY
        identity = ast.literal_eval(next(
            node.value for node in ast.parse((DOMAIN / 'documents.py').read_text()).body
            if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == '_IDENTITY'))
        for field in ('last_seen_at', 'last_seen_generation', 'device', 'full_report_due'):
            self.assertFalse(fields[field].get('unique'), field)
            self.assertNotIn(field, identity)

    def test_only_human_management_rpc_methods_are_whitelisted(self):
        tree = ast.parse((DOMAIN / 'api.py').read_text())
        exposed = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.decorator_list}
        # installer 只回安装包地址（部署配置），同样是登录用户的 GET、先过 require_actor。
        # register 只收 POST（CC 桌面端登记这台电脑）；管理用的 issue / revoke / status 已下线（ADR-021）。
        self.assertEqual(exposed, {'online', 'installer', 'register'})
        register = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'register')
        self.assertIn("methods=['POST']", ast.unparse(register.decorator_list[0]))
        self.assertIn('_local_transaction(credentials.register', ast.unparse(register))
        # online 只回在不在线（录入角色也要能选 Express），走连接的使用授权、不要求管理权限
        online = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'online')
        self.assertIn("methods=['GET']", ast.unparse(online.decorator_list[0]))
        self.assertIn('pool.for_user(connection', ast.unparse(online))  # 池的状态（ADR-021），形状兼容旧页面
        installer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'installer')
        self.assertIn("methods=['GET']", ast.unparse(installer.decorator_list[0]))
        self.assertIn('access.require_actor()', ast.unparse(installer))
        self.assertNotIn('allow_guest', (DOMAIN / 'api.py').read_text())
        self.assertIn('cowork_private_response = True', (DOMAIN / 'api.py').read_text())
        service = (DOMAIN / 'credentials.py').read_text()
        self.assertNotIn('@frappe.whitelist', service)
        self.assertNotIn('frappe.set_user(', service)
        self.assertNotIn('frappe.db.commit(', service)

    def test_pure_contract_has_no_framework_io_dependency(self):
        tree = ast.parse((DOMAIN / 'contracts.py').read_text())
        imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imported, {'dataclasses', 'hashlib', 'hmac', 'json', 're'})


if __name__ == '__main__': unittest.main()
