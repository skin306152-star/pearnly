"""Run inside local control-bench. Only --fixture creates a named disposable draft."""
import json
import os
import sys
import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="control.localhost")
frappe.connect()
frappe.set_user("Administrator")
from pearnly_control import domains, infra, provisioning
from pearnly_core.keycloak_admin import client_for_target

target = frappe.conf.get("pearnly_production_target")
assert target, "No verified production target is connected"
assert infra.is_cloud(target)
assert domains.base_domain(target) == "pearnly.com"
assert not infra.is_cloud("本地样板")
assert domains.base_domain("本地样板") == "localhost"
assert infra.deployment_base_url(frappe._dict(url="http://testdms.localhost:8103", site_name="testdms.localhost", product="dms")) == "http://testdms.localhost:8103"
assert provisioning.target_credentials(target) == {"native_account": True}
client, name = client_for_target()
assert name == target
assert client.base == "http://host.docker.internal:18180"
assert provisioning._issuer_url(target) == "https://auth.pearnly.com/realms/pearnly-business"
assert not any(job["payload"].get("site", "").endswith(".pearnly.com") for job in provisioning.next_steps())
print("PASS target routing, local TEST URL preservation, no cloud realm secret in tenant payload")

# A native LINE form must display the same public URL as its cloud deployment.
from unittest.mock import patch
from pearnly_control.pearnly_control.doctype.pearnly_line_channel.pearnly_line_channel import PearnlyLINEChannel
channel = frappe.new_doc("Pearnly LINE Channel")
channel.deployment = "DEP-2026-0191"
def deployment_value(doctype, name, fields, **kwargs):
    assert "url" in fields
    return frappe._dict(product="dms", site_name="cloudcheck0919.pearnly.com", port=0,
                        url="https://cloudcheck0919.pearnly.com")
with patch.object(frappe.db, "get_value", deployment_value):
    PearnlyLINEChannel.validate(channel)
assert channel.webhook_url == "https://cloudcheck0919.pearnly.com/api/method/pearnly_core.line.webhook"
print("PASS native LINE form uses deployment HTTPS URL, not local port rules")

if "--fixture" in sys.argv:
    email = "cloudcheck0919@pearnly.invalid"
    existing = frappe.db.get_value("Pearnly Issue", {"account_email": email}, "name")
    if not existing:
        doc = frappe.get_doc({"doctype": "Pearnly Issue", "tenant_name": "Cloud verification 0919",
            "account_email": email, "account_username": "cloudcheck0919", "account_password": "7319426085",
            "target": target, "apps": [{"app": "dms", "domain_prefix": "cloudcheck0919"}]})
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        existing = doc.name
    print(json.dumps({"verification_issue": existing}))
frappe.destroy()
