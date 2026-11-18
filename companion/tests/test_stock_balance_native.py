# -*- coding: utf-8 -*-
"""总结存与对方余额照 Express 原生（11-07，``docs/work/2026-11-07-cc-stock-balance-native-评估.md``）。

- ``STMAS.TOTBAL / TOTVAL`` 是总结存（S 盘 95 本：两者不等的 3 万件里各仓合计 = TOTBAL 占 99.96%），``MREMBAL / MREMVAL`` 是先进先出批次链。
  CC 以前把 TOTBAL 写成「MREMBAL ± 本单」：TEST 500603 写前 TOTBAL 45、MREMBAL 3，写一张进货 +1 后 TOTBAL 成了 4。
  这里夹具照它把一件品做成 TOTBAL 45 / MREMBAL 3：每种动库存的单写后 TOTBAL 必须是 45 ± 本单，判有没有货、均价、负库存核对都看总结存。
- ``UNITPR`` 随总结存重算（= TOTVAL / TOTBAL，原生）。
- 赊购 RR 加 ``APMAS.BALANCE``、记 ``LASRCV``；赊销 IV 加 ``ARMAS.BALANCE``；现购 HP、现销 HS 不动（TEST 实录）。
"""

from __future__ import annotations

import datetime as dt

import dbf
import pytest

from account_rules_fixture import isinfo, seed
from companion import dbf_schema as S
from companion import stock_guard
from companion import stock_sale
from test_dbf_stock import _entry, _line, _write, book  # noqa: F401
from test_dbf_void import _void
from test_stock_location import _buy, _item, _purchase, _rows, _sale, _sell, _stloc, acct  # noqa: F401


def _set(acct, table, key, code, **fields):
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for row in t:
            if str(row[key]).strip() == code:
                with row as rec:
                    for name, value in fields.items():
                        rec[name] = value
    finally:
        t.close()


@pytest.fixture
def apart(book):
    """ICE01：总结存 45（货值 450，均价 10），批次链只剩 3（货值 60，批均价 20），主仓 45 —— 同 TEST 500603 的样子。"""
    _set(book, "STMAS", "STKCOD", "ICE01", TOTBAL=45.0, TOTVAL=450.0, MREMBAL=3.0, MREMVAL=60.0, UNITPR=10.0)
    _set(book, "STLOC", "STKCOD", "ICE01", LOCBAL=45.0)
    seed(book, "APMAS", [{"SUPCOD": "S001", "SUPNAM": "โรงน้ำแข็ง", "BALANCE": 100.0, "LASRCV": dt.date(2026, 1, 1)}])
    seed(book, "ARMAS", [{"CUSCOD": "C001", "CUSNAM": "ร้านป้าแดง", "BALANCE": 200.0}])
    return book


def _master(acct):
    row = _rows(acct, "STMAS", STKCOD="ICE01")[0]
    return {name: row[name] for name in ("TOTBAL", "TOTVAL", "MREMBAL", "MREMVAL", "UNITPR")}


def _balance(acct, table, key, code):
    return _rows(acct, table, **{key: code})[0]["BALANCE"]


def test_a_purchase_adds_to_the_total_and_to_the_supplier_balance(apart):
    res = _buy(apart, _purchase([_item("ICE01", 5, 100.0, "stock_item")]))
    assert res.ok, res.error
    # 旧写法：TOTBAL = MREMBAL 3 + 5 = 8
    assert _master(apart) == {"TOTBAL": 50.0, "TOTVAL": 550.0, "MREMBAL": 8.0, "MREMVAL": 160.0, "UNITPR": 11.0}
    assert _stloc(apart, "ICE01", "01")["LOCBAL"] == 50.0  # 总结存仍 = 各仓合计
    supplier = _rows(apart, "APMAS", SUPCOD="S001")[0]
    assert supplier["BALANCE"] == pytest.approx(207.0)  # 100 + 含税 107
    assert supplier["LASRCV"] == dt.date(2026, 1, 15)


def test_a_cash_purchase_leaves_the_supplier_balance_alone(apart):
    res = _buy(apart, _purchase([_item("ICE01", 5, 100.0, "stock_item")], doctype="HP"))
    assert res.ok, res.error
    assert _master(apart)["TOTBAL"] == 50.0
    supplier = _rows(apart, "APMAS", SUPCOD="S001")[0]
    assert (supplier["BALANCE"], supplier["LASRCV"]) == (100.0, dt.date(2026, 1, 1))


def test_an_older_credit_purchase_does_not_move_the_last_receipt_date_back(apart):
    _set(apart, "APMAS", "SUPCOD", "S001", LASRCV=dt.date(2026, 2, 1))
    assert _buy(apart, _purchase([_item("ICE01", 1, 10.0, "stock_item")])).ok
    assert _rows(apart, "APMAS", SUPCOD="S001")[0]["LASRCV"] == dt.date(2026, 2, 1)


def test_a_sale_is_costed_from_the_total_and_adds_to_the_customer_balance(apart):
    res = _sell(apart, _sale([_item("ICE01", 2, 60.0, "stock_sale")]))
    assert res.ok, res.error
    line = _rows(apart, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["XUNITPR"], line["XTRNVAL"]) == (10.0, 20.0)  # 均价 TOTVAL / TOTBAL，不是批次链的 20
    assert _master(apart) == {"TOTBAL": 43.0, "TOTVAL": 430.0, "MREMBAL": 1.0, "MREMVAL": 40.0, "UNITPR": 10.0}
    assert _balance(apart, "ARMAS", "CUSCOD", "C001") == pytest.approx(264.2)  # 200 + 含税 64.20


def test_stock_on_hand_counts_even_when_the_lot_chain_is_empty(apart):
    """总结存 45、批次链 0：以前判成没货（按标准成本 / 不结转），现在照有货按均价结转。"""
    _set(apart, "STMAS", "STKCOD", "ICE01", MREMBAL=0.0, MREMVAL=0.0)
    res = _sell(apart, _sale([_item("ICE01", 2, 60.0, "stock_sale")]))
    assert res.ok, res.error
    assert _rows(apart, "STCRD", DOCNUM=res.docnum)[0]["XTRNVAL"] == 20.0
    assert not [mode for mode in res.line_modes if mode.get("reason")]
    assert _master(apart)["TOTBAL"] == 43.0


def test_a_cash_sale_leaves_the_customer_balance_alone(apart):
    res = _sell(apart, _sale([_item("ICE01", 2, 60.0, "stock_sale")], doctype="HS"))
    assert res.ok, res.error
    assert _master(apart)["TOTBAL"] == 43.0
    assert _balance(apart, "ARMAS", "CUSCOD", "C001") == 200.0


def test_a_stock_issue_and_its_void_move_the_total(apart):
    # 领用照原生按批次链取价（11-18 实录 D4）：批次链 3 / 60 → 每个 20，不是总结存的均价 10
    res = _write(apart, _entry("OU", [_line("ICE01", 2)]))
    assert res.ok, res.error
    assert _rows(apart, "STCRD", DOCNUM=res.docnum)[0]["XUNITPR"] == 20.0
    assert _master(apart) == {"TOTBAL": 43.0, "TOTVAL": 410.0, "MREMBAL": 1.0, "MREMVAL": 20.0, "UNITPR": 9.5349}
    out = _void(apart, res.docnum, "OU", [{"seq": 1, "code": "ICE01", "loccod": "01", "qty": "2"}])
    assert out.ok, out.error
    assert _master(apart) == {"TOTBAL": 45.0, "TOTVAL": 450.0, "MREMBAL": 3.0, "MREMVAL": 60.0, "UNITPR": 10.0}


def test_an_increase_adds_to_the_total_and_reprices(apart):
    res = _write(apart, _entry("JU", [_line("ICE01", 5, unit_cost="20")]))
    assert res.ok, res.error
    assert _master(apart) == {"TOTBAL": 50.0, "TOTVAL": 550.0, "MREMBAL": 8.0, "MREMVAL": 160.0, "UNITPR": 11.0}


def test_the_unit_price_stays_when_the_total_is_not_positive(apart):
    _set(apart, "STMAS", "STKCOD", "ICE01", TOTBAL=1.0, TOTVAL=10.0, UNITPR=10.0)
    _set(apart, "STLOC", "STKCOD", "ICE01", LOCBAL=1.0)
    res = _write(apart, _entry("OU", [_line("ICE01", 3)]))
    assert res.ok, res.error
    master = _master(apart)
    assert (master["TOTBAL"], master["UNITPR"]) == (-2.0, 10.0)  # 原生：结存不为正时均价不动（实录 000004 −2 → −3）


def test_the_whole_book_negative_stock_check_reads_the_total(apart):
    isinfo(apart, MAINLOC="01", ISPERPETUA="Y", NEGALLOW="N", CHKNEGLEV="0")
    _set(apart, "STMAS", "STKCOD", "ICE01", TOTBAL=5.0, MREMBAL=0.0)
    key = ("ICE01", stock_guard.ALL_LOCATIONS)
    assert stock_guard.short_of(apart, {key: stock_guard.Decimal(3)}, False) == []  # 以前按批次链 0 拦成不够
    short = stock_guard.short_of(apart, {key: stock_guard.Decimal(6)}, False)
    assert [(row["need"], row["have"]) for row in short] == [("6", "5")]


def test_an_opening_after_negative_sales_adds_to_the_total(apart):
    _set(apart, "STMAS", "STKCOD", "ICE01", TOTBAL=-3.0, TOTVAL=0.0, MREMBAL=0.0, MREMVAL=0.0)
    out = stock_sale.set_opening_stock(apart, [{"name": "น้ำแข็งหลอด", "qty": 10, "unit_cost": 4}], dt.date(2026, 1, 1))
    assert [row["mode"] for row in out] == ["opening_set"]
    row = _rows(apart, "STMAS", STKCOD="ICE01")[0]
    assert (row["OPNBAL"], row["TOTBAL"], row["TOTVAL"], row["MREMBAL"]) == (10.0, 7.0, 40.0, 10.0)


def test_an_opening_is_skipped_when_the_total_is_positive_even_if_the_lot_chain_is_empty(apart):
    _set(apart, "STMAS", "STKCOD", "ICE01", MREMBAL=0.0, MREMVAL=0.0)
    out = stock_sale.set_opening_stock(apart, [{"name": "น้ำแข็งหลอด", "qty": 10, "unit_cost": 4}], dt.date(2026, 1, 1))
    assert [row["mode"] for row in out] == ["skip_has_stock"]
    assert _master(apart)["TOTBAL"] == 45.0
