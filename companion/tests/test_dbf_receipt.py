# -*- coding: utf-8 -*-
"""收款单 RE（รับชำระหนี้）写进 Express（载荷 20，dbf_receipt；评估 docs/work/2026-11-11-collection-receipt-assessment.md）。合成账套。

逐字段对照 NLTTEST 原生 RE（1.0.1.94）：单头 ARTRN（RECTYP 9、DOCSTAT M、IVCAMT = NETAMT、CSHRCV、CHQRCV = CHQPAS、BILNUM ~、SRV_VATTYP -、
ORGNUM 同第一张发票）、ARRCPIT（RECTYP 3）、ARRCPCQ（前缀 + 单号截 12 字）、被收发票（RCVAMT / REMAMT / CMPLAPP / CMPLDAT）、凭证
（JNLTYP 02、摘要按单据簿 JNLEXP、SEQIT 2 借现金 / 各方式、SEQIT 6 贷应收）、ISRUN 下一个号、ARBAL RE 桶、ARMAS.BALANCE。覆盖：纯现金、单一转账、
现金 + 转账、带折扣（DC 行）、部分收款两张发票、余额不够整单不写（已被收清）、发票不在 / 别的客户、幂等重放回原号、类型无科目 / 冲突 / 不在、
现金科目缺失、借贷平、读回不符与中途失败都整单回滚、单号跳过已用的、载荷校验与旧版拒收、worker 按方向分流。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import dbf
import pytest

import _synth_account
from account_rules_fixture import isinfo, seed
from companion import dbf_receipt as R
from companion import dbf_schema as S
from companion import fiscal_period, payload_contract, payment_methods

CASH, BBL_SA, BBL_CA, DISCOUNT, AR, FEE = "1111-00", "1113-01", "1112-01", "4100-04", "1130-01", "5390-01"
CUSTOMER = "2100883"
DAY = dt.date(2026, 11, 10)
NLT_JNLEXP = "'รับชำระหนี้   ' + (ARMAS->PRENAM - '\xa0' - ARMAS->CUSNAM)"


def _rows(acct: Path, name: str, **match):
    table = dbf.Table(str(acct / f"{name}.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        return [{field: row[field] for field in table.field_names} for row in S.live_rows(table)
                if all(str(row[k] or "").strip() == v for k, v in match.items())]
    finally:
        table.close()


def _state(folder: Path):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(folder.iterdir())
            if path.is_file() and path.suffix.upper() == ".DBF"}


def _glacc(acct, *accounts):
    seed(acct, "GLACC", [{"ACCNUM": num, "ACCNAM": num, "ACCTYP": "0", "USEDEP": "Y"} for num in accounts])


@pytest.fixture
def acct(tmp_path):
    acct = _synth_account.build(tmp_path / "69EXP" / "NLTTEST")
    isinfo(acct, ACCNUM00=CASH, ACCNUM02=AR)
    _glacc(acct, CASH, BBL_SA, BBL_CA, DISCOUNT, AR, FEE)
    seed(acct, "ISRUN", [
        {"DOCTYP": "RE", "DOCCOD": "", "PREFIX": "RE", "DOCNUM": "2403080", "DEPCOD": "CPI", "JNLTYP": "02",
         "JNLEXP": NLT_JNLEXP, "POSDES": "รับชำระหนี้"},
        {"DOCTYP": "ZR", "DOCCOD": "02", "PREFIX": "CH", "ACCNUM01": CASH, "POSDES": "รับเงินสด"},
        {"DOCTYP": "ZR", "DOCCOD": "05", "PREFIX": "BB", "ACCNUM01": BBL_SA, "POSDES": "เงินโอนเข้าบัญชี BBL/ออมทรัพย์"},
        {"DOCTYP": "ZR", "DOCCOD": "12", "PREFIX": "TB", "SHORTNAM": "B1", "POSDES": "เงินโอนเข้าบัญชี BBL/กระแส"},
        {"DOCTYP": "ZR", "DOCCOD": "09", "PREFIX": "DC", "ACCNUM01": DISCOUNT, "POSDES": "ส่วนลดเงินสด"},
        {"DOCTYP": "ZR", "DOCCOD": "04", "PREFIX": "TF", "ACCNUM01": FEE, "POSDES": "ค่าธรรมเนียมการโอน"},
        {"DOCTYP": "ZR", "DOCCOD": "40", "PREFIX": "NA", "POSDES": "ไม่มีบัญชี"},              # 两处都没有科目
        {"DOCTYP": "ZR", "DOCCOD": "22", "PREFIX": "SA", "ACCNUM01": "2132-03", "POSDES": "หัก 3%"},
        {"DOCTYP": "ZR", "DOCCOD": "22", "PREFIX": "22", "ACCNUM01": "2136-02", "POSDES": "Credit Card"},  # DOCCOD 重复
        {"DOCTYP": "ZR", "DOCCOD": "41", "PREFIX": "XX", "ACCNUM01": "2136-02", "POSDES": "conflict 1"},
        {"DOCTYP": "ZR", "DOCCOD": "42", "PREFIX": "XX", "ACCNUM01": "2132-03", "POSDES": "conflict 2"},  # 同前缀科目不同
        {"DOCTYP": "ZR", "DOCCOD": "01", "PREFIX": "QR", "ACCNUM01": "1130-40", "POSDES": "เช็ครับ"},
        {"DOCTYP": "ZR", "DOCCOD": "01", "PREFIX": "QR", "ACCNUM01": "1130-40", "POSDES": "เช็ครับ"},  # 重复两行内容相同
        {"DOCTYP": "IV", "PREFIX": "IV", "DOCNUM": "0000100", "JNLTYP": "03"},
    ])
    seed(acct, "BKMAS", [{"BNKACC": "B1", "BNKNAM": "ธ.กรุงเทพ/กระแสรายวัน", "ACCNUM": BBL_CA}])
    seed(acct, "ARMAS", [
        {"CUSCOD": CUSTOMER, "PRENAM": "", "CUSNAM": "ร้านส.เจริญสินพานิช", "ACCNUM": AR, "BALANCE": 30000.0},
        {"CUSCOD": "C2", "PRENAM": "บริษัท", "CUSNAM": "ซี.พี.ฟู้ดสโตร์\xa0จำกัด", "ACCNUM": AR, "BALANCE": 500.0},
    ])
    seed(acct, "ARTRN", [
        _invoice("IVP42400239", 9972.0),
        _invoice("IVP42400240", 185347.0, received=155351.0),
        _invoice("IVP42400241", 1000.0, orgnum=0),
        _invoice("IVPB2400376", 10002.24),
        _invoice("IVC2000001", 500.0, customer="C2"),
        _invoice("IVPAID", 300.0, received=300.0),
    ])
    seed(acct, "ARBAL", [{"CUSCOD": CUSTOMER, "IV8": 100.0}])
    return acct


def _invoice(docnum, net, *, received=0.0, customer=CUSTOMER, orgnum=1):
    remaining = round(net - received, 2)
    return {"RECTYP": "3", "DOCNUM": docnum, "DOCDAT": dt.date(2026, 8, 22), "CUSCOD": customer, "SLMCOD": "NLTP4",
            "DEPCOD": "CPI", "NETAMT": net, "RCVAMT": received, "REMAMT": remaining, "DOCSTAT": "N", "ORGNUM": orgnum,
            "CMPLAPP": "Y" if remaining == 0 else "N", "CMPLDAT": dt.date(2026, 8, 30) if remaining == 0 else None}


def _payload(invoices=(("IVP42400239", "9972.00"),), cash="0.00", methods=(("BB", "9972.00"),), **over):
    payload = {"direction": "receipt", "payload_version": 20, "account_set": "NLTTEST", "ref_no": "ERC-0001",
               "company": {"tax_id": "0105555000001", "branch": "00000"}, "doc_date": DAY.isoformat(),
               "customer": CUSTOMER, "salesman": "", "department": "",
               "invoices": [{"docnum": d, "amount": a} for d, a in invoices], "cash": cash,
               "methods": [{"code": "", "prefix": p, "amount": a} for p, a in methods], "user": "somchai"}
    payload.update(over)
    return payload


def _write(acct, payload, **kw):
    return R.write_receipt(payload, acct, allowed_sets=("NLTTEST",), selected_dir=str(acct),
                           make_backup=kw.pop("make_backup", True), **kw)


def _journal(acct, voucher):
    return [(row["SEQIT"], row["ACCNUM"].strip(), row["TRNTYP"].strip(), Decimal(str(row["AMOUNT"])), row["DEPCOD"].strip())
            for row in _rows(acct, "GLJNLIT", VOUCHER=voucher)]


def _head(acct, docnum):
    rows = _rows(acct, "ARTRN", DOCNUM=docnum, RECTYP="9")
    assert len(rows) == 1
    return rows[0]


def _inv(acct, docnum):
    return _rows(acct, "ARTRN", DOCNUM=docnum, RECTYP="3")[0]


def _re_bucket(acct, customer=CUSTOMER):
    field = fiscal_period.resolve_month_field(acct, DAY, "RE")[0]
    return _rows(acct, "ARBAL", CUSCOD=customer)[0][field]


def _balance(acct, customer=CUSTOMER):
    return _rows(acct, "ARMAS", CUSCOD=customer)[0]["BALANCE"]


# ── 五种原生场景 ──


def test_single_transfer_writes_like_native_re2403079(acct):
    res = _write(acct, _payload())
    assert res.ok, res.error
    assert res.docnum == "RE2403080" and not res.skipped
    head = _head(acct, "RE2403080")
    assert {k: (v.strip() if isinstance(v, str) else v) for k, v in head.items() if k in (
        "RECTYP", "DOCDAT", "DEPCOD", "SLMCOD", "CUSCOD", "YOUREF", "DUEDAT", "BILNUM", "CMPLAPP", "CMPLDAT", "DOCSTAT",
        "SRV_VATTYP", "ORGNUM", "USERID")} == {
        "RECTYP": "9", "DOCDAT": DAY, "DEPCOD": "CPI", "SLMCOD": "NLTP4", "CUSCOD": CUSTOMER, "YOUREF": "ERC-0001",
        "DUEDAT": DAY, "BILNUM": "~", "CMPLAPP": "Y", "CMPLDAT": DAY, "DOCSTAT": "M", "SRV_VATTYP": "-", "ORGNUM": 1,
        "USERID": "somchai"}
    assert (head["NETAMT"], head["IVCAMT"], head["CSHRCV"], head["CHQRCV"], head["CHQPAS"], head["INTRCV"]) == \
        (9972.0, 9972.0, 0.0, 9972.0, 9972.0, 0.0)
    assert [(r["DOCNUM"].strip(), r["RECTYP"], r["RCVAMT"], r["VATAMT"]) for r in _rows(acct, "ARRCPIT", RCPNUM="RE2403080")] \
        == [("IVP42400239", "3", 9972.0, 0.0)]
    assert [(r["CHQNUM"].strip(), r["RCVAMT"]) for r in _rows(acct, "ARRCPCQ", RCPNUM="RE2403080")] == [("BBRE2403080", 9972.0)]
    inv = _inv(acct, "IVP42400239")
    assert (inv["RCVAMT"], inv["REMAMT"], inv["CMPLAPP"], inv["CMPLDAT"]) == (9972.0, 0.0, "Y", DAY)
    assert _journal(acct, "RE2403080") == [(" 2", BBL_SA, "0", Decimal("9972.0"), "CPI"),
                                           (" 6", AR, "1", Decimal("9972.0"), "CPI")]
    gl = _rows(acct, "GLJNL", VOUCHER="RE2403080")
    assert [(r["JNLTYP"], r["DESCRP"].rstrip(), r["TRNSTAT"], r["DOCSTAT"]) for r in gl] == \
        [("02", "รับชำระหนี้   \xa0ร้านส.เจริญสินพานิช", "P", "N")]
    assert _rows(acct, "ISRUN", DOCTYP="RE")[0]["DOCNUM"].strip() == "2403081"
    assert _re_bucket(acct) == 9972.0
    assert _balance(acct) == 30000.0 - 9972.0
    assert res.receipt == {"docnum": "RE2403080", "invoices": [{"docnum": "IVP42400239", "remaining_after": "0.00"}]}


def test_pure_cash_goes_to_the_header_only_and_debits_the_book_cash_account(acct):
    res = _write(acct, _payload(cash="9972.00", methods=()))
    assert res.ok, res.error
    head = _head(acct, res.docnum)
    assert (head["CSHRCV"], head["CHQRCV"], head["CHQPAS"], head["IVCAMT"]) == (9972.0, 0.0, 0.0, 9972.0)
    assert _rows(acct, "ARRCPCQ", RCPNUM=res.docnum) == []
    assert _journal(acct, res.docnum) == [(" 2", CASH, "0", Decimal("9972.0"), "CPI"), (" 6", AR, "1", Decimal("9972.0"), "CPI")]


def test_cash_plus_transfer(acct):
    res = _write(acct, _payload(cash="972.00", methods=(("BB", "9000.00"),)))
    assert res.ok, res.error
    head = _head(acct, res.docnum)
    assert (head["CSHRCV"], head["CHQRCV"], head["CHQPAS"], head["IVCAMT"], head["NETAMT"]) == (972.0, 9000.0, 9000.0, 9972.0, 9972.0)
    assert _journal(acct, res.docnum) == [(" 2", CASH, "0", Decimal("972.0"), "CPI"), (" 2", BBL_SA, "0", Decimal("9000.0"), "CPI"),
                                          (" 6", AR, "1", Decimal("9972.0"), "CPI")]


def test_discount_is_a_dc_line_and_a_type_without_its_own_account_uses_the_bank(acct):
    """原生 RE2402778：TB 10002 + DC 0.24。TB 自己的科目栏空，经 SHORTNAM B1 → BKMAS → 1112-01；DISCAMT 不写。"""
    res = _write(acct, _payload(invoices=(("IVPB2400376", "10002.24"),), methods=(("TB", "10002.00"), ("DC", "0.24"))))
    assert res.ok, res.error
    head = _head(acct, res.docnum)
    assert (head["CHQRCV"], head["CHQPAS"], head["IVCAMT"], head["CSHRCV"]) == (10002.24, 10002.24, 10002.24, 0.0)
    assert [(r["CHQNUM"].strip(), r["RCVAMT"]) for r in _rows(acct, "ARRCPCQ", RCPNUM=res.docnum)] == \
        [("TBRE2403080", 10002.0), ("DCRE2403080", 0.24)]
    assert _journal(acct, res.docnum) == [(" 2", BBL_CA, "0", Decimal("10002.0"), "CPI"),
                                          (" 2", DISCOUNT, "0", Decimal("0.24"), "CPI"),
                                          (" 6", AR, "1", Decimal("10002.24"), "CPI")]
    assert _re_bucket(acct) == 10002.24 and _balance(acct) == pytest.approx(30000.0 - 10002.24)


def test_partial_payment_on_two_invoices(acct):
    """原生 RE2401367.1：部分收款发票 RCVAMT += 本次、REMAMT -= 本次、CMPLAPP 仍 N、CMPLDAT 空；另一张收清标完成。"""
    res = _write(acct, _payload(invoices=(("IVP42400240", "2001.00"), ("IVP42400241", "1000.00")), cash="3001.00",
                                methods=()))
    assert res.ok, res.error
    part = _inv(acct, "IVP42400240")
    assert (part["RCVAMT"], part["REMAMT"], part["CMPLAPP"], part["CMPLDAT"]) == (157352.0, 27995.0, "N", None)
    full = _inv(acct, "IVP42400241")
    assert (full["RCVAMT"], full["REMAMT"], full["CMPLAPP"], full["CMPLDAT"]) == (1000.0, 0.0, "Y", DAY)
    assert sorted((r["DOCNUM"].strip(), r["RCVAMT"]) for r in _rows(acct, "ARRCPIT", RCPNUM=res.docnum)) == \
        [("IVP42400240", 2001.0), ("IVP42400241", 1000.0)]
    assert _head(acct, res.docnum)["IVCAMT"] == 3001.0
    assert res.receipt["invoices"] == [{"docnum": "IVP42400240", "remaining_after": "27995.00"},
                                       {"docnum": "IVP42400241", "remaining_after": "0.00"}]


def test_journal_text_follows_the_book_expression_with_a_prename(acct):
    seed(acct, "ARTRN", [_invoice("IVC2000002", 100.0, customer="C2")])
    res = _write(acct, _payload(customer="C2", invoices=(("IVC2000002", "100.00"),), cash="100.00", methods=()))
    assert res.ok, res.error
    assert _rows(acct, "GLJNL", VOUCHER=res.docnum)[0]["DESCRP"].rstrip() == "รับชำระหนี้   บริษัท\xa0ซี.พี.ฟู้ดสโตร์\xa0จำกัด"


# ── 管钱底线：写前重读，不够整单不写 ──


def test_an_invoice_already_collected_stops_the_whole_receipt(acct):
    before = _state(acct)
    res = _write(acct, _payload(invoices=(("IVP42400239", "9972.00"), ("IVPAID", "300.00")), methods=(("BB", "10272.00"),)))
    assert not res.ok and res.error_code == R.ERR_ALREADY_COLLECTED
    assert res.evidence["kind"] == "receipt_invoices"
    assert res.evidence["invoices"] == [
        {"docnum": "IVP42400239", "amount": "9972.00", "remaining": "9972.00", "problem": ""},
        {"docnum": "IVPAID", "amount": "300.00", "remaining": "0.00", "problem": "short"}]
    assert _state(acct) == before  # 一行没写


def test_a_balance_made_smaller_since_the_snapshot_stops_it_and_never_shrinks_the_amount(acct):
    before = _state(acct)
    res = _write(acct, _payload(invoices=(("IVP42400240", "29997.00"),), methods=(("BB", "29997.00"),)))
    assert not res.ok and res.error_code == R.ERR_ALREADY_COLLECTED
    assert res.evidence["invoices"][0]["remaining"] == "29996.00"
    assert _state(acct) == before


@pytest.mark.parametrize("docnum,problem", [("NOPE", "not_found"), ("IVC2000001", "other_customer")])
def test_a_missing_or_other_customers_invoice_is_not_found(acct, docnum, problem):
    before = _state(acct)
    res = _write(acct, _payload(invoices=((docnum, "100.00"),), methods=(("BB", "100.00"),)))
    assert not res.ok and res.error_code == R.ERR_INVOICE_NOT_FOUND
    assert res.evidence["invoices"][0]["problem"] == problem
    assert _state(acct) == before


def test_a_cancelled_invoice_is_not_found(acct):
    seed(acct, "ARTRN", [{**_invoice("IVX", 50.0), "DOCSTAT": "C"}])
    res = _write(acct, _payload(invoices=(("IVX", "50.00"),), methods=(("BB", "50.00"),)))
    assert res.error_code == R.ERR_INVOICE_NOT_FOUND and res.evidence["invoices"][0]["problem"] == "cancelled"


def test_a_cash_basis_vat_invoice_stops_the_whole_receipt(acct):
    """服务品现金制增值税的发票（SRV_VATTYP 2，原生收款要另记税）本批不收（主会话 11-11）：整单不写、转人工，problem cash_basis_vat。"""
    seed(acct, "ARTRN", [{**_invoice("IVSRV", 107.0), "SRV_VATTYP": "2"}])
    before = _state(acct)
    res = _write(acct, _payload(invoices=(("IVP42400239", "9972.00"), ("IVSRV", "107.00")), methods=(("BB", "10079.00"),)))
    assert not res.ok and res.error_code == R.ERR_INVOICE_NOT_FOUND
    assert [row["problem"] for row in res.evidence["invoices"]] == ["", "cash_basis_vat"]
    assert res.evidence["invoices"][1]["remaining"] == "107.00"
    assert _state(acct) == before


def test_replaying_the_same_receipt_returns_the_first_number_and_writes_nothing(acct):
    first = _write(acct, _payload())
    assert first.ok and first.written
    before = _state(acct)
    again = _write(acct, _payload())
    assert again.ok and again.skipped and not again.written and again.docnum == first.docnum
    assert again.receipt == {"docnum": first.docnum, "invoices": [{"docnum": "IVP42400239", "remaining_after": "0.00"}]}
    assert _state(acct) == before


# ── 收款方式与科目 ──


@pytest.mark.parametrize("prefix,problem", [("NA", "missing"), ("XX", "conflict"), ("ZZ", "not_found")])
def test_a_type_without_a_usable_account_is_refused(acct, prefix, problem):
    before = _state(acct)
    res = _write(acct, _payload(methods=((prefix, "9972.00"),)))
    assert not res.ok and res.error_code == R.ERR_RECEIPT_TYPE
    assert res.evidence["problem"] == problem
    assert _state(acct) == before


def test_a_type_whose_account_cannot_be_posted_is_refused(acct):
    res = _write(acct, _payload(methods=(("SA", "9972.00"),)))  # 2132-03 不在科目表
    assert res.error_code == R.ERR_RECEIPT_TYPE and res.evidence["problem"] == "not_postable"


def test_cash_without_a_cash_account_is_refused(acct):
    isinfo(acct, ACCNUM00="", ACCNUM02=AR)
    before = _state(acct)
    res = _write(acct, _payload(cash="9972.00", methods=()))
    assert not res.ok and res.error_code == R.ERR_CASH_ACCOUNT
    assert _state(acct) == before


def test_the_types_are_listed_row_by_row_with_their_issues(acct):
    rows = payment_methods.types(acct)
    by_prefix = {}
    for row in rows:
        by_prefix.setdefault(row["prefix"], []).append(row)
    assert by_prefix["TB"] == [{"code": "12", "prefix": "TB", "name": "เงินโอนเข้าบัญชี BBL/กระแส", "name_en": "", "bank": "B1",
                                "account": BBL_CA, "account_issue": ""}]
    assert [r["account_issue"] for r in by_prefix["XX"]] == ["conflict", "conflict"]
    assert [r["account"] for r in by_prefix["QR"]] == ["1130-40", "1130-40"]  # 重复两行内容相同不算冲突
    assert by_prefix["NA"][0]["account_issue"] == "missing"
    assert [r["code"] for r in rows if r["code"] == "22"] == ["22", "22"]  # DOCCOD 重复照原样给
    assert payment_methods.line_number("CH", "RE2401367.1") == "CHRE2401367."


# ── 单号、借贷平、回滚 ──


def test_a_negative_method_line_is_a_credit_like_native_re2402899(acct):
    """原生 RE2402899：客户转 24087、发票 24086.45，多的 0.55 记「25 รายได้อื่นๆ」-0.55，凭证 SEQIT 2 贷 0.55。"""
    seed(acct, "ISRUN", [{"DOCTYP": "ZR", "DOCCOD": "25", "PREFIX": "25", "ACCNUM01": "4200-06", "POSDES": "รายได้อื่นๆ"}])
    _glacc(acct, "4200-06")
    res = _write(acct, _payload(invoices=(("IVPB2400376", "10002.24"),), methods=(("25", "-0.76"), ("TB", "10003.00"))))
    assert res.ok, res.error
    head = _head(acct, res.docnum)
    assert (head["CHQRCV"], head["CHQPAS"], head["IVCAMT"]) == (10002.24, 10002.24, 10002.24)
    assert [(r["CHQNUM"].strip(), r["RCVAMT"]) for r in _rows(acct, "ARRCPCQ", RCPNUM=res.docnum)] == \
        [("25RE2403080", -0.76), ("TBRE2403080", 10003.0)]
    assert _journal(acct, res.docnum) == [(" 2", BBL_CA, "0", Decimal("10003.0"), "CPI"),
                                          (" 2", "4200-06", "1", Decimal("0.76"), "CPI"),
                                          (" 6", AR, "1", Decimal("10002.24"), "CPI")]


def test_journal_lines_merge_by_account_and_sort_like_native(acct):
    """原生多方式的分录按科目升序（与方式行先后无关），同科目合并：现金框与 CH 行都是 1111-00 记一条。"""
    res = _write(acct, _payload(invoices=(("IVPB2400376", "10002.24"),), cash="2.24",
                                methods=(("TF", "5.00"), ("DC", "0.24"), ("CH", "10.00"), ("TB", "9984.76"))))
    assert res.ok, res.error
    assert _journal(acct, res.docnum) == [(" 2", CASH, "0", Decimal("12.24"), "CPI"),
                                          (" 2", BBL_CA, "0", Decimal("9984.76"), "CPI"),
                                          (" 2", DISCOUNT, "0", Decimal("0.24"), "CPI"),
                                          (" 2", FEE, "0", Decimal("5.0"), "CPI"),
                                          (" 6", AR, "1", Decimal("10002.24"), "CPI")]
    assert [r["CHQNUM"].strip() for r in _rows(acct, "ARRCPCQ", RCPNUM=res.docnum)] == \
        ["TFRE2403080", "DCRE2403080", "CHRE2403080", "TBRE2403080"]  # 方式行照载荷先后


def test_a_number_already_used_is_skipped(acct):
    seed(acct, "ARTRN", [{"RECTYP": "9", "DOCNUM": "RE2403080", "CUSCOD": "C2", "DOCSTAT": "M"}])
    res = _write(acct, _payload())
    assert res.ok and res.docnum == "RE2403081"
    assert _rows(acct, "ISRUN", DOCTYP="RE")[0]["DOCNUM"].strip() == "2403082"


def test_the_journal_balances_for_every_combination(acct):
    res = _write(acct, _payload(invoices=(("IVP42400239", "9972.00"), ("IVP42400241", "1000.00")), cash="10.00",
                                methods=(("TB", "10950.00"), ("TF", "5.00"), ("DC", "7.00"))))
    assert res.ok, res.error
    rows = _journal(acct, res.docnum)
    debit = sum(r[3] for r in rows if r[2] == "0")
    credit = sum(r[3] for r in rows if r[2] == "1")
    assert debit == credit == Decimal("10972.00")


def test_a_readback_mismatch_rolls_everything_back(acct):
    before = _state(acct)
    real = R._write_all

    def broken(account_dir, plan):
        docnum, gl_rows = real(account_dir, plan)
        return docnum, gl_rows + [("2", "9999-99", "0", Decimal("1"))]

    with mock.patch.object(R, "_write_all", broken):
        res = _write(acct, _payload())
    assert not res.ok and res.error_code == "READBACK_VERIFY_FAILED"
    assert _state(acct) == before


def test_a_failure_half_way_rolls_everything_back(acct):
    before = _state(acct)
    with mock.patch.object(R, "_bump_run", side_effect=OSError("disk gone")):
        res = _write(acct, _payload())
    assert not res.ok and res.error_code == "DBF_WRITE_FAILED"
    assert _state(acct) == before


def test_a_book_without_a_receipt_row_or_tables_is_refused(acct):
    (acct / "ARRCPCQ.DBF").unlink()
    res = _write(acct, _payload())
    assert res.error_code == R.ERR_BOOK and "ARRCPCQ" in res.error


def test_the_payload_department_and_salesman_win_and_user_is_cut_to_eight_bytes(acct):
    res = _write(acct, _payload(department="NLT3", salesman="NLTP7", user="somchai.k@example.com"))
    assert res.ok, res.error
    head = _head(acct, res.docnum)
    assert (head["DEPCOD"].strip(), head["SLMCOD"].strip(), head["USERID"].strip()) == ("NLT3", "NLTP7", "somchai.")
    assert {row[4] for row in _journal(acct, res.docnum)} == {"NLT3"}


def test_a_locked_period_is_refused_for_erp(acct):
    _synth_account.seed_fiscal_year(acct, 2026, locked_cy=(11,))
    res = _write(acct, _payload(respect_period_lock=True))
    assert res.error_code == fiscal_period.ERR_PERIOD_LOCKED


# ── 载荷 ──


@pytest.mark.parametrize("over", [
    {"cash": "1.00"},                                                     # 合计对不上
    {"invoices": [{"docnum": "IVP42400239", "amount": "1.001"}]},         # 三位小数
    {"invoices": [{"docnum": "A", "amount": "1.00"}, {"docnum": "A", "amount": "1.00"}]},
    {"methods": [{"prefix": "BB", "amount": "4986.00"}, {"prefix": "BB", "amount": "4986.00"}]},
    {"methods": [{"prefix": "BB", "amount": "0.00"}]}, {"cash": "-1.00"},
    {"ref_no": ""}, {"ref_no": "x" * 31}, {"customer": ""}, {"doc_date": "10/11/2026"},
    {"account_set": "OTHER"}, {"invoices": []},
])
def test_bad_payloads_write_nothing(acct, over):
    before = _state(acct)
    res = _write(acct, _payload(**over))
    assert not res.ok and res.error_code == "PAYLOAD_INVALID"
    assert _state(acct) == before


def test_payload_20_is_supported_and_newer_is_refused():
    assert payload_contract.RECEIPT_PAYLOAD_VERSION == 20 and 20 in payload_contract.SUPPORTED_PAYLOAD_VERSIONS
    assert payload_contract.check_payload_version({"payload_version": 20}) is None
    assert payload_contract.check_payload_version({"payload_version": 22}) == payload_contract.ERR_PAYLOAD_VERSION


# ── worker 分流与回执 ──


class _Queue:
    def __init__(self):
        self.acks = []

    def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
        self.acks.append(("success", extracted_invoice_no, meta))
        return True

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append(("failed", error_code, outcome, meta))
        return True


def _run_task(monkeypatch, acct, payload, task_id="T-RE-1"):
    from companion import task_journal, worker, writer_id

    found = SimpleNamespace(account_dir=str(acct), account_set="NLTTEST", account_company="", has_selection=lambda: True)
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, p: found)
    monkeypatch.setattr(writer_id, "resolve", lambda path: "PEARNLY")
    monkeypatch.setattr(task_journal, "written", lambda key: None)
    monkeypatch.setattr(task_journal, "remember", lambda key, value: None)
    queue = _Queue()
    outcome = worker._handle_dbf_task(SimpleNamespace(default_depcod="", ui_lang="en"), queue,
                                      SimpleNamespace(id=task_id, payload=payload, invoice_no="X"))
    return outcome, queue.acks


def test_the_worker_routes_receipts_and_acks_the_number_and_balances(monkeypatch, acct):
    outcome, acks = _run_task(monkeypatch, acct, _payload())
    assert outcome is None
    kind, docnum, meta = acks[0]
    assert (kind, docnum) == ("success", "RE2403080")
    assert meta["doc_type"] == "receipt" and meta["written"] is True
    assert meta["receipt"] == {"docnum": "RE2403080", "invoices": [{"docnum": "IVP42400239", "remaining_after": "0.00"}]}
    assert _rows(acct, "APTRN") == []  # 没被当进项写


def test_the_worker_sends_a_refusal_to_a_person_with_the_balances(monkeypatch, acct):
    outcome, acks = _run_task(monkeypatch, acct, _payload(invoices=(("IVPAID", "300.00"),), methods=(("BB", "300.00"),)))
    assert outcome == "needs_review"
    kind, code, outcome_, meta = acks[0]
    assert (kind, code, outcome_) == ("failed", "already_collected", "needs_review")
    assert meta["evidence"]["invoices"][0]["remaining"] == "0.00"


def test_an_older_cc_refuses_payload_20(monkeypatch, acct):
    older = frozenset(range(1, 20))
    with mock.patch.object(payload_contract, "SUPPORTED_PAYLOAD_VERSIONS", older):
        outcome, acks = _run_task(monkeypatch, acct, _payload())
    assert outcome == "failed" and acks[0][1] == payload_contract.ERR_PAYLOAD_VERSION
    assert _rows(acct, "ARTRN", RECTYP="9") == []


def test_an_operator_flag_never_sends_a_receipt_to_the_operator(monkeypatch, acct):
    outcome, acks = _run_task(monkeypatch, acct, _payload(entry_mode="operator"))
    assert outcome is None and acks[0][1] == "RE2403080"
