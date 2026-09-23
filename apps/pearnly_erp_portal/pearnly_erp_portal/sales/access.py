"""Record ownership also applies to native forms, files and PDF downloads."""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard

DOCTYPE = "ERP Sales Document"


def get_document(name, *, write=False, lock=False):
    guard.require("sales.write" if write else "sales.read")
    if lock:
        frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", (name,))
    doc = frappe.get_doc(DOCTYPE, name)
    if document_permission(doc, "write" if write else "read", frappe.session.user) is False:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    doc.check_permission("write" if write else "read")
    return doc


def document_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    if not guard.has("sales.read" if ptype in ("read", "print", "export", "select") else "sales.write", user):
        return False
    scope = guard.scope_filters(user)
    if scope and doc.owner != user:
        return False
    return True


def query_conditions(user=None):
    user = user or frappe.session.user
    if guard.scope_filters(user):
        return f"`tab{DOCTYPE}`.`owner` = " + frappe.db.escape(user)
    return ""
