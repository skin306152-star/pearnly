"""Uploaded PRN keys are claims, never permission or ERP posting evidence."""
import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.history import storage


def require_links(result, workspace, client):
    scope.require("intake")
    scope.require_scope(workspace, client)
    for name in sorted({page.source_history_id for page in result.pages if page.source_history_id}):
        # No legacy UUID fallback. A future data migration must establish the
        # actual scoped History before its exported key can become a reference.
        try:
            prior = storage.read(name)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            frappe.throw(_("roundtrip.reference_unavailable"), frappe.PermissionError)
        if prior.workspace != workspace or (prior.client or "") != (client or ""):
            frappe.throw(_("roundtrip.reference_unavailable"), frappe.PermissionError)
