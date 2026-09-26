"""Stocktake permissions for dedicated APIs and native Frappe resources."""
import frappe
from frappe import _
from pearnly_core.shell import entitlements
from pearnly_cowork.access import scope

TYPES = ("Cowork Stocktake Task", "Cowork Stocktake Item", "Cowork Stocktake Entry", "Cowork Stocktake Operation")


def allowed(user=None):
    user = user or frappe.session.user
    return bool(user and user != "Guest" and frappe.conf.get("pearnly_product") == "cowork"
                and "stocktake" in entitlements() and frappe.db.get_value("User", user, "enabled")
                and {"Cowork Stocktake", "Cowork Admin"}.intersection(frappe.get_roles(user)))


def require(workspace=None):
    if not allowed() or (workspace is not None and not scope.contains(workspace)):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def has_permission(doc, ptype=None, user=None, **kwargs):
    # False would allow Frappe's DocShare fallback to restore revoked scope.
    if (not allowed(user) or ptype in {"delete", "share", "submit", "cancel", "amend"}
            or not scope.contains(doc.workspace, user=user)):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    if doc.get("task") and not frappe.db.exists("Cowork Stocktake Task", {
            "name": doc.task, "workspace": doc.workspace}):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return True


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES:
        frappe.throw(_("Assign Cowork access through company and client permissions."))


def _query(doctype, user=None):
    user = user or frappe.session.user
    if frappe.get_all("DocShare", filters={"share_doctype": doctype},
                      or_filters={"user": user, "everyone": 1}, pluck="name", limit_page_length=1):
        frappe.throw(_("Remove legacy document shares before accessing Cowork records."), frappe.PermissionError)
    if not allowed(user):
        return "1=0"
    # Identifiers are internal constants. No per-user company filter (Cowork has none).
    table = f"`tab{doctype}`"
    clauses = [f"{table}.workspace IN (SELECT name FROM `tabCowork Workspace` WHERE enabled=1)"]
    if doctype != TYPES[0]:
        clauses.append(f"EXISTS (SELECT name FROM `tabCowork Stocktake Task` t "
                       f"WHERE t.name={table}.task AND t.workspace={table}.workspace)")
    return " AND ".join(clauses)


def task_query(user=None):
    return _query(TYPES[0], user)


def item_query(user=None):
    return _query(TYPES[1], user)


def entry_query(user=None):
    return _query(TYPES[2], user)


def operation_query(user=None):
    return _query(TYPES[3], user)
