# -*- coding: utf-8 -*-
"""载荷 21「引导录入」的新键：读不读、类型与取值（纯函数，不连账套、不依赖 dbf；tests/test_companion_contract.py 直接导入）。

约定见 docs/work/2026-11-12-cc-guided-entry-express-coverage.md 第 8 节；连账套的核对在 ``guided``（它把这里的名字原样再导出）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from .payload_contract import GUIDED_KEYS, GUIDED_PAYLOAD_VERSION

HEADER_KEYS = GUIDED_KEYS["header"]
LINE_KEYS = GUIDED_KEYS["line"]
PARTY_KEYS = GUIDED_KEYS["party"]

ERR_PAYLOAD_INVALID = "PAYLOAD_INVALID"
ERR_INVALID_DUE_DATE = "INVALID_DUE_DATE"
ERR_UNIT_CODE_NOT_FOUND = "UNIT_CODE_NOT_FOUND"
ERR_UNIT_CHOICE = "UNIT_CHOICE"
ERR_ITEM_GROUP_NOT_FOUND = "ITEM_GROUP_NOT_FOUND"
ERR_COST_BASIS_CHOICE = "COST_BASIS_CHOICE"
ERR_CASH_ACCOUNT_CHOICE = "CASH_ACCOUNT_CHOICE"
ERR_PARTY_TYPE_NOT_FOUND = "PARTY_TYPE_NOT_FOUND"
ERR_ACCOUNT_NOT_POSTABLE = "ACCOUNT_NOT_POSTABLE"

COST_GIVEN, COST_NONE = "given", "none"
NEGALLOW_VALUES = ("", "Y", "N")
PAYTERM_MAX = 999
CREDIT_DOCTYPES = ("RR", "IV")  # 赊购 / 赊销：可带到期日
CASH_DOCTYPES = ("HP", "HS")  # 现购 / 现销：可带现金科目
STOCK_IN, STOCK_OUT = "stock_item", "stock_sale"
PARTY_BLOCKS = ("supplier", "customer")


def active(payload) -> bool:
    """这份载荷读不读新键：只认版本 ≥ 21（服务端带了任一新键才盖 21）。"""
    try:
        return int((payload or {}).get("payload_version") or 1) >= GUIDED_PAYLOAD_VERSION
    except (TypeError, ValueError):
        return False


def guided(payload) -> bool:
    """不静默模式：人在 CC 对话里过了引导卡的单。"""
    return active(payload) and (payload or {}).get("guided") is True


def uses(payload) -> bool:
    """载荷里出现了任一新键（不看版本；代录的单带了它们要拒收）。"""
    payload = payload if isinstance(payload, dict) else {}
    if any(key in payload for key in HEADER_KEYS):
        return True
    if any(isinstance(item, dict) and any(key in item for key in LINE_KEYS) for item in payload.get("items") or []):
        return True
    return any(isinstance(payload.get(block), dict) and any(key in payload[block] for key in PARTY_KEYS)
               for block in PARTY_BLOCKS)


def items(payload) -> List[Any]:
    """交给各写路的明细行。版本 ≥ 21：给了 ``unit_code`` 的行把 ``unit`` 换成这个码（已核在单位表里；各写路照旧读 ``unit``，
    库存行、非库存行都按它写），其余原样；版本 ≤ 20：去掉行上的新键（旧任务不读新键）。没带新键的行是同一个对象。"""
    rows = list((payload or {}).get("items") or [])
    if active(payload):
        return [{**row, "unit": str(row["unit_code"]).strip()} if isinstance(row, dict) and row.get("unit_code") else row
                for row in rows]
    return [{k: v for k, v in row.items() if k not in LINE_KEYS}
            if isinstance(row, dict) and any(key in row for key in LINE_KEYS) else row for row in rows]


def header(payload, key: str):
    """表头新键的值；版本 ≤ 20 或没带是 ``None``。"""
    return (payload or {}).get(key) if active(payload) else None


def party(payload, block: str, key: str):
    """对方块（``supplier`` / ``customer``）新键的值；版本 ≤ 20 或没带是 ``None``。"""
    data = (payload or {}).get(block)
    return data.get(key) if active(payload) and isinstance(data, dict) else None


# ---------------- 类型与取值（不连账套） ----------------


def _dec(value) -> Optional[Decimal]:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _be_date(value) -> bool:
    text = value if isinstance(value, str) else ""
    if not (len(text) == 6 and text.isdigit()):
        return False
    try:
        date(1957 + int(text[:2]), int(text[2:4]), int(text[4:6]))  # 真有这一天（2 月 30 日之类的不收）；佛历 2 位年同 dbf_schema
    except ValueError:
        return False
    return True


def shape_problem(payload: Dict[str, Any], doctype: str, block: str) -> Optional[Tuple[str, str]]:
    """新键的类型与取值；合格回 ``None``，否则 ``(子码, 原文)``（调用方整单拒收 ``PAYLOAD_INVALID``）。只在 :func:`active` 时调用。"""

    def bad(detail, code=ERR_PAYLOAD_INVALID):
        return code, detail

    if "guided" in payload and not isinstance(payload["guided"], bool):
        return bad("guided must be a boolean")
    if "due_date_be" in payload:
        due = payload["due_date_be"]
        if doctype not in CREDIT_DOCTYPES:
            return bad("due_date_be only goes on a credit purchase or a credit sale")
        if not _be_date(due):
            return bad("due_date_be must be Buddhist YYMMDD")
        if due < str(payload.get("docdate_be") or ""):
            return bad("due_date_be is earlier than the document date", ERR_INVALID_DUE_DATE)
    if "cash_account" in payload:
        if doctype not in CASH_DOCTYPES or "pos" in payload or "refund" in payload:
            return bad("cash_account only goes on a plain cash purchase or cash sale")
        if not _text(payload["cash_account"]):
            return bad("cash_account must be an account code")
    for index, item in enumerate(payload.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        for key in ("unit_code", "acccod", "stkgrp"):
            if key in item and not _text(item[key]):
                return bad(f"line {index}: {key} must be a code")
        if "negallow" in item and item["negallow"] not in NEGALLOW_VALUES:
            return bad(f"line {index}: negallow must be empty, Y or N")
        if "cost_choice" in item:
            if str(item.get("item_mode") or "").strip() != STOCK_OUT:
                return bad(f"line {index}: cost_choice only goes on a stock line of a sale")
            if item["cost_choice"] not in (COST_GIVEN, COST_NONE):
                return bad(f"line {index}: cost_choice must be given or none")
            if item["cost_choice"] == COST_GIVEN:
                cost = _dec(item.get("cost")) if isinstance(item.get("cost"), str) else None
                if cost is None or cost < 0:
                    return bad(f"line {index}: cost_choice given needs a cost that is a decimal string, zero or more")
    data = payload.get(block)
    if isinstance(data, dict):
        if "type" in data and not _text(data["type"]):
            return bad(f"{block} type must be a code")
        if "payterm" in data:
            days = data["payterm"]
            if type(days) is not int or not 0 <= days <= PAYTERM_MAX:
                return bad(f"{block} payterm must be a whole number of days from 0 to {PAYTERM_MAX}")
    return None
