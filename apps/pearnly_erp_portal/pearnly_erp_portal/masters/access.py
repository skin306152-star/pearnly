"""Company permissions also apply to native master document access."""
import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company


def company_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    read = ptype in ("read", "select", "print", "export")
    if not guard.has("company.read" if read else "company.manage", user):
        return False
    return not doc.name or doc.is_new() or doc.name in company.allowed_companies(user)


def warehouse_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    if ptype not in ("read", "select", "print", "export") and not guard.has("warehouses.manage", user):
        return False
    return not doc.company or doc.company in company.allowed_companies(user)
