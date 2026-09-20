"""Menu 4 HTTP save of the same authorized lab credentials, then real DMS login.

Only Pearnly's isolated test Site is updated. No third-party password is changed.
Never prints credentials, portal markup, tickets or external customer details.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import frappe
import requests

SITE = 'dms-line-lab.localhost'
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')

from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

try:
    assert frappe.conf.pearnly_line_channel_access_token == 'lab:' + SITE
    operator = frappe.get_doc('DMS Operator', {'display_name': 'E2E DMS operator'})
    endpoint = resolve_dms_endpoint(operator.user, None)
    before = frappe.get_doc('Pearnly ERP Endpoint', endpoint['name'])
    username, password = before.username, before.get_password('password')
    identity = requests.post('http://simulator:8090/__lab/identity', headers={'Host': SITE},
                             json={'user': 'Ulab-alice'}, timeout=15).json()
    http = requests.Session()
    http.headers['Host'] = SITE
    def api(method, **kwargs):
        response = http.post('http://site:8000/api/method/pearnly_dms.account.api.' + method,
                             json=kwargs, timeout=30)
        if response.status_code != 200:
            raise AssertionError('Account HTTP status ' + str(response.status_code))
        return response.json()['message']
    ticket = api('authenticate', id_token=identity['id_token'])['ticket']
    assert api('credentials', ticket=ticket) == {'username': username}
    assert api('save_credentials', ticket=ticket, username=username, password=password)['ok']
    frappe.db.commit()
    after = frappe.get_doc('Pearnly ERP Endpoint', endpoint['name'])
    assert after.username == username and after.get_password('password') == password
    assert api('credentials', ticket=ticket) == {'username': username}
    # The adapter must actually log in with the just-saved owned endpoint.
    def login(client, adapter):
        home = adapter.transport().get(adapter.base_url + 'home/home.php').text
        return {'ok': username in home and 'idusers' in home}
    result = run_logged_in(endpoint, login)
    assert result.get('ok'), 'Saved credentials did not produce the owned DMS home'
    evidence = {'http_auth': True, 'http_save': True, 'encrypted_endpoint_readback': True,
                'password_not_returned': True, 'real_external_login': True,
                'external_password_changed': False, 'same_existing_credentials': True}
    path = Path(SITE) / 'private/line-lab-account-real.json'
    path.write_text(json.dumps(evidence, indent=2))
    path.chmod(0o600)
    print(json.dumps(evidence))
finally:
    frappe.destroy()
