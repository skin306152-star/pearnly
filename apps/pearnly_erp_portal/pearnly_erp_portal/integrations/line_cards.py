"""LINE 预览卡。

版式与字段顺序照旧线上的审核卡（`pearnly-app@ec2e5c95`：`services/line_erp/cards.py` →
`cowork_line/review_cards.py` → `line_platform/summary_review_card.py`，用户 2026-09-26 给的截图）：
标题「ตรวจสอบเอกสาร · ซื้อ/ขาย」+ 副标题，采购绿、销售红；左灰标签、右粗体值；
公司 / 方向 / 单据数，分隔线后是票面的 19 个字段（单号 … 备注），合计用主题色。

与旧卡不同的地方（用户口径）：
- 〔定·09-25〕只有「编辑」一个按钮：打开网页端原生的采购 / 销售页，提交才入账；卡片不过期。
- 〔定·09-26〕「字段细一点、商品名称全部显示」：在 LINE 里选的（商品类别 / 仓库、供应商或客户、
  付款）照实显示在对应行里；明细不再只报条数，每一行商品名、数量 × 单价 = 金额都列出来。
"""
import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from pearnly_erp_portal.integrations import line_flow

COLORS = {"purchase": "#16873E", "sales": "#B11B50"}
DOC_KINDS = {"purchase_invoice": "Purchase Invoice", "purchase_order": "Purchase Order",
             "expense": "Expense", "receipt": "Receipt", "quotation": "Quotation"}
# 一张气泡的 JSON 有大小上限（LINE 30 KB）：明细按实际大小列，放不下的报「还有 N 项」。
# 实测一行泰文商品约 570 字节，40 行就到 32.5 KB（09-26）；留一点余量。
MAX_BYTES = 28 * 1024


def _money(value) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{Decimal(str(value)):,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def _number(value) -> str:
    try:
        return f"{Decimal(str(value)).normalize():f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value or "")


def _be_date(value) -> str:
    """旧卡片显示佛历日期（วันที่ พ.ศ.），读不懂的原样给人看。"""
    try:
        year, month, day = str(value)[:10].split("-")
        return f"{int(day):02d}/{int(month):02d}/{int(year) + 543}"
    except (ValueError, TypeError):
        return str(value or "-")


def _kv(label, value, *, accent=None):
    return {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
        {"type": "text", "text": str(label), "size": "xxs", "color": accent or "#777777",
         "weight": "bold" if accent else "regular", "flex": 2, "wrap": True},
        {"type": "text", "text": str(value) if value not in (None, "") else "-", "size": "sm" if accent else "xxs",
         "weight": "bold", "color": accent or "#1B1B2B", "align": "end", "flex": 3, "wrap": True}]}


def _separator():
    return {"type": "separator", "margin": "md", "color": "#EEEAF7"}


def _branch(kind, number=None, name=None):
    if kind in ("head_office", "head"):
        return _("Head Office")
    if kind == "branch":
        return _("Branch {0}").format(" ".join(str(part) for part in (number, name) if part))
    return "-"


def _company(doc):
    row = frappe.db.get_value("ERP Company", doc.company, ["company_name", "tax_id", "branch", "address"],
                              as_dict=True) or {}
    return {"name": row.get("company_name") or doc.company, "tax_id": row.get("tax_id"),
            "branch": row.get("branch") or "-", "address": row.get("address")}


def _chosen_party(doc):
    """LINE 里选的对方：兜底（一般供应商 / 散客）、主档里的一家，或「按票面识别」（None）。"""
    if doc.party == line_flow.FALLBACK:
        return {"name": _(line_flow.FALLBACK_LABELS[doc.direction])}
    if doc.party and doc.party != line_flow.FROM_DOCUMENT:
        if doc.direction == "purchase":
            row = frappe.db.get_value("ERP Supplier", doc.party, ["supplier_name", "tax_id", "branch_type",
                                      "branch_no", "branch_name", "address"], as_dict=True) or {}
            return {"name": row.get("supplier_name") or doc.party, "tax_id": row.get("tax_id"),
                    "branch": _branch(row.get("branch_type"), row.get("branch_no"), row.get("branch_name")),
                    "address": row.get("address")}
        row = frappe.db.get_value("ERP Customer", doc.party, ["customer_name", "tax_id", "address"],
                                  as_dict=True) or {}
        return {"name": row.get("customer_name") or doc.party, "tax_id": row.get("tax_id"),
                "address": row.get("address")}
    return None


def _parties(doc, result):
    """(卖方, 买方)。采购：卖方是供应商、买方是我方公司；销售反过来。"""
    ours, chosen = _company(doc), _chosen_party(doc)
    if doc.direction == "purchase":
        seller = chosen or {"name": result.get("supplier_name"), "tax_id": result.get("supplier_tax_id"),
                            "branch": _branch(result.get("supplier_branch_type"), result.get("supplier_branch_no"),
                                              result.get("supplier_branch_name")),
                            "address": result.get("supplier_address")}
        return seller, ours
    buyer = chosen or {"name": result.get("buyer_name") or _("Unknown buyer"),
                       "address": result.get("buyer_address") or result.get("address")}
    return ours, buyer


def _party_rows(role, party):
    """对方那一组：名称、税号、分店、地址（标签沿用旧卡片的「ชื่อผู้ขาย / ชื่อผู้ซื้อ」等）。"""
    return [_kv(_(role), party.get("name")), _kv(_(role + " tax ID"), party.get("tax_id")),
            _kv(_(role + " branch"), party.get("branch")), _kv(_(role + " address"), party.get("address"))]


def _payment(doc):
    if not doc.is_paid:
        return _("Unpaid")
    return _(line_flow.METHOD_LABELS.get(doc.payment_method, "Other")) if doc.payment_method else _("Paid")


def _item_kind(doc):
    kind = _("Stock item") if doc.item_kind == "goods" else _("Service")
    if doc.item_kind == "goods" and doc.warehouse:
        kind += " · " + (frappe.db.get_value("ERP Warehouse", doc.warehouse, "warehouse_name") or doc.warehouse)
    return kind


def _items(result, totals, shown):
    lines = result.get("lines") or []
    computed = (totals or {}).get("lines") or []
    rows = [{"type": "text", "text": f"{_('Items')} · {len(lines)}", "size": "sm", "weight": "bold",
             "color": "#30295F", "wrap": True}]
    for index, row in enumerate(lines[:shown]):
        amount = computed[index].get("line_total") if index < len(computed) else None
        detail = " ".join(part for part in (_number(row.get("qty") or 1), str(row.get("unit") or ""),
                                            "×", _money(row.get("unit_price"))) if part)
        if row.get("discount") not in (None, "", 0, "0"):
            detail += f" − {_money(row.get('discount'))}"
        rows.append({"type": "box", "layout": "horizontal", "margin": "md", "contents": [
            {"type": "text", "text": f"{index + 1}. {row.get('description') or '-'}", "size": "xxs",
             "color": "#1B1B2B", "flex": 5, "wrap": True},
            {"type": "text", "text": _money(amount) if amount is not None else " ", "size": "xxs",
             "weight": "bold", "color": "#1B1B2B", "align": "end", "flex": 2, "wrap": True}]})
        rows.append({"type": "text", "text": detail, "size": "xxs", "color": "#6F688C", "wrap": True})
    if len(lines) > shown:
        rows.append({"type": "text", "size": "xxs", "color": "#6F688C", "margin": "sm",
                     "text": _("{0} more items").format(len(lines) - shown)})
    if not lines:
        rows.append({"type": "text", "size": "xxs", "color": "#6F688C", "text": _("No items were read")})
    # 明细不加底色（用户 09-26）：与上面的字段同一块白底。
    return {"type": "box", "layout": "vertical", "margin": "md", "contents": rows}


def _review(result):
    """没过识别检查（`recognition.checks`）：卡片最上面用红字写明要核对什么。"""
    from pearnly_erp_portal.recognition import policy

    texts = policy.describe(result.get("review") or [])
    if not texts:
        return []
    return [{"type": "box", "layout": "vertical", "margin": "none", "paddingAll": "8px", "cornerRadius": "6px",
             "borderWidth": "1px", "borderColor": "#D93025", "contents": [
                 {"type": "text", "text": _("Please check this document before submitting"), "size": "xs",
                  "weight": "bold", "color": "#D93025", "wrap": True},
                 *({"type": "text", "text": "• " + text, "size": "xxs", "color": "#D93025", "wrap": True}
                   for text in texts)]},
            _separator()]


def _amounts(doc, totals):
    """(税前, 折扣, VAT, 预扣, 合计)。税前 = 合计 − VAT：含税价与不含税价同一个口径。"""
    if not totals:
        return None, None, None, None, None
    grand, vat = totals.get("grand_total"), totals.get("vat_amount")
    before = Decimal(str(grand)) - Decimal(str(vat)) if grand not in (None, "") and vat not in (None, "") else None
    discount = Decimal(str(totals.get("discount_total") or 0))
    if doc.direction == "purchase":
        # 采购的 `discount_total` 只是整单折扣，行折扣另算（`purchasing/amounts.py`）。
        discount += sum((Decimal(str(row.get("discount") or 0)) for row in totals.get("lines") or []), Decimal(0))
    wht = totals.get("wht_amount")
    wht = wht if wht not in (None, "") and Decimal(str(wht)) else None
    return before, discount or None, vat, wht, grand


def preview(doc, result, totals) -> dict:
    """一张 Flex 气泡：旧卡片的字段顺序 + LINE 里的选择 + 全部明细，只有「编辑」。"""
    shown = len(result.get("lines") or [])
    while True:
        card = _card(doc, result, totals, shown)
        if shown == 0 or len(json.dumps(card, ensure_ascii=False).encode()) <= MAX_BYTES:
            return card
        shown -= 1


def _card(doc, result, totals, shown) -> dict:
    accent, head = COLORS[doc.direction], _(line_flow.DIRECTION_LABELS[doc.direction])
    seller, buyer = _parties(doc, result)
    before, discount, vat, wht, grand = _amounts(doc, totals)
    kind = result.get("doc_kind")
    body = [
        *_review(result),
        _kv(_("Company"), _company(doc)["name"]),
        _kv(_("Direction"), head),
        _kv(_("Documents"), "1"),
        _kv(_("Item Type"), _item_kind(doc)),
        _separator(),
        _kv(_("Document no."), result.get("doc_no")),
        _kv(_("Date (BE)"), _be_date(result["doc_date"]) if result.get("doc_date") else "-"),
        _kv(_("Document type"), _(DOC_KINDS[kind]) if kind in DOC_KINDS else "-"),
        # 采购只列卖方（供应商，票面识别或 LINE 里选的），销售只列买方（客户）；我方公司已在上面
        # 「公司」一行（用户 2026-09-26）。
        *_party_rows("Seller" if doc.direction == "purchase" else "Buyer",
                     seller if doc.direction == "purchase" else buyer),
        _kv(_("Notes"), result.get("notes")),
        _separator(),
        _items(result, totals, shown),
        # 金额与付款放到明细下面（用户 2026-09-26）。
        _separator(),
        _kv(_("Subtotal"), _money(before) if before is not None else "-"),
        _kv(_("Discount"), _money(discount) if discount else "-"),
        _kv(_("VAT"), _money(vat)),
        _kv(_("WHT amount"), _money(wht) if wht else "-"),
        _kv(_("Document total"), _money(grand), accent=accent),
        _kv(_("Currency"), result.get("currency") or "THB"),
        _kv(_("Payment method"), _payment(doc)),
    ]
    title = f"{_('Review document')} · {head}"
    button = {"type": "button", "style": "primary", "height": "sm", "color": accent,
              "action": {"type": "uri", "label": _("Edit")[:20], "uri": editor_link(doc)}}
    alt = f"{title} · {_money(grand)}" if grand not in (None, "") else title
    return {"type": "flex", "altText": alt[:400],
            "contents": {"type": "bubble", "size": "mega",
                "header": {"type": "box", "layout": "vertical", "backgroundColor": accent, "paddingAll": "16px",
                           "contents": [
                               {"type": "text", "text": title, "size": "lg", "weight": "bold",
                                "color": "#FFFFFF", "wrap": True},
                               {"type": "text", "text": _("Document summary · open details to review and post"),
                                "size": "xxs", "color": "#F4F4F4", "margin": "xs", "wrap": True}]},
                "body": {"type": "box", "layout": "vertical", "paddingAll": "16px", "contents": body},
                "footer": {"type": "box", "layout": "vertical", "paddingAll": "14px", "contents": [button]}}}


def editor_link(doc) -> str:
    """网页端原生页里「从这张 LINE 单据打开」的地址（LINE 内置浏览器打开；没登录先走正常登录）。"""
    root = "pearnly-purchase" if doc.direction == "purchase" else "pearnly-sales"
    return f"{frappe.utils.get_url().rstrip('/')}/desk/{root}/line/{doc.name}"
