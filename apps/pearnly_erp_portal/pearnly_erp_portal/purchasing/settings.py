"""采购设置（单例）。默认值来自旧线上 SettingsIn。"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard

SINGLE = "ERP Purchase Settings"
INVALID = "Invalid value"

# 可写字段与旧线上一一对应。
FIELDS = (
    "default_vat_rate", "auto_stock_in", "dedupe_block", "default_due_days",
    "pay_needs_approval", "default_wht_service_rate", "base_currency",
)


def get_settings() -> dict:
    guard.require("purchase.read")
    return read_settings()


def read_settings() -> dict:
    """不给能力判据的内部读法：同域服务（建单）要用默认值，不必再要一次读权限。"""
    doc = frappe.get_single(SINGLE)
    return {field: doc.get(field) for field in FIELDS}


def update_settings(**changes) -> dict:
    guard.require("purchase.write")
    doc = frappe.get_single(SINGLE)
    for field in FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if field in ("default_vat_rate", "default_wht_service_rate"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                frappe.throw(_(INVALID))
            if not 0 <= number <= 100:
                frappe.throw(_(INVALID))
            doc.set(field, number)
        elif field == "default_due_days":
            try:
                days = int(value)
            except (TypeError, ValueError):
                frappe.throw(_(INVALID))
            if days < 0:
                frappe.throw(_(INVALID))
            doc.set(field, days)
        elif field in ("auto_stock_in", "dedupe_block", "pay_needs_approval"):
            doc.set(field, 1 if value in (True, 1, "1", "true", "True") else 0)
        else:
            doc.set(field, str(value or "").strip())
    doc.save()
    frappe.db.commit()
    return {field: doc.get(field) for field in FIELDS}
