"""Native stock-entry access follows direction and the user's company scope."""
import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company


def entry_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    if not guard.has("stock." + str(doc.direction), user):
        return False
    if doc.warehouse:
        owner = frappe.db.get_value("ERP Warehouse", doc.warehouse, "company")
        if owner not in company.allowed_companies(user):
            return False
    return True


def entry_query(user=None):
    user = user or frappe.session.user
    directions = [key for key in ("in", "out") if guard.has("stock." + key, user)]
    if not directions:
        return "1=0"
    choices = ",".join(frappe.db.escape(key) for key in directions)
    active = frappe.db.escape(company.active_company(user))
    return (f"`tabERP Stock Entry`.`direction` in ({choices}) and `tabERP Stock Entry`.`warehouse` in "
            f"(select name from `tabERP Warehouse` where company={active})")
