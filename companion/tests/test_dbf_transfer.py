# -*- coding: utf-8 -*-
"""仓间调拨写进 Express（原生 RL，dbf_transfer；ERP 多仓闭环 P2）与作废 RL（dbf_void）。

照服务器测试账套原生录的 RL0000001–0000003（副本 s12-*，10-30 逐栏核过）：单头 POSOPR 4、LOCCOD 调出、PEOPLE 调入、无金额；
每件一对明细（调出 4 / 调入 3、同行号、互指对方仓、单价金额空、STKDES 单头说明）；STLOC 两仓增减、调入仓没有行就新建、
不动日期与金额栏；STMAS 不动；不写凭证；ISRUN RL +1。作废：单头 C、NXTSEQ 空，那一对打删除标记，两仓回滚。
"""

from __future__ import annotations

import datetime as dt

import dbf
import pytest

from account_rules_fixture import seed
from companion import dbf_schema as S
from companion import dbf_transfer, dbf_void, payload_contract, stock_guard
from test_dbf_stock import book  # noqa: F401
from test_stock_location import _rows, _state, _stloc, acct  # noqa: F401

TODAY = dt.date(2026, 10, 30)


@pytest.fixture
def rl_book(book):
    seed(book, "ISRUN", [{"DOCTYP": "RL", "PREFIX": "RL", "DOCNUM": "0000001", "JNLTYP": ""}])
    return book


def _transfer(items, **extra):
    return {"payload_version": 14, "direction": "stock", "doctype": "RL", "docdate_be": "691030", "ref_no": "TF2569-000001",
            "loccod": "01", "to_loccod": "02", "items": items, **extra}


def _line(code, qty, **extra):
    return {"code": code, "name": code, "qty": str(qty), "unit": "ถัง", "loccod": "01", **extra}


def _write(acct, payload, **kw):
    return dbf_transfer.write_transfer(payload, acct, selected_dir=str(acct), make_backup=kw.pop("make_backup", False),
                                       today=TODAY, **kw)


def _deleted(acct, name, docnum):
    table = dbf.Table(str(acct / f"{name}.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        return [{field: row[field] for field in table.field_names} for row in table
                if dbf.is_deleted(row) and str(row["DOCNUM"]).strip() == docnum]
    finally:
        table.close()


def test_a_transfer_writes_a_native_rl_header_and_one_pair_of_lines_per_item(rl_book):
    master_before = _rows(rl_book, "STMAS", STKCOD="ICE01")[0]
    res = _write(rl_book, _transfer([_line("ICE01", 2)], remark="ย้ายไปสาขา"))
    assert res.ok, res.error
    assert res.docnum == "RL0000001"  # Express 原生流水号（用户 11-18 定 D5）
    head = _rows(rl_book, "STTRN", DOCNUM=res.docnum)
    assert len(head) == 1
    head = head[0]
    assert (head["POSOPR"], head["LOCCOD"].strip(), head["PEOPLE"].strip()) == ("4", "01", "02")
    assert head["TRNVAL"] == 0.0 and head["NXTSEQ"] == "  1" and head["DOCSTAT"] == "N"
    assert head["REMARK"].strip() == "ย้ายไปสาขา" and head["ACCNUMDR"].strip() == head["ACCNUMCR"].strip() == ""
    rows = sorted(_rows(rl_book, "STCRD", DOCNUM=res.docnum), key=lambda row: row["POSOPR"], reverse=True)
    assert [(r["POSOPR"], r["LOCCOD"].strip(), r["PEOPLE"].strip(), r["SEQNUM"]) for r in rows] == [
        ("4", "01", "02", "  1"), ("3", "02", "01", "  1")]
    for row in rows:
        assert (row["TRNQTY"], row["XTRNQTY"], row["TFACTOR"]) == (2.0, 2.0, 1.0)
        assert row["UNITPR"] == row["TRNVAL"] == row["XUNITPR"] == row["XTRNVAL"] == 0.0
        assert row["STKDES"].strip() == "ย้ายไปสาขา" and row["REIMBURSE"] == "\x00"
        assert (row["MREMBAL"], row["MREMVAL"]) == (5.0, 50.0)  # 全仓结存不变
        assert row["TQUCOD"] == "ถง"
    assert _rows(rl_book, "STMAS", STKCOD="ICE01")[0] == master_before
    assert _stloc(rl_book, "ICE01", "01")["LOCBAL"] == 3.0
    assert _stloc(rl_book, "ICE01", "01")["LMOVDAT"] == dt.date(2025, 12, 1)  # 原生不动日期栏
    target = _stloc(rl_book, "ICE01", "02")  # 调入仓原来没有这件：新建一行，只有数量
    assert target["LOCBAL"] == 2.0 and target["STATUS"] == "A" and target["STKCLASS"].strip() == ""
    assert target["LMOVDAT"] is None and all(not target[f"VAL{m}NY"] for m in range(1, 13))
    assert _rows(rl_book, "GLJNL", VOUCHER=res.docnum) == []
    assert _rows(rl_book, "ISRUN", PREFIX="RL")[0]["DOCNUM"].strip() == "0000002"


def test_without_a_remark_the_line_description_is_the_product_name(rl_book):
    res = _write(rl_book, _transfer([{**_line("ICE01", 1), "name": "น้ำแข็งหลอด"}]))
    assert res.ok, res.error
    assert {row["STKDES"].strip() for row in _rows(rl_book, "STCRD", DOCNUM=res.docnum)} == {"น้ำแข็งหลอด"}


def test_voiding_a_transfer_marks_it_cancelled_and_rolls_both_locations_back(rl_book):
    before = _stloc(rl_book, "ICE01", "01")
    res = _write(rl_book, _transfer([_line("ICE01", 2)]))
    assert res.ok, res.error
    payload = {"payload_version": 14, "direction": "stock", "action": "void", "doctype": "RL", "docnum": res.docnum,
               "items": [{"seq": 1, "code": "ICE01", "loccod": "01", "qty": "2"}]}
    out = dbf_void.void_stock_entry(payload, rl_book, selected_dir=str(rl_book), make_backup=False)
    assert out.ok, out.error
    head = _rows(rl_book, "STTRN", DOCNUM=res.docnum)[0]
    assert (head["DOCSTAT"], head["NXTSEQ"].strip()) == ("C", "")
    assert _rows(rl_book, "STCRD", DOCNUM=res.docnum) == []
    gone = _deleted(rl_book, "STCRD", res.docnum)
    assert len(gone) == 2 and all(row["NETVAL"] == 0.0 and row["SEQNUM"] == "  1" for row in gone)  # 字段不改
    assert _stloc(rl_book, "ICE01", "01")["LOCBAL"] == before["LOCBAL"]
    assert _stloc(rl_book, "ICE01", "01")["LMOVDAT"] == before["LMOVDAT"]
    assert _stloc(rl_book, "ICE01", "02")["LOCBAL"] == 0.0
    assert _rows(rl_book, "STMAS", STKCOD="ICE01")[0]["MREMBAL"] == 5.0
    assert _rows(rl_book, "ISRUN", PREFIX="RL")[0]["DOCNUM"].strip() == "0000002"  # 不回退


def test_a_transfer_changed_in_express_is_not_voided(rl_book):
    res = _write(rl_book, _transfer([_line("ICE01", 2)]))
    payload = {"payload_version": 14, "direction": "stock", "action": "void", "doctype": "RL", "docnum": res.docnum,
               "items": [{"seq": 1, "code": "ICE01", "loccod": "02", "qty": "2"}]}
    out = dbf_void.void_stock_entry(payload, rl_book, selected_dir=str(rl_book), make_backup=False)
    assert not out.ok and out.error_code == dbf_void.ERR_CHANGED


@pytest.mark.parametrize("change, reason", [
    ({"to_loccod": "01"}, "same location"),
    ({"to_loccod": ""}, "needs both"),
    ({"items": [_line("ICE01", 0)]}, "not valid for RL"),
    ({"items": [_line("ICE01", 1, loccod="02")]}, "differs from the header"),
])
def test_bad_transfer_payloads_are_refused_before_touching_the_book(rl_book, change, reason):
    before = _state(rl_book)
    res = _write(rl_book, {**_transfer([_line("ICE01", 1)]), **change})
    assert not res.ok and res.error_code == "PAYLOAD_INVALID" and reason in res.error
    assert _state(rl_book) == before


def test_an_unknown_location_writes_nothing(rl_book):
    before = _state(rl_book)
    res = _write(rl_book, _transfer([_line("ICE01", 1)], to_loccod="09"))
    assert not res.ok and res.error_code == "ERR_LOCATION"
    assert _state(rl_book) == before


def test_a_book_without_an_rl_counter_is_reported(book):
    res = _write(book, _transfer([_line("ICE01", 1)]))
    assert not res.ok and res.error_code == "JOURNAL_BOOK_NOT_FOUND"


def test_an_unknown_item_stops_the_whole_transfer(rl_book):
    res = _write(rl_book, _transfer([_line("ICE01", 1), _line("NOPE", 1)]))
    assert not res.ok and res.error_code == "STOCK_ITEM_NOT_FOUND"
    assert _rows(rl_book, "STTRN", POSOPR="4") == []


def test_an_item_marked_new_is_created_before_the_transfer(rl_book):
    """多仓闭环 P3x：Express 里没有、人选了新建的行（``new_ok``，载荷 15）：主档也进写前备份，建 0 余额存货后照常调；逐行回报建出的码。
    写失败时新档一起回滚。"""
    item = {"code": "", "name": "ถ่านอัดแท่ง", "qty": "2", "unit": "ถัง", "loccod": "01", "new_ok": True}
    before = _state(rl_book)
    from unittest import mock
    with mock.patch.object(dbf_transfer, "_verify", side_effect=dbf_transfer._WriteError("boom", "READBACK_VERIFY_FAILED")):
        res = _write(rl_book, _transfer([item], payload_version=15), make_backup=True)
    assert not res.ok and _state(rl_book) == before
    res = _write(rl_book, _transfer([item], payload_version=15))
    assert res.ok, res.error
    made = [row for row in _rows(rl_book, "STMAS") if row["STKDES"].strip() == "ถ่านอัดแท่ง"]
    assert len(made) == 1 and made[0]["MREMBAL"] == 0.0  # 调拨不动全仓结存
    code = made[0]["STKCOD"].strip()
    assert [(row["seq"], row["stkcod"]) for row in res.line_modes] == [(1, code)]
    assert _stloc(rl_book, code, "02")["LOCBAL"] == 2.0


def test_a_new_item_is_not_transferred_out_of_a_book_that_forbids_negative_stock(rl_book):
    from account_rules_fixture import isinfo
    isinfo(rl_book, MAINLOC="01", ISPERPETUA="Y", NEGALLOW="N")
    before = _state(rl_book)
    res = _write(rl_book, _transfer([{"code": "", "name": "ถ่านอัดแท่ง", "qty": "2", "unit": "ถัง", "loccod": "01", "new_ok": True}],
                                    payload_version=15))
    assert not res.ok and res.error_code == stock_guard.ERR_STOCK_INSUFFICIENT and _state(rl_book) == before


def test_negative_stock_is_refused_at_the_source_when_the_book_forbids_it(rl_book, monkeypatch):
    monkeypatch.setattr(stock_guard, "short_of", lambda *_a, **_k: [{"stkcod": "ICE01", "loccod": "01", "need": 9, "have": 5}])
    res = _write(rl_book, _transfer([_line("ICE01", 9)]))
    assert not res.ok and res.error_code == stock_guard.ERR_STOCK_INSUFFICIENT


def test_this_companion_takes_the_transfer_payload_version():
    assert payload_contract.check_payload_version({"payload_version": 14}) is None


def test_a_transfer_in_a_book_costed_by_warehouse_carries_the_value_and_makes_a_lot_in(rl_book):
    """分仓计价的账套（ISINFO.LOC_COST = Y）原生调拨带金额：两行按出库成本计价（单头货值 = 合计），调入那一行成为调入仓的新一批
    （11-18 先进先出、分仓计价测试账套实录）。不分仓计价的账套照旧不带金额（上一条）。"""
    from account_rules_fixture import isinfo
    isinfo(rl_book, MAINLOC="01", ISPERPETUA="Y", LOC_COST="Y")
    res = _write(rl_book, _transfer([_line("ICE01", 2)]))
    assert res.ok, res.error
    assert _rows(rl_book, "STTRN", DOCNUM=res.docnum)[0]["TRNVAL"] == 20.0
    rows = {r["POSOPR"]: r for r in _rows(rl_book, "STCRD", DOCNUM=res.docnum)}
    out, into = rows["4"], rows["3"]
    assert (out["XUNITPR"], out["XTRNVAL"], out["NETVAL"], out["LOTBAL"]) == (10.0, 20.0, 0.0, 0.0)
    assert (into["XTRNVAL"], into["NETVAL"], into["LOTBAL"], into["LOTVAL"], into["MREMBAL"]) == (20.0, 20.0, 2.0, 20.0, 2.0)
    assert into["MLOTNUM"].rstrip() == f"202610303{res.docnum:<12}  1"


def test_a_transfer_in_a_book_costed_by_warehouse_keeps_both_lot_chains_in_stloc(rl_book):
    """11-19 实录：分仓计价账套的批次链按仓记在 STLOC。调出行记调出仓链的写前结存与指针；写后调出仓链减、调入仓链加且指向调入那一批，
    两个仓的 LOCVAL 随货值增减，主档的批次栏抄调入仓的链（总结存不变）。"""
    from account_rules_fixture import isinfo
    from test_dbf_stock import _chain
    from test_stock_location import _stloc
    isinfo(rl_book, MAINLOC="01", ISPERPETUA="Y", LOC_COST="Y")
    head = "202509010RR0000001     1"
    _chain(rl_book, "ICE01", "01", MLOTNUM=head, MREMBAL=5.0, MREMVAL=50.0, LOCVAL=50.0)
    res = _write(rl_book, _transfer([_line("ICE01", 2)]))
    assert res.ok, res.error
    out = next(r for r in _rows(rl_book, "STCRD", DOCNUM=res.docnum) if r["POSOPR"] == "4")
    assert (out["MLOTNUM"].strip(), out["MREMBAL"], out["MREMVAL"]) == (head, 5.0, 50.0)
    lot = f"202610303{res.docnum:<12}  1"
    source, target = _stloc(rl_book, "ICE01", "01"), _stloc(rl_book, "ICE01", "02")
    assert (source["MLOTNUM"].strip(), source["MREMBAL"], source["MREMVAL"], source["LOCVAL"]) == (head, 3.0, 30.0, 30.0)
    assert (target["MLOTNUM"].strip(), target["MREMBAL"], target["MREMVAL"], target["LOCVAL"]) == (lot, 2.0, 20.0, 20.0)
    master = _rows(rl_book, "STMAS", STKCOD="ICE01")[0]
    assert (master["TOTBAL"], master["MLOTNUM"].strip(), master["MREMBAL"], master["MREMVAL"]) == (5.0, lot, 2.0, 20.0)


def test_same_day_documents_written_after_an_over_issue_start_from_the_opening_state_as_native_recalculates(rl_book):
    """11-20 69EXP 先进先出 + 分仓计价 + 允许负库存逐张实录（P0–P5）：原生每存一张单把这件存货在这个仓当天的明细按动作类别重排、
    从排最前那一行记的写前值起整段重算。出库 12 个（开日 10 个 @712.8，出超 2）之后同一天再写调拨、调增：调拨（4）与调增（1）都排在
    出库（6）前面，要记开日的状态（10 / 7,128、指开日那一批）；调拨按开日的批取价 712.8（不是出库后链上的 0）。
    这样原生下一次存单或「计算」就从开日的状态起算，与全程原生一致（出库 8,515.2、调拨 712.8）。"""
    from account_rules_fixture import isinfo
    from test_dbf_stock import _chain, _entry, _line as stock_line, _receipt, _write as write_stock
    isinfo(rl_book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F", LOC_COST="Y", NEGALLOW="Y")
    _receipt(rl_book, "EC0000051", dt.date(2016, 6, 29), 10, 7128)
    head = "201606292EC0000051     1"
    _chain(rl_book, "ICE01", "01", MLOTNUM=head, MREMBAL=10.0, MREMVAL=7128.0, LOCVAL=7128.0, LOCBAL=10.0)
    issue = write_stock(rl_book, _entry("OU", [stock_line("ICE01", 12, loccod="01")], docdate_be="691030"))
    assert issue.ok, issue.error
    line = _rows(rl_book, "STCRD", DOCNUM=issue.docnum)[0]
    assert (line["XTRNVAL"], line["MREMBAL"], line["MREMVAL"], line["MLOTNUM"].strip()) == (7128.0, 10.0, 7128.0, head)

    res = _write(rl_book, _transfer([_line("ICE01", 1)]))
    assert res.ok, res.error
    out = next(r for r in _rows(rl_book, "STCRD", DOCNUM=res.docnum) if r["POSOPR"] == "4")
    assert (out["XTRNVAL"], out["MREMBAL"], out["MREMVAL"], out["MLOTNUM"].strip()) == (712.8, 10.0, 7128.0, head)

    gain = write_stock(rl_book, _entry("JU", [stock_line("ICE01", 3, loccod="01", unit_cost="700")], docdate_be="691030"))
    assert gain.ok, gain.error
    row = _rows(rl_book, "STCRD", DOCNUM=gain.docnum)[0]
    assert (row["POSOPR"], row["XTRNVAL"], row["MREMBAL"], row["MREMVAL"], row["MLOTNUM"].strip()) == ("1", 2100.0, 10.0, 7128.0, head)


def test_a_document_sorting_after_the_days_rows_keeps_the_current_chain(rl_book):
    """当天已有调拨（4），再写出库（6）排在它后面：照旧记当前链的写前值、按当前的批取价（这本来就是原生排序后的位置）。"""
    from account_rules_fixture import isinfo
    from test_dbf_stock import _chain, _entry, _line as stock_line, _receipt, _write as write_stock
    isinfo(rl_book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F", LOC_COST="Y", NEGALLOW="Y")
    _receipt(rl_book, "EC0000051", dt.date(2016, 6, 29), 10, 7128)
    head = "201606292EC0000051     1"
    _chain(rl_book, "ICE01", "01", MLOTNUM=head, MREMBAL=10.0, MREMVAL=7128.0, LOCVAL=7128.0, LOCBAL=10.0)
    assert _write(rl_book, _transfer([_line("ICE01", 1)])).ok
    issue = write_stock(rl_book, _entry("OU", [stock_line("ICE01", 2, loccod="01")], docdate_be="691030"))
    assert issue.ok, issue.error
    line = _rows(rl_book, "STCRD", DOCNUM=issue.docnum)[0]
    assert (line["XTRNVAL"], line["MREMBAL"], line["MREMVAL"]) == (1425.6, 9.0, 6415.2)
