"""Freeze cloud employee mutations before the authorized tenant teardown starts.

Only the local operator's existing teardown calls this boundary. No product
credential can request deletion. A member reused by another issued tenant must
not be destroyed as a side effect of removing its original tenant.
"""
import frappe
from frappe import _

from pearnly_control import billing_remote, infra
from pearnly_control.account import identity_pools


def prepare(tenant, issues):
    if not any(infra.is_cloud(row.get("target")) for row in issues):
        return
    state = billing_remote.call("prepare_purge", tenant=tenant)
    subjects = state.get("subjects") or []
    # 两个账号池（业务 / Cowork）的开户身份都要查：员工身份被别的租户当开户账号用着就不删。
    if subjects and any(frappe.get_all("Pearnly Issue", filters={
            "tenant": ["!=", tenant], field: ["in", subjects]}, limit_page_length=1)
            for field in identity_pools.SUBJECT_FIELDS.values()):
        frappe.throw(_("An employee identity is used by another tenant; review it before deleting this tenant"))
