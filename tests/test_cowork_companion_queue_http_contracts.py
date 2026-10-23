"""Pure queue HTTP envelope matrix, extracted from the actual native bridge.

No Frappe doubles, authentication, transaction or current_request execution.
Real WSGI tests separately verify operation-bound AgentContext/ReceiptContext,
revocation, response wrapping, sessions, Recorder and safe transaction failures.
"""
from copy import deepcopy
import unittest

from pearnly_core.cc_desktop.wire.queue_contracts import QueueContractError, ack_request, lease_request
from test_cowork_companion_http_auth import pure_contract


TOKEN = 'exp_CWEC-synthetic_Ab-c_d'
DELIVERY = '972778cf-0b5a-4815-a687-bec2d3738db5'
METHODS = {
    'heartbeat': 'pearnly_cowork.companion.heartbeat.report',
    'lease': 'pearnly_cowork.companion.queue_api.lease',
    'ack': 'pearnly_cowork.companion.queue_api.acknowledge',
}


class CompanionQueueHttpContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = pure_contract()

    def values(self, operation, **updates):
        value = {'path': '/api/method/' + METHODS[operation], 'method': 'POST',
                 'header': 'Bearer ' + TOKEN, 'cookies': {}, 'query': {}, 'form': {},
                 'payload': {}, 'session_user': 'Guest'}
        value.update(updates)
        return value

    def check(self, operation, **updates):
        return self.contract['_envelope'](**self.values(operation, **updates))

    def deny(self, operation, **updates):
        with self.assertRaises(self.contract['RequestContractError']):
            self.check(operation, **updates)

    def test_route_map_is_exact_and_heartbeat_constants_remain_compatible(self):
        self.assertEqual(self.contract['METHODS'], METHODS)
        self.assertEqual(self.contract['PATHS'], {key: '/api/method/' + method for key, method in METHODS.items()})
        self.assertEqual(self.contract['METHOD'], METHODS['heartbeat'])
        self.assertEqual(self.contract['PATH'], '/api/method/' + METHODS['heartbeat'])

    def test_queue_canonical_post_accepts_json_object_with_bearer(self):
        for operation, body in (('lease', {'max': 1}), ('ack', {'log_id': DELIVERY, 'result': 'success'})):
            for scheme in ('Bearer', 'bearer', 'BEARER'):
                with self.subTest(operation=operation, scheme=scheme):
                    self.assertEqual(self.check(operation, header=scheme + ' ' + TOKEN, payload=body), TOKEN)

    def test_all_machine_method_aliases_and_commands_are_candidates_without_a_token(self):
        candidate = self.contract['_machine_candidate']
        for method in METHODS.values():
            for prefix in ('/api/method/', '/api/v1/method/', '/api/v2/method/'):
                self.assertTrue(candidate(prefix + method, ''))
                self.assertTrue(candidate(prefix + method + '/ignored', ''))
            self.assertTrue(candidate('/', '', method))
        self.assertFalse(candidate('/api/method/frappe.auth.get_logged_user', ''))

    def test_legacy_v1_v2_suffix_and_altered_paths_never_pass_the_envelope(self):
        for operation in ('lease', 'ack'):
            method = METHODS[operation]; path = '/api/method/' + method
            aliases = ('/api/v1/method/' + method, '/api/v2/method/' + method,
                       path + '/', path + '/ignored', path + 'extra', '//api/method/' + method,
                       '/API/method/' + method, path.replace('.', '%2E'),
                       '/api/erp/agent/' + ('ack' if operation == 'ack' else 'lease'))
            for alias in aliases:
                with self.subTest(operation=operation, path=alias): self.deny(operation, path=alias)

    def test_every_non_post_method_is_rejected_for_both_queue_methods(self):
        for operation in ('lease', 'ack'):
            for method in ('GET', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'DELETE', 'TRACE', 'post'):
                with self.subTest(operation=operation, method=method): self.deny(operation, method=method)

    def test_api_key_basic_missing_duplicate_or_badly_spaced_bearer_are_rejected(self):
        headers = ('', 'token human-key:human-secret', 'Basic dXNlcjpwYXNz', 'Basic ' + TOKEN,
                   'Bearer ', 'Bearer  ' + TOKEN, 'Bearer\t' + TOKEN, ' Bearer ' + TOKEN,
                   'Bearer ' + TOKEN + '\n', 'Bearer ' + TOKEN + ', Bearer other',
                   'Bearer brg_other_secret', 'Bearer exp_invalid', 'Bearer EXP_CWEC-synthetic_Ab-c_d')
        for operation in ('lease', 'ack'):
            for header in headers:
                with self.subTest(operation=operation, header=header): self.deny(operation, header=header)

    def test_cmd_and_sid_in_all_raw_sources_are_rejected_even_if_empty(self):
        for operation in ('lease', 'ack'):
            for source in ('query', 'form', 'payload'):
                for field in ('cmd', 'sid'):
                    for value in (None, '', 'Guest', 'login', *METHODS.values()):
                        with self.subTest(operation=operation, source=source, field=field, value=value):
                            self.deny(operation, **{source: {field: value}})

    def test_browser_sid_and_authenticated_session_cannot_supply_machine_authority(self):
        for operation in ('lease', 'ack'):
            for sid in ('session-id', 'Guest', '', None): self.deny(operation, cookies={'sid': sid})
            for user in ('Administrator', 'manager@example.invalid'):
                self.deny(operation, session_user=user)
            for user in ('Guest', '', None):
                self.assertEqual(self.check(operation, session_user=user), TOKEN)

    def test_non_object_json_never_reaches_either_queue_contract(self):
        for operation in ('lease', 'ack'):
            for payload in (None, [], ['value'], 'text', 1, True):
                with self.subTest(operation=operation, payload=payload): self.deny(operation, payload=payload)

    def test_body_query_or_form_credentials_are_not_a_header_fallback(self):
        for operation in ('lease', 'ack'):
            for source in ('payload', 'query', 'form'):
                for key in ('token', 'access_token', 'authorization'):
                    self.deny(operation, header='', **{source: {key: TOKEN}})

    def test_frappe_authorization_source_cannot_redirect_token_lookup(self):
        for operation in ('lease', 'ack'):
            for source in ('User', 'Custom User', 'Cowork Express Agent Credential'):
                self.deny(operation, authorization_source=source)

    def test_non_auth_query_and_language_cookie_do_not_rewrite_json_body(self):
        for operation, body in (('lease', {'max': 1, 'agent_id': 'A'}),
                                ('ack', {'log_id': DELIVERY, 'result': 'failed', 'meta': {'written': False}})):
            before = deepcopy(body)
            self.assertEqual(self.check(operation, payload=body, query={'max': 50, 'result': 'success'},
                                        cookies={'preferred_language': 'th'}), TOKEN)
            self.assertEqual(body, before)

    def test_machine_token_on_unrelated_or_human_rpc_is_a_candidate_but_is_denied(self):
        candidate = self.contract['_machine_candidate']
        for path in ('/api/resource/User', '/api/method/frappe.auth.get_logged_user',
                     '/api/method/pearnly_cowork.companion.api.register',
                     '/api/method/pearnly_cowork.companion.api.online',
                     # 已下线的管理接口（ADR-021）：带机器密钥同样是候选、同样拒绝
                     '/api/method/pearnly_cowork.companion.api.issue',
                     '/api/method/pearnly_cowork.companion.api.revoke',
                     '/api/method/pearnly_cowork.companion.api.status'):
            for header in ('Bearer ' + TOKEN, 'Basic ' + TOKEN, 'Bearer  ' + TOKEN):
                self.assertTrue(candidate(path, header))
                self.deny('lease', path=path, header=header)

    def test_ack_body_preserves_receipt_fields_and_drops_untrusted_identity_fields(self):
        for result in ('success', 'failed'):
            for outcome in (None, 'success', 'waiting_lock', 'failed', 'rolled_back', 'needs_mapping', 'needs_review'):
                body = {'log_id': DELIVERY, 'result': result, 'express_docnum': 'RR-1', 'error': 'reported',
                        'agent_id': 'untrusted-label', 'line_modes': [{'mode': 'direct_account', 'reason': 'unmapped'}],
                        'outcome': outcome, 'meta': {'written': True, 'stage': 'client-claimed-stage'}}
                self.assertEqual(self.check('ack', payload=body), TOKEN)
                self.assertEqual(ack_request(body), body)
        # These values are discarded input, not authorization evidence. The
        # real bridge must independently bind ReceiptContext to this delivery.
        extra = {**body, 'issuer': 'Administrator', 'operation': 'lease', 'fence': 99, 'attempt_token': 'untrusted'}
        self.assertEqual(ack_request(extra), body)

    def test_invalid_ack_objects_pass_only_the_envelope_then_fail_before_mutation(self):
        for body in ({}, {'log_id': DELIVERY}, {'log_id': DELIVERY, 'result': 'unknown'},
                     {'log_id': 'native-record', 'result': 'success'},
                     {'log_id': DELIVERY, 'result': 'success', 'line_modes': ['invalid']},
                     {'log_id': DELIVERY, 'result': 'success', 'meta': []}):
            self.assertEqual(self.check('ack', payload=body), TOKEN)
            with self.assertRaises(QueueContractError): ack_request(body)

    def test_lease_uses_only_json_contract_defaults_not_query_or_identity_fields(self):
        for maximum in (1, 10, 50):
            body = {'max': maximum, 'agent_id': 'A', 'connection': 'untrusted', 'issuer': 'Administrator'}
            self.assertEqual(self.check('lease', payload=body, query={'max': 51}), TOKEN)
            self.assertEqual(lease_request(body), {'max': maximum, 'agent_id': 'A'})
        self.assertEqual(self.check('lease', payload={}, query={'max': 50}), TOKEN)
        self.assertEqual(lease_request({}), {'max': 10, 'agent_id': None})
        for maximum in (0, 51, True, '1', 1.0):
            body = {'max': maximum}
            self.assertEqual(self.check('lease', payload=body), TOKEN)
            with self.assertRaises(QueueContractError): lease_request(body)


if __name__ == '__main__':
    unittest.main()
