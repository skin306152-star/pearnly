# -*- coding: utf-8 -*-
"""载荷 21「引导录入」（约定：docs/work/2026-11-12-cc-guided-entry-express-coverage.md 第 8 节）。

人在 CC 对话里过了引导卡之后写的单带表头 ``guided: true`` 与答过的题对应的新键。这里只做三件事，写入本身仍在各写路里：

1. :func:`active` —— 只有 ``payload_version >= 21`` 的载荷才读新键；版本 ≤ 20 的载荷（LINE、网页、定时、自动推、部署前排队的）
   一个新键都不读，与以前逐字节相同（:func:`items` 把行上的新键去掉，表头 / 对方块的新键由适配层按 :func:`header` / :func:`party` 取）。
2. :func:`shape_problem` —— 新键的类型与取值（不连账套）：不合格整单拒收 ``PAYLOAD_INVALID``，到期日早于单据日回子码 ``INVALID_DUE_DATE``。
3. :func:`preflight` —— 写前只读核对账套（不动任何表）：
   - 新键的值不在这本账套里 → 拒收（``UNIT_CODE_NOT_FOUND`` / ``ITEM_GROUP_NOT_FOUND`` / ``STOCK_ACC_GROUP_UNFIT`` /
     ``PARTY_TYPE_NOT_FOUND`` / ``ACCOUNT_NOT_POSTABLE``），不回落默认；
   - 引导模式（``guided``）下原来「缺了就用默认值写」的情形 → 整单不写、回 ``*_CHOICE`` 并带候选（``Refusal.choice`` → 回执 ``meta.choice``），
     服务端把它变成引导卡上的一题（同 ``SERVICE_VAT_BASIS_CHOICE`` 的做法）：
     ``UNIT_CHOICE``（新建库存品单位解析不出；已有物料而行上的单位不是它的任何一个单位、又没有 ``factor``。显式给了 ``unit_code`` 的行不论是不是
     引导模式都不回落）、``COST_BASIS_CHOICE``（出库行没有成本基础而没给 ``cost_choice``）、``CASH_ACCOUNT_CHOICE``（账套现金科目不能记账而没给
     ``cash_account``）。负库存先核（``stock_guard``，调用方在 :func:`preflight` 的两段之间核），过了才问单位与成本。

``type`` 的合法值：这本账套已有对方档用过的类型码。对方类型在代码表 ISTAB 里是哪一段（本机一套看着是 45 / 46）没有多套原生证据，
先按「账套里用过的」核，服务端的候选也只来自已有档（记录见任务记录「没有原生证据的」一节）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_schema as S
from . import stock_lookup
from . import stock_template
from . import stock_units
from . import unit_code
from .guided_keys import (  # noqa: F401  纯函数与常量原样再导出：调用方只认 guided
    CASH_DOCTYPES, COST_GIVEN, COST_NONE, CREDIT_DOCTYPES, ERR_ACCOUNT_NOT_POSTABLE, ERR_CASH_ACCOUNT_CHOICE,
    ERR_COST_BASIS_CHOICE, ERR_INVALID_DUE_DATE, ERR_ITEM_GROUP_NOT_FOUND, ERR_PARTY_TYPE_NOT_FOUND, ERR_PAYLOAD_INVALID,
    ERR_UNIT_CHOICE, ERR_UNIT_CODE_NOT_FOUND, HEADER_KEYS, LINE_KEYS, NEGALLOW_VALUES, PARTY_KEYS, PAYTERM_MAX, STOCK_IN,
    STOCK_OUT, active, guided, header, items, operator_allowed, party, shape_problem, uses,
)

ITEM_GROUP_TABTYP = "22"  # ISTAB 里物料分类（STMAS.STKGRP）那一段：本机 SINCTEST 6 / 6 个在用的分类都在这一段
CASH_SLOT = "ACCNUM00"
_PARTY_TABLE = {"supplier": ("APMAS", "SUPTYP"), "customer": ("ARMAS", "CUSTYP")}
_ROLES = (("QUCOD", None, "stock"), ("SQUCOD", "SFACTOR", "sale"), ("PQUCOD", "PFACTOR", "purchase"),
          ("CQUCOD", "CFACTOR", "count"))


@dataclass
class Refusal:
    """整单不写的原因：``code`` 回执错误码，``detail`` 原文，``choice`` 给服务端出题用的候选（只有 ``*_CHOICE`` 带）。"""

    code: str
    detail: str
    choice: Optional[dict] = None


# ---------------- 写前只读核对账套 ----------------


def _rows(account_dir, table: str, fields: Tuple[str, ...]) -> List[dict]:
    path = Path(account_dir) / f"{table}.DBF"
    if not path.is_file():
        return []
    handle = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    handle.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in handle.field_names}
        return [{field: str(rec[field] or "").strip() if field in names else "" for field in fields}
                for rec in S.live_rows(handle)]
    finally:
        handle.close()


def item_groups(account_dir) -> List[str]:
    """这本账套的物料分类码（ISTAB TABTYP = 22）。"""
    return [row["TYPCOD"] for row in _rows(account_dir, "ISTAB", ("TABTYP", "TYPCOD"))
            if row["TABTYP"] == ITEM_GROUP_TABTYP and row["TYPCOD"]]


def party_types(account_dir, block: str) -> List[str]:
    """这本账套已有供应商 / 客户档用过的类型码。"""
    table, field = _PARTY_TABLE[block]
    return sorted({row[field] for row in _rows(account_dir, table, (field,)) if row[field]})


def _stock_lines(payload, mode: str):
    if str(payload.get("items_status") or "").strip() != "ok":
        return
    for index, item in enumerate(payload.get("items") or [], start=1):
        if isinstance(item, dict) and str(item.get("item_mode") or "").strip() == mode:
            yield index, item


def _found(account_dir, item: dict, mode: str):
    """这一行对到的 Express 库存品：``(行, 是否确定)``。给了码而码不在 → ``(None, False)``（写单时照旧停下交人重选，这里不管）；
    没给码按品名找不到 → ``(None, True)`` = 要随单新建。与写单同一个找法。"""
    from . import product_master
    from .stock_sale import StockItemNotFound, find_stock_item

    name, code = str(item.get("name") or ""), str(item.get("code") or "").strip()
    if mode == STOCK_OUT:
        try:
            row = find_stock_item(account_dir, name, code)
        except StockItemNotFound:
            return None, False
        return row, True
    if code:
        rows = stock_lookup.find_rows_by_code(account_dir, code, (stock_lookup.STOCK_TYPE,))
        return (rows[0], True) if rows else (None, False)
    found = product_master.find_stock_item_code(account_dir, name)
    return ({"stkcod": found} if found else None), True


def item_units(account_dir, stkcod: str) -> List[dict]:
    """这件物料在 Express 的各单位与系数：``[{code, factor, role}]``（role：stock / sale / purchase / count）。"""
    master = stock_units._master(account_dir, stkcod) or {}
    out = []
    for field, factor_field, role in _ROLES:
        code = str(master.get(field) or "").strip()
        if not code:
            continue
        factor = Decimal(1) if factor_field is None else stock_units._dec(master.get(factor_field))
        out.append({"code": code, "factor": format(factor.normalize(), "f"), "role": role})
    return out


def _has_factor(item: dict) -> bool:
    given = stock_units._dec(item.get("factor"))
    return given > 0 and given != 1


def _unit_line(account_dir, index: int, item: dict, mode: str, strict: bool) -> Optional[dict]:
    """这一行的单位要不要人选：要 → 候选行 ``{line, name, stkcod, units}``。``strict`` = 引导模式。"""
    explicit = str(item.get("unit_code") or "").strip()
    if not (strict or explicit):
        return None
    found, sure = _found(account_dir, item, mode)
    if not sure:
        return None
    name = str(item.get("name") or "")
    text = str(item.get("unit") or "").strip()
    code = explicit or (unit_code.resolve(account_dir, text) if text else "")
    if found is None:
        # 要随单新建的库存品：单位解析不出（现在取账套众数）→ 让人选
        return None if code else {"line": index, "name": name, "stkcod": "", "units": []}
    stkcod = str(found["stkcod"]).strip()
    if not (explicit or text):
        return None  # 票面没有单位：按这件物料的库存单位写（不是「对不上」）
    units = item_units(account_dir, stkcod)
    if (code and code in {row["code"] for row in units}) or _has_factor(item):
        return None
    return {"line": index, "name": name, "stkcod": stkcod, "units": units}


def _cost_line(account_dir, index: int, item: dict) -> Optional[dict]:
    """出库行没有成本基础（要新建的物料；结存 ≤ 0 且没有标准成本）而没给 ``cost_choice`` → 候选行 ``{line, name, stkcod, balance}``。"""
    from .stock_sale import standard_cost

    if item.get("cost_choice") in (COST_GIVEN, COST_NONE):
        return None
    found, sure = _found(account_dir, item, STOCK_OUT)
    if not sure:
        return None
    name = str(item.get("name") or "")
    if found is None:
        return {"line": index, "name": name, "stkcod": "", "balance": "0"}
    if found["totbal"] > 0 or standard_cost(account_dir, found["stkcod"]) > 0:
        return None
    balance = Decimal(str(found["totbal"]))
    return {"line": index, "name": name, "stkcod": str(found["stkcod"]).strip(),
            "balance": format(balance.normalize(), "f") if balance else "0"}


def _choice(code: str, lines: List[dict], **extra) -> dict:
    """``meta.choice``：第一行的各项在顶层（line / name / stkcod / units / balance），全部要定的行在 ``lines``。"""
    return {"code": code, **(lines[0] if lines else {}), **extra, **({"lines": lines} if lines else {})}


def invalid_values(account_dir, payload: Dict[str, Any], mode: str, block: str) -> Optional[Refusal]:
    """新键的值不在这本账套里 → 整单拒收（不回落默认）。``mode``：这张单的库存行是 :data:`STOCK_IN` 还是 :data:`STOCK_OUT`。"""
    if not active(payload):
        return None
    rows = [(i, item) for i, item in enumerate(payload.get("items") or [], start=1) if isinstance(item, dict)]
    if any("unit_code" in item for _i, item in rows):
        legal = set(unit_code.legal_codes(account_dir))
        for index, item in rows:
            if "unit_code" in item and str(item["unit_code"]).strip() not in legal:
                return Refusal(ERR_UNIT_CODE_NOT_FOUND, f"line {index}: unit code {item['unit_code']!r} is not in the unit table")
    if any("stkgrp" in item for _i, item in rows):
        groups = set(item_groups(account_dir))
        for index, item in rows:
            if "stkgrp" in item and str(item["stkgrp"]).strip() not in groups:
                return Refusal(ERR_ITEM_GROUP_NOT_FOUND, f"line {index}: item group {item['stkgrp']!r} is not in the group table")
    for index, item in rows:
        if "acccod" in item and str(item.get("item_mode") or "").strip() in (STOCK_IN, STOCK_OUT):
            try:
                stock_template.assert_usable_group(account_dir, str(item["acccod"]).strip())
            except stock_template.InventoryAccountUnfit as exc:
                return Refusal(stock_template.ERR_STOCK_GROUP, f"line {index}: {exc}")
    data = payload.get(block)
    if isinstance(data, dict) and "type" in data and str(data["type"]).strip() not in party_types(account_dir, block):
        return Refusal(ERR_PARTY_TYPE_NOT_FOUND, f"{block} type {data['type']!r} is not used by any {block} in this account set")
    cash = payload.get("cash_account")
    if cash is not None and not account_rules.postable_account(account_dir, str(cash).strip()):
        return Refusal(ERR_ACCOUNT_NOT_POSTABLE, f"cash account {cash!r} is not a postable account")
    return None


def choices_needed(account_dir, payload: Dict[str, Any], mode: str) -> Optional[Refusal]:
    """要人定的（单位 → 成本 → 现金科目，一次回一类、这一类的行全部带回）。调用方先核负库存再调这里。"""
    if not active(payload):
        return None
    strict = guided(payload)
    lines = [row for row in (_unit_line(account_dir, index, item, mode, strict)
                             for index, item in _stock_lines(payload, mode)) if row]
    if lines:
        return Refusal(ERR_UNIT_CHOICE, "the unit of line " + ", ".join(str(row["line"]) for row in lines) + " needs a choice",
                       _choice(ERR_UNIT_CHOICE, lines, unit_table=unit_code.legal_codes(account_dir)))
    if not strict:
        return None
    if mode == STOCK_OUT:
        lines = [row for row in (_cost_line(account_dir, index, item) for index, item in _stock_lines(payload, mode)) if row]
        if lines:
            return Refusal(ERR_COST_BASIS_CHOICE,
                           "no cost basis for line " + ", ".join(str(row["line"]) for row in lines),
                           _choice(ERR_COST_BASIS_CHOICE, lines))
    doctype = str(payload.get("doctype") or "").strip().upper()
    if doctype in CASH_DOCTYPES and "pos" not in payload and "refund" not in payload and payload.get("cash_account") is None:
        cash = account_rules.default_account(account_dir, CASH_SLOT)
        if not (cash and account_rules.postable_account(account_dir, cash)):
            return Refusal(ERR_CASH_ACCOUNT_CHOICE, f"the cash account of this account set {cash!r} is not postable",
                           _choice(ERR_CASH_ACCOUNT_CHOICE, [], cash_account=cash))
    return None


__all__ = [
    "CASH_DOCTYPES", "COST_GIVEN", "COST_NONE", "CREDIT_DOCTYPES", "HEADER_KEYS", "LINE_KEYS", "PARTY_KEYS", "Refusal",
    "active", "choices_needed", "guided", "header", "invalid_values", "item_groups", "item_units", "items",
    "party", "party_types", "shape_problem", "uses",
]
