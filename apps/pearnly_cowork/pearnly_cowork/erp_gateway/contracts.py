"""Pearnly ERP 前台 → 事务所 Express 账本的网关契约（2026-10-22，纯函数：不连库、不依赖 Frappe、不做 IO）。

ERP 只当前台：外地的人在 ERP 开采购 / 销售单，客户、供应商、商品、仓库都从 Express 回读的主档里选（带 Express 编码）。
单据交到这里，转成一张「方向、现 / 赊、货 / 费、对方档、逐行存货码与仓库」都已写明的票，走现有推送（准备 → 确认 →
办公室电脑写 Express → 回读核对），推送规则一行不改。票面写明的值（付款状态、对方编码、行编码）只管这一张，不写进
账套记忆。

单据（``document``，ERP 侧 `pearnly_erp_portal.express.contracts.document` 出的同一份）::

    {"version": 1, "ref": "PUR-2026-00012", "direction": "purchase" | "sales", "date": "YYYY-MM-DD",
     "number": "票面 / 本方单号", "company": {"tax_id": 13 位, "branch": "00000"},
     "party": {"code": Express 对方档码或空, "name", "tax_id", "branch"},
     "paid": true | false, "has_vat": true | false, "currency": "THB"（ERP 单据的币种，照写进票面；非泰铢由推送照旧拦下）,
     "subtotal": 税前（扣折扣后）, "vat": 税额, "total": 含税总额（字符串化的十进制数）,
     "items": [{"name", "code": 存货码或空, "unit", "qty", "price", "amount": 行净额, "loccod": 仓库码或空,
                "stock": true | false}]}
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import re

VERSION = 1
DIRECTIONS = ("purchase", "sales")
ITEMS_MAX = 200
REF_MAX = 140
_TAX = re.compile(r"^\d{13}$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TOL = Decimal("0.05")

# 网关回给 ERP 的单据状态（ERP 只按这几个值显示，不自己判推送成败）
QUEUED = "queued"          # 已交给办公室电脑，等写入（电脑关着也在这里等）
WRITTEN = "written"        # 已写进 Express（单号在 ``docnum``）
MISMATCH = "mismatch"      # 写进去了，但回读核对与交的不一样
HELD = "held"              # 停在事务所等人处理（要选的项、找不到账套、没有电脑能写）
FAILED = "failed"          # 交不进来（单据本身不合规）
STATES = (QUEUED, WRITTEN, MISMATCH, HELD, FAILED)


class GatewayError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value, limit: int, *, required: bool = False, code: str = "erp_gateway.document_invalid") -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise GatewayError(code)
    value = value.strip()
    if required and not value:
        raise GatewayError(code)
    return value[:limit]


def _money(value, *, positive: bool = False, code: str = "erp_gateway.amount_invalid") -> Decimal:
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise GatewayError(code) from None
    if not amount.is_finite() or amount < 0 or (positive and amount <= 0):
        raise GatewayError(code)
    return amount.quantize(Decimal("0.0001")).normalize()


def _s(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _date(value) -> str:
    match = _ISO.match(str(value or ""))
    if not match:
        raise GatewayError("erp_gateway.date_invalid")
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        raise GatewayError("erp_gateway.date_invalid") from None


def _branch(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return (digits[-5:] if digits else "0").zfill(5)


def _party(value) -> dict:
    if not isinstance(value, dict):
        raise GatewayError("erp_gateway.party_invalid")
    tax = re.sub(r"\D", "", str(value.get("tax_id") or ""))
    party = {"code": _text(value.get("code"), 20, code="erp_gateway.party_invalid"),
             "name": _text(value.get("name"), 200, code="erp_gateway.party_invalid"),
             "tax_id": tax if _TAX.match(tax) else "", "branch": _branch(value.get("branch"))}
    if not party["code"] and not party["name"]:
        raise GatewayError("erp_gateway.party_invalid")
    return party


def _item(value) -> dict:
    if not isinstance(value, dict):
        raise GatewayError("erp_gateway.item_invalid")
    item = {"name": _text(value.get("name"), 200, required=True, code="erp_gateway.item_invalid"),
            "code": _text(value.get("code"), 20, code="erp_gateway.item_invalid"),
            "unit": _text(value.get("unit"), 20, code="erp_gateway.item_invalid"),
            "qty": _money(value.get("qty"), positive=True, code="erp_gateway.item_invalid"),
            "price": _money(value.get("price"), code="erp_gateway.item_invalid"),
            "amount": _money(value.get("amount"), code="erp_gateway.item_invalid"),
            "loccod": _text(value.get("loccod"), 4, code="erp_gateway.item_invalid"),
            "stock": value.get("stock") is True}
    if item["stock"] and not item["code"] and item["loccod"]:
        # 带仓库的库存行必须是 Express 已有的存货（新品由小助手按名建档时写主仓，不能指定仓库）
        raise GatewayError("erp_gateway.item_invalid")
    return item


def _currency(value) -> str:
    code = str(value or "THB").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise GatewayError("erp_gateway.currency_invalid")
    return code


def document(body) -> dict:
    """校验并规整 ERP 交来的一张单；不合规抛 ``GatewayError``（码给 ERP 显示，不含单据内容）。"""
    if not isinstance(body, dict) or body.get("version") != VERSION:
        raise GatewayError("erp_gateway.version_unsupported")
    direction = body.get("direction")
    if direction not in DIRECTIONS:
        raise GatewayError("erp_gateway.document_invalid")
    company = body.get("company")
    if not isinstance(company, dict):
        raise GatewayError("erp_gateway.company_invalid")
    tax = re.sub(r"\D", "", str(company.get("tax_id") or ""))
    if not _TAX.match(tax):
        raise GatewayError("erp_gateway.company_tax_missing")
    items = body.get("items")
    if not isinstance(items, list) or not items or len(items) > ITEMS_MAX:
        raise GatewayError("erp_gateway.items_invalid")
    doc = {"version": VERSION, "ref": _text(body.get("ref"), REF_MAX, required=True),
           "direction": direction, "date": _date(body.get("date")),
           "number": _text(body.get("number"), 40),
           "company": {"tax_id": tax, "branch": _branch(company.get("branch"))},
           "party": _party(body.get("party")), "paid": body.get("paid") is True, "has_vat": body.get("has_vat") is True,
           "currency": _currency(body.get("currency")),
           "subtotal": _money(body.get("subtotal"), positive=True), "vat": _money(body.get("vat")),
           "total": _money(body.get("total"), positive=True), "items": [_item(item) for item in items]}
    if abs(doc["subtotal"] + doc["vat"] - doc["total"]) > _TOL:
        raise GatewayError("erp_gateway.amount_invalid")
    if not doc["has_vat"] and doc["vat"] != 0:
        raise GatewayError("erp_gateway.amount_invalid")
    lines = sum((item["amount"] for item in doc["items"]), Decimal(0))
    if abs(lines - doc["subtotal"]) > _TOL:
        raise GatewayError("erp_gateway.items_mismatch")
    return doc


def pages(doc: dict) -> list:
    """规整后的单 → 识别记录的页面（推送读主页面的 ``fields``）。本方是买方（进项）或卖方（销项）。"""
    party, own = doc["party"], doc["company"]
    them, us = ("seller", "buyer") if doc["direction"] == "purchase" else ("buyer", "seller")
    fields = {
        "direction": doc["direction"],
        "date": doc["date"],
        "invoice_number": doc["number"] or doc["ref"].split("@")[0][:40],
        "document_type": "tax_invoice" if doc["has_vat"] else "receipt",
        # 币种照 ERP 单据写明（不让推送按品名 / 单号里的外币记号去猜：商品叫「USD」的泰铢单不是外币票）
        "currency": doc["currency"],
        "subtotal": _s(doc["subtotal"]), "vat": _s(doc["vat"]), "total_amount": _s(doc["total"]),
        # 票面写明的现 / 赊（推送按「票面写明」判 HP/RR、HS/IV，不写进这家的付款记忆）
        "payment_status": "paid" if doc["paid"] else "unpaid",
        # 进项按可抵进项的货票车道（有明细、按行声明库存 / 服务）；费用车道只给识别出来的小票
        "posting_item_type_manual": "goods",
        f"{them}_name": party["name"], f"{them}_tax": party["tax_id"], f"{them}_branch": party["branch"],
        f"{us}_tax": own["tax_id"], f"{us}_branch": own["branch"],
        "party_code": party["code"],
        "items": [{"name": item["name"], "qty": _plain(item["qty"]), "price": _plain(item["price"]),
                   "subtotal": _s(item["amount"]), "unit": item["unit"], "code": item["code"],
                   "loccod": item["loccod"], "posting_kind": "stock" if item["stock"] else "service"}
                  for item in doc["items"]],
    }
    return [{"page_index": 1, "fields": fields}]


def _plain(value: Decimal) -> str:
    return format(value, "f")


def request_key(client: str, doc: dict) -> str:
    """建票的请求号：同一个 ERP 站点同一张单同样内容是同一件（重交不重建），内容变了另算一件（ERP 侧另起版本）。"""
    seed = "\0".join([str(client), doc["ref"], doc["direction"]])
    return "erp:" + hashlib.sha256(seed.encode()).hexdigest()[:60]


def state_of(push_status: str, reason: str, record_status: str, docnum: str, verify: str) -> str:
    """识别记录的推送投影 + 推送记录 → 给 ERP 的状态。HTTP 成功、准备好都不算写入；只有回执带单号才是 ``written``。"""
    if record_status == "success" and docnum:
        return MISMATCH if verify == "mismatch" else WRITTEN
    if push_status == "pending" or record_status in ("prepared", "pending", "leased"):
        return QUEUED
    if push_status == "manual" or record_status == "manual" or reason:
        return HELD
    return QUEUED


__all__ = ["DIRECTIONS", "FAILED", "GatewayError", "HELD", "ITEMS_MAX", "MISMATCH", "QUEUED", "STATES", "VERSION",
           "WRITTEN", "document", "pages", "request_key", "state_of"]
