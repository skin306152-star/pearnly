"""Run inside an isolated verify-billing.internal Site, never a real tenant."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import uuid

import frappe

SITE = "verify-billing.internal"
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE)
frappe.connect()
assert frappe.conf.allow_tests and frappe.conf.pearnly_billing_only
frappe.set_user("Administrator")
from pearnly_control import billing, billing_api
from pearnly_control.billing_auth import tenant_token, valid_token

prefix = "verify-" + uuid.uuid4().hex[:10]
tenant = frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": prefix}).insert().name
frappe.db.commit()
assert billing.balance_of(tenant) == 0
assert not billing.gate(tenant, kind="pdf", units=1)["allowed"]
frappe.db.commit()
root = "isolated-verification-root"
token = tenant_token(root, tenant, "dms")
assert valid_token(root, tenant, "dms", token)
assert not valid_token(root, tenant + "-other", "dms", token)
assert not valid_token(root, tenant, "erp", token)
assert not valid_token(root + "x", tenant, "dms", token)
print("PASS zero balance and scoped credentials")

request = frappe.get_doc({"doctype": billing.TOPUP, "tenant": tenant,
                          "requested_thb": 500, "status": "pending"}).insert().name
frappe.db.commit()

def isolated(callback):
    frappe.init(site=SITE)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        result = callback()
        frappe.db.commit()
        return result
    except frappe.ValidationError:
        frappe.db.rollback()
        return {"rejected": True}
    finally:
        frappe.destroy()

with ThreadPoolExecutor(4) as pool:
    results = list(pool.map(lambda _: isolated(lambda: billing_api.approve_local(
        request, "skinzihao@gmail.com", 500, "isolated verification")), range(4)))
frappe.db.rollback()
assert sum(bool(r.get("ok")) for r in results) == 1, results
assert billing.balance_of(tenant) == Decimal("500.00")
assert frappe.db.count(billing.TXN, {"tenant": tenant, "type": "topup"}) == 1
print("PASS four concurrent approvals credit once")

ref = prefix + ":scan-1"
with ThreadPoolExecutor(4) as pool:
    results = list(pool.map(lambda _: isolated(lambda: billing.charge(
        tenant, product="dms", kind="pdf", units=1, tier="A", reference=ref)), range(4)))
frappe.db.rollback()
assert all(r.get("ok") for r in results), results
assert sum(bool(r.get("duplicate")) for r in results) == 3, results
assert billing.balance_of(tenant) == Decimal("498.50")
assert frappe.db.count(billing.TXN, {"tenant": tenant, "reference": ref}) == 1
print("PASS concurrent OCR retries charge once")

frappe.db.rollback()
with ThreadPoolExecutor(4) as pool:
    results = list(pool.map(lambda n: isolated(lambda: billing.charge(
        tenant, product="dms", kind="pdf", units=1, tier="A", reference=f"{prefix}:scan-{n+2}")), range(4)))
frappe.db.rollback()
assert all(r.get("ok") for r in results), results
assert billing.balance_of(tenant) == Decimal("492.50")
assert frappe.db.count(billing.TXN, {"tenant": tenant, "type": "usage"}) == 5
print("PASS distinct concurrent scans keep all five deductions")

assert billing.subscribe(tenant, "S")["ok"]
assert billing.balance_of(tenant) == Decimal("342.50")
sub_result = billing.charge(tenant, product="dms", kind="pdf", units=1, reference=prefix+":plan")
assert sub_result["charged_thb"] == 0
assert billing.subscription_view(tenant)["pages_used_this_cycle"] == 1
again = billing.charge(tenant, product="dms", kind="pdf", units=1, reference=prefix+":plan")
assert again["duplicate"]
assert billing.subscription_view(tenant)["pages_used_this_cycle"] == 1
frappe.db.commit()
with ThreadPoolExecutor(4) as pool:
    results = list(pool.map(lambda n: isolated(lambda: billing.charge(
        tenant, product="dms", kind="pdf", units=1, reference=f"{prefix}:plan-{n}")), range(4)))
frappe.db.rollback()
assert all(r.get("ok") for r in results), results
assert billing.subscription_view(tenant)["pages_used_this_cycle"] == 5
assert billing.balance_of(tenant) == Decimal("342.50")
print("PASS concurrent plan scans retain all quota usage")
for value in ["NaN", "Infinity", "invalid"]:
    try:
        billing._money(value)
        raise AssertionError(value)
    except frappe.ValidationError:
        pass
print("PASS plan quota, duplicate quota consumption and invalid amount rejection")
print("ALL BILLING DATABASE CHECKS PASSED; isolated tenant=" + tenant)
frappe.destroy()
