"""Real local Delivery/Password/ledger transactions, synthetic work only.

No HTTP listener, ERP writer, Windows client, notification or external call.
Explicit pending activation uses the existing internal synthetic fixture seam.

Lease v2 (ADR-021): the computer first reports its reach through `heartbeat._receive`; each account set's
queue head only, `attended` on the job, lock scheme v2 (account-set lock, company lock by content key).
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
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

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-push-native-lab-verify.py')))
BASE = FIXTURE['NativePush']
connect = FIXTURE['connect']
# 锁口径 v2（ADR-021 第 3 批）：只取账套锁 + 按内容键的公司锁，不再有整条连接的 endpoint 锁；合成登记没有内容键，只有账套锁
LOCKS = 1
MAPPING_KEYS = ('fallback_acc', 'vat_input_acc', 'ap_acc', 'revenue_acc', 'vat_output_acc', 'ar_acc')


class CompanionTransport(unittest.TestCase):
    tearDown, prepare, prepared = BASE.tearDown, BASE.prepare, BASE.prepared
    install, availability, another_history = BASE.install, BASE.availability, BASE.another_history

    @classmethod
    def setUpClass(cls):
        # Same explicit synthetic role fixture as the native connections suite;
        # this standalone installation is not production tenant provisioning.
        if not frappe.db.exists('Role', 'Cowork Push Log'):
            frappe.get_doc({'doctype': 'Role', 'role_name': 'Cowork Push Log'}).insert()
        BASE.setUpClass.__func__(cls)

    def setUp(self):
        BASE.setUp(self)
        from pearnly_cowork.companion import credentials, delivery_store, receipt_auth, transport
        self.credentials, self.deliveries, self.receipts, self.transport = credentials, delivery_store, receipt_auth, transport
        frappe.set_user(self.manager)
        self.issued = credentials.issue(self.connection['name'], uuid4().hex)
        self.machine = credentials.authenticate(self.issued['token'])
        self.report()
        self.choose()
        frappe.set_user(self.actor)

    def report(self, machine=None, **changes):
        # 多台电脑之后（ADR-021）领取只看这台电脑够得到、能写的账套：电脑先经真实心跳入口报到这份账套（可达行、账套资料）
        from pearnly_cowork.companion import heartbeat
        mapping = {key: self.config[key] for key in MAPPING_KEYS}
        body = {'account_set': 'DATAT', 'express_root': self.config['express_root'],
                'account_sets': [{'code': 'DATAT', 'path': 'DATAT', 'root': self.config['express_root'],
                                  'writable': True, 'mapping': mapping}],
                'accounts': [{'code': code} for code in sorted(set(mapping.values()))],
                'catalog': {'products': [], 'customers': []}, 'max_payload_version': 1, **changes}
        return heartbeat._receive(machine or self.machine, body)

    def choose(self):
        # 推送前要定的付款方式与科目：像会计在复核页选过一次一样记在账套记忆里
        from pearnly_cowork.push import choices as choice_store
        subject = 'purchase|' + FIXTURE['FIXTURE']['FIELDS']['seller_tax']
        choice_store.record(self.connection['name'], 'DATAT', 'payment', subject, 'credit')
        choice_store.record(self.connection['name'], 'DATAT', 'account', subject, 'A')

    def pending(self):
        name, context = self.prepared()
        self.native.activate(name, context=context, adapter=self.adapter)
        return name

    def take(self):
        return self.transport.lease(self.machine, {'max': 50, 'agent_id': 'untrusted-device-label'})

    def claimed(self):
        name = self.pending()
        jobs = self.take()['jobs']
        self.assertEqual(len(jobs), 1)
        return name, jobs[0]['log_id']

    def ack(self, handle, *, token=None, **fields):
        context = self.receipts.authenticate(token or self.issued['token'], handle)
        return self.transport.acknowledge(context, {'log_id': handle, 'result': 'success',
            'express_docnum': 'SYNTHETIC-001', **fields})

    def rotate(self):
        current = frappe.session.user
        frappe.set_user(self.manager)
        result = self.credentials.issue(self.connection['name'], uuid4().hex, reset=True, expected_generation=1)
        frappe.set_user(current)
        return result

    def revoke_actor(self):
        # Cowork 不按人分公司（用户 2026-09-26），删工作空间的用户权限不再是失权；收回录入角色才是（改使用名单会连带改
        # 连接版本，那是「目标变了」，另一个原因）
        frappe.db.delete('Has Role', {'parent': self.actor, 'role': ('in', ('Cowork Intake', 'Cowork Admin'))})
        frappe.clear_cache(user=self.actor)

    def assert_pristine(self, name, auth_count):
        row = frappe.get_doc(self.native.RECORD, name)
        self.assertEqual(row.status, 'pending')
        self.assertFalse(row.active_attempt)
        self.assertEqual(row.fence, 0)
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 0)
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)
        self.assertEqual(frappe.db.count('Cowork Companion Delivery', {'push_record': name}), 0)
        self.assertEqual(frappe.db.sql('SELECT COUNT(*) FROM `__Auth`')[0][0], auth_count)

    def test_poll_never_activates_prepared(self):
        name, _ = self.prepared()
        self.assertEqual(self.take()['jobs'], [])
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'prepared')

    def test_claim_has_exact_frozen_job_and_no_attempt_capability(self):
        name = self.pending()
        result = self.take(); job = result['jobs'][0]
        row = frappe.get_doc(self.native.RECORD, name)
        delivery = self.deliveries.get(job['log_id'])
        secret = self.deliveries.secret(delivery)
        self.assertEqual(set(result), {'ok', 'lease_seconds', 'jobs'})
        self.assertEqual(result['lease_seconds'], 120)
        # 领取 v2 多给 `attended`（这张单写的时候有没有人在电脑前，自动推的单不弹「账套被占用」）
        self.assertEqual(set(job), {'log_id', 'history_id', 'invoice_no', 'payload', 'attended'})
        self.assertIs(job['attended'], False)  # 旧槽位密钥的电脑没有登录记录：过渡期替谁都写，不弹询问框
        self.assertEqual(job['payload'], json.loads(row.payload_json))
        self.assertEqual(job['history_id'], self.history['name'])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(name, job['log_id'])
        self.assertEqual(delivery.credential_id, self.issued['credential_id'])
        self.assertEqual(delivery.push_attempt, row.active_attempt)
        self.assertEqual(row.lease_owner, self.deliveries.owner(self.machine))
        self.assertEqual(self.take()['jobs'], [])

    def test_password_encrypted_no_version_no_plaintext_document(self):
        _, handle = self.claimed()
        doc = self.deliveries.get(handle); secret = self.deliveries.secret(doc)
        auth = frappe.db.sql('SELECT password,encrypted FROM `__Auth` WHERE doctype=%s AND name=%s',
            (doc.doctype, doc.name))[0]
        self.assertEqual(auth[1], 1)
        self.assertNotEqual(auth[0], secret)
        self.assertNotIn(secret, json.dumps(doc.as_dict(), default=str))
        self.assertNotIn(secret, repr(doc))
        self.assertEqual(frappe.db.count('Version', {'ref_doctype': doc.doctype, 'docname': doc.name}), 0)

    def test_one_attempt_cannot_have_two_delivery_handles(self):
        name, handle = self.claimed()
        doc = self.deliveries.get(handle)
        claim = {'leased': True, 'record': self.native.project(frappe.get_doc(self.native.RECORD, name)),
                 'fence': int(doc.fence), 'attempt_token': self.deliveries.secret(doc)}
        before = frappe.db.sql('SELECT COUNT(*) FROM `__Auth`')[0][0]
        with self.assertRaises(frappe.UniqueValidationError), self.native.atomic():
            self.deliveries.create(credential=self.machine, claim=claim)
        self.assertEqual(frappe.db.count('Cowork Companion Delivery', {'push_record': name}), 1)
        self.assertEqual(frappe.db.sql('SELECT COUNT(*) FROM `__Auth`')[0][0], before)
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_real_framework_translation_resolves_all_eleven_new_terms(self):
        keys = ['Cowork Companion Delivery', 'Credential ID', 'Owner ID', 'Push Attempt', 'Attempt Secret',
                'Use the Cowork Companion service to access deliveries.', 'companion.queue_request_invalid',
                'companion.queue_response_invalid', 'companion.delivery_invalid', 'companion.delivery_not_found',
                'companion.queue_access_revoked']
        frappe.clear_cache()
        for language in ('en', 'zh', 'th', 'ja'):
            path = Path(frappe.get_app_path('pearnly_cowork', 'translations', language + '.csv'))
            table = dict(csv.reader(path.open()))
            for key in keys:
                with self.subTest(language=language, key=key):
                    self.assertEqual(frappe._(key, lang=language), table[key])

    def test_real_wsgi_delivery_read_list_and_internal_methods_are_denied(self):
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        from frappe.app import application
        _, handle = self.claimed()
        secret = self.deliveries.secret(self.deliveries.get(handle))
        site = frappe.local.site
        frappe.set_user('Administrator')
        actor = frappe.get_doc('User', self.manager)
        actor.api_key, api_secret = uuid4().hex, uuid4().hex
        actor.api_secret = api_secret; actor.save()
        headers = {'Authorization': f'token {actor.api_key}:{api_secret}'}
        frappe.db.commit(); frappe.destroy()
        client = Client(application, Response)
        try:
            for path, method, body in (
                ('/api/resource/Cowork Companion Delivery/' + handle, 'GET', None),
                ('/api/resource/Cowork Companion Delivery', 'GET', None),
                ('/api/resource/Cowork Companion Delivery/' + handle, 'PUT', {'fence': 99}),
                ('/api/method/pearnly_cowork.companion.delivery_store.secret', 'POST', {}),
                ('/api/method/pearnly_cowork.companion.transport.lease', 'POST', {}),
                ('/api/method/pearnly_cowork.companion.transport.acknowledge', 'POST', {}),
            ):
                with self.subTest(path=path, method=method):
                    response = client.open(path, method=method, base_url='http://' + site,
                                           headers=headers, json=body)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn(secret, response.get_data(as_text=True))
        finally:
            connect(site)

    def test_generic_permissions_list_write_share_delete_and_rename_denied(self):
        from frappe import share
        _, handle = self.claimed()
        frappe.set_user('Administrator')
        for action in (
            lambda: self.deliveries.get(handle).check_permission('read'),
            lambda: frappe.get_list('Cowork Companion Delivery'),
            lambda: self.deliveries.get(handle).save(ignore_permissions=True),
            lambda: share.add('Cowork Companion Delivery', handle, self.actor, read=1),
            lambda: frappe.delete_doc('Cowork Companion Delivery', handle, ignore_permissions=True),
            lambda: frappe.rename_doc('Cowork Companion Delivery', handle, str(uuid4()), force=True),
        ):
            with self.subTest(action=action), self.assertRaises(frappe.PermissionError): action()
        self.assertTrue(frappe.db.exists('Cowork Companion Delivery', handle))

    def test_transport_is_not_a_public_rpc(self):
        for function in (self.transport.lease, self.transport.acknowledge, self.receipts.authenticate,
                         self.deliveries.get, self.deliveries.secret):
            with self.subTest(function=function.__name__), self.assertRaises(frappe.PermissionError):
                frappe.is_whitelisted(function)

    def test_forged_dispatch_context_and_receipt_context_rejected(self):
        name, handle = self.claimed()
        with self.assertRaises(frappe.ValidationError):
            self.transport.lease(replace(self.machine, _capability=None), {})
        context = self.receipts.authenticate(self.issued['token'], handle)
        for forged in (replace(context, _capability=None), self.machine,
                       replace(context, site='cowork-backend-b.localhost'),
                       replace(context, push_record='another-record'), replace(context, fence=context.fence + 1)):
            with self.subTest(forged=type(forged).__name__), self.assertRaises(frappe.AuthenticationError):
                self.transport.acknowledge(forged, {'log_id': handle, 'result': 'success'})
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'leased')

    def test_raw_record_id_random_delivery_wrong_secret_rejected(self):
        name, handle = self.claimed()
        for token, target in ((self.issued['token'], name), (self.issued['token'], str(uuid4())),
                              (self.issued['token'] + 'wrong', handle)):
            with self.subTest(target=target), self.assertRaises(frappe.AuthenticationError):
                self.receipts.authenticate(token, target)

    def test_success_minimal_reply_and_duplicate_failure_does_not_overwrite(self):
        name, handle = self.claimed()
        result = self.ack(handle, meta={'companion_version': 'synthetic', 'stage': 'failed'},
                          line_modes=[{'mode': 'direct', 'reason': 'synthetic', 'stkcod': 'SYNTH-STOCK'}])
        self.assertEqual(result, {'ok': True, 'status': 'success', 'express_docnum': 'SYNTHETIC-001'})
        row = frappe.get_doc(self.native.RECORD, name)
        stored = row.response_json
        self.assertEqual(json.loads(stored)['meta']['stage'], 'success')
        self.assertEqual(self.ack(handle, result='failed', error='late changed callback'),
                         {'ok': True, 'status': 'success', 'idempotent': True})
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'response_json'), stored)
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)

    def test_waiting_lock_new_fence_rejects_old_handle(self):
        name, handle = self.claimed()
        waiting = self.ack(handle, result='failed', outcome='waiting_lock')
        self.assertEqual(waiting, {'ok': True, 'status': 'pending', 'stage': 'waiting_lock', 'retry': True})
        self.assertEqual(self.ack(handle, result='failed', outcome='waiting_lock'), waiting)
        newer = self.take()['jobs'][0]['log_id']
        self.assertNotEqual(handle, newer)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'fence'), 2)
        with self.assertRaises(frappe.ValidationError): self.ack(handle)
        self.assertEqual(self.ack(newer)['status'], 'success')

    def test_definite_failure_exhaustion_and_duplicate_conflict(self):
        name, handle = self.claimed()
        for index in range(1, 4):
            result = self.ack(handle, result='failed', outcome='rolled_back', error='known restored')
            self.assertEqual(result['agent_failures'], index)
            self.assertEqual(result['attempt'], index + 1)
            self.assertEqual(result['status'], 'manual' if index == 3 else 'pending')
            self.assertEqual(self.ack(handle, result='failed', outcome='rolled_back', error='known restored'), result)
            with self.assertRaises(frappe.ValidationError):
                self.ack(handle, result='failed', outcome='rolled_back', error='different')
            if index < 3: handle = self.take()['jobs'][0]['log_id']
        self.assertEqual(self.take()['jobs'], [])
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'failure_count'), 3)

    def test_unknown_outcome_uses_source_fallback_and_needs_mapping_is_manual(self):
        _, handle = self.claimed()
        result = self.ack(handle, result='failed', outcome='unknown-future-stage')
        self.assertEqual(result['stage'], 'failed')
        handle = self.take()['jobs'][0]['log_id']
        result = self.ack(handle, result='success', outcome='needs_mapping')
        self.assertEqual(result, {'ok': True, 'status': 'manual', 'stage': 'needs_mapping'})

    def test_credential_reset_old_token_receipt_only_new_token_cannot_ack_old(self):
        _, handle = self.claimed()
        new = self.rotate()
        with self.assertRaises(frappe.AuthenticationError): self.take()
        with self.assertRaises(frappe.AuthenticationError): self.receipts.authenticate(new['token'], handle)
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_revoke_preserves_old_receipt_but_prevents_dispatch(self):
        _, handle = self.claimed()
        frappe.set_user(self.manager)
        self.credentials.revoke(self.connection['name'], uuid4().hex, 1)
        with self.assertRaises(frappe.AuthenticationError): self.take()
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_global_off_preserves_receipt_only(self):
        _, handle = self.claimed()
        frappe.conf.pearnly_cowork_erp_push_enabled = False
        with self.assertRaises(frappe.PermissionError): self.take()
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_issuer_disabled_preserves_receipt_only(self):
        _, handle = self.claimed()
        frappe.db.set_value('User', self.manager, 'enabled', 0, update_modified=False)
        frappe.clear_cache(user=self.manager)
        with self.assertRaises(frappe.AuthenticationError): self.take()
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_original_actor_revoked_before_claim_is_rejected(self):
        name = self.pending()
        self.revoke_actor()
        self.assertEqual(self.take()['jobs'], [])
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'manual')
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'reason'), 'companion.queue_access_revoked')
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 0)

    def test_original_actor_revoked_after_claim_preserves_receipt(self):
        _, handle = self.claimed(); self.revoke_actor()
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_connection_disabled_preserves_receipt_only(self):
        _, handle = self.claimed()
        frappe.db.set_value('Cowork ERP Connection', self.connection['name'], 'enabled', 0, update_modified=False)
        with self.assertRaises(frappe.AuthenticationError): self.take()
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_changed_history_version_blocks_dispatch_without_new_attempt(self):
        name = self.pending()
        pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'changed'
        self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        self.assertEqual(self.take()['jobs'], [])
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'reason'), 'push.native_history_changed')
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 0)

    def test_receipt_still_valid_after_history_edit(self):
        _, handle = self.claimed()
        pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'changed'
        self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_history_scope_change_rejects_receipt(self):
        name, handle = self.claimed()
        frappe.db.set_value('Cowork OCR History', self.history['name'], 'workspace', self.foreign, update_modified=False)
        with self.assertRaises(frappe.PermissionError): self.ack(handle)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'leased')

    def test_delivery_connection_scope_change_rejects_authentication(self):
        _, handle = self.claimed()
        frappe.db.set_value('Cowork ERP Connection', self.connection['name'], 'workspace', self.foreign, update_modified=False)
        with self.assertRaises(frappe.AuthenticationError): self.receipts.authenticate(self.issued['token'], handle)

    def test_expired_delivery_stays_unknown_and_late_ack_releases_lock(self):
        name, handle = self.claimed()
        row = frappe.get_doc(self.native.RECORD, name)
        future = row.lease_expires_at + timedelta(seconds=1)
        with patch.object(self.native, 'now_utc', return_value=future):
            self.assertEqual(self.take()['jobs'], [])
        row = frappe.get_doc(self.native.RECORD, name)
        self.assertEqual((row.status, row.result_unknown), ('manual', 1))
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), LOCKS)
        self.assertEqual(self.ack(handle)['status'], 'success')
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)

    def test_rotation_can_mark_old_lease_unknown_without_releasing_writer(self):
        name, handle = self.claimed()
        new = self.rotate(); self.machine = self.credentials.authenticate(new['token'])
        expires = frappe.db.get_value(self.native.RECORD, name, 'lease_expires_at')
        with patch.object(self.native, 'now_utc', return_value=expires + timedelta(seconds=1)):
            self.assertEqual(self.take()['jobs'], [])
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), LOCKS)
        self.assertEqual(self.ack(handle)['status'], 'success')

    def test_internal_sources_filtered_before_fifo_limit(self):
        first = self.pending()
        frappe.db.set_value('Cowork OCR History', self.history['name'], 'source', 'erp_web', update_modified=False)
        self.history = self.another_history(); self.key = uuid4().hex
        second = self.pending()
        result = self.take()
        delivery = self.deliveries.get(result['jobs'][0]['log_id'])
        self.assertEqual(delivery.push_record, second)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, first, 'status'), 'pending')

    def test_one_writer_leases_only_first_fifo_even_with_max50(self):
        first = self.pending()
        self.history = self.another_history(); self.key = uuid4().hex
        second = self.pending()
        frappe.db.set_value(self.native.RECORD, first, 'created_at', '2026-09-20 00:00:00', update_modified=False)
        handle = self.take()['jobs'][0]['log_id']
        self.assertEqual(self.deliveries.get(handle).push_record, first)
        self.assertEqual(self.take()['jobs'], [])
        self.ack(handle)
        handle = self.take()['jobs'][0]['log_id']
        self.assertEqual(self.deliveries.get(handle).push_record, second)

    def test_invalid_ack_does_not_change_leased_work(self):
        name, handle = self.claimed()
        for fields in ({'result': 'bad'}, {'line_modes': ['bad']}, {'meta': []}):
            with self.subTest(fields=fields), self.assertRaises(frappe.ValidationError): self.ack(handle, **fields)
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'leased')

    def test_ack_handle_must_match_receipt_capability(self):
        name, handle = self.claimed()
        context = self.receipts.authenticate(self.issued['token'], handle)
        with self.assertRaises(frappe.AuthenticationError):
            self.transport.acknowledge(context, {'log_id': str(uuid4()), 'result': 'success'})
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'leased')

    def test_mapping_insert_failure_rolls_back_encrypted_secret_and_lease(self):
        from pearnly_cowork.companion.delivery_documents import DeliveryDocument
        name = self.pending(); before = frappe.db.sql('SELECT COUNT(*) FROM `__Auth`')[0][0]
        def broken(doc, **kwargs):
            self.assertEqual(frappe.db.sql('SELECT COUNT(*) FROM `__Auth`')[0][0], before + 1)
            raise RuntimeError('synthetic mapping insertion failure')
        with patch.object(DeliveryDocument, 'db_insert', broken), self.assertRaises(RuntimeError): self.take()
        self.assert_pristine(name, before)

    def test_password_encryption_failure_rolls_back_lease(self):
        name = self.pending(); before = frappe.db.sql('SELECT COUNT(*) FROM `__Auth`')[0][0]
        with patch('frappe.utils.password.set_encrypted_password', side_effect=RuntimeError('synthetic encryption failure')):
            with self.assertRaises(RuntimeError): self.take()
        self.assert_pristine(name, before)

    def test_response_or_refresh_failure_rolls_back_mapping_secret_and_lease(self):
        name = self.pending(); before = frappe.db.sql('SELECT COUNT(*) FROM `__Auth`')[0][0]
        for target in ('pearnly_core.cc_desktop.wire.queue_contracts.job', 'pearnly_cowork.connections.refresh.lease'):
            with self.subTest(target=target), patch(target, side_effect=RuntimeError('synthetic tail failure')):
                with self.assertRaises(RuntimeError): self.take()
            self.assert_pristine(name, before)

    def test_empty_queue_still_offers_native_directory_refresh(self):
        frappe.set_user(self.manager)
        self.connections.request_refresh(self.connection['name'], uuid4().hex)
        result = self.take()
        self.assertEqual(result['jobs'], [])
        self.assertEqual(result['master_refresh']['scope_kind'], 'endpoint')
        self.assertNotIn('attempt_token', json.dumps(result))

    def test_stale_head_is_quarantined_and_following_valid_work_is_leased(self):
        first = self.pending()
        pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'stale'
        self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        self.history = self.another_history(); self.key = uuid4().hex
        second = self.pending()
        frappe.db.set_value(self.native.RECORD, first, 'created_at', '2026-09-20 00:00:00', update_modified=False)
        # 领取 v2 每份账套只看排头：这一次把失效的排头转人工（不再整条连接停下），同一份账套的下一张下一次领到
        self.assertEqual(self.take()['jobs'], [])
        self.assertEqual(frappe.db.get_value(self.native.RECORD, first, 'status'), 'manual')
        handle = self.take()['jobs'][0]['log_id']
        self.assertEqual(self.deliveries.get(handle).push_record, second)
        self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': first}), 0)
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': second}), LOCKS)

    def test_quarantine_cannot_change_unknown_leased_work_or_release_locks(self):
        name, _ = self.claimed()
        row = frappe.get_doc(self.native.RECORD, name)
        context = FIXTURE['context_from_row'](row)
        for reason in ('push.native_history_changed', 'made-up-reason'):
            with self.subTest(reason=reason), self.assertRaises(frappe.ValidationError):
                self.native.quarantine_pending(name, context=context, adapter=self.adapter, reason=reason)
        self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), LOCKS)

    def test_finished_ack_after_quarantine_remains_accepted_without_requeue(self):
        for outcome in ('waiting_lock', 'failed'):
            with self.subTest(outcome=outcome):
                self.history = self.another_history(); self.key = uuid4().hex
                name, handle = self.claimed()
                self.assertEqual(self.ack(handle, result='failed', outcome=outcome)['status'], 'pending')
                row = frappe.get_doc(self.native.RECORD, name)
                saved = frappe.db.get_value(self.native.ATTEMPT, row.active_attempt, 'ack_digest')
                pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'changed-after-ack'
                self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
                self.assertEqual(self.take()['jobs'], [])
                self.assertEqual(self.ack(handle, result='failed', outcome=outcome),
                                 {'ok': True, 'status': 'manual', 'idempotent': True})
                self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'manual')
                self.assertEqual(frappe.db.get_value(self.native.ATTEMPT, row.active_attempt, 'ack_digest'), saved)
                self.assertEqual(frappe.db.count(self.native.WRITER_LOCK, {'holder': name}), 0)

    def test_database_failure_is_not_misclassified_as_invalid_work(self):
        name = self.pending()
        with patch.object(self.native, 'lease', side_effect=RuntimeError('synthetic database failure')):
            with self.assertRaises(RuntimeError): self.take()
        self.assertEqual(frappe.db.get_value(self.native.RECORD, name, 'status'), 'pending')

    def test_concurrent_old_snapshots_create_one_delivery_and_one_secret(self):
        name = self.pending(); frappe.db.commit()
        workers = [subprocess.Popen([sys.executable, __file__, '--worker', self.actor],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for process in workers: self.assertEqual(process.stdout.readline().strip(), 'READY')
            for process in workers:
                # Synthetic token travels only in the local pipe, never argv/log.
                process.stdin.write(json.dumps({'token': self.issued['token']}) + '\n'); process.stdin.flush()
            outputs = [process.communicate(timeout=45) for process in workers]
            for process, (output, error) in zip(workers, outputs):
                self.assertEqual(process.returncode, 0, output + error)
            values = [json.loads(output.strip().splitlines()[-1]) for output, _ in outputs]
            self.assertEqual(sum(value['jobs'] for value in values), 1)
            frappe.db.rollback()
            self.assertEqual(frappe.db.count(self.native.ATTEMPT, {'push_record': name}), 1)
            self.assertEqual(frappe.db.count('Cowork Companion Delivery', {'push_record': name}), 1)
            delivery = frappe.db.get_value('Cowork Companion Delivery', {'push_record': name}, 'name')
            self.assertEqual(len(frappe.db.sql('SELECT name FROM `__Auth` WHERE doctype=%s AND name=%s',
                ('Cowork Companion Delivery', delivery))), 1)
        finally:
            for process in workers:
                if process.poll() is None: process.kill(); process.communicate()


def worker():
    from pearnly_cowork.companion import credentials, transport
    frappe.set_user(sys.argv[2]); frappe.conf.pearnly_cowork_erp_push_enabled = True
    frappe.db.count('Cowork Push Record')
    print('READY', flush=True)
    token = json.loads(sys.stdin.readline())['token']
    for attempt in range(3):
        try:
            result = transport.lease(credentials.authenticate(token), {})
            frappe.db.commit()
            print(json.dumps({'jobs': len(result['jobs']), 'local_retries': attempt}), flush=True)
            return
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2: raise


if __name__ == '__main__':
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--worker':
            worker()
        else:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(CompanionTransport)
            outcome = unittest.TextTestRunner(verbosity=2).run(suite)
            raise SystemExit(0 if outcome.wasSuccessful() else 1)
    finally:
        frappe.db.rollback()
        frappe.destroy()
