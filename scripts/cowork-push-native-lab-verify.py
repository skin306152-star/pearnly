"""Real Site transactions with synthetic trusted ACKs; never calls an ERP.

Uses the guarded lab connector and the application's synthetic setup. Concurrent
workers intentionally establish an old RR snapshot before their barrier. No
lease token, frozen payload, credentials, or response body is printed.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-push-application-lab-verify.py')))
BASE = FIXTURE['NativePreparation']
connect = FIXTURE['connect']


def context_from_row(row):
    from pearnly_cowork.push.native.contracts import ReservationContext
    return ReservationContext(row.actor, row.workspace, row.client or '', row.history,
        int(row.history_version), row.connection, row.account_key, branch=row.branch,
        binding_generation=int(row.binding_generation), connection_revision=int(row.connection_revision))


class NativePush(unittest.TestCase):
    setUpClass = classmethod(BASE.setUpClass.__func__)
    tearDown, prepare = BASE.tearDown, BASE.prepare

    def setUp(self):
        from pearnly_cowork.connections import service as connections
        from pearnly_cowork.history import storage
        from pearnly_cowork.push.application import api
        self.connections, self.storage, self.api = connections, storage, api
        self.old_conf = deepcopy(dict(frappe.conf))
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        # A committed concurrent lease must not lock another test's account.
        self.config = {**FIXTURE['CONFIG'], 'express_root': 'D:\\SYNTH-' + uuid4().hex}
        frappe.set_user(self.manager)
        self.connection = connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic native Express', self.config, users=[self.actor, self.manager])
        self.install()
        frappe.set_user(self.actor)
        self.history = self.another_history(invoice=FIXTURE['FIELDS']['invoice_number'])
        self.key = uuid4().hex
        from pearnly_cowork.push.native import service
        self.native = service
        self.adapter = service.trusted_adapter_context(self.connection['name'], 'synthetic-writer')
        self.clock = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

    def install(self):
        return self.availability()

    def prepared(self, **changes):
        result = self.prepare(**changes)
        self.assertEqual(result['status'], 'prepared', result)
        row = frappe.get_doc(self.native.RECORD, result['name'])
        return row.name, context_from_row(row)

    def claimed(self):
        name, context = self.prepared()
        self.native.activate(name, context=context, adapter=self.adapter)
        claim = self.native.lease(name, context=context, adapter=self.adapter, now=self.clock)
        self.assertTrue(claim['leased'])
        return name, context, claim

    def acknowledge(self, name, context, claim, **kwargs):
        return self.native.ack(name, context=context, adapter=self.adapter,
            fence=claim['fence'], attempt_token=claim['attempt_token'], **kwargs)

    def another_history(self, *, invoice=None):
        fields = deepcopy(FIXTURE['FIELDS'])
        fields['invoice_number'] = invoice or uuid4().hex
        pages = [{'page_index': 1, 'fields': fields}]
        return self.storage.create_recognized(self.workspace, pages, uuid4().hex,
            source='manual', confidence='high', ai_raw=deepcopy(pages))

    def availability(self, account='DATAT', *, active=True, writable=True):
        current = frappe.session.user
        frappe.set_user(self.manager)
        refresh = self.connections.request_refresh(self.connection['name'], uuid4().hex)
        observation = {'adapter': 'express', 'observed_at': '2026-09-21T00:00:00Z',
            'collector': {'kind': 'synthetic_lab'}, 'account_sets': [
                {'source_id': account, 'label': 'Synthetic account', 'active': active,
                 'attributes': {'root': self.config['express_root'], 'account_dir': account, 'writable': writable,
                    'mapping': {key: self.config[key] for key in ('fallback_acc', 'vat_input_acc', 'ap_acc',
                        'revenue_acc', 'vat_output_acc', 'ar_acc')}}}]}
        preparation = {'complete': True, 'account_set': account, 'root_key': self.config['express_root'],
            'mappings': {'accounts': [], 'clients': []}, 'supplier_profiles': {},
            'bank_index': [], 'reported_config': {}}
        result = self.connections.install_snapshot(self.connection['name'], observation,
            refresh['request_id'], self.connection['revision'], preparation)
        frappe.set_user(current)
        return {'catalog_request_id': refresh['request_id'], 'catalog_revision': result['revision']}

    def test_prepared_has_no_attempt_or_external_success(self):
        name, context = self.prepared()
        row = frappe.get_doc(self.native.RECORD, name)
        self.assertEqual(row.status, 'prepared')
        self.assertFalse(row.external_docnum)
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 0)
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)
        self.assertEqual(self.native.lookup_request(context, self.key)['name'], name)

    def test_adapter_dataclass_and_administrator_are_not_dispatch_authority(self):
        name, context = self.prepared()
        forged = self.native.AdapterContext(self.connection['name'], 'synthetic-writer', 0)
        frappe.set_user('Administrator')
        with self.assertRaises(frappe.PermissionError):
            self.native.activate(name, context=context, adapter=forged)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'prepared')

    def test_legacy_waiting_lock_does_not_burn_wire_attempt_but_next_lease_fences(self):
        name, context, first = self.claimed()
        result = self.acknowledge(name, context, first, success=False, outcome='waiting_lock')
        self.assertEqual((result['status'], result['wire_attempt'], result['failure_count']), ('pending', 1, 0))
        second = self.native.lease(name, context=context, adapter=self.adapter, now=self.clock)
        self.assertEqual(second['fence'], first['fence'] + 1)
        self.assertNotEqual(second['attempt_token'], first['attempt_token'])
        with self.assertRaises(frappe.ValidationError):
            self.acknowledge(name, context, first, success=True, express_docnum='STALE')
        done = self.acknowledge(name, context, second, success=True, express_docnum='SYNTHETIC-001')
        self.assertEqual(done['status'], 'success')

    def test_attempt_rejects_wrong_owner_token_and_fence(self):
        name, context, claim = self.claimed()
        for changes in ({'fence': claim['fence'] + 1}, {'attempt_token': 'wrong'},
                        {'adapter': self.native.trusted_adapter_context(self.connection['name'], 'other-writer')}):
            args = dict(context=context, adapter=self.adapter, fence=claim['fence'],
                attempt_token=claim['attempt_token'], success=True)
            args.update(changes)
            with self.subTest(fields=list(changes)), self.assertRaises(frappe.ValidationError):
                self.native.ack(name, **args)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'leased')

    def test_success_and_exact_ack_replay_never_regress(self):
        name, context, claim = self.claimed()
        result = self.acknowledge(name, context, claim, success=True, express_docnum='SYNTHETIC-OK',
            meta={'stage': 'failed'}, line_modes=[{'mode': self.native.common.ITEM_MODE_DIRECT,
                'reason': 'synthetic-fallback', 'stkcod': 'SYNTHETIC-STK'}])
        self.assertEqual(result['response_body']['meta']['stage'], 'success')
        self.assertEqual(result['response_body']['fallback_count'], 1)
        self.assertEqual(result['response_body']['meta']['created_masters'], ['SYNTHETIC-STK'])
        late = self.acknowledge(name, context, claim, success=False, error='late-error')
        self.assertTrue(late['replayed'])
        self.assertEqual((late['status'], late['external_docnum']), ('success', 'SYNTHETIC-OK'))
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)

    def test_response_body_matches_source_branch_and_stage_combinations(self):
        # Fixed ec2e5c agent_store.ack/_build_response_body and
        # shared_express_agent_queue.ack_managed/_response_body call contracts.
        # Source callers pass line_modes only on success. The intentionally
        # corrected stale meta.stage behavior has a separate real ACK test.
        modes = [{'mode': self.native.common.ITEM_MODE_DIRECT, 'reason': 'fallback'},
                 {'mode': self.native.common.ITEM_MODE_STOCK, 'stkcod': 'SYNTHETIC-CODE'}]
        for branch in ('legacy', 'managed'):
            for stage in self.native.common.ACK_OUTCOMES:
                expected = {'ok': stage == 'success',
                    'express_docnum': 'SYNTHETIC-REF' if stage == 'success' else None,
                    'meta': {'docnum': 'SYNTHETIC-META', 'stage': stage}}
                if stage == 'success':
                    expected['line_modes'] = modes
                    if branch == 'legacy':
                        expected['fallback_count'] = 1
                        expected['meta']['created_masters'] = ['SYNTHETIC-CODE']
                with self.subTest(branch=branch, stage=stage):
                    actual = self.native._response(branch, stage, 'SYNTHETIC-REF', modes,
                        {'docnum': 'SYNTHETIC-META', 'stage': stage})
                    self.assertEqual(actual, expected)

    def test_three_failed_attempts_exhaust_and_duplicate_ack_is_idempotent(self):
        name, context, claim = self.claimed()
        for number in range(1, 4):
            kwargs = {'success': False, 'error': 'synthetic-rejection',
                      'line_modes': [{'mode': 'stock', 'stkcod': 'MUST-NOT-PUBLISH'}]}
            result = self.acknowledge(name, context, claim, **kwargs)
            self.assertEqual(result['failure_count'], number)
            self.assertNotIn('line_modes', result['response_body'])
            self.assertNotIn('created_masters', result['response_body']['meta'])
            replay = self.acknowledge(name, context, claim, **kwargs)
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['failure_count'], number)
            with self.assertRaises(frappe.ValidationError):
                self.acknowledge(name, context, claim, success=False, error='different-receipt')
            self.assertEqual(result['status'], 'manual' if number == 3 else 'pending')
            if number < 3:
                claim = self.native.lease(name, context=context, adapter=self.adapter, now=self.clock)
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 3)

    def test_manual_mapping_and_review_do_not_retry(self):
        for stage in ('needs_mapping', 'needs_review'):
            self.history = self.another_history()
            self.key = uuid4().hex
            name, context, claim = self.claimed()
            result = self.acknowledge(name, context, claim, success=False, outcome=stage)
            self.assertEqual((result['status'], result['failure_count']), ('manual', 0))
            with self.assertRaises(frappe.ValidationError):
                self.native.lease(name, context=context, adapter=self.adapter)

    def test_unknown_retains_locks_blocks_retry_and_accepts_late_owned_ack(self):
        name, context, claim = self.claimed()
        early = self.native.recover_expired(name, context=context, adapter=self.adapter, now=self.clock)
        self.assertEqual(early['status'], 'leased')
        result = self.native.recover_expired(name, context=context, adapter=self.adapter,
            now=self.clock + timedelta(seconds=121))
        self.assertEqual((result['status'], result['result_unknown']), ('manual', True))
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 2)
        blocked = self.prepare(request_key=uuid4().hex)
        self.assertEqual(blocked['blocked_reason'], 'unknown')
        with self.assertRaises(frappe.ValidationError):
            self.native.lease(name, context=context, adapter=self.adapter)
        late = self.acknowledge(name, context, claim, success=True, express_docnum='LATE-OK')
        self.assertEqual((late['status'], late['result_unknown']), ('success', False))
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)

    def test_endpoint_and_cross_connection_account_are_exclusive(self):
        first, _, _ = self.claimed()
        self.history = self.another_history()
        self.key = uuid4().hex
        second, context = self.prepared()
        self.native.activate(second, context=context, adapter=self.adapter)
        busy = self.native.lease(second, context=context, adapter=self.adapter)
        self.assertEqual((busy['leased'], busy['reason']), (False, 'waiting_lock'))
        frappe.set_user(self.manager)
        self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic second endpoint', self.config, users=[self.actor, self.manager])
        self.install()
        frappe.set_user(self.actor)
        self.adapter = self.native.trusted_adapter_context(self.connection['name'], 'second-writer')
        self.key = uuid4().hex
        third, context = self.prepared()
        self.native.activate(third, context=context, adapter=self.adapter)
        busy = self.native.lease(third, context=context, adapter=self.adapter)
        self.assertEqual((busy['leased'], busy['reason']), (False, 'waiting_lock'))
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': first}), 2)

    def test_switch_off_blocks_dispatch_but_does_not_drop_receipt(self):
        name, context = self.prepared()
        frappe.conf.pearnly_cowork_erp_push_enabled = False
        with self.assertRaises(frappe.ValidationError): self.native.activate(name, context=context, adapter=self.adapter)
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        self.native.activate(name, context=context, adapter=self.adapter)
        frappe.conf.pearnly_cowork_erp_push_enabled = False
        with self.assertRaises(frappe.ValidationError): self.native.lease(name, context=context, adapter=self.adapter)
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        claim = self.native.lease(name, context=context, adapter=self.adapter)
        frappe.conf.pearnly_cowork_erp_push_enabled = False
        done = self.acknowledge(name, context, claim, success=True)
        self.assertEqual(done['status'], 'success')

    def test_revoked_actor_cannot_dispatch_but_owned_receipt_still_records(self):
        name, context, claim = self.claimed()
        frappe.set_user('Administrator')
        frappe.db.delete('User Permission', {'name': self.permission})
        frappe.clear_cache(user=self.actor)
        with self.assertRaises(frappe.PermissionError): self.native.lease(name, context=context, adapter=self.adapter)
        result = self.acknowledge(name, context, claim, success=True)
        self.assertEqual(result['status'], 'success')

    def test_changed_history_blocks_activation_and_cancellation_releases_only_prepared(self):
        name, context = self.prepared()
        frozen = frappe.db.get_value(self.native.RECORD, name, 'payload_json')
        pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'EDITED-' + uuid4().hex
        self.history = self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        with self.assertRaises(frappe.ValidationError): self.native.activate(name, context=context, adapter=self.adapter)
        refreshed_context = replace(context, history_version=self.history['record_version'])
        with self.assertRaises(frappe.ValidationError):
            self.native.activate(name, context=refreshed_context, adapter=self.adapter)
        with self.assertRaises(frappe.ValidationError): self.native.cancel_prepared(name, context=context, expected_revision=2)
        result = self.native.cancel_prepared(name, context=context, expected_revision=1)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(frappe.db.get_value(self.native.RECORD, name, 'active_key'))
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'payload_json'), frozen)
        second, _ = self.prepared(request_key=uuid4().hex)
        self.assertNotEqual(second, name)

    def test_cancel_denied_for_pending_leased_unknown_and_success(self):
        name, context = self.prepared()
        self.native.activate(name, context=context, adapter=self.adapter)
        for state in ('pending', 'leased', 'unknown', 'success'):
            if state == 'leased': claim = self.native.lease(name, context=context, adapter=self.adapter, now=self.clock)
            if state == 'unknown': self.native.recover_expired(name, context=context, adapter=self.adapter, now=self.clock + timedelta(seconds=121))
            if state == 'success': self.acknowledge(name, context, claim, success=True)
            row = frappe.get_doc(self.native.RECORD, name)
            with self.subTest(state=state), self.assertRaises(frappe.ValidationError):
                self.native.cancel_prepared(name, context=context, expected_revision=int(row.revision))

    def test_catalog_refresh_without_config_revision_blocks_frozen_target_dispatch(self):
        proof = self.availability('DATAB')
        name, context = self.prepared(account_set_key='DATAB', **proof)
        self.availability('DATAB', active=False)
        self.assertEqual(frappe.db.get_value('Cowork ERP Connection', self.connection['name'], 'record_revision'), context.connection_revision)
        with self.assertRaises(frappe.ValidationError):
            self.native.activate(name, context=context, adapter=self.adapter)
        self.native.cancel_prepared(name, context=context, expected_revision=1)
        proof = self.availability('DATAB')
        name, context = self.prepared(account_set_key='DATAB', request_key=uuid4().hex, **proof)
        self.native.activate(name, context=context, adapter=self.adapter)
        self.availability('DATAB', writable=False)
        with self.assertRaises(frappe.ValidationError):
            self.native.lease(name, context=context, adapter=self.adapter)
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 0)

    def test_bound_default_explicit_negative_catalog_does_not_lose_old_ack(self):
        name, context, claim = self.claimed()
        self.availability(writable=False)
        self.assertEqual(self.acknowledge(name, context, claim, success=True)['status'], 'success')
        self.history = self.another_history(); self.key = uuid4().hex
        self.availability()
        name, context = self.prepared()
        self.availability(active=False)
        with self.assertRaises(frappe.ValidationError):
            self.native.activate(name, context=context, adapter=self.adapter)

    def test_revoked_actor_pristine_stale_preparation_is_atomically_superseded(self):
        name, context = self.prepared()
        frozen = frappe.db.get_value(self.native.RECORD, name, 'payload_json')
        frappe.set_user(self.manager)
        self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic new owner', self.config, users=[self.manager],
            name=self.connection['name'], expected_revision=self.connection['revision'])
        self.install()
        frappe.set_user(self.actor)
        with self.assertRaises(frappe.PermissionError):
            self.native.cancel_prepared(name, context=context, expected_revision=1)
        frappe.set_user(self.manager)
        replacement = self.prepare(request_key=uuid4().hex)
        self.assertEqual(replacement['status'], 'prepared')
        self.assertNotEqual(replacement['name'], name)
        old = frappe.get_doc(self.native.RECORD, name)
        self.assertEqual((old.status, old.reason, old.modified_by),
            ('cancelled', 'push.preparation_superseded', self.manager))
        self.assertEqual(old.payload_json, frozen)
        self.assertFalse(old.active_key)
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 0)

    def test_changed_authorization_never_supersedes_an_activated_record(self):
        name, context = self.prepared()
        self.native.activate(name, context=context, adapter=self.adapter)
        frappe.set_user(self.manager)
        self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic new owner', self.config, users=[self.manager],
            name=self.connection['name'], expected_revision=self.connection['revision'])
        self.install()
        blocked = self.prepare(request_key=uuid4().hex)
        self.assertEqual((blocked['name'], blocked['blocked_reason']), (name, 'active'))
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'pending')

    def test_role_revocation_without_revision_change_allows_pristine_replacement(self):
        name, _ = self.prepared()
        frappe.set_user(self.manager)
        blocked = self.prepare(request_key=uuid4().hex)
        self.assertEqual((blocked['name'], blocked['blocked_reason']), (name, 'active'))
        frappe.db.delete('Has Role', {'parent': self.actor, 'role': 'Cowork Intake'})
        replacement = self.prepare(request_key=uuid4().hex)
        self.assertEqual(replacement['status'], 'prepared')
        self.assertNotEqual(replacement['name'], name)
        self.assertEqual(frappe.db.get_value('Cowork ERP Connection', self.connection['name'], 'record_revision'), 1)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'cancelled')

    def test_same_invoice_in_another_history_is_pushed_again(self):
        # 不查重（用户 2026-09-30 / 10-08）：同发票号同卖方的另一张票照常准备、照常领取写入。
        first, first_context, claim = self.claimed()
        self.history = self.another_history(invoice=FIXTURE['FIELDS']['invoice_number'])
        self.key = uuid4().hex
        second, second_context = self.prepared()
        self.native.activate(second, context=second_context, adapter=self.adapter)
        self.acknowledge(first, first_context, claim, success=True, express_docnum='BIZ-OK')
        leased = self.native.lease(second, context=second_context, adapter=self.adapter)
        self.assertTrue(leased['leased'], leased)
        self.history = self.another_history(invoice=FIXTURE['FIELDS']['invoice_number'])
        third = self.prepare(request_key=uuid4().hex)
        self.assertEqual(third['status'], 'prepared', third)
        self.assertNotIn(third['name'], (first, second))
        self.assertFalse(third.get('blocked_by'))

    def test_lease_and_ack_failure_roll_back_all_rows(self):
        name, context = self.prepared()
        self.native.activate(name, context=context, adapter=self.adapter)
        actual = self.native.persist
        def fail_record(doc):
            if doc.doctype == self.native.RECORD: raise RuntimeError('synthetic final record failure')
            return actual(doc)
        with patch.object(self.native, 'persist', side_effect=fail_record), self.assertRaises(RuntimeError):
            self.native.lease(name, context=context, adapter=self.adapter)
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 0)
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'pending')
        claim = self.native.lease(name, context=context, adapter=self.adapter)
        with patch.object(self.native, 'persist', side_effect=fail_record), self.assertRaises(RuntimeError):
            self.acknowledge(name, context, claim, success=True)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'leased')
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 2)
        self.assertEqual(self.acknowledge(name, context, claim, success=True)['status'], 'success')

    def test_generic_reads_writes_and_shares_are_denied_even_admin(self):
        name, _, _ = self.claimed()
        for doctype in self.native.TYPES:
            rows = frappe.get_all(doctype, filters={'workspace': self.workspace}, pluck='name')
            self.assertTrue(rows)
            for actor in (self.actor, 'Administrator'):
                frappe.set_user(actor)
                doc = frappe.get_doc(doctype, rows[0])
                with self.subTest(doctype=doctype, actor=actor):
                    with self.assertRaises(frappe.PermissionError): doc.check_permission('read')
                    with self.assertRaises(frappe.PermissionError): frappe.get_list(doctype)
                    doc.flags.cowork_push_native = True
                    with self.assertRaises(frappe.PermissionError): doc.save(ignore_permissions=True)
                    with self.assertRaises(frappe.PermissionError): frappe.delete_doc(doctype, doc.name, ignore_permissions=True)
                    with self.assertRaises(frappe.PermissionError):
                        frappe.get_doc({'doctype': 'DocShare', 'share_doctype': doctype,
                            'share_name': doc.name, 'user': self.viewer, 'read': 1}).insert(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'leased')

    def workers(self, mode, arguments):
        workers = [subprocess.Popen([sys.executable, __file__, '--worker', mode, self.actor, *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for args in arguments]
        try:
            for worker in workers: self.assertEqual(worker.stdout.readline().strip(), 'READY')
            for worker in workers:
                worker.stdin.write('go\n'); worker.stdin.flush()
            outputs = [worker.communicate(timeout=45) for worker in workers]
            for worker, (output, error) in zip(workers, outputs): self.assertEqual(worker.returncode, 0, output + error)
            return [json.loads(output.strip().splitlines()[-1]) for output, _ in outputs]
        finally:
            for worker in workers:
                if worker.poll() is None: worker.kill(); worker.communicate()

    def test_concurrent_reservation_same_and_different_keys_under_old_snapshots(self):
        for same_key in (True, False):
            if not same_key: self.history = self.another_history()
            frappe.db.commit()
            keys = [uuid4().hex, uuid4().hex]
            if same_key: keys[1] = keys[0]
            args = [[self.history['name'], self.connection['name'], str(self.history['record_version']),
                str(self.connection['revision']), key] for key in keys]
            results = self.workers('prepare', args)
            self.assertEqual(results[0]['name'], results[1]['name'])
            frappe.db.rollback()
            self.assertEqual(frappe.db.count(self.native.RECORD, {'history': self.history['name']}), 1)
            frappe.db.rollback()

    def test_concurrent_lease_has_one_fence_and_one_attempt(self):
        name, context = self.prepared()
        self.native.activate(name, context=context, adapter=self.adapter)
        frappe.db.commit()
        results = self.workers('lease', [[name], [name]])
        self.assertEqual(sum(result['leased'] for result in results), 1)
        frappe.db.rollback()
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 1)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'fence'), 1)


def worker():
    from pearnly_cowork.push.native import service as native
    from pearnly_cowork.push.application import api
    mode, actor, *args = sys.argv[2:]
    frappe.set_user(actor)
    frappe.conf.pearnly_cowork_erp_push_enabled = True
    frappe.db.count(native.RECORD)  # Establish an older RR snapshot.
    print('READY', flush=True)
    if sys.stdin.readline().strip() != 'go': raise SystemExit('Missing concurrency barrier')
    if mode == 'prepare':
        history, connection, history_version, connection_revision, key = args
        value = api.prepare(history, connection, key, int(history_version), int(connection_revision))
        result = {'name': value['name'], 'status': value['status']}
    elif mode == 'lease':
        # This process owns one complete local transaction and has emitted no
        # external request. A deadlock/1020 requires a fresh transaction from
        # the beginning, including current authorization and immutable routing.
        for attempt in range(3):
            try:
                row = frappe.get_doc(native.RECORD, args[0])
                context = context_from_row(row)
                adapter = native.trusted_adapter_context(row.connection, 'synthetic-concurrent')
                try:
                    value = native.lease(row.name, context=context, adapter=adapter)
                    result = {'leased': bool(value['leased'])}
                except frappe.ValidationError as error:
                    if frappe._('push.native_state_invalid') not in str(error): raise
                    result = {'leased': False, 'rejected': True}
                break
            except frappe.QueryDeadlockError:
                frappe.db.rollback()
                if attempt == 2: raise
    else: raise SystemExit('Invalid synthetic worker mode')
    frappe.db.commit()
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--worker': worker()
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativePush))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
