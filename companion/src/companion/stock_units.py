# -*- coding: utf-8 -*-
"""库存行的单位与换算（验收 10-25：包装单位按 1 写进 Express，结存错 25 倍）。所有写库存的路径（进货、销货、库存单、日汇总）共用。

Express 原生记法（S 盘真账 6902ASC / 6907GIZ 只读核对）：商品主档 STMAS 有库存单位 ``QUCOD``，另有采购 / 销售 / 计数三个包装单位
（``PQUCOD`` / ``PFACTOR``、``SQUCOD`` / ``SFACTOR``、``CQUCOD`` / ``CFACTOR``）。用包装单位录的行：``TQUCOD`` = 包装单位码、``TFACTOR`` =
系数、``TRNQTY`` = 包装数量、``XTRNQTY`` = 数量 × 系数（库存单位）、``UNITPR`` = 每包的价、``XUNITPR`` = 每个库存单位的成本。
结存（STMAS / STLOC）一律按 ``XTRNQTY`` 动。

认法：行上的单位按这套账的单位表解析成码（`unit_code.resolve`），是这件商品三个包装单位之一且系数不是 1 → 照原生记法写；
不是这件商品的单位、但来源（ERP）给了自己的换算率 → 换成库存单位写（``TQUCOD`` = 库存单位、``TFACTOR`` = 1、数量 × 换算率）；
都不是 → 按 1 写、单位码写这件商品的库存单位码并记日志（单位码只认商品自己的，固定映射表只在没有主档时兜底）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import dbf

from . import dbf_schema as S
from . import unit_code
from .logger import get_logger

_PACKS = (("SQUCOD", "SFACTOR"), ("PQUCOD", "PFACTOR"), ("CQUCOD", "CFACTOR"))
log = get_logger(__name__)


@dataclass(frozen=True)
class Units:
    """一行要写的单位：``unit`` 是交给 ``localize_stcrd`` 的单位串（码或名），``factor`` 写进 TFACTOR，``qty`` 写进 TRNQTY，
    ``base_qty`` 写进 XTRNQTY 并用来动结存。"""

    unit: str
    factor: Decimal
    qty: Decimal
    base_qty: Decimal


def _dec(value) -> Decimal:
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return number if number.is_finite() else Decimal(0)


def _text(rec, name: str) -> str:
    try:
        return str(rec[name] or "").strip()
    except (KeyError, dbf.FieldMissingError):
        return ""


def _master(account_dir, stkcod: str) -> Optional[dict]:
    path = Path(account_dir) / "STMAS.DBF"
    if not stkcod or not path.is_file():
        return None
    table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in table.field_names}
        for rec in S.live_rows(table):
            if _text(rec, "STKCOD") == stkcod:
                return {name: (rec[name] if name in names else None)
                        for name in ("QUCOD", *(field for pair in _PACKS for field in pair))}
    finally:
        table.close()
    return None


def resolve(account_dir, stkcod: str, unit: str, qty, given_factor=None) -> Units:
    """这一行（存货 ``stkcod``、单位 ``unit``、数量 ``qty``）照 Express 怎么写。``given_factor``：来源自己的换算率（ERP 商品单位表）。

    单位码一律用这件商品自己在 STMAS 上的单位（库存单位 QUCOD 或三个包装单位）；行上的单位不是它的单位时写库存单位码、记日志
    （主会话 10-25：固定映射表只在没有主档时兜底）。"""
    qty = _dec(qty)
    unit = str(unit or "").strip()
    master = _master(account_dir, stkcod)
    code = unit_code.resolve(account_dir, unit) if unit else ""
    given = _dec(given_factor)
    if master is None:
        # 没有主档（不该发生：库存行写之前都已找到或建好存货）：退回单位表 / 固定映射，照来源的换算率
        log.info("存货 %s 没有主档，单位 %r 按单位表写", stkcod, unit)
        if given > 0 and given != 1:
            return Units(code or unit, Decimal(1), qty * given, qty * given)
        return Units(code or unit, Decimal(1), qty, qty)
    stock_unit = str(master.get("QUCOD") or "").strip()
    if code and code == stock_unit:
        return Units(code, Decimal(1), qty, qty)
    if code:
        for unit_field, factor_field in _PACKS:
            factor = _dec(master.get(factor_field))
            if str(master.get(unit_field) or "").strip() == code and factor > 0 and factor != 1:
                if given > 0 and given != factor:
                    # 主档不一致（例：ERP「箱 = 12」、Express「箱 = 10」）：照 Express 写，记下来查（验收 10-25）
                    log.warning("存货 %s 单位 %r：ERP 换算率 %s 与 Express 系数 %s 不一致，按 Express 写", stkcod, unit, given, factor)
                return Units(code, factor, qty, qty * factor)
    if given > 0 and given != 1:
        return Units(stock_unit or code or unit, Decimal(1), qty * given, qty * given)
    if unit and stock_unit:
        log.info("单位 %r 不是存货 %s 在 Express 的单位，按库存单位 %s 写（数量照原样）", unit, stkcod, stock_unit)
    return Units(stock_unit or code or unit, Decimal(1), qty, qty)


def stamp(row: dict, units: Units, base_cost=None) -> dict:
    """把 :func:`resolve` 的结果写进一条刚建好的 STCRD 行：TFACTOR、XTRNQTY（库存单位），给了 ``base_cost`` 再写 XUNITPR（每个库存单位的成本）。
    TRNQTY / UNITPR / TRNVAL 由行的建法按交易单位写（调用方传 ``units.qty`` 与每包的价）。"""
    row["TFACTOR"] = float(units.factor)
    row["XTRNQTY"] = float(units.base_qty)
    if base_cost is not None:
        row["XUNITPR"] = float(base_cost)
    return row


__all__ = ["Units", "resolve", "stamp"]
