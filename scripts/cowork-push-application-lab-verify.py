"""Synthetic native prepare -> frozen ledger and immutable PRN integration.

Only guarded offline Cowork lab. No ERP/Companion/LINE IO or model spending.
Roundtrip uses existing fake billing and transport with real Source/Batch rows.
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

BASE = runpy.run_path(str(Path(__file__).with_name('cowork-backend-lab-verify.py')))
connect, user, grant = (BASE[key] for key in ('connect', 'user', 'grant'))
ROUNDTRIP = runpy.run_path(str(Path(__file__).with_name('cowork-roundtrip-lab-verify.py')))
RT = ROUNDTRIP['NativeRoundtrip']
CONFIG = {'account_set': 'DATAT', 'account_dir': 'DATAT', 'express_root': r'D:\EXP',
          'fallback_acc': 'A', 'vat_input_acc': 'V', 'ap_acc': 'P',
          'revenue_acc': 'S', 'vat_output_acc': 'O', 'ar_acc': 'R'}
FIELDS = {'direction': 'purchase', 'seller_name': 'Synthetic seller', 'seller_tax': '0107561000013',
          'buyer_tax': '0994000333444', 'buyer_name': 'Synthetic buyer', 'subtotal': '100',
          'vat': '7', 'total_amount': '107', 'date': '2026-09-21',
          'document_type': 'tax_invoice', 'invoice_number': 'CW-SYNTHETIC-001'}


class NativePreparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api
        suffix = uuid4().hex[:10]
        cls.actor, cls.other, cls.viewer, cls.manager = [f'cw-push-{role}-{suffix}@example.invalid' for role in ('actor', 'other', 'viewer', 'manager')]
        user(cls.actor, ['Cowork Intake', 'Cowork Push Log'])
        user(cls.other, ['Cowork Intake', 'Cowork Push Log'])
        user(cls.viewer, ['Cowork Push Log'])
        user(cls.manager, ['Cowork Admin'])
        cls.workspace = api.create_workspace('Synthetic push application ' + suffix, '0994000333444')['name']
        cls.foreign = api.create_workspace('Synthetic push foreign ' + suffix)['name']
        cls.permission = grant(cls.actor, 'Cowork Workspace', cls.workspace).name
        grant(cls.other, 'Cowork Workspace', cls.foreign)
        grant(cls.viewer, 'Cowork Workspace', cls.workspace)
        frappe.db.commit()

    def setUp(self):
        from pearnly_cowork.connections import service
        from pearnly_cowork.history import storage
        from pearnly_cowork.push.application import api
        self.connections, self.storage, self.api = service, storage, api
        self.old_conf = deepcopy(dict(frappe.conf))
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        frappe.set_user(self.manager)
        self.connection = service.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic Express', CONFIG, users=[self.actor, self.manager])
        self.install()
        frappe.set_user(self.actor)
        pages = [{'page_index': 1, 'fields': deepcopy(FIELDS)}]
        self.history = storage.create_recognized(self.workspace, pages, uuid4().hex,
            source='manual', confidence='high', ai_raw=deepcopy(pages))
        self.key = uuid4().hex

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.clear(); frappe.conf.update(self.old_conf)
        frappe.set_user('Administrator'); frappe.clear_cache()

    def install(self, complete=True):
        refresh = self.connections.request_refresh(self.connection['name'], uuid4().hex)
        observation = {'adapter': 'express', 'observed_at': '2026-09-21T00:00:00Z',
            'collector': {'kind': 'synthetic_lab'},
            'account_sets': [{'source_id': 'DATAT', 'label': 'Synthetic DATAT',
                'attributes': {'root': r'D:\EXP', 'account_dir': 'DATAT'}}]}
        prep = {'complete': complete, 'account_set': 'DATAT', 'root_key': r'D:\EXP',
            'mappings': {'accounts': [], 'clients': []}, 'supplier_profiles': {}, 'bank_index': [], 'reported_config': {}}
        return self.connections.install_snapshot(self.connection['name'], observation,
            refresh['request_id'], self.connection['revision'], prep)

    def prepare(self, **changes):
        args = {'history': self.history['name'], 'connection': self.connection['name'],
            'request_key': self.key, 'expected_history_version': self.history['record_version'],
            'expected_connection_revision': self.connection['revision']}
        args.update(changes)
        return self.api.prepare(**args)

    def test_ready_is_prepared_not_external_success(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'prepared', result)
        self.assertFalse(result['external_reference']['external_doc_no'])
        self.assertNotIn('frozen_payload', result)
        self.assertNotIn('frozen_target', result)
        doc = frappe.get_doc('Cowork Push Record', result['name'])
        payload = self.storage.parse(doc.payload_json)
        self.assertEqual(payload['account_set'].lower(), 'datat')
        self.assertEqual(payload['account_root'], r'D:\EXP')
        self.assertEqual(doc.history_version, self.history['record_version'])
        self.assertEqual(frappe.db.count('Cowork Push Attempt', {'push_record': doc.name}), 0)
        self.assertEqual(frappe.db.count('Pearnly Push Log'), 0)

    def test_same_request_replays_frozen_payload_after_edit_and_reconfigure(self):
        first = self.prepare()
        frozen = frappe.db.get_value('Cowork Push Record', first['name'], 'payload_json')
        pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'EDITED-LATER'
        self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        frappe.set_user(self.manager)
        self.connections.configure(self.workspace, 'express', uuid4().hex, 'Synthetic changed',
            {**CONFIG, 'fallback_acc': 'CHANGED'}, users=[self.actor, self.manager],
            name=self.connection['name'], expected_revision=self.connection['revision'])
        frappe.set_user(self.actor)
        with patch('pearnly_cowork.push.application.service.prepare_express', side_effect=AssertionError('No regeneration')):
            replay = self.prepare()
        self.assertEqual(replay['name'], first['name'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(frappe.db.get_value('Cowork Push Record', first['name'], 'payload_json'), frozen)

    def test_same_key_changed_intent_is_rejected(self):
        first = self.prepare()
        with self.assertRaises(frappe.ValidationError):
            self.prepare(posting_kind='stock')
        self.assertEqual(frappe.db.count('Cowork Push Record', {'history': self.history['name']}), 1)
        self.assertEqual(frappe.db.get_value('Cowork Push Record', first['name'], 'status'), 'prepared')

    def test_other_key_same_invoice_does_not_create_another_preparation(self):
        first = self.prepare()
        other = self.prepare(request_key=uuid4().hex)
        self.assertEqual(other['name'], first['name'])
        self.assertEqual(frappe.db.count('Cowork Push Record', {'history': self.history['name']}), 1)

    def test_record_and_connection_versions_are_checked(self):
        for values in ({'expected_history_version': 99}, {'expected_connection_revision': 99}):
            with self.subTest(values=values), self.assertRaises(frappe.TimestampMismatchError):
                self.prepare(**values)
        self.assertEqual(frappe.db.count('Cowork Push Record', {'history': self.history['name']}), 0)

    def test_failed_native_collection_cannot_be_masked_by_legacy_complete_flag(self):
        from pearnly_cowork.masters import push_inputs
        frappe.set_user(self.manager); self.install(complete=True); frappe.set_user(self.actor)
        with patch.object(push_inputs, 'mappings', side_effect=RuntimeError('synthetic unavailable')):
            with self.assertRaises(RuntimeError): self.prepare()
        self.assertEqual(frappe.db.count('Cowork Push Record', {'history': self.history['name']}), 0)

    def test_declared_disabled_is_not_prepared(self):
        frappe.conf.pearnly_cowork_erp_push_enabled = False
        self.assertEqual(self.prepare()['status'], 'disabled')
        self.assertEqual(frappe.db.count('Cowork Push Record', {'history': self.history['name']}), 0)

    def test_mutable_history_id_does_not_redirect_prior_lookup(self):
        pages = deepcopy(self.history['pages']); pages[0]['fields']['history_id'] = 'FOREIGN-FORGED-HISTORY'
        self.history = self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        result = self.prepare()
        frozen = self.storage.parse(frappe.db.get_value('Cowork Push Record', result['name'], 'target_json'))
        self.assertEqual(frozen['original_history'], self.history['name'])

    def test_scope_and_grant_revocation_close_replay(self):
        first = self.prepare()
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError): self.prepare()
        with self.assertRaises(frappe.PermissionError): self.api.detail(first['name'])
        frappe.set_user('Administrator')
        frappe.db.delete('User Permission', {'name': self.permission})
        frappe.set_user(self.actor)
        with self.assertRaises(frappe.PermissionError): self.prepare()

    def test_current_log_grant_checks_and_no_payload_leak(self):
        first = self.prepare()
        detail = self.api.detail(first['name'])
        self.assertEqual(detail['name'], first['name'])
        self.assertNotIn('frozen_payload', detail)
        self.assertNotIn('response_body', detail)
        self.assertEqual(len(self.api.records(self.connection['name'])['records']), 1)
        frappe.set_user(self.viewer)
        with self.assertRaises(frappe.PermissionError): self.api.detail(first['name'])
        with self.assertRaises(frappe.PermissionError): self.prepare()

    def test_manual_preflight_never_promotes_to_ready(self):
        pages = deepcopy(self.history['pages']); pages[0]['fields']['currency'] = 'USD'
        self.history = self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        result = self.prepare()
        self.assertEqual(result['status'], 'manual', result)
        self.assertTrue(result['reason'])

    def test_changed_invoice_can_cancel_never_sent_preparation_and_prepare_again(self):
        first = self.prepare()
        frozen = frappe.db.get_value('Cowork Push Record', first['name'], 'payload_json')
        pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'EDITED-NEW'
        self.history = self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        cancelled = self.api.cancel_prepared(first['name'], first['revision'])
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(frappe.db.get_value('Cowork Push Record', first['name'], 'payload_json'), frozen)
        second = self.prepare(request_key=uuid4().hex)
        self.assertEqual(second['status'], 'prepared')
        self.assertNotEqual(second['name'], first['name'])

    def test_completed_ack_meta_docnum_is_used_for_prior_lookup(self):
        from pearnly_cowork.push.native import service as native
        from pearnly_cowork.push.express.prepare import prepare_express
        result = self.prepare()
        record = frappe.get_doc('Cowork Push Record', result['name'])
        context = native.ReservationContext(self.actor, self.workspace, '', self.history['name'],
            self.history['record_version'], self.connection['name'], record.account_key,
            connection_revision=self.connection['revision'])
        adapter = native.trusted_adapter_context(self.connection['name'], 'synthetic-only')
        native.activate(record.name, context=context, adapter=adapter)
        lease = native.lease(record.name, context=context, adapter=adapter)
        native.ack(record.name, context=context, adapter=adapter, fence=lease['fence'],
            attempt_token=lease['attempt_token'], success=True, meta={'docnum': 'META-123'})
        with patch('pearnly_cowork.push.application.service.prepare_express', wraps=prepare_express) as prepare:
            again = self.prepare(request_key=uuid4().hex)
        self.assertEqual(prepare.call_args.kwargs['context'].prior_docnum, 'META-123')
        self.assertEqual(again['blocked_reason'], 'already_success')
        self.assertEqual(frappe.db.count('Cowork Push Record', {'history': self.history['name']}), 1)

    def test_tax_anchor_is_current_after_another_transaction_changes_company(self):
        pages = deepcopy(self.history['pages']); pages[0]['fields'].pop('direction')
        self.history = self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        frappe.db.set_value('Cowork Workspace', self.workspace, 'tax_id', FIELDS['seller_tax'])
        frappe.db.commit()
        self.assertEqual(frappe.db.get_value('Cowork Workspace', self.workspace, 'tax_id'), FIELDS['seller_tax'])
        subprocess.run([sys.executable, __file__, '--change-synthetic-tax', self.workspace], check=True, timeout=30)
        # Demonstrate that an ordinary RR read really is stale here.
        self.assertEqual(frappe.db.get_value('Cowork Workspace', self.workspace, 'tax_id'), FIELDS['seller_tax'])
        result = self.prepare()
        doc = frappe.get_doc('Cowork Push Record', result['name'])
        self.assertEqual(self.storage.parse(doc.payload_json)['direction'], 'purchase')
        self.assertEqual(self.storage.parse(doc.target_json)['own_tax_id'], FIELDS['buyer_tax'])

    def test_http_prepare_status_cancel_and_native_bypass_guards(self):
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        from frappe.app import application
        frappe.set_user('Administrator')
        actor = frappe.get_doc('User', self.actor)
        actor.api_key, secret = uuid4().hex, uuid4().hex
        actor.api_secret = secret; actor.save()
        headers = {'Authorization': f'token {actor.api_key}:{secret}'}
        site = frappe.local.site
        config_path = Path(frappe.get_site_path('site_config.json'))
        original_config = config_path.read_text()
        config = json.loads(original_config)
        self.assertTrue(config['pearnly_backend_lab'])
        config['pearnly_cowork_erp_push_enabled'] = True
        config_path.write_text(json.dumps(config))
        data = {'history': self.history['name'], 'connection': self.connection['name'],
            'request_key': self.key, 'expected_history_version': self.history['record_version'],
            'expected_connection_revision': self.connection['revision']}
        frappe.db.commit(); frappe.destroy()
        client = Client(application, Response)
        base = 'http://' + site
        rpc = '/api/method/pearnly_cowork.push.application.api.'
        try:
            result = client.post(rpc + 'prepare', base_url=base, headers=headers, json=data)
            self.assertEqual(result.status_code, 200)
            first = result.json['message']
            self.assertEqual(first['status'], 'prepared')
            read = client.get(rpc + 'detail?name=' + first['name'], base_url=base, headers=headers)
            self.assertEqual(read.status_code, 200)
            self.assertNotIn('payload_json', read.get_data(as_text=True))
            self.assertEqual(client.get(rpc + 'prepare', base_url=base, headers=headers).status_code, 403)
            self.assertEqual(client.post(rpc + 'prepare', base_url=base, json=data).status_code, 403)
            self.assertEqual(client.get('/api/resource/Cowork Push Record/' + first['name'], base_url=base,
                                       headers=headers).status_code, 403)
            self.assertEqual(client.put('/api/resource/Cowork Push Record/' + first['name'], base_url=base,
                                       headers=headers, json={'status': 'success'}).status_code, 403)
            cancelled = client.post(rpc + 'cancel_prepared', base_url=base, headers=headers,
                json={'name': first['name'], 'expected_revision': first['revision']})
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json['message']['status'], 'cancelled')
        finally:
            config_path.write_text(original_config)
            connect(site)


class NativePushProvenance(unittest.TestCase):
    setUpClass = classmethod(RT.setUpClass.__func__)
    setUp, tearDown = RT.setUp, RT.tearDown
    prior, row, upload, admit, run_work = RT.prior, RT.row, RT.upload, RT.admit, RT.run_work

    def imported(self):
        from pearnly_cowork.roundtrip import spec as rt
        prior = self.prior()
        self.upload(ROUNDTRIP['book']([(rt.SHEET_SALES, [self.row(prior)])]))
        result = self.run_work(self.admit())
        self.assertEqual(result['state'], 'saved')
        return prior, frappe.get_doc('Cowork OCR History', result['history_records'][0])

    def test_real_roundtrip_uses_original_manifest_even_after_edit(self):
        from pearnly_cowork.push.application.provenance import original_history
        from pearnly_cowork.history import storage
        prior, doc = self.imported()
        self.assertEqual(original_history(doc), prior['name'])
        pages = storage.parse(doc.pages); pages[0]['fields']['history_id'] = 'FORGED-UNRELATED'
        changed = storage.update_pages(doc.name, pages, doc.record_version)
        self.assertEqual(original_history(frappe.get_doc('Cowork OCR History', changed['name'])), prior['name'])
        self.assertEqual(self.http.call_count, 0)

    def test_missing_batch_or_tampered_source_relationship_is_rejected(self):
        from pearnly_cowork.push.application.provenance import original_history
        prior, doc = self.imported()
        # Deliberate lab corruption, not a service capability exposed to users.
        batch = frappe.get_value('Cowork OCR Batch', {'source_file': doc.source_file}, 'name')
        frappe.db.set_value('Cowork OCR Batch', batch, 'history_records', '[]', update_modified=False)
        with self.assertRaises(frappe.PermissionError): original_history(doc)

    def test_original_reference_is_reauthorized_after_revocation(self):
        from pearnly_cowork.push.application.provenance import original_history
        from pearnly_cowork.history import storage
        prior, doc = self.imported()
        actual = storage.read
        def revoke(name, **kw):
            if name == prior['name']: raise frappe.PermissionError('Synthetic revoked original')
            return actual(name, **kw)
        with patch.object(storage, 'read', side_effect=revoke), self.assertRaises(frappe.PermissionError):
            original_history(doc)


if __name__ == '__main__':
    connect()
    try:
        if len(sys.argv) == 3 and sys.argv[1] == '--change-synthetic-tax':
            target = frappe.get_doc('Cowork Workspace', sys.argv[2])
            assert target.display_name.startswith('Synthetic push application ')
            frappe.db.set_value('Cowork Workspace', target.name, 'tax_id', FIELDS['buyer_tax'])
            frappe.db.commit()
            raise SystemExit(0)
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                                   for cls in (NativePreparation, NativePushProvenance))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
