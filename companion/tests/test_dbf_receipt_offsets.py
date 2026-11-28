# -*- coding: utf-8 -*-
"""收款单 RE 带抵减单（载荷 23，退货 / 减项进收款；评估 docs/work/2026-11-28-credit-note-assessment.md §1.2）。合成账套，
照 NLTTEST 原生：减项 RECTYP 5（例 RE2400820：IV 12620 − SR 6310 → TB 6310）、定金 RECTYP 0（AI，分几张 RE 部分抵）、
全抵净额 0（例 RE2400023：DOCSTAT N、无方式行、无凭证、ARBAL / ARMAS 不动）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from account_rules_fixture import seed
from companion import dbf_receipt as R
from companion import payload_contract
from test_dbf_receipt import AR, BBL_SA, CUSTOMER, DAY, _balance, _head, _journal, _payload, _re_bucket, _rows, _state, _write
from test_dbf_receipt import acct as _base  # noqa: F401  (fixture)


def _doc(docnum, rectyp, net, *, received=0.0, customer=CUSTOMER, status="N"):
    remaining = round(net - received, 2)
    return {"RECTYP": rectyp, "DOCNUM": docnum, "DOCDAT": dt.date(2026, 9, 1), "CUSCOD": customer, "SLMCOD": "NLTP4",
            "DEPCOD": "CPI", "NETAMT": net, "RCVAMT": received, "REMAMT": remaining, "DOCSTAT": status, "ORGNUM": 0,
            "CMPLAPP": "N", "CNTYP": "2" if rectyp == "5" else ""}


@pytest.fixture
def acct(_base):
    seed(_base, "ARTRN", [_doc("SR002400054", "5", 6310.0), _doc("AI2400002", "0", 9798.0),
                          _doc("SRC2", "5", 100.0, customer="C2"), _doc("SRVOID", "5", 50.0, status="C")])
    return _base


def _offsets(*rows):
    return [{"docnum": d, "rectyp": r, "amount": a} for d, r, a in rows]


def _pay(invoices, methods, offsets, cash="0.00"):
    return _payload(invoices=invoices, methods=methods, cash=cash, offsets=_offsets(*offsets),
                    payload_version=payload_contract.RECEIPT_OFFSETS_PAYLOAD_VERSION)


def _artrn(acct, docnum):
    return _rows(acct, "ARTRN", DOCNUM=docnum)[0]


def test_a_credit_note_takes_its_amount_off_like_native_re2400820(acct):
    res = _write(acct, _pay((("IVP42400239", "9972.00"),), (("BB", "3662.00"),), (("SR002400054", "5", "6310.00"),)))
    assert res.ok, res.error
    head = _head(acct, res.docnum)
    assert (head["NETAMT"], head["IVCAMT"], head["CHQRCV"], head["CHQPAS"], head["CSHRCV"], head["DOCSTAT"]) == \
        (3662.0, 3662.0, 3662.0, 3662.0, 0.0, "M")
    assert sorted((r["DOCNUM"].strip(), r["RECTYP"], r["RCVAMT"]) for r in _rows(acct, "ARRCPIT", RCPNUM=res.docnum)) == \
        [("IVP42400239", "3", 9972.0), ("SR002400054", "5", 6310.0)]
    sr = _artrn(acct, "SR002400054")
    assert (sr["RCVAMT"], sr["REMAMT"], sr["CMPLAPP"], sr["CMPLDAT"]) == (6310.0, 0.0, "Y", DAY)
    assert _journal(acct, res.docnum) == [(" 2", BBL_SA, "0", Decimal("3662.0"), "CPI"), (" 6", AR, "1", Decimal("3662.0"), "CPI")]
    assert _re_bucket(acct) == 3662.0 and _balance(acct) == 30000.0 - 3662.0
    assert {"docnum": "SR002400054", "remaining_after": "0.00"} in res.receipt["invoices"]


def test_a_full_offset_writes_no_voucher_and_leaves_the_balances_like_native_re2400023(acct):
    res = _write(acct, _pay((("IVP42400241", "1000.00"),), (), (("AI2400002", "0", "1000.00"),)))
    assert res.ok, res.error
    head = _head(acct, res.docnum)
    assert (head["NETAMT"], head["IVCAMT"], head["CHQRCV"], head["CSHRCV"], head["DOCSTAT"], head["CMPLAPP"]) == \
        (0.0, 0.0, 0.0, 0.0, "N", "Y")
    assert _rows(acct, "ARRCPCQ", RCPNUM=res.docnum) == []
    assert _rows(acct, "GLJNL", VOUCHER=res.docnum) == [] and _journal(acct, res.docnum) == []
    assert _balance(acct) == 30000.0
    ai = _artrn(acct, "AI2400002")  # 定金部分抵：余额减本次，没抵完不标完成（原生 AI2400002 分三张 RE 抵）
    assert (ai["RCVAMT"], ai["REMAMT"], ai["CMPLAPP"]) == (1000.0, 8798.0, "N")
    assert _rows(acct, "ISRUN", DOCTYP="RE")[0]["DOCNUM"].strip() == "2403081"


def test_the_same_full_offset_replayed_returns_the_first_number(acct):
    payload = _pay((("IVP42400241", "1000.00"),), (), (("AI2400002", "0", "1000.00"),))
    first = _write(acct, payload)
    before = _state(acct)
    again = _write(acct, payload)
    assert again.ok and again.skipped and again.docnum == first.docnum and _state(acct) == before


@pytest.mark.parametrize("docnum, rectyp, problem", [
    ("SRC2", "5", "other_customer"), ("SRVOID", "5", "not_found"), ("SR002400054", "0", "not_found"),
    ("NOPE", "5", "not_found")])
def test_an_offset_that_is_not_this_customers_open_one_stops_the_receipt(acct, docnum, rectyp, problem):
    before = _state(acct)
    res = _write(acct, _pay((("IVP42400239", "9972.00"),), (("BB", "9872.00"),), ((docnum, rectyp, "100.00"),)))
    assert not res.ok and res.error_code == R.ERR_INVOICE_NOT_FOUND
    assert _state(acct) == before


def test_an_offset_larger_than_its_balance_is_already_collected(acct):
    res = _write(acct, _pay((("IVP42400239", "9972.00"),), (("BB", "2972.00"),), (("SR002400054", "5", "7000.00"),)))
    assert not res.ok and res.error_code == R.ERR_ALREADY_COLLECTED


@pytest.mark.parametrize("over", [
    {"offsets": [{"docnum": "SR002400054", "rectyp": "3", "amount": "10.00"}]},          # 发票不当抵减行
    {"offsets": [{"docnum": "IVP42400239", "rectyp": "5", "amount": "10.00"}]},          # 同一张两处
    {"offsets": [{"docnum": "SR002400054", "rectyp": "5", "amount": "0.00"}]},
    {"offsets": "SR002400054"},
])
def test_bad_offsets_write_nothing(acct, over):
    before = _state(acct)
    res = _write(acct, {**_payload(), **over})
    assert not res.ok and res.error_code == R.ERR_VALIDATION and _state(acct) == before


def test_offsets_larger_than_the_invoices_are_refused_until_the_native_recording(acct):
    before = _state(acct)
    res = _write(acct, _pay((("IVP42400241", "1000.00"),), (), (("SR002400054", "5", "6310.00"),), cash="-5310.00"))
    assert not res.ok and res.error_code == R.ERR_VALIDATION and _state(acct) == before


def test_a_full_offset_with_a_method_line_is_refused(acct):
    res = _write(acct, _pay((("IVP42400241", "1000.00"),), (("BB", "0.00"),), (("AI2400002", "0", "1000.00"),)))
    assert not res.ok and res.error_code == R.ERR_VALIDATION
