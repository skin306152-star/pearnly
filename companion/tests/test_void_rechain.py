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
from test_stock_location import _buy, _item, _purchase, _rows, _stloc, acct  # noqa: F401

TK_LOT = "20261026:TK0000001     1"
STALE = "202208113RL002200034   1"


def _master(acct):
    row = _rows(acct, "STMAS", STKCOD="ICE01")[0]
    return {k: (row[k].rstrip() if isinstance(row[k], str) else row[k]) for k in ("MLOTNUM", "MREMBAL", "MREMVAL", "TOTBAL", "TOTVAL")}


def _patch_rows(acct, docnum, **fields):
    import dbf as _dbf
    table = _dbf.Table(str(acct / "STCRD.DBF"), codepage="cp874")
    table.open(_dbf.READ_WRITE)
    try:
        for record in table:
            if record["DOCNUM"].strip() == docnum:
                with record:
                    for key, value in fields.items():
                        record[key] = value
    finally:
        table.close()


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


def test_an_increase_on_a_master_chain_left_negative_on_an_earlier_day_records_it_as_stored_and_a_void_puts_it_back(book):
    """11-25 原生实录（70EXP\\test 物料 000004，平均成本、不分仓）：主档链 -2 / 0、批号 -/F（10-26 起，当天没有别的明细）→ 原生 JU +15
    明细记写前原值 -2 / 0、批号 -/F（带标记原样），主档 13、批号换成本单的批；原生作废 → 主档原样回 (-2, 0, -2, 0, -/F)。
    以前 CC 行上记 0 / B/F，作废后回成 0 / B/F、标记丢了（101 本回放里 8 本）。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="A", NEGALLOW="Y")
    _set_master(book, MLOTNUM="-/F", MREMBAL=-2.0, MREMVAL=0.0, TOTBAL=-2.0, TOTVAL=0.0, UNITPR=0.0)
    before = _master(book)
    res = _write(book, _entry("JU", [_line("ICE01", 15, unit_cost="0")]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["POSOPR"], line["MREMBAL"], line["MREMVAL"], line["MLOTNUM"].rstrip()) == ("1", -2.0, 0.0, "-/F")
    assert _master(book) == {"MLOTNUM": f"202603201{res.docnum:<12}  1", "MREMBAL": 13.0, "MREMVAL": 0.0, "TOTBAL": 13.0, "TOTVAL": 0.0}
    assert _void(book, res.docnum, "JU", [{"code": "ICE01", "qty": "15"}]).ok
    assert _master(book) == before


def test_a_purchase_on_a_master_chain_left_negative_keeps_recording_zero_and_the_plain_lot(acct):
    """进货 RR 行没有跨日负链的原生实录：照旧记 0、批号去标记（11-25 只按调增 JU 的实录改）。"""
    isinfo(acct, MAINLOC="01", ISPERPETUA="Y", COSMTD="A", NEGALLOW="Y")
    _set_master(acct, MLOTNUM="-/F", MREMBAL=-2.0, MREMVAL=0.0, TOTBAL=-2.0, TOTVAL=0.0)
    res = _buy(acct, _purchase([_item("ICE01", 5, 100.0, "stock_item")]))
    assert res.ok, res.error
    line = [r for r in _rows(acct, "STCRD", STKCOD="ICE01") if r["POSOPR"] == "0"][-1]
    assert (line["MREMBAL"], line["MLOTNUM"].rstrip()) == (0.0, "B/F")


def test_voiding_a_count_loss_keeps_the_master_lot_since_count_rows_record_none(book):
    """盘点的减少行不记批号（行上 MLOTNUM 空）：作废时主档批号留着现在的，不回成空（审查 11-25：以前按行回写把主档批号清空）。"""
    for head in (TK_LOT, "-/F"):
        isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="A", NEGALLOW="Y")
        _set_master(book, MLOTNUM=head, MREMBAL=5.0, MREMVAL=50.0, TOTBAL=5.0, TOTVAL=50.0, UNITPR=10.0)
        before = _master(book)
        res = _write(book, _entry("TK", [_line("ICE01", -2)]))
        assert res.ok, res.error
        assert _rows(book, "STCRD", DOCNUM=res.docnum)[0]["MLOTNUM"].strip() == ""
        assert _void(book, res.docnum, "TK", [{"code": "ICE01", "qty": "-2"}]).ok
        assert _master(book) == before


def test_a_count_gain_records_the_lot_before_writing_and_its_void_puts_it_back(book):
    """11-28 70EXP\\test 原生实录 TK0000002（501780 盘盈 +5）：行上 MLOTNUM 记写前主档那一批（20261026:TK0000001），挂靠的进货批只记
    RDOCNUM（RR0000014）；原生作废主档五栏原样回写前，批号就是行上记的那一批，不是挂靠的进货批。"""
    import datetime as dt
    from test_dbf_stock import _receipt
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="A", NEGALLOW="Y")
    _receipt(book, "RR0000009", dt.date(2026, 3, 1), 10, 400)
    first = _write(book, _entry("JU", [_line("ICE01", 1, unit_cost="40")], docdate_be="690310"))
    assert first.ok, first.error
    ju_lot = f"202603101{first.docnum:<12}  1"
    before = _master(book)
    assert before["MLOTNUM"] == ju_lot
    res = _write(book, _entry("TK", [_line("ICE01", 2)]))
    assert res.ok, res.error
    row = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (row["MLOTNUM"].strip(), row["RDOCNUM"].strip()) == (ju_lot.strip(), "RR0000009     1".strip())
    assert _void(book, res.docnum, "TK", [{"code": "ICE01", "qty": "2"}]).ok
    assert _master(book) == before


def test_voiding_an_older_cc_count_gain_that_recorded_the_linked_lot_finds_the_lot_before_it(book):
    """11-28 前 CC 写的盘盈行记的是挂靠的进货批（= RDOCNUM）：那种行作废时仍按库存卡写入顺序找本单之前的链头，不回到那张进货的批。"""
    import datetime as dt
    from test_dbf_stock import _receipt
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="A", NEGALLOW="Y")
    _receipt(book, "RR0000009", dt.date(2026, 3, 1), 10, 400)
    first = _write(book, _entry("JU", [_line("ICE01", 1, unit_cost="40")], docdate_be="690310"))
    assert first.ok, first.error
    before = _master(book)
    res = _write(book, _entry("TK", [_line("ICE01", 2)]))
    assert res.ok, res.error
    _patch_rows(book, res.docnum, MLOTNUM="202603010RR0000009     1")  # 旧版写法
    assert _void(book, res.docnum, "TK", [{"code": "ICE01", "qty": "2"}]).ok
    assert _master(book) == before


def test_voiding_a_count_in_a_book_costed_by_warehouse_leaves_the_warehouse_chain_alone(book):
    """盘点的行写入时不动仓链（只动仓结存与主档）：作废也不回仓链、不动 LOCVAL，主档按差额回写前（101 本回放 T7HENG15：以前倒扣成 -84）。"""
    acct = _by_place(book)
    first = _write(acct, _entry("JU", [_line("ICE01", 2, unit_cost="15")]))
    assert first.ok, first.error
    place_before, master_before = _place(acct), _master(acct)
    for qty in ("-1", "1"):
        res = _write(acct, _entry("TK", [_line("ICE01", int(qty), unit_cost="99")]))
        assert res.ok, res.error
        assert _void(acct, res.docnum, "TK", [{"code": "ICE01", "qty": qty}]).ok
        assert _place(acct) == place_before
        assert _master(acct) == master_before


def test_the_head_before_a_document_follows_write_order_and_skips_count_rows_on_a_warehouse_chain():
    """``_previous_lot``：按记录号往前找；进货类正数行是它自己的新批，其余记了批号的行是它记的批号，没记的跳过；找仓链时跳过盘点行。"""
    import datetime as dt
    from decimal import Decimal as Dc
    from companion import dbf_void
    day = dt.date(2026, 3, 1)
    rr = ((day, ("0", "RR0000009", 1)), "0", "RR0000009", "1", day, Dc(10), 1, "")
    later_by_date = ((dt.date(2026, 4, 1), ("0", "RR0000010", 1)), "0", "RR0000010", "1", dt.date(2026, 4, 1), Dc(5), 2, "")
    issue = ((day, ("6", "OU0000001", 1)), "6", "OU0000001", "1", day, Dc(-1), 3, "202603010RR0000009     1")
    loss = ((day, (":", "TK0000001", 1)), ":", "TK0000001", "1", day, Dc(-1), 4, "")
    gain = ((day, (":", "TK0000002", 1)), ":", "TK0000002", "1", day, Dc(1), 5, "")
    rows = [rr, later_by_date, issue, loss, gain]
    assert dbf_void._previous_lot(rows, 4) == "202603010RR0000009     1"  # 出库记的批号，不是日期更晚的 RR0000010
    assert dbf_void._previous_lot(rows, 6) == "20260301:TK0000002     1"  # 主档链：盘盈成新批
    assert dbf_void._previous_lot(rows, 6, place=True) == "202603010RR0000009     1"  # 仓链：盘点行跳过
    assert dbf_void._previous_lot(rows, 1) == ""


def test_voiding_a_count_gain_with_no_earlier_rows_goes_back_to_the_purchase_lot_it_links(book):
    """库存卡上没有更早的行（清过卡的账套，101 本回放 T 副本 39 本）：盘盈作废退回行上挂靠的进货批，不回 B/F。"""
    import datetime as dt
    from test_dbf_stock import _receipt
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="A", NEGALLOW="Y")
    _receipt(book, "RR0000009", dt.date(2026, 3, 21), 10, 400)  # 在盘盈单之后写入的行不算「之前」
    lot = "202603210RR0000009     1"
    _set_master(book, MLOTNUM=lot, MREMBAL=0.0, MREMVAL=0.0, TOTBAL=0.0, TOTVAL=0.0)
    before = _master(book)
    res = _write(book, _entry("TK", [_line("ICE01", 2)], docdate_be="690322"))
    assert res.ok, res.error
    row = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert row["MLOTNUM"].strip() == lot
    import dbf
    from companion import dbf_schema
    t = dbf.Table(str(book / "STCRD.DBF"), codepage=dbf_schema.CODEPAGE); t.open(dbf.READ_WRITE)
    try:  # 模拟清过卡：把那张进货行删掉，只留主档上的批号
        for rec in dbf_schema.live_rows(t):
            if (rec.DOCNUM or "").strip() == "RR0000009":
                dbf.delete(rec)
    finally:
        t.close()
    assert _void(book, res.docnum, "TK", [{"code": "ICE01", "qty": "2"}]).ok
    assert _master(book)["MLOTNUM"] == lot


def test_a_count_gain_on_a_master_chain_like_70exp_test_matches_the_native_record(book):
    """照 11-28 原生 TK0000002：主档 25 / 1,200、批号 20261026:TK0000001；盘盈 +5 挂进货批 → 行记写前批号与写前结存，主档换成本单的批；
    作废后主档五栏回到写前。"""
    import datetime as dt
    from test_dbf_stock import _receipt
    acct = _average(book)
    PRE_LOT = "20261026:TK0000009     1"  # 写前那一批（原生是 TK0000001；夹具这张新单也编成 TK0000001，换个号免得撞号）
    _receipt(acct, "RR0000014", dt.date(2026, 10, 26), 1, 60)
    _set_master(acct, MLOTNUM=PRE_LOT, MREMBAL=25.0, MREMVAL=1200.0, TOTBAL=25.0, TOTVAL=1200.0, UNITPR=48.0)
    before = _master(acct)
    res = _write(acct, _entry("TK", [_line("ICE01", 5)], docdate_be="691128"))
    assert res.ok, res.error
    row = _rows(acct, "STCRD", DOCNUM=res.docnum)[0]
    assert (row["MLOTNUM"].rstrip(), row["MREMBAL"], row["MREMVAL"], row["RDOCNUM"].rstrip()) == (PRE_LOT, 25.0, 1200.0, "RR0000014     1")
    assert _master(acct)["MLOTNUM"] == f"20261128:{res.docnum:<12}  1"
    assert _void(acct, res.docnum, "TK", [{"code": "ICE01", "qty": "5"}]).ok
    assert _master(acct) == before
