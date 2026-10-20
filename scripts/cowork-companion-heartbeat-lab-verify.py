"""Synthetic WSGI Companion report tests; dedicated lab Site only, no external IO.

Writes/commits labelled credentials, report snapshots and refreshes. Temporarily
sets only this marked Site's test configuration and restores it. Recorder tests
preserve global settings and inspect only entries produced during each test.
"""
from copy import deepcopy
import argparse
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.config import clear_site_config_cache
from werkzeug.test import Client, EnvironBuilder
from werkzeug.wrappers import Request, Response
from pearnly_cowork.transaction.conflicts import ATTEMPTS  # 共用的整笔重来（10-20 起 4 次、带退避）

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-companion-credentials-lab-verify.py')))
BASE = FIXTURE['NativeCredentials']
connect = FIXTURE['connect']
METHOD = 'pearnly_cowork.companion.heartbeat.report'
PATH = '/api/method/' + METHOD


class NativeHeartbeat(unittest.TestCase):
    setUpClass = classmethod(BASE.setUpClass.__func__)
    tearDownClass = classmethod(BASE.tearDownClass.__func__)

    def setUp(self):
        from frappe.app import application
        from pearnly_cowork.companion import credentials, heartbeat, http_auth
        from pearnly_cowork.connections import collector, service
        self.credentials, self.heartbeat, self.http_auth = credentials, heartbeat, http_auth
        self.collector, self.connections = collector, service
        self.old_conf = deepcopy(dict(frappe.conf))
        self.site = frappe.local.site
        self.base = 'http://' + self.site
        self.config_path = Path(frappe.get_site_path('site_config.json'))
        self.original_config = self.config_path.read_text()
        config = json.loads(self.original_config)
        self.assertTrue(config['pearnly_backend_lab'])
        config['pearnly_cowork_erp_push_enabled'] = True
        config['pearnly_features'] = ['intake', 'master_data', 'push_log']
        self.config_path.write_text(json.dumps(config))
        clear_site_config_cache()
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        frappe.conf.pearnly_features = config['pearnly_features']
        frappe.set_user(self.manager)
        self.connection = service.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic HTTP companion', {'account_set': 'DATAT', 'express_root': r'D:\SYNTHETIC'},
            users=[self.manager, self.actor])
        self.issued = credentials.issue(self.connection['name'], uuid4().hex)
        self.token = self.issued['token']
        frappe.set_user('Administrator')
        manager = frappe.get_doc('User', self.manager)
        manager.api_key, self.api_secret = uuid4().hex, uuid4().hex
        manager.api_secret = self.api_secret; manager.save()
        self.human_headers = {'Authorization': f'token {manager.api_key}:{self.api_secret}'}
        frappe.db.commit(); frappe.destroy()
        self.client = Client(application, Response, use_cookies=False)

    def tearDown(self):
        self.config_path.write_text(self.original_config)
        clear_site_config_cache()
        if getattr(frappe.local, 'db', None): frappe.db.rollback(); frappe.destroy()
        connect(self.site)
        frappe.conf.clear(); frappe.conf.update(self.old_conf)
        frappe.set_user('Administrator')

    def db(self, function, *, commit=False):
        if getattr(frappe.local, 'db', None): frappe.db.rollback(); frappe.destroy()
        connect(self.site); frappe.set_user(self.manager)
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        frappe.conf.pearnly_features = ['intake', 'master_data', 'push_log']
        try:
            result = function()
            if commit: frappe.db.commit()
            return result
        finally:
            frappe.db.rollback(); frappe.destroy()

    def request(self, path=PATH, *, method='POST', body=None, headers=None, raw=None, content_type=None):
        arguments = {'base_url': self.base, 'method': method, 'buffered': True,
                     'headers': {'Authorization': 'Bearer ' + self.token} if headers is None else headers}
        if raw is not None:
            arguments.update(data=raw, content_type=content_type or 'application/json')
        elif method not in ('GET', 'HEAD', 'OPTIONS'):
            arguments['json'] = {} if body is None else body
        response = self.client.open(path, **arguments)
        # Errors must also avoid changing a browser/Guest session or caching.
        if 'exp_' in arguments['headers'].get('Authorization', '') or METHOD in path:
            self.assertEqual(response.headers.getlist('Set-Cookie'), [])
            self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        return response

    def report(self, body=None):
        response = self.request(body=body)
        self.assertEqual(response.status_code, 200)
        return response.json['message']

    def body(self, **updates):
        body = {'device': 'Synthetic machine', 'account_set': 'DATAT', 'express_root': r'D:\SYNTHETIC',
            'companion_version': 'synthetic-1', 'max_payload_version': 2,
            'account_sets': [{'code': 'DATAT', 'name': 'Synthetic book', 'path': 'DATAT',
                'root': r'D:\SYNTHETIC', 'writable': True}],
            'accounts': [{'code': '4000', 'name': 'Synthetic sales'}],
            'catalog': {'products': [{'code': 'SYN-1', 'name': 'Synthetic product'}],
                        'customers': [], 'fingerprint': 'synthetic-http'}}
        body.update(updates)
        return body

    def persisted(self):
        return self.db(lambda: frappe.get_doc('Cowork ERP Connection', self.connection['name']).as_dict())

    def facts(self):
        from pearnly_cowork.connections import pool
        return self.db(lambda: pool.facts(self.connection['name'], 'DATAT')) or {}

    def test_real_report_preserves_body_and_publishes_local_directory(self):
        result = self.report(self.body())
        self.assertTrue(result['ok']); self.assertTrue(result['connected'])
        self.assertEqual(result['endpoint_id'], self.connection['name'])
        self.assertEqual(result['accounts_received'], 1)
        self.assertNotIn(self.token, json.dumps(result))
        # 多台电脑之后（ADR-021 第 2 批）：连接的上报记录只留被拦的自动更新，电脑名、版本记在电脑上，主档进这份账套的资料
        row = self.persisted(); report = json.loads(row['report_json'] or '{}')
        self.assertFalse({'agent_device_name', 'reported_accounts', 'companion_version'} & set(report))
        self.assertEqual(self.facts()['reported_accounts'][0]['code'], '4000')
        targets = self.db(lambda: self.connections.targets(self.connection['name']))
        self.assertTrue(targets['account_sets'])
        self.assertEqual(targets['account_sets'][0]['source_id'], 'datat')

    def test_human_refresh_machine_lease_and_completion_through_http(self):
        self.report(self.body())
        rpc = '/api/method/pearnly_cowork.connections.api.request_refresh'
        requested = self.request(rpc, headers=self.human_headers,
            body={'name': self.connection['name'], 'request_key': uuid4().hex})
        self.assertEqual(requested.status_code, 200)
        human = requested.json['message']
        lease = self.report({})['master_refresh']
        self.assertNotEqual(lease['request_id'], human['request_id'])
        self.assertEqual(lease['scope_kind'], 'endpoint')
        completed = self.report(self.body(master_refresh_request_id=lease['request_id'],
            master_refresh_scope=lease['scope_kind'], master_refresh_account_set=lease.get('account_set_key') or ''))
        self.assertTrue(completed['ok'])
        state = self.db(lambda: frappe.db.get_value('Cowork ERP Refresh', human['request_id'], 'status'))
        self.assertEqual(state, 'succeeded')

    def test_no_cookie_session_update_or_guest_cache_mutation_on_machine_success(self):
        from frappe.sessions import Session
        baseline = self.db(lambda: (frappe.db.count('Sessions'), frappe.cache.hget('session', 'Guest')))
        with patch.object(Session, 'update', side_effect=AssertionError('machine must not update a browser session')) as update:
            self.report(self.body())
        self.assertEqual(update.call_count, 0)
        final = self.db(lambda: (frappe.db.count('Sessions'), frappe.cache.hget('session', 'Guest')))
        self.assertEqual(final, baseline)

    def test_private_handoff_skips_native_token_parsers_but_keeps_ip_validation(self):
        from frappe import auth
        real_authenticate = self.credentials.authenticate
        def inspect_handoff(token):
            self.assertFalse(frappe.request.headers.get('Authorization'))
            envelope = frappe.local.cowork_companion_http_envelope
            self.assertFalse(self.token in repr(envelope))
            return real_authenticate(token)
        real_ingest = self.collector.ingest
        def inspect_consumed(*args, **kwargs):
            self.assertIsNone(frappe.local.cowork_companion_http_envelope)
            return real_ingest(*args, **kwargs)
        with patch.object(auth, 'validate_oauth', side_effect=AssertionError('machine OAuth must not run')) as oauth:
            with patch.object(auth, 'validate_auth_via_api_keys', side_effect=AssertionError('machine API key must not run')) as api_key:
                with patch.object(auth, 'validate_ip_address', wraps=auth.validate_ip_address) as ip:
                    with patch.object(self.credentials, 'authenticate', side_effect=inspect_handoff):
                        with patch.object(self.collector, 'ingest', side_effect=inspect_consumed):
                            self.report(self.body())
                    self.assertEqual([call.args for call in ip.call_args_list], [('Guest',), (self.manager,)])
                self.assertEqual(api_key.call_count, 0)
            self.assertEqual(oauth.call_count, 0)

    def test_native_ip_failure_keeps_diagnostics_without_machine_token_snapshot(self):
        from frappe import auth
        from frappe.utils import error
        real_log_error = error.log_error
        real_ip = auth.validate_ip_address
        def fail_issuer_ip(user):
            if user == self.manager: raise RuntimeError('synthetic native IP failure')
            return real_ip(user)
        captured = []
        def inspect_snapshot(*args, **kwargs):
            captured.append(self.token in frappe.get_traceback(with_context=True))
            self.assertFalse(frappe.request.headers.get('Authorization'))
            self.assertIsNone(frappe.local.cowork_companion_http_envelope)
            return real_log_error(*args, **kwargs)
        with patch.object(error, 'log_error', side_effect=inspect_snapshot) as snapshot:
            with patch.object(auth, 'validate_ip_address', side_effect=fail_issuer_ip) as ip:
                with patch.object(self.collector, 'ingest', wraps=self.collector.ingest) as ingest:
                    response = self.request(body=self.body())
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual([call.args for call in ip.call_args_list], [('Guest',), (self.manager,)])
                    self.assertEqual(ingest.call_count, 0)
                    self.assertFalse(self.token in response.get_data(as_text=True))
            self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(captured, [False])
        self.assertFalse(self.persisted().get('report_json'))

    def test_exp_token_cannot_authenticate_other_rpc_crud_or_version_aliases(self):
        for path in ('/', '/login', '/api/method/frappe.auth.get_logged_user', '/api/resource/User',
                     '/api/v2/document/User', '/api/v1/method/' + METHOD,
                     '/api/v2/method/' + METHOD, PATH + '/', PATH + '/ignored'):
            with self.subTest(path=path):
                self.assertIn(self.request(path).status_code, (401, 403))
        for method in ('GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE'):
            with self.subTest(method=method): self.assertIn(self.request(method=method).status_code, (401, 403))

    def test_cmd_in_json_query_or_form_does_not_override_route(self):
        for payload in ({'cmd': 'frappe.auth.get_logged_user'}, {'cmd': METHOD}, {'cmd': ''}):
            self.assertIn(self.request(body=payload).status_code, (401, 403))
        self.assertIn(self.request(PATH + '?cmd=frappe.auth.get_logged_user').status_code, (401, 403))
        self.assertIn(self.request(raw='cmd=frappe.auth.get_logged_user', content_type='application/x-www-form-urlencoded').status_code, (401, 403))
        self.assertFalse(self.persisted().get('report_json'))

    def test_cmd_login_is_denied_before_authentication_and_session_creation(self):
        from frappe.auth import LoginManager
        baseline = self.db(lambda: frappe.db.count('Sessions'))
        with patch.object(LoginManager, 'authenticate', side_effect=AssertionError('must stop before password authentication')) as login:
            for path in (PATH, '/api/method/login'):
                response = self.request(path, body={'cmd': 'login', 'usr': self.manager, 'pwd': 'synthetic-unusable'})
                self.assertIn(response.status_code, (401, 403))
        self.assertEqual(login.call_count, 0)
        self.assertEqual(self.db(lambda: frappe.db.count('Sessions')), baseline)

    def test_existing_cookie_and_body_sid_cannot_be_rebound_or_modified(self):
        from frappe.sessions import Session
        def make_session():
            frappe.local.request = Request(EnvironBuilder(base_url=self.base).get_environ())
            frappe.local.request_ip = '127.0.0.1'; frappe.local.form_dict = frappe._dict()
            value = Session(user=self.manager, resume=False)
            return value.sid
        sid = self.db(make_session, commit=True)
        before = self.db(lambda: (frappe.db.sql('SELECT user,sessiondata,lastupdate FROM `tabSessions` WHERE sid=%s', (sid,)),
                                 frappe.cache.hget('session', sid)))
        self.assertEqual(len(before[0]), 1)
        self.assertEqual(before[0][0][0], self.manager)
        headers = {'Authorization': 'Bearer ' + self.token, 'Cookie': 'sid=' + sid}
        self.assertIn(self.request(headers=headers).status_code, (401, 403))
        self.assertIn(self.request(body={'sid': sid}).status_code, (401, 403))
        self.assertIn(self.request(PATH + '?sid=' + sid).status_code, (401, 403))
        after = self.db(lambda: (frappe.db.sql('SELECT user,sessiondata,lastupdate FROM `tabSessions` WHERE sid=%s', (sid,)),
                                frappe.cache.hget('session', sid)))
        self.assertEqual(before, after)

    def test_human_cookie_api_key_body_token_and_header_variants_never_grant_machine_capability(self):
        for headers in ({}, self.human_headers, {'Authorization': 'Bearer  ' + self.token},
                        {'Authorization': 'token ' + self.token},
                        {'Authorization': 'Bearer ' + self.token, 'Frappe-Authorization-Source': 'User'}):
            self.assertIn(self.request(headers=headers, body={'token': self.token}).status_code, (401, 403))
        self.assertFalse(self.persisted().get('report_json'))

    def test_non_object_or_malformed_json_is_rejected_before_persistence(self):
        for raw in ('[]', 'null', '"text"', '{invalid'):
            response = self.request(raw=raw)
            self.assertIn(response.status_code, (400, 401, 403, 417))
        self.assertFalse(self.persisted().get('report_json'))

    def test_plain_human_rpc_cannot_reuse_a_previous_machine_request_context(self):
        self.report(self.body())
        denied = self.request(headers=self.human_headers, body={'offline': True})
        self.assertIn(denied.status_code, (401, 403))
        # 报到时间记在密钥行：人的会话冒用不能把它清成下线。
        self.assertIsNotNone(self.db(lambda: frappe.db.get_value('Cowork Express Agent Credential',
                                                                 self.issued['credential_id'], 'last_seen_at')))

    def test_authority_is_revalidated_after_hook_and_before_writes(self):
        real = self.heartbeat._receive
        calls = []
        def revoke_role(context, body):
            calls.append(context.credential_id)
            frappe.db.delete('Has Role', {'parent': context.issuer, 'role': 'Cowork Admin'})
            return real(context, body)
        with patch.object(self.heartbeat, '_receive', side_effect=revoke_role):
            with patch.object(self.collector, 'ingest', wraps=self.collector.ingest) as ingest:
                self.assertIn(self.request(body=self.body()).status_code, (401, 403))
                self.assertEqual(ingest.call_count, 0)
        self.assertEqual(calls, [self.issued['credential_id']])
        self.assertFalse(self.persisted().get('report_json'))

    def test_rotation_between_hook_and_write_rejects_old_generation(self):
        real = self.heartbeat._receive; calls = []
        def rotate(context, body):
            rotated = self.credentials.issue(context.connection, uuid4().hex, reset=True,
                expected_generation=context.token_generation)
            calls.append(rotated['generation'])
            return real(context, body)
        with patch.object(self.heartbeat, '_receive', side_effect=rotate):
            with patch.object(self.collector, 'ingest', wraps=self.collector.ingest) as ingest:
                self.assertIn(self.request(body=self.body()).status_code, (401, 403))
                self.assertEqual(ingest.call_count, 0)
        self.assertEqual(calls, [2])
        self.assertFalse(self.persisted().get('report_json'))

    def test_initial_authentication_retries_one_local_deadlock_before_binding_identity(self):
        real = self.credentials.authenticate; attempts = []
        def initial_conflict(token):
            self.assertFalse(getattr(frappe.local, 'cowork_companion_http_context', None))
            attempts.append(True)
            if len(attempts) == 1: raise frappe.QueryDeadlockError('synthetic initial lock conflict')
            return real(token)
        with patch.object(self.credentials, 'authenticate', side_effect=initial_conflict):
            with patch.object(self.collector, 'ingest', wraps=self.collector.ingest) as ingest:
                self.report(self.body())
                self.assertEqual(ingest.call_count, 1)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.facts()['reported_accounts'][0]['code'], '4000')  # 这一次上报落了库

    def test_initial_authentication_deadlock_exhaustion_never_reaches_report(self):
        from frappe.utils import error
        with patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
            with patch.object(self.credentials, 'authenticate', side_effect=frappe.QueryDeadlockError('synthetic exhaustion')) as auth:
                with patch.object(self.collector, 'ingest', wraps=self.collector.ingest) as ingest:
                    response = self.request(body=self.body())
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(auth.call_count, ATTEMPTS)
                    self.assertEqual(ingest.call_count, 0)
                    self.assertEqual(snapshot.call_count, 0)
        self.assertFalse(self.persisted().get('report_json'))

    def test_unexpected_initial_auth_failure_is_visible_without_secret_snapshot_or_retry(self):
        from frappe.utils import error
        logger = self.db(lambda: frappe.logger('cowork_companion', allow_site=True))
        with patch.object(logger, 'warning', wraps=logger.warning) as diagnostic:
            with patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
                with patch.object(self.credentials, 'authenticate', side_effect=RuntimeError(self.token)) as auth:
                    response = self.request(body=self.body())
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(auth.call_count, 1)
                    self.assertEqual(snapshot.call_count, 0)
                    self.assertFalse(self.token in response.get_data(as_text=True))
        self.assertEqual(diagnostic.call_count, 1)
        self.assertEqual(diagnostic.call_args.args,
            ('companion.report_busy stage=%s exception_type=%s', 'authenticate', 'RuntimeError'))
        self.assertFalse(self.persisted().get('report_json'))

    def test_identity_binding_failure_is_safe_without_native_error_snapshot(self):
        from frappe.utils import error
        with patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
            with patch('frappe.set_user', side_effect=RuntimeError(self.token)) as bind:
                response = self.request(body=self.body())
                self.assertEqual(response.status_code, 503)
                self.assertEqual(bind.call_count, 1)
                self.assertEqual(snapshot.call_count, 0)
                self.assertFalse(self.token in response.get_data(as_text=True))
        self.assertFalse(self.persisted().get('report_json'))

    def test_disabled_global_gate_in_developer_mode_is_safe_403_without_snapshot(self):
        from frappe.utils import error
        config = json.loads(self.config_path.read_text())
        config['developer_mode'] = True
        config['pearnly_cowork_erp_push_enabled'] = False
        self.config_path.write_text(json.dumps(config))
        clear_site_config_cache()
        with patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
            with patch.object(self.credentials, 'authenticate', wraps=self.credentials.authenticate) as auth:
                with patch.object(self.collector, 'ingest', wraps=self.collector.ingest) as ingest:
                    response = self.request(body=self.body())
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(auth.call_count, 1)
                    self.assertEqual(ingest.call_count, 0)
                    self.assertEqual(snapshot.call_count, 0)
                    self.assertFalse(self.token in response.get_data(as_text=True))

    def test_deep_valid_json_handoff_failure_does_not_snapshot_raw_token(self):
        from frappe.utils import error
        # json.loads accepts this depth; deepcopy traverses dicts recursively
        # twice per level and fails under the real candidate recursion limit.
        raw = '{"nested":' * 550 + '0' + '}' * 550
        self.assertIsInstance(json.loads(raw), dict)
        logger = self.db(lambda: frappe.logger('cowork_companion', allow_site=True))
        with patch.object(logger, 'warning', wraps=logger.warning) as diagnostic:
            with patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
                with patch.object(self.credentials, 'authenticate', wraps=self.credentials.authenticate) as auth:
                    response = self.request(raw=raw)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(auth.call_count, 0)
                    self.assertEqual(snapshot.call_count, 0)
                    self.assertFalse(self.token in response.get_data(as_text=True))
        self.assertEqual(diagnostic.call_args.args,
            ('companion.report_busy stage=%s exception_type=%s', 'envelope', 'RecursionError'))

    def test_report_deadlock_exhaustion_is_retryable_without_raw_error_snapshot(self):
        from frappe.utils import error
        with patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
            with patch.object(self.heartbeat, '_receive', side_effect=frappe.QueryDeadlockError('synthetic report exhaustion')) as receive:
                response = self.request(body=self.body())
                self.assertEqual(response.status_code, 503)
                self.assertEqual(receive.call_count, ATTEMPTS)
                self.assertEqual(snapshot.call_count, 0)
        self.assertFalse(self.persisted().get('report_json'))

    def test_initial_authentication_retry_checks_current_role_and_does_not_retry_denial(self):
        real = self.credentials.authenticate; attempts = []
        def revoke_on_retry(token):
            self.assertFalse(getattr(frappe.local, 'cowork_companion_http_context', None))
            attempts.append(True)
            if len(attempts) == 1:
                real(token)  # A valid result before the conflict must be discarded.
                raise frappe.QueryDeadlockError('synthetic conflict after current read')
            frappe.db.delete('Has Role', {'parent': self.manager, 'role': 'Cowork Admin'})
            return real(token)
        with patch.object(self.credentials, 'authenticate', side_effect=revoke_on_retry):
            with patch.object(self.collector, 'ingest', wraps=self.collector.ingest) as ingest:
                self.assertIn(self.request(body=self.body()).status_code, (401, 403))
                self.assertEqual(ingest.call_count, 0)
        self.assertEqual(len(attempts), 2)
        self.assertFalse(self.persisted().get('report_json'))

    def test_reporting_exception_rolls_back_local_rows_without_partial_success(self):
        real = self.collector.ingest; attempted = []
        def fail_after_write(*args, **kwargs):
            real(*args, **kwargs); attempted.append(True)
            raise self.heartbeat.model.ConnectionError('connection.snapshot_invalid')
        with patch.object(self.collector, 'ingest', side_effect=fail_after_write):
            response = self.request(body=self.body())
        self.assertEqual(attempted, [True])
        self.assertEqual(response.status_code, 417)
        self.assertFalse(self.persisted().get('report_json'))

    def test_only_local_deadlocks_retry_and_completed_request_has_one_visible_report(self):
        real = self.heartbeat._receive; attempts = []
        def retry_once(context, body):
            result = real(context, body); attempts.append(True)
            if len(attempts) == 1: raise frappe.QueryDeadlockError('synthetic local failure')
            return result
        with patch.object(self.heartbeat, '_receive', side_effect=retry_once): self.report(self.body())
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.facts()['reported_accounts'][0]['code'], '4000')  # 这一次上报落了库
        with patch.object(self.heartbeat, '_receive', side_effect=frappe.PermissionError('synthetic denied')) as denied:
            self.assertEqual(self.request(body=self.body()).status_code, 403)
            self.assertEqual(denied.call_count, 1)

    def test_override_and_server_script_cannot_inherit_machine_identity(self):
        from frappe.core.doctype.server_script import server_script_utils
        with patch('frappe.override_whitelisted_method', return_value='frappe.auth.get_logged_user'):
            self.assertIn(self.request().status_code, (401, 403))
        actual = server_script_utils.get_server_script_map
        with patch.object(server_script_utils, 'get_server_script_map', return_value={'_api': {METHOD: 'synthetic-script'}}):
            self.assertIn(self.request().status_code, (401, 403))
        self.assertFalse(self.persisted().get('report_json'))

    def test_machine_requests_opt_out_of_recorder_but_other_requests_still_record(self):
        from frappe import recorder
        saved = self.db(lambda: (frappe.client_cache.get_value(recorder.RECORDER_INTERCEPT_FLAG),
                                frappe.cache.get_value(recorder.RECORDER_CONFIG_FLAG)))
        def enable():
            recorder.RecorderConfig(record_requests=1, record_jobs=0, record_sql=1,
                profile=0, capture_stack=0, capture_doc_events=0, explain=0,
                request_filter='/').store()
            frappe.client_cache.set_value(recorder.RECORDER_INTERCEPT_FLAG, True)
            return set(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))
        baseline = self.db(enable)
        try:
            self.report(self.body(device='synthetic-private-recorder-canary'))
            self.assertIn(self.request('/api/method/frappe.auth.get_logged_user').status_code, (401, 403))
            self.assertIn(self.request(raw='{bad-json').status_code, (400, 417))
            self.assertEqual(self.db(lambda: set(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))), baseline)
            ordinary = self.request('/api/method/frappe.ping', method='GET', headers={})
            self.assertEqual(ordinary.status_code, 200)
            recorded = self.db(lambda: frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))
            new = {key: value for key, value in recorded.items() if key not in baseline}
            self.assertTrue(new)
            text = json.dumps(list(new.values()), default=str)
            self.assertNotIn(self.token, text)
            self.assertNotIn('synthetic-private-recorder-canary', text)
        finally:
            def restore():
                if saved[0] is None: frappe.client_cache.delete_value(recorder.RECORDER_INTERCEPT_FLAG)
                else: frappe.client_cache.set_value(recorder.RECORDER_INTERCEPT_FLAG, saved[0])
                if saved[1] is None: frappe.cache.delete_value(recorder.RECORDER_CONFIG_FLAG)
                else: frappe.cache.set_value(recorder.RECORDER_CONFIG_FLAG, saved[1])
            self.db(restore)


if __name__ == '__main__':
    connect()
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--test', action='append', help='Run one named test method (repeatable)')
        args = parser.parse_args()
        if args.test and any(not name.startswith('test_') for name in args.test): parser.error('Expected a test_ method')
        suite = (unittest.TestSuite(NativeHeartbeat(name) for name in args.test) if args.test
                 else unittest.defaultTestLoader.loadTestsFromTestCase(NativeHeartbeat))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
