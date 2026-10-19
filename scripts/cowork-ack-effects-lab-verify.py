"""Bounded ACK effects checks on the marked isolated Cowork lab.

Synthetic local users, invoices and machine credentials only. Reuses fixture
helpers, not earlier suites. No LINE/ERP call, scheduler or external network.

Evidence v2 (ADR-021): the stock master count lives on the account set's Account Facts, whichever
computer read it; lock scheme v2 holds the account-set lock only for synthetic registrations.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

PREP = runpy.run_path(str(Path(__file__).with_name('cowork-preparation-confirm-lab-verify.py')))
P = PREP['PreparationConfirmation']
connect = PREP['connect']
# 锁口径 v2（ADR-021 第 3 批）：账套锁 + 按内容键的公司锁，没有 endpoint 锁；合成登记没有内容键，只有账套锁
LOCKS = 1


class AckEffects(unittest.TestCase):
    setUpClass = classmethod(P.setUpClass.__func__)
    tearDown, install, prepare, report, confirm, row = P.tearDown, P.install, P.prepare, P.report, P.confirm, P.row
    choose, refused = P.choose, P.refused

    def setUp(self):
        P.setUp(self)
        from pearnly_cowork.push.native import service, completion
        from pearnly_cowork.history import api as history_api, push_state
        from pearnly_cowork.companion import receipt_auth, delivery_store
        self.native, self.effects, self.history_api, self.push_state = service, completion, history_api, push_state
        self.receipt_auth, self.deliveries = receipt_auth, delivery_store
        # Supply the same attributed inventory fingerprint as the real heartbeat.
        self.report(catalog={'products': [], 'customers': [], 'fingerprint': {'stock_master_count': 0}})
        self.initial_count = self.count()

    def line_source(self):
        self.history = self.storage.create_recognized(self.workspace, self.history['pages'], uuid4().hex,
            source='cowork_line', confidence='high', ai_raw=self.history['pages'])

    def claimed(self, *, line=True):
        if line: self.line_source()
        row = self.prepare()
        self.confirm(row)
        job = self.transport.lease(self.machine, {})['jobs'][0]
        return row, job['log_id']

    def ack(self, handle, **changes):
        body = {'log_id': handle, 'result': 'success', 'express_docnum': 'SYNTHETIC-ACK-001',
                'line_modes': [{'mode': 'stock_item', 'created': True, 'stkcod': 'SYNTHETIC-S1'},
                               {'mode': 'stock_item', 'created': False}, {'mode': 'direct_account', 'created': True}]}
        body.update(changes)
        context = self.receipt_auth.authenticate(self.issued['token'], handle)
        return self.transport.acknowledge(context, body)

    def count(self):
        # 存货主档数记在这份账套的账套资料上（ADR-021 第 2 批：主档不再存在连接的上报记录里）
        from pearnly_cowork.connections import pool
        found = pool.facts(self.connection['name'], 'datat') or {}
        return (found.get('catalog_fingerprint') or {}).get('stock_master_count')

    def events(self, row):
        return frappe.get_all(self.effects.NOTIFICATION, filters={'push_record': row['name']}, fields=['*'])

    def test_success_duplicate_and_late_failure_one_effect(self):
        row, handle = self.claimed()
        before = self.storage.read(self.history['name']).as_dict()
        self.assertEqual(before['last_push_status'], 'pending')
        self.assertEqual(self.initial_count, 0)
        self.assertEqual(self.ack(handle)['status'], 'success')
        after = self.history_api.detail(self.history['name'])
        self.assertEqual((after['last_push_status'], after['last_push_record']), ('success', row['name']))
        self.assertEqual((after['record_version'], after['edit_count'], after['fields_edited_at']),
                         (before['record_version'], before['edit_count'], before['fields_edited_at']))
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.history_api.records(self.workspace, staged=1)[0]['last_push_status'], 'success')
        event = self.events(row)
        self.assertEqual(len(event), 1)
        self.assertEqual(event[0].status, 'held')
        body = json.loads(event[0].payload_json)
        self.assertEqual((body['actor'], body['channel'], body['total_amount']), (self.actor, 'cowork', '107.00'))
        self.assertNotIn(self.issued['token'], event[0].payload_json)
        first = self.row(row).as_dict()
        self.ack(handle)
        self.ack(handle, result='failed', outcome='failed', error='late failure')
        self.assertEqual(self.count(), 1)
        self.assertEqual(len(self.events(row)), 1)
        self.assertEqual(self.row(row).response_json, first['response_json'])
        self.assertEqual(self.row(row).revision, first['revision'])

    def test_review_workbook_uses_server_status_not_invoice_field(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from pearnly_cowork.roundtrip import service
        pages = deepcopy(self.history['pages']); pages[0]['fields']['push_status'] = 'FORGED-STATUS'
        self.history = self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        row, handle = self.claimed(line=False)
        self.ack(handle)
        workbook = load_workbook(BytesIO(service.export([self.history['name']])), data_only=True)
        values = [str(c) for sheet in workbook for row in sheet.values for c in row if c is not None]
        self.assertNotIn('FORGED-STATUS', values)
        # 复核工作簿的表头与状态跟着录入人的界面语言（10-08 文案整改起，合成用户是英文）；状态取服务端的推送结果
        self.assertIn(frappe._('Succeeded', lang='en'), values)

    def test_web_source_never_creates_line_event(self):
        row, handle = self.claimed(line=False)
        self.ack(handle)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.events(row), [])

    def test_retry_and_manual_outcomes_do_not_increment_stock_or_notify(self):
        row, handle = self.claimed()
        for number in range(3):
            result = self.ack(handle, result='failed', outcome='rolled_back')
            self.assertEqual(result['status'], 'manual' if number == 2 else 'pending')
            self.assertEqual(self.storage.read(self.history['name']).last_push_status, result['status'])
            self.assertEqual(self.count(), 0)
            self.assertEqual(self.events(row), [])
            if number < 2:
                handle = self.transport.lease(self.machine, {})['jobs'][0]['log_id']

    def test_waiting_then_needs_review_mirrors_only_terminal(self):
        row, handle = self.claimed()
        before = self.storage.read(self.history['name']).last_pushed_at
        self.ack(handle, result='failed', outcome='waiting_lock')
        self.assertEqual(self.storage.read(self.history['name']).last_pushed_at, before)
        new = self.transport.lease(self.machine, {})['jobs'][0]['log_id']
        self.ack(new, result='failed', outcome='needs_review')
        self.assertEqual(self.storage.read(self.history['name']).last_push_status, 'manual')
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.events(row), [])

    def test_unknown_keeps_locks_and_late_success_records_once(self):
        row, handle = self.claimed()
        delivery = self.deliveries.get(handle)
        current = self.row(row)
        from pearnly_cowork.companion.transport import _context, _adapter
        self.native.recover_expired(row['name'], context=_context(current, self.machine),
            adapter=_adapter(current.connection, delivery.owner_id),
            now=current.lease_expires_at.replace(tzinfo=PREP['timezone'].utc) + timedelta(seconds=1))
        self.assertEqual(self.storage.read(self.history['name']).last_push_status, 'manual')
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': row['name']}), LOCKS)
        self.assertEqual(self.events(row), [])
        self.ack(handle)
        self.assertEqual(self.storage.read(self.history['name']).last_push_status, 'success')
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': row['name']}), 0)

    def test_revoked_actor_and_rotated_report_accept_receipt_without_wrong_count(self):
        row, handle = self.claimed()
        frappe.set_user(self.manager)
        fresh = self.credentials.issue(self.connection['name'], uuid4().hex, reset=True, expected_generation=1)
        self.machine = self.credentials.authenticate(fresh['token'])
        self.report(catalog={'fingerprint': {'stock_master_count': 10}})
        # 录入人失权：收回录入角色（Cowork 不按人分公司，删工作空间权限不再是失权）
        frappe.db.delete('Has Role', {'parent': self.actor, 'role': ('in', ('Cowork Intake', 'Cowork Admin'))})
        frappe.clear_cache(user=self.actor)
        frappe.set_user('Guest')
        self.ack(handle)
        # 账套资料按账套存、不按哪把密钥读的（ADR-021 第 2 批）：换钥后新钥读到的 10 个存货主档仍是这份账套的，回执新建的
        # 1 个累加上去（v1 按上报来源判「目标变了」不加）
        self.assertEqual(self.count(), 11)
        self.assertEqual(json.loads(self.row(row).completion_json)['stock']['state'], 'applied')
        self.assertEqual(len(self.events(row)), 1)
        self.assertEqual(frappe.db.get_value('Cowork OCR History', row['history'], 'last_push_status'), 'success')

    def test_stock_effect_requires_account_origin_and_later_scan_replaces_it(self):
        row, handle = self.claimed()
        # 这份账套的资料里没有目录指纹（读不到存货主档数）：回执不编数，如实记 unavailable
        from pearnly_cowork.connections import pool
        name = pool.facts_name(self.connection['name'], 'datat')
        body = json.loads(frappe.db.get_value(pool.FACTS, name, 'body')); body.pop('catalog_fingerprint', None)
        frappe.db.set_value(pool.FACTS, name, 'body', json.dumps(body), update_modified=False)
        self.ack(handle)
        self.assertIsNone(self.count())
        self.assertEqual(json.loads(self.row(row).completion_json)['stock']['state'], 'unavailable')
        self.report(catalog={'fingerprint': {'stock_master_count': 7}})
        self.ack(handle)
        self.assertEqual(self.count(), 7)

    def test_effect_failure_rolls_back_receipt_history_count_event_and_release(self):
        row, handle = self.claimed()
        for module, name in ((self.effects, 'persist'), (self.push_state, 'mirror')):
            with self.subTest(failing=name):
                with patch.object(module, name, side_effect=RuntimeError('synthetic late failure')):
                    with self.assertRaises(RuntimeError): self.ack(handle)
                self.assertEqual(self.row(row).status, 'leased')
                self.assertEqual(self.storage.read(self.history['name']).last_push_status, 'pending')
                self.assertEqual(self.count(), 0)
                self.assertEqual(self.events(row), [])
                self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': row['name']}), LOCKS)
        self.ack(handle)
        self.assertEqual(len(self.events(row)), 1)

    def test_edit_after_lease_does_not_change_notification_amount_or_edit_revision(self):
        row, handle = self.claimed()
        pages = deepcopy(self.history['pages']); pages[0]['fields']['total_amount'] = '999'
        edited = self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        self.ack(handle)
        doc = self.storage.read(self.history['name'])
        self.assertEqual(doc.record_version, edited['record_version'])
        self.assertEqual(json.loads(self.events(row)[0].payload_json)['total_amount'], '107.00')

    def test_two_process_ack_with_old_snapshots_counts_once(self):
        row, handle = self.claimed()
        frappe.db.commit()
        workers = []
        data = {'token': self.issued['token'], 'body': {'log_id': handle, 'result': 'success',
                'line_modes': [{'mode': 'stock_item', 'created': True}]}}
        try:
            for _ in range(2):
                process = subprocess.Popen([sys.executable, __file__, '--ack-worker'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                workers.append(process)
                process.stdin.write(json.dumps(data) + '\n'); process.stdin.flush()
            for process in workers: self.assertEqual(process.stdout.readline().strip(), 'READY')
            for process in workers: process.stdin.write('go\n'); process.stdin.flush()
            results = []
            for process in workers:
                output, errors = process.communicate(timeout=40)
                self.assertEqual(process.returncode, 0, errors)
                results.append(json.loads(output))
            self.assertEqual(sum(bool(r.get('idempotent')) for r in results), 1)
            frappe.db.rollback()
            self.assertEqual(self.count(), 1)
            self.assertEqual(len(self.events(row)), 1)
            self.assertEqual(frappe.db.get_value('Cowork OCR History', row['history'], 'last_push_status'), 'success')
        finally:
            for process in workers:
                if process.poll() is None: process.kill(); process.communicate()

    def test_late_ack_does_not_replace_more_recent_connection_selection(self):
        old_row, handle = self.claimed()
        old_connection, old_issued, old_history = self.connection, self.issued, self.history
        # A second authorized connection for the same invoice, never dispatched.
        frappe.set_user(self.manager)
        self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic second connection', PREP['BASE']['CONFIG'], users=[self.actor, self.manager])
        self.install()
        self.issued = self.credentials.issue(self.connection['name'], uuid4().hex)
        self.machine = self.credentials.authenticate(self.issued['token'])
        self.report(); self.choose(); frappe.set_user(self.actor)
        newer = self.prepare(request_key=uuid4().hex); self.confirm(newer)
        self.connection, self.issued, self.history = old_connection, old_issued, old_history
        self.ack(handle)
        history = self.storage.read(old_history['name'])
        self.assertEqual((history.last_push_record, history.last_push_status), (newer['name'], 'pending'))
        self.assertEqual(self.row(old_row).status, 'success')
        self.assertEqual(len(self.events(old_row)), 1)

    def test_private_event_and_projection_cannot_be_forged(self):
        row, handle = self.claimed(); self.ack(handle)
        event = self.events(row)[0]
        for actor in (self.actor, self.other, 'Guest', 'Administrator'):
            frappe.set_user(actor)
            with self.assertRaises(frappe.PermissionError): frappe.get_list(self.effects.NOTIFICATION)
            with self.assertRaises(frappe.PermissionError): frappe.get_doc(self.effects.NOTIFICATION, event.name).check_permission()
        frappe.set_user(self.actor)
        doc = self.storage.read(self.history['name'])
        doc.last_push_status = 'manual'; doc.flags.cowork_history_update = self.storage._UPDATE_TOKEN
        with self.assertRaises(frappe.ValidationError): doc.save()
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({'doctype': 'DocShare', 'share_doctype': self.effects.NOTIFICATION,
                           'share_name': event.name, 'user': self.other, 'read': 1}).insert(ignore_permissions=True)
        self.assertNotIn(self.effects.success, frappe.whitelisted)


if __name__ == '__main__':
    connect()
    try:
        if '--ack-worker' in sys.argv:
            from pearnly_cowork.companion import receipt_auth, transport
            data = json.loads(sys.stdin.readline())
            frappe.db.count('Cowork Push Record')
            print('READY', flush=True)
            assert sys.stdin.readline().strip() == 'go'
            for attempt in range(3):
                try:
                    context = receipt_auth.authenticate(data['token'], data['body']['log_id'])
                    result = transport.acknowledge(context, data['body'])
                    frappe.db.commit()
                    print(json.dumps(result), flush=True)
                    break
                except frappe.QueryDeadlockError:
                    frappe.db.rollback()
                    if attempt == 2: raise
            raise SystemExit(0)
        suite = (unittest.TestSuite(AckEffects(name) for name in sys.argv[1:]) if len(sys.argv)>1
                 else unittest.defaultTestLoader.loadTestsFromTestCase(AckEffects))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful(): raise SystemExit(1)
    finally:
        frappe.db.rollback(); frappe.destroy()
