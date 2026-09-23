"""收银台：**属于公司、卖这家公司某个仓库里的货**（官方 POS 配置也是这两样：
`company` + `warehouse`）。

所以一句话：收银台 = 某家公司的一台柜台，货从它指定的仓库出。班次、小票、库存因此都能
一路追到"哪家公司、哪个仓"。每家公司的收银台各自有一个默认（`is_default`）。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import warehouses
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP POS Terminal"
NOT_FOUND = "Terminal not found"
NAME_REQUIRED = "A terminal needs a name"
# 收银台没配卖货的仓、而这家公司又有多个仓时那句提示（没有"默认仓"可以兜）。
WAREHOUSE_REQUIRED = "This terminal has no warehouse; set one in the terminal settings"
FIELDS = ("name", "terminal_name", "company", "warehouse", "is_default", "is_active")


def ensure_default(company=None, warehouse=None) -> str:
    """这家公司的**默认收银台**（没有就建一台）。

    ⚠️ 这里说的是"默认**收银台**"（同一家公司有多台柜台时先给哪一台），不是"默认仓" ——
    "默认仓"这个概念已经删掉了（用户 2026-09-24）。新建那台时把卖货的仓写成**显式的**：
    给了 `warehouse` 就用给的，没给就取这家公司**唯一**的仓；多个仓/一个都没有时留空，
    卖货那条路会明确报"这台收银台还没配仓库"（见 `place_of`）。
    """
    company = company or companies.active_company()
    existing = frappe.db.get_value(DOCTYPE, {"company": company, "is_default": 1}, "name")
    if existing:
        return existing
    any_terminal = frappe.db.get_value(DOCTYPE, {"company": company}, "name",
                                       order_by="creation asc")
    if any_terminal:
        frappe.db.set_value(DOCTYPE, any_terminal, "is_default", 1)
        return any_terminal
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": "Counter 1",
                          "company": company,
                          "warehouse": warehouse or warehouses.sole(company) or "",
                          "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def place_of(terminal_doc, *, company=None) -> str:
    """这台收银台**卖货的仓**：写在它自己身上的那一个。

    "自己没配就取默认仓"那条兜底已经删掉（用户 2026-09-24："不要做默认仓，依赖全部删掉"）。
    只剩一种例外：这家公司**只有一个仓** —— 那是唯一解，不算替谁拿主意。
    其余情况（没配 + 多个仓）明确报错，让用户去收银台上把仓配好。
    """
    explicit = str(terminal_doc.get("warehouse") or "").strip()
    if explicit:
        return warehouses.required(explicit, company=company or terminal_doc.get("company"))
    only = warehouses.sole(company or terminal_doc.get("company"))
    if not only:
        frappe.throw(_(WAREHOUSE_REQUIRED))
    return only


def resolve(terminal=None, company=None) -> str:
    if terminal:
        row = frappe.db.get_value(DOCTYPE, terminal, ["company", "is_active"], as_dict=True)
        if not row or not row.is_active:
            frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
        # 别家公司的收银台：不能拿来落单（货会记到别人账上）。
        if row.company != (company or companies.active_company()):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        return terminal
    return ensure_default(company)


def list_terminals(*, include_inactive=False, limit=50, company=None) -> list[dict]:
    guard.require("cashier.read")
    filters = {"company": companies.document_company(company)}
    if not include_inactive:
        filters["is_active"] = 1
    rows = frappe.get_list(DOCTYPE, filters=filters, fields=list(FIELDS),
                           order_by="is_default desc, terminal_name asc",
                           limit_page_length=int(limit))
    # 「这台收银台实际从哪个仓卖货」：自己配了就用它，没配就是**这家公司的默认仓** ——
    # 与卖货那条路（`place_of`）同一口径：**只认它自己配的那个仓**，
    # 唯一的例外是"这家公司只有一个仓"（唯一解）。列表接口只**读**、不写、不建。
    for row in rows:
        row["effective_warehouse"] = (row.get("warehouse")
                                      or warehouses.sole(row.get("company")) or "")
        row["effective_warehouse_name"] = frappe.db.get_value(
            "ERP Warehouse", row["effective_warehouse"], "warehouse_name") if row["effective_warehouse"] else ""
        # 开班/交班弹窗要显示「这家公司叫什么」给收银员看，编号（`CO-00552`）没人认。
        row["company_name"] = frappe.db.get_value(
            "ERP Company", row.get("company"), "company_name") or row.get("company") or ""
        # 「这家公司收哪几种钱」：收银台的付款方式面板、开班/交班那张对账表都照它给
        # （建公司时勾的那几种，见 `masters/company.payment_methods_of`）。
        row["payment_methods"] = companies.payment_methods_of(row.get("company"))
    return rows


def create_terminal(*, terminal_name, warehouse=None, is_default=False, company=None,
                    internal=False) -> dict:
    # `internal=True`：公司页那条路（判据是 `company.manage`，见 `masters/company.create_terminal`）
    # —— 建公司时就要把收银台配好，那会儿不一定有人已经拿到收银岗位。
    if not internal:
        guard.require("cashier.manage")
    name = (terminal_name or "").strip()
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    # 建公司时一次配齐要用它建属于**那家新公司**的收银台，所以公司要能显式给。
    company = companies.document_company(company)
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": name,
                          "company": company,
                          "warehouse": warehouses.required(warehouse, company=company),
                          "is_default": 1 if is_default else 0, "is_active": 1})
    doc.insert()
    if doc.is_default:
        for other in frappe.get_all(DOCTYPE, filters={"company": company, "is_default": 1,
                                                      "name": ["!=", doc.name]}, pluck="name"):
            frappe.db.set_value(DOCTYPE, other, "is_default", 0)
    frappe.db.commit()
    return {"name": doc.name}


def names_in_company(company=None) -> list:
    """某家公司的收银台。班次表上没有公司/仓库列，所以班次的作用域靠这一份名单来收
    （`terminal in (...)`）。"""
    return frappe.get_all(DOCTYPE, {"company": companies.document_company(company)},
                          pluck="name", order_by="creation asc")


def scope_filter(company=None) -> dict:
    """列表用的"这家公司的收银台"过滤；一个都没有时给一个匹配不到任何行的哨兵。"""
    names = names_in_company(company)
    return {"terminal": ["in", names]} if names else {"terminal": "__missing__"}


def update_terminal(name, internal=False, **changes) -> dict:
    if not internal:
        guard.require("cashier.manage")
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(DOCTYPE, name)
    for field in ("terminal_name", "warehouse"):
        if field in changes:
            doc.set(field, changes[field])
    for field in ("is_default", "is_active"):
        if field in changes:
            doc.set(field, 1 if changes[field] in (True, 1, "1", "true") else 0)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}
