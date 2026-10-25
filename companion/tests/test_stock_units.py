# -*- coding: utf-8 -*-
"""包装单位照 Express 原生记法写（stock_units；验收 10-25 实测 CO-00088 / T9TSDV12「1 กล่อง = 25 เส้น」按 1 写、结存 0 → −1）。

Express 原生（S 盘真账 6902ASC / 6907GIZ）：TQUCOD = 包装单位码、TFACTOR = 系数、TRNQTY = 包装数量、XTRNQTY = 数量 × 系数，
UNITPR = 每包的价、XUNITPR = 每个库存单位的成本；结存按 XTRNQTY 动。进货、销货、库存单、负库存核对都用同一个换法。
"""

from __future__ import annotations

from decimal import Decimal

from account_rules_fixture import isinfo
from companion import stock_guard, stock_units
from test_dbf_stock import _entry, _write, book  # noqa: F401
from test_stock_location import _append, _buy, _item, _purchase, _rows, _sale, _sell, _stloc, acct  # noqa: F401
from test_stock_guard import _set_item


def _packs(acct):
    """ICE01：库存单位 ถุง（ถง），销售包装 กล่อง（กล）= 25，采购包装 ลัง（ลง）= 100。"""
    _append(acct / "ISTAB.DBF", [{"TABTYP": "20", "TYPCOD": "กล", "TYPDES": "กล่อง"},
                                 {"TABTYP": "20", "TYPCOD": "ลง", "TYPDES": "ลัง"},
                                 {"TABTYP": "20", "TYPCOD": "ถง", "TYPDES": "ถุง"}])
    _set_item(acct, "ICE01", SQUCOD="กล", SFACTOR=25.0, PQUCOD="ลง", PFACTOR=100.0)


def test_resolve_follows_the_item_packs_then_the_given_factor(acct):
    _packs(acct)
    assert stock_units.resolve(acct, "ICE01", "กล่อง", "2") == stock_units.Units("กล", Decimal(25), Decimal(2), Decimal(50))
    assert stock_units.resolve(acct, "ICE01", "ลัง", "1").base_qty == Decimal(100)
    assert stock_units.resolve(acct, "ICE01", "ถุง", "3") == stock_units.Units("ถง", Decimal(1), Decimal(3), Decimal(3))
    # 不是这件商品的单位、来源也没给换算率：写商品自己的库存单位码（数量照原样），不留空、不用固定表
    assert stock_units.resolve(acct, "ICE01", "ขวด", "3") == stock_units.Units("ถง", Decimal(1), Decimal(3), Decimal(3))
    # 不是 Express 的包装单位、来源给了自己的换算率：换成库存单位写
    assert stock_units.resolve(acct, "ICE01", "แพ็ค", "2", "6") == stock_units.Units("ถง", Decimal(1), Decimal(12), Decimal(12))
    assert stock_units.resolve(acct, "ICE01", "", "4").base_qty == Decimal(4)


def test_a_purchase_in_cases_records_the_pack_and_adds_qty_times_factor(acct):
    _packs(acct)
    assert _buy(acct, _purchase([{**_item("ICE01", 2, 400.0, "stock_item"), "unit": "ลัง"}])).ok
    row = [r for r in _rows(acct, "STCRD") if r["STKCOD"].strip() == "ICE01" and r["DOCNUM"].strip().startswith("RR")][0]
    assert (row["TQUCOD"].strip(), row["TFACTOR"], row["TRNQTY"], row["XTRNQTY"]) == ("ลง", 100.0, 2.0, 200.0)
    assert row["UNITPR"] == 200.0 and row["XUNITPR"] == 2.0  # 每箱 200、每袋 2
    master = _rows(acct, "STMAS", STKCOD="ICE01")[0]
    assert master["MREMBAL"] == 205.0  # 原有 5 + 2 × 100
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == 205.0


def test_a_sale_in_boxes_takes_qty_times_factor_and_costs_it(acct):
    _packs(acct)
    assert _buy(acct, _purchase([{**_item("ICE01", 1, 200.0, "stock_item"), "unit": "ลัง"}])).ok  # 结存 105
    res = _sell(acct, _sale([{**_item("ICE01", 2, 300.0, "stock_sale"), "unit": "กล่อง"}]))
    assert res.ok, res.error
    row = [r for r in _rows(acct, "STCRD") if r["DOCNUM"].strip().startswith("IV")][0]
    assert (row["TQUCOD"].strip(), row["TFACTOR"], row["TRNQTY"], row["XTRNQTY"]) == ("กล", 25.0, 2.0, 50.0)
    assert _rows(acct, "STMAS", STKCOD="ICE01")[0]["MREMBAL"] == 55.0
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == 55.0


def test_a_stock_issue_in_boxes_records_the_pack(book):
    _packs(book)
    res = _write(book, _entry("JU", [{"code": "ICE01", "name": "ICE01", "qty": "2", "unit": "ลัง", "unit_cost": "200"}]))
    assert res.ok, res.error
    res = _write(book, _entry("OU", [{"code": "ICE01", "name": "ICE01", "qty": "1", "unit": "กล่อง"}]))
    assert res.ok, res.error
    row = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (row["TQUCOD"].strip(), row["TFACTOR"], row["TRNQTY"], row["XTRNQTY"]) == ("กล", 25.0, 1.0, 25.0)
    assert _rows(book, "STMAS", STKCOD="ICE01")[0]["MREMBAL"] == 5.0 + 200 - 25
    assert _stloc(book, "ICE01", "01")["LOCBAL"] == 180.0


def test_the_negative_check_counts_the_pack_in_stock_units(acct):
    _packs(acct)
    isinfo(acct, MAINLOC="01", NEGALLOW="N")
    short = stock_guard.shortages(acct, [{**_item("ICE01", 1, 30.0, "stock_sale", loccod="01"), "unit": "กล่อง"}], ["01"])
    assert short == [{"stkcod": "ICE01", "loccod": "01", "need": "25", "have": "5"}]  # 1 กล่อง = 25 ถุง，只有 5
