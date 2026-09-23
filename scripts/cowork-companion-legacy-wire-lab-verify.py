"""Hash-bound legacy QueueClient against native WSGI, on the internal lab only.

Only its file logger is replaced; the real httpx WSGI transport serializes the
unmodified client bodies and parses responses. No HTTP listener or ERP writer.
Pending fixtures use the existing synthetic activation seam, not human consent.
"""
import argparse
import ast
import hashlib
import json
import logging
from pathlib import Path
import runpy
import unittest
from uuid import uuid4

import frappe
import httpx
from werkzeug.test import Client
from werkzeug.wrappers import Response

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-companion-queue-http-lab-verify.py')))
BASE, connect = FIXTURE['NativeQueueHttp'], FIXTURE['connect']
CLIENT_HASH = '6ee2a943fbe8b847ec3c7db34b53ddd41d30fccdde20cd088823d5c3be5572f4'
LEGACY = {}
PATHS = {name: '/api/erp/agent/' + name for name in ('heartbeat', 'lease', 'ack')}


def load_client(path):
    source = Path(path).read_bytes()
    if hashlib.sha256(source).hexdigest() != CLIENT_HASH:
        raise RuntimeError('Expected fixed 33af0fd QueueClient source')
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.level == 1 and node.module == 'logger']
    if len(imports) != 1:
        raise RuntimeError('Unexpected logger seam')
    tree.body.remove(imports[0])
    namespace = {'__name__': __name__, 'get_logger': logging.getLogger}
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace


class LegacyWireHttp(unittest.TestCase):
    prepare, prepared, install = BASE.prepare, BASE.prepared, BASE.install
    availability, another_history = BASE.availability, BASE.another_history
    change_config, db, pending, state = BASE.change_config, BASE.db, BASE.pending, BASE.state
    claimed, receipt = BASE.claimed, BASE.receipt

    def setUp(self):
        BASE.setUp(self)
        from pearnly_cowork.companion.wsgi import application
        self.client = Client(application, Response, use_cookies=False)
        self.legacy = LEGACY['QueueClient'](LEGACY['QueueClientConfig'](
            self.base, self.token, 'synthetic-sixteenth-client'))
        self.legacy.close()
        self.legacy._client = httpx.Client(base_url=self.base,
            headers={'Authorization': 'Bearer ' + self.token},
            transport=httpx.WSGITransport(app=application), trust_env=False)

    def tearDown(self):
        if hasattr(self, 'legacy'):
            self.legacy.close()
        BASE.tearDown(self)

    def request(self, operation='lease', **kwargs):
        kwargs.setdefault('path', PATHS[operation])
        return BASE.request(self, operation, **kwargs)

    def message(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertNotIn('message', response.json)
        self.assertIs(response.json['ok'], True)
        return response.json

    def test_fixed_client_health_prepared_poll_lease_and_duplicate_ack(self):
        health = self.legacy.health(companion_version='1.1.77', max_payload_version=3)
        self.assertIs(health['ok'], True)
        self.assertNotIn('message', health)
        self.assertIsNone(self.legacy.poll())
        def prepared():
            frappe.set_user(self.actor)
            return self.prepared()[0]
        name = self.db(prepared, commit=True)
        self.assertIsNone(self.legacy.poll())
        self.assertEqual(self.state(name)['status'], 'prepared')
        self.assertEqual(self.pending(), name)
        task = self.legacy.poll()
        self.assertIsNotNone(task)
        self.assertEqual(task.payload, json.loads(self.state(name)['payload_json']))
        self.assertEqual(self.state(name)['status'], 'leased')
        self.assertIsNone(self.legacy.poll())
        for _ in range(2):
            self.assertTrue(self.legacy.ack_success(task.id, 'SYNTHETIC-WIRE-001'))
        row = self.state(name)
        self.assertEqual((row['status'], row['external_docnum']), ('success', 'SYNTHETIC-WIRE-001'))
        self.assertEqual(self.db(lambda: frappe.db.count(self.native.ATTEMPT, {'push_record': name})), 1)

    def test_revoked_client_cannot_poll_but_can_ack_its_historical_delivery(self):
        name = self.pending(); task = self.legacy.poll()
        self.db(lambda: self.credentials.revoke(self.connection['name'], uuid4().hex, 1), commit=True)
        with self.assertRaises(httpx.HTTPStatusError) as error:
            self.legacy.poll()
        self.assertIn(error.exception.response.status_code, (401, 403))
        self.assertTrue(self.legacy.ack_success(task.id, 'SYNTHETIC-REVOKED-001'))
        self.assertEqual(self.state(name)['status'], 'success')

    def test_native_auth_guards_and_canonical_envelope_stay_in_effect(self):
        name = self.pending()
        for headers in ({}, self.human_headers, {'Authorization': 'Bearer ' + self.token, 'Cookie': 'sid=Guest'}):
            self.assertIn(self.request(headers=headers).status_code, (401, 403))
        for path in (PATHS['lease'] + '?cmd=ping', PATHS['lease'] + '?sid=Guest', PATHS['lease'] + '/'):
            self.assertIn(self.request(path=path).status_code, (401, 403))
        self.assertIn(self.request(method='GET').status_code, (401, 403))
        self.assertIn(self.request(body={'cmd': 'ping'}).status_code, (401, 403))
        self.assertEqual(self.state(name)['status'], 'pending')
        canonical = self.request(path=FIXTURE['PATHS']['lease'])
        self.assertEqual(canonical.status_code, 200)
        self.assertIn('message', canonical.json)
        self.assertNotIn('jobs', canonical.json)
        self.assertEqual(len(canonical.json['message']['jobs']), 1)

    # Existing meaningful transaction failure scenario, through the new seam.
    test_after_commit_failure_keeps_unknown_delivery_and_never_reissues = (
        BASE.test_after_commit_failure_keeps_unknown_delivery_and_never_reissues)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy-client', required=True)
    args = parser.parse_args()
    LEGACY = load_client(args.legacy_client)
    connect()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(LegacyWireHttp))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
