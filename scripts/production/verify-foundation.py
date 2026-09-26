"""Read-only checks inside billing-web. Does not create any tenant or transaction."""
import json
import os
from pathlib import Path
import requests

os.chdir("/home/frappe/frappe-bench/sites")
config = json.loads(Path("billing.internal/site_config.json").read_text())
assert config["pearnly_billing_only"] and config["pearnly_billing_scoped_tokens"]
assert not config.get("allow_tests")
base = "http://127.0.0.1:8000"
headers = {"Host": "billing.internal"}
for path in ("/desk", "/api/resource/Pearnly%20Tenant", "/api/method/frappe.auth.get_logged_user"):
    r = requests.get(base + path, headers=headers, timeout=10, allow_redirects=False)
    assert r.status_code in (401, 403), (path, r.status_code)
private = base + "/api/method/pearnly_control.billing_control.dispatch"
r = requests.post(private, headers=headers, json={"action": "topups"}, timeout=10)
assert r.status_code in (401, 403)
r = requests.post(private, headers={**headers,
    "X-Pearnly-Billing-Management": config["pearnly_billing_management_token"]},
    json={"action": "topups"}, timeout=10)
assert r.status_code == 200
member = base + "/api/method/pearnly_control.account.member_api.execute"
assert requests.get(member, headers=headers, timeout=10, allow_redirects=False).status_code in (401, 403)
response = requests.post(member, headers=headers, json={"action": "create"}, timeout=10)
assert response.status_code == 200 and response.json().get("message") == {"ok": False, "error": "not_permitted"}
assert all(config.get("pearnly_member_identity_" + key) for key in (
    "issuer", "admin_client_id", "admin_client_secret", "ownership_key"))
# Cowork's own account pool: all three keys or none (none until the realm exists).
cowork = [config.get("pearnly_member_identity_cowork_" + key) for key in ("issuer", "admin_client_id", "admin_client_secret")]
assert all(cowork) or not any(cowork)
print("PASS cloud billing: restricted routes, rejected anonymous access, authenticated read")

import frappe
frappe.init(site="billing.internal")
frappe.connect()
for name in ("Pearnly Tenant", "Pearnly Wallet", "Pearnly Credit Transaction", "Pearnly Topup Request",
             "Pearnly Member Identity", "Pearnly Member Operation"):
    print(name + " records=" + str(frappe.db.count(name)))
frappe.destroy()
