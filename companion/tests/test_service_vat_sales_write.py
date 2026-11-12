# -*- coding: utf-8 -*-
"""服务品销项税口径写进销货单（ARTRN.SRV_VATTYP，11-12 晚，并进载荷 21）：载荷表头 ``srv_vattyp`` = 2（收付实现）且这张单有服务品 →
照原生记：单头 SRV_VATTYP 2、VATDAT 空，税那一行贷「未到期销项税」（ISINFO.ACCNUM27），开单不写销项税记录；其余与以前写死的一样
（有税 1、没税 -）。原生证据（S 盘副本 136 套，税额 > 0 的口径 2 销项单）：税贷 ACCNUM27 4124 张 / ACCNUM10 3 张；不写销项税记录
5095 / 46 张；VATDAT 空 5141 / 5141。科目没设或不能记账 → 不写、转人工（ACCOUNT_NOT_POSTABLE，同进货的口径 2）。"""

from __future__ import annotations

from account_rules_fixture import glacc, isinfo
from companion import dbf_schema as S
from companion import service_vat
from test_stock_location import _item, _no_runner, _rows, _sale, _sell, _state, acct  # noqa: F401

DEFERRED_OUT = "21-05-04-09"  # 未到期销项税（ISINFO.ACCNUM27）


def _book(acct, deferred=DEFERRED_OUT):
    isinfo(acct, MAINLOC="01", ACCNUM27=deferred)
    if deferred:
        glacc(acct, (deferred, "ภาษีขายยังไม่ถึงกำหนด"))


def _service(**header):
    return _sale([_item("ค่าบริการ", 1, 100.0, "direct_account", account="41-01-00-00")], **header)


def _vat_line(acct, docnum):
    rows = [r for r in _rows(acct, "GLJNLIT", VOUCHER=docnum) if str(r["SEQIT"]).strip() == S.SALES_SEQIT["output_vat"]]
    assert len(rows) == 1
    return rows[0]


def test_a_cash_basis_service_sale_books_its_vat_as_not_yet_due_and_writes_no_vat_record(acct):
    _book(acct)
    res = _sell(acct, _service(srv_vattyp="2"))
    assert res.ok, res.error
    head = _rows(acct, "ARTRN", DOCNUM=res.docnum)[0]
    assert head["SRV_VATTYP"].strip() == "2" and head["VATDAT"] is None
    assert _rows(acct, "ISVAT", DOCNUM=res.docnum) == []
    assert _vat_line(acct, res.docnum)["ACCNUM"].strip() == DEFERRED_OUT


def test_without_the_key_or_with_accrual_the_sale_is_written_as_before(acct):
    _book(acct)
    for header in ({}, {"srv_vattyp": "1"}, {"srv_vattyp": "accrual"}):
        res = _sell(acct, _service(ref_no=f"IV-{len(header)}{header.get('srv_vattyp', '')}", **header))
        assert res.ok, res.error
        head = _rows(acct, "ARTRN", DOCNUM=res.docnum)[0]
        assert head["SRV_VATTYP"].strip() == "1" and head["VATDAT"] is not None
        assert len(_rows(acct, "ISVAT", DOCNUM=res.docnum)) == 1
        assert _vat_line(acct, res.docnum)["ACCNUM"].strip() != DEFERRED_OUT


def test_a_sale_of_stock_only_keeps_accrual_even_when_cash_basis_is_given(acct):
    """Express 只在有服务品时问口径：全是库存品的单照权责记（同进货）。"""
    _book(acct)
    res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], srv_vattyp="2"))
    assert res.ok, res.error
    assert _rows(acct, "ARTRN", DOCNUM=res.docnum)[0]["SRV_VATTYP"].strip() == "1"
    assert len(_rows(acct, "ISVAT", DOCNUM=res.docnum)) == 1


def test_a_book_without_a_postable_not_yet_due_account_is_left_untouched(acct):
    _book(acct, deferred="")
    before = _state(acct)
    res = _sell(acct, _service(srv_vattyp="2"))
    assert not res.ok and res.error_code == "ACCOUNT_NOT_POSTABLE"
    assert service_vat.DEFERRED_OUTPUT_VAT_SLOT in res.error
    assert _state(acct) == before
