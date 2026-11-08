# -*- coding: utf-8 -*-
"""进货单的库存行照 Express 原生直接记物料科目组科目（11-07，``purchase_lines.stock_journal``；评估 docs/work/2026-11-07-cc-purchase-gl-native-评估.md）。

S 盘 95 本人录的赊购 / 现购单：永续盘存账套借科目组的存货科目 ACCNUM01（27,598 张），定期盘存账套借科目组的购货科目 ACCNUM02
（29,395 张），同一科目合成一条借方（SEQIT 0），没有「借存货 / 贷购货」重分类（SEQIT 7 / 8 一张都没有）。
"""

from __future__ import annotations

from decimal import Decimal

import dbf
import pytest

from account_rules_fixture import glacc, isinfo, seed
from companion import dbf_schema as S
from test_stock_location import (COGS_ACC, INVENTORY_ACC, PURCHASE_ACC, _append, _buy, _item, _purchase, _rows,  # noqa: F401
                                 acct)

OTHER_INV, OTHER_PUR, DEFAULT_PUR, GROUP_PUR = "11-04-04-00", "51-03-02-00", "51-03-09-00", "51-03-01-00"


def _debits(acct, docnum):
    return sorted(((r["SEQIT"].strip(), r["ACCNUM"].strip()), float(r["AMOUNT"])) for r in _rows(acct, "GLJNLIT", VOUCHER=docnum)
                  if r["TRNTYP"].strip() == "0")


def _seqits(acct, docnum):
    return {r["SEQIT"].strip() for r in _rows(acct, "GLJNLIT", VOUCHER=docnum)}


@pytest.fixture
def two_groups(acct):
    """ICE01 在科目组 ST01（存货 11-04-02-00 / 购货 51-01-00-00），OIL01 在 ST02（存货 11-04-04-00 / 购货 51-03-02-00）。"""
    _append(acct / "STMAS.DBF", [{"STKCOD": "OIL01", "STKDES": "น้ำมัน", "STKTYP": "0", "ACCCOD": "ST02", "QUCOD": "ถง"}])
    _append(acct / "ISACC.DBF", [{"ACCTYP": "S", "ACCCOD": "ST02", "METHOD": "A", "ACCNUM01": OTHER_INV, "ACCNUM02": OTHER_PUR}])
    glacc(acct, (INVENTORY_ACC, "สินค้า"), (OTHER_INV, "สินค้า 2"), (COGS_ACC, "ซื้อ"), (OTHER_PUR, "ซื้อ 2"),
          (DEFAULT_PUR, "ซื้อสินค้า"), (PURCHASE_ACC, "ซื้อสุทธิ"), (GROUP_PUR, "ซื้อน้ำแข็ง"))
    return acct


def _three_lines():
    return [_item("ICE01", 2, 20.0, "stock_item"), _item("ICE01", 3, 30.0, "stock_item"), _item("OIL01", 1, 50.0, "stock_item")]


def test_a_perpetual_book_debits_each_groups_inventory_account_once(two_groups):
    res = _buy(two_groups, _purchase(_three_lines()))
    assert res.ok, res.error
    assert _debits(two_groups, res.docnum) == [(("0", INVENTORY_ACC), 50.0), (("0", OTHER_INV), 50.0), (("3", "11-05-04-01"), 7.0)]
    assert not {"7", "8"} & _seqits(two_groups, res.docnum)


def _set_group(acct, acccod, **fields):
    table = dbf.Table(str(acct / "ISACC.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        for row in table:
            if row["ACCCOD"].strip() == acccod:
                with row as rec:
                    for name, value in fields.items():
                        rec[name] = value
    finally:
        table.close()


def test_a_periodic_book_debits_each_groups_purchases_account(two_groups):
    isinfo(two_groups, MAINLOC="01", ISPERPETUA="N", ACCNUM18=DEFAULT_PUR)
    _set_group(two_groups, "ST01", ACCNUM02=GROUP_PUR)
    res = _buy(two_groups, _purchase(_three_lines()))
    assert res.ok, res.error
    assert _debits(two_groups, res.docnum) == [(("0", GROUP_PUR), 50.0), (("0", OTHER_PUR), 50.0), (("3", "11-05-04-01"), 7.0)]


def test_a_periodic_group_without_a_postable_purchases_account_falls_back_to_the_book_default(two_groups):
    """科目组的购货科目在科目表里、但是汇总科目（ACCTYP 1，不能记账）→ 照原生（69POL 259 / 296 张）记账套默认购货 ISINFO.ACCNUM18。"""
    isinfo(two_groups, MAINLOC="01", ISPERPETUA="N", ACCNUM18=DEFAULT_PUR)
    seed(two_groups, "GLACC", [{"ACCNUM": "51-40-00-00", "ACCNAM": "ซื้อ (รวม)", "ACCTYP": "1", "GROUP": "5"}])
    seed(two_groups, "ISACC", [{"ACCTYP": "S", "ACCCOD": "ST03", "METHOD": "A", "ACCNUM01": OTHER_INV, "ACCNUM02": "51-40-00-00"}])
    _append(two_groups / "STMAS.DBF", [{"STKCOD": "GAS01", "STKDES": "แก๊ส", "STKTYP": "0", "ACCCOD": "ST03", "QUCOD": "ถง"}])
    res = _buy(two_groups, _purchase([_item("GAS01", 1, 40.0, "stock_item")]))
    assert res.ok, res.error
    assert _debits(two_groups, res.docnum)[0] == (("0", DEFAULT_PUR), 40.0)


@pytest.mark.parametrize("periodic", [False, True])
def test_a_group_account_missing_from_the_chart_stops_the_document_like_native(two_groups, periodic):
    """科目组的存货 / 购货科目不在科目表里 → 原生 Express 报 1155 整张拒存（11-08 TEST 实录），CC 照原生停单、写明科目组与科目，
    账套一个字节不动。"""
    if periodic:
        isinfo(two_groups, MAINLOC="01", ISPERPETUA="N", ACCNUM18=DEFAULT_PUR)
    seed(two_groups, "ISACC", [{"ACCTYP": "S", "ACCCOD": "ST09", "METHOD": "A", "ACCNUM01": "11-99-99-99", "ACCNUM02": "59-99-99-99"}])
    _append(two_groups / "STMAS.DBF", [{"STKCOD": "GAS01", "STKDES": "แก๊ส", "STKTYP": "0", "ACCCOD": "ST09", "QUCOD": "ถง"}])
    before = {p.name: p.read_bytes() for p in two_groups.iterdir() if p.is_file()}
    res = _buy(two_groups, _purchase([_item("GAS01", 1, 40.0, "stock_item"), _item("ICE01", 1, 10.0, "stock_item")]),
               make_backup=True)
    assert not res.ok
    assert res.error_code == "ITEM_GROUP_ACCOUNT_MISSING"
    assert "ST09" in res.error and ("59-99-99-99" if periodic else "11-99-99-99") in res.error
    assert {p.name: p.read_bytes() for p in two_groups.iterdir() if p.is_file()} == before


def test_a_cash_purchase_follows_the_same_rule(two_groups):
    res = _buy(two_groups, _purchase(_three_lines(), doctype="HP"))
    assert res.ok, res.error
    assert [acc for (_seq, acc), _amt in _debits(two_groups, res.docnum)][:2] == [INVENTORY_ACC, OTHER_INV]
    assert not {"7", "8"} & _seqits(two_groups, res.docnum)


def test_the_journal_still_balances_and_the_purchase_account_keeps_only_what_is_not_stock(two_groups):
    payload = _purchase([_item("ICE01", 2, 20.0, "stock_item"),
                         {**_item("ค่าขนส่ง", 1, 30.0, "direct_account"), "code": "", "name": "ค่าขนส่ง"}], payload_version=19)
    res = _buy(two_groups, payload)
    assert res.ok, res.error
    rows = _rows(two_groups, "GLJNLIT", VOUCHER=res.docnum)
    dr = sum(Decimal(str(r["AMOUNT"])) for r in rows if r["TRNTYP"].strip() == "0")
    cr = sum(Decimal(str(r["AMOUNT"])) for r in rows if r["TRNTYP"].strip() == "1")
    assert dr == cr
    assert (("0", INVENTORY_ACC), 20.0) in _debits(two_groups, res.docnum)
    assert (("0", PURCHASE_ACC), 30.0) in _debits(two_groups, res.docnum)
