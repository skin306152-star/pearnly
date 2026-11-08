# -*- coding: utf-8 -*-
"""销货单的收入照 Express 原生按物料科目组记（11-08，``dbf_sales.revenue_journal``；评估 docs/work/2026-11-07-cc-sales-revenue-native-评估.md）。

S 盘 95 本人录的赊销 / 现销：非库存品记物料科目组的收入科目 ISACC.ACCNUM03（IV 65,843 张、HS 2,317 张），同一科目合成一条、SEQIT 5；
库存品的科目组几乎没配 ACCNUM03，记账套默认收入 ACCNUM11（载荷本来就是它），配了的那 10 张记 ACCNUM03。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from account_rules_fixture import glacc, seed
from companion import dbf_sales as WS
from companion import dbf_schema as S
from test_stock_location import REVENUE_ACC, _append, _item, _rows, _sale, _sell, acct  # noqa: F401

SERVICE_REV, OTHER_REV, ICE_REV = "41-02-00-00", "41-03-00-00", "41-01-02-00"
DEPOSIT, ASSET, EXPENSE = "21-03-00-00", "14-01-09-00", "52-08-01-00"


@pytest.fixture
def book(acct):
    """服务品 SRV01「ค่าติดตั้ง」在科目组 SV01（收入 41-02-00-00）；库存品 ICE01 / ICE02 在 ST01（没配收入科目）。"""
    _append(acct / "STMAS.DBF", [{"STKCOD": "SRV01", "STKDES": "ค่าติดตั้ง", "STKTYP": "3", "ACCCOD": "SV01", "QUCOD": "ถง"}])
    seed(acct, "ISACC", [{"ACCTYP": "S", "ACCCOD": "SV01", "METHOD": "-", "ACCNUM01": SERVICE_REV, "ACCNUM03": SERVICE_REV}])
    glacc(acct, (REVENUE_ACC, "รายได้จากการขาย"), (SERVICE_REV, "รายได้ค่าบริการ"), (OTHER_REV, "รายได้อื่น"),
          (ICE_REV, "รายได้ขายน้ำแข็ง"))
    seed(acct, "GLACC", [{"ACCNUM": DEPOSIT, "ACCNAM": "เงินรับล่วงหน้า", "ACCTYP": "0", "GROUP": "2"},
                         {"ACCNUM": ASSET, "ACCNAM": "อุปกรณ์", "ACCTYP": "0", "GROUP": "1"},
                         {"ACCNUM": EXPENSE, "ACCNAM": "ค่าขนส่ง", "ACCTYP": "0", "GROUP": "5"}])
    return acct


def _credits(acct, docnum, seqit=S.SALES_SEQIT["revenue"]):
    return sorted((r["ACCNUM"].strip(), float(r["AMOUNT"])) for r in _rows(acct, "GLJNLIT", VOUCHER=docnum)
                  if r["TRNTYP"].strip() == "1" and r["SEQIT"].strip() == seqit)


def _balanced(acct, docnum):
    rows = _rows(acct, "GLJNLIT", VOUCHER=docnum)
    dr = sum(Decimal(str(r["AMOUNT"])) for r in rows if r["TRNTYP"].strip() == "0")
    cr = sum(Decimal(str(r["AMOUNT"])) for r in rows if r["TRNTYP"].strip() == "1")
    return dr == cr


def _service(amount=300.0):
    return {"name": "ค่าติดตั้ง", "qty": "1", "unit_price": str(amount), "amount": f"{amount:.2f}", "unit": "ครั้ง",
            "item_mode": "non_stock_item"}


def _set_group(acct, acccod, **fields):
    import dbf
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


def test_an_existing_service_item_credits_its_groups_revenue_account(book):
    res = _sell(book, _sale([_service()]))
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(SERVICE_REV, 300.0)]
    assert _balanced(book, res.docnum)


def test_a_mixed_sale_splits_revenue_by_group_and_keeps_the_rest_on_the_payload_account(book):
    payload = _sale([_item("ICE01", 2, 40.0, "stock_sale"), _service(), _item("ICE02", 1, 10.0, "stock_sale")],
                    payload_version=19)
    res = _sell(book, payload)
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(REVENUE_ACC, 50.0), (SERVICE_REV, 300.0)]
    assert _balanced(book, res.docnum)


def test_stock_items_whose_group_has_a_revenue_account_credit_it(book):
    _set_group(book, "ST01", ACCNUM03=ICE_REV)
    res = _sell(book, _sale([_item("ICE01", 2, 40.0, "stock_sale")]))
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(ICE_REV, 40.0)]
    assert _balanced(book, res.docnum)


def test_stock_items_without_a_group_revenue_account_keep_the_payload_account(book):
    res = _sell(book, _sale([_item("ICE01", 2, 40.0, "stock_sale")]))
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(REVENUE_ACC, 40.0)]


def test_a_group_revenue_account_that_cannot_be_posted_keeps_the_payload_account(book):
    _set_group(book, "SV01", ACCNUM03="49-99-99-99")  # 不在科目表里
    res = _sell(book, _sale([_service()]))
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(REVENUE_ACC, 300.0)]


@pytest.mark.parametrize("account, credited", [
    (DEPOSIT, DEPOSIT),  # 负债类（预收定金）：照原生贷它（S 盘 5,184 行）
    (ASSET, ASSET),  # 资产类：照原生（1,528 行）
    (EXPENSE, REVENUE_ACC),  # 费用类：照载荷（原生只有 51 行；按品名配到采购侧建的费用品不能把收入贷进费用）
])
def test_the_group_account_class_decides(book, account, credited):
    _set_group(book, "SV01", ACCNUM03=account)
    res = _sell(book, _sale([_service()]))
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(credited, 300.0)]
    assert _balanced(book, res.docnum)


def test_the_result_carries_the_revenue_actually_credited(book):
    res = _sell(book, _sale([_service(), _item("ICE01", 1, 20.0, "stock_sale")], payload_version=19))
    assert res.ok, res.error
    assert sorted(res.revenue, key=lambda r: r["acc"]) == [{"acc": REVENUE_ACC, "amount": "20.00"},
                                                           {"acc": SERVICE_REV, "amount": "300.00"}]


def test_a_direct_account_line_is_unchanged(book):
    items = [{"name": "ค่าขนส่ง", "qty": "1", "unit_price": "80", "amount": "80.00", "item_mode": "direct_account",
              "account": OTHER_REV}]
    payload = _sale(items, lines=[{"acc": "11-02-01-00", "side": "D", "role": "asset", "amount": "85.60"},
                                  {"acc": OTHER_REV, "side": "C", "role": "revenue", "amount": "80.00"},
                                  {"acc": "11-05-04-02", "side": "C", "role": "output_vat", "amount": "5.60"}])
    res = _sell(book, payload)
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(OTHER_REV, 80.0)]


def test_a_cash_sale_follows_the_same_rule(book):
    res = _sell(book, _sale([_service(), _item("ICE01", 1, 20.0, "stock_sale")], doctype="HS", payload_version=19))
    assert res.ok, res.error
    assert _credits(book, res.docnum) == [(REVENUE_ACC, 20.0), (SERVICE_REV, 300.0)]
    assert _balanced(book, res.docnum)


def test_lines_are_taken_out_of_the_revenue_row_of_their_own_account():
    """ERP 按行科目拆了几条收入：每行从科目 = 它 ``account`` 的那条里扣。"""
    rows = [("2", "11-02-01-00", "0", Decimal("535.00")), ("5", REVENUE_ACC, "1", Decimal("200.00")),
            ("5", OTHER_REV, "1", Decimal("300.00")), ("6", "11-05-04-02", "1", Decimal("35.00"))]
    items = [{"amount": "200.00", "account": REVENUE_ACC}, {"amount": "300.00", "account": OTHER_REV}]
    modes = [{"seq": 1, "stkcod": "ICE01"}, {"seq": 2, "stkcod": "SRV01"}]
    out = _journal(rows, items, modes, {"SRV01": SERVICE_REV})
    assert out == [rows[0], rows[1], ("5", SERVICE_REV, "1", Decimal("300.00")), rows[3]]


def test_lines_worth_more_than_their_revenue_row_leave_it_as_the_payload_says():
    """整单折扣等：行额合计超过那条收入 → 那条照载荷，不改、不报错。"""
    rows = [("2", "11-02-01-00", "0", Decimal("214.00")), ("5", REVENUE_ACC, "1", Decimal("200.00")),
            ("6", "11-05-04-02", "1", Decimal("14.00"))]
    out = _journal(rows, [{"amount": "250.00"}], [{"seq": 1, "stkcod": "SRV01"}], {"SRV01": SERVICE_REV})
    assert out == rows


def _journal(rows, items, modes, groups):
    from unittest import mock
    with mock.patch.object(WS.account_rules, "item_group_accounts", lambda *_a: groups), \
            mock.patch.object(WS.account_rules, "postable_account", lambda *_a: True), \
            mock.patch.object(WS.account_rules, "account_group", lambda *_a: "4"):
        return WS.revenue_journal("unused", rows, items, modes)
