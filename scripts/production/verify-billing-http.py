"""HTTP boundary acceptance inside the disposable billing verification container."""
import json
from pathlib import Path
import uuid
import requests

config = json.loads(Path("/home/frappe/frappe-bench/sites/verify-billing.internal/site_config.json").read_text())
assert config["allow_tests"] and config["pearnly_billing_only"]
base = "http://127.0.0.1:8000"
headers = {"Host": "verify-billing.internal"}
manage = {**headers, "X-Pearnly-Billing-Management": config["pearnly_billing_management_token"]}
dispatch = base + "/api/method/pearnly_control.billing_control.dispatch"
tenant = "http-verify-" + uuid.uuid4().hex[:12]
r = requests.post(dispatch, headers=manage, json={"action": "register", "payload": {
    "tenant": tenant, "product": "dms", "company_name": "Isolated HTTP verification"}}, timeout=20)
assert r.status_code == 200, (r.status_code, r.text[:200])
scope = {**headers, "X-Pearnly-Billing-Token": r.json()["message"]["token"],
         "X-Pearnly-Billing-Product": "dms"}
service = base + "/api/method/pearnly_control.billing_api."
r = requests.post(service + "service_home", headers=scope, json={"tenant": tenant}, timeout=20)
assert r.status_code == 200 and r.json()["message"]["balance_thb"] == 0
for path in ("/desk", "/api/resource/Pearnly%20Tenant", "/api/method/frappe.auth.get_logged_user",
             "/api/method/pearnly_control.billing_control.dispatch"):
    r = requests.get(base + path, headers=scope, timeout=20, allow_redirects=False)
    assert r.status_code in (401, 403), (path, r.status_code)
r = requests.post(service + "service_wallet", headers=scope, json={"tenant": tenant + "foreign"}, timeout=20)
assert r.status_code in (401, 403)
r = requests.post(service + "service_charge", headers=scope, json={"tenant": tenant,
    "product": "erp", "kind": "pdf", "units": 1, "reference": "x"}, timeout=20)
assert r.status_code in (401, 403)
r = requests.post(dispatch, headers=scope, json={"action": "summary", "payload": {"tenant": tenant}}, timeout=20)
assert r.status_code in (401, 403)
print("PASS real HTTP: valid scoped access; cross-tenant/product and Desk/admin denied")
r = requests.post(service + "service_topup_request", headers=scope,
                  json={"tenant": tenant, "amount_thb": 25, "payer_name": "isolated"}, timeout=20)
assert r.status_code == 200, (r.status_code, r.text[:200])
name = r.json()["message"]["request"]
r = requests.post(dispatch, headers=manage, json={"action": "approve", "payload": {
    "request": name, "actor": "skinzihao@gmail.com"}}, timeout=20)
assert r.status_code == 200 and r.json()["message"]["new_balance"] == 25
r = requests.post(service + "service_wallet", headers=scope, json={"tenant": tenant}, timeout=20)
assert r.status_code == 200 and r.json()["message"]["balance_thb"] == 25
print("PASS real HTTP: request -> separate management approval -> tenant balance readback")
