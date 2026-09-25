"""LINE 预览卡。

版式照旧线上的审核卡（`services/line_erp/cards.py`）：标题「ตรวจสอบ · ซื้อ/ขาย」，采购绿、
销售红；内容按用户 2026-09-25 的口径是「已选主档信息 + 识别字段」。按钮只有「编辑」：
打开网页端原生的采购 / 销售页，提交才入账；卡片不过期，入账后再点是查看页。
"""
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from pearnly_erp_portal.integrations import line_flow

COLORS = {"purchase": "#16873E", "sales": "#B11B50"}
HEADS = {"purchase": "ซื้อ", "sales": "ขาย"}
MAX_LINES = 5


def _money(value) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value or "")


def _row(label, value, *, bold=False):
    return {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
        {"type": "text", "text": str(label), "size": "sm", "color": "#8C8C8C", "flex": 3, "wrap": True},
        {"type": "text", "text": str(value or "-"), "size": "sm", "flex": 6, "wrap": True,
         **({"weight": "bold"} if bold else {})}]}


def _party(doc, result):
    purchase = doc.direction == "purchase"
    label = _("Supplier") if purchase else _("Customer")
    if doc.party == line_flow.FALLBACK:
        return label, _(line_flow.FALLBACK_LABELS[doc.direction])
    if doc.party and doc.party != line_flow.FROM_DOCUMENT:
        master, title = ("ERP Supplier", "supplier_name") if purchase else ("ERP Customer", "customer_name")
        return label, frappe.db.get_value(master, doc.party, title) or doc.party
    name = result.get("supplier_name") if purchase else result.get("buyer_name")
    return label, name or _("Not found on the document")


def _choices(doc):
    company = frappe.db.get_value("ERP Company", doc.company, "company_name") or doc.company
    paid = _("Paid") if doc.is_paid else _("Unpaid")
    if doc.is_paid and doc.payment_method:
        paid += f" ({_(line_flow.METHOD_LABELS.get(doc.payment_method, 'Other'))})"
    kind = _("Stock item") if doc.item_kind == "goods" else _("Service")
    if doc.item_kind == "goods" and doc.warehouse:
        kind += " · " + (frappe.db.get_value("ERP Warehouse", doc.warehouse, "warehouse_name") or doc.warehouse)
    return [(_("Company"), company), (_("Payment"), paid), (_("Item Type"), kind)]


def preview(doc, result, totals) -> dict:
    """一张 Flex 气泡：主档信息、识别字段、明细（最多 5 行）、合计，三个按钮。"""
    party_label, party = _party(doc, result)
    head = [_row(label, value) for label, value in _choices(doc)]
    head.insert(1, _row(party_label, party))
    fields = []
    if doc.direction == "purchase":
        fields.append(_row(_("Document No"), result.get("doc_no")))
        if result.get("supplier_tax_id"):
            fields.append(_row(_("Tax ID"), result.get("supplier_tax_id")))
    fields.append(_row(_("Date"), result.get("doc_date")))
    lines = result.get("lines") or []
    items = [{"type": "text", "wrap": True, "size": "sm",
              "text": f"{row.get('description') or '-'}  {row.get('qty') or ''} × {_money(row.get('unit_price'))}"}
             for row in lines[:MAX_LINES]]
    if len(lines) > MAX_LINES:
        items.append({"type": "text", "size": "xs", "color": "#8C8C8C",
                      "text": _("{0} more items").format(len(lines) - MAX_LINES)})
    money = []
    if totals:
        money = [_row(_("Subtotal"), _money(totals.get("subtotal"))),
                 _row(_("VAT"), _money(totals.get("vat_amount"))),
                 _row(_("Total"), _money(totals.get("grand_total")), bold=True)]
    separator = {"type": "separator", "margin": "md"}
    body = [*head, separator, *fields, separator, *(items or [{"type": "text", "size": "sm",
            "text": _("No items were read")}]), *([separator, *money] if money else [])]
    title = "ตรวจสอบ · " + HEADS[doc.direction]
    button = {"type": "button", "style": "primary", "color": COLORS[doc.direction], "height": "sm",
              "action": {"type": "uri", "label": _("Edit"), "uri": editor_link(doc)}}
    return {"type": "flex", "altText": title + " · " + _money((totals or {}).get("grand_total") or ""),
            "contents": {"type": "bubble", "size": "mega",
                "header": {"type": "box", "layout": "vertical", "backgroundColor": COLORS[doc.direction],
                           "paddingAll": "md", "contents": [{"type": "text", "text": title,
                           "color": "#FFFFFF", "weight": "bold", "size": "md"}]},
                "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
                "footer": {"type": "box", "layout": "vertical", "contents": [button]}}}


def editor_link(doc) -> str:
    """网页端原生页里「从这张 LINE 单据打开」的地址（LINE 内置浏览器打开；没登录先走正常登录）。"""
    root = "pearnly-purchase" if doc.direction == "purchase" else "pearnly-sales"
    return f"{frappe.utils.get_url().rstrip('/')}/desk/{root}/line/{doc.name}"
