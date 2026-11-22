"""LINE 预览卡。

版式与字段顺序照旧线上的审核卡（`pearnly-app@ec2e5c95`：`services/line_erp/cards.py` →
`cowork_line/review_cards.py` → `line_platform/summary_review_card.py`，用户 2026-09-26 给的截图）：
标题「ตรวจสอบเอกสาร · ซื้อ/ขาย」+ 副标题，采购绿、销售红；左灰标签、右粗体值；
公司 / 业务类型 / 单据数，分隔线后是票面的 19 个字段（单号 … 备注），合计用主题色。
〔定·09-26〕旧卡的「方向」改叫「业务类型」；日期仍按佛历年份显示，标签不加「佛历」。

与旧卡不同的地方（用户口径）：
- 〔定·09-25〕「编辑」打开网页端原生的采购 / 销售页，提交才入账；卡片不过期。
- 〔11-18〕「编辑」上方加「保存」：不进编辑页直接提交（同编辑页的提交）；要人定的项不提交，回原因与「编辑」。
- 〔11-19〕三个实底按钮：保存（业务色）、编辑（灰）、取消（灰）；取消 = 这一张什么都不做，记录作废。
- 〔定·09-26〕「字段细一点、商品名称全部显示」：在 LINE 里选的（供应商或客户、付款）照实显示在对应行里；明细不再只报条数，
  每一行商品名、数量 × 单价 = 金额都列出来。
- 〔11-06 按行选物料与库存〕每行标「入库 / 出库 / 只记账 / 新建 · 入库」（以前整张一行「物料类型：库存商品 · 仓库」），有入库行时另列仓库。
"""
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import frappe
from frappe import _

from pearnly_erp_portal.integrations import line_flow
from pearnly_core.tax_documents import codes as tax_codes

# LINE 卡片按业务类型取色（一处定义）：采购绿、销售红、收款用品牌紫（`pearnly_core/public/desk/product.css` 的 --pearnly-accent-deep）
COLORS = {"purchase": "#16873E", "sales": "#B11B50", "collection": "#6B3FF2"}
DOC_KINDS = {"purchase_invoice": "Purchase Invoice",
             "expense": "Expense", "receipt": "Receipt", "quotation": "Quotation"}
# 一张气泡的 JSON 有大小上限（LINE 30 KB）：明细按实际大小列，放不下的报「还有 N 项」。
# 实测一行泰文商品约 570 字节，40 行就到 32.5 KB（09-26）；留一点余量。
MAX_BYTES = 28 * 1024
SAVE_ACTION = "erp_save"  # 卡片「保存」的 postback（字面量：产品地图按它认入口）
CANCEL_ACTION = "erp_cancel"  # 卡片「取消」
RESULT_ACTION = "erp_result"  # 「录入中」后的【查看结果】（用户 11-22，a=erp_result&n=…）
RECORDED = {"purchase": ("Purchase Document", "purchase"), "sales": ("ERP Sales Document", "sales_document")}


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
    """日期按佛历年份显示（旧卡片口径），读不懂的原样给人看。"""
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
    buyer = chosen or {"name": result.get("buyer_name") or _("Unknown buyer"), "tax_id": result.get("buyer_tax_id"),
                       "branch": result.get("buyer_branch_no"),
                       "address": result.get("buyer_address") or result.get("address")}
    return ours, buyer


def _party_rows(role, party):
    """对方那一组：名称、税号、分店、地址（标签沿用旧卡片的「ชื่อผู้ขาย / ชื่อผู้ซื้อ」等）。每个标签是一条完整的词条，不拼接。"""
    if role == "Seller":
        labels = (_("Seller"), _("Seller tax ID"), _("Seller branch"), _("Seller address"))
    else:
        labels = (_("Buyer"), _("Buyer tax ID"), _("Buyer branch"), _("Buyer address"))
    values = (party.get("name"), party.get("tax_id"), party.get("branch"), party.get("address"))
    return [_kv(label, value) for label, value in zip(labels, values)]


def _payment(doc):
    if not doc.is_paid:
        return _("Unpaid")
    return _(line_flow.METHOD_LABELS.get(doc.payment_method, "Other")) if doc.payment_method else _("Paid")


def _warehouse(chosen):
    """单头仓（有入库 / 出库行才有）。"""
    name = (chosen or {}).get("warehouse")
    if not name or not any(row and row.get("item_type") == "goods" for row in chosen["lines"]):
        return None
    return frappe.db.get_value("ERP Warehouse", name, "warehouse_name") or name


# 每一行的标签（11-06 按行选物料与库存）：`line_rules.tag` 的键 → 词条
TAGS = {"in": "trade.groupIn", "out": "trade.groupOut", "none": "erp.line.tag.none",
        "new_in": "erp.line.tag.newIn", "new_out": "erp.line.tag.newOut"}


def _tag(doc, chosen, index):
    from pearnly_erp_portal.products import line_rules

    rows = (chosen or {}).get("lines") or []
    row = rows[index] if index < len(rows) else None
    return _(TAGS[line_rules.tag(row, doc.direction)]) if row else ""


def _items(doc, result, totals, shown):
    from pearnly_erp_portal.integrations import line_items

    chosen = line_items.choices_of(doc)
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
        tag = _tag(doc, chosen, index)
        if tag:
            detail += f" · {tag}"
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


def preview(doc, result, totals, save=True) -> dict:
    """一张 Flex 气泡：旧卡片的字段顺序 + LINE 里的选择 + 全部明细，底部「保存」「编辑」「取消」。``save`` 为假时不放「保存」
    （写不进 Express、又不是要人选的，例如账套里没有合格的存货科目组，11-19）：只留「编辑」「取消」。"""
    shown = len(result.get("lines") or [])
    while True:
        card = _card(doc, result, totals, shown, save)
        if shown == 0 or len(json.dumps(card, ensure_ascii=False).encode()) <= MAX_BYTES:
            return card
        shown -= 1


def _card(doc, result, totals, shown, save=True) -> dict:
    from pearnly_erp_portal.integrations import line_items

    accent, head = COLORS[doc.direction], _(line_flow.DIRECTION_LABELS[doc.direction])
    warehouse = _warehouse(line_items.choices_of(doc))
    seller, buyer = _parties(doc, result)
    before, discount, vat, wht, grand = _amounts(doc, totals)
    kind = result.get("doc_kind")
    body = [
        _kv(_("Company"), _company(doc)["name"]),
        _kv(_("Transaction type"), head),
        _kv(_("Documents"), "1"),
        *([_kv(_("Warehouse"), warehouse)] if warehouse else []),
        _separator(),
        _kv(_("Document no."), result.get("doc_no")),
        _kv(_("Date"), _be_date(result["doc_date"]) if result.get("doc_date") else "-"),
        # 票种按全平台共用码表写（ADR-0012：完整税票 / 简式税票 / 收据 …，决定抵不抵进项）；
        # 这项之前识别的卡片没有票种，退回单据类别。
        _kv(_("Document type"), _(tax_codes.LABELS[result["document_type"]])
            if result.get("document_type") in tax_codes.LABELS else _(DOC_KINDS[kind]) if kind in DOC_KINDS else "-"),
        # 采购只列卖方（供应商，票面识别或 LINE 里选的），销售只列买方（客户）；我方公司已在上面
        # 「公司」一行（用户 2026-09-26）。
        *_party_rows("Seller" if doc.direction == "purchase" else "Buyer",
                     seller if doc.direction == "purchase" else buyer),
        _kv(_("Notes"), result.get("notes")),
        _separator(),
        _items(doc, result, totals, shown),
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
    # 「保存」在「编辑」上方（用户 11-18）：保存 = 直接提交（同编辑页的提交，`intake.save_card`）
    # 三个按钮（用户 11-19）：都是实底；保存 = 业务色，编辑、取消 = 灰
    buttons = [{"type": "button", "style": "primary", "height": "sm", "color": accent,
                "action": {"type": "postback", "label": _("Save")[:20], "displayText": _("Save")[:20],
                           "data": urlencode({"a": "erp_save", "n": doc.name})}},  # = SAVE_ACTION（字面量：产品地图按它认入口）
               {"type": "button", "style": "secondary", "height": "sm", "margin": "sm",
                "action": {"type": "uri", "label": _("Edit")[:20], "uri": editor_link(doc)}},
               {"type": "button", "style": "secondary", "height": "sm", "margin": "sm",
                "action": {"type": "postback", "label": _("Cancel")[:20], "displayText": _("Cancel")[:20],
                           "data": urlencode({"a": "erp_cancel", "n": doc.name})}}]  # = CANCEL_ACTION
    if not save:
        buttons = buttons[1:]
        buttons[0] = {key: value for key, value in buttons[0].items() if key != "margin"}
    alt = f"{title} · {_money(grand)}" if grand not in (None, "") else title
    return {"type": "flex", "altText": alt[:400],
            "contents": {"type": "bubble", "size": "mega",
                "header": {"type": "box", "layout": "vertical", "backgroundColor": accent, "paddingAll": "16px",
                           "contents": [
                               {"type": "text", "text": title, "size": "lg", "weight": "bold",
                                "color": "#FFFFFF", "wrap": True},
                               {"type": "text", "text": _("Document summary"),
                                "size": "xxs", "color": "#F4F4F4", "margin": "xs", "wrap": True}]},
                "body": {"type": "box", "layout": "vertical", "paddingAll": "16px", "contents": body},
                "footer": {"type": "box", "layout": "vertical", "paddingAll": "14px", "contents": buttons}}}


def editor_link(doc) -> str:
    """网页端原生页里「从这张 LINE 单据打开」的地址。经 LINE 入口：LINE 里未登录时用绑定的 LINE 免密登录。"""
    from pearnly_core.account import line_login

    root = "pearnly-purchase" if doc.direction == "purchase" else "pearnly-sales"
    return line_login.entry_url(f"/desk/{root}/line/{doc.name}")


# 卡片「保存」停下时：要人定的项按单据页 Express 弹窗同一套名称（`transactions.js` 的 EXPRESS_KINDS）
NEEDED_KINDS = {"party": {"sales": "Customer", "purchase": "Supplier"}, "item": "Item", "account": "express.kind.account",
                "department": "Department", "payment": "Payment Method", "vat": "express.kind.vat",
                "account_set": "Account Set", "stock_group": "express.kind.stockGroup", "receipt": "express.kind.receipt",
                "vat_registered": "express.kind.vatRegistered", "refund": "express.kind.refundAccount",
                "exempt_mode": "express.kind.exemptMode", "service_vat": "express.kind.serviceVat",
                "location": "Warehouse"}  # 收单记录自己的仓库那一项（`express.operator.intake_items`）


def needed_text(doc, needed) -> str:
    rows = []
    for item in needed:
        kind = NEEDED_KINDS.get(item.get("kind"))
        label = _(kind.get(doc.direction) if isinstance(kind, dict) else kind or item.get("kind") or "")
        rows.append(" · ".join(dict.fromkeys(part for part in (label, item.get("label") or "") if part)))
    return "\n".join(dict.fromkeys(rows))


def stopped(doc, text) -> dict:
    """「保存」没有提交：一句原因（提交时原样的报错 / 要人定的项）+ 快捷回复「编辑」（打开同一页）。"""
    return {"type": "text", "text": (text or "-")[:2000],
            "quickReply": {"items": [{"type": "action", "action": {"type": "uri", "label": _("Edit")[:20],
                                                                    "uri": editor_link(doc)}}]}}


def saved(doc, created) -> dict:
    """「成功 · 单号」（没接 Express、不写账本的公司提交即成功）：采购用系统号（PE…，同提交时回的那个；重复点时 ``created`` 只有
    内部名，按名取系统号）。"""
    number = created.get("system_no") or ""
    if not number and doc.direction == "purchase" and created.get("name"):
        number = frappe.db.get_value("Purchase Document", created["name"], "system_no") or ""
    number = number or created.get("name") or ""
    return {"type": "text", "text": " · ".join(part for part in (_("Succeeded"), number) if part)}


# ── 保存之后的结果（用户 11-22：先三个点，写账回执到了回结果；等不到回「录入中」+【查看结果】）──────────────────


def recorded(doc, name="") -> tuple:
    """这张 LINE 单据提交出来的单：``(单据类型, 名称)``；还没提交回 ``(单据类型, "")``。``name``：提交刚回的单据名（内存里的这张
    LINE 单据可能还没带上链接栏）——等结果的键、回执叫醒的键、读状态都用同一个名字（`express.pending.line_key`）。"""
    doctype, link = RECORDED[doc.direction]
    return doctype, doc.get(link) or name or ""


def entering(doc) -> dict:
    """「录入中」+【查看结果】（等写账回执的兜底，同网页列表的状态词）。"""
    label = _("View result")[:20]
    data = urlencode({"a": "erp_result", "n": doc.name})  # = RESULT_ACTION（字面量：产品地图按它认入口）
    return {"type": "text", "text": _("Entering"), "quickReply": {"items": [
        {"type": "action", "action": {"type": "postback", "label": label, "data": data, "displayText": label}}]}}


def result(doc, created=None, *, final=False, name=""):
    """这张此刻的结果（状态词与网页同一张表，`express.contracts.ledger_word`）：没接 Express 的「成功 · 单号」；写进去了
    「成功 · Express 单号」；没写成「失败 · 原因」+【编辑】。还在录入回 None（``final`` 时回「录入中」+【查看结果】）。"""
    from pearnly_erp_portal.express import contracts as express_contracts, pending

    doctype, name = recorded(doc, name or (created or {}).get("name") or "")
    row = frappe.db.get_value(doctype, name, ["name", "system_no", "ledger_state", "ledger_reason", "ledger_docnum"],
                              as_dict=True) if name else None
    if not row:
        return saved(doc, created) if created else entering(doc) if final else None
    state, reason = row.get("ledger_state") or "", row.get("ledger_reason") or ""
    if not state:
        return saved(doc, {"name": row.name, "system_no": row.system_no, **(created or {})})
    if not express_contracts.ledger_settled(state, reason):
        return entering(doc) if final else None
    word = express_contracts.ledger_word(state)
    if word == express_contracts.SUCCEEDED:
        number = row.get("ledger_docnum") or row.get("system_no") or row.name
        return {"type": "text", "text": f"{_(word)} · {number}"}
    why = _(express_contracts.MISMATCH_REASON) if state == express_contracts.MISMATCH else pending.reason_label(state, reason)
    return stopped(doc, " · ".join(part for part in (_(word), why) if part))


def resume(intake, name=""):
    """（`line_receipt` 的短任务）写账回执到了：回这张的结果；还在录入回 None 接着等。``name``：等的那张单（同等结果的键）。"""
    doc = frappe.get_doc("ERP LINE Intake", intake)
    message = result(doc, name=name)
    return [message] if message else None
