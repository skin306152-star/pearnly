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

target = "production-contabo"
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
