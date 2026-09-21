"""Explicit temporary Windows integration, never a production server.

The Mac listener relays only three POSTs through Docker stdin to the marked
internal Site. LAN use requires TLS and one fixed peer. No Docker ports change. Setup creates new
lab identities/credentials; prepare/confirm require explicit local invocations.
No ERP writer runs here. Secrets travel in private stdin/files, never arguments.
"""
import argparse
import base64
from contextlib import redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import ipaddress
import os
from pathlib import Path
import runpy
import subprocess
import ssl
import sys
import time
from uuid import uuid4

SITE = 'cowork-backend-ack.localhost'
STATE = Path('/tmp/cowork-windows-seventeenth-state.json')
TARGET = r'\\Accserver\d$\ACCOUNT\70EXP\TEST'
ROOT = r'\\Accserver\d$\ACCOUNT\70EXP'
ROUTES = {'/api/erp/agent/' + part for part in ('heartbeat', 'lease', 'ack')}
LIMIT = 16 * 1024 * 1024


def save(value):
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)


def bench(action, body):
    import frappe
    from unittest.mock import patch
    fixture = runpy.run_path(str(Path(__file__).with_name('cowork-backend-lab-verify.py')))
    fixture['connect'](SITE)
    config_path = Path(frappe.get_site_path('site_config.json'))
    from frappe.config import clear_site_config_cache
    from pearnly_cowork.companion import credentials
    from pearnly_cowork.connections import service as connections
    if action == 'setup':
        if STATE.exists():
            raise RuntimeError('Existing integration state must be resumed or closed')
        original = config_path.read_text()
        config = json.loads(original)
        config.update(pearnly_cowork_erp_push_enabled=True,
                      pearnly_features=['intake', 'master_data', 'push_log'])
        config_path.write_text(json.dumps(config)); clear_site_config_cache()
        frappe.conf.update(config)
        suffix = uuid4().hex[:10]
        actor = 'cw-win-actor-' + suffix + '@example.invalid'
        manager = 'cw-win-manager-' + suffix + '@example.invalid'
        state = {'site': SITE, 'original_config': original, 'active_config': config_path.read_text(),
                 'actor': actor, 'manager': manager, 'run_id': suffix}
        save(state)  # Restoration data precedes fixture writes.
        with patch.dict(frappe.flags, {'in_import': True, 'in_install': True}):
            fixture['user'](actor, ['Cowork Intake', 'Cowork Push Log'])
            fixture['user'](manager, ['Cowork Admin'])
            from pearnly_cowork.masters import api
            workspace = api.create_workspace('Windows TEST migration ' + suffix, '0735527000289')['name']
            fixture['grant'](actor, 'Cowork Workspace', workspace)
            doc = frappe.get_doc('User', actor)
            doc.api_key, secret = uuid4().hex, uuid4().hex
            doc.api_secret = secret; doc.save()
        frappe.set_user(manager)
        connection = connections.configure(workspace, 'express', uuid4().hex,
            'Windows TEST migration ' + suffix,
            {'account_set': TARGET, 'account_dir': TARGET, 'express_root': ROOT},
            users=[actor, manager])
        issued = credentials.issue(connection['name'], uuid4().hex)
        state.update(workspace=workspace, connection=connection['name'], credential=issued['credential_id'],
            token=issued['token'], human_auth='token ' + doc.api_key + ':' + secret)
        frappe.db.commit(); save(state); frappe.destroy()
        return {'token': state['token'], 'run_id': suffix, 'target': TARGET,
                'root': ROOT, 'site': SITE, 'connection': state['connection']}

    state = json.loads(STATE.read_text())
    if state['site'] != SITE:
        raise RuntimeError('Wrong lab Site')
    if action == 'request':
        if body.get('path') not in ROUTES or body.get('authorization') != 'Bearer ' + state['token']:
            frappe.destroy()
            return {'status': 403, 'body': base64.b64encode(b'{"error":"lab_route_denied"}').decode()}
        from pearnly_cowork.companion.wsgi import application
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        frappe.destroy()
        response = Client(application, Response, use_cookies=False).post(body['path'],
            base_url='http://' + SITE, data=base64.b64decode(body['body'], validate=True),
            content_type='application/json', headers={'Authorization': body['authorization']}, buffered=True)
        return {'status': response.status_code, 'body': base64.b64encode(response.data).decode()}

    if action in ('prepare', 'prepare-fixture', 'confirm', 'cancel', 'review-fixture'):
        from pearnly_cowork.companion.wsgi import application
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        def rpc(path, data):
            if getattr(frappe.local, 'db', None):
                frappe.destroy()
            with redirect_stdout(sys.stderr):
                result = Client(application, Response, use_cookies=False).post(
                    '/api/method/pearnly_cowork.' + path, base_url='http://' + SITE,
                    json=data, headers={'Authorization': state['human_auth']}, buffered=True)
            if result.status_code != 200:
                # Native messages contain no credentials; preserve locally for diagnosis.
                return {'status': result.status_code, 'error': result.json}
            return result.json['message']
        if action == 'review-fixture':
            if not state.get('synthetic_ocr_fixture'):
                raise RuntimeError('Only this labelled fixture can be reviewed')
            current = rpc('history.api.detail', {'name': state['history']})
            return rpc('history.api.save', {'name': state['history'], 'pages': current['pages'],
                'expected_version': current['record_version']})
        if action in ('prepare', 'prepare-fixture'):
            if body.get('target') != TARGET or body.get('buyer_tax') != '0735527000289':
                raise RuntimeError('Only verified TEST facts accepted')
            fields = body['fields']
            preparation_round = body.get('preparation_round', 'initial')
            if not isinstance(preparation_round, str) or not preparation_round.isalnum() or len(preparation_round) > 20:
                raise RuntimeError('Explicit bounded preparation round required')
            if fields.get('invoice_number') != 'CW17-' + state['run_id']:
                raise RuntimeError('Unique task invoice required')
            frappe.set_user(state['actor'])
            revision = connections.detail(state['connection'])['revision']
            pages = [{'page_index': 1, 'fields': fields}]
            if action == 'prepare-fixture':
                # Explicit synthetic OCR result, not a public confidence override
                # and not proof of recognition or manual-draft acceptance.
                if body.get('synthetic_ocr_fixture') is not True:
                    raise RuntimeError('Synthetic fixture must be explicitly acknowledged')
                from pearnly_cowork.history import storage
                result = storage.create_recognized(state['workspace'], pages,
                    'win-synthetic-' + state['run_id'], source='manual',
                    confidence='high', ai_raw=pages, posting_kind='service')
                frappe.db.commit()
                state['synthetic_ocr_fixture'] = True
            else:
                result = rpc('history.api.create', {'workspace': state['workspace'],
                    'pages': pages, 'request_key': 'win-history-' + state['run_id']})
            if 'error' in result:
                return result
            state['history'] = result['name']; save(state)
            result = rpc('push.application.api.prepare', {'history': result['name'],
                'connection': state['connection'], 'request_key': action + '-' + state['run_id'] + '-' + str(revision) + '-' + preparation_round,
                'expected_history_version': result['record_version'], 'expected_connection_revision': revision,
                'posting_kind': 'service'})
            if result.get('status') == 'prepared':
                state['prepared'] = result; save(state)
            elif action == 'prepare':
                state['manual_draft_result'] = result; save(state)
            return result
        if action == 'cancel':
            result = rpc('push.application.api.cancel_prepared', {'name': state['prepared']['name'],
                'expected_revision': state['prepared']['revision']})
            if result.get('status') == 'cancelled':
                state.setdefault('cancelled_preparations', []).append(state.pop('prepared')); save(state)
            return result
        if not state.get('prepared') or body.get('confirmed_test_target') != TARGET:
            raise RuntimeError('Explicit confirmation of the prepared TEST invoice required')
        result = rpc('companion.submission.confirm', {'name': state['prepared']['name'],
            'expected_revision': state['prepared']['revision'], 'request_key': 'win-confirm-' + state['run_id']})
        state['confirmation'] = result; save(state)
        return result

    if action == 'write-plan':
        row = frappe.get_doc('Cowork Push Record', state['prepared']['name'])
        if row.status != 'prepared' or not state.get('synthetic_ocr_fixture'):
            raise RuntimeError('Unsent synthetic TEST preparation required')
        payload = json.loads(row.payload_json)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False).encode()).hexdigest()
        frappe.destroy()
        return {'run_id': state['run_id'], 'connection': state['connection'],
            'prepared_record': row.name, 'payload_sha256': digest,
            'confirmed_test_target': TARGET, 'invoice': 'CW17-' + state['run_id']}

    if action == 'status':
        doc = frappe.get_doc('Cowork ERP Connection', state['connection'])
        report = json.loads(doc.report_json or '{}')
        output = {'run_id': state['run_id'], 'connection_revision': doc.record_revision,
            'reported': bool(doc.report_credential), 'report_keys': sorted(report),
            'has_preparation_binding': bool(report.get('_preparation_binding')), 'prepared': state.get('prepared')}
        if state.get('prepared'):
            row = frappe.get_doc('Cowork Push Record', state['prepared']['name'])
            output['record'] = {key: row.get(key) for key in
                ('name', 'status', 'external_docnum', 'failure_count', 'fence', 'wire_attempt')}
            output['attempt_count'] = frappe.db.count('Cowork Push Attempt', {'push_record': row.name})
            output['writer_locks'] = frappe.db.count('Cowork Push Writer Lock', {'holder': row.name})
            output['receipt'] = json.loads(row.response_json or '{}')
        frappe.destroy(); return output

    if action == 'close':
        if state.get('connection'):
            frappe.set_user(state['manager'])
            credentials.revoke(state['connection'], 'close-' + state['run_id'], 1)
            frappe.db.commit()
        frappe.set_user('Administrator')
        from frappe.utils.password import remove_encrypted_password
        for key in ('actor', 'manager'):
            name = state[key]
            if name != 'cw-win-' + key + '-' + state['run_id'] + '@example.invalid':
                raise RuntimeError('Unexpected fixture identity; do not disable it')
            if frappe.db.exists('User', name):
                user = frappe.get_doc('User', name)
                user.enabled, user.api_key = 0, None
                user.save()
                remove_encrypted_password('User', name, 'api_secret')
        frappe.db.commit()
        if config_path.read_text() != state['active_config']:
            raise RuntimeError('Site config changed; preserve for manual restoration')
        config_path.write_text(state['original_config']); clear_site_config_cache()
        STATE.unlink(); frappe.destroy()
        return {'credential_revoked': True, 'fixture_users_disabled': True,
            'human_api_secrets_removed': True, 'site_config_restored': True, 'private_state_removed': True}
    raise ValueError('Unsupported action')


def relay(port, stop_file, bind, peer, cert, key):
    root = Path(__file__).resolve().parents[1]
    command = ['docker', '--context', 'colima-pearnly', 'compose', '-f',
        str(root/'infrastructure/compose/cowork-backend-lab.yml'), 'exec', '-T',
        '-e', 'PEARNLY_COWORK_TEST_SITE=' + SITE, 'bench', './env/bin/python',
        '/workspace/scripts/cowork-windows-lab-relay.py', '--bench', 'request']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if self.path not in ROUTES or not 0 < size <= LIMIT:
                    self.send_error(403); return
                self.connection.settimeout(15)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('Truncated request')
                request = {'path': self.path, 'body': base64.b64encode(raw).decode(),
                           'authorization': self.headers.get('Authorization', '')}
                result = subprocess.run(command, input=json.dumps(request), capture_output=True,
                                        text=True, timeout=60, check=True)
                response = json.loads(result.stdout)
                data = base64.b64decode(response['body'], validate=True)
                self.send_response(response['status'])
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store'); self.end_headers()
                self.wfile.write(data)
                print(json.dumps({'operation': self.path.rsplit('/', 1)[-1], 'status': response['status']}), flush=True)
            except Exception:
                # No request bodies, credentials or native stderr in relay logs.
                self.send_error(502, 'Lab relay failed; do not retry unknown writes')
    address = ipaddress.ip_address(bind)
    if not address.is_loopback:
        if not address.is_private or address.is_unspecified or not (peer and cert and key):
            raise ValueError('A fixed private LAN address, peer and TLS certificate are required')
        ipaddress.ip_address(peer)
    class Server(HTTPServer):
        def verify_request(self, request, client_address):
            return client_address[0] == (peer or '127.0.0.1')
    server = Server((bind, port), Handler)
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.timeout = 1
    print(json.dumps({'address': bind, 'port': server.server_port, 'tls': bool(cert), 'peer': peer}), flush=True)
    deadline = time.monotonic() + 3600
    try:
        while time.monotonic() < deadline and not stop_file.exists():
            server.handle_request()
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bench', choices=['setup', 'request', 'prepare', 'prepare-fixture', 'review-fixture', 'write-plan', 'confirm', 'cancel', 'status', 'close'])
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--peer')
    parser.add_argument('--cert', type=Path)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--stop-file', type=Path)
    args = parser.parse_args()
    if args.bench:
        data = json.loads(sys.stdin.read() or '{}')
        result = bench(args.bench, data)
        print(json.dumps(result, ensure_ascii=False))
        if 'error' in result:
            raise SystemExit(1)
    else:
        if args.stop_file is None or args.stop_file.exists():
            parser.error('A new task-owned stop file is required')
        relay(args.port, args.stop_file, args.bind, args.peer, args.cert, args.key)
