# -*- coding: utf-8 -*-
"""直写的录入人记授权的 Express 账户名（11-07，``writer_id``；评估 docs/work/2026-11-07-cc-direct-writer-userid-评估.md）。

- 单头 / 主档建档人记账户名（大写），取不到、超过 8 个字符、写不进 cp874、不在这本账套的 Express 用户表 SCUSER 里 → PEARNLY；
- 凭证头 GLJNL.CREBY 一律记 PEARNLY（机器标记）；
- 录入人改成真人名后，CC 认自己写的单改看凭证头：服务品进项税口径不把 CC 写的单当历史（不自己学自己）；
- 回执 meta 带 ``writer``；桥的 ``operator_state`` 回已授权的账户名。
"""

from __future__ import annotations

import logging

import dbf
import pytest

from companion import dbf_schema as S
from companion import service_vat, writer_id, worker
from companion.write_result import DbfWriteResult
from test_dbf_stock import _entry, _line, _write, book  # noqa: F401
from test_stock_location import _buy, _item, _purchase, _rows, _sale, _sell, acct  # noqa: F401


def _users(root, *codes):
    secure = root / "secure"
    secure.mkdir(parents=True, exist_ok=True)
    t = dbf.Table(str(secure / "SCUSER.DBF"), "RECTYP C(1); RECCOD C(8); USERPWD C(8)", codepage=S.CODEPAGE,
                  dbf_type=S.DBF_TYPE)
    t.open(dbf.READ_WRITE)
    try:
        t.append({"RECTYP": "G", "RECCOD": "********"})
        for code in codes:
            t.append({"RECTYP": "U", "RECCOD": code, "USERPWD": "secret"})
    finally:
        t.close()


def _as(name):
    return lambda: name


def test_the_authorised_express_user_is_the_writer_in_capitals(tmp_path):
    folder = tmp_path / "70EXP" / "test"
    folder.mkdir(parents=True)
    _users(tmp_path / "70EXP", "BIT1", "BIT5")
    assert writer_id.resolve(folder, stored=_as(" bit5 ")) == "BIT5"


@pytest.mark.parametrize("name", ["", "   ", "TOOLONGNM", "名字", "BIT9"])
def test_anything_unfit_falls_back_to_pearnly(tmp_path, name):
    """空、超过 8 个字符（不截断）、cp874 写不出、不在这本账套的 Express 用户表里 → PEARNLY，不拦单。"""
    folder = tmp_path / "70EXP" / "test"
    folder.mkdir(parents=True)
    _users(tmp_path / "70EXP", "BIT5", "TOOLONGN")
    assert writer_id.resolve(folder, stored=_as(name)) == S.PEARNLY_USERID


def test_without_a_user_table_the_name_is_used_and_a_failing_store_means_pearnly(tmp_path):
    assert writer_id.resolve(tmp_path, stored=_as("acc03")) == "ACC03"

    def broken():
        raise OSError("vault")
    assert writer_id.resolve(tmp_path, stored=broken) == S.PEARNLY_USERID


def test_the_password_column_of_the_user_table_never_reaches_the_log(tmp_path, caplog):
    folder = tmp_path / "70EXP" / "test"
    folder.mkdir(parents=True)
    _users(tmp_path / "70EXP", "BIT5")
    with caplog.at_level(logging.DEBUG):
        writer_id.resolve(folder, stored=_as("BIT9"))
    assert "secret" not in caplog.text


def test_documents_carry_the_writer_and_the_journal_keeps_the_machine_mark(acct):
    res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")]), userid="BIT5")
    assert res.ok, res.error
    assert _rows(acct, "APTRN", DOCNUM=res.docnum)[0]["USERID"].strip() == "BIT5"
    assert _rows(acct, "GLJNL", VOUCHER=res.docnum)[0]["CREBY"].strip() == S.PEARNLY_USERID
    sale = _sell(acct, _sale([_item("ICE01", 1, 15.0, "stock_sale")]), userid="BIT5")
    assert sale.ok, sale.error
    assert _rows(acct, "ARTRN", DOCNUM=sale.docnum)[0]["USERID"].strip() == "BIT5"
    assert _rows(acct, "GLJNL", VOUCHER=sale.docnum)[0]["CREBY"].strip() == S.PEARNLY_USERID


def test_stock_entries_carry_the_writer_and_the_journal_keeps_the_machine_mark(book):
    res = _write(book, _entry("OU", [_line("ICE01", 1)]), userid="BIT5")
    assert res.ok, res.error
    assert _rows(book, "STTRN", DOCNUM=res.docnum)[0]["USERID"].strip() == "BIT5"
    assert _rows(book, "GLJNL", VOUCHER=res.docnum)[0]["CREBY"].strip() == S.PEARNLY_USERID


def test_a_cc_document_under_a_real_writer_is_not_learnt_as_history(acct):
    """CC 以 BIT5 的名义写的赊购单（服务品、有税）：服务品进项税口径不把它当这家供应商的历史（看凭证头机器标记）。"""
    service_vat._cache.clear()
    payload = _purchase([{"name": "ค่าบริการ", "qty": "1", "unit_price": "100.00", "amount": "100.00", "unit": "",
                          "item_mode": "non_stock_item"}], srv_vattyp="accrual")
    res = _buy(acct, payload, userid="BIT5")
    assert res.ok, res.error
    assert _rows(acct, "APTRN", DOCNUM=res.docnum)[0]["SRV_VATTYP"].strip() == "1"
    assert writer_id.cc_vouchers(acct) == {res.docnum}
    assert service_vat.history(acct) == {}


def test_the_reply_names_the_writer():
    res = DbfWriteResult(True, docnum="RR690115-001", line_modes=[])
    assert worker._push_meta(res, "purchase", "", writer="BIT5")["writer"] == "BIT5"
    assert "writer" not in worker._push_meta(DbfWriteResult(False, error_code="X"), "purchase", "", writer="BIT5")
