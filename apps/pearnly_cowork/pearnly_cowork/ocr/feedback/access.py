"""Correction examples are internal data, never a native product read/write API."""
import frappe
from frappe import _

from pearnly_cowork.access import scope

DOCTYPE = "Cowork OCR Correction"
_INSERT_TOKEN = object()


def require_scope(workspace, client=None):
    scope.require("intake")
    scope.require_scope(workspace, client)
    if not frappe.local.site:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def native_query(user=None):
    # Raise rather than return 1=0: native DocShare adds an outer OR fallback.
    # The schema's All/Guest read ensures this hook is reached: Frappe 16's query
    # engine otherwise returns a shares-only query before consulting hooks.
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def guard_insert(doc):
    if not doc.is_new() or doc.flags.cowork_correction_insert is not _INSERT_TOKEN:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    require_scope(doc.workspace, doc.client)
    if doc.site_key != frappe.local.site or doc.actor != frappe.session.user:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def insert(body):
    doc = frappe.get_doc({"doctype": DOCTYPE, **body})
    doc.flags.cowork_correction_insert = _INSERT_TOKEN
    # Internal rows have no Desk list. Native list_update would broadcast even
    # their name/actor metadata to clients outside the example's company scope.
    doc.flags.notify_update = False
    doc.flags.ignore_version = True
    # The real-object capability plus current product/scope checks, not a JSON
    # flag or role permission, grants this internal write.
    doc.insert(ignore_permissions=True)
    return doc
