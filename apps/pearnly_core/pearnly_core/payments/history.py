"""Product history removal, preserving financial and OCR execution evidence."""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime


def selection(names):
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except (TypeError, ValueError):
            frappe.throw(_("Invalid value"))
    if (not isinstance(names, list) or not 1 <= len(names) <= 2500
            or any(not isinstance(name, str) or not name.strip() or len(name) > 140
                   for name in names)):
        frappe.throw(_("Invalid value"))
    return sorted(set(names))


def remove(doctype, names, *, filters, actor):
    """Internal caller supplies a fixed DocType and its authenticated scope."""
    names = selection(names)
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 140:
        frappe.throw(_("Invalid value"))
    # Lock and check every selected row before updating any; retries are harmless.
    rows = frappe.qb.get_query(doctype, filters={**filters, "name": ["in", names]},
                          fields=["name", "records_deleted_at"],
                          order_by="name", limit=2500, for_update=True).run(as_dict=True)
    if {row.name for row in rows} != set(names):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    pending = [row.name for row in rows if not row.records_deleted_at]
    if pending:
        frappe.db.set_value(doctype, {"name": ["in", pending]}, {
            "records_deleted_at": now_datetime(), "records_deleted_by": actor,
        }, update_modified=False)
    return {"deleted": len(pending)}


def delete_product_records(tab, names, *, actor):
    names = selection(names)
    if tab == "ocr":
        return remove("Pearnly OCR Record", names, filters={}, actor=actor)
    if tab not in ("usage", "topup"):
        frappe.throw(_("Invalid value"))
    from pearnly_core import billing_client
    return billing_client.delete_records(tab, names, actor)
