"""公司：本租户自己的主体资料（抬头、税号、分支、联系方式、地址）。

用户 2026-09-23 先要"就一个公司就好了"，同一天又说"**我要做一租户多公司**" ——
一个租户可以有**多家**公司，各家的单据、仓库、库存互相分开。做法照官方（ERPNext）：

· **公司**就是这张表的一条记录（官方 `Company` 也是一个列表/树）；
· **当前公司**是用户默认值 `company`（`frappe.defaults.get_user_default`，官方同一个键），
  官方还会回落到 User Permission 里的默认公司；
· **公司之间的隔离**用官方的 `User Permission`（`allow=ERP Company`）—— 给某个用户指一家
  或几家，框架就按它过滤；没指就按"全部"（官方默认，员工要收窄就在团队页里勾）。

默认公司不存在时自动建一条，否则新租户一来就先看到空页面。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import contracts

DOCTYPE = "ERP Company"
NOT_FOUND = "Company not found"
NAME_REQUIRED = "A company needs a name"
INVALID = "Invalid value"

LIST_FIELDS = ("name", "company_name", "short_name", "tax_id", "branch", "phone",
               "contact_person", "contact_email", "promptpay_id", "doc_prefix",
               "fiscal_year_start_month", "is_default", "is_active", "address", "note")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def ensure_default() -> str:
    """默认公司：没有就在第一次需要时建一条空档，资料页才有东西可填。"""
    existing = frappe.db.get_value(DOCTYPE, {"is_default": 1}, "name")
    if existing:
        return existing
    any_company = frappe.db.get_value(DOCTYPE, {}, "name", order_by="creation asc")
    if any_company:
        frappe.db.set_value(DOCTYPE, any_company, "is_default", 1)
        return any_company
    doc = frappe.get_doc({"doctype": DOCTYPE, "company_name": frappe.conf.get("pearnly_company_name")
                          or "My Company", "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


# ── 公司作用域（当前公司 / 能用哪些公司 / 切换）───────────────────────────────

def allowed_companies(user=None) -> list:
    """当前用户能用哪些公司：有 User Permission 就按它（官方隔离件），否则全部启用的。

    顺序把默认公司排前面，这样"没选过"的人拿到的就是租户的默认那家。
    """
    user = user or frappe.session.user
    order_by = "is_default desc, company_name asc"
    if user == "Administrator":
        rows = frappe.get_all(DOCTYPE, {"is_active": 1}, ["name"], order_by=order_by)
        return [row["name"] for row in rows]
    permitted = frappe.get_all("User Permission", filters={"user": user, "allow": DOCTYPE},
                               pluck="for_value")
    if permitted:
        rows = frappe.get_all(DOCTYPE, {"name": ["in", permitted]}, ["name"], order_by=order_by)
        return [row["name"] for row in rows]
    rows = frappe.get_all(DOCTYPE, {"is_active": 1}, ["name"], order_by=order_by)
    return [row["name"] for row in rows]


def active_company(user=None) -> str:
    """当前公司。官方口径：用户默认值 `company`（没设就看 User Permission 的默认那条），
    再回落租户的默认公司；都不行就报错，不猜一个出来。
    """
    user = user or frappe.session.user
    allowed = allowed_companies(user)
    if not allowed:
        frappe.throw(_("No company is available for this account"), frappe.PermissionError)
    from frappe.defaults import get_user_default
    chosen = get_user_default("company", user) or frappe.db.get_default("company")
    if chosen in allowed:
        return chosen
    return allowed[0]


def company_options(user=None) -> list:
    """切换公司要的那份清单：名字 + 抬头 + 是不是当前这家。"""
    user = user or frappe.session.user
    current = active_company(user)
    rows = frappe.get_all(DOCTYPE, {"name": ["in", allowed_companies(user)]},
                          ["name", "company_name", "short_name"],
                          order_by="is_default desc, company_name asc")
    return [{"name": row["name"], "company_name": row["company_name"] or row["name"],
             "short_name": row["short_name"] or "", "is_current": row["name"] == current}
            for row in rows]


def set_active_company(name, user=None) -> dict:
    """切换当前公司：官方做法就是写用户默认值（谁都能切自己那一份，不用管理员）。"""
    user = user or frappe.session.user
    name = str(name or "").strip()
    if name not in allowed_companies(user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    frappe.defaults.set_user_default("company", name, user)
    return {"company": name, "company_name": frappe.db.get_value(DOCTYPE, name, "company_name")}


def document_company(value=None, user=None) -> str:
    """单据上的公司：给了就用给的（必须在可用范围内），没给就用当前公司。"""
    user = user or frappe.session.user
    value = str(value or "").strip()
    if not value:
        return active_company(user)
    if value not in allowed_companies(user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return value


def scope(user=None) -> dict:
    """列表/汇总的默认过滤：只看当前公司。与 `guard.scope_filters()`（本人/全部）叠着用。"""
    return {"company": active_company(user)}


def for_document(name) -> dict:
    """单据抬头那一块：给了公司名就取它，没有就取默认公司（导出件/打印用）。"""
    target = str(name or "").strip()
    if not target or not frappe.db.exists(DOCTYPE, target):
        target = ensure_default()
    doc = frappe.get_doc(DOCTYPE, target)
    return doc.as_dict()


def list_companies(*, include_inactive=False, limit=50) -> list[dict]:
    guard.require("company.read")
    filters = {} if include_inactive else {"is_active": 1}
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="is_default desc, company_name asc",
                           limit_page_length=int(limit))


def create_company(*, company_name, **fields) -> dict:
    guard.require("company.manage")
    name = contracts.clean(company_name, contracts.MAX_NAME)
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    doc = frappe.get_doc({"doctype": DOCTYPE, "company_name": name, "is_active": 1})
    _apply(doc, fields)
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def get_profile(name=None) -> dict:
    """公司资料：不带名字就是默认公司。

    多回两个判据给页面用，前端不再复刻一份角色清单：

    · `can_write`：能不能改这张资料（服务端 `company.manage`）；
    · `can_lookup`：能不能按税号查泰国税务局（服务端 `tax.lookup`，与供应商/
      客户两页同一个能力码）—— 没这个能力的岗位那边不出现那颗按钮。
    """
    guard.require("company.read")
    doc = _get(name or ensure_default())
    payload = {field: doc.get(field) for field in LIST_FIELDS}
    payload["can_write"] = guard.has("company.manage")
    payload["can_lookup"] = guard.has("tax.lookup")
    return payload


def update_company(name, **fields) -> dict:
    guard.require("company.manage")
    doc = _get(name)
    _apply(doc, fields)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def update_profile(**fields) -> dict:
    """改公司资料（默认公司）。"""
    guard.require("company.manage")
    return update_company(ensure_default(), **fields)


def _apply(doc, fields) -> None:
    for field, limit in (("company_name", contracts.MAX_NAME), ("short_name", contracts.MAX_SHORT),
                         ("tax_id", contracts.MAX_TAX_ID), ("branch", contracts.MAX_CONTACT),
                         ("phone", contracts.MAX_PHONE), ("contact_person", contracts.MAX_CONTACT),
                         ("contact_email", contracts.MAX_EMAIL),
                         ("promptpay_id", contracts.MAX_PROMPTPAY),
                         ("doc_prefix", 20), ("address", contracts.MAX_ADDRESS),
                         ("note", contracts.MAX_NOTE)):
        if field not in fields:
            continue
        try:
            value = contracts.clean(fields[field], limit)
        except ValueError:
            frappe.throw(_(INVALID))
        if field == "company_name" and not value:
            frappe.throw(_(NAME_REQUIRED))
        doc.set(field, value)
    if "fiscal_year_start_month" in fields:
        if not contracts.fiscal_month_ok(fields["fiscal_year_start_month"]):
            frappe.throw(_("The fiscal year start month must be between 1 and 12"))
        doc.fiscal_year_start_month = int(fields["fiscal_year_start_month"])
    for field in ("is_default", "is_active"):
        if field in fields:
            doc.set(field, 1 if fields[field] in (True, 1, "1", "true") else 0)
