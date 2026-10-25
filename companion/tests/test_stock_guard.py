# -*- coding: utf-8 -*-
"""不允许负库存的账套写销货前核结存（出入库第一期，10-25；stock_guard）。

- ISINFO.NEGALLOW = N：卖超这件品在这个仓的结存（STLOC.LOCBAL；同单同品同仓合计）→ 整单不写，回 STOCK_INSUFFICIENT（转人工）；
- 够卖、这件品自己标了允许负库存（STMAS.NEGALLOW = Y）、账套允许负库存（NEGALLOW ≠ N）、随单新建的物料：照写；
- 没有 STLOC，或这件品各仓合计和 STMAS.MREMBAL 对不上（老账套 STLOC 偏少）：按全仓 STMAS.MREMBAL 核，宁可少拦不误拦。
"""

from __future__ import annotations

import dbf

from account_rules_fixture import isinfo
from companion import dbf_schema as S
from companion import stock_guard
from companion import worker
from test_stock_location import _buy, _item, _no_runner, _purchase, _rows, _sale, _sell, _state, _stloc, acct  # noqa: F401


def _set_item(acct, code, **fields):
    table = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        for rec in S.live_rows(table):
            if str(rec["STKCOD"]).strip() == code:
                with rec as row:
                    for name, value in fields.items():
                        row[name] = value
    finally:
        table.close()


def _no_negative(acct):
    isinfo(acct, MAINLOC="01", NEGALLOW="N")


def test_selling_more_than_the_warehouse_holds_writes_nothing(acct):
    _no_negative(acct)
    before = _state(acct)
    res = _sell(acct, _sale([_item("ICE01", 3, 90.0, "stock_sale"), _item("ICE01", 3, 90.0, "stock_sale")]),
                make_backup=True)
    assert not res.ok and res.error_code == stock_guard.ERR_STOCK_INSUFFICIENT == "STOCK_INSUFFICIENT"
    assert "ICE01@01 need 6 have 5" in res.error  # 主仓期初 5，同单两行合计 6
    assert _state(acct) == before
    assert worker._outcome_for(res.error_code) == "needs_review"


def test_enough_stock_or_an_item_that_may_go_negative_is_written(acct):
    _no_negative(acct)
    res = _sell(acct, _sale([_item("ICE01", 5, 150.0, "stock_sale")]))
    assert res.ok, res.error
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == 0.0
    _set_item(acct, "ICE01", NEGALLOW="Y")
    res = _sell(acct, _sale([_item("ICE01", 2, 60.0, "stock_sale")]))
    assert res.ok, res.error
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == -2.0


def test_the_check_follows_the_line_warehouse(acct):
    _no_negative(acct)
    assert _buy(acct, _purchase([_item("ICE01", 4, 40.0, "stock_item")], loccod="02")).ok
    res = _sell(acct, _sale([_item("ICE01", 5, 150.0, "stock_sale", loccod="02")]))
    assert not res.ok and "ICE01@02 need 5 have 4" in res.error  # 主仓那 5 个不算
    assert _sell(acct, _sale([_item("ICE01", 4, 120.0, "stock_sale", loccod="02")])).ok


def test_an_account_set_that_allows_negative_stock_is_written_as_before(acct):
    isinfo(acct, MAINLOC="01", NEGALLOW="Y")
    res = _sell(acct, _sale([_item("ICE01", 9, 270.0, "stock_sale")]))
    assert res.ok, res.error
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == -4.0


def test_a_material_created_with_the_sale_is_not_checked(acct):
    _no_negative(acct)
    item = {"name": "น้ำแข็งบด", "code": "", "qty": "2", "unit_price": "30", "amount": "60.00", "unit": "ถัง",
            "item_mode": "stock_sale"}
    res = _sell(acct, _sale([item]))
    assert res.ok, res.error


def test_without_stloc_the_whole_stock_balance_is_used(acct):
    _no_negative(acct)
    for path in acct.glob("STLOC.*"):
        path.unlink()
    assert stock_guard.shortages(acct, [_item("ICE01", 5, 150.0, "stock_sale")], None) == []
    short = stock_guard.shortages(acct, [_item("ICE01", 6, 180.0, "stock_sale")], None)
    assert short == [{"stkcod": "ICE01", "loccod": "all", "need": "6", "have": "5"}]


def _set_stloc(acct, code, loc, **fields):
    table = dbf.Table(str(acct / "STLOC.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        for rec in S.live_rows(table):
            if str(rec["STKCOD"]).strip() == code and str(rec["LOCCOD"]).strip() == loc:
                with rec as row:
                    for name, value in fields.items():
                        row[name] = value
    finally:
        table.close()


def test_when_the_warehouses_hold_less_than_the_stock_balance_the_whole_balance_is_used(acct):
    """老账套 STLOC 偏少（各仓 1，STMAS 结存 5）：按全仓 5 核，卖 4 照写，不误拦；卖 6 才拦。"""
    _no_negative(acct)
    _set_stloc(acct, "ICE01", "01", LOCBAL=1.0, BEGBAL=1.0)
    assert stock_guard.shortages(acct, [_item("ICE01", 4, 120.0, "stock_sale")], [None]) == []
    short = stock_guard.shortages(acct, [_item("ICE01", 3, 90.0, "stock_sale"), _item("ICE01", 3, 90.0, "stock_sale", loccod="02")],
                                  [None, "02"])
    assert short == [{"stkcod": "ICE01", "loccod": "all", "need": "6", "have": "5"}]  # 两个仓的行合起来对全仓
    res = _sell(acct, _sale([_item("ICE01", 4, 120.0, "stock_sale")]))
    assert res.ok, res.error


def test_when_the_warehouses_match_the_stock_balance_the_line_warehouse_is_used(acct):
    """对得上（主仓 5 = 结存 5）：照按仓核，卖到 02 仓（没有这件品的行）就拦。"""
    _no_negative(acct)
    short = stock_guard.shortages(acct, [_item("ICE01", 1, 30.0, "stock_sale", loccod="02")], ["02"])
    assert short == [{"stkcod": "ICE01", "loccod": "02", "need": "1", "have": "0"}]
