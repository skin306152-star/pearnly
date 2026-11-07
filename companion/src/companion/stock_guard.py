# -*- coding: utf-8 -*-
"""写销货前照 Express 的负库存规则先核结存（出入库第一期，10-25）：账套 ``ISINFO.NEGALLOW = 'N'`` 或商品标了不许负时
Express 自己录单会拦库存不足，CC 照写会把账写成 Express 不允许的负数。写前按 Express 结存核，不够就整单不写、回 :data:`ERR_STOCK_INSUFFICIENT`
（服务端转人工，单据上显示原因）。

- 只核卖出库存品的行（``item_mode = stock_sale``），按写单时同一个找品函数（``stock_sale.find_stock_item``，只读）对到
  Express 的存货；对不到、要随单新建的物料不核（新建的品按设计标了允许负库存，``product_master``）；
- 商品级「ส/คติดลบได้? [Y, N, A]」（``STMAS.NEGALLOW``）盖过账套设置（服务器测试账套 1-戊、第 6 条实录）：``Y`` 不核（Express 只弹 605
  提醒、照存，分仓可以变负）；``N`` 当作这件品「账套不许负库存」——账套允许负库存也核，按账套的 ``CHKNEGLEV`` 决定按仓（1 / 2）还是按全仓（0）；
  空跟账套（账套允许负库存时不核，CHKNEGLEV 对它不起作用）；``A`` 没测过，先跟账套，回报里标 ``item A``（待测）。
- 照 Express 自己拦单的办法（主会话 10-25 定稿，验收在服务器测试账套 ``70EXP\test`` 实录 CHKNEGLEV 三种取值，共享笔记「服务器test账套-录单结果」）：
  - ``ISINFO.CHKNEGLEV`` 是 1 或 2（空也按 2）且账套有 STLOC：按单个仓核。每行按**实际写入的仓**（行上的仓库码；没带就是主仓
    ``ISINFO.MAINLOC``，与写单同一条规则）的 ``LOCBAL`` 核，那个仓还没有这件品的行按 0；同一张单里同一件品同一个仓的几行合起来核。
    Express 录行时就拦那一行（Message 4224）；ERP 不能写出半张单，所以整单停在人工。不看全仓合计。
  - ``CHKNEGLEV = 0``，或账套没有 STLOC：不按仓核，同一件品全单合起来按全仓总结存 ``STMAS.TOTBAL`` 核（报出的仓记 :data:`ALL_LOCATIONS`）；
    合计够就照写，允许单个仓变负（Express 保存时才查全仓合计，Message 4091，01 仓照样存成 −3）。
  - 全仓总结存原生就等于各仓合计（S 盘 95 本 99.96%；以前按批次链 ``MREMBAL`` 核，18% 的品会核错，11-07 改）；对不上的老账照上面的规则核（原来「对不上就按全仓放行」会写出 Express 自己不允许的分仓负数，已去掉），
    ERP 同步时这件品进公司页 Express 区的差异清单（``express.masters.stock_differences``），提示去 Express 里修。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_schema as S
from . import stock_location
from . import stock_units
from .logger import get_logger

log = get_logger(__name__)

ERR_STOCK_INSUFFICIENT = "STOCK_INSUFFICIENT"
ALL_LOCATIONS = "all"
_SAME = Decimal("0.0001")
_NO = "N"
_YES = "Y"
_ITEM_UNKNOWN = "A"


def _table_rows(account_dir, name: str, fields: Tuple[str, ...]) -> List[dict]:
    table = stock_location._open(account_dir, name, dbf.READ_ONLY)
    if table is None:
        return []
    try:
        out = []
        for rec in S.live_rows(table):
            out.append({field: stock_location._text(rec, field) if field not in ("LOCBAL", "TOTBAL")
                        else stock_location._dec(stock_location._value(rec, field)) for field in fields})
        return out
    finally:
        table.close()


def negative_allowed(account_dir) -> bool:
    rows = _table_rows(account_dir, "ISINFO", ("NEGALLOW",))
    return not rows or rows[0]["NEGALLOW"].upper() != _NO


def by_warehouse(account_dir) -> bool:
    """Express 按单个仓拦（``ISINFO.CHKNEGLEV`` 1 / 2，空按 2）还是只查全仓合计（0）。"""
    rows = _table_rows(account_dir, "ISINFO", ("CHKNEGLEV",))
    level = rows[0]["CHKNEGLEV"] if rows else ""
    try:
        return not level or Decimal(level) != 0  # 真账里是字符 "0" / "1" / "2"，数字型也认
    except ArithmeticError:
        return True


def shortages(account_dir, items: List[dict], locations: Optional[List[Optional[str]]]) -> List[dict]:
    """这张销货里库存不够的行（按品 × 仓合计）：``[{stkcod, loccod, need, have}]``（商品级标 A 的另带 ``"item": "A"``）；
    要核的品都不缺时是空。"""
    from .stock_sale import StockItemNotFound, find_stock_item

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
        chosen = stock_location.location_at(locations, index)
        code = str(found["stkcod"]).strip()
        key = (code, chosen) if chosen else (code, ALL_LOCATIONS)
        # 包装单位按 Express 主档系数换成库存单位再核（与写单同一个换法，stock_units）
        units = stock_units.resolve(account_dir, code, str(item.get("unit") or ""), item.get("qty") or "1", item.get("factor"))
        need[key] = need.get(key, Decimal(0)) + abs(units.base_qty)
    return short_of(account_dir, need, locations is not None)


def short_of(account_dir, need: Dict[Tuple[str, str], Decimal], located_lines: bool) -> List[dict]:
    """按 Express 的负库存规则核一组要减掉的库存单位数量 ``{(存货码, 仓库码或 ALL_LOCATIONS): 数量}``：不够的 ``[{stkcod, loccod, need, have}]``。
    ``located_lines`` = 这张单的行带仓库码（账套有 STLOC 时才按单个仓核）。写销货（:func:`shortages`）与作废入库 / 盘盈（``dbf_void``）同一口径。"""
    if not need:
        return []
    book_checks = not negative_allowed(account_dir)
    codes = {key[0] for key in need}
    masters = {row["STKCOD"]: row for row in _table_rows(account_dir, "STMAS", ("STKCOD", "NEGALLOW", "TOTBAL"))
               if row["STKCOD"] in codes}
    flags = {code: (masters.get(code) or {}).get("NEGALLOW", "").upper() for code in codes}
    # 商品级「ส/คติดลบได้?」盖过账套设置（服务器测试账套 1-戊、第 6 条）：Y 不核；N 当作账套不许负库存（按 CHKNEGLEV 按仓 / 全仓）；空 / A 跟账套
    checked = {code for code, flag in flags.items() if flag == _NO or (flag != _YES and book_checks)}
    if not checked:
        return []
    totals = {code: (masters.get(code) or {}).get("TOTBAL", Decimal(0)) for code in checked}
    has_stloc = located_lines and stock_location._file(account_dir, stock_location.TABLE) is not None
    level_by_warehouse = by_warehouse(account_dir)
    located: Dict[Tuple[str, str], Decimal] = {}
    if has_stloc:
        located = {(row["STKCOD"], row["LOCCOD"]): row["LOCBAL"]
                   for row in _table_rows(account_dir, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL")) if row["STKCOD"] in checked}
    main = account_rules.main_location(account_dir) if has_stloc else ""
    wanted: Dict[Tuple[str, str], Decimal] = {}
    have: Dict[Tuple[str, str], Decimal] = {}
    for (code, loc), qty in need.items():
        if code not in checked:
            continue
        if has_stloc and level_by_warehouse:
            key = (code, main if loc == ALL_LOCATIONS else loc)  # 没带仓库码的行写进主仓，就按主仓核
            have[key] = located.get(key, Decimal(0))
        else:
            key = (code, ALL_LOCATIONS)
            have[key] = totals.get(code, Decimal(0))
        wanted[key] = wanted.get(key, Decimal(0)) + qty
    short = []
    for key, qty in sorted(wanted.items()):
        balance = have.get(key, Decimal(0))
        if qty > balance:
            row = {"stkcod": key[0], "loccod": key[1], "need": _num(qty), "have": _num(balance)}
            if flags[key[0]] == _ITEM_UNKNOWN:
                row["item"] = _ITEM_UNKNOWN  # 商品级 A：含义没测过，先跟账套，回报里标出来（待测）
            short.append(row)
    if short:
        log.info("库存不足（不允许负库存），整单不写：%s", short)
    return short


def _num(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def message(short: List[dict]) -> str:
    return "not enough stock in Express (negative stock not allowed): " + ", ".join(
        f"{item['stkcod']}@{item['loccod']} need {item['need']} have {item['have']}"
        + (f" item {item['item']}" if item.get("item") else "") for item in short)


__all__ = ["ALL_LOCATIONS", "ERR_STOCK_INSUFFICIENT", "by_warehouse", "message", "negative_allowed", "short_of", "shortages"]
