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


# ── 本公司看得到哪些主档：唯一一处口径（公司接入重做第 2 批，2026-11-14）────────────────────────────
# 账本档（Express / MR.ERP 同步来的，`express_company` = 归属公司）只给归属那家；本地档（没归属）各家都能用。
# 权限钩子、选择框、LINE、收款、打印、导出、单据提交时的校验都调下面几个函数，不各写一份
# （11-14 评估 §2.3：原来 1 个钩子 + 5 个函数 + 17 处手写、口径不一）；`scripts/check-master-scope.py` 拦新的手写。
OWNER = "express_company"


def usable(owner, company) -> bool:
    """一条主档（归属 ``owner``）能不能用在 ``company`` 这家：本地档、或就是这家的；``company`` 没给时不拦。"""
    return not owner or not company or owner == company


def scope_sql(company, table=None) -> tuple:
    """SQL 条件片段（参数化）：``(条件, {"master_scope_company": 公司})``。``table`` 给了就带表名前缀。
    不包 ifnull：`express_company` 上的索引才用得上（客户近 2 万条时列表、链接搜索不全表扫）。"""
    column = f"`{table}`.`{OWNER}`" if table else f"`{OWNER}`"
    local = f"{column} is null or {column} = ''"
    if not company:
        return f"({local})", {}
    return f"({local} or {column} = %(master_scope_company)s)", {"master_scope_company": company}


def scope_or_filters(company) -> list:
    """``frappe.get_all(or_filters=...)`` 用：本公司的账本档 + 本地档。"""
    return [[OWNER, "=", company], [OWNER, "is", "not set"]] if company else [[OWNER, "is", "not set"]]


def ledger_filters(company) -> dict:
    """只要这家公司的账本档（带账本编码的）：写进账本的单（收款）只能挂账本里有的对方。"""
    return {OWNER: company, "express_code": ["is", "set"]}


def master_query(doctype, user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return ""
    try:
        current = company.active_company(user)
    except frappe.PermissionError:
        current = None
    if current:
        # 用时再核一次（11-22，不阻塞）：选择框搜主档 / 打开主档列表时请账本看一眼有没有变，变了读完列表自己刷新
        from pearnly_erp_portal.express import live

        live.touch(current)
    condition, values = scope_sql(current, f"tab{doctype}")
    return condition.replace("%(master_scope_company)s", frappe.db.escape(values["master_scope_company"])) if values else condition


def master_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    owner = doc.get("express_company") if not isinstance(doc, str) else None
    # 锁定的 Frappe 版本里控制器只能收紧：回 True 不会越过角色权限，回 None 会被当成「不允许」（10-24 验收：销售选商品 403）
    return user == "Administrator" or not owner or company_allowed(owner, user)


def require_master(doc, user=None):
    """门户自己的单条读写接口（打开 / 修改 / 停用客户、供应商、商品）也按公司拦：别家公司 Express 的档不能读、不能改。
    这些接口自己 `frappe.get_doc`，不经过 `has_permission`（10-24 验收问题 23：只开了 CO-00001 的员工读到了别家的商品）。"""
    if not master_permission(doc, user=user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return doc


def customer_query(user=None):
    return master_query("ERP Customer", user)


def supplier_query(user=None):
    return master_query("ERP Supplier", user)


def product_query(user=None):
    return master_query("ERP Product", user)
