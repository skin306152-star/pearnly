"""LINE 收款的两种 Flex：发票多选列表、确认卡（`line_collection` 用）。

版式照 ERP 预览卡（`line_cards`：左灰标签、右粗体值、合计用主题色），颜色是收款自己的那一种（`line_cards.COLORS`，按业务类型一处定义）。
发票列表每张气泡 :data:`line_collection.PER_BUBBLE` 张，点一行的按钮切换选中、回一张新的列表；底部是已选张数与合计、【下一步】。
确认卡复述公司·账套·客户·各张本次收·方式·折扣·合计，带【确认】【撤回】（postback，带这张卡的一次性确认号）【编辑】（网页原生弹窗）；
草稿规则此刻不过（缺凭证、别人刚收了同一张）时不出【确认】。
"""
from urllib.parse import urlencode

from frappe import _

from pearnly_erp_portal.integrations.line_cards import COLORS, _be_date, _kv, _separator

ACCENT = COLORS["collection"]


def _money(value) -> str:
    from pearnly_erp_portal.integrations.line_collection import money

    return money(value)


def _data(step, value="", **extra) -> str:
    from pearnly_erp_portal.integrations.line_collection import ACTION

    return urlencode({"a": ACTION, "s": step, **({"v": value} if value != "" else {}), **extra})


def _postback(label, data, style="secondary"):
    label = str(label)[:20]
    return {"type": "button", "style": style, "height": "sm", "color": ACCENT if style == "primary" else None,
            "action": {"type": "postback", "label": label, "data": data, "displayText": label}}


def _clean(button):
    return {key: value for key, value in button.items() if value is not None}


def _header(title, subtitle=""):
    contents = [{"type": "text", "text": title, "weight": "bold", "size": "md", "color": "#FFFFFF", "wrap": True}]
    if subtitle:
        contents.append({"type": "text", "text": subtitle, "size": "xs", "color": "#FFFFFFCC", "wrap": True})
    return {"type": "box", "layout": "vertical", "backgroundColor": ACCENT, "paddingAll": "md", "contents": contents}


def _invoice_row(docnum, payload):
    picked = docnum in (payload.get("picked") or [])
    button = _clean(_postback(_("Selected") if picked else _("Select"), _data("inv", docnum),
                              "primary" if picked else "secondary"))
    button["flex"] = 2
    return {"type": "box", "layout": "horizontal", "margin": "md", "spacing": "sm", "alignItems": "center", "contents": [
        {"type": "box", "layout": "vertical", "flex": 4, "contents": [
            {"type": "text", "text": docnum, "size": "sm", "weight": "bold", "color": "#1B1B2B", "wrap": True},
            {"type": "text", "text": _be_date(payload["dates"].get(docnum)) + " · " + _("Due {0}").format(
                _money(payload["due"][docnum])), "size": "xxs", "color": "#777777", "wrap": True}]},
        button]}


def invoices(payload) -> dict:
    """未收清发票的多选列表（日期早的在前）。"""
    from pearnly_erp_portal.integrations.line_collection import PER_BUBBLE, _picked_total

    order = payload["order"]
    footer = {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
        {"type": "text", "text": _("{0} selected · total {1}").format(len(payload.get("picked") or []),
                                                                       _money(_picked_total(payload))),
         "size": "sm", "weight": "bold", "color": ACCENT, "wrap": True},
        _clean(_postback(_("Next step"), _data("invoices", "done"), "primary"))]}
    bubbles = []
    for start in range(0, len(order), PER_BUBBLE):
        rows = [_invoice_row(docnum, payload) for docnum in order[start:start + PER_BUBBLE]]
        bubbles.append({"type": "bubble", "size": "mega",
                        "header": _header(_("Choose invoices"), payload.get("customer_name") or payload["customer"]),
                        "body": {"type": "box", "layout": "vertical", "contents": rows},
                        "footer": footer})
    contents = bubbles[0] if len(bubbles) == 1 else {"type": "carousel", "contents": bubbles}
    return {"type": "flex", "altText": _("Choose invoices"), "contents": contents}


def confirm(card, edit_url="", nonce="") -> dict:
    """确认卡（`collection.service.card` 的内容）。"""
    rows = [_kv(_("Company"), card.get("company_name")), _kv(_("Account Set"), card.get("book")),
            _kv(_("Customer"), f"{card.get('customer') or ''} {card.get('customer_name') or ''}".strip()),
            _separator()]
    for row in card.get("invoices") or []:
        rows.append(_kv(row["docnum"], _money(row["amount"])))
    rows.append(_separator())
    if float(card.get("cash") or 0):
        rows.append(_kv(_("Cash"), _money(card["cash"])))
    for row in card.get("methods") or []:
        rows.append(_kv(row.get("label") or row.get("name") or row["prefix"], _money(row["amount"])))
    rows.append(_kv(_("Total"), _money(card.get("total")), accent=ACCENT))
    buttons = []
    if card.get("ready"):
        buttons.append(_clean(_postback(_("Confirm"), _data("ok", n=card["name"], ver=card["version"], k=nonce), "primary")))
    if edit_url and card.get("status") == "draft":
        buttons.append({"type": "button", "style": "secondary", "height": "sm",
                        "action": {"type": "uri", "label": str(_("Edit"))[:20], "uri": edit_url}})
    buttons.append(_clean(_postback(_("Withdraw"), _data("wd", n=card["name"], k=nonce), "link")))
    return {"type": "flex", "altText": _("Confirm collection"), "contents": {
        "type": "bubble", "size": "mega", "header": _header(_("Confirm collection"), card.get("name") or ""),
        "body": {"type": "box", "layout": "vertical", "contents": rows},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons}}}
