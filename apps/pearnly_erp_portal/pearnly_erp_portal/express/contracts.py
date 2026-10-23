"""ERP 当前台、Express 当账本的纯规则（2026-10-22；10-23 起 CC 桌面版直连 ERP，ADR-025）：单据怎么变成 Express 写账规则读的
票面（`pages`）、Express 主档怎么落进 ERP 主档。不连库、不依赖 Frappe、不做 IO；金额一律 Decimal。

单据（:func:`document` 出、:func:`checked` 核）::

    {"version": 1, "ref": "PUR-2026-00012@<建单时刻>", "direction": "purchase" | "sales", "date": "YYYY-MM-DD",
     "number": "票面 / 本方单号", "company": {"tax_id": 13 位, "branch": "00000"},
     "party": {"code": Express 对方档码或空, "name", "tax_id", "branch"},
     "paid": true | false, "has_vat": true | false, "currency": "THB",
     "subtotal": 税前（扣折扣后）, "vat": 税额, "total": 含税总额（字符串化的十进制数）,
     "items": [{"name", "code": 存货码或空, "unit", "qty", "price", "amount": 行净额, "loccod": 仓库码或空,
                "stock": true | false}]}
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import re

VERSION = 1
CENT = Decimal("0.01")

# ERP 单据上的 Express 状态（只按电脑的回执定：HTTP 成功、交进队列都不算写入）
QUEUED, WRITTEN, MISMATCH, HELD, FAILED = "queued", "written", "mismatch", "held", "failed"
STATES = (QUEUED, WRITTEN, MISMATCH, HELD, FAILED)
# 还没写进 Express 的状态（库存对齐把这些单的变动加回去；每分钟再看一次停下的）
OPEN_STATES = (QUEUED, HELD)
# 停下的原因码（`express_reason`，页面按码出文案）
NO_TAX = "express.company_tax_missing"
NO_BOOK = "express.account_set_missing"
SEVERAL_BOOKS = "express.account_set_several"
NO_WRITER = "express.no_writer"
READING = "express.masters_reading"
NOT_CONNECTED = "express.cc_desktop_not_connected"
OFFLINE = "express.cc_desktop_offline"
CHOICE = "choice_needed"
# 过一会儿自己会好的（电脑开机、主档读完、有人在电脑上登录 CC）：每分钟再试；要人选的、单据本身不对的不自动重试
RETRY_REASONS = (NO_BOOK, NO_WRITER, READING)
# 这几种采购单是真进货 / 费用，推进 Express；采购订单只是订单，不推
PURCHASE_KINDS = ("purchase_invoice", "expense", "goods", "service")
SALES_KINDS = ("receipt",)


def d(value) -> Decimal:
    try:
        amount = Decimal(str(value if value not in (None, "") else 0).replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return amount if amount.is_finite() else Decimal(0)


def money(value: Decimal) -> str:
    return format(d(value).quantize(CENT, rounding=ROUND_HALF_UP), "f")


def branch5(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return (digits[-5:] if digits else "0").zfill(5)


def tax13(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 13 else ""


def spread(amounts: list, base: Decimal) -> list:
    """逐行金额按比例摊成合计正好等于 ``base``（含税单价、整单折扣、四舍五入都在这里收平），余数落末行。"""
    total = sum(amounts, Decimal(0))
    if not amounts:
        return []
    if total <= 0:
        share = (base / len(amounts)).quantize(CENT, rounding=ROUND_HALF_UP)
        out = [share] * len(amounts)
    else:
        out = [(amount * base / total).quantize(CENT, rounding=ROUND_HALF_UP) for amount in amounts]
    out[-1] = (out[-1] + base.quantize(CENT, rounding=ROUND_HALF_UP) - sum(out, Decimal(0))).quantize(CENT)
    return out


def document(*, ref: str, direction: str, doc_date: str, number: str, company: dict, party: dict, paid: bool,
             has_vat: bool, total, vat, lines: list, currency: str = "THB") -> dict:
    """一张已提交的 ERP 单 → 交给网关的单据。

    ``lines``：``[{name, code, unit, qty, amount（行金额，ERP 原样）, loccod, stock}]``。税前额 = 含税总额 − 税额，
    逐行金额摊成合计等于税前额（ERP 的行金额可能含税、可能没扣整单折扣），单价 = 行金额 / 数量。"""
    total, vat = d(total), d(vat) if has_vat else Decimal(0)
    base = total - vat
    rows = [line for line in lines if d(line.get("qty")) > 0]
    amounts = spread([d(line.get("amount")) for line in rows], base)
    items = []
    for line, amount in zip(rows, amounts):
        qty = d(line["qty"])
        code = str(line.get("code") or "").strip()
        items.append({"name": str(line.get("name") or code or "-").strip()[:200], "code": code[:20],
                      "unit": str(line.get("unit") or "").strip()[:20], "qty": format(qty.normalize(), "f"),
                      "price": format((amount / qty).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f"),
                      "amount": money(amount),
                      # 仓库只给 Express 已有的存货（新品由小助手按名建档，写主仓）
                      "loccod": str(line.get("loccod") or "").strip()[:4] if code else "",
                      "stock": bool(line.get("stock"))})
    return {"version": VERSION, "ref": ref, "direction": direction, "date": str(doc_date)[:10],
            "number": str(number or "").strip()[:40],
            "company": {"tax_id": tax13(company.get("tax_id")), "branch": branch5(company.get("branch"))},
            "party": {"code": str(party.get("code") or "").strip()[:20], "name": str(party.get("name") or "").strip()[:200],
                      "tax_id": tax13(party.get("tax_id")), "branch": branch5(party.get("branch"))},
            "paid": bool(paid), "has_vat": bool(has_vat) and vat > 0,
            "currency": (str(currency or "THB").strip().upper() or "THB")[:3],
            "subtotal": money(base), "vat": money(vat), "total": money(total), "items": items}


# ── Express 主档 → ERP 主档 ──────────────────────────────────────────────────────


def rows(value) -> list:
    """有编码的那几行（Express 主档每类的行）。"""
    return [row for row in value or [] if isinstance(row, dict) and str(row.get("code") or "").strip()]


def unit_names(data: dict) -> dict:
    """单位码 → 名称（Express ISTAB 20）。"""
    return {str(row["code"]).strip(): str(row.get("name") or row["code"]).strip() for row in rows(data.get("units"))}


def party(row: dict) -> dict:
    """客户 / 供应商一行 → ERP 字段的共同部分。"""
    branch = branch5(row.get("branch"))
    return {"code": str(row["code"]).strip()[:20], "name": str(row.get("name") or row["code"]).strip()[:140],
            "tax_id": tax13(row.get("tax_id")), "branch": branch, "head": branch == "00000",
            "address": str(row.get("address") or "").strip()[:500], "phone": str(row.get("phone") or "").strip()[:60]}


def product(row: dict, units: dict) -> dict:
    """商品一行 → ERP 商品字段：库存单位是 Express 的基本单位，售卖单位与进货单位不同于它时进单位表（换算到基本单位）。"""
    base = units.get(str(row.get("unit") or "").strip()) or str(row.get("unit") or "").strip()
    extra = {}
    for unit_key, factor_key in (("sale_unit", "sale_factor"), ("purchase_unit", "purchase_factor")):
        name = units.get(str(row.get(unit_key) or "").strip()) or str(row.get(unit_key) or "").strip()
        factor = d(row.get(factor_key))
        if name and name != base and factor > 0:
            extra.setdefault(name, factor)
    sale = units.get(str(row.get("sale_unit") or "").strip()) or base
    prices = [d(price) for price in row.get("prices") or []]
    return {"code": str(row["code"]).strip()[:20], "name": str(row.get("name") or row["code"]).strip()[:140],
            "base_unit": base[:20], "unit": (sale or base)[:20], "units": [{"unit_name": name[:20], "factor": str(factor)}
                                                                           for name, factor in extra.items()],
            "price": money(prices[0]) if prices and prices[0] > 0 else "", "stock": row.get("kind") == "stock",
            "barcode": str(row.get("barcode") or "").strip()[:60]}


def stock_targets(stock_rows, pending) -> dict:
    """各 (存货码, 仓库码) 在 ERP 里应有的结存（基本单位）：Express 的结存 + 还没写进 Express 的 ERP 单的变动。

    ``pending``：``{(code, loccod): 带符号数量}``（进货为正、卖出为负），ERP 自己已经按这些单过了库存，Express 还没有。"""
    targets = {}
    for row in stock_rows or []:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("code") or "").strip(), str(row.get("loccod") or "").strip())
        if key[0] and key[1]:
            targets[key] = targets.get(key, Decimal(0)) + d(row.get("qty"))
    for key, qty in (pending or {}).items():
        targets[key] = targets.get(key, Decimal(0)) + d(qty)
    return targets


def state(value) -> str:
    return value if value in STATES else ""


# ── 单据 → 写账规则读的票面 ─────────────────────────────────────────────────────

ITEMS_MAX = 200
_TOL = Decimal("0.05")


class DocumentError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _amount(value, *, positive=False, code="express.amount_invalid") -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise DocumentError(code) from None
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise DocumentError(code)
    return number.quantize(Decimal("0.0001")).normalize()


def checked(doc: dict) -> dict:
    """:func:`document` 出的单再核一遍（金额对得上、行合计等于税前、日期合法），不合规抛 ``DocumentError``（码给页面显示）。"""
    if not tax13((doc.get("company") or {}).get("tax_id")):
        raise DocumentError(NO_TAX)
    try:
        date.fromisoformat(str(doc.get("date") or ""))
    except ValueError:
        raise DocumentError("express.date_invalid") from None
    items = doc.get("items") or []
    if not items or len(items) > ITEMS_MAX:
        raise DocumentError("express.items_invalid")
    party = doc.get("party") or {}
    if not party.get("code") and not party.get("name"):
        raise DocumentError("express.party_invalid")
    subtotal, vat = _amount(doc.get("subtotal"), positive=True), _amount(doc.get("vat"))
    total = _amount(doc.get("total"), positive=True)
    if abs(subtotal + vat - total) > _TOL or (not doc.get("has_vat") and vat != 0):
        raise DocumentError("express.amount_invalid")
    for item in items:
        _amount(item.get("qty"), positive=True, code="express.items_invalid")
        if item.get("stock") and not item.get("code") and item.get("loccod"):
            raise DocumentError("express.items_invalid")
    if abs(sum((_amount(item.get("amount"), code="express.items_invalid") for item in items), Decimal(0))
           - subtotal) > _TOL:
        raise DocumentError("express.items_mismatch")
    return doc


def pages(doc: dict) -> list:
    """核过的单 → 写账规则读的票面（主页面的 ``fields``）。本方是买方（进项）或卖方（销项）。票面写明的值（现 / 赊、对方编码、
    行编码与仓库、货 / 服务）只管这一张，由写账规则照写（`express_rules.push.choices` / `common` 的按码直落）。"""
    party, own = doc["party"], doc["company"]
    them, us = ("seller", "buyer") if doc["direction"] == "purchase" else ("buyer", "seller")
    fields = {
        "direction": doc["direction"],
        "date": doc["date"],
        "invoice_number": doc["number"] or doc["ref"].split("@")[0][:40],
        "document_type": "tax_invoice" if doc["has_vat"] else "receipt",
        # 币种照 ERP 单据写明（不让写账规则按品名 / 单号里的外币记号去猜）
        "currency": doc["currency"],
        "subtotal": doc["subtotal"], "vat": doc["vat"], "total_amount": doc["total"],
        "payment_status": "paid" if doc["paid"] else "unpaid",
        # 进项按可抵进项的货票车道（有明细、按行声明库存 / 服务）
        "posting_item_type_manual": "goods",
        f"{them}_name": party["name"], f"{them}_tax": party["tax_id"], f"{them}_branch": party["branch"],
        f"{us}_tax": own["tax_id"], f"{us}_branch": own["branch"],
        "party_code": party["code"],
        "items": [{"name": item["name"], "qty": item["qty"], "price": item["price"], "subtotal": item["amount"],
                   "unit": item["unit"], "code": item["code"], "loccod": item["loccod"],
                   "posting_kind": "stock" if item["stock"] else "service"}
                  for item in doc["items"]],
    }
    return [{"page_index": 1, "fields": fields}]


__all__ = ["CHOICE", "DocumentError", "FAILED", "HELD", "MISMATCH", "NOT_CONNECTED", "NO_BOOK", "NO_TAX", "NO_WRITER",
           "OFFLINE", "OPEN_STATES", "PURCHASE_KINDS", "QUEUED", "READING", "RETRY_REASONS", "SALES_KINDS",
           "SEVERAL_BOOKS", "STATES", "VERSION", "WRITTEN", "branch5", "checked", "d", "document", "money", "pages",
           "party", "product", "spread", "state", "stock_targets", "rows", "tax13", "unit_names"]
