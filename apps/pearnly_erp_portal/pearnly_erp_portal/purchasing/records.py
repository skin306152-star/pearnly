"""One permission-aware query for purchase lists, totals and exports."""
from decimal import Decimal

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.purchasing import access, amounts

FIELDS = ("name", "system_no", "doc_no", "doc_date", "creation", "supplier", "supplier_name",
          "summary", "doc_kind", "source", "status", "payment_status", "currency",
          "fx_rate", "grand_total", "vat_amount", "wht_amount", "net_payable", "paid_amount", "stock_posted_at")


def query(params=None):
    params = params or {}
    filters = [[key, "=", value] for key, value in guard.scope_filters().items()]
    for key in ("status", "supplier", "payment_status", "doc_kind"):
        value = params.get(key)
        if value:
            filters.append([key, "in" if isinstance(value, list) else "=", value])
    if params.get("group") == "expense":
        filters.append(["doc_kind", "=", "expense"])
    elif params.get("group") == "purchase":
        filters.append(["doc_kind", "!=", "expense"])
    elif params.get("group") == "unpaid":
        filters.extend([["payment_status", "!=", "paid"], ["status", "!=", "void"]])
    datefield = "creation" if params.get("date_basis") == "upload" else "doc_date"
    if params.get("month"):
        month_start = frappe.utils.getdate(str(params["month"]) + "-01")
        filters.extend([[datefield, ">=", str(month_start)],
                        [datefield, "<", str(frappe.utils.add_months(month_start, 1))]])
    if params.get("date_from"):
        filters.append([datefield, ">=", str(frappe.utils.getdate(params["date_from"]))])
    if params.get("date_to"):
        end = str(frappe.utils.getdate(params["date_to"])) + (" 23:59:59.999999" if datefield == "creation" else "")
        filters.append([datefield, "<=", end])
    if params.get("category"):
        filters.append(["Purchase Line", "category", "in", params["category"]])
    text = str(params.get("q") or "").strip()
    or_filters = [[field, "like", "%" + text + "%"] for field in
                  ("supplier_name", "doc_no", "system_no", "summary")] if text else None
    return filters, or_filters, datefield


def rows(params=None, *, start=0, limit=20):
    guard.require("purchase.read")
    filters, or_filters, datefield = query(params)
    result = frappe.get_list("Purchase Document", filters=filters, or_filters=or_filters,
        fields=list(FIELDS), distinct=True, order_by=f"{datefield} desc, creation desc",
        limit_start=max(0, int(start)), limit_page_length=max(0, int(limit)))
    for row in result:
        row["attachment_count"] = frappe.db.count("ERP Purchase Attachment", {"parent": row.name})
        categories = frappe.get_all("Purchase Line", filters={"parent": row.name}, pluck="category")
        row["categories"] = ", ".join(filter(None, [frappe.db.get_value("ERP Purchase Category", key, "category_name")
                                                    for key in dict.fromkeys(categories) if key]))
    return result


def overview(params=None):
    data = rows(params, limit=0)
    total = {key: Decimal(0) for key in ("purchase_total", "expense_total", "vat_creditable", "unpaid_total")}
    counts = {"purchase_count": 0, "expense_count": 0}
    suppliers = set()
    months = {}
    for row in data:
        basis = row.creation if (params or {}).get("date_basis") == "upload" else row.doc_date
        month = str(basis)[:7]
        months.setdefault(month, {"month": month, "count": 0, "draft_count": 0, "amount": Decimal(0)})
        months[month]["count"] += 1
        months[month]["draft_count"] += int(row.status == "draft")
        if row.status != "submitted":
            continue
        rate = amounts.decimal(row.fx_rate or 1)
        kind = "expense" if row.doc_kind == "expense" else "purchase"
        total[kind + "_total"] += amounts.money(amounts.decimal(row.grand_total) * rate)
        counts[kind + "_count"] += 1
        total["vat_creditable"] += amounts.money(amounts.decimal(row.vat_amount) * rate)
        outstanding = amounts.money((amounts.decimal(row.net_payable) - amounts.decimal(row.paid_amount)) * rate)
        total["unpaid_total"] += outstanding
        if outstanding > 0:
            suppliers.add(row.supplier or row.supplier_name)
        months[month]["amount"] += amounts.money(amounts.decimal(row.grand_total) * rate)
    return {"total": len(data), **{k: str(v) for k, v in total.items()}, **counts,
            "unpaid_suppliers": len(suppliers), "months": [dict(v, amount=str(v["amount"])) for v in months.values()]}


def get(name):
    doc = access.get_document(name)
    data = doc.as_dict()
    # These three controls did not exist on version-one records. Derive historical
    # facts instead of displaying the new-document defaults added by the schema.
    if doc.calculation_version == 1:
        data.update(is_paid=int(doc.payment_status == "paid"),
                    update_stock=int(bool(doc.stock_posted_at)), auto_create_product=0)
    return data


def categories():
    guard.require("purchase.read")
    return frappe.get_list("ERP Purchase Category", fields=["name", "category_name", "parent_category", "is_active"],
                           order_by="sort_order, category_name", limit_page_length=0)


def category(name, parent_category=None):
    guard.require("purchase.write")
    if parent_category:
        parent = frappe.get_doc("ERP Purchase Category", parent_category)
        parent.check_permission("read")
        if parent.parent_category:
            frappe.throw(_("Invalid value"))
    doc = frappe.get_doc({"doctype": "ERP Purchase Category", "category_name": name,
                          "parent_category": parent_category, "is_active": 1}).insert()
    return {"name": doc.name}
