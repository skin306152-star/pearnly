"""Real ACK WSGI tests on one marked local Cowork lab Site; zero external IO.

Reuses only the queue HTTP fixture helpers, not its test class. Writes/commits
unique synthetic users, work and receipts; restores Site configuration and
Recorder switches. Run serially with the other queue HTTP suite. Never prints
bearer credentials, decrypted attempt secrets, payloads or response bodies.
"""
from contextlib import contextmanager
from datetime import timedelta
import argparse
import json
import logging
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from pearnly_core.transaction.conflicts import ATTEMPTS  # 共用的整笔重来（10-20 起 4 次、带退避）
HELD = 1  # 一张领走的单持有的写者锁（见 `locks`）

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-companion-queue-http-lab-verify.py')))
BASE = FIXTURE['NativeQueueHttp']
connect, PATHS = FIXTURE['connect'], FIXTURE['PATHS']


class _Messages(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class NativeQueueAckHttp(unittest.TestCase):
    setUpClass = classmethod(BASE.setUpClass.__func__)
    setUp, tearDown = BASE.setUp, BASE.tearDown
    prepare, prepared, install = BASE.prepare, BASE.prepared, BASE.install
    availability, another_history = BASE.availability, BASE.another_history
    db, request, message = BASE.db, BASE.request, BASE.message
    pending, claimed, state = BASE.pending, BASE.claimed, BASE.state
    change_config, receipt = BASE.change_config, BASE.receipt
    report, choose = BASE.report, BASE.choose  # transport 夹具 setUp 要的（D11 起）

    # 写者锁口径 v2（ADR-021 D6）：Express 新记录只取账套锁，按内容键的公司锁要登记带内容键，合成登记没有，所以一把
    # （原来按整条连接 + 账套两把；transport 夹具 D11 起同样按一把断言）
    def locks(self, name):
        return self.db(lambda: frappe.db.count(self.native.WRITER_LOCK, {'holder': name}))

    def secret(self, handle):
        return self.db(lambda: self.deliveries.secret(self.deliveries.get(handle)))

    def denied(self, response, status):
        self.assertEqual(response.status_code, status)
        body = response.get_data(as_text=True)
        self.assertFalse(self.token in body)
        value = response.get_json(silent=True) or {}
        self.assertFalse(value.get('message', {}).get('ok') if isinstance(value.get('message'), dict) else False)
        self.assertNotIn('jobs', value)

    @contextmanager
    def no_secret_diagnostics(self, secret):
        from frappe.utils import error
        logger = self.db(lambda: frappe.logger('cowork_companion', allow_site=True))
        capture = _Messages()
        logger.addHandler(capture)
        try:
            with patch.object(error, 'log_error', wraps=error.log_error) as snapshot:
                yield
                self.assertEqual(snapshot.call_count, 0)
            diagnostic = '\n'.join(capture.messages)
            self.assertFalse(self.token in diagnostic)
            self.assertFalse(secret in diagnostic)
        finally:
            logger.removeHandler(capture)

    def test_success_duplicate_and_late_failure_preserve_first_receipt(self):
        name, handle = self.claimed()
        first = self.message(self.receipt(handle, meta={'stage': 'failed', 'companion_version': 'synthetic'},
            line_modes=[{'mode': 'direct', 'reason': 'synthetic fallback', 'stkcod': 'SYN-HTTP-STOCK'}]))
        self.assertEqual(first, {'ok': True, 'status': 'success', 'express_docnum': 'SYNTHETIC-HTTP-001'})
        saved = self.state(name)
        self.assertEqual(json.loads(saved['response_json'])['meta']['stage'], 'success')
        self.assertEqual(self.locks(name), 0)
        for fields in ({}, {'result': 'failed', 'outcome': 'rolled_back', 'error': 'late callback'}):
            with self.subTest(kind='duplicate' if not fields else 'late-failure'):
                self.assertEqual(self.message(self.receipt(handle, **fields)),
                    {'ok': True, 'status': 'success', 'idempotent': True})
                current = self.state(name)
                self.assertEqual(current['response_json'], saved['response_json'])
                self.assertEqual(current['revision'], saved['revision'])

    def test_waiting_lock_new_fence_rejects_old_handle_with_409(self):
        name, old_handle = self.claimed()
        waiting = self.message(self.receipt(old_handle, result='failed', outcome='waiting_lock'))
        self.assertEqual(waiting, {'ok': True, 'status': 'pending', 'stage': 'waiting_lock', 'retry': True})
        self.assertEqual(self.message(self.receipt(old_handle, result='failed', outcome='waiting_lock')), waiting)
        new_handle = self.message(self.request())['jobs'][0]['log_id']
        self.assertNotEqual(old_handle, new_handle)
        self.assertEqual(self.state(name)['fence'], 2)
        self.denied(self.receipt(old_handle), 409)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.locks(name), HELD)
        self.assertEqual(self.message(self.receipt(new_handle))['status'], 'success')

    def test_definite_failure_exhaustion_and_changed_duplicate_conflict(self):
        name, handle = self.claimed()
        for number in range(1, 4):
            reply = self.message(self.receipt(handle, result='failed', outcome='rolled_back', error='known restored'))
            self.assertEqual(reply['agent_failures'], number)
            self.assertEqual(reply['attempt'], number + 1)
            self.assertEqual(reply['status'], 'manual' if number == 3 else 'pending')
            self.assertEqual(self.message(self.receipt(handle, result='failed', outcome='rolled_back',
                                                     error='known restored')), reply)
            self.denied(self.receipt(handle, result='failed', outcome='rolled_back', error='changed'), 409)
            if number < 3:
                handle = self.message(self.request())['jobs'][0]['log_id']
        self.assertEqual(self.message(self.request())['jobs'], [])
        self.assertEqual(self.state(name)['failure_count'], 3)
        self.assertEqual(self.locks(name), 0)

    def test_unknown_keeps_writers_until_late_exact_receipt(self):
        name, handle = self.claimed()
        expires = self.state(name)['lease_expires_at']
        with patch.object(self.native, 'now_utc', return_value=expires + timedelta(seconds=1)):
            self.assertEqual(self.message(self.request())['jobs'], [])
        row = self.state(name)
        self.assertEqual((row['status'], row['result_unknown']), ('manual', 1))
        self.assertEqual(self.locks(name), HELD)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')
        self.assertEqual(self.locks(name), 0)
        self.assertFalse(self.state(name)['result_unknown'])

    def test_rotated_token_only_old_secret_can_ack_old_delivery(self):
        from pearnly_cowork.companion import credentials
        name, handle = self.claimed()
        new = self.db(lambda: credentials.issue(self.connection['name'], uuid4().hex,
            reset=True, expected_generation=1), commit=True)
        self.denied(self.request(), 401)
        self.denied(self.receipt(handle, token=new['token']), 401)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_revoked_credential_remains_receipt_only(self):
        from pearnly_cowork.companion import credentials
        _, handle = self.claimed()
        self.db(lambda: credentials.revoke(self.connection['name'], uuid4().hex, 1), commit=True)
        self.denied(self.request(), 401)
        self.denied(self.request(operation='heartbeat'), 401)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_disabled_issuer_retains_original_native_ip_restriction(self):
        from frappe import auth
        name, handle = self.claimed()
        def restrict():
            frappe.db.set_value('User', self.manager, {'enabled': 0, 'restrict_ip': '192.0.2.10'},
                                update_modified=False)
            frappe.clear_cache(user=self.manager)
        self.db(restrict, commit=True)
        self.denied(self.request(), 401)
        body = {'log_id': handle, 'result': 'success', 'express_docnum': 'SYNTHETIC-IP'}
        with patch.object(auth, 'validate_ip_address', wraps=auth.validate_ip_address) as ip:
            denied = self.request(operation='ack', body=body, headers={
                'Authorization': 'Bearer ' + self.token, 'X-Forwarded-For': '192.0.2.11'})
            self.denied(denied, 401)
            self.assertIn((self.manager,), [call.args for call in ip.call_args_list])
        self.assertEqual(self.state(name)['status'], 'leased')
        with patch.object(auth, 'validate_ip_address', wraps=auth.validate_ip_address) as ip:
            allowed = self.request(operation='ack', body=body, headers={
                'Authorization': 'Bearer ' + self.token, 'X-Forwarded-For': '192.0.2.10'})
            self.assertEqual(self.message(allowed)['status'], 'success')
            self.assertIn((self.manager,), [call.args for call in ip.call_args_list])

    def test_issuer_role_revocation_only_allows_historical_ack(self):
        _, handle = self.claimed()
        def revoke():
            frappe.db.delete('Has Role', {'parent': self.manager, 'role': 'Cowork Admin'})
            frappe.clear_cache(user=self.manager)
        self.db(revoke, commit=True)
        self.denied(self.request(), 401)
        self.denied(self.request(operation='heartbeat'), 401)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_disabled_company_scope_remains_receipt_only(self):
        _, handle = self.claimed()
        def restrict():
            frappe.db.set_value('Cowork Workspace', self.workspace, 'enabled', 0, update_modified=False)
        self.db(restrict, commit=True)
        self.denied(self.request(), 401)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_disabled_connection_remains_receipt_only(self):
        _, handle = self.claimed()
        self.db(lambda: frappe.db.set_value('Cowork ERP Connection', self.connection['name'],
            'enabled', 0, update_modified=False), commit=True)
        self.denied(self.request(), 401)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_global_off_remains_receipt_only(self):
        _, handle = self.claimed()
        self.change_config(pearnly_cowork_erp_push_enabled=False)
        self.denied(self.request(), 403)
        self.denied(self.request(operation='heartbeat'), 403)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_credential_binding_is_revalidated_between_hook_and_write(self):
        name, handle = self.claimed()
        real = self.receipts.revalidate
        def change_before_revalidation(context):
            frappe.db.set_value('Cowork Express Agent Credential', context.credential_id,
                'token_generation', context.token_generation + 1, update_modified=False)
            return real(context)
        with patch.object(self.receipts, 'revalidate', side_effect=change_before_revalidation) as checked:
            self.denied(self.receipt(handle), 401)
            self.assertEqual(checked.call_count, 1)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.locks(name), HELD)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_delivery_binding_is_revalidated_between_hook_and_write(self):
        name, handle = self.claimed()
        real = self.receipts.revalidate
        def change_before_revalidation(context):
            frappe.db.set_value('Cowork Companion Delivery', context.delivery,
                'fence', context.fence + 1, update_modified=False)
            return real(context)
        with patch.object(self.receipts, 'revalidate', side_effect=change_before_revalidation) as checked:
            self.denied(self.receipt(handle), 401)
            self.assertEqual(checked.call_count, 1)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_current_history_scope_still_rejects_ack(self):
        name, handle = self.claimed()
        self.db(lambda: frappe.db.set_value('Cowork OCR History', self.history['name'],
            'workspace', self.foreign, update_modified=False), commit=True)
        self.denied(self.receipt(handle), 403)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.locks(name), HELD)

    def test_ack_local_deadlock_retries_complete_unit_and_revalidates(self):
        name, handle = self.claimed()
        real = self.transport.acknowledge
        calls = []
        def conflict_after_ack(context, body):
            result = real(context, body)
            calls.append(True)
            if len(calls) == 1:
                raise frappe.QueryDeadlockError('synthetic local ACK conflict')
            return result
        with patch.object(self.receipts, 'revalidate', wraps=self.receipts.revalidate) as checked:
            with patch.object(self.transport, 'acknowledge', side_effect=conflict_after_ack):
                self.assertEqual(self.message(self.receipt(handle))['status'], 'success')
            self.assertEqual(checked.call_count, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state(name)['fence'], 1)
        self.assertEqual(self.db(lambda: frappe.db.count(self.native.ATTEMPT, {'push_record': name})), 1)
        self.assertEqual(self.locks(name), 0)

    def test_deadlock_retry_does_not_reuse_changed_receipt_authority(self):
        name, handle = self.claimed()
        real, calls = self.receipts.revalidate, []
        def retry(context):
            calls.append(True)
            if len(calls) == 1:
                real(context)
                raise frappe.QueryDeadlockError('synthetic receipt conflict')
            frappe.db.set_value('Cowork Companion Delivery', context.delivery,
                'fence', context.fence + 1, update_modified=False)
            return real(context)
        with patch.object(self.receipts, 'revalidate', side_effect=retry):
            self.denied(self.receipt(handle), 401)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.locks(name), HELD)

    def test_ack_deadlock_exhaustion_is_503_without_secret_snapshot(self):
        name, handle = self.claimed()
        secret = self.secret(handle)
        self.change_config(developer_mode=True)
        real, calls = self.transport.acknowledge, []
        def conflict(context, body):
            real(context, body)
            calls.append(True)
            raise frappe.QueryDeadlockError(secret)
        with self.no_secret_diagnostics(secret):
            with patch.object(self.transport, 'acknowledge', side_effect=conflict):
                response = self.receipt(handle)
                self.denied(response, 503)
                self.assertFalse(secret in response.get_data(as_text=True))
        self.assertEqual(len(calls), ATTEMPTS)
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.locks(name), HELD)

    def test_decrypted_attempt_secret_never_enters_error_log_or_recorder(self):
        from frappe import recorder
        name, handle = self.claimed()
        secret = self.secret(handle)
        self.change_config(developer_mode=True)
        saved = self.db(lambda: (frappe.client_cache.get_value(recorder.RECORDER_INTERCEPT_FLAG),
                                frappe.cache.get_value(recorder.RECORDER_CONFIG_FLAG)))
        def enable():
            recorder.RecorderConfig(record_requests=1, record_jobs=0, record_sql=1,
                profile=0, capture_stack=0, capture_doc_events=0, explain=0, request_filter='/').store()
            frappe.client_cache.set_value(recorder.RECORDER_INTERCEPT_FLAG, True)
            return set(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))
        baseline = self.db(enable)
        real_ack = self.native.ack
        try:
            for error_type, expected in ((frappe.PermissionError, 403), (frappe.ValidationError, 409),
                                         (RuntimeError, 503)):
                with self.subTest(error_type=error_type.__name__):
                    reached = []
                    def fail_after_native(*args, **kwargs):
                        self.assertTrue(kwargs['attempt_token'] == secret)
                        real_ack(*args, **kwargs)
                        reached.append(True)
                        raise error_type(secret)
                    with self.no_secret_diagnostics(secret):
                        with patch.object(self.native, 'ack', side_effect=fail_after_native):
                            response = self.receipt(handle)
                            self.denied(response, expected)
                            self.assertFalse(secret in response.get_data(as_text=True))
                    self.assertEqual(reached, [True])
                    self.assertEqual(self.state(name)['status'], 'leased')
                    self.assertEqual(self.db(lambda: set(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))), baseline)
            self.assertEqual(self.message(self.receipt(handle))['status'], 'success')
            self.assertEqual(self.db(lambda: set(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))), baseline)
            ordinary = self.request(path='/api/method/frappe.ping', method='GET', headers={})
            self.assertEqual(ordinary.status_code, 200)
            recorded = self.db(lambda: frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))
            new = {key: value for key, value in recorded.items() if key not in baseline}
            self.assertTrue(new)
            text = json.dumps(list(new.values()), default=str)
            self.assertFalse(secret in text)
            self.assertFalse(self.token in text)
        finally:
            def restore():
                if saved[0] is None: frappe.client_cache.delete_value(recorder.RECORDER_INTERCEPT_FLAG)
                else: frappe.client_cache.set_value(recorder.RECORDER_INTERCEPT_FLAG, saved[0])
                if saved[1] is None: frappe.cache.delete_value(recorder.RECORDER_CONFIG_FLAG)
                else: frappe.cache.set_value(recorder.RECORDER_CONFIG_FLAG, saved[1])
            self.db(restore)

    def test_ack_before_commit_failure_never_reports_success(self):
        name, handle = self.claimed()
        secret = self.secret(handle)
        real, committed = self.transport.acknowledge, []
        def fail_commit():
            committed.append(True)
            raise RuntimeError(secret)
        def register(context, body):
            result = real(context, body)
            frappe.db.before_commit.add(fail_commit)
            return result
        with self.no_secret_diagnostics(secret):
            with patch.object(self.transport, 'acknowledge', side_effect=register) as service:
                response = self.receipt(handle)
                self.denied(response, 503)
                self.assertFalse(secret in response.get_data(as_text=True))
                self.assertEqual(service.call_count, 1)
        self.assertEqual(committed, [True])
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertEqual(self.locks(name), HELD)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_ack_after_commit_failure_keeps_durable_receipt_for_replay(self):
        name, handle = self.claimed()
        secret = self.secret(handle)
        real, committed = self.transport.acknowledge, []
        def fail_commit():
            committed.append(True)
            raise RuntimeError(secret)
        def register(context, body):
            result = real(context, body)
            frappe.db.after_commit.add(fail_commit)
            return result
        with self.no_secret_diagnostics(secret):
            with patch.object(self.transport, 'acknowledge', side_effect=register) as service:
                response = self.receipt(handle)
                self.denied(response, 503)
                self.assertFalse(secret in response.get_data(as_text=True))
                self.assertEqual(service.call_count, 1)
        self.assertEqual(committed, [True])
        self.assertEqual(self.state(name)['status'], 'success')
        self.assertEqual(self.locks(name), 0)
        self.assertEqual(self.message(self.receipt(handle)), {'ok': True, 'status': 'success', 'idempotent': True})

    def test_ack_rollback_failure_stops_retry_without_secret_snapshot(self):
        from frappe.database.database import Database
        name, handle = self.claimed()
        secret = self.secret(handle)
        before = self.state(name)
        self.change_config(developer_mode=True)
        real_ack, real_rollback = self.native.ack, Database.rollback
        decrypted, rollback_failures = [], []
        def conflict_after_decryption(*args, **kwargs):
            self.assertTrue(kwargs['attempt_token'] == secret)
            real_ack(*args, **kwargs)
            decrypted.append(True)
            raise frappe.QueryDeadlockError(secret)
        def rollback(db, *args, **kwargs):
            # Fail only the queue boundary's first explicit recovery attempt (the
            # whole-transaction rollback in `transaction.conflicts.retried`, which
            # `queue_api._run` runs the service through since 10-20).
            # Native savepoint handling and the final app rollback remain real.
            from pearnly_core.transaction import conflicts
            if sys._getframe(1).f_code is conflicts.retried.__code__ and not rollback_failures:
                rollback_failures.append(True)
                raise RuntimeError(secret)
            return real_rollback(db, *args, **kwargs)
        with self.no_secret_diagnostics(secret):
            with patch.object(Database, 'rollback', rollback):
                with patch.object(self.native, 'ack', side_effect=conflict_after_decryption):
                    with patch.object(self.transport, 'acknowledge', wraps=self.transport.acknowledge) as service:
                        response = self.receipt(handle)
                        self.denied(response, 503)
                        self.assertFalse(secret in response.get_data(as_text=True))
                        self.assertEqual(service.call_count, 1)
        self.assertEqual(decrypted, [True])
        self.assertEqual(rollback_failures, [True])
        after = self.state(name)
        for key in ('status', 'fence', 'revision', 'active_attempt', 'response_json', 'result_unknown'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(self.locks(name), HELD)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_native_throw_messages_cannot_expose_decrypted_attempt_secret(self):
        name, handle = self.claimed()
        secret = self.secret(handle)
        self.change_config(developer_mode=True)
        real_ack = self.native.ack
        for exception_type, status in ((frappe.ValidationError, 409), (frappe.PermissionError, 403)):
            with self.subTest(exception_type=exception_type.__name__):
                reached = []
                def throw_after_decryption(*args, **kwargs):
                    self.assertTrue(kwargs['attempt_token'] == secret)
                    real_ack(*args, **kwargs)
                    reached.append(True)
                    # Unlike a bare raise, throw queues the secret in message_log
                    # before raising. The HTTP boundary must clear that channel.
                    frappe.throw(secret, exception_type)
                with self.no_secret_diagnostics(secret):
                    with patch.object(self.native, 'ack', side_effect=throw_after_decryption):
                        response = self.receipt(handle)
                        self.denied(response, status)
                        self.assertFalse(secret in response.get_data(as_text=True))
                        messages = (response.get_json(silent=True) or {}).get('_server_messages', '')
                        self.assertFalse(secret in str(messages))
                self.assertEqual(reached, [True])
                self.assertEqual(self.state(name)['status'], 'leased')
                self.assertEqual(self.locks(name), HELD)
        self.assertEqual(self.message(self.receipt(handle))['status'], 'success')

    def test_receipt_http_capability_cannot_call_lease_or_heartbeat(self):
        from pearnly_cowork.companion import heartbeat
        name, handle = self.claimed()
        real_ack, checked = self.transport.acknowledge, []
        def inspect(context, body):
            for operation in ('lease', 'heartbeat'):
                with self.assertRaises(frappe.AuthenticationError):
                    self.http_auth.current_request(operation)
            for endpoint in (self.queue_api.lease, heartbeat.report):
                with self.assertRaises(frappe.AuthenticationError):
                    endpoint()
            checked.append(True)
            return real_ack(context, body)
        with patch.object(self.transport, 'acknowledge', side_effect=inspect):
            self.assertEqual(self.message(self.receipt(handle))['status'], 'success')
        self.assertEqual(checked, [True])
        self.assertEqual(self.state(name)['status'], 'success')

    def test_successful_ack_retry_clears_native_throw_messages(self):
        name, handle = self.claimed()
        secret = self.secret(handle)
        before = self.state(name)
        self.change_config(developer_mode=True)
        real_ack, reached = self.native.ack, []
        def retry_after_throw(*args, **kwargs):
            self.assertTrue(kwargs['attempt_token'] == secret)
            if reached:
                self.assertFalse(secret in json.dumps(frappe.local.message_log, default=str))
                self.assertFalse(secret in str(frappe.flags.get('error_message', '')))
            result = real_ack(*args, **kwargs)
            reached.append(True)
            if len(reached) == 1:
                # Cover both native response-message channels. A successful
                # retry must not inherit the first transaction's diagnostic.
                frappe.flags.error_message = secret
                try:
                    frappe.throw(secret, frappe.QueryDeadlockError)
                except frappe.QueryDeadlockError:
                    self.assertTrue(secret in json.dumps(frappe.local.message_log, default=str))
                    raise
            return result
        with self.no_secret_diagnostics(secret):
            with patch.object(self.native, 'ack', side_effect=retry_after_throw):
                response = self.receipt(handle)
                self.assertEqual(self.message(response),
                    {'ok': True, 'status': 'success', 'express_docnum': 'SYNTHETIC-HTTP-001'})
                self.assertFalse(secret in response.get_data(as_text=True))
                self.assertFalse(secret in str(response.get_json().get('_server_messages', '')))
        self.assertEqual(reached, [True, True])
        after = self.state(name)
        self.assertEqual((after['status'], after['fence']), ('success', before['fence']))
        self.assertEqual(after['revision'], before['revision'] + 1)
        self.assertEqual(self.db(lambda: frappe.db.count(self.native.ATTEMPT, {'push_record': name})), 1)
        self.assertEqual(self.db(lambda: frappe.db.get_value(self.native.ATTEMPT,
            after['active_attempt'], 'status')), 'finished')
        self.assertEqual(self.locks(name), 0)


if __name__ == '__main__':
    connect()
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--test', action='append', help='Run one named ACK test (repeatable)')
        args = parser.parse_args()
        if args.test and any(not name.startswith('test_') for name in args.test):
            parser.error('Expected a test_ method')
        suite = (unittest.TestSuite(NativeQueueAckHttp(name) for name in args.test) if args.test
                 else unittest.defaultTestLoader.loadTestsFromTestCase(NativeQueueAckHttp))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, 'db', None): frappe.db.rollback(); frappe.destroy()
