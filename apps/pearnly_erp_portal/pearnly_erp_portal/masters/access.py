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


def company_write_allowed(doc, user=None):
    """Authorize both persisted and supplied ownership before native updates."""
    if not company_allowed(doc.company, user):
        return False
    previous = frappe.db.get_value(doc.doctype, doc.name, ["company"], as_dict=True) if doc.name else None
    return previous is None or company_allowed(previous.company, user)


def validate_company_unchanged(doc, method=None):
    """Reassigning a master would also reassign all historical records linked to it."""
    previous = frappe.db.get_value(doc.doctype, doc.name, ["company"], as_dict=True) if doc.name else None
    if previous is not None and previous.company != doc.company:
        frappe.throw(_("Not permitted"), frappe.PermissionError)


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
    return (company_allowed(doc.company, user) if ptype in READ_TYPES
            else company_write_allowed(doc, user))


# ── 主档按公司（2026-10-23 用户：选哪家公司就只看到、只能选哪家的档）──────────────────
# 从某家公司 Express 同步来的客户 / 供应商 / 商品（`express_company`）只在当前公司是那家时列得出、选得到（列表、报表、链接
# 搜索跟 `permission_query_conditions` 走）；打开单条（`has_permission`）要能用那家公司（看别家公司的旧单时链接照样打得开）。
# ERP 本地建的档（没挂 Express）没有归属公司，各家都能用（原来就全租户共用）。系统内部取数（`frappe.get_all`）不受影响。
COMPANY_MASTERS = ("ERP Customer", "ERP Supplier", "ERP Product")


def master_query(doctype, user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return ""
    try:
        current = company.active_company(user)
    except frappe.PermissionError:
        current = None
    # 不包 ifnull：`express_company` 上的索引才用得上（客户近 2 万条时列表、链接搜索不全表扫）
    column = f"`tab{doctype}`.`express_company`"
    local = f"{column} is null or {column} = ''"
    return f"({local} or {column} = {frappe.db.escape(current)})" if current else f"({local})"


def master_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    owner = doc.get("express_company") if not isinstance(doc, str) else None
    # 锁定的 Frappe 版本里控制器只能收紧：回 True 不会越过角色权限，回 None 会被当成「不允许」（10-24 验收：销售选商品 403）
    return user == "Administrator" or not owner or company_allowed(owner, user)


def customer_query(user=None):
    return master_query("ERP Customer", user)


def supplier_query(user=None):
    return master_query("ERP Supplier", user)


def product_query(user=None):
    return master_query("ERP Product", user)
