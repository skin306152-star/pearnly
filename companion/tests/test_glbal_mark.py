# -*- coding: utf-8 -*-
"""写完分录把科目余额表 GLBAL 的对应格标成待重算（照 Express 原生，11-12 正版 Express 实证；``glbal_mark``，所有写分录的直写）。

Express 保存一张单：对每行分录的「科目 + 部门」那一行，把 ``CALSTA`` 里这一期那一位从 ``'1'`` 改成 ``'0'``，别的字节不动；之后重算只算标了 0 的格。
我们以前不标 → 写的金额进不了科目余额与试算表。证据见共享笔记「共享写入实验-11-12/GLBAL核实/」。
那一位是空格（这一期还没算过）照原生不碰、不报；对应行不存在照原生追加一行（并只为此重建 GLBAL 索引）；
下一年度 / 判不出期间、那一位是别的字符：不写、记进回执 ``meta.glbal_unmarked``。
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


# 原生 GLBAL 的结构（11-12 测试机2 `70EXP\\test`，只取字段定义）：4 个字符栏 + 75 个 8 字节双精度金额栏
NATIVE_SPEC = "; ".join(["ACCNUM C(15)", "DEPCOD C(4)", "JOBCOD C(6)", "CALSTA C(24)", "BEGLY B"]
                        + [f"{side}{i}{seg} B" for side in ("DEBIT", "CREDIT") for seg in ("LY", "", "NY") for i in range(1, 13)]
                        + ["DEBITCLS B", "CREDITCLS B"])


def _native_glbal(acct, rows):
    table = dbf.Table(str(acct / "GLBAL.DBF"), NATIVE_SPEC, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    table.open(dbf.READ_WRITE)
    try:
        for acc, calsta in rows:
            table.append({"ACCNUM": acc, "DEPCOD": "", "CALSTA": calsta, "DEBIT1": 12.5})
    finally:
        table.close()


def _express_new_row(acc):
    """Express 原生保存用到 GLBAL 里没有的科目时追加的那一行（11-12 测试机2，原生 RR0000026 的 11-04-02-00）：
    未删除、科目码左对齐补空格、部门 / 项目 / 计算状态全空格、75 个金额栏全 0。"""
    return b" " + acc.ljust(15).encode() + b" " * (4 + 6 + 24) + b"\0" * (75 * 8)


def test_the_next_fiscal_year_marks_position_twelve_plus_k(acct):
    """下一年度第 k 期标第 12 + k 位（S 盘副本 1033 套：第 13–24 位的 0 都落在 NY 那一期有分录的位置）；那一期还没算过（空格）照原生不碰。"""
    _glbal(acct, [("11-04-02-00", "1" * 24), (VAT_IN_ACC, DONE12), (AP_ACC, "1" * 24)])
    before = _raw(acct)
    with glbal_mark.collecting() as unmarked:
        res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")], docdate="700105"))  # 2027-01-05：下一年度第 1 期
    assert res.ok, res.error
    assert unmarked == []
    states = _calsta(acct)
    assert states["11-04-02-00"] == "1" * 12 + "0" + "1" * 11
    assert states[AP_ACC] == "1" * 12 + "0" + "1" * 11
    assert _raw(acct)[1] == before[1]  # 进项税那一行下一年度还没算过（空格）：一个字节不动


def test_a_date_outside_both_years_is_left_alone_and_reported(acct):
    _glbal(acct, [("11-04-02-00", "1" * 24), (VAT_IN_ACC, "1" * 24), (AP_ACC, "1" * 24)])
    before = _raw(acct)
    with glbal_mark.collecting() as unmarked:
        res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")], docdate="710105"))  # 2028：两个年度都不是
    if not res.ok:  # 期间闸拦下也行：一个字节都没写
        assert _raw(acct) == before
        return
    assert _raw(acct) == before
    assert unmarked and all("period not in this or next fiscal year" in text for text in unmarked)


def test_a_cell_never_calculated_is_left_alone_without_a_report(acct):
    """那一位是空格（这一期还没算过）：Express 原生保存不碰它（11-12 测试机2 原生 RR0000025，GLBAL 逐字节不变），
    重算时整期照算，所以我们也不写、也不当成没标上报给云端。"""
    _glbal(acct, [("11-04-02-00", "1" * 11), (VAT_IN_ACC, DONE12), (AP_ACC, "1" * 11)])  # 第 12 位空：12 月还没算过
    before = _raw(acct)
    with glbal_mark.collecting() as unmarked:
        res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")], docdate="691215", ref_no="PO-12"))
    assert res.ok, res.error
    assert unmarked == []
    after = _raw(acct)
    states = _calsta(acct)
    assert states[VAT_IN_ACC].rstrip() == "1" * 11 + "0"
    assert after[0] == before[0] and after[2] == before[2]  # 空格那两行一个字节不动


def test_a_missing_row_is_appended_the_way_express_does_and_indexed(acct, monkeypatch):
    """用到 GLBAL 里没有的「科目 + 部门」：照 Express 原生追加一行（计算状态全空格、金额全 0），只为这种情形重建 GLBAL 的索引；
    回执不再报「no GLBAL row」。"""
    from companion import dbf_writer

    _native_glbal(acct, [(PURCHASE_ACC, DONE12), (VAT_IN_ACC, DONE12), ("11-04-02-00", DONE12)])
    reindexed = []
    real = dbf_writer._reindex
    monkeypatch.setattr(dbf_writer, "_reindex", lambda d, tables=S.WRITE_TABLES: (reindexed.append(tuple(tables)), real(d, tables))[1])
    before = _raw(acct)
    with glbal_mark.collecting() as unmarked:
        res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")]))
    assert res.ok, res.error
    assert unmarked == []
    after = _raw(acct)
    posted = _journal_accounts(acct, res.docnum)
    assert AP_ACC in posted
    assert len(after) == len(before) + 1
    assert after[-1] == _express_new_row(AP_ACC)
    assert (glbal_mark.TABLE,) in reindexed
    for acc, state in _calsta(acct).items():
        if acc != AP_ACC:
            assert state.rstrip() == ("0" + "1" * 11 if acc in posted else DONE12), acc


def test_nothing_is_reindexed_when_no_row_is_added(acct, monkeypatch):
    from companion import dbf_writer

    _native_glbal(acct, [(PURCHASE_ACC, DONE12), (VAT_IN_ACC, DONE12), (AP_ACC, DONE12), ("11-04-02-00", DONE12)])
    reindexed = []
    real = dbf_writer._reindex
    monkeypatch.setattr(dbf_writer, "_reindex", lambda d, tables=S.WRITE_TABLES: (reindexed.append(tuple(tables)), real(d, tables))[1])
    res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")]))
    assert res.ok, res.error
    assert all(glbal_mark.TABLE not in tables for tables in reindexed)


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
