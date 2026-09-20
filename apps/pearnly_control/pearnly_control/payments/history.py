"""Tenant-scoped product history; billing and review queries retain original rows."""
import frappe
from frappe import _
from pearnly_core.payments.history import remove


def delete_records(tenant, tab, names, actor):
    if tab == "usage":
        return remove("Pearnly Credit Transaction", names,
                      filters={"tenant": tenant, "type": "usage"}, actor=actor)
    if tab == "topup":
        return remove("Pearnly Topup Request", names, filters={"tenant": tenant}, actor=actor)
    frappe.throw(_("Invalid value"))
