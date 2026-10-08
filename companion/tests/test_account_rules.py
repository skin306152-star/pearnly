# -*- coding: utf-8 -*-
"""写入时按这套账自己的数据定的值(account_rules.py · 2026-10-08 用 62 套真实账套核出的问题)。

表结构只建用到的列(真账套列更多;读法按列名容错)。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from companion import account_rules as R  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import master_create as MC  # noqa: E402
import _synth_account as synth  # noqa: E402


def _table(acct: Path, name: str, spec: str, rows) -> None:
    path = acct / f"{name}.DBF"
    if path.exists():
        path.unlink()
    t = dbf.Table(str(path), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    t.open(dbf.READ_WRITE)
    try:
        for row in rows:
            t.append(row)
    finally:
        t.close()


@pytest.fixture
def acct(tmp_path):
    d = synth.build(tmp_path / "70EXP" / "T9TEST")
    _table(d, "GLACC", "ACCNUM C(15); ACCNAM C(60); ACCTYP C(1); GROUP C(1)", [
        {"ACCNUM": "1130-01", "ACCNAM": "ลูกหนี้การค้า", "ACCTYP": "0", "GROUP": "1"},
        {"ACCNUM": "1130-03", "ACCNAM": "ลูกหนี้ พ.ก.ง.", "ACCTYP": "0", "GROUP": "1"},
        {"ACCNUM": "1130-00", "ACCNAM": "ลูกหนี้(รวม)", "ACCTYP": "1", "GROUP": "1"},
        {"ACCNUM": "2120-01", "ACCNAM": "เจ้าหนี้การค้า", "ACCTYP": "0", "GROUP": "2"},
        {"ACCNUM": "2120-04", "ACCNAM": "เจ้าหนี้-ต่างประเทศ", "ACCTYP": "0", "GROUP": "2"},
    ])
    _table(d, "APMAS", "SUPCOD C(10); SUPNAM C(60); SUPTYP C(2); ACCNUM C(15); PAYTRM N(3,0)", [
        {"SUPCOD": "S001", "SUPNAM": "a", "SUPTYP": "01", "ACCNUM": "2120-04", "PAYTRM": 30},
        {"SUPCOD": "S002", "SUPNAM": "b", "SUPTYP": "01", "ACCNUM": "", "PAYTRM": 0},
        {"SUPCOD": "S003", "SUPNAM": "c", "SUPTYP": "02", "ACCNUM": "9999-99", "PAYTRM": 0},
    ])
    _table(d, "ISRUN", "DOCTYP C(2); PREFIX C(2); DOCNUM C(10); JNLTYP C(2); DEPCOD C(4)", [
        {"DOCTYP": "HP", "PREFIX": "HP", "DOCNUM": "0000001", "JNLTYP": "01", "DEPCOD": "HQ"},
        {"DOCTYP": "RR", "PREFIX": "RR", "DOCNUM": "0000001", "JNLTYP": "04", "DEPCOD": ""},
    ])
    _table(d, "ISINFO", "THINAM C(60); MAINLOC C(4); ACCNUM02 C(15); ACCNUM07 C(15)", [
        {"THINAM": "TEST", "MAINLOC": "1001", "ACCNUM02": "1130-01", "ACCNUM07": "2120-01"},
    ])
    return d


def test_the_control_account_is_the_one_on_the_counterpartys_record(acct):
    # Express 自己过账用对方档上的科目;档上没挂、不在科目表或是汇总科目时才用调用方给的(账套默认)
    assert R.control_account(acct, "APMAS", "S001", "2120-01") == "2120-04"
    assert R.control_account(acct, "APMAS", "S002", "2120-01") == "2120-01"
    assert R.control_account(acct, "APMAS", "S003", "2120-01") == "2120-01"
    assert R.control_account(acct, "APMAS", "S404", "2120-01") == "2120-01"
    assert R.postable_account(acct, "1130-01") and not R.postable_account(acct, "1130-00")


def test_due_date_follows_the_counterpartys_payment_term(acct):
    assert R.party_terms(acct, "APMAS", "S001") == ("2120-04", 30)
    assert R.due_date(dt.date(2026, 10, 1), 30) == dt.date(2026, 10, 31)
    assert R.due_date(dt.date(2026, 10, 1), 0) == dt.date(2026, 10, 1)


def test_book_journal_department_main_location_and_common_type(acct):
    assert R.book(acct, "HP") == {"JNLTYP": "01", "DEPCOD": "HQ"}  # 有的账套现购簿挂 01 号日记账
    assert R.book(acct, "XX") == {}
    assert R.main_location(acct) == "1001"  # 有一套账没有 01 仓
    assert R.common_type(acct, "APMAS", "SUPTYP") == "01"
    assert R.default_account(acct, "ACCNUM07") == "2120-01"


def test_main_location_falls_back_when_the_account_set_has_no_setting(tmp_path):
    assert R.main_location(tmp_path) == S.STCRD_DEFAULT_LOCCOD


def test_stock_card_rows_use_this_account_sets_units_and_main_warehouse(acct):
    row = R.localize_stcrd(acct, {"TQUCOD": "ชิ", "LOCCOD": "01"}, "ชิ้น")
    assert row == {"TQUCOD": "ชน", "LOCCOD": "1001"}
    assert R.localize_stcrd(acct, {}, "กระสอบ")["TQUCOD"] == ""  # 这套账没有的单位:留空,不写无效码


class _Cash:
    customer_code = MC.CASH_CUSTOMER
    customer_name = MC.CASH_CUSTOMER
    customer_tax_id = ""
    customer_prename = ""
    customer_address = ""
    lines = [{"acc": "1111-01", "side": "D", "amount": "107.00"}]  # 现销借方是现金


def _armas(acct, rows):
    synth._seed(acct, "ARMAS", rows)


def test_the_account_sets_own_cash_customer_is_used(acct):
    _armas(acct, [{"CUSCOD": "CASH", "CUSNAM": "ขายสด", "CUSTYP": "00", "ACCNUM": "1130-01"}])
    assert MC.ensure_customer(acct, _Cash(), created_date=dt.date(2026, 10, 1)) == "CASH"


def test_without_a_cash_customer_one_is_created_with_the_default_receivable(acct):
    _armas(acct, [{"CUSCOD": "C001", "CUSNAM": "ลูกค้า", "CUSTYP": "05", "ACCNUM": "1130-01"},
                  {"CUSCOD": "C002", "CUSNAM": "ลูกค้า 2", "CUSTYP": "05", "ACCNUM": "1130-01"}])
    assert MC.ensure_customer(acct, _Cash(), created_date=dt.date(2026, 10, 1)) == MC.CASH_CUSTOMER
    t = dbf.Table(str(acct / "ARMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        row = next(r for r in S.live_rows(t) if (r.CUSCOD or "").strip() == MC.CASH_CUSTOMER)
        # 客户档科目 = 账套默认应收(现销分录借方是现金,不能拿来当客户的应收科目);类型 = 最常用的
        assert (row.ACCNUM.strip(), row.CUSTYP.strip()) == ("1130-01", "05")
    finally:
        t.close()
    # 再来一张:这个码已经在了,直接用
    assert MC.ensure_customer(acct, _Cash(), created_date=dt.date(2026, 10, 2)) == MC.CASH_CUSTOMER


def _poison(path: Path, good: bytes, bad: bytes) -> None:
    """把文件里第一处 `good` 字节换成同长的 `bad`(模拟真实账套里解不出的字节)。"""
    data = path.read_bytes()
    assert good in data and len(good) == len(bad)
    path.write_bytes(data.replace(good, bad, 1))


def test_a_row_with_undecodable_bytes_does_not_fail_the_write(acct):
    # 2026-10-08 回归:62 套里 3 套有个别行带 0x90 / 0x99,严格解码读到就抛错,整张单回滚
    synth._seed(acct, "ARMAS", [{"CUSCOD": "XBAD", "CUSNAM": "BADNAME", "CUSTYP": "05", "ACCNUM": "1130-01"},
                                {"CUSCOD": "CASH", "CUSNAM": "ขายสด", "CUSTYP": "05", "ACCNUM": "1130-01"}])
    _poison(acct / "ARMAS.DBF", b"BADNAME", b"BAD\x90\x99ME")
    assert MC.ensure_customer(acct, _Cash(), created_date=dt.date(2026, 10, 1)) == "CASH"


def test_updating_a_row_keeps_its_undecodable_bytes(acct):
    synth._seed(acct, "ARMAS", [{"CUSCOD": "C9", "CUSNAM": "KEEPME", "CUSTYP": "05", "ACCNUM": "1130-01"}])
    _poison(acct / "ARMAS.DBF", b"KEEPME", b"KE\x90\x99ME")
    t = dbf.Table(str(acct / "ARMAS.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_WRITE)
    try:
        for r in S.live_rows(t):
            with r as rec:
                rec.CUSTYP = "06"
    finally:
        t.close()
    data = (acct / "ARMAS.DBF").read_bytes()
    assert b"KE\x90\x99ME" in data and b"06" in data
