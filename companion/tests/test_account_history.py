# -*- coding: utf-8 -*-
"""按需请求 account_history（小助手侧，科目建议 B）：只读汇总一本账套近 12 个月「供应商 → 常用科目」——只认本机能推送的
同一组账套、复制到本机再读、不动账套里任何文件；只算借方行，不算现金 / 进项税 / 应付与供应商档上的应付科目；作废、软删、
太早的单与作废的凭证不算；次数按单据数，同一张单几行同科目只算一次。"""

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
from companion import account_history, request_worker
from companion import dbf_schema as S
from companion.request_contract import MAX_BYTES, RequestFailed

TODAY = dt.date.today()
SINCE = TODAY - dt.timedelta(days=365)
_SCCOMP = "COMPNAM C(60); COMPCOD C(10); PATH C(150); GENDAT D; CANDEL C(1)"
_ISINFO = "THINAM C(60); TAXID C(15); ACCNUM00 C(15); ACCNUM06 C(15); ACCNUM07 C(15)"
STATIONERY, REPAIR, POWER = "53-20-01-00", "53-08-00-00", "53-10-00-00"
CASH, INPUT_VAT, PAYABLE, OWN_PAYABLE = "11-01-01-00", "11-05-00-00", "21-01-00-00", "21-01-00-02"


def _table(path: Path, spec: str, rows):
    table = dbf.Table(str(path), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    table.open(dbf.READ_WRITE)
    try:
        for row in rows:
            table.append(row)
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


def _days(n):
    return TODAY - dt.timedelta(days=n)


def _doc(docnum, supplier, days, docstat="N"):
    return {"RECTYP": "3", "DOCNUM": docnum, "DOCDAT": _days(days), "SUPCOD": supplier, "DOCSTAT": docstat}


def _head(voucher, days, docstat="N"):
    return {"JNLTYP": "04", "VOUDAT": _days(days), "VOUCHER": voucher, "DOCSTAT": docstat}


def _line(voucher, days, account, trntyp, amount=100.0, seq="0"):
    return {"VOUCHER": voucher, "SEQIT": seq, "VOUDAT": _days(days), "ACCNUM": account, "TRNTYP": trntyp,
            "AMOUNT": amount}


def _purchase(voucher, days, expense, *, payable=PAYABLE):
    return [_line(voucher, days, expense, "0", 100.0, "0"), _line(voucher, days, INPUT_VAT, "0", 7.0, "3"),
            _line(voucher, days, payable, "1", 107.0, "6")]


@pytest.fixture
def books(tmp_path):
    root = tmp_path / "EXP69"
    acct = _synth_account.build(root / "DAOWNUEA")
    (root / "secure").mkdir()
    _table(root / "secure" / "SCCOMP.DBF", _SCCOMP, [{"COMPNAM": "ดาวเหนือ", "COMPCOD": "DN", "PATH": "DAOWNUEA"}])
    _table(acct / "ISINFO.DBF", _ISINFO, [{"THINAM": "ดาวเหนือ", "TAXID": "0105560000001", "ACCNUM00": CASH,
                                           "ACCNUM06": INPUT_VAT, "ACCNUM07": PAYABLE}])
    _append(acct / "APMAS.DBF", [
        {"SUPCOD": "S001", "SUPNAM": "ศรีกรุง เครื่องเขียน", "TAXID": "0105556012345", "ORGNUM": 1,
         "ACCNUM": OWN_PAYABLE},
        {"SUPCOD": "S002", "SUPNAM": "การไฟฟ้าส่วนภูมิภาค", "TAXID": "0994000165510", "ACCNUM": PAYABLE},
    ])
    _append(acct / "APTRN.DBF", [
        _doc("RR-001", "S001", 30), _doc("RR-002", "S001", 60), _doc("HP-003", "S001", 90),
        _doc("RR-004", "S001", 120, docstat="C"),  # 作废的单
        _doc("RR-005", "S001", 400),  # 一年多以前
        _doc("RR-006", "S001", 10),  # 软删
        _doc("RR-007", "S001", 15),  # 凭证作废
        _doc("RR-008", "S002", 20),
        _doc("rr-009 ", "S001", 5),  # 单号大小写、空格与凭证不同也是同一张
    ], deleted=(5,))
    _append(acct / "GLJNL.DBF", [_head(number, 1) for number in
                                 ("RR-001", "RR-002", "HP-003", "RR-004", "RR-005", "RR-006", "RR-008", "RR-009")]
            + [_head("RR-007", 15, docstat="C")])
    _append(acct / "GLJNLIT.DBF", [
        *_purchase("RR-001", 30, STATIONERY, payable=OWN_PAYABLE),
        _line("RR-001", 30, STATIONERY, "0", 5.0, "1"),  # 同一张单两行同科目：只算一张
        *_purchase("RR-002", 60, STATIONERY, payable=OWN_PAYABLE),
        # 现购：借费用、贷现金
        _line("HP-003", 90, REPAIR, "0", 50.0), _line("HP-003", 90, CASH, "1", 50.0),
        *_purchase("RR-004", 120, REPAIR), *_purchase("RR-005", 400, REPAIR), *_purchase("RR-006", 10, REPAIR),
        *_purchase("RR-007", 15, REPAIR), *_purchase("RR-008", 20, POWER), *_purchase("RR-009", 5, STATIONERY),
        # 借方记了供应商档上的应付（冲销）：不算
        _line("RR-009", 5, OWN_PAYABLE, "0", 1.0, "7"), _line("RR-009", 5, REPAIR, "1", 1.0, "8"),
        _line("JV-999", 5, REPAIR, "0", 9.0),  # 不是进项单据的凭证
    ])
    return SimpleNamespace(root=root, acct=acct)


def _cfg(books):
    return SimpleNamespace(express_root=str(books.root), authorized_roots=[], account_set="", account_dir="")


def _params(books, **extra):
    return {"account_set": str(books.acct), "account_root": str(books.root), "since": SINCE.isoformat(), **extra}


def _read(books, **extra):
    result = account_history.read_history(_cfg(books), _params(books, **extra), sleep=lambda _s: None)
    return result, json.loads(gzip.decompress(result.content).decode("utf-8"))


def _state(folder: Path):
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(folder.iterdir()) if path.is_file()}


def test_counts_each_suppliers_usual_accounts_without_touching_the_books(books):
    before = _state(books.acct)
    result, data = _read(books)
    assert _state(books.acct) == before  # 只读：账套里每个文件都不变
    assert result.meta == {"format": "json.gz", "version": account_history.RESULT_VERSION, "suppliers": 2,
                           "documents": 5}
    assert (data["version"], data["since"], data["account_set"]) == (2, SINCE.isoformat(), "DAOWNUEA")
    assert data["read_at"].endswith("Z")
    assert data["excluded"] == sorted([CASH, INPUT_VAT, PAYABLE])
    suppliers = {row["code"]: row for row in data["suppliers"]}
    assert suppliers["S001"]["name"] == "ศรีกรุง เครื่องเขียน"
    assert (suppliers["S001"]["tax_id"], suppliers["S001"]["branch"]) == ("0105556012345", "00001")
    # 文具科目 3 张（RR-001 两行只算一张）、修理 1 张（现购 HP-003）；进项税、应付、现金、供应商档上的应付都不算；
    # 作废 / 软删 / 太早 / 凭证作废的单不算
    assert suppliers["S001"]["accounts"] == [
        {"code": STATIONERY, "docs": 3, "last": _days(5).isoformat()},
        {"code": REPAIR, "docs": 1, "last": _days(90).isoformat()}]
    assert suppliers["S001"]["documents"] == 4
    assert suppliers["S002"]["accounts"] == [{"code": POWER, "docs": 1, "last": _days(20).isoformat()}]
    assert data["diagnostics"]["missing_tables"] == []


def test_ties_put_the_most_recent_first_and_accounts_are_capped(books, monkeypatch):
    monkeypatch.setattr(account_history, "ACCOUNTS_PER_SUPPLIER", 1)
    _result, data = _read(books)
    assert [row["code"] for row in data["suppliers"][0]["accounts"]] == [STATIONERY]
    lines = [*_purchase("RR-101", 2, "53-99-00-00")]
    _append(books.acct / "APTRN.DBF", [_doc("RR-101", "S002", 2)])
    _append(books.acct / "GLJNL.DBF", [_head("RR-101", 2)])
    _append(books.acct / "GLJNLIT.DBF", lines)
    monkeypatch.setattr(account_history, "ACCOUNTS_PER_SUPPLIER", 5)
    _result, data = _read(books)
    s002 = next(row for row in data["suppliers"] if row["code"] == "S002")
    assert [row["code"] for row in s002["accounts"]] == ["53-99-00-00", POWER]  # 都是 1 张：最近用过的在前


def test_suppliers_are_capped_by_how_many_documents_they_have(books, monkeypatch):
    monkeypatch.setattr(account_history, "SUPPLIERS_MAX", 1)
    _result, data = _read(books)
    assert [row["code"] for row in data["suppliers"]] == ["S001"]


def test_only_account_sets_this_computer_may_push_to_and_a_sane_since(books, tmp_path):
    other = _synth_account.build(tmp_path / "ELSEWHERE")
    with pytest.raises(RequestFailed) as caught:
        account_history.read_history(_cfg(books), _params(books, account_set=str(other)), sleep=lambda _s: None)
    assert caught.value.code == "account_not_allowed"
    for since in ("", "2026-13-01", (TODAY + dt.timedelta(days=1)).isoformat(),
                  (TODAY - dt.timedelta(days=account_history.SINCE_DAYS_MAX + 1)).isoformat()):
        with pytest.raises(RequestFailed) as caught:
            account_history.read_history(_cfg(books), _params(books, since=since), sleep=lambda _s: None)
        assert caught.value.code == "params_invalid"


def test_missing_tables_and_settings(books):
    (books.acct / "ISINFO.DBF").unlink()
    _result, data = _read(books)
    assert data["excluded"] == [] and "ISINFO" in data["diagnostics"]["missing_tables"]
    # 没有账套设置时进项税 / 应付照样只在借方以外：进项税借方行这时会被算上（服务端只建议费用科目）
    s001 = next(row for row in data["suppliers"] if row["code"] == "S001")
    assert INPUT_VAT in {row["code"] for row in s001["accounts"]}
    (books.acct / "GLJNLIT.DBF").unlink()
    with pytest.raises(RequestFailed) as caught:
        _read(books)
    assert caught.value.code == "account_tables_missing"


def test_a_result_too_large_is_refused(books, monkeypatch):
    monkeypatch.setitem(MAX_BYTES, "account_history", 10)
    with pytest.raises(RequestFailed) as caught:
        _read(books)
    assert caught.value.code == "too_large"


def test_the_request_thread_knows_how_to_read_the_history():
    assert request_worker.HANDLERS["account_history"] is account_history.read_history
