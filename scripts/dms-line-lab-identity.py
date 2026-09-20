"""Bind the synthetic local Keycloak owner to the isolated laboratory Site only."""
import json
import os
from pathlib import Path

import frappe
import requests

SITE = 'dms-line-lab.localhost'
USER = 'line-lab@example.test'
ISSUER = 'http://dms-line-lab-keycloak.localhost:8198/realms/dms-line-lab'
SUBJECT = '09202609-2000-4000-8000-000000000001'
os.chdir('/home/frappe/frappe-bench/sites')
path = Path(SITE) / 'site_config.json'
config = json.loads(path.read_text())
assert config['pearnly_line_channel_access_token'] == 'lab:' + SITE
config.update(developer_mode=0, host_name='http://dms-line-lab.localhost:8197',
    pearnly_oidc_issuer=ISSUER, pearnly_oidc_client_id='dms-line-lab',
    pearnly_oidc_client_secret='line-lab-client-only',
    pearnly_oidc_redirect_uri='http://dms-line-lab.localhost:8197/api/method/pearnly_core.identity.callback',
    pearnly_native_account=True, pearnly_company_name='LINE Laboratory',
    pearnly_features=['roster', 'push_record'])
path.write_text(json.dumps(config, indent=2))
path.chmod(0o600)
# Align the disposable realm with the same native profile rules as bootstrap-auth.
http = requests.Session()
http.trust_env = False
base = ISSUER.split("/realms/")[0]
response = http.post(base + "/realms/master/protocol/openid-connect/token", data={
    "grant_type": "password", "client_id": "admin-cli", "username": "line-lab-admin",
    "password": "line-lab-local-only"}, timeout=20)
response.raise_for_status()
http.headers["Authorization"] = "Bearer " + response.json()["access_token"]
realm = base + "/admin/realms/dms-line-lab"
response = http.put(realm, json={"editUsernameAllowed": True}, timeout=20)
response.raise_for_status()
response = http.get(realm + "/users/profile", timeout=20)
response.raise_for_status()
profile = response.json()
for attribute in profile["attributes"]:
    if attribute["name"] in ("firstName", "lastName"):
        attribute.pop("required", None)
response = http.put(realm + "/users/profile", json=profile, timeout=20)
response.raise_for_status()

frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
try:
    if not frappe.db.exists('User', USER):
        frappe.get_doc({'doctype': 'User', 'email': USER, 'first_name': 'LINE Laboratory',
            'user_type': 'System User', 'language': 'th', 'time_zone': 'Asia/Bangkok',
            'send_welcome_email': 0,
            'roles': [{'role': 'DMS Admin'}]}).insert(ignore_permissions=True)
    if not frappe.db.exists('Pearnly Identity', {'issuer': ISSUER, 'subject': SUBJECT}):
        frappe.get_doc({'doctype': 'Pearnly Identity', 'issuer': ISSUER, 'subject': SUBJECT,
                       'user': USER, 'status': 'active', 'notes': 'Local disposable laboratory owner'}).insert()
    from pearnly_core.desk.navigation import ensure_navigation
    from pearnly_core.provisioning import finalize_site
    finalize_site()
    ensure_navigation()
    frappe.db.commit()
    print('Synthetic local owner bound; no formal provisioning or external account created')
finally:
    frappe.destroy()
