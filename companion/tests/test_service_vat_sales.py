# -*- coding: utf-8 -*-
"""服务品销项税口径（ARTRN.SRV_VATTYP）：客户档读回带这家在本账套赊销单上的口径串（11-12 晚，引导式第 1 期要用；做法同供应商的 srv）。

只算 Express 人录的赊销单（RECTYP 3、价格类型 1 / 2、没作废、不是 CC 写的）；口径串 = 出现过的 1 / 2 并集，"0" = 没有历史。
62 套统计（S 盘副本 1045 套）：IV 为 '1' 251 万行 / 770 套、'2' 120 行 / 20 套，自定前缀的销项单 '2' 1.7 万行 / 117 套。"""

from __future__ import annotations

import dbf
import pytest

import _synth_account
from companion import catalog_probe, service_vat
from companion import dbf_schema as S


@pytest.fixture
def acct(tmp_path):
    service_vat._cache.clear()
    return _synth_account.build(tmp_path / "70EXP" / "test")


def _sales(acct, *rows):
    """ARTRN 里放几张人录的单：(客户, 口径[, RECTYP, FLGVAT, 录入人, 状态])。"""
    t = dbf.Table(str(acct / "ARTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for index, row in enumerate(rows):
            cuscod, srv, rectyp, flgvat, userid, stat = (*row, "3", "2", "ACC", "N")[:6] if len(row) == 2 else row
            t.append({"RECTYP": rectyp, "DOCNUM": f"IV000{index:04d}", "CUSCOD": cuscod, "FLGVAT": flgvat,
                      "SRV_VATTYP": srv, "USERID": userid, "DOCSTAT": stat})
    finally:
        t.close()


def _customers(acct, *codes):
    t = dbf.Table(str(acct / "ARMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for code in codes:
            t.append({"CUSCOD": code, "CUSNAM": f"ลูกค้า {code}"})
    finally:
        t.close()


def test_each_customer_carries_the_basis_its_credit_sales_used(acct):
    _sales(acct, ("C1", "2"), ("C1", "2"), ("C2", "1"), ("C3", "1"), ("C3", "2"),
           ("C4", "2", "1", "2", "ACC", "N"),  # 现销（RECTYP 1）不算
           ("C5", "2", "3", "2", "ACC", "C"),  # 作废的不算
           ("C6", "2", "3", "2", S.PEARNLY_USERID, "N"))  # CC 写的不算
    known, read = service_vat.customer_report(acct)
    assert read and known == {"C1": "2", "C2": "1", "C3": "12"}
    _customers(acct, "C1", "C2", "C7")
    probed = {row["code"]: row["srv"] for row in catalog_probe.probe_customers(acct)}
    assert probed == {"C1": "2", "C2": "1", "C7": service_vat.NO_HISTORY}


def test_purchases_and_sales_histories_do_not_mix(acct):
    _sales(acct, ("S001", "2"))
    assert service_vat.history(acct) == {}  # 进项历史只读 APTRN
    assert service_vat.customer_report(acct)[0] == {"S001": "2"}
