"""Pay-as-you-go page usage is separate from prepaid plan consumption."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime, get_system_timezone

TRANSACTION = "Pearnly Credit Transaction"


def billing_month(value):
    """Frappe creation timestamps use the site's timezone, billing uses Bangkok."""
    instant = get_datetime(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=ZoneInfo(get_system_timezone()))
    return instant.astimezone(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m")


def pages_used(tenant, month, *, lock=True):
    # Lock actual rows instead of a locking aggregate (ER_CHECKREAD under load).
    # Callers hold the tenant parent lock; current reads must still see a charge
    # that committed after this request's repeatable-read snapshot began.
    rows = frappe.db.sql(
        """select pages
           from `tabPearnly Credit Transaction`
           where tenant=%s and type='usage' and billing_mode='payg'
             and billing_month=%s""" + (" for update" if lock else ""), (tenant, month),
    )
    return sum(int(row[0] or 0) for row in rows)


def ensure_indexes():
    """台账的两个带锁查询要的索引（幂等）：按月算阶梯价的已用页数（``pages_used``）、扣费前按参考号查是否扣过
    （``billing.charge``）。两者都在扣费的租户锁里加锁读；没有索引就扫全表、把所有租户的台账行一起锁住，所有租户
    的扣费互相排队（2026-10-01 生产：补丁记为已执行，表上却只剩主键和 creation 索引；128 张同时识别时扣费排队
    合计 28 秒）。DocType 每次同步时也会补上（``on_doctype_update``），不只靠一次性补丁。"""
    frappe.db.add_index(TRANSACTION, ["tenant", "type", "billing_mode", "billing_month"], "payg_month")
    frappe.db.add_index(TRANSACTION, ["tenant", "reference"], "tenant_reference")


def execute():
    """Native migration: classify old rows without changing any money or timestamps.

    Pre-migration plan purchases reference their Subscription document and buy a
    30-day cycle. Process that immutable ledger in order. Never guess from the
    translated description or charge amount (S overage and payg have equal rates).
    """
    ensure_indexes()
    subscriptions = set(frappe.get_all("Pearnly Subscription", pluck="name"))
    rows = frappe.get_all(
        TRANSACTION, filters={"type": "usage"},
        fields=["name", "tenant", "creation", "reference", "units", "pages", "billing_mode"],
        order_by="tenant asc, creation asc, name asc", limit_page_length=0,
    )
    cycle_ends = {}
    for row in rows:
        created = get_datetime(row.creation)
        is_fee = row.billing_mode == "subscription_fee" or (
            row.reference in subscriptions and not row.units and not row.pages
        )
        if is_fee:
            cycle_ends[row.tenant] = created + timedelta(days=30)
        if row.billing_mode:
            continue
        mode = "subscription_fee" if is_fee else (
            "subscription" if created < cycle_ends.get(row.tenant, datetime.min) else "payg"
        )
        frappe.db.set_value(TRANSACTION, row.name, {
            "billing_mode": mode, "billing_month": billing_month(created),
        }, update_modified=False)
