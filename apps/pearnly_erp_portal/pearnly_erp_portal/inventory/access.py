"""Native inventory records use the same capability and warehouse scope as services."""
import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company
from pearnly_erp_portal.masters import access as scope


def entry_permission(doc, ptype="read", user=None, **kwargs):
    return bool(guard.has("stock." + str(doc.direction), user)
                and scope.warehouse_allowed(doc.warehouse, user))


def entry_query(user=None):
    user = user or frappe.session.user
    directions = [key for key in ("in", "out") if guard.has("stock." + key, user)]
    if not directions:
        return "1=0"
    choices = ",".join(frappe.db.escape(key) for key in directions)
    active = frappe.db.escape(company.active_company(user))
    return (f"`tabERP Stock Entry`.`direction` in ({choices}) and `tabERP Stock Entry`.`warehouse` in "
            f"(select name from `tabERP Warehouse` where company={active})")


READ_CAPABILITIES = {
    "ERP Stock Balance": "inventory.read",
    "ERP Stock Movement": "report.read",
    "ERP Stock Opening": "report.read",
    "ERP Stock Count": "count.read",
    "ERP Stock Count Item": "count.read",
    "ERP Stock Count Entry": "count.read",
}


def _can_read(doctype, user=None):
    from pearnly_core.shell import entitlements

    feature = ("warehouses" if doctype == "ERP Stock Balance" else
               "stockcard" if doctype in ("ERP Stock Movement", "ERP Stock Opening") else "stocktake")
    return bool((user or frappe.session.user) == "Administrator"
                or (feature in entitlements() and guard.has(READ_CAPABILITIES[doctype], user)))


def record_permission(doc, ptype="read", user=None, **kwargs):
    if ptype not in scope.READ_TYPES:
        # Ledger and count changes are owned by their business services.
        return False
    if not _can_read(doc.doctype, user):
        return False
    if not scope.warehouse_allowed(doc.warehouse, user):
        return False
    if doc.doctype in ("ERP Stock Count Item", "ERP Stock Count Entry"):
        warehouse = frappe.db.get_value("ERP Stock Count", doc.get("count"), "warehouse")
        return scope.warehouse_allowed(warehouse, user)
    return True


def record_query(user=None, doctype=None):
    if not _can_read(doctype, user):
        return "1=0"
    condition = scope.warehouse_query(doctype, user)
    if doctype in ("ERP Stock Count Item", "ERP Stock Count Entry"):
        condition += (f" and `tab{doctype}`.`count` in (select name from `tabERP Stock Count` where "
                      + scope.warehouse_query("ERP Stock Count", user) + ")")
    return condition
