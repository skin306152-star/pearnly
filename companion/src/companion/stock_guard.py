# -*- coding: utf-8 -*-
"""不允许负库存的账套写销货前先核结存（出入库第一期，10-25）：``ISINFO.NEGALLOW = 'N'`` 时 Express 自己录单会拦库存不足，
CC 照写会把账写成 Express 不允许的负数。写前按 Express 结存核，不够就整单不写、回 :data:`ERR_STOCK_INSUFFICIENT`
（服务端转人工，单据上显示原因）。``NEGALLOW`` 不是 ``N`` 的账套照写（现状）。

- 只核卖出库存品的行（``item_mode = stock_sale``），按写单时同一个找品函数（``stock_sale.find_stock_item``，只读）对到
  Express 的存货；对不到、要随单新建的物料不核（新建的品按设计标了允许负库存，``product_master``）；
- 这件品自己标了允许负库存（``STMAS.NEGALLOW = 'Y'``）不核；
- 结存：账套有 STLOC、且这件品各仓 ``LOCBAL`` 合计等于 ``STMAS.MREMBAL`` 时，按这一行写进的仓（行的仓库码，没有是主仓
  ``ISINFO.MAINLOC``）的 ``LOCBAL``，那个仓还没有这件品的行按 0；同一张单里同一件品同一个仓的几行合起来核。
- 各仓合计和 ``MREMBAL`` 对不上（老账套：1.12.2 之前 Cowork 推送不记 STLOC、建物料不补 STLOC 行）或没有 STLOC：按全仓
  ``MREMBAL`` 核，同一件品各仓的行合起来（报出的仓记 :data:`ALL_LOCATIONS`）。宁可少拦，不误拦（主会话 10-25）；
  这类品 ERP 同步时进公司页 Express 区的差异清单（``express.masters.stock_differences`` 同一个判据）。
  Express 自己按仓还是按全仓拦（``ISINFO.LOC_COST`` / ``ANYLOC`` / ``CHKNEGLEV``）核实后再定对得上的那类怎么核。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_schema as S
from . import stock_location
from .logger import get_logger

log = get_logger(__name__)

ERR_STOCK_INSUFFICIENT = "STOCK_INSUFFICIENT"
ALL_LOCATIONS = "all"
_SAME = Decimal("0.0001")
_NO = "N"
_YES = "Y"


def _table_rows(account_dir, name: str, fields: Tuple[str, ...]) -> List[dict]:
    table = stock_location._open(account_dir, name, dbf.READ_ONLY)
    if table is None:
        return []
    try:
        out = []
        for rec in S.live_rows(table):
            out.append({field: stock_location._text(rec, field) if field not in ("LOCBAL", "MREMBAL")
                        else stock_location._dec(stock_location._value(rec, field)) for field in fields})
        return out
    finally:
        table.close()


def negative_allowed(account_dir) -> bool:
    rows = _table_rows(account_dir, "ISINFO", ("NEGALLOW",))
    return not rows or rows[0]["NEGALLOW"].upper() != _NO


def shortages(account_dir, items: List[dict], locations: Optional[List[Optional[str]]]) -> List[dict]:
    """这张销货里库存不够的行（按品 × 仓合计）：``[{stkcod, loccod, need, have}]``；账套允许负库存时是空。"""
    if negative_allowed(account_dir):
        return []
    from .stock_sale import StockItemNotFound, find_stock_item

    main = account_rules.main_location(account_dir)
    need: Dict[Tuple[str, str], Decimal] = {}
    for index, item in enumerate(items or []):
        if not isinstance(item, dict) or (item.get("item_mode") or "").strip() != "stock_sale":
            continue
        try:
            found = find_stock_item(account_dir, str(item.get("name") or ""), str(item.get("code") or ""))
        except StockItemNotFound:
            continue  # 码不在：写单时照旧停下交人重选
        if not found:
            continue
        loccod = stock_location.location_at(locations, index) or main
        key = (str(found["stkcod"]).strip(), loccod)
        need[key] = need.get(key, Decimal(0)) + Decimal(str(item.get("qty") or "1"))
    if not need:
        return []
    codes = {key[0] for key in need}
    allowed = {row["STKCOD"] for row in _table_rows(account_dir, "STMAS", ("STKCOD", "NEGALLOW"))
               if row["STKCOD"] in codes and row["NEGALLOW"].upper() == _YES}
    totals = {row["STKCOD"]: row["MREMBAL"]
              for row in _table_rows(account_dir, "STMAS", ("STKCOD", "MREMBAL")) if row["STKCOD"] in codes}
    have: Dict[Tuple[str, str], Decimal] = {}
    by_location = set()
    if locations is not None and stock_location._file(account_dir, stock_location.TABLE) is not None:
        located = {(row["STKCOD"], row["LOCCOD"]): row["LOCBAL"]
                   for row in _table_rows(account_dir, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL")) if row["STKCOD"] in codes}
        sums: Dict[str, Decimal] = {}
        for (code, _loc), balance in located.items():
            sums[code] = sums.get(code, Decimal(0)) + balance
        by_location = {code for code in codes if abs(sums.get(code, Decimal(0)) - totals.get(code, Decimal(0))) < _SAME}
        have.update({key: balance for key, balance in located.items() if key[0] in by_location})
        uneven = sorted(codes - by_location)
        if uneven:
            log.info("各仓结存合计与 STMAS 不一致，按全仓结存核：%s", uneven)
    whole: Dict[Tuple[str, str], Decimal] = {}
    for key, qty in need.items():
        if key[0] in by_location:
            whole[key] = qty
        else:
            merged = (key[0], ALL_LOCATIONS)
            whole[merged] = whole.get(merged, Decimal(0)) + qty
            have[merged] = totals.get(key[0], Decimal(0))
    short = []
    for key, qty in sorted(whole.items()):
        if key[0] in allowed:
            continue
        balance = have.get(key, Decimal(0))
        if qty > balance:
            short.append({"stkcod": key[0], "loccod": key[1], "need": _num(qty), "have": _num(balance)})
    if short:
        log.info("库存不足（账套不允许负库存），整单不写：%s", short)
    return short


def _num(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def message(short: List[dict]) -> str:
    return "not enough stock in Express (negative stock not allowed): " + ", ".join(
        f"{item['stkcod']}@{item['loccod']} need {item['need']} have {item['have']}" for item in short)


__all__ = ["ALL_LOCATIONS", "ERR_STOCK_INSUFFICIENT", "message", "negative_allowed", "shortages"]
