"""Real WSGI machine queue transactions on the marked, internal lab only.

Every test owns new synthetic people, workspace, endpoint and source data.
No listener, external ERP, Windows process or real notification is started.
Committed fixtures remain labelled on this disposable lab Site; config is restored.
"""
import argparse
from copy import deepcopy
import csv
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from werkzeug.test import Client
from werkzeug.wrappers import Response
from pearnly_cowork.transaction.conflicts import ATTEMPTS  # 共用的整笔重来（10-20 起 4 次、带退避）

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-companion-transport-lab-verify.py')))
BASE = FIXTURE['CompanionTransport']
connect = FIXTURE['connect']
METHODS = {'heartbeat': 'pearnly_cowork.companion.heartbeat.report',
           'lease': 'pearnly_cowork.companion.queue_api.lease',
           'ack': 'pearnly_cowork.companion.queue_api.acknowledge'}
PATHS = {operation: '/api/method/' + method for operation, method in METHODS.items()}


class NativeQueueHttp(unittest.TestCase):
    prepare, prepared, install = BASE.prepare, BASE.prepared, BASE.install
    availability, another_history = BASE.availability, BASE.another_history
    # D11 起 transport 夹具的 setUp 先经真实心跳报到账套、再记付款方式与科目（`report` / `choose`），这里一并借来
    report, choose = BASE.report, BASE.choose

    @classmethod
    def setUpClass(cls):
        # Individual tests commit permission mutations: never share identities.
        pass

    def setUp(self):
        from frappe.app import application
        from pearnly_cowork.companion import http_auth, queue_api
        self.http_auth, self.queue_api = http_auth, queue_api
        self.site = frappe.local.site
        self.base = 'http://' + self.site
        saved_conf = deepcopy(dict(frappe.conf))
        self.config_path = Path(frappe.get_site_path('site_config.json'))
        self.original_config = self.config_path.read_text()
        self.change_config(pearnly_cowork_erp_push_enabled=True,
                           pearnly_features=['intake', 'master_data', 'push_log'])
        frappe.conf.pearnly_features = ['intake', 'master_data', 'push_log']
        # Framework import mode applies only to synthetic fixture creation, not
        # to any WSGI request or authentication/authorization under test.
        with patch.dict(frappe.flags, {'in_import': True, 'in_install': True}):
            BASE.setUpClass.__func__(self.__class__)
        BASE.setUp(self)
        self.old_conf = saved_conf
        self.token = self.issued['token']
        frappe.set_user('Administrator')
        manager = frappe.get_doc('User', self.manager)
        manager.api_key, api_secret = uuid4().hex, uuid4().hex
        manager.api_secret = api_secret
        with patch.dict(frappe.flags, {'in_install': True}): manager.save()
        self.human_headers = {'Authorization': f'token {manager.api_key}:{api_secret}'}
        frappe.db.commit(); frappe.destroy()
        self.client = Client(application, Response, use_cookies=False)

    def tearDown(self):
        self.config_path.write_text(self.original_config)
        from frappe.config import clear_site_config_cache
        clear_site_config_cache()
        if getattr(frappe.local, 'db', None): frappe.db.rollback(); frappe.destroy()
        connect(self.site)
        frappe.conf.clear(); frappe.conf.update(self.old_conf)
        frappe.set_user('Administrator'); frappe.clear_cache()

    def change_config(self, **updates):
        config = json.loads(self.config_path.read_text())
        if not config.get('pearnly_backend_lab'): raise RuntimeError('Marked lab Site required')
        config.update(updates)
        self.config_path.write_text(json.dumps(config))
        # WSGI uses Frappe's 60s process cache, unlike direct bench init.
        # Invalidate the test process cache so these requests exercise the
        # intended switch/developer-mode settings immediately.
        from frappe.config import clear_site_config_cache
        clear_site_config_cache()

    def db(self, function, *, commit=False):
        if getattr(frappe.local, 'db', None): frappe.db.rollback(); frappe.destroy()
        connect(self.site); frappe.set_user(self.manager)
        try:
            value = function()
            if commit: frappe.db.commit()
            return value
        finally:
            frappe.db.rollback(); frappe.destroy()

    def request(self, operation='lease', *, path=None, method='POST', body=None,
                headers=None, raw=None, content_type=None):
        path = path or PATHS[operation]
        arguments = {'base_url': self.base, 'method': method, 'buffered': True,
            'headers': {'Authorization': 'Bearer ' + self.token} if headers is None else headers}
        if raw is not None:
            arguments.update(data=raw, content_type=content_type or 'application/json')
        elif method not in ('GET', 'HEAD', 'OPTIONS'):
            arguments['json'] = {} if body is None else body
        response = self.client.open(path, **arguments)
        if 'exp_' in arguments['headers'].get('Authorization', '') or any(m in path for m in METHODS.values()):
            self.assertEqual(response.headers.getlist('Set-Cookie'), [])
            self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        self.assertNotIn(self.token, response.get_data(as_text=True))
        return response

    def message(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn('message', response.json)
        self.assertNotIn('jobs', response.json)
        self.assertNotIn('ok', response.json)
        return response.json['message']

    def pending(self):
        def create():
            frappe.set_user(self.actor)
            return BASE.pending(self)
        return self.db(create, commit=True)

    def claimed(self):
        name = self.pending()
        jobs = self.message(self.request())['jobs']
        self.assertEqual(len(jobs), 1)
        return name, jobs[0]['log_id']

    def receipt(self, handle, *, token=None, **fields):
        return self.request('ack', headers={'Authorization': 'Bearer ' + (token or self.token)},
            body={'log_id': handle, 'result': 'success', 'express_docnum': 'SYNTHETIC-HTTP-001', **fields})

    def state(self, name):
        return self.db(lambda: frappe.get_doc(self.native.RECORD, name).as_dict())

    def test_lease_is_committed_before_wsgi_exposes_frozen_job(self):
        from frappe.database.database import Database
        name = self.pending()
        events = []; real_commit = Database.commit
        def commit(db, **kwargs):
            result = real_commit(db, **kwargs); events.append('committed'); return result
        def application(environ, start_response):
            from frappe.app import application as real_application
            def begin(status, headers, exc_info=None):
                self.assertIn('committed', events)
                events.append('response')
                return start_response(status, headers, exc_info)
            return real_application(environ, begin)
        self.client = Client(application, Response, use_cookies=False)
        with patch.object(Database, 'commit', commit): result = self.message(self.request())
        self.assertEqual(events[-1], 'response')
        self.assertEqual(set(result), {'ok', 'jobs', 'lease_seconds'})
        self.assertEqual(result['lease_seconds'], 120)
        job = result['jobs'][0]; row = self.state(name)
        # 领取 v2 多给 `attended`（同 transport 夹具，D11）
        self.assertEqual(set(job), {'log_id', 'history_id', 'invoice_no', 'payload', 'attended'})
        self.assertEqual(job['payload'], json.loads(row['payload_json']))
        self.assertEqual(row['status'], 'leased')
        self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name})), 1)
        secret = self.db(lambda: self.deliveries.secret(self.deliveries.get(job['log_id'])))
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(self.message(self.request())['jobs'], [])

    def test_poll_does_not_activate_prepared_or_accept_body_scope(self):
        def create():
            frappe.set_user(self.actor)
            return self.prepared()[0]
        name = self.db(create, commit=True)
        value = self.message(self.request(body={'max': 50, 'agent_id': 'forged-owner',
            'connection': self.connection['name'], 'record': name, 'workspace': self.foreign}))
        self.assertEqual(value['jobs'], [])
        self.assertEqual(self.state(name)['status'], 'prepared')
        self.assertEqual(self.db(lambda: frappe.db.count(self.native.ATTEMPT, {'push_record': name})), 0)

    def test_invalid_queue_contract_is_400_before_authentication_or_writes(self):
        name = self.pending()
        with patch.object(self.credentials, 'authenticate', wraps=self.credentials.authenticate) as authenticate:
            for maximum in (0, 51, '1', True, 1.0, None):
                with self.subTest(maximum=maximum):
                    self.assertEqual(self.request(body={'max': maximum}).status_code, 400)
            self.assertEqual(authenticate.call_count, 0)
        self.assertEqual(self.state(name)['status'], 'pending')

    def test_exact_queue_routes_reject_aliases_verbs_humans_and_cmd(self):
        for operation in ('lease', 'ack'):
            valid = {} if operation == 'lease' else {'log_id': str(uuid4()), 'result': 'success'}
            for path in ('/api/v1/method/' + METHODS[operation], '/api/v2/method/' + METHODS[operation],
                         PATHS[operation] + '/', PATHS[operation] + '/ignored'):
                with self.subTest(path=path): self.assertIn(self.request(path=path, body=valid).status_code, (401, 403))
            for method in ('GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE'):
                with self.subTest(method=method): self.assertIn(self.request(operation, method=method).status_code, (401, 403))
            for headers in ({}, self.human_headers, {'Authorization': 'Bearer ' + self.token, 'Cookie': 'sid=Guest'}):
                self.assertIn(self.request(operation, headers=headers, body=valid).status_code, (401, 403))
            for key in ('cmd', 'sid'):
                self.assertIn(self.request(operation, body={**valid, key: ''}).status_code, (401, 403))
                self.assertIn(self.request(path=PATHS[operation] + '?' + key + '=', body=valid).status_code, (401, 403))

    def test_rpc_cannot_reuse_sibling_capability_or_direct_administrator(self):
        real = self.transport.lease
        def inspect(context, body):
            for operation in ('ack', 'heartbeat'):
                with self.assertRaises(frappe.AuthenticationError): self.http_auth.current_request(operation)
            for function in (self.queue_api.acknowledge,):
                with self.assertRaises(frappe.AuthenticationError): function()
            return real(context, body)
        with patch.object(self.transport, 'lease', side_effect=inspect): self.message(self.request())
        def direct():
            frappe.set_user('Administrator')
            for function in (self.queue_api.lease, self.queue_api.acknowledge):
                with self.assertRaises(frappe.AuthenticationError): function()
        self.db(direct)

    def test_revoked_between_hook_and_lease_cannot_dispatch(self):
        name = self.pending(); real = self.transport.lease
        def revoke(context, body):
            self.credentials.revoke(context.connection, uuid4().hex, context.token_generation)
            return real(context, body)
        with patch.object(self.transport, 'lease', side_effect=revoke) as called:
            self.assertIn(self.request().status_code, (401, 403))
            self.assertEqual(called.call_count, 1)
        self.assertEqual(self.state(name)['status'], 'pending')
        self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name})), 0)

    def test_local_deadlock_after_staging_rolls_back_then_leases_once(self):
        name = self.pending(); real = self.transport.lease; staged = []
        def retry(context, body):
            result = real(context, body); staged.append(result['jobs'][0]['log_id'])
            if len(staged) == 1: raise frappe.QueryDeadlockError('synthetic local contention')
            return result
        with patch.object(self.transport, 'lease', side_effect=retry): result = self.message(self.request())
        self.assertEqual(len(staged), 2); self.assertNotEqual(*staged)
        self.assertEqual(result['jobs'][0]['log_id'], staged[1])
        self.assertEqual(self.db(lambda: frappe.db.count(self.native.ATTEMPT, {'push_record': name})), 1)
        self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name})), 1)

    def test_deadlock_retry_rechecks_credential_and_is_bounded(self):
        name = self.pending()
        with patch.object(self.transport, 'lease', side_effect=frappe.QueryDeadlockError('synthetic contention')) as called:
            self.assertEqual(self.request().status_code, 503); self.assertEqual(called.call_count, ATTEMPTS)
        self.assertEqual(self.state(name)['status'], 'pending')
        calls = []
        def revalidate(context):
            calls.append(True)
            if len(calls) == 1: raise frappe.QueryDeadlockError('synthetic validation contention')
            raise frappe.AuthenticationError('synthetic revoked')
        with patch.object(self.credentials, 'revalidate', side_effect=revalidate):
            self.assertIn(self.request().status_code, (401, 403))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state(name)['status'], 'pending')

    def test_commit_failure_before_sql_never_returns_job_and_does_not_retry(self):
        from frappe.database.database import Database
        name = self.pending(); real = Database.commit; calls = []
        def commit(db, **kwargs):
            calls.append(True)
            if len(calls) == 1: raise RuntimeError('synthetic pre-commit failure')
            return real(db, **kwargs)
        with patch.object(Database, 'commit', commit):
            with patch.object(self.transport, 'lease', wraps=self.transport.lease) as take:
                response = self.request()
                self.assertEqual(response.status_code, 503); self.assertEqual(take.call_count, 1)
        self.assertNotIn('jobs', response.get_data(as_text=True))
        self.assertEqual(self.state(name)['status'], 'pending')
        self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name})), 0)

    def test_after_commit_failure_keeps_unknown_delivery_and_never_reissues(self):
        name = self.pending(); real = self.transport.lease
        def stage(context, body):
            value = real(context, body)
            def fail(): raise RuntimeError('synthetic after-commit failure')
            frappe.db.after_commit.add(fail)
            return value
        with patch.object(self.transport, 'lease', side_effect=stage) as take:
            response = self.request()
            self.assertEqual(response.status_code, 503); self.assertEqual(take.call_count, 1)
        self.assertNotIn('jobs', response.get_data(as_text=True))
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.message(self.request())['jobs'], [])
        self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name})), 1)

    def test_outer_framework_commit_failure_does_not_expose_success(self):
        from frappe.database.database import Database
        name = self.pending(); real = Database.commit; calls = []
        def commit(db, **kwargs):
            calls.append(True)
            if len(calls) == 2: raise RuntimeError('synthetic framework second commit failure')
            return real(db, **kwargs)
        with patch.object(Database, 'commit', commit):
            with self.assertRaisesRegex(RuntimeError, 'synthetic framework second commit failure'):
                self.request()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.message(self.request())['jobs'], [])

    def test_permission_validation_and_unexpected_failures_are_private(self):
        from frappe.utils import error
        name = self.pending()
        for exception, status in ((frappe.PermissionError('synthetic denied'), 403),
            (frappe.ValidationError('synthetic invalid'), 409), (RuntimeError('synthetic unavailable'), 503)):
            with self.subTest(status=status), patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
                with patch.object(self.transport, 'lease', side_effect=exception) as take:
                    response = self.request(); self.assertEqual(response.status_code, status)
                    self.assertEqual(take.call_count, 1)
                self.assertEqual(snapshot.call_count, 0)
                self.assertNotIn('synthetic', response.get_data(as_text=True))
        self.assertEqual(self.state(name)['status'], 'pending')

    def test_all_three_methods_refuse_override_or_server_script(self):
        from frappe.core.doctype.server_script import server_script_utils
        for operation in METHODS:
            body = {'log_id': str(uuid4()), 'result': 'success'} if operation == 'ack' else {}
            with patch('frappe.override_whitelisted_method', return_value='frappe.auth.get_logged_user'):
                self.assertIn(self.request(operation, body=body).status_code, (401, 403))
            with patch.object(server_script_utils, 'get_server_script_map', return_value={'_api': {METHODS[operation]: 'synthetic'}}):
                self.assertIn(self.request(operation, body=body).status_code, (401, 403))

    def test_actual_four_language_conflict_translation(self):
        def verify():
            frappe.clear_cache()
            for language in ('en', 'zh', 'th', 'ja'):
                with self.subTest(language=language):
                    path = Path(frappe.get_app_path('pearnly_cowork', 'translations', language + '.csv'))
                    table = dict(csv.reader(path.open()))
                    self.assertEqual(frappe._('companion.queue_conflict', lang=language), table['companion.queue_conflict'])
        self.db(verify)

    def test_authentication_retry_discards_native_messages_for_each_operation(self):
        _, handle = self.claimed()
        for operation in ('lease', 'heartbeat', 'ack'):
            source = self.receipts if operation == 'ack' else self.credentials
            real = source.authenticate; calls = []
            def retry(*args):
                result = real(*args); calls.append(True)
                if len(calls) == 1:
                    frappe.flags.error_message = self.token
                    frappe.throw(self.token, frappe.QueryDeadlockError)
                return result
            with self.subTest(operation=operation), patch.object(source, 'authenticate', side_effect=retry):
                body = {'log_id': handle, 'result': 'success'} if operation == 'ack' else {}
                response = self.request(operation, body=body)
                self.message(response)
                self.assertEqual(len(calls), 2)
                self.assertNotIn('_server_messages', response.json)
                self.assertNotIn('_error_message', response.json)

    def test_two_http_processes_with_old_snapshots_get_only_one_job(self):
        name = self.pending()
        workers = [subprocess.Popen([sys.executable, __file__, '--lease-worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for worker in workers:
                # Secrets only in local pipes, never arguments or output.
                worker.stdin.write(json.dumps({'token': self.token}) + '\n'); worker.stdin.flush()
            for worker in workers: self.assertEqual(worker.stdout.readline().strip(), 'READY')
            for worker in workers: worker.stdin.write('go\n'); worker.stdin.flush()
            outcomes = []
            for worker in workers:
                output, error = worker.communicate(timeout=40)
                self.assertEqual(worker.returncode, 0, error)
                outcomes.append(json.loads(output))
            self.assertEqual(sum(result['jobs'] for result in outcomes), 1)
            self.assertEqual(self.state(name)['status'], 'leased')
            self.assertEqual(self.db(lambda: frappe.db.count(self.native.ATTEMPT, {'push_record': name})), 1)
            self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name})), 1)
            # 写者锁口径 v2：合成登记没有内容键，只有账套锁一把（同 transport 夹具，D11）
            self.assertEqual(self.db(lambda: frappe.db.count(self.native.WRITER_LOCK, {'holder': name})), 1)
        finally:
            for worker in workers:
                if worker.poll() is None: worker.kill(); worker.communicate()


def lease_worker():
    from frappe.app import application
    from pearnly_cowork.companion import credentials
    data = json.loads(sys.stdin.readline()); site = frappe.local.site
    original = credentials.authenticate; first = True
    def authenticate(token):
        nonlocal first
        if first:
            first = False
            frappe.db.count('Cowork Push Record')
            print('READY', flush=True)
            if sys.stdin.readline().strip() != 'go': raise AssertionError('Missing concurrency barrier')
        return original(token)
    frappe.destroy()
    try:
        with patch.object(credentials, 'authenticate', authenticate):
            response = Client(application, Response, use_cookies=False).post(PATHS['lease'],
                base_url='http://' + site, headers={'Authorization': 'Bearer ' + data['token']},
                json={}, buffered=True)
        if response.status_code != 200: raise AssertionError('Machine request returned ' + str(response.status_code))
        print(json.dumps({'jobs': len(response.json['message']['jobs'])}), flush=True)
    finally:
        connect(site)


if __name__ == '__main__':
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--lease-worker':
            lease_worker()
        else:
            parser = argparse.ArgumentParser()
            parser.add_argument('--test', action='append')
            args = parser.parse_args()
            if args.test and any(not name.startswith('test_') for name in args.test): parser.error('Expected test_ method')
            suite = (unittest.TestSuite(NativeQueueHttp(name) for name in args.test) if args.test
                     else unittest.defaultTestLoader.loadTestsFromTestCase(NativeQueueHttp))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
