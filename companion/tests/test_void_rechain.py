# -*- coding: utf-8 -*-
"""作废库存单时批次链回到写前（``dbf_void._rechain``；11-24 原生作废实录，库存 5.1 收口）。

两次原生实录（测试机1，服务器测试账套，写前整本快照、写后整本还原）逐字段做成期望：
- 70EXP\\test（平均成本、不分仓）：501780 主档链 25 / 1,200、批号 TK0000001；原生录 JU 再原生作废 → 主档链、批号、总结存、单价全回写前。
- 69EXP\\NLTTEST（先进先出 + 分仓计价）：40003046 的 01 仓空链（结存 0、无批号）、主档抄的是陈旧的 02 仓链；原生录 JU +2 @15 →
  明细行记写前 0 / 0、批号 B/F，01 仓链 2 / 30、LOCVAL 30，主档抄 01 仓链；原生作废 → 01 仓结存、LOCVAL、链回 0、批号 B/F，主档重抄 01 仓链（0 / 0 / B/F），总结存回写前。
以前 CC 作废只把结存加回，批号、仓链、LOCVAL 都停在被作废的单上（作废探针 150 次里 148 次）。
"""

from __future__ import annotations

from account_rules_fixture import isinfo
from test_dbf_stock import _chain, _entry, _line, _set_master, _write, book  # noqa: F401
from test_dbf_void import _void
from test_stock_location import _rows, _stloc, acct  # noqa: F401

TK_LOT = "20261026:TK0000001     1"
STALE = "202208113RL002200034   1"


def _master(acct):
    row = _rows(acct, "STMAS", STKCOD="ICE01")[0]
    return {k: (row[k].rstrip() if isinstance(row[k], str) else row[k]) for k in ("MLOTNUM", "MREMBAL", "MREMVAL", "TOTBAL", "TOTVAL")}


def _place(acct, loc="01"):
    row = _stloc(acct, "ICE01", loc)
    return (row["LOCBAL"], round(row["LOCVAL"], 2), row["MREMBAL"], round(row["MREMVAL"], 2), row["MLOTNUM"].rstrip())


def _average(book):
    """70EXP\\test 那样：平均成本、不分仓，主档链 25 / 1,200、批号指盘点 TK 那一批。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="A", NEGALLOW="Y")
    _set_master(book, MLOTNUM=TK_LOT, MREMBAL=25.0, MREMVAL=1200.0, TOTBAL=25.0, TOTVAL=1200.0, UNITPR=48.0)
    return book


def _by_place(book):
    """69EXP\\NLTTEST 那样：先进先出 + 分仓计价，01 仓空链，主档抄着别的仓的陈旧链。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F", LOC_COST="Y", NEGALLOW="Y")
    _set_master(book, MLOTNUM=STALE, MREMBAL=6.0, MREMVAL=49.04, TOTBAL=5.0, TOTVAL=50.0)
    _chain(book, "ICE01", "01", LOCBAL=0.0, LOCVAL=0.0, MREMBAL=0.0, MREMVAL=0.0, MLOTNUM="")
    return book


def test_voiding_the_last_adjustment_in_an_average_cost_book_puts_the_master_chain_back_like_native(book):
    acct = _average(book)
    before = _master(acct)
    res = _write(acct, _entry("JU", [_line("ICE01", 2, unit_cost="15")]))
    assert res.ok, res.error
    line = _rows(acct, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["MREMBAL"], line["MREMVAL"], line["MLOTNUM"].rstrip()) == (25.0, 1200.0, TK_LOT)  # 原生：行上记写前链
    assert _master(acct)["MLOTNUM"] != TK_LOT
    assert _void(acct, res.docnum, "JU", [{"code": "ICE01", "qty": "2"}]).ok
    assert _master(acct) == before  # 原生作废：批号、链、总结存全回写前（以前批号停在被作废的 JU 上）


def test_voiding_the_last_adjustment_in_a_book_costed_by_warehouse_puts_the_warehouse_chain_back_like_native(book):
    acct = _by_place(book)
    res = _write(acct, _entry("JU", [_line("ICE01", 2, unit_cost="15")]))
    assert res.ok, res.error
    line = _rows(acct, "STCRD", DOCNUM=res.docnum)[0]
    # 原生增加行：空链记写前 0 / 0、批号 B/F
    assert (line["MREMBAL"], line["MREMVAL"], line["MLOTNUM"].rstrip()) == (0.0, 0.0, "B/F")
    lot = f"202603201{res.docnum:<12}  1"
    assert _place(acct) == (2.0, 30.0, 2.0, 30.0, lot)
    assert _master(acct) == {"MLOTNUM": lot, "MREMBAL": 2.0, "MREMVAL": 30.0, "TOTBAL": 7.0, "TOTVAL": 80.0}
    assert _void(acct, res.docnum, "JU", [{"code": "ICE01", "qty": "2"}]).ok
    # 原生作废：仓结存、LOCVAL、仓链回 0、批号 B/F；主档重抄这个仓的链，总结存回写前（不回那条陈旧的主档链）
    assert _place(acct) == (0.0, 0.0, 0.0, 0.0, "B/F")
    assert _master(acct) == {"MLOTNUM": "B/F", "MREMBAL": 0.0, "MREMVAL": 0.0, "TOTBAL": 5.0, "TOTVAL": 50.0}


def test_a_document_written_after_a_void_continues_the_restored_chain(book):
    acct = _by_place(book)
    first = _write(acct, _entry("JU", [_line("ICE01", 2, unit_cost="15")]))
    assert _void(acct, first.docnum, "JU", [{"code": "ICE01", "qty": "2"}]).ok
    again = _write(acct, _entry("JU", [_line("ICE01", 1, unit_cost="20")], docdate_be="690321"))
    assert again.ok, again.error
    line = _rows(acct, "STCRD", DOCNUM=again.docnum)[0]
    assert (line["MREMBAL"], line["MREMVAL"], line["MLOTNUM"].rstrip()) == (0.0, 0.0, "B/F")
    lot = f"202603211{again.docnum:<12}  1"
    assert _place(acct) == (1.0, 20.0, 1.0, 20.0, lot)
    assert _master(acct)["MLOTNUM"] == lot


def test_voiding_an_adjustment_that_is_not_the_last_on_the_chain_takes_only_its_own_part_out(book):
    """链上后面还有别的单：不改别的单的明细，链按差额回；批号指着后面那张单的批，留着（原生作废非链尾的单没有实录）。"""
    acct = _by_place(book)
    first = _write(acct, _entry("JU", [_line("ICE01", 2, unit_cost="15")]))
    second = _write(acct, _entry("JU", [_line("ICE01", 1, unit_cost="20")], docdate_be="690321"))
    assert first.ok and second.ok
    lot = f"202603211{second.docnum:<12}  1"
    assert _void(acct, first.docnum, "JU", [{"code": "ICE01", "qty": "2"}]).ok
    assert _place(acct) == (1.0, 20.0, 1.0, 20.0, lot)
    assert _master(acct)["MLOTNUM"] == lot
    assert _rows(acct, "STCRD", DOCNUM=second.docnum)[0]["MREMBAL"] == 2.0  # 后面那张单的明细一字不动


def test_a_row_that_does_not_lead_to_the_current_chain_falls_back_to_taking_the_difference_out(book):
    """老版本 CC 写的行记的是写后值（或增加行写前为负记了 0）：按行回会错，退回按差额回；批号指着本单的批就回行上记的批号。"""
    acct = _average(book)
    res = _write(acct, _entry("JU", [_line("ICE01", 2, unit_cost="15")]))
    import dbf
    from companion import dbf_schema
    table = dbf.Table(str(acct / "STCRD.DBF"), codepage=dbf_schema.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        for rec in dbf_schema.live_rows(table):
            if (rec.DOCNUM or "").strip() == res.docnum:
                with rec as row:
                    row["MREMBAL"], row["MREMVAL"] = 27.0, 1230.0  # 写后值
    finally:
        table.close()
    assert _void(acct, res.docnum, "JU", [{"code": "ICE01", "qty": "2"}]).ok
    assert _master(acct) == {"MLOTNUM": TK_LOT, "MREMBAL": 25.0, "MREMVAL": 1200.0, "TOTBAL": 25.0, "TOTVAL": 1200.0}



def _add_place(acct, code, loc, **fields):
    """STLOC 加一行（老账套那样：有结存、没批号 → 这个仓不记链，链在主档上）。"""
    import dbf
    from companion import dbf_schema
    table = dbf.Table(str(acct / "STLOC.DBF"), codepage=dbf_schema.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        names = {name.upper() for name in table.field_names}
        table.append({k: v for k, v in {"STKCOD": code, "LOCCOD": loc, **fields}.items() if k in names})
    finally:
        table.close()


def test_a_document_on_a_warehouse_with_its_chain_and_one_without_leaves_the_master_on_the_warehouse_chain(book):
    """同一件存货 01 仓记着链、02 仓没记（老账套部分存货，链在主档上）：主档链只回 02 那行，最后照写入时一样抄 01 仓回写前的链，
    不把 01 仓那行重复算进主档的差额（审查 11-25）。"""
    acct = _by_place(book)
    _add_place(acct, "ICE01", "02", LOCBAL=5.0, LOCVAL=50.0)
    res = _write(acct, _entry("JU", [_line("ICE01", 2, unit_cost="15"), _line("ICE01", 3, loccod="02", unit_cost="10")]))
    assert res.ok, res.error
    assert _void(acct, res.docnum, "JU", [{"code": "ICE01", "qty": "2"}, {"code": "ICE01", "qty": "3"}]).ok
    assert _place(acct) == (0.0, 0.0, 0.0, 0.0, "B/F")
    assert _master(acct) == {"MLOTNUM": "B/F", "MREMBAL": 0.0, "MREMVAL": 0.0, "TOTBAL": 5.0, "TOTVAL": 50.0}
    assert _stloc(acct, "ICE01", "02")["LOCBAL"] == 5.0
