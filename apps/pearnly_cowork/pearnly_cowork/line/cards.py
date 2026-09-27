"""LINE 识别结果卡：每张票一张，版式照 ERP `pearnly_erp_portal.integrations.line_cards`（用户 2026-09-27：参考 ERP）。

标题「复核单据 · 进项/销项」+ 副标题，进项绿、销项红；左灰标签、右粗体值；上面是 LINE 里的选择
（目标 · 公司 · 年度、业务类型、入账方式），分隔线后是票面（单据号、日期、票种、对方名称 / 税号 / 分店 / 地址），
再是全部明细（名称、数量 × 单价 = 金额），最后是金额与付款。只有一个「编辑」按钮：打开网页编辑页
（复核、提交 / 导出都在网页做）。字段取识别记录的主页面，与复核页同一判据。
"""
import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from pearnly_core.tax_documents import codes as tax_codes

COLORS = {"purchase": "#16873E", "sales": "#B11B50"}
PAYMENTS = {"cash": "Cash", "credit": "Credit", "transfer": "Transfer", "promptpay": "PromptPay",
            "cheque": "Cheque", "card": "Card"}
# 一张气泡的 JSON 有大小上限（LINE 30 KB）：明细按实际大小列，放不下的报「还有 N 项」（ERP 同法）。
MAX_BYTES = 28 * 1024


def _money(value) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{Decimal(str(value).replace(',', '')):,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def _number(value) -> str:
    try:
        return f"{Decimal(str(value).replace(',', '')).normalize():f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value or "")


def _be_date(iso, raw) -> str:
    """日期按佛历年份显示（ERP 卡片同一口径，标签不写「佛历」）；没有公历日期就用票面原样。"""
    try:
        year, month, day = str(iso)[:10].split("-")
        return f"{int(day):02d}/{int(month):02d}/{int(year) + 543}"
    except (ValueError, TypeError):
        return str(raw or "-")


def _kv(label, value, *, accent=None):
    return {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
        {"type": "text", "text": str(label), "size": "xxs", "color": accent or "#777777",
         "weight": "bold" if accent else "regular", "flex": 2, "wrap": True},
        {"type": "text", "text": str(value) if value not in (None, "") else "-", "size": "sm" if accent else "xxs",
         "weight": "bold", "color": accent or "#1B1B2B", "align": "end", "flex": 3, "wrap": True}]}


def _separator():
    return {"type": "separator", "margin": "md", "color": "#EEEAF7"}


def _primary(pages):
    """主页面（`public/desk/intake-upload.js` 的 `primary_index` 同一判据）。"""
    keys = ("invoice_number", "total_amount", "seller_name")
    for page in pages:
        fields = (page or {}).get("fields") or {}
        if not page.get("is_copy") and not page.get("is_duplicate") and any(fields.get(key) for key in keys):
            return fields
    scored = [(sum(bool(((p or {}).get("fields") or {}).get(k)) for k in (*keys, "date")), p) for p in pages]
    best = max(scored, key=lambda item: item[0], default=(0, None))[1]
    return ((best or {}).get("fields") or {}) if best else {}


def _items(items, shown):
    rows = [{"type": "text", "text": f"{_('Items')} · {len(items)}", "size": "sm", "weight": "bold",
             "color": "#30295F", "wrap": True}]
    for index, row in enumerate(items[:shown]):
        rows.append({"type": "box", "layout": "horizontal", "margin": "md", "contents": [
            {"type": "text", "text": f"{index + 1}. {row.get('name') or '-'}", "size": "xxs",
             "color": "#1B1B2B", "flex": 5, "wrap": True},
            {"type": "text", "text": _money(row.get("subtotal")) if row.get("subtotal") not in (None, "") else " ",
             "size": "xxs", "weight": "bold", "color": "#1B1B2B", "align": "end", "flex": 2, "wrap": True}]})
        rows.append({"type": "text", "text": f"{_number(row.get('qty') or 1)} × {_money(row.get('price'))}",
                     "size": "xxs", "color": "#6F688C", "wrap": True})
    if len(items) > shown:
        rows.append({"type": "text", "size": "xxs", "color": "#6F688C", "margin": "sm",
                     "text": _("{0} more items").format(len(items) - shown)})
    if not items:
        rows.append({"type": "text", "size": "xxs", "color": "#6F688C", "text": _("No items were read")})
    return {"type": "box", "layout": "vertical", "margin": "md", "contents": rows}


def _party_rows(role, fields, prefix):
    return [_kv(_(role), fields.get(prefix + "_name")), _kv(_(role + " tax ID"), fields.get(prefix + "_tax")),
            _kv(_(role + " branch"), fields.get(prefix + "_branch")),
            _kv(_(role + " address"), fields.get(prefix + "_addr"))]


def _account(record, labels):
    """推到 ERP 的票写上推送账套（上传前用户选定、记在票上的那份）。"""
    if record.get("intake_target") not in ("express", "mrerp"):
        return []
    key = (record.get("push_connection"), record.get("push_account_set"))
    if key not in labels:
        from pearnly_cowork.connections import directory

        labels[key] = directory.account_label(*key) if all(key) else ""
    return [_kv(_("intake.target.accountSet"), labels[key])]


def _card(record, pending, shown, labels=None):
    fields = _primary(json.loads(record.pages or "[]"))
    direction = fields.get("direction") or pending.get("direction") or "purchase"
    accent = COLORS.get(direction, COLORS["purchase"])
    head = _("intake.dir.sales") if direction == "sales" else _("intake.dir.purchase")
    items = fields.get("items") or []
    kind = fields.get("document_type")
    payment = fields.get("posting_payment_manual") or fields.get("payment_method")
    body = [
        _kv(_("Target ERP"), pending.get("target")),
        *_account(record, {} if labels is None else labels),
        _kv(_("Transaction type"), head),
        *([_kv(_("Posting"), pending["mode"])] if pending.get("mode") else []),
        _separator(),
        _kv(_("Document no."), fields.get("invoice_number")),
        _kv(_("Date"), _be_date(fields.get("date"), fields.get("date_raw"))),
        # 票种按全平台共用码表写（ADR-0012，与 ERP 卡片同一份名称）。
        _kv(_("Document type"), _(tax_codes.LABELS[kind]) if kind in tax_codes.LABELS else "-"),
        # 进项只列卖方（供应商），销项只列买方（客户）：本方不在卡上（ERP 同一口径）。
        *(_party_rows("Seller", fields, "seller") if direction != "sales" else _party_rows("Buyer", fields, "buyer")),
        _separator(),
        _items(items, shown),
        _separator(),
        _kv(_("Subtotal"), _money(fields.get("subtotal"))),
        _kv(_("Discount"), _money(fields.get("discount")) if fields.get("discount") not in (None, "", "0", 0) else "-"),
        _kv(_("VAT"), _money(fields.get("vat"))),
        _kv(_("WHT amount"), _money(fields.get("wht_amount")) if fields.get("wht_amount") not in (None, "") else "-"),
        _kv(_("Document total"), _money(fields.get("total_amount") or record.total_amount), accent=accent),
        _kv(_("Currency"), fields.get("currency") or "THB"),
        _kv(_("Payment method"), _(PAYMENTS[payment]) if payment in PAYMENTS else "-"),
    ]
    title = f"{_('Review document')} · {head}"
    uri = frappe.utils.get_url().rstrip("/") + "/desk/pearnly-intake/new/" + record.name
    grand = fields.get("total_amount") or record.total_amount
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
                "footer": {"type": "box", "layout": "vertical", "paddingAll": "14px", "contents": [
                    {"type": "button", "style": "primary", "height": "sm", "color": accent,
                     "action": {"type": "uri", "label": str(_("Edit"))[:20], "uri": uri}}]}}}


def preview(record, pending, labels=None) -> dict:
    items = _primary(json.loads(record.pages or "[]")).get("items") or []
    shown = len(items)
    labels = {} if labels is None else labels
    while True:
        card = _card(record, pending, shown, labels)
        if shown == 0 or len(json.dumps(card, ensure_ascii=False).encode()) <= MAX_BYTES:
            return card
        shown -= 1


def previews(records, pending) -> list:
    rows = {row.name: row for row in frappe.get_all(
        "Cowork OCR History", filters={"name": ["in", records]},
        fields=["name", "pages", "total_amount", "intake_target", "push_connection", "push_account_set"])}
    labels = {}  # 同一批的账套名只按连接读一次目录
    return [preview(rows[name], pending, labels) for name in records if name in rows]
