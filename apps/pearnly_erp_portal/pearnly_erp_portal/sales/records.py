"""One permission-aware query for sales lists, totals and exports."""
import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.sales import access

DOCTYPE = "ERP Sales Document"

FIELDS = ("name", "system_no", "doc_date", "creation", "customer", "buyer_name", "party_type",
          "summary", "doc_kind", "source", "status", "payment_status", "payment_method",
          "payment_date", "subtotal", "vat_amount", "grand_total", "paid_amount",
          "stock_posted_at")

# 搜索只搜"人会在搜索框里打的字"：客户、我方单号、明细摘要、备注。
SEARCH_FIELDS = ("buyer_name", "system_no", "summary", "note")


def query(params=None):
    """列表、合计与导出共用的一份条件。"""
    params = params or {}
    filters = [[key, "=", value] for key, value in guard.scope_filters().items()]
    for key in ("doc_kind", "payment_status", "source"):
        value = params.get(key)
        if value:
            filters.append([key, "in" if isinstance(value, list) else "=", value])
    group = params.get("group")
    if group in ("receipt", "quotation"):
        filters.append(["doc_kind", "=", group])
    elif group == "unpaid":
        filters.extend([["payment_status", "!=", "paid"], ["status", "!=", "void"]])
    datefield = "creation" if params.get("date_basis") == "upload" else "doc_date"
    if params.get("month"):
        month_start = frappe.utils.getdate(str(params["month"]) + "-01")
        filters.extend([[datefield, ">=", str(month_start)],
                        [datefield, "<", str(frappe.utils.add_months(month_start, 1))]])
    if params.get("date_from"):
        filters.append([datefield, ">=", str(frappe.utils.getdate(params["date_from"]))])
    if params.get("date_to"):
        end = str(frappe.utils.getdate(params["date_to"])) + (
            " 23:59:59.999999" if datefield == "creation" else "")
        filters.append([datefield, "<=", end])
    text = str(params.get("q") or "").strip()
    or_filters = [[field, "like", "%" + text + "%"] for field in SEARCH_FIELDS] if text else None
    return filters, or_filters, datefield


def rows(params=None, *, start=0, limit=20):
    guard.require("sales.read")
    filters, or_filters, datefield = query(params)
    data = frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, fields=list(FIELDS),
                           distinct=True, order_by=f"{datefield} desc, creation desc",
                           limit_start=max(0, int(start)), limit_page_length=max(0, int(limit)))
    for row in data:
        row["attachment_count"] = frappe.db.count("ERP Sales Attachment", {"parent": row.name})
    return data


def page(params=None, *, start=0, limit=20) -> dict:
    """列表一页 + 命中总数（同一个查询，翻页与导出不会各算一份）。"""
    return {"items": rows(params, start=start, limit=limit),
            "total": len(rows(params, limit=0))}


def get(name) -> dict:
    return access.get_document(name).as_dict()
