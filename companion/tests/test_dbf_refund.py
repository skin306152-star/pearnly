# -*- coding: utf-8 -*-
"""收银退货单 SR 写进 Express（载荷 10，dbf_refund；ERP B3b）。合成账套，先用 CC 自己的现销日汇总写法写一张原单 HS，再退货。

逐字段对照服务器测试账套实录 SR0000001（挂 HS0000039，退 1 个、100 含税，退现金）：ARTRN（RECTYP 5、CNTYP 3、SONUM、IVCAMT、RCVAMT、
CMPLAPP）、STCRD（POSOPR 2、FLAG 2、RETSTK Y、RDOCNUM C(15)「原单号左对齐、行号右对齐到第 15 位」、含税的 UNITPR / TRNVAL、税前的 NETVAL / XSALVAL、
原单那一行的成本）、STMAS / STLOC 回仓、凭证 JNLTYP 03（借退货 1 / 借销项税 2 / 贷现金 3、借存货 8 / 贷成本 9）、ISVAT（RECTYP 5 正数、
REFNUM = SR 单号）、ISRUN SR +1、ARBAL SR 桶、ARMAS.BALANCE 冲减。另覆盖：非现金退款的反向转账凭证、部分退后再退、四种拒收与
存货认不出时一行不写、读回不符整单回滚、载荷校验与旧版 CC 桌面版拒收；原单第 12 行的 RDOCNUM 正好 15 位、已退数量认得到；
非库存行（服务、费用）只记金额与总账；同一张退货汇总换电脑 / 重领任务 / 回执丢失后重发只写一张（写前按 SONUM + YOUREF + DOCDAT + NETAMT 判重）。
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
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
SERVICE_ACC = "41-03-00-00"  # Express 服务类物料（编码就是科目，ERP 前台按行带 account）
CUSTOMER = "เงินสด"


@pytest.fixture
def book(acct):
    isinfo(acct, MAINLOC="01", ACCNUM00=CASH, ISPERPETUA="Y")
    glacc(acct, (CASH, "เงินสด"), (QR_ACC, "ธนาคาร QR"), (REFUND_ACC, "รับคืน/ลดหนี้"), (REVENUE, "รายได้"),
          (OUTPUT_VAT, "ภาษีขาย"), (SERVICE_ACC, "รายได้ค่าบริการ"))
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
    second = _write(book, _refund(ref_no="SI2569-000010"))  # 另一张退货汇总（票号区间不同）
    assert second.ok and not second.skipped, second.error
    assert second.docnum == "SR691026-002"
    assert _rows(book, "STCRD", DOCNUM=second.docnum)[0]["RDOCNUM"].rstrip() == "HS691025-001  1"
    before = _state(book)
    third = _write(book, _refund(ref_no="SI2569-000011"))  # 原单卖 2、已退 2
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
    lambda p: p["items"][0].update(item_mode="stock_item"),          # 行模式认不出（进货的库存行）
    lambda p: p["items"][0].update(qty="0"),
    lambda p: p["lines"][0].update(acc=""),
    lambda p: p["lines"].insert(0, {"acc": SERVICE_ACC, "side": "D", "amount": "1.00", "role": "refund"}),  # 借方合计 ≠ 税前
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


def test_the_original_line_number_is_right_aligned_to_fifteen_like_native_express():
    """RDOCNUM C(15)：原单号左对齐、原单行号右对齐到第 15 位（10-26 测试机 S 盘副本 1039 本账套读到的原生写法；
    验收：原来「补到 14 位再接行号」从第 10 行起多出一位）。"""
    assert dbf_refund.rdocnum("HS0000039", 1) == "HS0000039     1"   # 实录 SR0000001
    assert dbf_refund.rdocnum("IV5801890", 12) == "IV5801890    12"  # S 盘副本原生 SR
    assert dbf_refund.rdocnum("RR2016055", 100) == "RR2016055   100"
    assert dbf_refund.rdocnum("HS691025-001", 10) == "HS691025-001 10"
    assert dbf_refund.rdocnum("HS691025-001", 100) == "HS691025-001100"
    assert all(len(dbf_refund.rdocnum(doc, seq)) == 15 for doc in ("HS0000039", "HS691025-001") for seq in (1, 9, 10, 99, 100))


def _origin(seq, stkcod="ICE01"):
    return dbf_refund._Origin(str(seq), stkcod, "01", Decimal(10), Decimal(100), Decimal(10))


def test_earlier_returns_are_counted_per_original_line_from_ten_and_up(book):
    """以前的退货单按原单行号累计已退数量：第 10、100 行照样认；别的单号以它开头的不算。"""
    seed(book, "STCRD", [{"DOCNUM": "SR0000005", "SEQNUM": "1", "POSOPR": "2", "XTRNQTY": 1.0, "STKCOD": "ICE01",
                          "RDOCNUM": dbf_refund.rdocnum("HS0000039", 12)},
                         {"DOCNUM": "SR0000005", "SEQNUM": "2", "POSOPR": "2", "XTRNQTY": 2.0, "STKCOD": "ICE01",
                          "RDOCNUM": dbf_refund.rdocnum("HS0000039", 100)},
                         {"DOCNUM": "SR0000006", "SEQNUM": "1", "POSOPR": "2", "XTRNQTY": 4.0, "STKCOD": "ICE01",
                          "RDOCNUM": dbf_refund.rdocnum("HS0000039", 12)},
                         {"DOCNUM": "SR0000007", "SEQNUM": "1", "POSOPR": "2", "XTRNQTY": 9.0, "STKCOD": "ICE01",
                          "RDOCNUM": dbf_refund.rdocnum("HS00000391", 2)}])
    origins = [_origin(seq) for seq in (1, 2, 12, 100)]
    assert dbf_refund._returned(book, "HS0000039", origins) == {"12": Decimal("5"), "100": Decimal("2")}


def test_a_longer_original_number_is_not_read_as_a_line_of_a_shorter_one(book):
    """验收 10-26：单号长短不一时不按前缀认。14 位单号第 1 行「HS000003912345」+「1」与 13 位单号第 51 行拼出来一样，
    只认原单真有的行号、物料码也对得上的。"""
    seed(book, "STCRD", [{"DOCNUM": "SR0000008", "SEQNUM": "1", "POSOPR": "2", "XTRNQTY": 3.0, "STKCOD": "ICE02",
                          "RDOCNUM": dbf_refund.rdocnum("HS000003912345", 1)},
                         {"DOCNUM": "SR0000009", "SEQNUM": "1", "POSOPR": "2", "XTRNQTY": 7.0, "STKCOD": "ICE01",
                          "RDOCNUM": dbf_refund.rdocnum("HS00000391234", 5)}])
    assert dbf_refund.rdocnum("HS000003912345", 1) == dbf_refund.rdocnum("HS00000391234", 51)
    assert dbf_refund._returned(book, "HS00000391234", [_origin(5), _origin(51)]) == {"5": Decimal("7")}
    assert dbf_refund._returned(book, "HS00000391234", [_origin(5)]) == {"5": Decimal("7")}  # 原单没有第 51 行


def test_the_exempt_part_of_a_refund_goes_into_amtrat0(book):
    """实录第 10 条（Express 原生写法）：混有免税商品的退货单，销项税记录 AMT01 只记应税部分、免税部分记 AMTRAT0，照载荷 ``vat_fields``。"""
    res = _write(book, _refund(vat_fields={"AMT01": "60.00", "AMTRAT0": "33.46"}))
    assert res.ok, res.error
    vat = _rows(book, "ISVAT", DOCNUM=res.docnum)[0]
    assert (Decimal(str(vat["AMT01"])), Decimal(str(vat["AMTRAT0"])), Decimal(str(vat["VAT01"]))) == (
        Decimal("60.00"), Decimal("33.46"), Decimal("6.54"))


def _mixed_sale(*, exempt_only=False):
    """登记户的现销日汇总混有免税商品（实录第 10 条，原生 HH2200117 的口径）：ICE01 应税 100 + ICE02 免税 50，含税 157；
    ``exempt_only``：整张都是免税的 ICE02 50（AMT01 0、AMTRAT0 全额）。"""
    ice = {"name": "น้ำแข็งก้อน", "code": "ICE02", "qty": "1", "unit_price": "50.00", "amount": "50.00", "unit": "ถัง",
           "item_mode": "stock_sale", "loccod": "01", "vat_exempt": True}
    if exempt_only:
        sale = _sale_of([ice], ref_no="SI2569-000003", total="50.00", base="50.00", vat="0.00")
        sale.update(payload_version=9, vat_rate=0.0, vat_fields={"AMT01": "0.00", "AMTRAT0": "50.00"})
        sale["lines"] = [line for line in sale["lines"] if line["acc"] != OUTPUT_VAT]
        return sale
    taxable = {"name": "น้ำแข็งหลอด", "code": "ICE01", "qty": "1", "unit_price": "100.00", "amount": "100.00", "unit": "ถัง",
               "item_mode": "stock_sale", "loccod": "01"}
    sale = _sale_of([taxable, ice], ref_no="SI2569-000002", total="157.00", base="150.00", vat="7.00")
    sale.update(payload_version=9, vat_fields={"AMT01": "100.00", "AMTRAT0": "50.00"})
    return sale


def test_a_mixed_sale_and_its_refund_write_exempt_lines_like_native_express(book):
    """实录第 10 条（S 盘副本 6412NLT 的 HH2200117）：免税行 STCRD.VATCOD '0'、NETVAL = TRNVAL，应税行 VATCOD 空；ARTRN.AMTRAT0 与
    ISVAT.AMTRAT0 记免税金额、ISVAT.AMT01 只记应税部分。退货单 SR 同一口径。"""
    sale = _write(book, _mixed_sale())
    assert sale.ok, sale.error
    lines = {row["STKCOD"].strip(): row for row in _rows(book, "STCRD", DOCNUM=sale.docnum)}
    assert (lines["ICE01"]["VATCOD"].strip(), lines["ICE02"]["VATCOD"].strip()) == ("", "0")
    assert lines["ICE02"]["NETVAL"] == lines["ICE02"]["TRNVAL"]
    assert Decimal(str(_rows(book, "ARTRN", DOCNUM=sale.docnum)[0]["AMTRAT0"])) == Decimal("50")
    vat = _rows(book, "ISVAT", DOCNUM=sale.docnum)[0]
    assert (Decimal(str(vat["AMT01"])), Decimal(str(vat["AMTRAT0"])), Decimal(str(vat["VAT01"]))) == (
        Decimal("100"), Decimal("50"), Decimal("7"))
    refund = _refund(total="157.00", base="150.00", vat="7.00", against=sale.docnum, against_total="157.00",
                     vat_fields={"AMT01": "100.00", "AMTRAT0": "50.00"})
    refund["items"] = [{**item, "unit_price": item["amount"]} for item in _mixed_sale()["items"]]
    res = _write(book, refund)
    assert res.ok, res.error
    returned = {row["STKCOD"].strip(): row for row in _rows(book, "STCRD", DOCNUM=res.docnum)}
    assert (returned["ICE01"]["VATCOD"].strip(), returned["ICE02"]["VATCOD"].strip()) == ("", "0")
    assert (Decimal(str(returned["ICE01"]["TRNVAL"])), Decimal(str(returned["ICE02"]["TRNVAL"]))) == (Decimal("107"), Decimal("50"))
    assert Decimal(str(_rows(book, "ARTRN", DOCNUM=res.docnum)[0]["AMTRAT0"])) == Decimal("50")


def test_a_fully_exempt_sale_still_writes_its_output_vat_record(book):
    """实录第 10 条（整张免税的 SR002100392）：登记户整张都是免税商品，照样写销项税记录：AMT01 0、AMTRAT0 全额、税额 0。"""
    res = _write(book, _mixed_sale(exempt_only=True))
    assert res.ok, res.error
    vat = _rows(book, "ISVAT", DOCNUM=res.docnum)
    assert [(Decimal(str(row["AMT01"])), Decimal(str(row["AMTRAT0"])), Decimal(str(row["VAT01"]))) for row in vat] == [
        (Decimal("0"), Decimal("50"), Decimal("0"))]
    assert _rows(book, "STCRD", DOCNUM=res.docnum)[0]["VATCOD"].strip() == "0"


def test_an_exempt_line_takes_no_share_of_the_vat_in_its_gross_amount():
    """实验栈实测 10-26：应税 100 + 免税 50 退货、含税 157，原来按税前额占比摊成 104.67 / 52.33；免税行含税额就是税前额。"""
    taxable = {"amount": "100.00"}
    exempt = {"amount": "50.00", "vat_exempt": True}
    assert dbf_refund._gross([taxable, exempt], Decimal("157.00")) == [Decimal("107.00"), Decimal("50.00")]
    assert dbf_refund._gross([exempt, taxable, {"amount": "50.00"}], Decimal("210.50")) == [
        Decimal("50.00"), Decimal("107.00"), Decimal("53.50")]
    assert dbf_refund._gross([{**exempt}, {**exempt, "amount": "30.00"}], Decimal("80.00")) == [Decimal("50.00"), Decimal("30.00")]
    assert dbf_refund._gross([taxable], Decimal("107.00")) == [Decimal("107.00")]


def test_an_express_refund_against_an_old_cc_summary_is_counted_with_the_left_aligned_line(book):
    """验收 10-26 B3b 实录点 ①：Express 拼 RDOCNUM 是「原单号 C(12) + 原单 SEQNUM 原样」，存量 CC 日汇总的行号左对齐，Express 里对着它
    手工录的退货是「HS690705-0011␣␣」。已退数量两种写法都认（新写的明细照原生右对齐）。"""
    seed(book, "STCRD", [{"DOCNUM": "SR0000002", "SEQNUM": "1", "POSOPR": "2", "XTRNQTY": 1.0, "STKCOD": "ICE01",
                          "RDOCNUM": "HS690705-0011"},
                         {"DOCNUM": "SR0000003", "SEQNUM": "1", "POSOPR": "2", "XTRNQTY": 2.0, "STKCOD": "ICE01",
                          "RDOCNUM": dbf_refund.rdocnum("HS690705-001", 1)}])
    assert dbf_refund._returned(book, "HS690705-001", [_origin(1)]) == {"1": Decimal("3")}


def test_new_detail_lines_are_numbered_right_aligned_like_native_express(book):
    """新写的明细 SEQNUM 照 Express 原生右对齐（"  1"），Express 对着 CC 的单录退货时拼出的 RDOCNUM 与原生单一样。"""
    import dbf
    from companion import dbf_schema

    table = dbf.Table(str(book / "STCRD.DBF"), codepage=dbf_schema.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        raw = [str(rec.SEQNUM) for rec in table if str(rec.DOCNUM).strip() == "HS691025-001"]
    finally:
        table.close()
    assert raw == ["  1"]


def test_a_line_without_a_unit_takes_the_stock_unit_of_the_product(book):
    """验收 10-26 B3b 实录点 ①：CC 写的 HS 明细单位空着，Express 录退货时要人手补选单位、倍数默认 0（不改成 1 就报 13）。
    行上没单位（ERP 收银小票）时用这件品主档的库存单位、倍数 1。"""
    sale = _sale(qty=1, total="100.00", base="93.46", vat="6.54")
    sale["items"][0]["unit"] = ""
    sale.update(ref_no="SI2569-000011")
    res = _write(book, sale)
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    master = _rows(book, "STMAS", STKCOD="ICE01")[0]
    assert line["TQUCOD"].strip() == master["QUCOD"].strip() != "" and line["TFACTOR"] == 1.0


def _sale_of(items, *, ref_no, total, base, vat):
    """原单：收银日汇总现销 HS（CC 现有写法），明细照给的行。"""
    sale = _sale(total=total, base=base, vat=vat)
    sale.update(ref_no=ref_no, items=items)
    sale["pos"].update(first=ref_no, last=ref_no, cash=total)
    return sale


def test_returning_line_twelve_of_a_twelve_line_sale_fits_fifteen_and_is_counted_afterwards(book):
    """原单 12 行、退第 12 行：RDOCNUM 正好 15 位（合成表同真表 C(15)），读回时已退数量按第 12 行认得到、再退就退超。"""
    items = [{"name": "น้ำแข็งก้อน", "code": "ICE02", "qty": "1", "unit_price": "10.00", "amount": "10.00", "unit": "ถัง",
              "item_mode": "stock_sale", "loccod": "01"} for _ in range(11)]
    items.append({"name": "น้ำแข็งหลอด", "code": "ICE01", "qty": "1", "unit_price": "10.00", "amount": "10.00",
                  "unit": "ถัง", "item_mode": "stock_sale", "loccod": "01"})
    sale = _write(book, _sale_of(items, ref_no="SI2569-000002", total="128.40", base="120.00", vat="8.40"))
    assert sale.ok, sale.error
    assert sale.docnum == "HS691025-002"
    assert sorted(int(row["SEQNUM"]) for row in _rows(book, "STCRD", DOCNUM=sale.docnum))[-1] == 12

    refund = _refund(qty="1", total="10.70", base="10.00", vat="0.70", against=sale.docnum, against_total="128.40")
    res = _write(book, refund)
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)
    assert len(line) == 1
    assert line[0]["RDOCNUM"] == "HS691025-002 12" and len(line[0]["RDOCNUM"]) == 15
    assert dbf_refund._returned(book, sale.docnum, dbf_refund._origins(book, sale.docnum)) == {"12": Decimal("1")}
    again = _write(book, {**copy.deepcopy(refund), "ref_no": "SI2569-000010"})  # 第 12 行只卖了 1 个、已退 1 个
    assert not again.ok and again.error_code == dbf_refund.ERR_EXCEEDS, again.error


def _mixed():
    """退货里混有非库存行：ICE01 退 1 个（库存）+ 服务费（非库存，没带科目 → 借退货科目）+ 安装费（服务类物料，带自己的科目）。"""
    payload = _refund(total="174.90", base="163.46", vat="11.44")
    payload["items"] += [
        {"name": "ค่าบริการ", "qty": "1", "unit_price": "50.00", "amount": "50.00", "unit": "", "item_mode": "non_stock_item"},
        {"name": "ค่าติดตั้ง", "qty": "1", "unit_price": "20.00", "amount": "20.00", "unit": "", "item_mode": "direct_account",
         "account": SERVICE_ACC},
    ]
    payload["lines"][:1] = [
        {"acc": REFUND_ACC, "side": "D", "amount": "143.46", "desc": "ลดหนี้/รับคืน", "role": "refund"},
        {"acc": SERVICE_ACC, "side": "D", "amount": "20.00", "desc": "ลดหนี้/รับคืน", "role": "refund"},
    ]
    return payload


def test_non_stock_lines_of_a_refund_only_post_their_amount(book):
    master_before = _rows(book, "STMAS", STKCOD="ICE01")[0]
    res = _write(book, _mixed())
    assert res.ok, res.error
    head = _rows(book, "ARTRN", DOCNUM=res.docnum)[0]
    assert (head["NETAMT"], head["NETVAL"], head["VATAMT"]) == (174.9, 163.46, 11.44)
    assert head["NXTSEQ"].strip() == "1"  # 只数库存行
    lines = _rows(book, "STCRD", DOCNUM=res.docnum)
    assert [(row["SEQNUM"].strip(), row["STKCOD"].strip()) for row in lines] == [("1", "ICE01")]
    master = _rows(book, "STMAS", STKCOD="ICE01")[0]
    assert (master["MREMBAL"], master["MREMVAL"]) == (master_before["MREMBAL"] + 1, master_before["MREMVAL"] + 10)
    assert _journal(book, res.docnum) == sorted([
        ("1", REFUND_ACC, "0", Decimal("143.46")), ("1", SERVICE_ACC, "0", Decimal("20.00")),
        ("2", OUTPUT_VAT, "0", Decimal("11.44")), ("3", CASH, "1", Decimal("174.90")),
        ("8", INVENTORY_ACC, "0", Decimal("10.00")), ("9", COGS_ACC, "1", Decimal("10.00"))])
    assert _rows(book, "ISVAT", DOCNUM=res.docnum)[0]["AMT01"] == 163.46
    assert [(mode["seq"], mode["mode"]) for mode in res.line_modes] == [
        (1, "stock_sale"), (2, "non_stock_item"), (3, "direct_account")]


def test_a_refund_of_only_non_stock_lines_leaves_the_stock_alone(book):
    payload = _refund(total="53.50", base="50.00", vat="3.50")
    payload["items"] = [{"name": "ค่าบริการ", "qty": "1", "unit_price": "50.00", "amount": "50.00", "unit": "",
                         "item_mode": "non_stock_item"}]
    stock_before = (_rows(book, "STMAS", STKCOD="ICE01"), _stloc(book, "ICE01", "01"))
    res = _write(book, payload)
    assert res.ok, res.error
    assert _rows(book, "STCRD", DOCNUM=res.docnum) == []
    assert (_rows(book, "STMAS", STKCOD="ICE01"), _stloc(book, "ICE01", "01")) == stock_before
    assert _journal(book, res.docnum) == sorted([
        ("1", REFUND_ACC, "0", Decimal("50.00")), ("2", OUTPUT_VAT, "0", Decimal("3.50")), ("3", CASH, "1", Decimal("53.50"))])
    assert _rows(book, "ARTRN", DOCNUM=res.docnum)[0]["NXTSEQ"].strip() == "1"  # 同 HS 表头模式


# ── 换电脑 / 重领任务 / 回执丢失：只写一张 ─────────────────────────────────────────────────────────────


def _srs(book):
    return [row["DOCNUM"].strip() for row in _rows(book, "ARTRN", RECTYP="5", SONUM="HS691025-001")]


def test_the_same_refund_written_again_returns_the_one_already_there(book):
    first = _write(book, _refund())
    assert first.ok and first.written
    before = _state(book)
    again = _write(book, _refund(), make_backup=True)
    assert again.ok and again.skipped and not again.written, again.error
    assert (again.docnum, again.party_code) == (first.docnum, CUSTOMER)
    assert _state(book) == before
    assert _srs(book) == [first.docnum]


@pytest.mark.parametrize("change", [
    lambda p: p.update(ref_no="SI2569-000010"),        # 另一张退货汇总
    lambda p: p.update(docdate_be="691027"),           # 另一天
])
def test_a_different_refund_is_written(book, change):
    first = _write(book, _refund())
    payload = _refund()
    change(payload)
    second = _write(book, payload)
    assert second.ok and second.written, second.error
    assert second.docnum != first.docnum and len(_srs(book)) == 2


def test_a_voided_earlier_refund_does_not_count(book):
    first = _write(book, _refund())
    t = dbf.Table(str(book / "ARTRN.DBF"), codepage="cp874")
    t.open(dbf.READ_WRITE)
    try:
        for row in t:
            if (row["DOCNUM"] or "").strip() == first.docnum:
                with row as rec:
                    rec["DOCSTAT"] = "C"
    finally:
        t.close()
    second = _write(book, _refund())
    assert second.ok and second.written and second.docnum != first.docnum


class _Queue:
    """推送队列的回执口；``lose`` = 回执没送到服务器（断网 / 关机）。"""

    def __init__(self, lose=False):
        self.acks, self.lose = [], lose

    def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
        self.acks.append(("ok", extracted_invoice_no, meta))
        return not self.lose

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append(("failed", error_code, error))
        return not self.lose


def _deliver(monkeypatch, book, journal_dir, task_id, payload, *, lose_receipt=False):
    """CC 桌面版收到一个推送任务（worker 真流程：本机已写记录 → 找账套 → 写 → 回执）；``journal_dir`` 是这台电脑的本机记录目录。"""
    from companion import task_journal, worker

    journal_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(task_journal, "FILE", journal_dir / "written_tasks.json")
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, p: SimpleNamespace(
        account_dir=str(book), account_set="DATAT", account_company="", has_selection=lambda: True))
    queue = _Queue(lose=lose_receipt)
    worker._handle_dbf_task(SimpleNamespace(default_depcod="", ui_lang="en"), queue,
                            SimpleNamespace(id=task_id, payload=copy.deepcopy(payload), invoice_no="SI2569-000009"))
    return queue.acks[-1]


def test_two_computers_writing_the_same_refund_task_make_one_sr(book, tmp_path, monkeypatch):
    first = _deliver(monkeypatch, book, tmp_path / "pc-a", "ERP-PUSH-1", _refund())
    second = _deliver(monkeypatch, book, tmp_path / "pc-b", "ERP-PUSH-1", _refund())  # 另一台：本机记录里没有它
    assert first[0] == second[0] == "ok", (first, second)
    assert first[1] == second[1] == "SR691026-001"
    assert first[2]["written"] is True and second[2]["written"] is False and second[2]["duplicate_skipped"] is True
    assert _srs(book) == ["SR691026-001"]


def test_a_reclaimed_task_after_a_crash_before_the_journal_was_saved_makes_one_sr(book, tmp_path, monkeypatch):
    from companion import task_journal

    with mock.patch.object(task_journal, "remember", lambda *a, **kw: None):  # 写完、记本机记录前断电
        first = _deliver(monkeypatch, book, tmp_path / "pc-a", "ERP-PUSH-1", _refund())
    again = _deliver(monkeypatch, book, tmp_path / "pc-a", "ERP-PUSH-1", _refund())  # 租约到期、同一台重领
    assert first[:2] == again[:2] == ("ok", "SR691026-001")
    assert again[2]["duplicate_skipped"] is True
    assert _srs(book) == ["SR691026-001"]


def test_a_lost_receipt_then_the_same_payload_again_makes_one_sr(book, tmp_path, monkeypatch):
    first = _deliver(monkeypatch, book, tmp_path / "pc-a", "ERP-PUSH-1", _refund(), lose_receipt=True)
    resent = _deliver(monkeypatch, book, tmp_path / "pc-a", "ERP-PUSH-1", _refund())  # 服务器重发同一个任务
    pushed = _deliver(monkeypatch, book, tmp_path / "pc-b", "ERP-PUSH-2", _refund())  # ERP 原样再交一次（新任务、另一台）
    assert first[1] == resent[1] == pushed[1] == "SR691026-001"
    assert resent[2]["duplicate_skipped"] is True and pushed[2]["duplicate_skipped"] is True
    assert _srs(book) == ["SR691026-001"]
