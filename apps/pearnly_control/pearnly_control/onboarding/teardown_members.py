"""Freeze cloud employee mutations before the authorized tenant teardown starts.

Only the local operator's existing teardown calls this boundary. No product
credential can request deletion. A member reused by another issued tenant must
not be destroyed as a side effect of removing its original tenant.
"""
import frappe
from frappe import _

from pearnly_control import billing_remote, infra


def prepare(tenant, issues):
    if not any(infra.is_cloud(row.get("target")) for row in issues):
        return
    state = billing_remote.call("prepare_purge", tenant=tenant)
    subjects = state.get("subjects") or []
    if subjects and frappe.get_all("Pearnly Issue", filters={
            "tenant": ["!=", tenant], "identity_subject": ["in", subjects]}, limit_page_length=1):
        frappe.throw(_("An employee identity is used by another tenant; review it before deleting this tenant"))
