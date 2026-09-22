"""Preserve historical unnamed stock; allow operational roles to read the shared catalog."""
import frappe
from frappe.permissions import add_permission, update_permission_property


def execute():
    for doctype in ("ERP Stock Balance", "ERP Stock Movement", "ERP Stock Opening",
                    "ERP Stock Entry Line", "Purchase Line", "ERP Sales Document Line", "ERP Sale Line"):
        # Fixed identifiers from our schema; no user-provided SQL identifiers.
        frappe.db.sql(f"update `tab{doctype}` set location=%s where location is null", ("",))
    for role in ("ERP Purchase", "ERP Sales", "ERP Cashier"):
        if not frappe.db.exists("Role", role):
            continue
        if not frappe.db.exists("Custom DocPerm", {"parent": "ERP Product", "role": role}):
            add_permission("ERP Product", role, ptype="read")
        else:
            update_permission_property("ERP Product", role, 0, "read", 1)
    for role in ("ERP Admin", "ERP Stock", "System Manager"):
        if not frappe.db.exists("Role", role):
            continue
        add_permission("ERP Product", role, permlevel=1, ptype="read")
        if role in ("ERP Admin", "System Manager"):
            update_permission_property("ERP Product", role, 1, "write", 1)
    frappe.clear_cache()
