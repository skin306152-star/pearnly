# -*- coding: utf-8 -*-
"""作废 ERP 写进 Express 的库存单（载荷 7，dbf_void；出入库第一期 B 段 ①）。

照 Express 原生作废（服务器测试账套实录 OU0000001）：单头留着标 C、货值清 0；明细打删除标记、NETVAL 记货值；凭证头标 C、分录金额改空；
STLOC / STMAS 回滚、日期栏不动；ISRUN 不回退。原单被改过 → VOID_DOCUMENT_CHANGED；已作废 → 当作完成；关账 → PERIOD_LOCKED。
"""

from __future__ import annotations

from decimal import Decimal

import dbf

from companion import dbf_schema as S
from companion import dbf_void, payload_contract, worker
from test_dbf_stock import _entry, _line, _write, book  # noqa: F401
from test_stock_location import _rows, _state, _stloc, acct  # noqa: F401


def _void(acct, docnum, doctype, items, **extra):
    payload = {"payload_version": 7, "direction": "stock", "action": "void", "doctype": doctype, "docnum": docnum,
               "items": items, **extra}
    return dbf_void.void_stock_entry(payload, acct, selected_dir=str(acct), make_backup=extra.pop("make_backup", False))


def _deleted(acct, name, docnum):
    table = dbf.Table(str(acct / f"{name}.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        return [{field: row[field] for field in table.field_names} for row in table
                if dbf.is_deleted(row) and str(row["DOCNUM"]).strip() == docnum]
    finally:
        table.close()


def test_voiding_an_issue_marks_it_cancelled_and_rolls_the_stock_back_like_express(book):
    master_before = _rows(book, "STMAS", STKCOD="ICE01")[0]
    stloc_before = _stloc(book, "ICE01", "01")
    res = _write(book, _entry("OU", [_line("ICE01", 2)]))
    assert res.ok, res.error
    docnum = res.docnum
    moved = _stloc(book, "ICE01", "01")["LMOVDAT"]
    out = _void(book, docnum, "OU", [{"seq": 1, "code": "ICE01", "loccod": "01", "qty": "2"}])
    assert out.ok, out.error
    head = _rows(book, "STTRN", DOCNUM=docnum)[0]
    assert (head["DOCSTAT"], head["TRNVAL"], head["NXTSEQ"].strip()) == ("C", 0.0, "")
    assert _rows(book, "STCRD", DOCNUM=docnum) == []
    gone = _deleted(book, "STCRD", docnum)
    assert len(gone) == 1 and gone[0]["NETVAL"] == 20.0 and gone[0]["SEQNUM"].strip() == "1"
    assert all(row["DOCSTAT"] == "C" for row in _rows(book, "GLJNL", VOUCHER=docnum))
    assert [row["AMOUNT"] for row in _rows(book, "GLJNLIT", VOUCHER=docnum)] == [None, None]
    master = _rows(book, "STMAS", STKCOD="ICE01")[0]
    for name in ("MREMBAL", "MREMVAL", "TOTBAL", "TOTVAL"):
        assert master[name] == master_before[name], name
    stloc = _stloc(book, "ICE01", "01")
    assert stloc["LOCBAL"] == stloc_before["LOCBAL"] and stloc["LMOVDAT"] == moved  # 日期栏不动
    assert _rows(book, "ISRUN", PREFIX="OU")[0]["DOCNUM"].strip() == "0000002"  # 计数器不回退


def test_voiding_an_increase_takes_the_quantity_back_out(book):
    before = _rows(book, "STMAS", STKCOD="ICE01")[0]["MREMBAL"]
    res = _write(book, _entry("JU", [_line("ICE01", 4, unit_cost="12")]))
    assert res.ok, res.error
    out = _void(book, res.docnum, "JU", [{"code": "ICE01", "loccod": "01", "qty": "4"}])
    assert out.ok, out.error
    assert _rows(book, "STMAS", STKCOD="ICE01")[0]["MREMBAL"] == before


def test_a_document_changed_in_express_is_left_alone(book):
    res = _write(book, _entry("OU", [_line("ICE01", 2)]))
    before = _state(book)
    out = _void(book, res.docnum, "OU", [{"code": "ICE01", "loccod": "01", "qty": "3"}], make_backup=True)
    assert not out.ok and out.error_code == dbf_void.ERR_CHANGED == "VOID_DOCUMENT_CHANGED"
    assert "TRNQTY 2" in out.error
    assert _state(book) == before
    assert worker._outcome_for(out.error_code) == "needs_review"


def test_an_already_cancelled_document_counts_as_done_and_a_missing_one_is_reported(book):
    res = _write(book, _entry("OU", [_line("ICE01", 1)]))
    items = [{"code": "ICE01", "qty": "1"}]
    assert _void(book, res.docnum, "OU", items).ok
    again = _void(book, res.docnum, "OU", items)
    assert again.ok and again.notes == {"already_voided": True}
    missing = _void(book, "OU690320-999", "OU", items)
    assert not missing.ok and missing.error_code == "VOID_DOCUMENT_NOT_FOUND"


def test_a_locked_period_is_not_touched(book, monkeypatch):
    res = _write(book, _entry("OU", [_line("ICE01", 1)]))
    monkeypatch.setattr(dbf_void.fiscal_period, "locked", lambda acct, day: True)
    out = _void(book, res.docnum, "OU", [{"code": "ICE01", "qty": "1"}], respect_period_lock=True)
    assert not out.ok and out.error_code == "PERIOD_LOCKED"


def test_a_line_written_in_stock_units_with_the_erp_factor_still_matches(book):
    """ERP 的包装单位不是 Express 的：写入时按 ERP 换算率换成库存单位（TRNQTY = 数量 × 换算率），作废核原单照样认得出。"""
    res = _write(book, _entry("OU", [{"code": "ICE01", "name": "ICE01", "qty": "1", "unit": "แพ็ค", "factor": "2"}]))
    assert res.ok, res.error
    assert _rows(book, "STCRD", DOCNUM=res.docnum)[0]["TRNQTY"] == 2.0
    assert _void(book, res.docnum, "OU", [{"code": "ICE01", "qty": "1", "factor": "2"}]).ok


def test_payload_seven_is_supported():
    assert payload_contract.check_payload_version({"payload_version": 7}) is None


def test_a_failure_after_writing_restores_every_table(book, monkeypatch):
    res = _write(book, _entry("OU", [_line("ICE01", 2)]))
    before = _state(book)

    def boom(*a, **k):
        raise RuntimeError("disk")

    monkeypatch.setattr(dbf_void, "_verify", boom)
    out = _void(book, res.docnum, "OU", [{"code": "ICE01", "qty": "2"}], make_backup=True)
    assert not out.ok
    assert _state(book) == before


def test_amounts_are_compared_to_the_written_quantity_sign(book):
    res = _write(book, _entry("TK", [_line("ICE01", -1)]))
    assert res.ok, res.error
    assert not _void(book, res.docnum, "TK", [{"code": "ICE01", "qty": "1"}]).ok
    assert _void(book, res.docnum, "TK", [{"code": "ICE01", "qty": "-1"}]).ok
    assert Decimal(str(_rows(book, "STMAS", STKCOD="ICE01")[0]["MREMBAL"])) == Decimal("5")
