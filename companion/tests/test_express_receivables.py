# -*- coding: utf-8 -*-
"""收款单 RE（载荷 20）读的那一半（评估 2026-11-11「读：主档补充」「读：新按需种类」）：

- 主档 ``express_masters`` 多给 ``receipt_types``（ISRUN ZR 逐行，科目按 SHORTNAM→BKMAS 否则 ACCNUM01，同前缀科目不同标 conflict）、
  ``receipt_run``（RE 单据簿）、``salesmen``（OESLM）、``cash_account``（ISINFO.ACCNUM00）；
- ``express_marks`` 另报 ``ar_mark``（单据表 ARTRN 变了才变）；
- 新按需种类 ``express_ar_open``：未删、RECTYP 3、没作废、余额 > 0、不是服务品现金制增值税（SRV_VATTYP 2）的发票，可只读一个客户；只读、复制到本机读，账套一个字节不动。"""

from __future__ import annotations

import datetime as dt
import gzip
import json

import pytest

from account_rules_fixture import seed
from companion import express_masters, express_receivables, request_worker
from companion.request_contract import KINDS, LEASE_SECONDS, MAX_BYTES, RequestFailed
from test_express_masters import _append, _cfg, _delete, _params, _read, _state, _table, books  # noqa: F401

_ISINFO_CASH = "THINAM C(60); MAINLOC C(4); NEGALLOW C(1); COSMTD C(1); USEDEP C(1); ACCNUM00 C(15)"


@pytest.fixture
def receipts(books):
    acct = books.acct
    (acct / "ISINFO.DBF").unlink()
    _table(acct / "ISINFO.DBF", _ISINFO_CASH, [{"THINAM": "ดาวเหนือ", "MAINLOC": "01", "ACCNUM00": "1111-00"}])
    seed(acct, "ISRUN", [
        {"DOCTYP": "RE", "PREFIX": "RE", "DOCNUM": "2403080", "DEPCOD": "CPI", "JNLTYP": "02"},
        {"DOCTYP": "ZR", "DOCCOD": "05", "PREFIX": "BB", "ACCNUM01": "1113-01", "POSDES": "เงินโอน BBL", "POSDES2": "BBL"},
        {"DOCTYP": "ZR", "DOCCOD": "12", "PREFIX": "TB", "SHORTNAM": "B1", "POSDES": "เงินโอน BBL/กระแส"},
        {"DOCTYP": "ZR", "DOCCOD": "03", "PREFIX": "TR", "SHORTNAM": "S1", "ACCNUM01": "1113-09", "POSDES": "SCB"},
        {"DOCTYP": "ZR", "DOCCOD": "40", "PREFIX": "NA", "POSDES": "ไม่มีบัญชี"},
        {"DOCTYP": "ZR", "DOCCOD": "41", "PREFIX": "XX", "ACCNUM01": "2136-02"},
        {"DOCTYP": "ZR", "DOCCOD": "42", "PREFIX": "XX", "ACCNUM01": "2132-03"},
        {"DOCTYP": "ZR", "DOCCOD": "43", "PREFIX": "GO", "ACCNUM01": "1111-00"},
    ])
    _delete(acct / "ISRUN.DBF", 7)  # 软删的类型不给
    seed(acct, "BKMAS", [{"BNKACC": "B1", "ACCNUM": "1112-01"}, {"BNKACC": "S1", "ACCNUM": "1113-02"}])
    seed(acct, "OESLM", [{"SLMCOD": "NLTP4", "SLMNAM": "อาคม\xa0083"}, {"SLMCOD": "OLD1", "SLMNAM": "ลาออก", "STATUS": "I"},
                         {"SLMCOD": "OLD2", "SLMNAM": "พัก", "INACTDAT": dt.date(2026, 1, 1)},
                         {"SLMCOD": "ACT", "SLMNAM": "ใช้งาน", "STATUS": "A"}])
    seed(acct, "ARTRN", [
        _inv("IV001", "C01", 9972.0, 0.0), _inv("IV002", "C01", 185347.0, 155351.0), _inv("IV003", "C01", 500.0, 500.0),
        _inv("IV004", "C09", 120.5, 0.0), {**_inv("IV005", "C01", 50.0, 0.0), "DOCSTAT": "C"},
        {**_inv("IV006", "C01", 70.0, 0.0), "RECTYP": "1"}, _inv("IV007", "C01", 10.0, 0.0),
        {**_inv("IV008", "C01", 107.0, 0.0), "SRV_VATTYP": "2"},  # 服务品现金制增值税：本批不收，不列
        {"RECTYP": "9", "DOCNUM": "RE001", "CUSCOD": "C01", "REMAMT": 5.0},
    ])
    _delete(acct / "ARTRN.DBF", 6)  # IV007 软删
    return books


def _open(books, params=None):
    result = express_receivables.read_ar_open(_cfg(books), params or _params(books), sleep=lambda _s: None)
    return result, json.loads(gzip.decompress(result.content).decode("utf-8"))


def test_the_masters_carry_receipt_types_book_salesmen_and_cash_account(receipts):
    _result, data = _read(receipts)
    assert data["receipt_types"] == [
        {"code": "05", "prefix": "BB", "name": "เงินโอน BBL", "name_en": "BBL", "bank": "", "account": "1113-01", "account_issue": ""},
        {"code": "12", "prefix": "TB", "name": "เงินโอน BBL/กระแส", "name_en": "", "bank": "B1", "account": "1112-01",
         "account_issue": ""},
        {"code": "03", "prefix": "TR", "name": "SCB", "name_en": "", "bank": "S1", "account": "", "account_issue": "conflict"},
        {"code": "40", "prefix": "NA", "name": "ไม่มีบัญชี", "name_en": "", "bank": "", "account": "", "account_issue": "missing"},
        {"code": "41", "prefix": "XX", "name": "", "name_en": "", "bank": "", "account": "", "account_issue": "conflict"},
        {"code": "42", "prefix": "XX", "name": "", "name_en": "", "bank": "", "account": "", "account_issue": "conflict"},
    ]
    assert data["receipt_run"] == {"prefix": "RE", "next": "2403080", "department": "CPI"}
    assert data["salesmen"] == [{"code": "NLTP4", "name": "อาคม 083", "inactive": False},
                                {"code": "OLD1", "name": "ลาออก", "inactive": True},
                                {"code": "OLD2", "name": "พัก", "inactive": True},
                                {"code": "ACT", "name": "ใช้งาน", "inactive": False}]  # 状态 A 是在用（同 ARMAS 的记法）
    assert data["cash_account"] == "1111-00"


def test_open_invoices_are_live_unpaid_invoices_and_the_books_stay_untouched(receipts):
    before = _state(receipts.acct)
    result, data = _open(receipts)
    assert _state(receipts.acct) == before
    assert list(data) == ["version", "account_set", "ar_mark", "read_at", "items"]
    assert data["ar_mark"] == express_masters.mark(receipts.acct, express_masters.AR_MARK_TABLES)
    assert data["items"] == [
        {"docnum": "IV001", "rectyp": "3", "docdat": "2026-08-22", "customer": "C01", "salesman": "NLTP4", "department": "CPI",
         "net": "9972.00", "received": "0.00", "remaining": "9972.00"},
        {"docnum": "IV002", "rectyp": "3", "docdat": "2026-08-22", "customer": "C01", "salesman": "NLTP4", "department": "CPI",
         "net": "185347.00", "received": "155351.00", "remaining": "29996.00"},
        {"docnum": "IV004", "rectyp": "3", "docdat": "2026-08-22", "customer": "C09", "salesman": "NLTP4", "department": "CPI",
         "net": "120.50", "received": "0.00", "remaining": "120.50"},
    ]
    assert result.meta["counts"] == {"items": 3} and result.meta["format"] == "json.gz"


def test_one_customer_can_be_read_alone(receipts):
    _result, data = _open(receipts, {**_params(receipts), "customer": "C09"})
    assert [row["docnum"] for row in data["items"]] == ["IV004"] and data["customer"] == "C09"


@pytest.mark.parametrize("extra", [{"since": "x"}, {"customer": 5}, {"customer": "X" * 11}])
def test_bad_params_are_refused(receipts, extra):
    with pytest.raises(RequestFailed) as error:
        _open(receipts, {**_params(receipts), **extra})
    assert error.value.code == "params_invalid"


def test_a_result_too_large_is_refused_not_cut(receipts, monkeypatch):
    monkeypatch.setitem(express_receivables.MAX_BYTES, "express_ar_open", 10)
    with pytest.raises(RequestFailed) as error:
        _open(receipts)
    assert error.value.code == "too_large"


def test_the_ar_mark_moves_with_the_invoice_table_only(receipts):
    params = _params(receipts)
    marks = json.loads(gzip.decompress(express_masters.read_marks(_cfg(receipts), params).content))
    assert set(marks) == {"mark", "ar_mark"}
    _append(receipts.acct / "ARTRN.DBF", [{"RECTYP": "3", "DOCNUM": "IV100", "CUSCOD": "C01", "REMAMT": 1.0}])
    again = json.loads(gzip.decompress(express_masters.read_marks(_cfg(receipts), params).content))
    assert again["ar_mark"] != marks["ar_mark"] and again["mark"] == marks["mark"]  # 写一张单不触发整套主档重读
    _append(receipts.acct / "OESLM.DBF", [{"SLMCOD": "NEW", "SLMNAM": "ใหม่"}])
    assert express_masters.mark(receipts.acct) != marks["mark"]  # 销售员变了主档要重读


def test_the_request_thread_knows_the_new_kind():
    assert "express_ar_open" in KINDS
    assert request_worker.HANDLERS["express_ar_open"] is express_receivables.read_ar_open
    assert MAX_BYTES["express_ar_open"] == 2 * 1024 * 1024 and LEASE_SECONDS["express_ar_open"] == 180


def _inv(docnum, customer, net, received):
    return {"RECTYP": "3", "DOCNUM": docnum, "DOCDAT": dt.date(2026, 8, 22), "CUSCOD": customer, "SLMCOD": "NLTP4",
            "DEPCOD": "CPI", "NETAMT": net, "RCVAMT": received, "REMAMT": round(net - received, 2), "DOCSTAT": "N"}
