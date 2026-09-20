"""Real local MariaDB billing regression. No OCR, external DMS or money transfer.

Creates only uniquely marked tenants in the isolated LINE laboratory billing
Site; removes only their synthetic records in finally. Never a production Site.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from unittest.mock import patch
import uuid

import frappe

SITE = "dms-line-lab-control.localhost"
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
from pearnly_control import billing
from pearnly_control.payments import usage
from pearnly_control.payments.transactions import retry_service

created = []
checks = 0
marker = "pricing-audit-" + uuid.uuid4().hex[:12]


def equal(actual, expected, label):
    global checks
    assert actual == expected, (label, actual, expected)
    checks += 1


def tenant(balance=2000):
    name = frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": f"{marker}-{len(created)}"}).insert().name
    created.append(name)
    frappe.db.commit()
    if balance:
        billing.grant(name, balance, description=marker)
    return name


def charge(name, units, *, kind="pdf", tier="A", reference=None):
    return billing.charge(name, product="dms", kind=kind, units=units, tier=tier,
                          reference=reference or uuid.uuid4().hex, description=marker)


def concurrent(name, references):
    def run(reference):
        frappe.init(site=SITE, sites_path=".")
        frappe.connect()
        frappe.set_user("Administrator")
        try:
            # HTTP service authentication resolves the tenant before billing,
            # opening a repeatable-read snapshot before waiting for its lock.
            @retry_service
            def service():
                assert frappe.db.exists("Pearnly Tenant", name)
                return charge(name, 1, reference=reference)
            return service()
        finally:
            frappe.db.rollback()
            frappe.destroy()
    frappe.db.commit()
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(run, references))
    frappe.db.rollback()
    return results


try:
    for tier in ("A", "B"):
        for used, pages, price in [(0, 0, "0"), (0, 1, "1.50"), (199, 2, "2.25"),
                                    (200, 1, "0.75"), (0, 201, "300.75")]:
            equal(billing.pdf_cost_thb(used, pages, tier), Decimal(price), (tier, used, pages))
        for chars, price, quota in [(0, "0", 0), (1, ".01", 1), (50, ".01", 1),
                                    (51, ".02", 1), (7500, "1.50", 1), (7501, "1.51", 2)]:
            equal(billing.excel_cost_thb(chars, tier), Decimal(price), (tier, chars))
            equal(billing.doc_quota_pages(chars), quota, ("quota", chars))
    for pages, rows, units in [(1, 0, 1), (1, 40, 1), (1, 41, 2), (3, 41, 3)]:
        equal(billing.pdf_billing_units(pages, rows), units, "bank units")

    empty = tenant(0)
    equal(billing.gate(empty, kind="pdf", units=1, tier="A")["allowed"], False, "empty gate")
    for plan in "SML":
        equal(billing.subscribe(empty, plan)["ok"], False, "empty plan")
    for balance in (1.49, 1.50, 4.49):
        name = tenant(balance)
        result = billing.gate(name, kind="pdf", units=1, tier="A")
        equal(result["allowed"], balance >= 1.50, "gate uses actual 1.50 price")
        equal(result["estimate_thb"], 1.50, "quote parity")

    name = tenant()
    equal(charge(name, 199)["charged_thb"], 298.50, "first 199")
    equal(charge(name, 2)["charged_thb"], 2.25, "cross threshold")
    equal(charge(name, 51, kind="excel")["charged_thb"], .02, "chars not pages")
    equal(billing.wallet_summary(name)["pages_used_this_month"], 201, "chars excluded from monthly pages")
    equal(charge(name, 0)["charged_thb"], 0, "zero units")

    for code, quota, fee, rate in [("S", 100, 150, 1.50), ("M", 200, 250, 1.25), ("L", 500, 500, 1.00)]:
        name = tenant()
        equal(billing.subscribe(name, code)["charged_thb"], fee, "plan fee")
        equal(charge(name, quota-1)["charged_thb"], 0, "plan allowance")
        equal(billing.gate(name, kind="pdf", units=3, tier="A")["estimate_thb"], rate*2, "overage quote")
        equal(charge(name, 3)["charged_thb"], rate*2, "overage no A multiplier")
        equal(billing.subscription_view(name)["pages_used_this_cycle"], quota+2, "quota readback")
        equal(billing.wallet_summary(name)["pages_used_this_month"], 0, "plan not payg")
        # Only this test tenant: expire the cycle without changing the machine clock.
        sub = frappe.db.get_value(billing.SUBSCRIPTION, {"tenant": name}, "name")
        frappe.db.set_value(billing.SUBSCRIPTION, sub, "cycle_end", "2000-01-01 00:00:00")
        equal(charge(name, 1)["charged_thb"], 1.50, "expired plan reverts to first payg band")

    name = tenant(150)
    billing.subscribe(name, "S")
    equal(billing.balance_of(name), Decimal("0"), "fee fully consumed balance")
    equal(billing.gate(name, kind="pdf", units=1, tier="A")["allowed"], True, "zero balance with prepaid quota")
    equal(charge(name, 7501, kind="excel")["quota_pages"], 2, "chars consume plan quota")
    equal(billing.balance_of(name), Decimal("0"), "within allowance remains free")

    name = tenant()
    result = concurrent(name, [marker + "-same"]*20)
    equal(sum(bool(r.get("duplicate")) for r in result), 19, "20 identical events")
    equal(billing.balance_of(name), Decimal("1998.50"), "exactly one deduction")
    result = concurrent(name, [f"{marker}-{i}" for i in range(20)])
    equal(sum(r["charged_thb"] for r in result), 30, "20 distinct events")
    equal(billing.balance_of(name), Decimal("1968.50"), "all distinct deductions retained")
    equal(billing.wallet_summary(name)["pages_used_this_month"], 21, "all page units retained")
    try:
        charge(name, 2, reference=marker + "-same")
    except frappe.ValidationError:
        frappe.db.rollback()
        checks += 1
    else:
        raise AssertionError("same reference different units must fail")

    threshold = tenant()
    charge(threshold, 199)
    result = concurrent(threshold, [f"{marker}-threshold-{i}" for i in range(20)])
    equal(sum(r["charged_thb"] for r in result), 15.75, "concurrent 200-page boundary")
    equal(billing.wallet_summary(threshold)["pages_used_this_month"], 219, "boundary count")

    legacy = tenant()
    charge(legacy, 10)
    billing.subscribe(legacy, "S")
    charge(legacy, 99)
    charge(legacy, 2)  # S overage equals payg price; amount cannot classify it.
    fields = ["name", "creation", "modified", "amount_thb", "balance_after", "billing_mode"]
    before = frappe.get_all(billing.TXN, filters={"tenant": legacy}, fields=fields, order_by="creation")
    balance_before = billing.balance_of(legacy)
    frappe.db.sql("""update `tabPearnly Credit Transaction` set billing_mode='',billing_month=''
                      where tenant=%s and type='usage'""", (legacy,))
    usage.execute()
    equal(frappe.get_all(billing.TXN, filters={"tenant": legacy}, fields=fields, order_by="creation"),
          before, "migration preserves all money/timestamps and separates same-rate rows")
    equal(billing.wallet_summary(legacy)["pages_used_this_month"], 10, "migration payg count")
    usage.execute()
    equal(billing.balance_of(legacy), balance_before, "migration repeat never changes balance")

    with patch.object(usage, "get_system_timezone", return_value="UTC"):
        equal(usage.billing_month("2026-09-30 16:59:59"), "2026-09", "Bangkok boundary before")
        equal(usage.billing_month("2026-09-30 17:00:00"), "2026-10", "Bangkok boundary after")
    with patch.object(usage, "get_system_timezone", return_value="Asia/Kolkata"):
        equal(usage.billing_month("2026-09-30 22:30:00"), "2026-10", "non-Bangkok site")
    # A previous-month row must never lower this month's tier price.
    frappe.db.set_value(billing.TXN, {"tenant": name, "reference": marker + "-same"},
                        "billing_month", "2000-01")
    equal(billing.wallet_summary(name)["pages_used_this_month"], 20, "monthly reset")
    # Only an entire uncommitted service is replayable, never an inner debit
    # after its durable commit or an endlessly conflicting transaction.
    from pearnly_control.payments import transactions
    trial = tenant(1000)
    calls = [0]
    @retry_service
    def rolled_back():
        calls[0] += 1
        if calls[0] == 1:
            frappe.db.set_value(billing.WALLET, trial, 'balance_thb', 1)
            raise frappe.QueryDeadlockError('local conflict')
        return billing.balance_of(trial)
    equal(rolled_back(), Decimal('1000.00'), 'whole failed transaction rolled back before retry')
    calls[0] = 0
    @retry_service
    def committed():
        calls[0] += 1
        billing.grant(trial, 1, description=marker)
        raise frappe.QueryDeadlockError('after committed result')
    try: committed()
    except frappe.QueryDeadlockError: pass
    else: raise AssertionError('committed error hidden')
    equal(calls[0], 1, 'post-commit failure never repeats a money mutation')
    equal(billing.balance_of(trial), Decimal('1001.00'), 'committed grant occurred exactly once')
    calls[0] = 0
    @retry_service
    def conflict():
        calls[0] += 1
        raise frappe.QueryDeadlockError('persistent local conflict')
    with patch.object(transactions.time, 'sleep'):
        try: conflict()
        except frappe.QueryDeadlockError: pass
        else: raise AssertionError('persistent failure hidden')
    equal(calls[0], 12, 'transaction retries stop at a fixed limit')
    print(f"PASS {checks} pricing / real ledger / gate / plan / 20-user concurrency checks")
finally:
    frappe.db.rollback()
    for index, name in enumerate(created):
        assert frappe.db.get_value("Pearnly Tenant", name, "tenant_name") == f"{marker}-{index}"
        for doctype in (billing.TXN, billing.SUBSCRIPTION, billing.WALLET):
            frappe.db.delete(doctype, {"tenant": name})
        frappe.db.delete("Pearnly Tenant", {"name": name})
    frappe.db.commit()
    frappe.destroy()
