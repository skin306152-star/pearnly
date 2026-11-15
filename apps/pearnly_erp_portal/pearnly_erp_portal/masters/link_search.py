"""客户 / 供应商 / 物料选择框的搜索：原生 Link 的 ``get_query`` 服务端查询（公司接入重做第 2 批，2026-11-14）。

11-14 生产只读：testerp CO-00146（同步了 1,079 个启用物料）在物料调整的物料框里不打字只看到手工建的几条——原生链接搜索按
最近修改排、只回一页，本公司账本里的档挤不进去，账本编码又不在搜索字段里。这里照原生查询函数的签名（`frappe.desk.search`
调 ``get_query`` 的 ``query``）按类给三种主档：

- 范围同权限口径（`masters.access.scope_sql`）：本公司的账本档 + 本地档；公司取选择框给的（单据的公司），本人用不了那家时退回当前公司；
- 顺序：本公司账本档在前，其次本地档，各自按名称；
- 打字按名称、编码、账本编码、条码、税号等（各 DocType 的 ``search_fields``）模糊找，账本编码与条码完全相同的排最前；
- 服务端分页（``start`` / ``page_len``），每页最多 :data:`PAGE_MAX`。

前端在共用的 Link 控件里按类挂上（`public/desk/company-connect.js` 里的 ``p.link_query_wrap``），各页原来给的条件（``is_active`` 等）照带。
"""
import frappe
from frappe import _

from pearnly_erp_portal.masters import access, company as companies

PAGE_MAX = 50
TITLES = {"ERP Product": "product_name", "ERP Customer": "customer_name", "ERP Supplier": "supplier_name"}
EXACT = ("express_code", "code", "barcode")  # 打的字与这些列完全相同的排最前（扫码、按编码打）
FILTERABLE = {"is_active", "party_type", "track_serial", "express_stock", "category"}


def _company(wanted) -> str:
    allowed = companies.allowed_companies(frappe.session.user)
    return wanted if wanted in allowed else companies.active_company()


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search(doctype, txt, searchfield, start, page_len, filters):
    if doctype not in TITLES:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not frappe.has_permission(doctype, "select") and not frappe.has_permission(doctype, "read"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    filters = dict(filters or {})
    company = _company(filters.pop("company", None))
    title = TITLES[doctype]
    meta = frappe.get_meta(doctype)
    columns = [field for field in (meta.search_fields or "").split(",") if field and meta.has_field(field.strip())]
    columns = list(dict.fromkeys([title, *[field.strip() for field in columns]]))
    scope, values = access.scope_sql(company)
    where = [scope]
    for key, value in filters.items():
        # 只收「某列 = 某值」（前端 `p.link_query_wrap` 同一份名单）：认不得的条件不丢掉（丢了会放宽范围），直接拒
        if key not in FILTERABLE or not meta.has_field(key) or isinstance(value, (list, dict)):
            frappe.throw(_("Invalid request"))
        where.append(f"`{key}` = %(f_{key})s")
        values[f"f_{key}"] = value
    text = " ".join(str(txt or "").split())
    exact = ""
    if text:
        values["like"] = "%" + "%".join(text.split()) + "%"
        values["text"] = text
        where.append("(" + " or ".join(["`name` like %(like)s", *[f"`{column}` like %(like)s" for column in columns]]) + ")")
        exact = " or ".join(f"`{column}` = %(text)s" for column in EXACT if meta.has_field(column))
    values["own"] = company
    values["start"] = max(0, int(start or 0))
    values["page_len"] = max(1, min(int(page_len or 20), PAGE_MAX))
    described = [column for column in columns if column != title][:3]
    order = [f"({exact}) desc" if exact else "", f"(`{access.OWNER}` = %(own)s) desc", f"`{title}` asc", "`name` asc"]
    return frappe.db.sql(
        f"""select `name`, `{title}`, {", ".join(f"`{column}`" for column in described) or "''"}
            from `tab{doctype}` where {" and ".join(where)}
            order by {", ".join(part for part in order if part)} limit %(start)s, %(page_len)s""",
        values)
