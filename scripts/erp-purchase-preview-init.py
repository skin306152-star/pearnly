"""Prepare the existing synthetic purchase lab for browser acceptance only.

Creates a named test account and native workspace, completes native setup, sets
THB / Asia Bangkok / Chinese defaults. No identity provider, production data,
external API, scheduler or email. Password is a documented local fixture only.
"""
import json
import sys

from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

spec = spec_from_file_location('purchase_lab', Path(__file__).with_name('erp-purchase-lab-verify.py'))
lab = module_from_spec(spec)
spec.loader.exec_module(lab)
lab.connect()
import frappe
from frappe.installer import update_site_config
from frappe.utils.password import update_password
from frappe.desk.page.setup_wizard.setup_wizard import enable_setup_wizard_complete
from pearnly_core.provisioning import ensure_feature_roles, ensure_product_workspace
sys.path.insert(0, '/home/frappe/frappe-bench/apps/pearnly_control')
from pearnly_control.features import codes_of, roles_payload, workspace_payload

user = 'purchase-preview@example.test'
if not frappe.db.exists('User', user):
    frappe.get_doc({'doctype': 'User', 'email': user, 'first_name': 'Purchase Preview',
                    'language': 'zh', 'send_welcome_email': 0,
                    'roles': [{'role': 'Desk User'}, {'role': 'ERP Admin'}, {'role': 'ERP Purchase'}]}).insert(ignore_permissions=True)
update_password(user, 'erp-pos-lab-only')
ensure_feature_roles(json.dumps(roles_payload('erp', codes_of('erp'))), admin_user=user)
ensure_product_workspace(json.dumps(workspace_payload('erp', codes_of('erp'), 'Purchase Preview')))
for app in frappe.get_installed_apps():
    enable_setup_wizard_complete(app)
for key, value in {'setup_complete': 1, 'time_zone': 'Asia/Bangkok', 'currency': 'THB', 'language': 'zh', 'enable_telemetry': 0}.items():
    frappe.db.set_single_value('System Settings', key, value)
update_site_config('developer_mode', 0)
# Socket.IO in this isolated development bench resolves the local Web port.
update_site_config('webserver_port', 8000, site_config_path=str(lab.ROOT / 'common_site_config.json'))
frappe.db.commit()
frappe.clear_cache()
frappe.destroy()
print('Purchase browser fixture ready; local account and synthetic data only')
