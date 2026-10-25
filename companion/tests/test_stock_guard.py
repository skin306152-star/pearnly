# -*- coding: utf-8 -*-
"""不允许负库存的账套写销货前核结存（出入库第一期，10-25；stock_guard）。

- ISINFO.NEGALLOW = N：卖超结存 → 整单不写，回 STOCK_INSUFFICIENT（转人工）。照 Express（服务器测试账套实录，主会话 10-25 定稿）：
  CHKNEGLEV 1 / 2（空按 2）按单个仓——每行按实际写入的仓（行上的仓库码，没带就是主仓 MAINLOC）的 STLOC.LOCBAL 核，同单同品同仓合计，
  各仓合计和 STMAS.MREMBAL 对不上的老账也按 STLOC；CHKNEGLEV 0 只按全仓 STMAS.MREMBAL 合计，允许单个仓变负；
- 够卖、这件品自己标了允许负库存（STMAS.NEGALLOW = Y）、账套允许负库存（NEGALLOW ≠ N）、随单新建的物料：照写；
- 没有 STLOC：按全仓 STMAS.MREMBAL 核。
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
    assert "ICE01@01 need 6 have 5" in res.error  # 没带仓库码：写进主仓 01，按 01 仓的 5 核，同单两行合计 6
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


def test_when_the_warehouses_hold_less_than_the_stock_balance_the_warehouse_still_decides(acct):
    """老账套 STLOC 偏少（主仓 1，STMAS 结存 5）：Express 照 01 仓的 1 拦，我们也一样，卖 2 就拦；01 仓够才写。"""
    _no_negative(acct)
    _set_stloc(acct, "ICE01", "01", LOCBAL=1.0, BEGBAL=1.0)
    short = stock_guard.shortages(acct, [_item("ICE01", 2, 60.0, "stock_sale")], [None])
    assert short == [{"stkcod": "ICE01", "loccod": "01", "need": "2", "have": "1"}]
    short = stock_guard.shortages(acct, [_item("ICE01", 1, 30.0, "stock_sale"), _item("ICE01", 1, 30.0, "stock_sale", loccod="01")],
                                  [None, "01"])
    assert short == [{"stkcod": "ICE01", "loccod": "01", "need": "2", "have": "1"}]  # 没带仓库码的行与写明 01 的行同一个仓，合起来核
    assert _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")])).ok


def test_a_warehouse_without_a_row_for_the_item_holds_nothing(acct):
    """主仓 5 = 结存 5，卖到 02 仓（没有这件品的行）按 0 拦。"""
    _no_negative(acct)
    short = stock_guard.shortages(acct, [_item("ICE01", 1, 30.0, "stock_sale", loccod="02")], ["02"])
    assert short == [{"stkcod": "ICE01", "loccod": "02", "need": "1", "have": "0"}]


def test_a_line_without_a_warehouse_code_is_checked_against_the_main_warehouse(acct):
    """服务器测试账套实录同样的情形（货都在 02 仓、主仓 0）：Express 从 01 仓卖会拦。Cowork 销项不带仓库码、写进主仓 01，
    按 01 仓的 0 核 → 停下转人工（回报写明 01 仓要 3 有 0）；行上写 02 仓才照写。"""
    _no_negative(acct)
    assert _buy(acct, _purchase([_item("ICE01", 4, 40.0, "stock_item")], loccod="02")).ok
    _set_stloc(acct, "ICE01", "01", LOCBAL=0.0, BEGBAL=0.0)
    _set_item(acct, "ICE01", MREMBAL=4.0, TOTBAL=4.0)
    before = _state(acct)
    res = _sell(acct, _sale([_item("ICE01", 3, 90.0, "stock_sale")]), make_backup=True)
    assert not res.ok and res.error_code == stock_guard.ERR_STOCK_INSUFFICIENT
    assert "ICE01@01 need 3 have 0" in res.error
    assert _state(acct) == before
    assert _sell(acct, _sale([_item("ICE01", 3, 90.0, "stock_sale", loccod="02")])).ok


def test_level_zero_checks_only_the_whole_balance_and_lets_a_warehouse_go_negative(acct):
    """CHKNEGLEV = 0（服务器测试账套 1-乙）：01 仓 0、02 仓 4、全仓 4，从 01 仓卖 3 照写，01 仓变 −3；卖到合计超过全仓才拦。"""
    isinfo(acct, MAINLOC="01", NEGALLOW="N", CHKNEGLEV="0")
    assert _buy(acct, _purchase([_item("ICE01", 4, 40.0, "stock_item")], loccod="02")).ok
    _set_stloc(acct, "ICE01", "01", LOCBAL=0.0, BEGBAL=0.0)
    _set_item(acct, "ICE01", MREMBAL=4.0, TOTBAL=4.0)
    assert stock_guard.shortages(acct, [_item("ICE01", 3, 90.0, "stock_sale")], [None]) == []
    short = stock_guard.shortages(acct, [_item("ICE01", 3, 90.0, "stock_sale"), _item("ICE01", 2, 60.0, "stock_sale", loccod="02")],
                                  [None, "02"])
    assert short == [{"stkcod": "ICE01", "loccod": "all", "need": "5", "have": "4"}]  # 两个仓的行合起来对全仓
    res = _sell(acct, _sale([_item("ICE01", 3, 90.0, "stock_sale")]))
    assert res.ok, res.error
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == -3.0


def test_levels_one_and_two_check_the_single_warehouse(acct):
    """CHKNEGLEV = 1 与 2（服务器测试账套 1-甲 / 1-丙 / 1-丁）：同样的货，从 01 仓卖 3 被拦，不看全仓合计。"""
    for level in ("1", "2", ""):
        isinfo(acct, MAINLOC="01", NEGALLOW="N", CHKNEGLEV=level)
        _set_stloc(acct, "ICE01", "01", LOCBAL=0.0, BEGBAL=0.0)
        _set_item(acct, "ICE01", MREMBAL=4.0, TOTBAL=4.0)
        short = stock_guard.shortages(acct, [_item("ICE01", 3, 90.0, "stock_sale")], [None])
        assert short == [{"stkcod": "ICE01", "loccod": "01", "need": "3", "have": "0"}], level
        assert stock_guard.by_warehouse(acct)


def test_negative_allowed_book_is_not_checked_at_any_level(acct):
    for level in ("0", "2"):
        isinfo(acct, MAINLOC="01", NEGALLOW="Y", CHKNEGLEV=level)
        assert stock_guard.shortages(acct, [_item("ICE01", 99, 90.0, "stock_sale")], [None]) == []
