"""Credential lifecycle in a dedicated lab Site; synthetic secrets never printed.

Since ADR-021 the management RPCs issue / revoke / status are retired: this drives the internal functions through the
same local-transaction wrapper `register` uses, and checks the old RPC paths no longer answer. D1: an entry-role user
with the use grant may issue (own computer); revoke / status stay management-only.

Writes labelled local fixtures and, for concurrency/WSGI, commits them. No ERP,
model, LINE, identity provider, heartbeat, sockets or external transport calls.
Root owns Site installation and execution; this script never migrates a Site.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-connections-lab-verify.py')))
BASE = FIXTURE['NativeConnections']
connect = FIXTURE['BASE']['connect']


class NativeCredentials(unittest.TestCase):
    setUpClass = classmethod(BASE.setUpClass.__func__)
    tearDownClass = classmethod(BASE.tearDownClass.__func__)

    def setUp(self):
        from pearnly_cowork.companion import api, credentials, contracts, documents
        from pearnly_cowork.connections import service as connections
        self.api, self.service, self.contracts, self.documents = api, credentials, contracts, documents
        self.connections = connections
        self.old_conf = deepcopy(dict(frappe.conf))
        frappe.conf.pearnly_features = ['intake', 'master_data', 'push_log']
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        frappe.set_user(self.manager)
        self.connection = connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic credential', {}, users=[self.manager, self.actor])
        self.key = uuid4().hex

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.clear(); frappe.conf.update(self.old_conf)
        frappe.set_user('Administrator'); frappe.clear_cache()

    def issue(self, **values):
        return self.api._local_transaction(self.service.issue, self.connection['name'],
                                           values.pop('request_key', self.key), **values)

    def status(self, connection):
        return self.service.status(connection)

    def revoke(self, *args):
        return self.service.revoke(*args)

    def row(self, value):
        return frappe.get_doc(self.documents.DOCTYPE, value['credential_id'])

    def update_connection(self, **values):
        args = {'workspace': self.workspace, 'adapter': 'express', 'request_key': uuid4().hex,
                'label': 'Synthetic credential changed', 'config': {},
                'users': [self.manager, self.actor], 'name': self.connection['name'],
                'expected_revision': self.connection['revision']}
        args.update(values)
        self.connection = self.connections.configure(**args)

    def test_issue_hash_only_and_default_exists_and_replay_never_return_plaintext(self):
        before = frappe.db.count('User')
        value = self.issue(); row = self.row(value)
        self.assertEqual((value['status'], value['generation'], value['state']), ('created', 1, 'active'))
        self.assertEqual(row.token_digest, self.contracts.token_digest(value['token']))
        self.assertNotIn(value['token'], json.dumps(row.as_dict(), default=str))
        for again in (self.issue(), self.issue(request_key=uuid4().hex), self.status(row.connection)):
            self.assertNotIn('token', again); self.assertNotIn('token_digest', again)
            self.assertEqual(again['credential_id'], row.name)
        self.assertEqual(frappe.db.count(self.documents.DOCTYPE, {'connection': row.connection}), 1)
        self.assertEqual(frappe.db.count('User'), before)
        self.assertTrue(frappe.local.cowork_private_response)

    def test_reset_cas_invalidates_old_token_and_old_request_replay_does_not_rotate(self):
        old = self.issue(); old_context = self.service.authenticate(old['token'])
        key = uuid4().hex
        new = self.issue(request_key=key, reset=True, expected_generation=1)
        self.assertEqual(new['generation'], 2)
        for action in (lambda: self.service.authenticate(old['token']), lambda: self.service.revalidate(old_context)):
            with self.assertRaises(frappe.AuthenticationError): action()
        self.assertEqual(self.service.authenticate(new['token']).token_generation, 2)
        self.assertNotIn('token', self.issue(request_key=key, reset=True, expected_generation=1))
        original = self.issue(); self.assertEqual(original['state'], 'revoked')
        self.assertNotIn('token', original)
        with self.assertRaises(frappe.ValidationError): self.issue(request_key=uuid4().hex, reset=True, expected_generation=1)
        with self.assertRaises(frappe.ValidationError): self.issue(reset=True, expected_generation=2)
        self.assertEqual(frappe.db.count(self.documents.DOCTYPE, {'connection': self.connection['name']}), 2)

    def test_revoke_replay_and_new_generation_preserve_old_receipts(self):
        old = self.issue(); key = uuid4().hex
        result = self.revoke(self.connection['name'], key, 1, 'synthetic retirement')
        self.assertEqual(result['state'], 'revoked')
        with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
        again = self.revoke(self.connection['name'], key, 1, 'synthetic retirement')
        self.assertTrue(again['replayed'])
        new = self.issue(request_key=uuid4().hex)
        self.assertEqual(new['generation'], 2)
        self.assertTrue(self.revoke(self.connection['name'], key, 1, 'synthetic retirement')['replayed'])
        self.assertEqual(self.status(self.connection['name'])['generation'], 2)
        with self.assertRaises(frappe.ValidationError): self.revoke(self.connection['name'], uuid4().hex, 1)

    def test_current_explicit_grant_required_even_for_admin(self):
        old = self.issue(); context = self.service.authenticate(old['token'])
        self.update_connection(users=[self.actor])
        with self.assertRaises(frappe.PermissionError): self.issue(request_key=uuid4().hex)
        with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
        with self.assertRaises(frappe.AuthenticationError): self.service.revalidate(context)
        self.assertEqual(self.revoke(self.connection['name'], uuid4().hex, 1)['state'], 'revoked')

    def test_disabled_connection_prevents_issue_auth_but_can_be_revoked(self):
        old = self.issue(); self.update_connection(enabled=False)
        with self.assertRaises(frappe.PermissionError): self.issue(request_key=uuid4().hex)
        with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
        self.assertFalse(self.status(self.connection['name'])['connection_enabled'])
        self.assertEqual(self.revoke(self.connection['name'], uuid4().hex, 1)['state'], 'revoked')

    def test_disabled_issuer_and_removed_role_are_current_not_cached(self):
        old = self.issue(); context = self.service.authenticate(old['token'])
        frappe.db.set_value('User', self.manager, 'enabled', 0, update_modified=False)
        with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
        with self.assertRaises(frappe.AuthenticationError): self.service.revalidate(context)
        frappe.db.set_value('User', self.manager, 'enabled', 1, update_modified=False)
        frappe.db.delete('Has Role', {'parent': self.manager, 'role': ('in', ('Cowork Admin', 'Cowork Intake'))})
        with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
        with self.assertRaises(frappe.PermissionError): self.status(self.connection['name'])

    def test_company_client_and_entitlements_are_current(self):
        self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic credential client', {}, client=self.client, users=[self.manager])
        old = self.issue()
        for doctype, name in (('Cowork Workspace', self.workspace), ('Cowork Client', self.client)):
            frappe.db.set_value(doctype, name, 'enabled', 0, update_modified=False)
            with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
            frappe.db.set_value(doctype, name, 'enabled', 1, update_modified=False)
        frappe.conf.pearnly_features = ['master_data']
        with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
        frappe.conf.pearnly_features = ['intake']  # D1 (ADR-021): the entry entitlement is enough for the machine
        self.assertEqual(self.service.authenticate(old['token']).credential_id, old['credential_id'])

    def test_switch_blocks_issuance_auth_and_revalidation_but_not_revoke(self):
        old = self.issue(); context = self.service.authenticate(old['token'])
        for value in (False, None, 'true', '1'):
            frappe.conf.pearnly_cowork_erp_push_enabled = value
            with self.assertRaises(frappe.PermissionError): self.issue(request_key=uuid4().hex)
            with self.assertRaises(frappe.PermissionError): self.service.authenticate(old['token'])
            with self.assertRaises(frappe.PermissionError): self.service.revalidate(context)
        self.assertEqual(self.status(self.connection['name'])['state'], 'active')
        self.assertEqual(self.revoke(self.connection['name'], uuid4().hex, 1)['state'], 'revoked')

    def test_real_context_rechecks_scope_site_and_cannot_be_forged(self):
        old = self.issue(); context = self.service.authenticate(old['token'])
        actor = frappe.session.user
        self.assertEqual(self.service.revalidate(context).credential_id, old['credential_id'])
        self.assertEqual(frappe.session.user, actor)
        forged = self.contracts.AgentContext(context.site, context.connection, context.workspace,
            context.client, context.issuer, context.credential_id, context.token_generation)
        for value in (forged, {}, replace(context, site='another.localhost')):
            with self.assertRaises(frappe.ValidationError): self.service.revalidate(value)
        with self.assertRaises(frappe.AuthenticationError): self.service.revalidate(replace(context, issuer=self.actor))
        frappe.conf.pearnly_product = 'dms'
        with self.assertRaises(frappe.PermissionError): self.service.authenticate(old['token'])

    def test_wrong_tokens_and_cross_connection_tokens_do_not_authenticate(self):
        old = self.issue()
        another = self.connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic other credential', {}, users=[self.manager])
        for token in ('', 'Bearer ' + old['token'], old['token'] + 'x',
                      old['token'].replace(self.connection['name'], another['name'])):
            with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(token)
        self.assertNotIn(self.service.authenticate, frappe.whitelisted)
        self.assertNotIn(self.service.revalidate, frappe.whitelisted)

    def test_intake_user_and_administrator_without_actual_role_cannot_manage(self):
        self.issue()
        for actor in (self.actor, self.outsider, 'Administrator', 'Guest'):
            frappe.set_user(actor)
            if actor == self.actor:
                # D1 (ADR-021): the entry role with the use grant may issue for its own computer, never take the slot
                self.assertEqual(self.issue(request_key=uuid4().hex)['status'], 'exists')
                with self.assertRaises(frappe.PermissionError):
                    self.issue(request_key=uuid4().hex, reset=True, expected_generation=1)
            else:
                with self.assertRaises(frappe.PermissionError): self.issue(request_key=uuid4().hex)
            with self.assertRaises(frappe.PermissionError): self.status(self.connection['name'])
            with self.assertRaises(frappe.PermissionError): self.revoke(self.connection['name'], uuid4().hex, 1)

    def test_native_crud_and_shares_denied_even_administrator(self):
        value = self.issue()
        for actor in (self.manager, 'Administrator'):
            frappe.set_user(actor)
            doc = self.row(value)
            with self.assertRaises(frappe.PermissionError): doc.check_permission('read')
            with self.assertRaises(frappe.PermissionError): frappe.get_list(self.documents.DOCTYPE)
            doc.flags.cowork_companion_credential = True
            with self.assertRaises(frappe.PermissionError): doc.save(ignore_permissions=True)
            with self.assertRaises(frappe.PermissionError): frappe.delete_doc(doc.doctype, doc.name, ignore_permissions=True)
            with self.assertRaises(frappe.PermissionError):
                frappe.get_doc({'doctype': 'DocShare', 'share_doctype': doc.doctype,
                    'share_name': doc.name, 'user': self.actor, 'read': 1}).insert(ignore_permissions=True)
            with self.assertRaises(frappe.PermissionError):
                frappe.get_doc({'doctype': doc.doctype, 'record_id': 'synthetic-forged'}).insert(ignore_permissions=True)

    def test_preseed_share_cannot_grant_read_or_allow_known_credential_insertion(self):
        operation = self.contracts.operation_identity(frappe.local.site, self.manager, self.connection['name'], self.key)
        name = 'CWEAC-' + operation[:48]
        share = frappe.get_doc({'doctype': 'DocShare', 'share_doctype': self.documents.DOCTYPE,
            'share_name': name, 'user': self.actor, 'read': 1})
        share.name = uuid4().hex; share.db_insert()
        with self.assertRaises(frappe.PermissionError): self.issue()
        self.assertFalse(frappe.db.exists(self.documents.DOCTYPE, name))
        frappe.db.set_value('DocShare', share.name, 'read', 0, update_modified=False)
        value = self.issue()
        frappe.db.set_value('DocShare', share.name, 'read', 1, update_modified=False)
        frappe.set_user(self.actor)
        with self.assertRaises(frappe.PermissionError): self.row(value).check_permission('read')
        with self.assertRaises(frappe.PermissionError): frappe.get_list(self.documents.DOCTYPE)

    def test_no_version_records_or_plaintext_after_alias_reset_revoke(self):
        old = self.issue(); self.issue(request_key=uuid4().hex)
        new = self.issue(request_key=uuid4().hex, reset=True, expected_generation=1)
        self.revoke(self.connection['name'], uuid4().hex, 2)
        for value in (old, new):
            self.assertEqual(frappe.db.count('Version', {'ref_doctype': self.documents.DOCTYPE, 'docname': value['credential_id']}), 0)
            self.assertNotIn(value['token'], json.dumps(self.row(value).as_dict(), default=str))

    def test_full_issuance_receipts_preserve_first_revocation_and_replay(self):
        old = self.issue(); row = self.row(old)
        receipts = json.loads(row.request_receipts)
        # Deliberate synthetic near-limit evidence, retaining the real issuance.
        limit = self.contracts.MAX_RECEIPT_BYTES - self.contracts.REVOCATION_RESERVE_BYTES
        receipts['synthetic-padding'] = ''
        receipts['synthetic-padding'] = 'x' * (limit - len(self.contracts.encode(receipts).encode()))
        row.request_receipts = self.contracts.encode(receipts)
        self.documents.persist(row)
        with self.assertRaises(frappe.ValidationError): self.issue(request_key=uuid4().hex)
        key = uuid4().hex
        result = self.revoke(self.connection['name'], key, 1)
        self.assertEqual(result['state'], 'revoked')
        self.assertTrue(self.revoke(self.connection['name'], key, 1)['replayed'])
        with self.assertRaises(frappe.AuthenticationError): self.service.authenticate(old['token'])
        self.assertLessEqual(len(self.row(old).request_receipts.encode()), self.contracts.MAX_RECEIPT_BYTES)

    def test_reset_failure_rolls_back_revocation_and_generation(self):
        old = self.issue(); attempted = []
        real = self.documents.CredentialDocument.before_insert
        def fail_new(doc):
            real(doc)
            if int(doc.token_generation) == 2:
                attempted.append(2)
                self.assertIsNotNone(frappe.db.get_value(self.documents.DOCTYPE, old['credential_id'], 'revoked_at'))
                raise RuntimeError('synthetic insertion failure')
        with patch.object(self.documents.CredentialDocument, 'before_insert', fail_new):
            with self.assertRaises(RuntimeError): self.issue(request_key=uuid4().hex, reset=True, expected_generation=1)
        self.assertEqual(attempted, [2])
        self.assertEqual(self.service.authenticate(old['token']).token_generation, 1)
        self.assertEqual(frappe.db.count(self.documents.DOCTYPE, {'connection': self.connection['name']}), 1)

    def test_local_transaction_retries_only_local_conflicts_and_never_swallows_permission(self):
        for error, count in ((frappe.QueryDeadlockError('synthetic'), 3), (frappe.PermissionError('synthetic'), 1)):
            with patch.object(self.service, 'issue', side_effect=error) as called:
                with self.assertRaises(type(error)): self.issue()
                self.assertEqual(called.call_count, count)

    def workers(self, mode, keys):
        workers = [subprocess.Popen([sys.executable, __file__, '--worker', mode, self.manager,
            self.connection['name'], key], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for key in keys]
        try:
            for worker in workers: self.assertEqual(worker.stdout.readline().strip(), 'READY')
            for worker in workers: worker.stdin.write('go\n'); worker.stdin.flush()
            outputs = [worker.communicate(timeout=45) for worker in workers]
            for worker, (output, error) in zip(workers, outputs): self.assertEqual(worker.returncode, 0, output + error)
            return [json.loads(output.strip().splitlines()[-1]) for output, _ in outputs]
        finally:
            for worker in workers:
                if worker.poll() is None: worker.kill(); worker.communicate()

    def test_concurrent_issue_same_key_and_distinct_keys_return_only_one_token(self):
        for same in (True, False):
            if not same:
                self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex,
                    'Synthetic concurrent credential', {}, users=[self.manager])
            frappe.db.commit()
            keys = [uuid4().hex, uuid4().hex]
            if same: keys[1] = keys[0]
            results = self.workers('issue', keys)
            self.assertEqual(sum(row['has_token'] for row in results), 1)
            self.assertEqual({row['generation'] for row in results}, {1})
            frappe.db.rollback()
            self.assertEqual(frappe.db.count(self.documents.DOCTYPE, {'connection': self.connection['name']}), 1)

    def test_concurrent_reset_distinct_keys_has_one_new_generation(self):
        self.issue(); frappe.db.commit()
        results = self.workers('reset', [uuid4().hex, uuid4().hex])
        self.assertEqual(sum(row['has_token'] for row in results), 1)
        self.assertEqual(sum(row.get('conflict', False) for row in results), 1)
        frappe.db.rollback()
        self.assertEqual(frappe.db.count(self.documents.DOCTYPE, {'connection': self.connection['name']}), 2)
        self.assertEqual(self.status(self.connection['name'])['generation'], 2)

    def test_http_retired_management_rpcs_and_no_machine_authentication_bridge(self):
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        from frappe.app import application
        frappe.set_user('Administrator')
        actor = frappe.get_doc('User', self.manager)
        actor.api_key, secret = uuid4().hex, uuid4().hex
        actor.api_secret = secret; actor.save()
        headers = {'Authorization': f'token {actor.api_key}:{secret}'}
        site = frappe.local.site
        config_path = Path(frappe.get_site_path('site_config.json'))
        original = config_path.read_text(); config = json.loads(original)
        self.assertTrue(config['pearnly_backend_lab'])
        config['pearnly_cowork_erp_push_enabled'] = True
        config['pearnly_features'] = ['intake', 'master_data', 'push_log']
        config_path.write_text(json.dumps(config))
        data = {'connection': self.connection['name'], 'request_key': self.key}
        value = self.issue(); token = value['token']
        frappe.db.commit(); frappe.destroy()
        client = Client(application, Response); base = 'http://' + site
        rpc = '/api/method/pearnly_cowork.companion.api.'
        try:
            for name in ('issue', 'revoke', 'status'):  # retired (ADR-021): no longer whitelisted methods
                response = client.post(rpc + name, base_url=base, headers=headers, json=data)
                self.assertNotEqual(response.status_code, 200, name)
                self.assertNotIn(token, response.get_data(as_text=True))
            for path in (rpc + 'online?connection=' + self.connection['name'],
                         '/api/method/frappe.auth.get_logged_user',
                         '/api/resource/Cowork Express Agent Credential/' + value['credential_id']):
                denied = client.get(path, base_url=base, headers={'Authorization': 'Bearer ' + token})
                self.assertIn(denied.status_code, (401, 403))
                self.assertNotIn(token, denied.get_data(as_text=True))
            native = '/api/resource/Cowork Express Agent Credential/' + value['credential_id']
            self.assertEqual(client.get(native, base_url=base, headers=headers).status_code, 403)
            self.assertEqual(client.put(native, base_url=base, headers=headers, json={'token_tail': 'oops'}).status_code, 403)
        finally:
            config_path.write_text(original); connect(site)


def worker():
    from pearnly_cowork.companion import api, credentials, documents
    mode, actor, connection, key = sys.argv[2:]
    frappe.set_user(actor)
    frappe.conf.pearnly_features = ['intake', 'master_data', 'push_log']
    frappe.conf.pearnly_cowork_erp_push_enabled = True
    frappe.db.count(documents.DOCTYPE)  # Establish a stale RR snapshot before both workers race.
    print('READY', flush=True)
    if sys.stdin.readline().strip() != 'go': raise SystemExit('Missing concurrency barrier')
    try:
        result = api._local_transaction(credentials.issue, connection, key, reset=mode == 'reset',
                                        expected_generation=1 if mode == 'reset' else None)
        value = {'generation': result['generation'], 'has_token': 'token' in result, 'state': result['state']}
        frappe.db.commit()
    except frappe.ValidationError:
        if mode != 'reset': raise
        frappe.db.rollback(); value = {'has_token': False, 'conflict': True}
    print(json.dumps(value), flush=True)


if __name__ == '__main__':
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--worker': worker()
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeCredentials))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
