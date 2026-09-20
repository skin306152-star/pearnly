"""HTTP regression of the disposable lab identity's native password flow.

No browser automation, production identity, email, or external DMS is touched.
Changes only the documented synthetic lab password, verifies it, and restores it.
"""
import json
import os
import socket
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

BASE = 'http://dms-line-lab.localhost:8197'
USER = 'line-lab@example.test'
ORIGINAL = 'LineLab-2026-Local'
TEMPORARY = 'LineLab-2026-Roundtrip'
real_resolve = socket.getaddrinfo


def resolve(host, port, *args, **kwargs):
    if host == 'dms-line-lab.localhost' and int(port) == 8197:
        host, port = 'simulator', 8090
    return real_resolve(host, port, *args, **kwargs)


socket.getaddrinfo = resolve
checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def request(http, method, url, **kwargs):
    assert urlsplit(url).hostname in {'dms-line-lab.localhost', 'dms-line-lab-keycloak.localhost'}
    # Browsers treat localhost as a secure context; requests does not implement
    # that localhost cookie exception. Apply it only in this disposable client.
    for cookie in http.cookies:
        if cookie.domain.lstrip('.') in {'dms-line-lab.localhost', 'dms-line-lab-keycloak.localhost'}:
            cookie.secure = False
    result = http.request(method, url, timeout=30, **kwargs)
    result.raise_for_status()
    return result


def submit(http, page, form_id, values):
    form = BeautifulSoup(page.text, 'html.parser').find('form', id=form_id)
    check('native form ' + form_id, form is not None)
    data = {item['name']: item.get('value', '') for item in form.select('input[name][type=hidden]')}
    data.update(values)
    return request(http, 'POST', urljoin(page.url, form['action']), data=data)


def login(password, username=USER):
    http = requests.Session()
    http.trust_env = False
    page = request(http, 'GET', BASE + '/api/method/pearnly_core.identity.begin_login')
    page = submit(http, page, 'kc-form-login', {'username': username, 'password': password})
    result = request(http, 'GET', BASE + '/api/method/frappe.auth.get_logged_user')
    check('native SSO login and callback', result.json().get('message') == USER)
    return http


def change(http, current, new, negative=False):
    page = request(http, 'GET', BASE + '/api/method/pearnly_core.identity.begin_password_change')
    if negative:
        failed = submit(http, page, 'kc-form-login', {'password': 'deliberately-wrong-local-password'})
        check('wrong current password cannot reach update', 'id="kc-passwd-update-form"' not in failed.text)
        page = failed
    page = submit(http, page, 'kc-form-login', {'password': current})
    soup = BeautifulSoup(page.text, 'html.parser')
    check('new password and confirmation present', bool(soup.select('input[name=password-new]'))
          and bool(soup.select('input[name=password-confirm]')))
    if negative:
        page = submit(http, page, 'kc-passwd-update-form',
                      {'password-new': new, 'password-confirm': 'different-local-password'})
        check('mismatching confirmation rejected', 'id="kc-passwd-update-form"' in page.text)
    page = submit(http, page, 'kc-passwd-update-form',
                  {'password-new': new, 'password-confirm': new})
    check('password update returns to product', urlsplit(page.url).hostname == 'dms-line-lab.localhost')


def main():
    os.chdir('/home/frappe/frappe-bench/sites')
    config = json.loads((Path('dms-line-lab.localhost') / 'site_config.json').read_text())
    assert config['pearnly_line_channel_access_token'] == 'lab:dms-line-lab.localhost'
    assert config['pearnly_oidc_issuer'].endswith(':8198/realms/dms-line-lab')
    changed = False
    try:
        session = login(ORIGINAL)
        change(session, ORIGINAL, TEMPORARY, negative=True)
        changed = True
        session = login(TEMPORARY)
    finally:
        if changed:
            session = login(TEMPORARY)
            change(session, TEMPORARY, ORIGINAL)
            login(ORIGINAL)
    report = {'checks': checks, 'passed': len(checks), 'synthetic_password_restored': True,
              'production_identity_touched': False}
    path = Path('dms-line-lab.localhost/private/line-lab-password.json')
    path.write_text(json.dumps(report, indent=2))
    path.chmod(0o600)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
