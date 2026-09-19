"""HTTP billing checks for this task's disposable tenant; never real money/data."""
import json
import os
import sys
import frappe
import requests

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="cloudcheck0919.pearnly.com")
frappe.connect()
assert frappe.conf.pearnly_tenant == "T-2026-0189"
assert frappe.conf.pearnly_product == "dms"
assert frappe.db.exists("User", "cloudcheck0919@pearnly.invalid")
from pearnly_core import billing_client as billing

headers = {"Host": "billing.internal", "X-Pearnly-Billing-Token": frappe.conf.pearnly_billing_token,
           "X-Pearnly-Billing-Product": "dms"}
url = frappe.conf.pearnly_billing_api + "/api/method/pearnly_control.billing_api.service_wallet"
for tenant, product in [("T-2026-0189-other", "dms"), ("T-2026-0189", "erp")]:
    response = requests.post(url, headers={**headers, "X-Pearnly-Billing-Product": product},
                             json={"tenant": tenant}, timeout=10)
    assert response.status_code in (401, 403), response.status_code
print("PASS cross-tenant and cross-product billing credentials rejected")
if "--before" in sys.argv:
    result = billing.status("pdf", 1)
    assert not result["allowed"] and result["error_code"] == "insufficient_balance", result
    print("PASS new tenant has no OCR spending entitlement at zero balance")
else:
    assert billing.status("pdf", 1)["allowed"]
    reference = "cloud-verification-0919:scan-1"
    first = billing.charge("dms", "pdf", 1, reference=reference, description="Disposable verification")
    second = billing.charge("dms", "pdf", 1, reference=reference)
    assert first["ok"] and second["ok"] and second["duplicate"], (first, second)
    assert not billing.charge("erp", "pdf", 1, reference=reference)["ok"]
    print("PASS approved balance permits use; same OCR reference charges once; wrong product denied")
print(json.dumps(billing.wallet()))
frappe.destroy()
