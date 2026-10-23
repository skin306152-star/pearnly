"""Bounded transaction lookups; selecting a master does not grant its management page."""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.masters import company as companies

KINDS = {
    "product": ("ERP Product", "product_name", ("code", "barcode", "qr_payload")),
    "supplier": ("ERP Supplier", "supplier_name", ("express_code", "tax_id", "name")),
    "customer": ("ERP Customer", "customer_name", ("express_code", "tax_id", "name")),
}
FIELDS = {
    "product": ("name", "product_name", "code", "barcode", "qr_payload", "base_unit", "unit",
                "unit_price", "price_set", "default_cost", "vat_applicable", "default_location",
                "track_batch", "track_expiry"),
    "supplier": ("name", "supplier_name", "express_code", "tax_id", "branch_type", "branch_no", "branch_name", "address"),
    "customer": ("name", "customer_name", "express_code", "tax_id", "branch_type", "branch_no", "party_type", "address"),
}


def for_company(rows, company):
    """同名 / 同税号的几条档里这家公司该用的：本公司从 Express 同步来的优先，其次没挂 Express 的；别家公司从 Express
    同步来的不认（那是别家账套的编码，写进本公司账套认不出）。``company`` 没给就不分。"""
    if not company:
        return list(rows)
    own = [row for row in rows if row.get("express_company") == company]
    return own or [row for row in rows if not row.get("express_company")]


def match_party(kind, name, *, tax_id=None, branch_type=None, branch_no=None, company=None, ambiguous="throw"):
    """Resolve only an unambiguous active master; a tax identity outranks spelling.

    ``company``：按 :func:`for_company` 只在这家公司该用的档里认。``ambiguous="none"``：认出几条时不报错、回 None
    （识别后预选用，交给人选）。"""
    doctype, title, _codes = KINDS[kind]
    filters = {"is_active": 1}
    if tax_id:
        filters["tax_id"] = str(tax_id).strip()
        if branch_type == "branch":
            filters.update(branch_type="branch", branch_no=str(branch_no or "").strip())
        elif branch_type in ("head", "head_office", "hq"):
            filters["branch_type"] = ["in", ["head", "head_office", "hq"]]  # 客户的总店记作 hq，供应商记作 head_office
    elif name:
        filters[title] = name
    else:
        return None
    rows = for_company(frappe.get_all(doctype, filters=filters, fields=["name", "express_company", title],
                                      limit_page_length=50), company)
    if len(rows) > 1:
        rows = _in_use(kind, rows, name, title, company)
    if len(rows) > 1:
        if ambiguous == "none":
            return None
        frappe.throw(_("Several matches found for {0}. Please select a record.").format(name or tax_id))
    return rows[0]["name"] if rows else None


USED_BY = {"supplier": ("Purchase Document", "supplier"), "customer": ("ERP Sales Document", "customer")}


def _plain(text):
    return " ".join(str(text or "").replace("\xa0", " ").split()).casefold()


def _in_use(kind, rows, name, title, company):
    """同税号同分店有几条档（Express 里常见：旧档改名「ไม่ใช้」留着不删）时取哪条：名称完全相同的；否则这家公司
    最近在单据上用过的那条（在用的）；都分不出就原样交回（交人选）。"""
    same = [row for row in rows if name and _plain(row.get(title)) == _plain(name)]
    if len(same) == 1:
        return same
    doctype, field = USED_BY[kind]
    filters = {field: ["in", [row["name"] for row in rows]], "status": ["!=", "void"]}
    if company:
        filters["company"] = company
    used = frappe.get_all(doctype, filters=filters, pluck=field, order_by="creation desc", limit_page_length=1)
    return [row for row in rows if row["name"] == used[0]] if used else rows


def match_product(description, company=None):
    """明细行按品名认商品（没选商品时）：``(商品, 认出几条)``。按 :func:`for_company` 只在这家公司该用的商品里认；
    认出不止一条时商品给 None，调用方按各自口径处理（要人选）。"""
    rows = frappe.get_all("ERP Product", filters={"product_name": description, "is_active": 1},
                          fields=["name", "express_company"], limit_page_length=50)
    rows = for_company(rows, company)
    return (rows[0]["name"] if len(rows) == 1 else None), len(rows)


@frappe.whitelist()
def api_choices(kind, context, txt="", scan=False, company=None):
    if context not in ("purchase", "sales") or kind not in KINDS:
        frappe.throw(_("Invalid value"))
    if (kind == "supplier" and context != "purchase") or (kind == "customer" and context != "sales"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    guard.require(context + ".read")
    doctype, title, codes = KINDS[kind]
    text = str(txt or "").strip()[:500]
    exact = scan in (True, 1, "1", "true")
    if exact and not text:
        return []
    fields = list(FIELDS[kind])
    if kind == "product" and not guard.cost_visible():
        fields.remove("default_cost")
    filters = {"is_active": 1}
    matches = [[field, "=" if exact else "like", text if exact else f"%{text}%"]
               for field in (title, *codes)] if text else None
    # This endpoint intentionally grants only a bounded selection projection, under
    # transaction capability. Generic master CRUD still requires its own module role.
    if company and company not in companies.allowed_companies():
        company = None  # 页面带来的公司不是本人能用的：不按它分（只影响排序与过滤，不放宽权限）
    if company:
        # 这张单的公司：别家公司从 Express 同步来的档不列（`for_company`），本公司同步来的排前
        fields.append("express_company")
        filters["express_company"] = ["in", [company, ""]]
    rows = frappe.get_all(doctype, filters=filters, or_filters=matches, fields=fields,
                          order_by=f"{title} asc, name asc", limit_page_length=20)
    if company:
        rows.sort(key=lambda row: row.get("express_company") != company)
    if exact and kind == "product":
        parents = frappe.get_all("ERP Product Unit", filters={"barcode": text}, pluck="parent", limit_page_length=20)
        if parents:
            extra = frappe.get_all(doctype, filters={**filters, "name": ["in", parents]}, fields=fields,
                                   limit_page_length=20)
            rows = list({row.name: row for row in [*rows, *extra]}.values())
    return rows
