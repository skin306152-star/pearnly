"""Native inventory records use the same capability and warehouse scope as services."""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company
from pearnly_erp_portal.masters import access as scope

_ENTRY_WRITE = object()
_OPENING_WRITE = object()


ADJUST = "adjust"
COUNT_REF = "count"


def entry_kind(direction, ref_type=None) -> str:
    """这张单归哪一页、要哪种能力：物料调整（``adjust``）含历史盘点生成的 AD 单（方向是 in / out、``ref_type = count``，
    岳父 11-06：以后在物料调整列表里显示，不再出现在入库 / 出库列表）；其余按方向。"""
    return ADJUST if direction == ADJUST or ref_type == COUNT_REF else str(direction or "")


def entry_permission(doc, ptype="read", user=None, **kwargs):
    if ptype not in scope.READ_TYPES:
        return False
    kind = entry_kind(doc.direction, doc.get("ref_type"))
    # 盘点生成的物料调整单：盘点员（`count.write`）也能看（`documents.require_kind` 同判）
    allowed = guard.has("stock." + kind, user) or (kind == ADJUST and doc.get("stock_count") and guard.has("count.write", user))
    return bool(allowed and scope.warehouse_allowed(doc.warehouse, user))


def validate_entry_write(doc, method=None, *args, **kwargs):
    """Only the service may change an entry, including its lines and posting state."""
    context = getattr(frappe.local, "stock_entry_service_write", None)
    if context and context[1] is _ENTRY_WRITE and method == "on_trash" and context[0].name == doc.name:
        return  # 删草稿（`documents.delete_draft`）：框架按名字重新取一份再删，认同一张单
    if not context or context[0] is not doc or context[1] is not _ENTRY_WRITE:
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def write_entry(doc, method):
    """Bind an unforgeable write capability to this document for one service save."""
    if doc.doctype != "ERP Stock Entry" or method not in ("insert", "save", "delete"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    previous = getattr(frappe.local, "stock_entry_service_write", None)
    ignored = doc.flags.ignore_permissions
    frappe.local.stock_entry_service_write = (doc, _ENTRY_WRITE)
    try:
        return getattr(doc, method)(ignore_permissions=True)
    finally:
        frappe.local.stock_entry_service_write = previous
        doc.flags.ignore_permissions = ignored


def validate_opening_write(doc, method=None, *args, **kwargs):
    """Keep the opening voucher in the same transaction as its service-owned ledger."""
    context = getattr(frappe.local, "stock_opening_service_write", None)
    if (not isinstance(context, tuple) or len(context) != 2
            or context[0] is not doc or context[1] is not _OPENING_WRITE):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def write_opening(doc, method="insert"):
    """Authorize one opening save on this exact object, never a client-supplied flag."""
    if doc.doctype != "ERP Stock Opening" or method not in ("insert", "save"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    previous = getattr(frappe.local, "stock_opening_service_write", None)
    ignored = doc.flags.ignore_permissions
    frappe.local.stock_opening_service_write = (doc, _OPENING_WRITE)
    try:
        return getattr(doc, method)(ignore_permissions=True)
    finally:
        frappe.local.stock_opening_service_write = previous
        doc.flags.ignore_permissions = ignored


def entry_query(user=None):
    user = user or frappe.session.user
    directions = [key for key in ("in", "out", "transfer") if guard.has("stock." + key, user)]
    adjust = guard.has("stock." + ADJUST, user)
    counted = guard.has("count.write", user)  # 盘点生成的物料调整单盘点员也看得到
    if not directions and not adjust and not counted:
        return "1=0"
    table = "`tabERP Stock Entry`"
    kinds = []
    if directions:
        choices = ",".join(frappe.db.escape(key) for key in directions)
        kinds.append(f"({table}.`direction` in ({choices}) and ifnull({table}.`ref_type`, '') != 'count')")
    if adjust:
        kinds.append(f"({table}.`direction` = 'adjust' or {table}.`ref_type` = 'count')")
    elif counted:
        kinds.append(f"({table}.`direction` = 'adjust' and ifnull({table}.`stock_count`, '') != '')")
    active = frappe.db.escape(company.active_company(user))
    return (f"({' or '.join(kinds)}) and {table}.`warehouse` in "
            f"(select name from `tabERP Warehouse` where company={active})")


READ_CAPABILITIES = {
    "ERP Stock Balance": "inventory.read",
    "ERP Stock Movement": "report.read",
    "ERP Stock Opening": "report.read",
    "ERP Stock Count": "count.read",
    "ERP Stock Count Item": "count.read",
    "ERP Stock Count Entry": "count.read",
    # 按台记录与按台流水（`inventory.serials`）：同在库一览的读权限，按仓
    "ERP Serial No": "inventory.read",
    "ERP Serial Movement": "inventory.read",
}


def _can_read(doctype, user=None):
    from pearnly_core.shell import entitlements

    if doctype in ("ERP Serial No", "ERP Serial Movement"):
        # 按台记录：进货、销售、收银、出入库、盘点都要选台 / 看台，任一读权限都能看（仍按仓，`record_permission`）
        from pearnly_erp_portal.inventory import serials

        return bool((user or frappe.session.user) == "Administrator"
                    or any(guard.has(capability, user) for capability in serials.READ_CAPABILITIES))

    feature = ("warehouses" if doctype in ("ERP Stock Balance", "ERP Serial No", "ERP Serial Movement") else
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
