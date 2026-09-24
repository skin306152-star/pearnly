"""仓库。**仓库属于公司**（官方也是这么分的：`Warehouse.company`），库存因此天然按公司分开 ——
`ERP Stock Balance` / `ERP Stock Movement` 都按仓库记账，不用再加一列。

**没有"默认仓"这个概念**（用户 2026-09-24："不要做默认仓，依赖的全部删掉"）：
仓库是"这家公司名下第几个仓"这样一份平铺的清单，哪张单据、哪台收银台用哪个仓，
一律**写在那个地方自己身上**（单据上的仓库字段、收银台的仓库字段）。

需要"随便给我一个仓"的地方（建单没写仓、出入库没选仓）只有一条兜底：
**这家公司只有一个启用的仓就用它；有多个就报错让你去选** —— 不替你猜一个，
那是"默认仓"换个名字。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP Warehouse"
NOT_FOUND = "Warehouse not found"
NAME_REQUIRED = "A warehouse needs a name"

FIELDS = ("name", "warehouse_name", "company", "is_active", "note")
# 没指定仓库、而这家公司又有多个仓时那句提示（不给隐式默认）。
WAREHOUSE_REQUIRED = "Pick a warehouse"
# 建仓必须挂在某家公司名下（用户 2026-09-24："没有绑定公司不可以建仓库"）。
COMPANY_REQUIRED = "A warehouse must belong to a company"


def company_of(name) -> str:
    """Authorize the company saved on a warehouse, independent of the active company.

    Historical documents and reversals use their saved warehouse, including an
    inactive one. New movements still pass through required() to check activity.
    """
    owner = frappe.db.get_value(DOCTYPE, name, "company") if name else None
    if not owner:
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return companies.document_company(owner)


def resolve(value=None, *, create=False, company=None):
    """按名字取一个**已存在**的仓库（官方原生口径：仓库是主数据，不靠自由填写凭空长出来）。

    只有显式传 `create=True` 的调用方才允许按名字新建（开通/实验室造数据那些路径）。
    **留空不再等于"默认仓"**：这里只回 None，让调用方自己决定（大多数路径用 `required()`）。
    """
    company = companies.document_company(company)
    value = str(value or "").strip()
    if not value:
        return None
    if len(value) > 140:
        frappe.throw(_("Invalid value"))
    name = frappe.db.exists(DOCTYPE, value) or frappe.db.get_value(
        DOCTYPE, {"warehouse_name": value, "company": company}, "name")
    if name:
        row = frappe.db.get_value(DOCTYPE, name, ["is_active", "company"], as_dict=True)
        if not row or not row.is_active or row.company != company:
            # 别家公司的仓库、或已停用：**一律报错**。静默返回空会让单据落到"没有仓库"上，
            # 那种错很难查（货记到哪去了？）。只有"查不到这个名字"才允许当空处理。
            frappe.throw(_(NOT_FOUND))
        return name
    if not create:
        return None
    # Serialize catalog creation, then re-read after a competing transaction commits.
    frappe.db.sql("select name from `tabDocType` where name=%s for update", (DOCTYPE,))
    name = frappe.db.get_value(DOCTYPE, {"warehouse_name": value, "company": company}, "name")
    if name:
        return name
    doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": value, "company": company,
                          "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def sole(company=None) -> str | None:
    """这家公司**唯一**的那个启用仓；有多个或一个都没有时回 None。

    "唯一"是**事实判断**，不是标记：不存"谁是默认"这种配置位（那是"默认仓"换个名字）。
    """
    names = frappe.get_all(DOCTYPE, filters={"company": companies.document_company(company),
                                             "is_active": 1},
                           pluck="name", limit_page_length=2)
    return names[0] if len(names) == 1 else None


def required(value=None, *, company=None) -> str:
    """建单/过账时取仓库：给了名字就用它（必须是这家公司启用着的仓）。

    没给就**只认"这家公司只有一个启用仓"这一种情况**，多个仓时明确报错让你去选 ——
    没有"默认仓"这种替你拿主意的东西（见本模块抬头）。
    """
    if str(value or "").strip():
        resolved = resolve(value, create=False, company=company)
        if not resolved:
            frappe.throw(_(NOT_FOUND))
        return resolved
    only = sole(company)
    if not only:
        frappe.throw(_(WAREHOUSE_REQUIRED))
    return only


def receiving(value=None, *, company=None) -> str:
    """**单据收发用哪个仓**：单据上明确的仓 → 这家公司**卖货的那个仓** → 唯一的仓 → 报错。

    比 `required()` 多中间那一步，是因为"进货"和"卖货"必须是同一个仓，库存的数量与金额
    才连得上（用户 2026-09-24："进货就进卖货那个仓"）。卖货仓在收银台上写着，见
    `masters.company.selling_warehouse`；没有收银台（或几台指向不同的仓）就退回原来那条
    "唯一的仓 / 报错"，不替谁猜一个。
    """
    if str(value or "").strip():
        return required(value, company=company)
    company = companies.document_company(company)
    shop = companies.selling_warehouse(company)
    if shop and frappe.db.get_value(DOCTYPE, shop, "is_active"):
        return shop
    return required(None, company=company)


def create_warehouse(*, warehouse_name, note=None, company=None, is_active=True) -> dict:
    """建一个仓。**必须明确挂在某家公司名下** —— 仓库属于公司（`Warehouse.company`），
    没有公司就没有归属，库存也就没法按公司分开算（用户 2026-09-24："没有绑定公司不可以建仓库"）。
    所以这里不再"没给就用当前公司"兜底：调用方（页面/建公司那一路）要把公司传进来。
    """
    guard.require("warehouses.manage")
    if not str(company or "").strip():
        frappe.throw(_(COMPANY_REQUIRED))
    company = companies.document_company(company)
    name = (warehouse_name or "").strip()
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    if frappe.db.exists(DOCTYPE, {"warehouse_name": name, "company": company}):
        frappe.throw(_("This warehouse already exists"))
    doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": name, "company": company,
                          "is_active": 1 if is_active in (True, 1, "1", "true") else 0,
                          "note": note})
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def ensure_for_company(company, name=None) -> str:
    """给某家公司配好**第一个**仓库：建公司时就走这条路 —— 有新名字就按它建，没有就用 `Main`。
    已存在同名（这家公司的）就直接用它。**只负责"这家公司至少有一个仓"，不设任何"默认"标记**。幂等。
    """
    wanted = (name or "").strip() or "Main"
    existing = frappe.db.get_value(DOCTYPE, {"company": company, "warehouse_name": wanted}, "name")
    if not existing:
        doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": wanted, "company": company,
                              "is_active": 1})
        doc.insert(ignore_permissions=True)
        existing = doc.name
    return existing


def list_warehouses(*, include_inactive=False, limit=50, company=None) -> list[dict]:
    guard.require("stock.read")
    filters = {"company": companies.document_company(company)}
    if not include_inactive:
        filters["is_active"] = 1
    rows = frappe.get_list(DOCTYPE, filters=filters, fields=list(FIELDS),
                           order_by="warehouse_name asc",
                           limit_page_length=int(limit))
    # 每行带上**公司抬头**：仓库属于公司，界面上要看得见这个绑定（用户 2026-09-24：
    # "现在的仓库和公司数据是否联动"）—— 显示的是抬头，不是 `CO-00552`。
    names = {row["company"] for row in rows if row.get("company")}
    titles = {row["name"]: row["company_name"] for row in frappe.get_all(
        "ERP Company", filters={"name": ["in", sorted(names)]},
        fields=["name", "company_name"], limit_page_length=0)} if names else {}
    for row in rows:
        row["company_name"] = titles.get(row.get("company")) or row.get("company") or ""
    return rows


def names_of(company=None, *, include_inactive=True) -> list:
    """某家公司名下的仓库名。库存/出入库/盘点都按仓库记账，所以"看这家公司的库存"
    就是"看这些仓库"；单据上没有 company 列的那些表靠它过滤（`warehouse in (...)`）。"""
    company = company or companies.active_company()
    filters = {"company": company}
    if not include_inactive:
        filters["is_active"] = 1
    return frappe.get_all(DOCTYPE, filters=filters, pluck="name", order_by="creation asc")


def scope_filter(company=None) -> dict:
    """列表用的"这家公司的仓库"过滤；一家仓库都没有时给一个匹配不到任何行的哨兵。"""
    names = names_of(company)
    return {"warehouse": ["in", names]} if names else {"warehouse": "__missing__"}


def update_warehouse(name, **changes) -> dict:
    guard.require("warehouses.manage")
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(DOCTYPE, name)
    if "warehouse_name" in changes:
        cleaned = (changes["warehouse_name"] or "").strip()
        if not cleaned:
            frappe.throw(_(NAME_REQUIRED))
        doc.warehouse_name = cleaned
    if "is_active" in changes:
        doc.is_active = 1 if changes["is_active"] in (True, 1, "1", "true") else 0
    if "note" in changes:
        doc.note = changes["note"]
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}
