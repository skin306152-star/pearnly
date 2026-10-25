# -*- coding: utf-8 -*-
"""收银退货单 SR 写进 Express（载荷 10，dbf_refund；ERP B3b）。合成账套，先用 CC 自己的现销日汇总写法写一张原单 HS，再退货。

逐字段对照服务器测试账套实录 SR0000001（挂 HS0000039，退 1 个、100 含税，退现金）：ARTRN（RECTYP 5、CNTYP 3、SONUM、IVCAMT、RCVAMT、
CMPLAPP）、STCRD（POSOPR 2、FLAG 2、RETSTK Y、RDOCNUM「原单号补空格到 14 位 + 行号」、含税的 UNITPR / TRNVAL、税前的 NETVAL / XSALVAL、
原单那一行的成本）、STMAS / STLOC 回仓、凭证 JNLTYP 03（借退货 1 / 借销项税 2 / 贷现金 3、借存货 8 / 贷成本 9）、ISVAT（RECTYP 5 正数、
REFNUM = SR 单号）、ISRUN SR +1、ARBAL SR 桶、ARMAS.BALANCE 冲减。另覆盖：非现金退款的反向转账凭证、部分退后再退、四种拒收与
存货认不出时一行不写、读回不符整单回滚、载荷校验与旧版 CC 桌面版拒收。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

import dbf
import pytest

from account_rules_fixture import glacc, isinfo, seed
from companion import dbf_refund
from companion import dbf_sales as WS
from companion import fiscal_period
from companion import payload_contract
from test_stock_location import COGS_ACC, INVENTORY_ACC, _no_runner, _rows, _state, _stloc, acct  # noqa: F401

CASH, QR_ACC, REFUND_ACC, REVENUE, OUTPUT_VAT = "11-01-01-00", "11-02-01-01", "41-02-00-00", "41-01-00-00", "11-05-04-02"
CUSTOMER = "เงินสด"


@pytest.fixture
def book(acct):
    isinfo(acct, MAINLOC="01", ACCNUM00=CASH, ISPERPETUA="Y")
    glacc(acct, (CASH, "เงินสด"), (QR_ACC, "ธนาคาร QR"), (REFUND_ACC, "รับคืน/ลดหนี้"), (REVENUE, "รายได้"),
          (OUTPUT_VAT, "ภาษีขาย"))
    seed(acct, "ISRUN", [{"DOCTYP": "HS", "PREFIX": "HS", "DOCNUM": "0000039", "JNLTYP": "03"},
                         {"DOCTYP": "SR", "PREFIX": "SR", "DOCNUM": "0000001", "JNLTYP": "03"},
                         {"DOCTYP": "GL", "DOCCOD": "00", "PREFIX": "JV", "DOCNUM": "", "JNLTYP": ""}])
    seed(acct, "ARMAS", [{"CUSCOD": CUSTOMER, "CUSNAM": CUSTOMER, "BALANCE": 160.5}])
    res = _write(acct, _sale())
    assert res.ok, res.error
    assert res.docnum == "HS691025-001"
    return acct


def _sale(qty=2, total="200.00", base="186.92", vat="13.08"):
    """原单：收银日汇总现销 HS（CC 现有写法，载荷 3 / 4），ICE01 卖 2 个、200 含税。"""
    return {"payload_version": 4, "direction": "sales", "doctype": "HS", "account_set": "DATAT", "docdate_be": "691025",
            "vat_period_be": "691001", "ref_no": "SI2569-000001",
            "customer": {"code": CUSTOMER, "name": CUSTOMER, "tax_id": "", "prename": "", "customer_new": False},
            "vat_rate": 7.0, "base_amount": base, "vat_amount": vat, "total_amount": total,
            "lines": [{"acc": CASH, "side": "D", "amount": total, "desc": CUSTOMER},
                      {"acc": REVENUE, "side": "C", "amount": base, "desc": "รายได้จากการขาย"},
                      {"acc": OUTPUT_VAT, "side": "C", "amount": vat, "desc": "ภาษีขาย"}],
            "items": [{"name": "น้ำแข็งหลอด", "code": "ICE01", "qty": str(qty), "unit_price": "93.46", "amount": base,
                       "unit": "ถัง", "item_mode": "stock_sale", "loccod": "01"}],
            "items_status": "ok", "items_account": REVENUE,
            "pos": {"first": "SI2569-000001", "last": "SI2569-000001", "count": 1, "docnum": "auto", "cash": total,
                    "transfers": []}}


def _refund(qty="1", total="100.00", base="93.46", vat="6.54", *, against="HS691025-001", against_total="200.00",
            code="ICE01", loccod="01", transfers=None, cash=None, **over):
    """退货单（共用写账规则出的载荷 10 的样子）：退 1 个、100 含税，退现金。"""
    transfers = transfers or []
    if cash is None:
        cash = format(Decimal(total) - sum((Decimal(row["amount"]) for row in transfers), Decimal(0)), "f")
    lines = [{"acc": REFUND_ACC, "side": "D", "amount": base, "desc": "ลดหนี้/รับคืน", "role": "refund"}]
    if Decimal(vat) > 0:
        lines.append({"acc": OUTPUT_VAT, "side": "D", "amount": vat, "desc": "ภาษีขาย", "role": "output_vat"})
    lines.append({"acc": CASH, "side": "C", "amount": total, "desc": CUSTOMER, "role": "cash"})
    payload = {"payload_version": 10, "direction": "sales", "doctype": "SR", "account_set": "DATAT",
               "docdate_be": "691026", "vat_period_be": "691001", "ref_no": "SI2569-000009",
               "customer": {"code": CUSTOMER, "name": CUSTOMER, "tax_id": "", "prename": "", "customer_new": False},
               "vat_rate": 7.0 if Decimal(vat) > 0 else 0.0, "base_amount": base, "vat_amount": vat,
               "total_amount": total, "lines": lines,
               "items": [{"name": "น้ำแข็งหลอด", "code": code, "qty": qty, "unit_price": base, "amount": base,
                          "unit": "ถัง", "item_mode": "stock_sale", "loccod": loccod}],
               "items_status": "ok", "items_account": REVENUE,
               "pos": {"first": "SI2569-000009", "last": "SI2569-000009", "count": 1, "docnum": "auto", "cash": cash,
                       "transfers": transfers},
               "refund": {"against": against, "against_total": against_total, "cntyp": 3, "retstk": "Y"}}
    payload.update(over)
    return payload


def _write(acct, payload, **kw):
    return WS.write_sale(payload, acct, allowed_sets=("DATAT",), selected_dir=str(acct),
                         make_backup=kw.pop("make_backup", False), **kw)


def _journal(acct, voucher):
    return sorted((row["SEQIT"].strip(), row["ACCNUM"].strip(), row["TRNTYP"].strip(), Decimal(str(row["AMOUNT"])))
                  for row in _rows(acct, "GLJNLIT", VOUCHER=voucher))


def _sr_bucket(acct):
    field = fiscal_period.resolve_month_field(acct, dt.date(2026, 10, 26), "SR")[0]
    return _rows(acct, "ARBAL", CUSCOD=CUSTOMER)[0][field]


def test_a_cash_refund_writes_every_table_like_the_recorded_sr(book):
    res = _write(book, _refund())
    assert res.ok, res.error
    docnum = res.docnum
    assert docnum == "SR691026-001" and res.party_code == CUSTOMER
    head = _rows(book, "ARTRN", DOCNUM=docnum)
    assert len(head) == 1
    head = head[0]
    assert (head["RECTYP"], head["CNTYP"], head["SONUM"].strip(), head["FLGVAT"]) == ("5", "3", "HS691025-001", "1")
    assert (head["AMOUNT"], head["AFTDISC"], head["TOTAL"], head["NETAMT"]) == (100.0, 100.0, 100.0, 100.0)
    assert (head["NETVAL"], head["VATAMT"], head["RCVAMT"], head["IVCAMT"]) == (93.46, 6.54, 100.0, 200.0)
    assert (head["REMAMT"], head["CSHRCV"]) == (0.0, 0.0)
    assert (head["CMPLAPP"], head["CMPLDAT"], head["DOCSTAT"], head["SRV_VATTYP"]) == ("Y", dt.date(2026, 10, 26), "N", "1")
    assert head["NXTSEQ"].strip() == "1" and head["CUSCOD"].strip() == CUSTOMER and head["YOUREF"].strip() == "SI2569-000009"

    line = _rows(book, "STCRD", DOCNUM=docnum)
    assert len(line) == 1
    line = line[0]
    assert (line["POSOPR"], line["FLAG"], line["RETSTK"]) == ("2", "2", "Y")
    assert line["RDOCNUM"].rstrip() == "HS691025-001  1"  # 原单号补空格到 14 位 + 原单行号（实录「HS0000039     1」）
    assert (line["STKCOD"].strip(), line["LOCCOD"].strip(), line["PEOPLE"].strip()) == ("ICE01", "01", CUSTOMER)
    assert (line["TRNQTY"], line["UNITPR"], line["TRNVAL"]) == (1.0, 100.0, 100.0)  # 含税（照原生 SR）
    assert (line["NETVAL"], line["XSALVAL"]) == (93.46, 93.46)  # 税前
    assert (line["XTRNQTY"], line["XUNITPR"], line["XTRNVAL"]) == (1.0, 10.0, 10.0)  # 原单那一行的成本
    assert (line["MREMBAL"], line["MREMVAL"]) == (4.0, 40.0)  # 期初 5 卖 2 退 1

    master = _rows(book, "STMAS", STKCOD="ICE01")[0]
    assert (master["MREMBAL"], master["MREMVAL"], master["TOTBAL"], master["TOTVAL"]) == (4.0, 40.0, 4.0, 40.0)
    stloc = _stloc(book, "ICE01", "01")
    assert stloc["LOCBAL"] == 4.0 and stloc["QTY10"] == -1.0 and stloc["VAL10"] == -10.0  # 10 月：卖 −2 / −20、退 +1 / +10
    assert stloc["LMOVDAT"] == dt.date(2026, 10, 26) and stloc["LSELDAT"] == dt.date(2026, 10, 25)

    heads = _rows(book, "GLJNL", VOUCHER=docnum)
    assert len(heads) == 1 and heads[0]["JNLTYP"] == "03" and heads[0]["DESCRP"].strip() == f"ลดหนี้/รับคืน {CUSTOMER}"
    assert _journal(book, docnum) == sorted([
        ("1", REFUND_ACC, "0", Decimal("93.46")), ("2", OUTPUT_VAT, "0", Decimal("6.54")), ("3", CASH, "1", Decimal("100.00")),
        ("8", INVENTORY_ACC, "0", Decimal("10.00")), ("9", COGS_ACC, "1", Decimal("10.00"))])

    vat = _rows(book, "ISVAT", DOCNUM=docnum)
    assert len(vat) == 1
    vat = vat[0]
    assert (vat["VATREC"], vat["RECTYP"], vat["AMT01"], vat["VAT01"]) == ("S", "5", 93.46, 6.54)
    assert vat["REFNUM"].strip() == docnum and vat["DESCRP"].strip() == CUSTOMER and vat["DOCSTAT"] == "N"
    assert vat["VATPRD"] == dt.date(2026, 10, 1)

    assert _rows(book, "ISRUN", PREFIX="SR")[0]["DOCNUM"].strip() == "0000002"
    assert _sr_bucket(book) == 100.0
    assert _rows(book, "ARMAS", CUSCOD=CUSTOMER)[0]["BALANCE"] == 60.5  # 实录：160.5 → 60.5
    assert [mode["mode"] for mode in res.line_modes] == ["stock_sale"]


def test_a_non_cash_refund_writes_a_reversed_transfer_voucher(book):
    res = _write(book, _refund(transfers=[{"acc": QR_ACC, "amount": "60.00", "label": "พร้อมเพย์"}]))
    assert res.ok, res.error
    vouchers = res.notes["pos_vouchers"]
    assert vouchers == ["JV691026-001"]
    assert _journal(book, vouchers[0]) == [("1", CASH, "0", Decimal("60.00")), ("2", QR_ACC, "1", Decimal("60.00"))]
    assert _rows(book, "GLJNL", VOUCHER=vouchers[0])[0]["JNLTYP"] == "00"


def test_a_second_refund_counts_what_was_already_returned(book):
    assert _write(book, _refund()).ok
    second = _write(book, _refund())
    assert second.ok, second.error
    assert second.docnum == "SR691026-002"
    assert _rows(book, "STCRD", DOCNUM=second.docnum)[0]["RDOCNUM"].rstrip() == "HS691025-001  1"
    before = _state(book)
    third = _write(book, _refund())  # 原单卖 2、已退 2
    assert not third.ok and third.error_code == dbf_refund.ERR_EXCEEDS
    assert _state(book) == before


@pytest.mark.parametrize("payload, code", [
    (_refund(against="HS691025-009"), "REFUND_ORIGINAL_NOT_FOUND"),
    (_refund(against_total="210.00"), "REFUND_ORIGINAL_CHANGED"),
    (_refund(code="ICE02"), "REFUND_LINE_NOT_FOUND"),
    (_refund(loccod="02"), "REFUND_LINE_NOT_FOUND"),  # 原单是 01 仓卖的
    (_refund(qty="3", total="300.00", base="280.37", vat="19.63"), "REFUND_EXCEEDS_ORIGINAL"),
    (_refund(code="NOPE"), "STOCK_ITEM_NOT_FOUND"),
])
def test_what_cannot_be_returned_writes_nothing(book, payload, code):
    before = _state(book)
    res = _write(book, payload, make_backup=True)
    assert not res.ok and res.error_code == code, res.error
    assert _state(book) == before


def test_a_voided_original_is_not_found(book):
    t = dbf.Table(str(book / "ARTRN.DBF"), codepage="cp874")
    t.open(dbf.READ_WRITE)
    try:
        for row in t:
            if (row["DOCNUM"] or "").strip() == "HS691025-001":
                with row as rec:
                    rec["DOCSTAT"] = "C"
    finally:
        t.close()
    res = _write(book, _refund())
    assert not res.ok and res.error_code == "REFUND_ORIGINAL_NOT_FOUND"


def test_a_failed_readback_rolls_everything_back(book):
    before = _state(book)
    with mock.patch.object(dbf_refund, "_verify", side_effect=dbf_refund._WriteError("boom", "READBACK_VERIFY_FAILED")):
        res = _write(book, _refund(transfers=[{"acc": QR_ACC, "amount": "60.00", "label": "พร้อมเพย์"}]),
                     make_backup=True)
    assert not res.ok and res.error_code == "READBACK_VERIFY_FAILED"
    assert _state(book) == before


@pytest.mark.parametrize("change", [
    lambda p: p.pop("refund"),                                       # SR 只在带 refund 块时认
    lambda p: p.update(doctype="HS"),                                # refund 块必须是 SR
    lambda p: p["refund"].update(against="IV691025-001"),            # 原单必须是现销 HS
    lambda p: p["refund"].update(against="HS691025-0001"),           # 超过 12 字节
    lambda p: p["refund"].update(against_total="0"),
    lambda p: p["refund"].update(cntyp=2),                           # 只支持退现金
    lambda p: p["refund"].update(retstk="N"),                        # 只支持货回仓
    lambda p: p.pop("pos"),
    lambda p: p["items"][0].pop("code"),                             # 明细必须带存货码
    lambda p: p["items"][0].update(item_mode="non_stock_item"),
    lambda p: p.update(items_status="empty"),
    lambda p: p["lines"].reverse(),                                  # 分录角色顺序
    lambda p: p["lines"][0].update(amount="90.00", side="D"),
    lambda p: p["pos"].update(cash="-10.00", transfers=[{"acc": QR_ACC, "amount": "110.00", "label": "QR"}]),
])
def test_a_malformed_refund_is_refused_before_touching_the_account_set(book, change):
    payload = _refund()
    change(payload)
    before = _state(book)
    res = _write(book, payload)
    assert not res.ok and res.error_code == "PAYLOAD_INVALID", res.error
    assert _state(book) == before


def test_payload_ten_is_supported_and_older_cc_desktops_refuse_it():
    assert payload_contract.check_payload_version(_refund()) is None
    older = frozenset(range(1, 10))
    with mock.patch.object(payload_contract, "SUPPORTED_PAYLOAD_VERSIONS", older):
        assert payload_contract.check_payload_version(_refund()) == payload_contract.ERR_PAYLOAD_VERSION


def test_refusals_need_a_person_and_ordinary_sales_never_reach_the_refund_writer(book):
    from companion import worker

    for code in dbf_refund.REFUSALS:
        assert worker._outcome_for(code) == "needs_review"
    with mock.patch.object(dbf_refund, "write_refund", side_effect=AssertionError("not a refund")):
        res = _write(book, _sale(qty=1, total="100.00", base="93.46", vat="6.54"))
    assert res.ok, res.error
    assert res.docnum.startswith("HS")
