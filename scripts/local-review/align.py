"""Apply an allowlisted production shape to this isolated preview only.

Run within pearnly-auth-preview-site-1. No production credentials or business
writes: native local billing/site data, config and the fixture user's roles.
"""
import json
import secrets
from pathlib import Path

import frappe
from frappe.installer import update_site_config

SNAPSHOT = Path('/tmp/pearnly-product-baseline.json')
SITE = 'auth-preview.localhost'
BILLING_SITE = 'control-preview.localhost'
TENANT = 'LOCAL-REVIEW'
USER = 'preview@example.test'
data = json.loads(SNAPSHOT.read_text())
assert data['apps'] == ['frappe', 'pearnly_core', 'pearnly_dms']
assert data['home']['balance_thb'] == 0 and not data['home']['transactions']
assert data['home']['subscription'] is None and not data['home']['pending_topups']
# This fixture matches a zero-balance test customer; never silently replace
# nonempty financial history with an empty database in a later snapshot.
common = Path('common_site_config.json')
config = json.loads(common.read_text()); config.pop('default_site', None); config['socketio_port'] = 8186
common.write_text(json.dumps(config, indent=2))
frappe.init(site=BILLING_SITE)
frappe.connect()
assert frappe.local.site == BILLING_SITE
root = frappe.conf.get('pearnly_billing_token') or secrets.token_urlsafe(32)
management = frappe.conf.get('pearnly_billing_management_token') or secrets.token_urlsafe(32)
for key, value in {
    'pearnly_billing_management_token': management, 'pearnly_billing_reviewers': ['Administrator'],
    'pearnly_billing_token': root, 'pearnly_billing_scoped_tokens': True,
    'pearnly_billing_only': True, 'host_name': 'http://control-preview.localhost:8184',
    'mute_emails': True, 'pause_scheduler': True,
}.items():
    update_site_config(key, value)
frappe.db.set_single_value('System Settings', 'time_zone', 'Asia/Bangkok')
frappe.db.set_single_value('System Settings', 'language', 'th')
if not frappe.db.exists('Pearnly Tenant', TENANT):
    frappe.get_doc({'doctype': 'Pearnly Tenant', 'tenant_name': 'Local review',
                    'status': 'active'}).insert(ignore_permissions=True, set_name=TENANT)
from pearnly_control import billing
from pearnly_control.billing_auth import tenant_token
billing.wallet_of(TENANT)
assert billing.plans() == data['home']['plans']
frappe.db.commit()
frappe.destroy()

frappe.init(site=SITE)
frappe.connect()
for key, value in {
    **data['config'], 'pearnly_tenant': TENANT, 'developer_mode': 0,
    'pearnly_billing_api': 'http://site:8000',
    'pearnly_billing_host_header': BILLING_SITE,
    'pearnly_billing_token': tenant_token(root, TENANT, 'dms'),
    'pearnly_default_language': data['system']['language'],
    'mute_emails': True, 'pause_scheduler': True,
}.items():
    update_site_config(key, value)
settings = frappe.get_single('System Settings')
settings.language = data['system']['language']
settings.time_zone = data['system']['time_zone']
settings.enable_scheduler = 0
settings.save(ignore_permissions=True)
from pearnly_control.features import roles_payload
from pearnly_core.provisioning import ensure_feature_roles
ensure_feature_roles(json.dumps(roles_payload('dms', data['config']['pearnly_features'])), USER)
user = frappe.get_doc('User', USER)
for role in data['roles']:
    assert frappe.db.exists('Role', role), role
user.set('roles', [{'role': role} for role in data['roles']])
user.language = data['system']['language']
user.time_zone = data['system']['time_zone']
user.save(ignore_permissions=True)
frappe.clear_cache(user=USER)
frappe.db.commit()
frappe.destroy()
print('Aligned: apps, features, roles, language, time zone, native billing and empty account state.')

frappe.init(site='desk-preview.localhost');frappe.connect()
for key, value in {
    'pearnly_billing_remote_url': 'http://site:8000',
    'pearnly_billing_remote_host': BILLING_SITE,
    'pearnly_billing_management_token': management,
    'host_name': 'http://desk-preview.localhost:8184',
    'mute_emails': True, 'pause_scheduler': True,
}.items():
    update_site_config(key,value)
if not frappe.db.exists('Pearnly Tenant', TENANT):
    frappe.get_doc({'doctype':'Pearnly Tenant','tenant_name':'Local review','status':'active'}).insert(ignore_permissions=True,set_name=TENANT)
from pearnly_core.provisioning import finalize_site
finalize_site()
from pearnly_control.desk import ensure_shortcuts
ensure_shortcuts()
frappe.db.set_default('currency','THB')
frappe.db.set_single_value('System Settings','time_zone','Asia/Bangkok')
frappe.db.set_single_value('System Settings','language','zh')
frappe.db.set_value('User','Administrator',{'language':'zh','time_zone':'Asia/Bangkok'})
frappe.db.commit();frappe.destroy()
print('Isolated Desk review uses authenticated management RPC to isolated billing.')
