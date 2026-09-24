"""Grouped collector orchestration in the pinned runtime; no Site or real IO.

Native client protocol has its own fake-transport suite. Here DB/publication,
session entry and catalogue work are explicit substitutes, including failures.
"""
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from pathlib import Path
import runpy
import socket
import time
import unittest
from unittest.mock import Mock, patch

import frappe
from pearnly_dms.adapter import snapshot_sync as sync
from pearnly_dms.client.snapshot_reader import collect_vehicle_options, collect_company_options
from pearnly_dms.contracts.client_base import DmsClientError

fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/test-dms-snapshot-reader.py'))


class OptionsTests(unittest.TestCase):
    def setUp(self):
        self.transport = fixture['Transport']()
        self.client = fixture['DmsClient'](self.transport, 'https://snapshot.invalid/dms/')
        self.raw = {'car': {cid: {'fields': {'chkcarpaint[]': ['COLOR-' + cid]}}
                            for cid in ('CAR1', 'CAR2')},
                    'carpaint': {'COLOR-CAR1': {}, 'COLOR-CAR2': {}, 'UNASSOCIATED': {}}}

    def test_vehicle_projection_keeps_raw_unassociated_and_cancelled_records(self):
        self.raw['car']['CANCELLED'] = {'fields': {'chkcarpaint[]': []}}
        before = deepcopy(self.raw)
        data = collect_vehicle_options(self.client, catalogues=self.raw)
        self.assertEqual([row[0] for row in data['masters']['cars']], ['CAR1', 'CAR2'])
        self.assertEqual(set(data['paints']), {'CAR1', 'CAR2'})
        self.assertEqual(self.raw, before)
        self.assertFalse(any(path.startswith(('cus/', 'users/', 'bank/')) for path, _ in self.transport.calls))

    def test_removed_car_or_colour_relationship_is_not_published(self):
        for key in ('car', 'colour'):
            with self.subTest(key=key):
                raw = deepcopy(self.raw)
                if key == 'car':
                    del raw['car']['CAR1']
                else:
                    raw['car']['CAR1']['fields']['chkcarpaint[]'] = []
                with self.assertRaises(DmsClientError):
                    collect_vehicle_options(self.client, catalogues=raw)

    def test_company_projection_has_no_vehicle_or_customer_scan(self):
        raw = {'branch': {'1': {}}, 'bank': {'1': {}}, 'users': {
            '1': {'fields': {'txtuserscode': 'SALES-CODE', 'txtusers': 'login-distinct', 'txtusersname': 'Synthetic'}}}}
        result = collect_company_options(self.client, catalogues=raw, bank_reader=lambda: fixture['BANKS'])
        self.assertEqual(result['masters']['employees'][0]['login'], 'login-distinct')
        self.assertNotIn('cars', result['masters'])
        self.assertNotIn('prefixes', result['masters'])
        self.assertFalse(any(path.startswith(('cus/', 'car/')) for path, _ in self.transport.calls))


class GroupedSyncTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        frappe.local.db = Mock()
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')))
        self.stack.enter_context(patch.object(sync.manual, 'assert_active'))
        self.stack.enter_context(patch.object(sync.manual, 'publish_guard', side_effect=lambda *a: nullcontext()))
        self.stack.enter_context(patch.object(sync.store, 'scope', return_value='scope'))
        self.stage = self.stack.enter_context(patch.object(sync.store, 'stage', return_value=None))
        self.saved = self.stack.enter_context(patch.object(sync.store, 'checkpoint'))
        self.published = self.stack.enter_context(patch.object(sync.store, 'publish'))
        self.failed = self.stack.enter_context(patch.object(sync.store, 'failed'))
        self.session = self.stack.enter_context(patch.object(sync, 'run_logged_in',
            side_effect=lambda ep, callback, **kw: callback(Mock(), Mock())))

    def test_old_groups_and_scheduled_jobs_never_log_in(self):
        for dataset, request in (('directory', None), ('masters', 'id'), ('vehicles', None)):
            self.assertTrue(sync.refresh('ep', dataset=dataset, request_id=request)['retired'])
        self.session.assert_not_called()

    def test_each_group_collects_its_own_native_tables_before_projection(self):
        for dataset, source in (('vehicles', 'vehicles'), ('company', 'company'), ('customers', 'customer_aux')):
            with self.subTest(dataset=dataset), patch.object(sync, 'start_catalogues', return_value={}) as start, \
                    patch.object(sync, 'advance_catalogues', return_value={'done': True, 'processed': 3, 'total': 3}):
                self.assertFalse(sync.refresh('ep', dataset=dataset, request_id='id')['complete'])
                self.assertEqual(start.call_args.args[1], source)
                self.assertEqual(self.saved.call_args.args[1]['phase'], 'projection')
                self.published.assert_not_called()

    def test_finished_vehicle_form_batch_publishes_raw_and_native_options_together(self):
        self.stage.return_value = {'version': 2, 'started_at': time.time(), 'phase': 'projection',
            'tree': {'data': {'catalogues': {'car': {}, 'carpaint': {}}}, 'counts': {'car': 0},
                     'done': True, 'dataset': 'vehicles'}}
        with patch.object(sync, 'collect_vehicle_options', return_value={'masters': {'cars': []}, 'paints': {}}), \
                patch.object(sync, 'advance_catalogues', return_value=self.stage.return_value['tree']):
            self.assertTrue(sync.refresh('ep', dataset='vehicles', request_id='id')['complete'])
        self.assertEqual(set(self.published.call_args.args[2]), {'catalogues', 'masters', 'paints'})
        self.assertIsNone(self.saved.call_args.args[1])

    def test_invalid_wrapper_restarts_without_using_old_three_group_checkpoint(self):
        for checkpoint in ([], {'version': 1}, {'version': 2, 'started_at': float('nan'),
                            'phase': 'projection', 'tree': {}}):
            self.stage.return_value = checkpoint
            with patch.object(sync, 'start_catalogues', return_value={}) as start, \
                    patch.object(sync, 'advance_catalogues', return_value={'done': False, 'processed': 0, 'total': None}):
                self.assertFalse(sync.refresh('ep', dataset='vehicles', request_id='id')['complete'])
                start.assert_called_once()
                self.published.assert_not_called()

    def test_incomplete_failure_discards_staging_but_never_publishes(self):
        self.session.side_effect = None
        self.session.return_value = {'ok': False, 'error_code': 'ERR_DMS_SNAPSHOT_INCOMPLETE'}
        for dataset in ('vehicles', 'company', 'customers'):
            self.assertFalse(sync.refresh('ep', dataset=dataset, request_id='id')['ok'])
            self.assertIsNone(self.saved.call_args.args[1])
            self.assertEqual(self.failed.call_args.args[1], dataset)
        self.published.assert_not_called()

    def test_busy_retains_private_checkpoint_and_last_good_generation(self):
        self.session.side_effect = None
        self.session.return_value = {'ok': False, 'error_code': 'ERR_DMS_SNAPSHOT_BUSY'}
        self.assertTrue(sync.refresh('ep', dataset='company', request_id='id')['busy'])
        self.saved.assert_not_called(); self.published.assert_not_called(); self.failed.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
