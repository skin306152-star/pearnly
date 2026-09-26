"""Reconciliation permissions shared by the native task stores.

The bank and income stores are two DocType pairs with the same rules: the recon
purchase feature, a recon operator role, no company isolation (the Site's one
internal ownership row only keeps references consistent), no generic delete or
share, and query conditions that follow the same rules. This module
holds those rules once and each domain module binds its own DocType names.

The deployed recon permissions used one operator role for every type; the user split
it into one role per type on 2026-09-27 (team management ticks each separately).
Each domain module passes its own role; holders of the old shared role were given
all three by `patches.v0_0_5_split_recon_roles`.
"""
import frappe
from frappe import _

from pearnly_core.shell import entitlements
from pearnly_cowork.access import scope

ADMIN_ROLE = "Cowork Admin"


class ReconAccess:
	"""Permission rules for one task/row DocType pair."""

	def __init__(self, task, row, role):
		self.TASK = task
		self.ROW = row
		self.TYPES = (task, row)
		self.ROLES = {role, ADMIN_ROLE}

	def allowed(self, user=None):
		user = user or frappe.session.user
		return bool(user and user != "Guest" and frappe.conf.get("pearnly_product") == "cowork"
			and "recon" in entitlements() and frappe.db.get_value("User", user, "enabled")
			and self.ROLES.intersection(frappe.get_roles(user)))

	def require(self, workspace=None):
		if not self.allowed() or (workspace is not None and not scope.contains(workspace)):
			frappe.throw(_("Not Permitted"), frappe.PermissionError)

	def has_permission(self, doc, ptype=None, user=None, **kwargs):
		# False would let Frappe's DocShare fallback restore revoked scope.
		# Generic delete would orphan result rows; deletion goes through the service.
		if (not self.allowed(user) or ptype in {"delete", "share", "submit", "cancel", "amend"}
				or not scope.contains(doc.workspace, user=user)):
			frappe.throw(_("Not Permitted"), frappe.PermissionError)
		if doc.get("task") and not frappe.db.exists(self.TASK, {"name": doc.task,
				"workspace": doc.workspace}):
			frappe.throw(_("Not Permitted"), frappe.PermissionError)
		return True

	def reject_share(self, doc, method=None):
		if doc.share_doctype in self.TYPES:
			frappe.throw(_("Assign Cowork access through company and client permissions."))

	def _query(self, doctype, user=None):
		user = user or frappe.session.user
		if frappe.get_all("DocShare", filters={"share_doctype": doctype},
				or_filters={"user": user, "everyone": 1}, pluck="name", limit_page_length=1):
			frappe.throw(_("Remove legacy document shares before accessing Cowork records."),
				frappe.PermissionError)
		if not self.allowed(user):
			return "1=0"
		table = f"`tab{doctype}`"
		clauses = [f"{table}.workspace IN (SELECT name FROM `tabCowork Workspace` WHERE enabled=1)"]
		if doctype == self.ROW:
			clauses.append(f"EXISTS (SELECT name FROM `tab{self.TASK}` t "
				f"WHERE t.name={table}.task AND t.workspace={table}.workspace)")
		return " AND ".join(clauses)

	def task_query(self, user=None):
		return self._query(self.TASK, user)

	def row_query(self, user=None):
		return self._query(self.ROW, user)


def recon_access(task, row, role):
	return ReconAccess(task, row, role)
