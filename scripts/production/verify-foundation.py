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
print("PASS cloud billing: restricted routes, rejected anonymous access, authenticated read")

import frappe
frappe.init(site="billing.internal")
frappe.connect()
for name in ("Pearnly Tenant", "Pearnly Wallet", "Pearnly Credit Transaction", "Pearnly Topup Request"):
    print(name + " records=" + str(frappe.db.count(name)))
frappe.destroy()
