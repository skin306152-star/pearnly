"""Import only authorized test DMS/OCR credentials; never production LINE or billing tokens.

Requires a private export mounted read-only at /lab-private. No external requests here.
"""
import json
import os
from pathlib import Path

import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dms-line-lab.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
from pearnly_dms.operators import roster

source = json.loads(Path("/lab-private/external-fixture.json").read_text())
if source.get("source_site") != "testdms.pearnly.com":
    raise SystemExit("Only the explicitly selected test tenant fixture is accepted")
shared_name = frappe.db.get_value("Pearnly ERP Endpoint", {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]}, "name")
shared = frappe.get_doc("Pearnly ERP Endpoint", shared_name) if shared_name else frappe.new_doc("Pearnly ERP Endpoint")
shared.update(source["endpoint"])
shared.update({"adapter": "mrerp_dms", "label": "E2E authorized DMS", "enabled": 1, "auto_push": 0, "is_default": 0})
shared.save(ignore_permissions=True)
operators = source["operators"]
if not operators:
    raise SystemExit("No active test operator credentials exported")
credentials = operators[0]
existing = frappe.db.get_value("DMS Operator", {"display_name": "E2E DMS operator"}, "name")
if existing:
    result = roster.update_operator(existing, dms_username=credentials["username"], dms_password=credentials["password"])
else:
    result = roster.create_operator("E2E DMS operator", credentials["username"], credentials["password"], can_query_dms=1)
operator = frappe.get_doc("DMS Operator", result["name"])
operator.advisor_id = credentials.get("advisor_id") or ""
operator.advisor_name = credentials.get("advisor_name") or ""
operator.save(ignore_permissions=True)
frappe.db.commit()
from pearnly_core import line_binding
code = line_binding.generate_bind_code(operator.user)
# The code is local and short lived; actual binding is exercised through the signed webhook.
Path("dms-line-lab.localhost/private/line-lab-bind-code.json").write_text(json.dumps({"code": code["code"]}))
config_file = Path("dms-line-lab.localhost/site_config.json")
config = json.loads(config_file.read_text())
config.update(source.get("ocr") or {})
if source.get("ocr_service_account"):
    credential_file = Path("dms-line-lab.localhost/private/ocr-lab.json")
    credential_file.write_text(json.dumps(source["ocr_service_account"]))
    credential_file.chmod(0o600)
    config["pearnly_ocr_credentials"] = str(credential_file.resolve())
config_file.write_text(json.dumps(config, indent=2))
print(json.dumps({"imported_test_endpoint": shared.name, "imported_operator": operator.name,
                  "ocr_configured": bool(config.get("pearnly_ocr_credentials") or config.get("pearnly_ocr_api_key")),
                  "production_line_credentials_imported": False}))
frappe.destroy()
