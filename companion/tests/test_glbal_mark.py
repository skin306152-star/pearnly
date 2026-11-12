# -*- coding: utf-8 -*-
"""写完分录把科目余额表 GLBAL 的对应格标成待重算（照 Express 原生，11-12 正版 Express 实证；``glbal_mark``，所有写分录的直写）。

Express 保存一张单：对每行分录的「科目 + 部门」那一行，把 ``CALSTA`` 里这一期那一位从 ``'1'`` 改成 ``'0'``，别的字节不动；之后重算只算标了 0 的格。
我们以前不标 → 写的金额进不了科目余额与试算表。证据见共享笔记「共享写入实验-11-12/GLBAL核实/」。
没证据的情形不写、记进回执 ``meta.glbal_unmarked``：行不存在、下一年度 / 判不出期间、那一位不是 1 也不是 0。
"""

from __future__ import annotations

import dbf
import pytest

from account_rules_fixture import isinfo
from companion import dbf_schema as S
from companion import glbal_mark
from companion import worker
from test_stock_location import (AP_ACC, PURCHASE_ACC, VAT_IN_ACC, _buy, _item, _no_runner, _purchase,  # noqa: F401
                                 _rows, _sale, _sell, _state, acct)

SPEC = "ACCNUM C(15); DEPCOD C(4); JOBCOD C(4); CALSTA C(24); BEGLY N(15,2); DEBIT1 N(15,2); CREDIT1 N(15,2)"
DONE12 = "1" * 12


def _glbal(acct, rows):
    table = dbf.Table(str(acct / "GLBAL.DBF"), SPEC, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    table.open(dbf.READ_WRITE)
    try:
        for acc, calsta in rows:
            table.append({"ACCNUM": acc, "DEPCOD": "", "CALSTA": calsta, "BEGLY": 100.0, "DEBIT1": 55.5, "CREDIT1": 7.25})
    finally:
        table.close()


def _raw(acct):
    """GLBAL 每一行的原始字节（不经 dbf 解码），按行号。"""
    data = (acct / "GLBAL.DBF").read_bytes()
    header_len = int.from_bytes(data[8:10], "little")
    rec_len = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[4:8], "little")
    return [data[header_len + i * rec_len: header_len + (i + 1) * rec_len] for i in range(count)]


def _calsta(acct):
    return {str(r["ACCNUM"]).strip(): str(r["CALSTA"]) for r in _rows(acct, "GLBAL")}


def _journal_accounts(acct, docnum):
    return {str(r["ACCNUM"]).strip() for r in _rows(acct, "GLJNLIT", VOUCHER=docnum)}


def test_a_purchase_marks_the_period_position_of_every_journal_account_and_nothing_else(acct):
    """赊购单 2026-01-15（第 1 期）：分录里每个科目那一行 CALSTA 第 1 位 1 → 0；别的行、别的字节一个不动。"""
    _glbal(acct, [(PURCHASE_ACC, DONE12), (VAT_IN_ACC, DONE12), (AP_ACC, DONE12), ("11-04-02-00", DONE12),
                  ("99-99-99-99", DONE12)])
    before = _raw(acct)
    res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")]))
    assert res.ok, res.error
    posted = _journal_accounts(acct, res.docnum)
    assert posted and "99-99-99-99" not in posted
    states = _calsta(acct)
    for acc, state in states.items():
        assert state.rstrip() == ("0" + "1" * 11 if acc in posted else DONE12), acc
    after = _raw(acct)
    offset = 1 + 15 + 4 + 4  # 删除标记 + ACCNUM + DEPCOD + JOBCOD → CALSTA 第 1 位
    for old, new in zip(before, after):
        diff = [i for i in range(len(old)) if old[i] != new[i]]
        assert diff in ([], [offset]), diff
    assert len(after) == len(before)


def test_a_sale_and_a_stock_entry_mark_their_accounts_too(acct):
    _glbal(acct, [("11-02-01-00", DONE12 * 2), ("41-01-00-00", DONE12), ("11-05-04-02", DONE12)])
    res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], docdate="690320"))  # 第 3 期
    assert res.ok, res.error
    states = _calsta(acct)
    assert states["41-01-00-00"].rstrip() == "110111111111"
    assert states["11-02-01-00"].rstrip() == "110" + "1" * 21  # 24 位都有值的行：只动第 3 位


def test_cells_without_evidence_are_left_alone_and_reported(acct, monkeypatch):
    """下一年度的单、对应行不存在、那一位是空格：不写，记进回执 meta.glbal_unmarked。"""
    _glbal(acct, [("11-04-02-00", "1" * 11), (VAT_IN_ACC, DONE12)])  # 存货科目第 12 位空：这一期还没算过
    before = _raw(acct)
    with glbal_mark.collecting() as unmarked:
        res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")], docdate="700105"))  # 2027-01-05：下一年度
    assert res.ok and _raw(acct) == before
    assert unmarked and all("period not in this fiscal year" in text for text in unmarked)
    with glbal_mark.collecting() as unmarked:
        res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")], docdate="691215", ref_no="PO-12"))
    assert res.ok
    assert any(text.startswith("11-04-02-00") and "calculation state not set" in text for text in unmarked)
    assert any(text.startswith(AP_ACC) and "no GLBAL row" in text for text in unmarked)
    assert _calsta(acct)[VAT_IN_ACC].rstrip() == "1" * 11 + "0"


def test_a_book_without_glbal_is_written_as_before_and_says_so(acct):
    with glbal_mark.collecting() as unmarked:
        res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")]))
    assert res.ok and unmarked and all("no GLBAL" in text for text in unmarked)


def test_a_failed_readback_rolls_the_whole_document_back(acct, monkeypatch):
    _glbal(acct, [(PURCHASE_ACC, DONE12), (VAT_IN_ACC, DONE12), (AP_ACC, DONE12), ("11-04-02-00", DONE12)])
    before = _state(acct)

    from companion import dbf_writer

    def broken(path, changed):
        raise dbf_writer._WriteError("readback", dbf_writer.ERR_VERIFY)

    monkeypatch.setattr(glbal_mark, "_verify", broken)
    res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")]), make_backup=True)
    assert not res.ok and res.error_code == "READBACK_VERIFY_FAILED"
    assert _state(acct) == before  # GLBAL 在写前备份里，整单还原


def test_the_receipt_carries_what_was_not_marked(acct, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(worker, "_task_settings", lambda cfg, payload: SimpleNamespace(
        account_dir=str(acct), account_set="DATAT", account_company="", has_selection=lambda: True))
    seen = {}

    class Queue:
        def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
            seen.update(meta or {})
            return True

        def ack_failure(self, *a, **k):
            raise AssertionError(k)

    task = SimpleNamespace(id="T-GL", payload={**_purchase([_item("ICE01", 2, 20.0, "stock_item")]), "direction": "purchase"},
                           invoice_no="PO-1", attended=False)
    assert worker._handle_dbf_task(SimpleNamespace(default_depcod="", ui_lang="en"), Queue(), task) is None
    assert seen["glbal_unmarked"] and all("no GLBAL" in text for text in seen["glbal_unmarked"])


@pytest.mark.parametrize("direction, payload", [("purchase", {"doctype": "RR"}), ("sales", {"doctype": "IV"}),
                                                ("receipt", {}), ("stock", {"doctype": "OU"}),
                                                ("stock", {"doctype": "OU", "action": "void"})])
def test_glbal_is_probed_and_backed_up_on_every_path_that_writes_a_journal(direction, payload):
    assert glbal_mark.TABLE in worker._write_tables(direction, payload)
    assert glbal_mark.TABLE not in worker._write_tables("stock", {"doctype": "RL"})  # 调拨不写分录
