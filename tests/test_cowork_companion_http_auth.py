"""Pure envelope rules extracted from the native bridge; no framework doubles.

The actual Frappe hook, session, transaction and HTTP chains are exercised by
cowork-companion-heartbeat-lab-verify.py against the locked candidate runtime.
"""
import ast
from pathlib import Path
import re
import unittest

from pearnly_cowork.companion.contracts import parse_token

SOURCE = Path(__file__).resolve().parents[1] / 'apps/pearnly_cowork/pearnly_cowork/companion/http_auth.py'


def pure_contract():
    tree = ast.parse(SOURCE.read_text())
    names = {'METHOD', 'PATH', 'METHODS', 'PATHS', 'FOLDER_METHODS', 'REQUEST_METHODS', 'MACHINE_METHODS',
             'MACHINE_PATHS', '_HEADER'}
    selected = [node for node in tree.body if
        isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in
            {'RequestContractError', '_machine_candidate', '_envelope'}
        or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)]
    env = {'re': re, 'parse_token': parse_token}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), env)
    return env


class CompanionHttpContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = pure_contract()

    def values(self, **updates):
        value = {'path': self.contract['PATH'], 'method': 'POST',
                 'header': 'Bearer exp_CWEC-synthetic_Ab-c_d', 'cookies': {},
                 'query': {}, 'form': {}, 'payload': {}, 'session_user': 'Guest'}
        value.update(updates)
        return value

    def check(self, **updates):
        return self.contract['_envelope'](**self.values(**updates))

    def deny(self, **updates):
        with self.assertRaises(self.contract['RequestContractError']): self.check(**updates)

    def test_canonical_post_with_json_object_or_empty_body(self):
        self.assertEqual(self.check(), 'exp_CWEC-synthetic_Ab-c_d')
        self.assertEqual(self.check(payload={'offline': False, 'account_sets': []}), 'exp_CWEC-synthetic_Ab-c_d')
        self.assertEqual(self.check(header='bearer exp_CWEC-synthetic_Ab-c_d'), 'exp_CWEC-synthetic_Ab-c_d')

    def test_other_methods_are_not_machine_methods(self):
        for method in ('GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE', 'PATCH'):
            self.deny(method=method)

    def test_v1_v2_and_suffix_aliases_are_not_canonical(self):
        method = self.contract['METHOD']; path = self.contract['PATH']
        for other in ('/api/v1/method/' + method, '/api/v2/method/' + method,
                      path + '/', path + '/ignored', '//api/method/' + method, '/api/method/frappe.auth.get_logged_user'):
            self.deny(path=other)

    def test_credential_candidate_is_detected_on_any_route_and_bad_spacing(self):
        candidate = self.contract['_machine_candidate']
        for header in ('Bearer exp_CWEC-a_s', 'Bearer  exp_CWEC-a_s', 'Basic exp_CWEC-a_s',
                       'Bearer exp_CWEC-a_s, token other', 'bearer EXP_CWEC-a_s'):
            self.assertTrue(candidate('/api/resource/User', header))
        self.assertFalse(candidate('/api/resource/User', 'token ordinary:key'))

    def test_machine_alias_or_command_without_token_is_still_guarded(self):
        candidate = self.contract['_machine_candidate']; method = self.contract['METHOD']
        for path in ('/api/method/', '/api/v1/method/', '/api/v2/method/'):
            self.assertTrue(candidate(path + method, ''))
        self.assertTrue(candidate('/', '', method))
        self.assertFalse(candidate('/api/method/frappe.auth.get_logged_user', ''))

    def test_malformed_or_multiple_authorization_does_not_fall_back_to_cookie(self):
        for header in ('', 'Bearer ', 'Bearer  exp_a_s', 'Bearer\texp_a_s',
                       'Basic exp_a_s', 'token key:secret', 'Bearer exp_a_s\n',
                       'Bearer exp_a_s, Bearer other', ' Bearer exp_a_s'):
            self.deny(header=header)

    def test_body_or_query_token_does_not_authenticate(self):
        for key in ('token', 'access_token', 'authorization'):
            self.deny(header='', payload={key: 'exp_a_s'})
            self.deny(header='', query={key: 'exp_a_s'})

    def test_valid_cookie_and_even_guest_sid_are_explicitly_rejected(self):
        for sid in ('synthetic-session', 'Guest', ''):
            self.deny(cookies={'sid': sid})
        self.deny(session_user='manager@example.invalid')

    def test_raw_cmd_is_rejected_even_if_empty_or_ignored_by_json_form_dict(self):
        for field in ('query', 'form', 'payload'):
            for value in ('frappe.auth.get_logged_user', 'login', self.contract['METHOD'], '', None):
                self.deny(**{field: {'cmd': value}})

    def test_raw_sid_is_rejected_even_after_session_consumes_form_dict(self):
        for field in ('query', 'form', 'payload'):
            for value in ('synthetic-session', 'Guest', '', None): self.deny(**{field: {'sid': value}})

    def test_json_array_null_scalar_or_credential_source_override_rejected(self):
        for payload in ([], None, 'text', 1, True): self.deny(payload=payload)
        self.deny(authorization_source='Custom User')

    def test_non_auth_query_or_language_cookie_cannot_change_report_body(self):
        body = {'device_name': 'synthetic', 'offline': True}
        self.assertTrue(self.check(query={'device_name': 'ignored'}, cookies={'preferred_language': 'th'}, payload=body))
        self.assertEqual(body, {'device_name': 'synthetic', 'offline': True})

    def test_bridge_has_no_whitelist_and_auth_does_not_register_machine_worker(self):
        source = SOURCE.read_text()
        self.assertNotIn('@frappe.whitelist', source)
        self.assertNotIn('frappe.db.commit(', source)
        self.assertNotIn('allow_guest', source)
        self.assertIn('frappe.local.session_obj = None', source)



class FolderMachineMethods(unittest.TestCase):
    """文件夹自动录入的四个机器方法与原来三个同一套信封规则；原来三个的表逐字不变。"""

    @classmethod
    def setUpClass(cls):
        cls.contract = pure_contract()

    def test_tables(self):
        methods, folder = self.contract['METHODS'], self.contract['FOLDER_METHODS']
        requests = self.contract['REQUEST_METHODS']
        self.assertEqual(set(methods), {'heartbeat', 'lease', 'ack'})
        self.assertFalse(set(methods) & set(folder))
        self.assertFalse((set(methods) | set(folder)) & set(requests))
        self.assertEqual(self.contract['MACHINE_METHODS'], {**methods, **folder, **requests})
        self.assertEqual(set(self.contract['PATHS'].values()) & {'/api/method/' + m for m in folder.values()}, set())
        self.assertEqual(len(set(self.contract['MACHINE_PATHS'].values())), len(methods) + len(folder) + len(requests))

    def test_folder_paths_use_the_same_envelope(self):
        for method in self.contract['FOLDER_METHODS'].values():
            path = '/api/method/' + method
            self.assertTrue(self.contract['_machine_candidate'](path, ''))
            self.contract['_envelope'](path=path, method='POST', header='Bearer exp_CWEC-synthetic_Ab-c_d',
                                       cookies={}, query={}, form={}, payload={}, session_user='Guest')
            for bad in ({'method': 'GET'}, {'session_user': 'someone@example.com'}, {'cookies': {'sid': 'x'}},
                        {'payload': []}, {'query': {'cmd': 'x'}}):
                values = dict(path=path, method='POST', header='Bearer exp_CWEC-synthetic_Ab-c_d', cookies={},
                              query={}, form={}, payload={}, session_user='Guest')
                values.update(bad)
                with self.assertRaises(self.contract['RequestContractError']):
                    self.contract['_envelope'](**values)


class OnDemandRequestMethods(unittest.TestCase):
    """CC 桌面端按需请求的两条机器方法（2026-10 CC 闭环）：又一张表，同一套信封与配对码身份。"""

    @classmethod
    def setUpClass(cls):
        cls.contract = pure_contract()

    def test_the_table_names_the_two_request_api_methods(self):
        self.assertEqual(self.contract['REQUEST_METHODS'], {
            'request_next': 'pearnly_cowork.companion.request_api.next_request',
            'request_complete': 'pearnly_cowork.companion.request_api.complete_request'})

    def test_request_paths_use_the_same_envelope(self):
        for method in self.contract['REQUEST_METHODS'].values():
            path = '/api/method/' + method
            self.assertTrue(self.contract['_machine_candidate'](path, ''))
            for alias in ('/api/v1/method/', '/api/v2/method/'):
                self.assertTrue(self.contract['_machine_candidate'](alias + method, ''))
            base = dict(path=path, method='POST', header='Bearer exp_CWEC-synthetic_Ab-c_d', cookies={}, query={},
                        form={}, payload={'version': '1.5.0', 'kinds': []}, session_user='Guest')
            self.assertEqual(self.contract['_envelope'](**base), 'exp_CWEC-synthetic_Ab-c_d')
            for bad in ({'method': 'GET'}, {'session_user': 'someone@example.com'}, {'cookies': {'sid': 'x'}},
                        {'payload': []}, {'payload': {'cmd': 'x'}}, {'header': ''}, {'path': path + '/'}):
                with self.assertRaises(self.contract['RequestContractError']):
                    self.contract['_envelope'](**{**base, **bad})


class FolderOnlyKeys(unittest.TestCase):
    """MR.ERP 的小助手配对码只读文件夹（2026-10-09）：鉴权后、绑定身份前，非文件夹方法一律 403。"""

    def test_folder_only_adapters_are_refused_outside_the_folder_methods(self):
        tree = ast.parse(SOURCE.read_text())
        body = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_authenticate_request')
        text = ast.unparse(body)
        guard = text.index('credentials.FOLDER_ONLY_ADAPTERS')
        self.assertIn('if operation not in FOLDER_METHODS and operation not in REQUEST_METHODS and',
                      text[text.rindex('if', 0, guard) - 4:guard])
        self.assertLess(text.index('credentials.authenticate(token)'), guard)
        self.assertLess(guard, text.index('frappe.set_user(context.issuer)'))
        self.assertIn('_deny_permission()', text[guard:text.index('frappe.set_user(context.issuer)')])

    def test_only_mrerp_is_folder_only_and_both_can_hold_a_key(self):
        source = (SOURCE.parent / 'credentials.py').read_text()
        self.assertIn("HELPER_ADAPTERS = ('express', 'mrerp')", source)
        self.assertIn("FOLDER_ONLY_ADAPTERS = ('mrerp',)", source)
        self.assertNotIn("adapter != 'express'", source)


if __name__ == '__main__':
    unittest.main()
