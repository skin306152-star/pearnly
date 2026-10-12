# -*- coding: utf-8 -*-
"""按需请求 express_read（小助手侧）：只读读出一本账套一个月的账交回云端——只认本机能推送的同一组账套、复制到本机再读、
不动账套里任何文件、按月筛、点名的单据不论日期都给、作废 / 软删 / 没有凭证头的行各按口径处理、金额按 str → Decimal 两位。"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import dbf
import pytest

import _synth_account
from companion import dbf_schema as S
from companion import express_read
from companion.request_contract import RequestFailed

D = dt.date
_SCCOMP = "COMPNAM C(60); COMPCOD C(10); PATH C(150); GENDAT D; CANDEL C(1)"
_ISINFO = "THINAM C(60); TAXID C(15); ORGNUM N(5,0); ACCNUM00 C(15); ACCNUM04 C(15); ACCNUM18 C(15); ISPERPETUA C(1)"
# GLBAL 的列名照测试机原型 xgl.py 读真表用的那几组（BEGLY、DEBIT{k}LY / CREDIT{k}LY、DEBIT{k} / CREDIT{k}、*CLS）
_GLBAL = "ACCNUM C(15); BEGLY B; " + "; ".join(
    f"{side}{k}{suffix} B" for suffix in ("LY", "") for k in range(1, 13) for side in ("DEBIT", "CREDIT")
) + "; DEBITCLS B; CREDITCLS B"


def _table(path: Path, spec: str, rows, deleted=()):
    table = dbf.Table(str(path), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    table.open(dbf.READ_WRITE)
    try:
        for row in rows:
            table.append(row)
        for index in deleted:
            dbf.delete(table[index])
    finally:
        table.close()


def _append(path: Path, rows, deleted=()):
    table = dbf.Table(str(path), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        start = len(table)
        for row in rows:
            table.append(row)
        for index in deleted:
            dbf.delete(table[start + index])
    finally:
        table.close()


def _line(voucher, seq, day, account, trntyp, amount, descrp=""):
    return {"VOUCHER": voucher, "SEQIT": seq, "VOUDAT": day, "ACCNUM": account, "DESCRP": descrp,
            "TRNTYP": trntyp, "AMOUNT": amount}


@pytest.fixture
def books(tmp_path):
    root = tmp_path / "EXP69"
    acct = _synth_account.build(root / "T9SINCER")
    (root / "secure").mkdir()
    _table(root / "secure" / "SCCOMP.DBF", _SCCOMP, [{"COMPNAM": "T9 SINCERE", "COMPCOD": "T9", "PATH": "T9SINCER"}])
    _table(acct / "ISINFO.DBF", _ISINFO, [{"THINAM": "บริษัท ซินเซียร์ จำกัด", "TAXID": "0315551000297", "ORGNUM": 0,
                                           "ACCNUM00": "11-01-01-00", "ACCNUM04": "11-04-00-00",
                                           "ACCNUM18": "51-03-00-00", "ISPERPETUA": "N"}])
    _table(acct / "GLBAL.DBF", _GLBAL, [
        {"ACCNUM": "11-04-00-00", "BEGLY": 1.005, "DEBIT3LY": 200.0, "CREDIT3LY": 50.0, "DEBIT1": 999.0},
        {"ACCNUM": "11-04-00-00", "BEGLY": 10.0},  # 同一科目两行（按部门）：相加
        {"ACCNUM": "41-01-00-00", "CREDIT12LY": 300.0},
    ])
    _append(acct / "GLJNL.DBF", [
        {"JNLTYP": "00", "VOUDAT": D(2026, 4, 15), "VOUCHER": "JV690415-001", "DESCRP": "ยกมา", "DOCSTAT": "N"},
        {"JNLTYP": "04", "VOUDAT": D(2026, 5, 2), "VOUCHER": "RR690502-001", "DESCRP": "ซื้อเชื่อจาก A",
         "DOCSTAT": "N", "CREBY": "PEARNLY"},
        {"JNLTYP": "03", "VOUDAT": D(2026, 5, 10), "VOUCHER": "IV690510-001", "DESCRP": "ขาย", "DOCSTAT": "C"},
        {"JNLTYP": "01", "VOUDAT": D(2026, 6, 1), "VOUCHER": "RR690601-002", "DESCRP": "ซื้อ", "DOCSTAT": "N"},
    ])
    _append(acct / "GLJNLIT.DBF", [
        _line("JV690415-001", "1", D(2026, 4, 15), "11-04-00-00", "0", 100.0),
        _line("JV690415-001", "2", D(2026, 4, 15), "41-01-00-00", "1", 100.0),
        _line("RR690502-001", "0", D(2026, 5, 2), "51-03-00-00", "0", 1000.0, "ค่าน้ำแข็ง"),
        _line("RR690502-001", "6", D(2026, 5, 2), "11-04-00-00", "1", 1000.0),
        _line("IV690510-001", "2", D(2026, 5, 10), "11-04-00-00", "0", 50.0),
        _line("IV690510-001", "5", D(2026, 5, 10), "41-01-00-00", "1", 50.0),
        _line("RR690601-002", "0", D(2026, 6, 1), "51-03-00-00", "0", 70.0),
        _line("RR690601-002", "6", D(2026, 6, 1), "11-04-00-00", "1", 70.0),
        _line("NOHEAD-1", "0", D(2026, 5, 3), "51-03-00-00", "0", 5.0),
        _line("RR690502-001", "9", D(2026, 5, 2), "51-03-00-00", "0", 77.0),  # 软删：不算
    ], deleted=(9,))
    _append(acct / "ISVAT.DBF", [
        {"VATREC": "P", "RECTYP": "3", "VATPRD": D(2026, 5, 1), "VATDAT": D(2026, 5, 2), "DOCDAT": D(2026, 5, 2),
         "DOCNUM": "RR690502-001", "REFNUM": "INV-9", "DESCRP": "ร้าน A", "AMT01": 1000.0, "VAT01": 70.0,
         "DOCSTAT": "N", "TAXID": "0105556012345", "ORGNUM": 1, "PRENAM": "บจก."},
        # 税期在下月、税票日在本月（顺延抵扣）：照给
        {"VATREC": "P", "RECTYP": "3", "VATPRD": D(2026, 6, 1), "VATDAT": D(2026, 5, 28), "DOCDAT": D(2026, 4, 30),
         "DOCNUM": "RR690430-004", "AMT01": 10.0, "VAT01": 0.7, "DOCSTAT": "N"},
        {"VATREC": "P", "RECTYP": "3", "VATPRD": D(2026, 7, 1), "VATDAT": D(2026, 7, 2), "DOCDAT": D(2026, 7, 2),
         "DOCNUM": "RR690702-001", "AMT01": 1.0, "VAT01": 0.07, "DOCSTAT": "N"},
        {"VATREC": "P", "RECTYP": "3", "VATPRD": D(2026, 6, 1), "VATDAT": D(2026, 6, 1), "DOCDAT": D(2026, 6, 1),
         "DOCNUM": "RR690520-003", "AMT01": 3.0, "VAT01": 0.21, "DOCSTAT": "N"},
        {"VATREC": "S", "RECTYP": "3", "VATPRD": D(2026, 5, 1), "VATDAT": D(2026, 5, 9), "DOCDAT": D(2026, 5, 9),
         "DOCNUM": "IV690509-001", "AMT01": 4.0, "VAT01": 0.28, "DOCSTAT": "N"},
    ], deleted=(3, 4))
    _append(acct / "APMAS.DBF", [{"SUPCOD": "S001", "SUPNAM": "ร้าน A", "TAXID": "0105556012345",
                                  "ACCNUM": "21-01-00-02"}])
    _append(acct / "APTRN.DBF", [
        {"RECTYP": "3", "DOCNUM": "RR690502-001", "DOCDAT": D(2026, 5, 2), "REFNUM": "INV-9", "SUPCOD": "S001",
         "NETVAL": 1000.0, "VATAMT": 70.0, "NETAMT": 1070.0, "DOCSTAT": "N", "USERID": "PEARNLY"},
        {"RECTYP": "3", "DOCNUM": "RR690601-002", "DOCDAT": D(2026, 6, 1), "SUPCOD": "S009", "NETAMT": 70.0,
         "DOCSTAT": "N"},
        {"RECTYP": "1", "DOCNUM": "HP690415-009", "DOCDAT": D(2026, 4, 15), "NETAMT": 1.0, "DOCSTAT": "N"},
        {"RECTYP": "3", "DOCNUM": "RR690520-003", "DOCDAT": D(2026, 5, 20), "NETAMT": 3.21, "DOCSTAT": "N"},
        {"RECTYP": "1", "DOCNUM": "HP690521-001", "DOCDAT": D(2026, 5, 21), "NETAMT": 9.0, "DOCSTAT": "N"},
    ], deleted=(3, 4))
    _append(acct / "ARTRN.DBF", [
        {"RECTYP": "3", "DOCNUM": "IV690510-001", "DOCDAT": D(2026, 5, 10), "YOUREF": "S-1", "CUSCOD": "C1",
         "NETAMT": 53.5, "DOCSTAT": "C", "USERID": "AOM"},
    ])
    return SimpleNamespace(root=root, acct=acct)


def _cfg(books, **extra):
    return SimpleNamespace(express_root=str(books.root), authorized_roots=[], account_set="", account_dir="", **extra)


def _params(books, **extra):
    return {"account_set": str(books.acct), "account_root": str(books.root), "period": "2026-05",
            "docnums": ["RR690601-002", "rr690520-003"], **extra}


def _read(books, **extra):
    result = express_read.read_books(_cfg(books), _params(books, **extra), sleep=lambda _s: None)
    return result, json.loads(gzip.decompress(result.content).decode("utf-8"))


def _state(folder: Path):
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(folder.iterdir()) if path.is_file()}


def test_reads_the_month_without_touching_the_account_set(books):
    before = _state(books.acct)
    result, data = _read(books)
    assert _state(books.acct) == before  # 只读：账套里每个文件的大小、修改时间、内容都不变
    assert result.meta["format"] == "json.gz" and data["version"] == express_read.RESULT_VERSION
    assert (data["period"], data["account_set"]) == ("2026-05", "T9SINCER")
    assert data["read_at"].endswith("Z")
    assert data["company"] == {"name": "บริษัท ซินเซียร์ จำกัด", "tax_id": "0315551000297", "branch": "00000"}
    # 推送写分录时会换用的默认科目与盘存制（服务端据此认出推送时换过的科目）
    assert data["settings"] == {"cash": "11-01-01-00", "stock": "11-04-00-00", "purchases": "51-03-00-00",
                                "periodic": True}
    assert len(data["calendar"]) == 24 and data["calendar"][0] == {"index": 1, "slot": "", "beg": "2026-01-01",
                                                                    "end": "2026-01-31"}
    assert {"code": "41-01-00-00", "name": "รายได้จากการขาย", "name2": "", "group": "4", "level": "",
            "parent": "41-00-00-00"} in data["accounts"]


def test_settings_are_empty_when_the_account_set_has_no_such_columns(books):
    (books.acct / "ISINFO.DBF").unlink()
    _table(books.acct / "ISINFO.DBF", "THINAM C(60); TAXID C(15)", [{"THINAM": "X", "TAXID": "1"}])
    _result, data = _read(books)
    assert data["settings"] == {"cash": "", "stock": "", "purchases": "", "periodic": False}
    (books.acct / "ISINFO.DBF").unlink()
    _result, data = _read(books)
    assert data["settings"] == {"cash": "", "stock": "", "purchases": "", "periodic": False}
    assert "ISINFO" in data["diagnostics"]["missing_tables"]


def test_balances_add_rows_of_one_account_and_keep_two_decimals_from_the_printed_value(books):
    _result, data = _read(books)
    balances = {row["code"]: row["values"] for row in data["balances"]}
    # 1.005 经 str → Decimal 四舍五入是 1.01（直接按二进制浮点舍入会成 1.00）；两行相加；0 的列不给
    assert balances["11-04-00-00"] == {"BEGLY": "11.01", "CREDIT3LY": "50.00", "DEBIT1": "999.00",
                                       "DEBIT3LY": "200.00"}
    assert balances["41-01-00-00"] == {"CREDIT12LY": "300.00"}


def test_movements_up_to_the_month_skip_cancelled_soft_deleted_and_headless_lines(books):
    _result, data = _read(books)
    moved = {(row["code"], row["month"], row["trntyp"]): row["amount"] for row in data["movements"]}
    assert moved == {("11-04-00-00", "2026-04", "0"): "100.00", ("41-01-00-00", "2026-04", "1"): "100.00",
                     ("51-03-00-00", "2026-05", "0"): "1000.00", ("11-04-00-00", "2026-05", "1"): "1000.00"}
    assert data["diagnostics"]["orphan_lines"] == 1


def test_journal_has_the_months_vouchers_and_the_named_ones_with_their_heads(books):
    _result, data = _read(books)
    vouchers = {row["voucher"]: row for row in data["journal"]}
    assert set(vouchers) == {"RR690502-001", "IV690510-001", "RR690601-002"}
    assert vouchers["IV690510-001"]["docstat"] == "C"  # 作废的也给（服务端出总账时跳过）
    assert vouchers["RR690502-001"]["book"] == "04" and vouchers["RR690502-001"]["creby"] == "PEARNLY"
    assert vouchers["RR690502-001"]["lines"][0] == {"seq": "0", "account": "51-03-00-00", "date": "2026-05-02",
                                                    "descrp": "ค่าน้ำแข็ง", "trntyp": "0", "amount": "1000.00",
                                                    "dept": ""}
    assert len(vouchers["RR690502-001"]["lines"]) == 2  # 软删的那一行不在
    assert data["diagnostics"]["unbalanced"] == []


def test_vat_rows_by_tax_period_document_date_or_tax_date_and_named_deleted_rows(books):
    _result, data = _read(books)
    rows = {row["docnum"]: row for row in data["vat"]}
    # RR690430-004：税期在下月、税票日在本月；RR690702-001 不在本月；IV690509-001 软删且没点名：不给
    assert set(rows) == {"RR690502-001", "RR690430-004", "RR690520-003"}
    assert rows["RR690502-001"] == {
        "vatrec": "P", "rectyp": "3", "vatprd": "2026-05-01", "vatdat": "2026-05-02", "docdat": "2026-05-02",
        "docnum": "RR690502-001", "refnum": "INV-9", "descrp": "ร้าน A", "prenam": "บจก.",
        "taxid": "0105556012345", "branch": "00001", "docstat": "N", "amt01": "1000.00", "vat01": "70.00",
        "extra": {}, "deleted": False}
    assert rows["RR690520-003"]["deleted"] is True


def test_documents_of_the_month_and_named_ones_with_the_party_from_the_master(books):
    _result, data = _read(books)
    docs = {row["docnum"]: row for row in data["documents"]}
    assert set(docs) == {"RR690502-001", "RR690601-002", "RR690520-003", "IV690510-001"}
    assert docs["RR690502-001"]["party_name"] == "ร้าน A" and docs["RR690502-001"]["party_taxid"] == "0105556012345"
    assert docs["RR690502-001"]["party_account"] == "21-01-00-02"  # 对方档上的科目（赊购的应付行记它）
    assert docs["RR690601-002"]["party_account"] == ""  # 对方档里没有这家
    assert (docs["RR690502-001"]["table"], docs["RR690502-001"]["ref"], docs["RR690502-001"]["netamt"],
            docs["RR690502-001"]["userid"]) == ("AP", "INV-9", "1070.00", "PEARNLY")
    assert docs["RR690520-003"]["deleted"] is True and docs["RR690601-002"]["deleted"] is False
    assert (docs["IV690510-001"]["table"], docs["IV690510-001"]["ref"], docs["IV690510-001"]["docstat"]) == \
        ("AR", "S-1", "C")


def test_an_unbalanced_voucher_is_read_again_once_then_reported(books, monkeypatch):
    _append(books.acct / "GLJNL.DBF", [{"JNLTYP": "01", "VOUDAT": D(2026, 5, 3), "VOUCHER": "HP690503-001",
                                        "DOCSTAT": "N"}])
    _append(books.acct / "GLJNLIT.DBF", [_line("HP690503-001", "0", D(2026, 5, 3), "52-08-00-00", "0", 10.0),
                                         _line("HP690503-001", "6", D(2026, 5, 3), "11-04-00-00", "1", 9.0)])
    reads, sleeps = [], []
    real = express_read.collect
    monkeypatch.setattr(express_read, "collect", lambda *a, **k: reads.append(1) or real(*a, **k))
    result = express_read.read_books(_cfg(books), _params(books), sleep=sleeps.append)
    data = json.loads(gzip.decompress(result.content).decode("utf-8"))
    assert len(reads) == 2 and sleeps == [express_read.REREAD_WAIT]
    assert data["diagnostics"]["unbalanced"] == ["HP690503-001"]


def test_reads_while_the_tables_are_open_elsewhere(books):
    held = dbf.Table(str(books.acct / "GLJNLIT.DBF"), codepage=S.CODEPAGE)
    held.open(dbf.READ_WRITE)  # 推送或 Express 开着这张表
    try:
        _result, data = _read(books)
    finally:
        held.close()
    assert {row["voucher"] for row in data["journal"]} >= {"RR690502-001"}


def test_only_account_sets_this_computer_may_push_to(books, tmp_path):
    cfg = _cfg(books)
    for params, code in (
        (_params(books, account_root=str(tmp_path / "other")), "account_not_allowed"),
        (_params(books, account_set=str(books.root / "NOTREGISTERED")), "account_not_allowed"),
        (_params(books, account_root=""), "account_not_allowed"),
        (_params(books, period="2026-13"), "params_invalid"),
        (_params(books, docnums=["X" * 21]), "params_invalid"),
        (_params(books, docnums="RR1"), "params_invalid"),
    ):
        with pytest.raises(RequestFailed) as caught:
            express_read.read_books(cfg, params, sleep=lambda _s: None)
        assert caught.value.code == code, params
    # 授权根目录里的账套同样可读；没给根目录时只认 CC 桌面端里选定的那一本
    other = SimpleNamespace(express_root="", authorized_roots=[str(books.root)], account_set="", account_dir="")
    assert express_read.read_books(other, _params(books), sleep=lambda _s: None).content
    chosen = SimpleNamespace(express_root="", authorized_roots=[], account_set=str(books.acct),
                             account_dir=str(books.acct))
    assert express_read.read_books(chosen, _params(books, account_root=""), sleep=lambda _s: None).content
    (books.root / "secure" / "SCCOMP.DBF").unlink()
    with pytest.raises(RequestFailed) as caught:
        express_read.read_books(cfg, _params(books), sleep=lambda _s: None)
    assert caught.value.code == "account_unreachable"


def test_a_table_held_exclusively_is_retried_then_reported_busy(books, monkeypatch):
    real_open, sleeps = open, []

    def locked(path, mode="r", *args, **kwargs):
        if str(path).upper().endswith("GLJNLIT.DBF") and "r" in mode and "b" in mode:
            raise PermissionError(13, "sharing violation")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(express_read, "open", locked, raising=False)
    with pytest.raises(RequestFailed) as caught:
        express_read.read_books(_cfg(books), _params(books), sleep=sleeps.append)
    assert caught.value.code == "account_busy"
    assert sleeps == list(express_read.COPY_WAITS)


def test_missing_required_tables_and_too_many_lines(books, monkeypatch):
    monkeypatch.setattr(express_read, "JOURNAL_LINES_MAX", 1)
    with pytest.raises(RequestFailed) as caught:
        _read(books)
    assert caught.value.code == "too_large"
    (books.acct / "GLJNL.DBF").unlink()
    with pytest.raises(RequestFailed) as caught:
        _read(books)
    assert caught.value.code == "account_tables_missing"


def test_the_request_thread_knows_how_to_read_books():
    from companion import request_worker

    assert request_worker.HANDLERS["express_read"] is express_read.read_books
