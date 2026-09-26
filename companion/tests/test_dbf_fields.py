# -*- coding: utf-8 -*-
"""Express 金额/审核字段口径单测(抽出 dbf_fields · 行为须与抽出前一致)。"""
from src.companion.dbf_fields import amount_fields, audit_fields


def test_amount_fields_credit():
    # 赊单 FLGVAT='2':AMOUNT/AFTDISC/TOTAL/NETVAL=税前 · NETAMT=含税
    flgvat, amount, aftdisc, total, netval, netamt = amount_fields(
        is_cash=False, has_vat=True, base=100, vat=7, total=107
    )
    assert flgvat == "2"
    assert (amount, aftdisc, total, netval, netamt) == (100.0, 100.0, 100.0, 100.0, 107.0)


def test_amount_fields_cash():
    # 现单 FLGVAT='1':AMOUNT/AFTDISC/TOTAL/NETAMT=含税 · NETVAL=税前
    flgvat, amount, aftdisc, total, netval, netamt = amount_fields(
        is_cash=True, has_vat=True, base=100, vat=7, total=107
    )
    assert flgvat == "1"
    assert (amount, aftdisc, total, netval, netamt) == (107.0, 107.0, 107.0, 100.0, 107.0)


def test_amount_fields_no_vat():
    flgvat, *vals = amount_fields(is_cash=False, has_vat=False, base=50, vat=0, total=50)
    assert flgvat == "0" and vals == [50.0, 50.0, 50.0, 50.0, 50.0]


def test_audit_fields_visibility_keys():
    f = audit_fields("D", "01", has_vat=True)
    assert f["CMPLAPP"] == "Y"  # 报表可见性关键
    assert f["BILNUM"] == "~" and f["ORGNUM"] == 0 and f["SRV_VATTYP"] == "1"
    assert audit_fields("D", "", has_vat=False)["SRV_VATTYP"] == "-"


def test_reexported_from_dbf_writer():
    # dbf_sales 仍从 dbf_writer 取 → 抽出后 import 路径不变
    from src.companion.dbf_writer import amount_fields as aw_af, audit_fields as aw_audit
    assert aw_af is amount_fields and aw_audit is audit_fields
