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
from companion import express_masters, express_receivables, payment_methods, request_worker
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
        {"code": "05", "prefix": "BB", "name": "เงินโอน BBL", "name_en": "BBL", "bank": "", "account": "1113-01", "account_issue": "",
         "uses": 0},
        {"code": "12", "prefix": "TB", "name": "เงินโอน BBL/กระแส", "name_en": "", "bank": "B1", "account": "1112-01",
         "account_issue": "", "uses": 0,
         "bank_account": {"code": "B1", "bank": "", "short": "", "number": "", "branch": "", "via": "shortnam"}},
        {"code": "03", "prefix": "TR", "name": "SCB", "name_en": "", "bank": "S1", "account": "", "account_issue": "conflict",
         "uses": 0},
        {"code": "40", "prefix": "NA", "name": "ไม่มีบัญชี", "name_en": "", "bank": "", "account": "", "account_issue": "missing",
         "uses": 0},
        {"code": "41", "prefix": "XX", "name": "", "name_en": "", "bank": "", "account": "", "account_issue": "conflict", "uses": 0},
        {"code": "42", "prefix": "XX", "name": "", "name_en": "", "bank": "", "account": "", "account_issue": "conflict", "uses": 0},
    ]
    assert data["receipt_run"] == {"prefix": "RE", "next": "2403080", "department": "CPI"}
    assert data["salesmen"] == [{"code": "NLTP4", "name": "อาคม 083", "inactive": False},
                                {"code": "OLD1", "name": "ลาออก", "inactive": True},
                                {"code": "OLD2", "name": "พัก", "inactive": True},
                                {"code": "ACT", "name": "ใช้งาน", "inactive": False}]  # 状态 A 是在用（同 ARMAS 的记法）
    assert data["cash_account"] == "1111-00"


def test_receipt_types_carry_their_bank_account_and_how_often_native_receipts_used_them(receipts):
    """11-13 户头标签（接口见共享笔记「收款-LINE侧-交接-11-13.md」§4）：方式经 SHORTNAM 指到户头 → via shortnam；只填了科目、
    科目恰好是唯一一个户头的 → via account；几个户头同科目、方式有问题的不带。uses = 原生 RE 的方式行按前缀计（别的单据簿的行不算），
    只在同一户头有两种以上方式时才数（这里 TB 与 BK 都指 B1）。"""
    acct = receipts.acct
    (acct / "BKMAS.DBF").unlink()
    _table(acct / "BKMAS.DBF", "BNKACC C(2); BNKNAM C(50); BRANCH C(30); SHORTNAM C(10); BNKNUM C(12); ACCNUM C(15)", [
        {"BNKACC": "B1", "BNKNAM": "ธ.กรุงเทพ/กระแสรายวัน", "BRANCH": "สวนสยาม", "SHORTNAM": "BBL/CA", "BNKNUM": "1923045866",
         "ACCNUM": "1112-01"},
        {"BNKACC": "B2", "BNKNAM": "ธ.กรุงเทพ/ออมทรัพย์", "SHORTNAM": "BBL/SA", "BNKNUM": "1924226366", "ACCNUM": "1113-01"},
        {"BNKACC": "S1", "BNKNAM": "SCB", "ACCNUM": "1113-02"}])
    _append(acct / "ISRUN.DBF", [{"DOCTYP": "ZR", "DOCCOD": "60", "PREFIX": "TW", "ACCNUM01": "1113-02", "POSDES": "same as S1"},
                                 {"DOCTYP": "ZR", "DOCCOD": "61", "PREFIX": "DU", "ACCNUM01": "1113-07"},
                                 {"DOCTYP": "ZR", "DOCCOD": "62", "PREFIX": "BK", "SHORTNAM": "B1", "POSDES": "BBL/CA 2"}])
    _append(acct / "BKMAS.DBF", [{"BNKACC": "S9", "ACCNUM": "1113-02"}])  # S1、S9 同科目：只填科目的 TW 认不出是哪个户头
    seed(acct, "ARRCPCQ", [{"RCPNUM": "RE2403001", "CHQNUM": "TBRE2403001", "RCVAMT": 1.0},
                           {"RCPNUM": "RE2403002", "CHQNUM": "TBRE2403002", "RCVAMT": 2.0},
                           {"RCPNUM": "RE2403002", "CHQNUM": "BBRE2403002", "RCVAMT": 3.0},
                           {"RCPNUM": "HS0000001", "CHQNUM": "TBHS0000001", "RCVAMT": 4.0}])
    _result, data = _read(receipts)
    rows = {row["prefix"]: row for row in data["receipt_types"]}
    assert rows["TB"]["bank_account"] == {"code": "B1", "bank": "ธ.กรุงเทพ/กระแสรายวัน", "short": "BBL/CA", "number": "1923045866",
                                          "branch": "สวนสยาม", "via": "shortnam"}
    assert rows["BB"]["bank_account"] == {"code": "B2", "bank": "ธ.กรุงเทพ/ออมทรัพย์", "short": "BBL/SA", "number": "1924226366",
                                          "branch": "", "via": "account"}
    assert all("bank_account" not in rows[prefix] for prefix in ("TR", "NA", "XX", "TW", "DU"))
    assert {prefix: row["uses"] for prefix, row in rows.items()} == {"BB": 1, "TB": 2, "TR": 0, "NA": 0, "XX": 0, "TW": 0, "DU": 0,
                                                                       "BK": 0}
    assert rows["BK"]["bank_account"]["code"] == "B1"


def test_uses_are_not_counted_when_no_bank_account_has_two_methods(receipts):
    seed(receipts.acct, "ARRCPCQ", [{"RCPNUM": "RE2403001", "CHQNUM": "TBRE2403001", "RCVAMT": 1.0}])
    _result, data = _read(receipts)
    assert [row["uses"] for row in data["receipt_types"]] == [0] * len(data["receipt_types"])


def test_receipt_types_carry_the_account_class_from_the_books_own_chart(receipts):
    """11-12 按类放宽：每种带科目类别（``account_class``），按账套自己的科目表结构判，名称不参与（统计
    docs/work/2026-11-12-collection-transfer-rule-stats.md）。现金组 = 现金科目往上第一个底下有 BKMAS 科目的上级；组里挂在下级
    分组下的（不在现金那一支）、或在 BKMAS 里的是存款；BKMAS 里组外的（借款、退票应收）不是。"""
    acct = receipts.acct
    _append(acct / "GLACC.DBF", [{"ACCNUM": num, "ACCNAM": num, "PARENT": parent, "GROUP": num[0], "ACCTYP": "0", "STATUS": "A"}
                                 for num, parent in (
        ("1100-00", ""), ("1110-00", "1100-00"), ("1111-00", "1110-00"), ("1111-50", "1110-00"), ("1112-00", "1110-00"),
        ("1112-01", "1112-00"), ("1113-00", "1110-00"), ("1113-01", "1113-00"), ("1113-02", "1113-00"), ("1113-09", "1113-00"),
        ("1210-01", "1100-00"), ("2300-08", "2300-00"), ("2300-00", ""), ("2136-02", ""), ("2132-03", ""))])
    _append(acct / "BKMAS.DBF", [{"BNKACC": "L1", "ACCNUM": "2300-08"}, {"BNKACC": "CH", "ACCNUM": "1111-00"}])
    _append(acct / "ISRUN.DBF", [
        {"DOCTYP": "ZR", "DOCCOD": "02", "PREFIX": "CH", "ACCNUM01": "1111-00", "POSDES": "รับเงินสด"},
        {"DOCTYP": "ZR", "DOCCOD": "29", "PREFIX": "CC", "ACCNUM01": "1111-50", "POSDES": "เงินสดย่อย"},
        {"DOCTYP": "ZR", "DOCCOD": "50", "PREFIX": "LN", "SHORTNAM": "L1", "POSDES": "เงินโอนเข้าบัญชี TMB"},
        {"DOCTYP": "ZR", "DOCCOD": "51", "PREFIX": "GZ", "ACCNUM01": "1210-01", "POSDES": "เงินโอนเข้าบัญชี GIZE"},
    ])
    _result, data = _read(receipts)
    assert {row["prefix"]: row["account_class"] for row in data["receipt_types"]} == {
        "BB": "bank", "TB": "bank", "TR": "", "NA": "", "XX": "", "CH": "cash", "CC": "cash_group", "LN": "other",
        "GZ": "other"}
    assert set(payment_methods.ACCOUNT_CLASSES) == {"bank", "cash_group", "cash", "other"}


def test_no_account_class_when_the_chart_cannot_place_the_cash_account(receipts):
    # 现金科目不在科目表里（或没有现金科目、没有 PARENT 栏）判不出结构：不报这一栏，服务端照旧按名称判
    _result, data = _read(receipts)
    assert all("account_class" not in row for row in data["receipt_types"])
    assert payment_methods.account_classes(receipts.acct) is None


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
