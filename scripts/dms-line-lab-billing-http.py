"""Concurrent real HTTP reads/charges against the disposable lab billing Site.

Uses three product-scoped tokens for one synthetic tenant, no OCR or external
money transfer. Removes only this run's synthetic tenant and ledger in finally.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import os
import time
import uuid

import frappe
import requests

from pearnly_control import billing, billing_api
from pearnly_control.billing_auth import tenant_token

SITE = "dms-line-lab-control.localhost"
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
marker = "http-capacity-" + uuid.uuid4().hex
tenant = None

try:
    assert frappe.local.site == SITE and frappe.conf.pearnly_billing_scoped_tokens
    tenant = frappe.get_doc({"doctype":"Pearnly Tenant", "tenant_name":marker}).insert().name
    billing.grant(tenant, 1000, description="Synthetic HTTP concurrency; no real funds")
    tokens = {product: tenant_token(billing_api.service_token(create=False), tenant, product)
              for product in ("dms", "erp", "cowork")}
    frappe.db.commit()

    def send(index):
        product = ("dms", "erp", "cowork")[index % 3]
        method = "service_home" if index % 2 else "service_charge"
        payload = {"tenant":tenant}
        if method == "service_charge":
            payload.update(product=product, kind="pdf", units=1, tier="A", reference=f"{marker}-{index}")
        started = time.monotonic()
        try:
            response = requests.post("http://site:8000/api/method/pearnly_control.billing_api."+method,
                headers={"Host":SITE, "X-Pearnly-Billing-Token":tokens[product],
                         "X-Pearnly-Billing-Product":product}, json=payload, timeout=30)
            body = response.json()
            result = {"method":method, "http":response.status_code,
                      "seconds":round(time.monotonic()-started,3)}
            if response.status_code != 200:
                result["exception"] = str(body.get("exception") or body.get("exc_type") or "")[:200]
                result["frames"] = [line.strip() for line in str(body.get("exc") or "").replace('\\n','\n').splitlines()
                                    if line.strip().startswith('File ')]
            else:
                message = body["message"]
                assert (message.get("ok") and message["charged_thb"] == 1.5) if index % 2 == 0 else "balance_thb" in message
            return result
        except Exception as exc:
            return {"method":method, "error":type(exc).__name__}

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(send, range(200)))
    errors = [row for row in results if row.get("http") != 200]
    print(json.dumps({"requests":len(results), "error_count":len(errors), "errors":errors[:2],
                      "p95_seconds":sorted(row.get("seconds",30) for row in results)[189]}), flush=True)
    assert not errors, "Concurrent HTTP billing failed; inspect safe response summary"
    frappe.db.rollback()
    assert billing.balance_of(tenant) == Decimal("850.00")
    assert frappe.db.count(billing.TXN, {"tenant":tenant, "type":"usage"}) == 100
    assert billing.wallet_summary(tenant)["pages_used_this_month"] == 100
    print("PASS 100 concurrent charges / 100 homepage reads, 3 App tokens, exact wallet and ledger")
finally:
    frappe.db.rollback()
    if tenant:
        assert frappe.db.get_value("Pearnly Tenant", tenant, "tenant_name") == marker
        for doctype in (billing.TXN, billing.SUBSCRIPTION, billing.WALLET):
            frappe.db.delete(doctype, {"tenant":tenant})
        frappe.db.delete("Pearnly Tenant", {"name":tenant})
        frappe.db.commit()
    frappe.destroy()
