# -*- coding: utf-8 -*-
"""不允许负库存的账套写销货前核结存（出入库第一期，10-25；stock_guard）。

- ISINFO.NEGALLOW = N：卖超结存 → 整单不写，回 STOCK_INSUFFICIENT（转人工）。照 Express（服务器测试账套实录，主会话 10-25 定稿）：
  CHKNEGLEV 1 / 2（空按 2）按单个仓——每行按实际写入的仓（行上的仓库码，没带就是主仓 MAINLOC）的 STLOC.LOCBAL 核，同单同品同仓合计，
  各仓合计和 STMAS.MREMBAL 对不上的老账也按 STLOC；CHKNEGLEV 0 只按全仓 STMAS.MREMBAL 合计，允许单个仓变负；
- 够卖、这件品自己标了允许负库存（STMAS.NEGALLOW = Y）、账套允许负库存（NEGALLOW ≠ N）：照写；
- 随单新建的物料按结存 0 一起核（覆盖表 G-B6，11-12，对所有入口生效）：账套不许负库存 → 整单不写，回报带品名与 new；账套允许 → 照写；
- 没有 STLOC：按全仓 STMAS.MREMBAL 核；
- 商品级 STMAS.NEGALLOW 盖过账套（服务器测试账套 1-戊、第 6 条）：Y 不核，N 当作账套不许负库存（按 CHKNEGLEV 按仓 / 全仓，账套 Y 也核），
  空跟账套，A 先跟账套、回报标 item A。
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


_NEW_ITEM = {"name": "น้ำแข็งบด", "code": "", "qty": "2", "unit_price": "30", "amount": "60.00", "unit": "ถัง",
             "item_mode": "stock_sale"}


def test_a_material_created_with_the_sale_counts_as_zero_stock(acct):
    """覆盖表 G-B6（主会话 11-12 定稿，所有入口）：账套不许负库存时，随单新建的物料结存是 0，这张销货 Express 自己也写不进 →
    整单不写、不建档；回报带品名与 new。以前不核，新建的档又写死允许负库存，会写出 Express 不让写的负数。"""
    _no_negative(acct)
    before = _state(acct)
    res = _sell(acct, _sale([_item("ICE01", 6, 180.0, "stock_sale"), dict(_NEW_ITEM)]), make_backup=True)
    assert not res.ok and res.error_code == stock_guard.ERR_STOCK_INSUFFICIENT
    assert res.shortages == [{"stkcod": "ICE01", "loccod": "01", "need": "6", "have": "5"},
                             {"stkcod": "", "loccod": "01", "need": "2", "have": "0", "name": "น้ำแข็งบด", "new": True}]
    assert _state(acct) == before
    meta = worker._push_meta(res, "sales", str(acct))
    assert meta["shortages"] == res.shortages and meta["written"] is False


def test_the_shortage_text_keeps_the_format_the_erp_page_parses(acct):
    """原文格式不能动：ERP 单据页（transactions.js）与待处理按「物料@仓 need 要 have 有」逐行解析。已有物料的行逐字不变；
    新建的行物料那一格是品名、行尾加 new（同库存单新建行），旧解析照样取出四格。"""
    import re

    _no_negative(acct)
    res = _sell(acct, _sale([_item("ICE01", 6, 180.0, "stock_sale"), dict(_NEW_ITEM)]))
    assert res.error == ("not enough stock in Express (negative stock not allowed): "
                         "ICE01@01 need 6 have 5, น้ำแข็งบด@01 need 2 have 0 new")
    parsed = re.findall(r"(\S+)@(\S+) need (\S+) have (\S+)", res.error)  # transactions.js 的那条正则
    # （多行时旧正则把行间的逗号连在「有」那一格后面，是页面那边原有的样子，这里照实比）
    assert parsed == [("ICE01", "01", "6", "5,"), ("น้ำแข็งบด", "01", "2", "0")]
    old_only = _sell(acct, _sale([_item("ICE01", 6, 180.0, "stock_sale")]))
    assert old_only.error == "not enough stock in Express (negative stock not allowed): ICE01@01 need 6 have 5"


def test_a_new_material_is_sold_when_the_book_allows_negative_stock_or_the_line_says_so(acct):
    """账套允许负库存：随单新建的物料照写（档上「允许负库存」留空）。账套不许、但行上 negallow = Y（载荷 21）：照写、档上记 Y；
    行上 negallow = N 而账套允许：不写。"""
    res = _sell(acct, _sale([dict(_NEW_ITEM)]))
    assert res.ok, res.error
    assert _rows(acct, "STMAS", STKDES="น้ำแข็งบด")[0]["NEGALLOW"].strip() == ""
    refused = _sell(acct, _sale([dict(_NEW_ITEM, name="น้ำแข็งแห้ง", negallow="N")], payload_version=21))
    assert not refused.ok and refused.error_code == stock_guard.ERR_STOCK_INSUFFICIENT
    assert refused.shortages[0]["name"] == "น้ำแข็งแห้ง" and refused.shortages[0]["new"] is True
    _no_negative(acct)
    allowed = _sell(acct, _sale([dict(_NEW_ITEM, name="น้ำแข็งแห้ง", negallow="Y")], payload_version=21))
    assert allowed.ok, allowed.error
    assert _rows(acct, "STMAS", STKDES="น้ำแข็งแห้ง")[0]["NEGALLOW"] == "Y"
    # 版本 ≤ 20 的载荷不读行上的 negallow：账套不许负库存就不写
    old = _sell(acct, _sale([dict(_NEW_ITEM, name="น้ำแข็งป่น", negallow="Y")]))
    assert not old.ok and old.error_code == stock_guard.ERR_STOCK_INSUFFICIENT


def test_a_new_material_is_not_checked_when_the_lines_are_not_written(acct):
    """明细不可信（items_status 不是 ok）的单只记表头、不新建物料：新建的行不核。"""
    _no_negative(acct)
    res = _sell(acct, _sale([dict(_NEW_ITEM)], items_status="mismatch"))
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


def test_the_item_flag_overrides_the_book_setting(acct):
    """商品级 Y / N / 空 × 账套 NEGALLOW Y / N（01 仓 5、02 仓 0）：从 02 仓卖 1 个。"""
    sale = [_item("ICE01", 1, 30.0, "stock_sale", loccod="02")]
    blocked = [{"stkcod": "ICE01", "loccod": "02", "need": "1", "have": "0"}]
    for book, flag, expected in (("N", "", blocked), ("N", "Y", []), ("N", "N", blocked),
                                 ("Y", "", []), ("Y", "Y", []), ("Y", "N", blocked)):
        isinfo(acct, MAINLOC="01", NEGALLOW=book, CHKNEGLEV="2")
        _set_item(acct, "ICE01", NEGALLOW=flag)
        assert stock_guard.shortages(acct, sale, ["02"]) == expected, (book, flag)


def test_an_item_that_may_not_go_negative_follows_the_book_check_level(acct):
    """服务器测试账套第 6 条：商品标 N 当作账套不许负库存，按账套 CHKNEGLEV 判——0 级看全仓合计（账套 Y / 0：01 仓 −2、全仓 7，卖 1 照存），
    2 级看单仓（账套 Y / 2 同样情形录行就 4224）。"""
    _set_item(acct, "ICE01", NEGALLOW="N")
    for book in ("Y", "N"):
        isinfo(acct, MAINLOC="01", NEGALLOW=book, CHKNEGLEV="0")
        assert stock_guard.shortages(acct, [_item("ICE01", 1, 30.0, "stock_sale", loccod="02")], ["02"]) == [], book
        short = stock_guard.shortages(acct, [_item("ICE01", 6, 30.0, "stock_sale", loccod="02")], ["02"])
        assert short == [{"stkcod": "ICE01", "loccod": "all", "need": "6", "have": "5"}], book
        isinfo(acct, MAINLOC="01", NEGALLOW=book, CHKNEGLEV="2")
        short = stock_guard.shortages(acct, [_item("ICE01", 1, 30.0, "stock_sale", loccod="02")], ["02"])
        assert short == [{"stkcod": "ICE01", "loccod": "02", "need": "1", "have": "0"}], book


def test_an_item_marked_a_follows_the_book_and_is_named_in_the_report(acct):
    isinfo(acct, MAINLOC="01", NEGALLOW="Y", CHKNEGLEV="2")
    _set_item(acct, "ICE01", NEGALLOW="A")
    assert stock_guard.shortages(acct, [_item("ICE01", 9, 30.0, "stock_sale")], [None]) == []
    isinfo(acct, MAINLOC="01", NEGALLOW="N", CHKNEGLEV="2")
    short = stock_guard.shortages(acct, [_item("ICE01", 9, 30.0, "stock_sale")], [None])
    assert short == [{"stkcod": "ICE01", "loccod": "01", "need": "9", "have": "5", "item": "A"}]
    assert stock_guard.message(short).endswith("ICE01@01 need 9 have 5 item A")


def test_a_book_that_allows_negative_stock_writes_an_item_marked_no_only_when_it_is_there(acct):
    isinfo(acct, MAINLOC="01", NEGALLOW="Y")
    _set_item(acct, "ICE01", NEGALLOW="N")
    res = _sell(acct, _sale([_item("ICE01", 6, 180.0, "stock_sale")]))
    assert not res.ok and "ICE01@01 need 6 have 5" in res.error
    assert _sell(acct, _sale([_item("ICE01", 5, 150.0, "stock_sale")])).ok
