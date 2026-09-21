"""Reconciliation permissions for dedicated APIs and native Frappe resources."""
import frappe
from frappe import _

from pearnly_core.shell import entitlements
from pearnly_cowork.access import scope

TASK = "Cowork Bank Reconciliation"
ROW = "Cowork Bank Reconciliation Row"
TYPES = (TASK, ROW)
ROLES = {"Cowork Bank Reconciliation", "Cowork Admin"}


def allowed(user=None):
	user = user or frappe.session.user
	return bool(user and user != "Guest" and frappe.conf.get("pearnly_product") == "cowork"
		and "recon" in entitlements() and frappe.db.get_value("User", user, "enabled")
		and ROLES.intersection(frappe.get_roles(user)))


def require(workspace=None):
	if not allowed() or (workspace is not None and not scope.contains(workspace)):
		frappe.throw(_("Not Permitted"), frappe.PermissionError)


def has_permission(doc, ptype=None, user=None, **kwargs):
	# False would let Frappe's DocShare fallback restore revoked scope.
	# Generic delete would orphan result rows; deletion goes through the service.
	if (not allowed(user) or ptype in {"delete", "share", "submit", "cancel", "amend"}
			or not scope.contains(doc.workspace, user=user)):
		frappe.throw(_("Not Permitted"), frappe.PermissionError)
	if doc.get("task") and not frappe.db.exists(TASK, {"name": doc.task, "workspace": doc.workspace}):
		frappe.throw(_("Not Permitted"), frappe.PermissionError)
	return True


def reject_share(doc, method=None):
	if doc.share_doctype in TYPES:
		frappe.throw(_("Assign Cowork access through company and client permissions."))


def _query(doctype, user=None):
	user = user or frappe.session.user
	if frappe.get_all("DocShare", filters={"share_doctype": doctype},
			or_filters={"user": user, "everyone": 1}, pluck="name", limit_page_length=1):
		frappe.throw(_("Remove legacy document shares before accessing Cowork records."),
			frappe.PermissionError)
	if not allowed(user):
		return "1=0"
	table = f"`tab{doctype}`"
	clauses = [f"{table}.workspace IN (SELECT name FROM `tabCowork Workspace` WHERE enabled=1)"]
	if not scope.administrator(user):
		values = scope.grants("Cowork Workspace", user)
		clauses.append(f"{table}.workspace IN ({', '.join(frappe.db.escape(value) for value in sorted(values))})"
			if values else "1=0")
	if doctype == ROW:
		clauses.append(f"EXISTS (SELECT name FROM `tab{TASK}` t "
			f"WHERE t.name={table}.task AND t.workspace={table}.workspace)")
	return " AND ".join(clauses)


def task_query(user=None):
	return _query(TASK, user)


def row_query(user=None):
	return _query(ROW, user)
