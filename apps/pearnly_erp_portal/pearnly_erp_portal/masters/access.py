"""Company permissions also apply to native master document access."""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company

READ_TYPES = ("read", "select", "print", "export", "report")


def company_allowed(name, user=None):
    return bool(name and name in company.allowed_companies(user))


def warehouse_allowed(name, user=None):
    return company_allowed(frappe.db.get_value("ERP Warehouse", name, "company") if name else None, user)


def require_warehouse(name, user=None):
    """Document reads retain access to allowed historical warehouses, including inactive ones."""
    if not warehouse_allowed(name, user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def company_query(doctype="ERP Company", user=None, field="name"):
    names = company.allowed_companies(user)
    if not names:
        return "1=0"
    values = ",".join(frappe.db.escape(name) for name in names)
    return f"`tab{doctype}`.`{field}` in ({values})"


def warehouse_query(doctype, user=None, field="warehouse"):
    return (f"`tab{doctype}`.`{field}` in (select name from `tabERP Warehouse` where "
            + company_query("ERP Warehouse", user, "company") + ")")


def company_list_query(user=None):
    return company_query(user=user) if guard.has("company.read", user) else "1=0"


def warehouse_list_query(user=None):
    return company_query("ERP Warehouse", user, "company") if guard.has("stock.read", user) else "1=0"


def company_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    read = ptype in READ_TYPES
    if not guard.has("company.read" if read else "company.manage", user):
        return False
    return doc.is_new() or company_allowed(doc.name, user)


def warehouse_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    if not guard.has("stock.read" if ptype in READ_TYPES else "warehouses.manage", user):
        return False
    return company_allowed(doc.company, user)
