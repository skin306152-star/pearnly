"""Native POS reads and service document loads share company and cashier boundaries."""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.masters import access as scope, company


def terminal_company(name):
    return frappe.db.get_value("ERP POS Terminal", name, "company") if name else None


def record_permission(doc, ptype="read", user=None, **kwargs):
    if ptype not in scope.READ_TYPES:
        return False
    caps = ("cashier.read", "receipts.read") if doc.doctype == "ERP Sale" else (
        "cashier.read", "receipts.read", "shifts.read")
    if not any(guard.has(cap, user) for cap in caps):
        return False
    name = terminal_company(doc.terminal)
    if not name or not company.pos_enabled(name, user):
        return False
    if doc.doctype == "ERP Sale":
        if not scope.warehouse_allowed(doc.warehouse, user):
            return False
        if frappe.db.get_value("ERP Warehouse", doc.warehouse, "company") != name:
            return False
    return bool(rules.record_scope(guard.current_roles(user)) == rules.ALL_RECORDS
                or doc.cashier == (user or frappe.session.user))


def require_record(doc):
    if not record_permission(doc):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def record_query(user=None, doctype=None):
    caps = ("cashier.read", "receipts.read") if doctype == "ERP Sale" else (
        "cashier.read", "receipts.read", "shifts.read")
    if not any(guard.has(cap, user) for cap in caps):
        return "1=0"
    condition = (f"`tab{doctype}`.`terminal` in (select t.name from `tabERP POS Terminal` t "
                 "inner join `tabERP Company` c on c.name=t.company where c.pos_enabled=1 and "
                 + scope.company_query("ERP Company", user).replace("`tabERP Company`", "c") + ")")
    if doctype == "ERP Sale":
        condition += " and " + scope.warehouse_query(doctype, user)
    if rules.record_scope(guard.current_roles(user)) != rules.ALL_RECORDS:
        condition += f" and `tab{doctype}`.`cashier` = " + frappe.db.escape(user or frappe.session.user)
    return condition


def terminal_permission(doc, ptype="read", user=None, **kwargs):
    capability = "stock.read" if ptype in scope.READ_TYPES else "company.manage"
    return bool(guard.has(capability, user) and scope.company_allowed(doc.company, user)
                and (not doc.warehouse or frappe.db.get_value("ERP Warehouse", doc.warehouse, "company") == doc.company))


def terminal_query(user=None):
    return scope.company_query("ERP POS Terminal", user, "company") if guard.has("stock.read", user) else "1=0"
