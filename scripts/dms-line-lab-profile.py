"""Native local SSO -> User save -> username/password/email login roundtrip.

Only changes the disposable lab identity and one temporary identity collision
fixture; restores the original identity/password in finally. No production IO.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests
import frappe

spec = importlib.util.spec_from_file_location('password_lab', '/workspace/scripts/dms-line-lab-password.py')
password = importlib.util.module_from_spec(spec)
spec.loader.exec_module(password)
check = password.check
BASE = password.BASE
ISSUER = 'http://dms-line-lab-keycloak.localhost:8198'
REALM = 'dms-line-lab'
NEW = 'linelab-updated'
TAKEN = 'linelab-username-taken'
USER = password.USER
os.chdir('/home/frappe/frappe-bench/sites')
config = json.loads((Path('dms-line-lab.localhost') / 'site_config.json').read_text())
assert config['pearnly_line_channel_access_token'] == 'lab:dms-line-lab.localhost'
assert config['pearnly_oidc_issuer'] == ISSUER + '/realms/' + REALM
admin = requests.Session()
admin.trust_env = False
r = admin.post(ISSUER + '/realms/master/protocol/openid-connect/token', data={
    'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'line-lab-admin',
    'password': 'line-lab-local-only'}, timeout=20)
r.raise_for_status()
admin.headers['Authorization'] = 'Bearer ' + r.json()['access_token']
accounts = ISSUER + '/admin/realms/' + REALM + '/users'
r = admin.get(accounts, params={'email': USER, 'exact': 'true'}, timeout=20)
r.raise_for_status()
original = r.json()[0]
account = accounts + '/' + original['id']
collision = None
changed_password = False


def profile(http):
    page = password.request(http, 'GET', BASE + '/desk/user/' + USER)
    match = re.search(r'frappe.csrf_token\s*=\s*["\']([^"\']+)', page.text)
    check('native Desk CSRF available', bool(match))
    http.headers['X-Frappe-CSRF-Token'] = match[1]
    result = password.request(http, 'GET', BASE + '/api/method/frappe.desk.form.load.getdoc',
                              params={'doctype': 'User', 'name': USER})
    return result.json()['docs'][0]


def save(http, doc):
    r = http.post(BASE + '/api/method/frappe.desk.form.save.savedocs',
                  data={'doc': json.dumps(doc), 'action': 'Save'}, timeout=35)
    return r


def login_rejected(username, secret):
    http = requests.Session(); http.trust_env = False
    page = password.request(http, 'GET', BASE + '/api/method/pearnly_core.identity.begin_login')
    result = password.submit(http, page, 'kc-form-login', {'username': username, 'password': secret})
    check('old credentials rejected', urlsplit(result.url).hostname == 'dms-line-lab-keycloak.localhost'
          and 'id="kc-form-login"' in result.text)


try:
    r = admin.post(accounts, json={'username': TAKEN, 'email': TAKEN + '@example.test',
                                  'enabled': True, 'firstName': TAKEN}, timeout=20)
    r.raise_for_status()
    collision = r.headers['Location']
    http = password.login(password.ORIGINAL)
    doc = profile(http)
    sid = next(cookie.value for cookie in http.cookies if cookie.name == 'sid')
    frappe.init(site='dms-line-lab.localhost',sites_path='.')
    frappe.connect()
    key = 'pearnly_oidc_session:' + sid
    saved_tokens = frappe.cache.get_value(key)
    assert saved_tokens and saved_tokens.get('refresh_token')
    frappe.cache.set_value(key,{**saved_tokens,'refresh_token':'expired-laboratory-token'},expires_in_sec=300)
    frappe.destroy()
    try:
        rejected = save(http,{**doc,'first_name':'must-not-be-saved'})
        check('expired identity refresh refuses rename',rejected.status_code != 200)
        check('expired rename keeps working username',profile(http)['first_name']==original['username'])
    finally:
        frappe.init(site='dms-line-lab.localhost',sites_path='.')
        frappe.connect();frappe.cache.set_value(key,saved_tokens,expires_in_sec=3600);frappe.destroy()
    doc = profile(http)
    check('username source is verified signup identity', doc['first_name'] == original['username'])
    check('company and domain included', bool(doc.get('__onload', {}).get('pearnly_account', {}).get('company'))
          and bool(doc.get('__onload', {}).get('pearnly_account', {}).get('domain')))
    attempted = {**doc, 'first_name': TAKEN}
    r = save(http, attempted)
    check('realm-wide duplicate username rejected', r.status_code != 200)
    doc = profile(http)
    check('duplicate did not alter local username', doc['first_name'] == original['username'])
    r = save(http, {**doc, 'email': 'not-owned@example.test'})
    check('email identity cannot change', r.status_code != 200)
    doc = profile(http)
    doc.update(first_name=NEW, language='zh', mute_sounds=1)
    r = save(http, doc)
    if r.status_code != 200:
        raise AssertionError('native profile save failed: ' + str(r.status_code) + ' ' + r.text[:1800])
    current = profile(http)
    check('username, locale, mute saved together', current['first_name'] == NEW
          and current['language'] == 'zh' and current['mute_sounds'] == 1)
    check('email/name relationship unchanged', current['email'] == USER and current['name'] == USER)
    r = admin.get(account, timeout=20); r.raise_for_status(); remote = r.json()
    check('identity username/email/subject aligned', remote['username'] == NEW
          and remote['email'] == USER and remote['id'] == original['id'])
    http = password.login(password.ORIGINAL, username=NEW)
    password.login(password.ORIGINAL, username=USER)
    password.change(http, password.ORIGINAL, password.TEMPORARY, negative=True)
    changed_password = True
    password.login(password.TEMPORARY, username=NEW)
    password.login(password.TEMPORARY, username=USER)
    login_rejected(NEW, password.ORIGINAL)
    check('new username plus new password works', True)
finally:
    if changed_password:
        http = password.login(password.TEMPORARY, username=USER)
        password.change(http, password.TEMPORARY, password.ORIGINAL)
    r = admin.put(account, json={key: original[key] for key in
        ('username', 'email', 'firstName', 'lastName', 'attributes') if key in original}, timeout=20)
    r.raise_for_status()
    if collision:
        r = admin.delete(collision, timeout=20); r.raise_for_status()
    password.login(password.ORIGINAL)
report = {'passed': len(password.checks), 'checks': password.checks,
          'synthetic_identity_restored': True, 'production_identity_touched': False}
path = Path('dms-line-lab.localhost/private/line-lab-profile.json')
path.write_text(json.dumps(report, indent=2)); path.chmod(0o600)
print(json.dumps(report))
