"""Synthetic local acceptance fixture on the production image and Compose overlay.

Writes only stocktake.localhost in a marked, isolated Site volume and its own
Keycloak realm. No production tenant, SMTP, LINE, OCR or billing credentials.
Never enable this fixture through the formal tenant provisioning selector.
"""
import json
import os
from pathlib import Path
import secrets
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SITE = 'stocktake.localhost'
AUTH = 'http://stocktake-auth.localhost:8127'
REALM = 'pearnly-stocktake-acceptance'
EMAIL = 'stocktake-owner@example.com'


def request(path, method='GET', data=None, token=None, form=False):
    headers = {'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
    with urlopen(Request(AUTH + path, data=body, method=method, headers=headers), timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def identity_fixture():
    for attempt in range(60):
        try:
            request('/realms/master/.well-known/openid-configuration')
            break
        except URLError:
            if attempt == 59:
                raise
            time.sleep(2)
    token = request('/realms/master/protocol/openid-connect/token', 'POST', {
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'pearnly-bootstrap',
        'password': os.environ['KEYCLOAK_ADMIN_PASSWORD'],
    }, form=True)['access_token']
    realms = request('/admin/realms', token=token)
    if not any(row['realm'] == REALM for row in realms):
        request('/admin/realms', 'POST', {
            'realm': REALM, 'enabled': True, 'displayName': 'Pearnly',
            # HTTP is allowed only in this private loopback acceptance environment.
            'sslRequired': 'none', 'registrationAllowed': False,
            'loginWithEmailAllowed': True, 'duplicateEmailsAllowed': False,
            'resetPasswordAllowed': True, 'rememberMe': True, 'passwordPolicy': '',
            'loginTheme': 'pearnly', 'emailTheme': 'pearnly',
            'internationalizationEnabled': True, 'supportedLocales': ['th', 'zh-CN', 'en', 'ja'],
            'defaultLocale': 'zh-CN', 'bruteForceProtected': True,
        }, token)
    base = '/admin/realms/' + REALM
    clients = request(base + '/clients?clientId=' + SITE, token=token)
    redirect = f'http://{SITE}:8126/api/method/pearnly_core.identity.callback'
    if not clients:
        request(base + '/clients', 'POST', {
            'clientId': SITE, 'enabled': True, 'publicClient': False,
            'standardFlowEnabled': True, 'directAccessGrantsEnabled': False,
            'serviceAccountsEnabled': False, 'redirectUris': [redirect],
            'attributes': {'pkce.code.challenge.method': 'S256'},
        }, token)
        clients = request(base + '/clients?clientId=' + SITE, token=token)
    secret = request(base + '/clients/' + clients[0]['id'] + '/client-secret', token=token)['value']
    users = request(base + '/users?username=stocktake-owner&exact=true', token=token)
    if not users:
        request(base + '/users', 'POST', {
            'username': 'stocktake-owner', 'email': EMAIL, 'emailVerified': True, 'enabled': True,
            'firstName': 'Stocktake', 'lastName': 'Owner',
            'credentials': [{'type': 'password', 'temporary': False,
                             'value': os.environ['STOCKTAKE_LAB_PASSWORD']}],
        }, token)
        users = request(base + '/users?username=stocktake-owner&exact=true', token=token)
    return secret, users[0]['id'], redirect


def main():
    if os.environ.get('PEARNLY_STOCKTAKE_ACCEPTANCE') != '1':
        raise SystemExit('Dedicated acceptance environment required')
    os.chdir('/home/frappe/frappe-bench/sites')
    marker = Path('.pearnly-stocktake-acceptance')
    existing = {p.parent.name for p in Path('.').glob('*/site_config.json')}
    if existing - {SITE} or (existing and not marker.exists()):
        raise SystemExit('Refusing an unowned Site volume')
    common = Path('common_site_config.json')
    values = json.loads(common.read_text()) if common.exists() else {}
    if values.get('db_host') not in (None, 'mariadb'):
        raise SystemExit('Refusing a foreign database')
    values.update(db_host='mariadb', db_port=3306, redis_cache='redis://redis-cache:6379',
                  redis_queue='redis://redis-queue:6379/2', redis_socketio='redis://redis-queue:6379/2',
                  socketio_port=9000, serve_default_site=False,
                  pearnly_ocr_budget_redis='redis://redis-queue:6379/6', workers={'ocr': {'timeout': 600}})
    common.write_text(json.dumps(values))
    Path('apps.txt').write_text('frappe\nerpnext\npearnly_core\npearnly_dms\npearnly_cowork\npearnly_erp_portal\n')
    marker.write_text('local synthetic stocktake acceptance\n')
    secret, subject, redirect = identity_fixture()
    import frappe
    from frappe.installer import _new_site, install_app, update_site_config
    from cryptography.fernet import Fernet
    if not Path(SITE, 'site_config.json').exists():
        frappe.init(site=SITE, new_site=True)
        _new_site(db_name=None, site=SITE, db_type='mariadb', db_host='mariadb',
                  db_root_username='root', db_root_password=os.environ['DB_ROOT_PASSWORD'],
                  admin_password=secrets.token_urlsafe(40), mariadb_user_host_login_scope='%')
        frappe.destroy()
    frappe.init(site=SITE)
    frappe.connect()
    frappe.set_user('Administrator')
    for app in ('pearnly_core', 'pearnly_erp_portal'):
        if app not in frappe.get_installed_apps():
            install_app(app)
    settings = dict(host_name=f'http://{SITE}:8126', developer_mode=0, maintenance_mode=0,
                    pause_scheduler=0, enable_scheduler=True, mute_emails=1,
                    pearnly_product='erp', pearnly_native_account=True,
                    pearnly_default_language='zh', pearnly_oidc_issuer=AUTH + '/realms/' + REALM,
                    pearnly_oidc_client_id=SITE, pearnly_oidc_client_secret=secret,
                    pearnly_oidc_redirect_uri=redirect)
    if not frappe.conf.get('encryption_key'):
        settings['encryption_key'] = Fernet.generate_key().decode()
    from pearnly_control.features import codes_of, roles_payload, workspace_payload
    settings['pearnly_features'] = codes_of('erp')
    for key, value in settings.items():
        update_site_config(key, value)
        frappe.conf[key] = value
    from pearnly_core.provisioning import (ensure_local_user, ensure_feature_roles,
                                          ensure_product_workspace, finalize_site)
    ensure_local_user(EMAIL, '盘点验收', subject, 'stocktake-acceptance')
    ensure_feature_roles(json.dumps(roles_payload('erp', codes_of('erp'))), admin_user=EMAIL)
    ensure_product_workspace(json.dumps(workspace_payload('erp', codes_of('erp'), '盘点验收')))
    finalize_site()
    from frappe.utils.scheduler import enable_scheduler
    enable_scheduler()
    # Seed inventory, leave the task list empty for native empty-state acceptance.
    from pearnly_erp_portal.products.service import create_product
    from pearnly_erp_portal.inventory import stock, warehouses
    if not frappe.db.exists('ERP Product', {'code': 'ACCEPT-STOCKTAKE'}):
        frappe.set_user(EMAIL)
        product = create_product(product_name='盘点验收商品', code='ACCEPT-STOCKTAKE',
                                 barcode='8850000009912', qr_payload='盘点 QR 001',
                                 unit='瓶', unit_price=15, default_cost=8)['name']
        warehouse = warehouses.resolve('验收仓库', create=True)
        for location, qty in [('A架', 10), ('B架', 5)]:
            stock.apply_movement(product=product, warehouse=warehouse, location=location,
                                 movement_type='stock_in', qty=qty, unit_cost=8)
    frappe.db.commit()
    assert 'erpnext' not in frappe.get_installed_apps()
    assert frappe.is_setup_complete()
    assert frappe.db.get_single_value('System Settings', 'disable_user_pass_login')
    assert not frappe.db.get_value('Has Role', {'parent': EMAIL, 'role': 'System Manager'})
    frappe.clear_cache()
    frappe.destroy()
    print('ACCEPTANCE_READY: native OIDC, finalized ERP Site, product roles, synthetic inventory')


if __name__ == '__main__':
    main()
