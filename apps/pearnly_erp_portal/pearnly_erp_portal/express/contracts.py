"""ERP 当前台、事务所 Express 当账本（2026-10-22）的纯规则：单据怎么交给网关、Express 主档怎么落进 ERP 主档。

不连库、不依赖 Frappe、不做 IO；金额一律 Decimal。网关的契约见 Cowork `erp_gateway.contracts`（同一份单据格式）。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import re

VERSION = 1
CENT = Decimal("0.01")

# ERP 单据上的 Express 状态（网关回的原值，ERP 不自己判推送成败）
QUEUED, WRITTEN, MISMATCH, HELD, FAILED = "queued", "written", "mismatch", "held", "failed"
STATES = (QUEUED, WRITTEN, MISMATCH, HELD, FAILED)
# 还要再问网关的状态（写进去了 / 交不进来的不再问）
OPEN_STATES = (QUEUED, HELD)
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
             has_vat: bool, total, vat, lines: list) -> dict:
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


__all__ = ["FAILED", "HELD", "MISMATCH", "OPEN_STATES", "PURCHASE_KINDS", "QUEUED", "SALES_KINDS", "STATES", "VERSION",
           "WRITTEN", "branch5", "d", "document", "money", "party", "product", "spread", "state", "stock_targets",
           "rows", "tax13", "unit_names"]
