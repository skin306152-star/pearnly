# -*- coding: utf-8 -*-
"""库存单写进 Express（载荷 6，dbf_stock；出入库第一期 B 段）。写法照 S 盘真账样本（方案九.7b）：

- 领用 OU：STTRN 单头（POSOPR 6、ACCNUMDR、货值合计）、STCRD 明细（POSOPR 6、移动均价、写后结存）、STMAS 与 STLOC 减少、
  凭证 JNLTYP 00「借 领用科目 / 贷 存货」、每行 SEQIT '1'、摘要取备注；
- 调整 JU（正）用载荷的单位成本、盘点 TK 有正有负：凭证按存货科目的净额与对方科目分借贷，没写备注是「ปรับปรุงสินค้า + 单号」；
- 定期盘存账套、零金额不过账；ISRUN 没有这个单别、码不在、科目不能记账、负库存不够 → 整单不写；读回不符 → 回滚。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest

from account_rules_fixture import glacc, isinfo, seed
from companion import dbf_stock
from companion import payload_contract
from companion import stock_guard
from test_stock_location import INVENTORY_ACC, _no_runner, _rows, _state, _stloc, acct  # noqa: F401

ISSUE_ACC, OFFSET_ACC = "53-01-00-00", "51-05-00-00"
TODAY = dt.date(2026, 10, 25)


@pytest.fixture
def book(acct):
    isinfo(acct, MAINLOC="01", ISPERPETUA="Y")
    glacc(acct, (ISSUE_ACC, "วัสดุสิ้นเปลืองใช้ไป"), (OFFSET_ACC, "ปรับปรุงสินค้า"))
    seed(acct, "ISRUN", [{"DOCTYP": "OU", "PREFIX": "OU", "DOCNUM": "0000001", "JNLTYP": "00", "ACCNUM01": ISSUE_ACC},
                         {"DOCTYP": "JU", "PREFIX": "JU", "DOCNUM": "0000001", "JNLTYP": "00", "ACCNUM02": OFFSET_ACC},
                         {"DOCTYP": "JU", "PREFIX": "TK", "DOCNUM": "0000001", "JNLTYP": "00", "ACCNUM02": OFFSET_ACC}])
    return acct


def _entry(doctype, items, **extra):
    return {"payload_version": 6, "direction": "stock", "doctype": doctype, "docdate_be": "690320",
            "ref_no": "IS2569-000001", "items": items, **extra}


def _line(code, qty, **extra):
    return {"code": code, "name": code, "qty": str(qty), "unit": "ถัง", **extra}


def _write(acct, payload, **kw):
    return dbf_stock.write_stock_entry(payload, acct, selected_dir=str(acct), make_backup=kw.pop("make_backup", False),
                                       today=TODAY, **kw)


def _journal(acct, docnum):
    heads = _rows(acct, "GLJNL", VOUCHER=docnum)
    lines = sorted((row["TRNTYP"].strip(), row["ACCNUM"].strip(), Decimal(str(row["AMOUNT"])), row["SEQIT"].strip())
                   for row in _rows(acct, "GLJNLIT", VOUCHER=docnum))
    return heads, lines


def test_an_issue_writes_the_header_the_detail_the_balances_and_the_journal_like_express(book):
    res = _write(book, _entry("OU", [_line("ICE01", 2)], remark="เบิกใช้ในสำนักงาน"))
    assert res.ok, res.error
    assert res.docnum == "OU690320-001"
    head = _rows(book, "STTRN", DOCNUM="OU690320-001")
    assert len(head) == 1
    head = head[0]
    assert head["POSOPR"] == "6" and head["ACCNUMDR"].strip() == ISSUE_ACC and head["TRNVAL"] == 20.0
    assert head["REMARK"].strip() == "เบิกใช้ในสำนักงาน" and head["REFNUM"].strip() == "IS2569-000001"
    assert head["LOCCOD"].strip() == "01" and head["DOCSTAT"] == "N" and head["NXTSEQ"].strip() == "1"
    line = _rows(book, "STCRD", DOCNUM="OU690320-001")[0]
    assert line["POSOPR"] == "6" and line["TRNQTY"] == 2.0 and line["XUNITPR"] == 10.0 and line["XTRNVAL"] == 20.0
    assert line["MREMBAL"] == 3.0 and line["MREMVAL"] == 30.0 and line["ACCNUMDR"].strip() == ISSUE_ACC
    master = _rows(book, "STMAS", STKCOD="ICE01")[0]
    assert (master["MREMBAL"], master["MREMVAL"], master["TOTBAL"]) == (3.0, 30.0, 3.0)
    stloc = _stloc(book, "ICE01", "01")
    assert stloc["LOCBAL"] == 3.0 and stloc["LMOVDAT"] == dt.date(2026, 3, 20)
    heads, lines = _journal(book, "OU690320-001")
    assert len(heads) == 1 and heads[0]["JNLTYP"] == "00" and heads[0]["DESCRP"].strip() == "เบิกใช้ในสำนักงาน"
    assert lines == [("0", ISSUE_ACC, Decimal("20.00"), "1"), ("1", INVENTORY_ACC, Decimal("20.00"), "1")]
    assert _rows(book, "ISRUN", PREFIX="OU")[0]["DOCNUM"].strip() == "0000002"


def test_an_increase_uses_the_given_cost_and_journals_inventory_against_the_offset_account(book):
    res = _write(book, _entry("JU", [_line("ICE01", 4, unit_cost="12")]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert line["POSOPR"] == "8" and line["TRNQTY"] == 4.0 and line["XTRNVAL"] == 48.0 and line["MREMBAL"] == 9.0
    assert line["ACCNUMCR"].strip() == OFFSET_ACC
    assert _rows(book, "STMAS", STKCOD="ICE01")[0]["MREMVAL"] == 98.0
    heads, lines = _journal(book, res.docnum)
    assert heads[0]["DESCRP"].strip() == f"ปรับปรุงสินค้า {res.docnum}"  # 没写备注：同样本的默认摘要
    assert lines == [("0", INVENTORY_ACC, Decimal("48.00"), "1"), ("1", OFFSET_ACC, Decimal("48.00"), "1")]


def test_a_count_with_both_signs_nets_the_journal_per_inventory_account(book):
    """盘点调整 TK 照 Express 原生（实录第 9 条 TK0000001）：POSOPR ':'、FLAG 5、MREMBAL / MREMVAL 写前结存、PHYBAL 写后这个仓的结存、
    没有对方科目与品名；单头货值 0、没有参考号与对方科目；凭证摘要「ตรวจนับสินค้า + 单号」。"""
    res = _write(book, _entry("TK", [_line("ICE01", -1), _line("ICE02", 2)], remark="นับสต็อก"))
    assert res.ok, res.error
    assert res.docnum.startswith("TK690320-")
    rows = {row["STKCOD"].strip(): row for row in _rows(book, "STCRD", DOCNUM=res.docnum)}
    ice01, ice02 = rows["ICE01"], rows["ICE02"]
    assert (ice01["POSOPR"], ice01["FLAG"].strip(), ice01["TRNQTY"], ice01["XTRNVAL"]) == (":", "5", -1.0, -10.0)
    assert (ice01["MREMBAL"], ice01["MREMVAL"], ice01["PHYBAL"]) == (5.0, 50.0, 4.0)  # 写前结存 5 / 50，这个仓写后 4
    assert (ice01["ACCNUMCR"].strip(), ice01["STKDES"].strip(), ice01["MLOTNUM"].strip()) == ("", "", "")
    assert ice02["TRNQTY"] == 2.0 and ice02["XTRNVAL"] == 0.0 and ice02["PHYBAL"] == 2.0  # 没货、没批次、没均价：数量照记、货值 0
    head = _rows(book, "STTRN", DOCNUM=res.docnum)[0]
    assert (head["POSOPR"], head["TRNVAL"], head["REFNUM"].strip(), head["ACCNUMCR"].strip()) == (":", 0.0, "", "")
    heads, lines = _journal(book, res.docnum)
    assert heads[0]["DESCRP"].strip() == f"ตรวจนับสินค้า {res.docnum}"  # 原生摘要，不用备注
    assert lines == [("0", OFFSET_ACC, Decimal("10.00"), "1"), ("1", INVENTORY_ACC, Decimal("10.00"), "1")]
    assert _stloc(book, "ICE02", "01")["LOCBAL"] == 2.0


def test_the_header_and_journal_line_numbers_are_right_aligned_like_native_express(book):
    """主会话 10-26：STTRN.NXTSEQ C(3)、GLJNLIT.SEQIT C(2) 照原生右对齐（追加后按字节写，dbf 库会去掉前导空格）。"""
    import dbf
    from companion import dbf_schema

    res = _write(book, _entry("JU", [_line("ICE01", 4, unit_cost="12")]))
    assert res.ok, res.error

    def raw(table, field, key):
        t = dbf.Table(str(book / f"{table}.DBF"), codepage=dbf_schema.CODEPAGE)
        t.open(dbf.READ_ONLY)
        try:
            return [str(getattr(rec, field)) for rec in t if str(getattr(rec, key)).strip() == res.docnum]
        finally:
            t.close()

    assert raw("STTRN", "NXTSEQ", "DOCNUM") == ["  1"] and raw("STCRD", "SEQNUM", "DOCNUM") == ["  1"]
    assert raw("GLJNLIT", "SEQIT", "VOUCHER") == [" 1", " 1"]


def _receipt(acct, docnum, day, qty, value, code="ICE01", loc="01", seq="  1"):
    """账里一行进货（STCRD POSOPR 0）：盘盈要挂的那一批。"""
    import dbf
    from companion import dbf_schema

    table = dbf.Table(str(acct / "STCRD.DBF"), codepage=dbf_schema.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:  # 原生进货行的行号右对齐（"  1"）；``seq`` 给左对齐的 "1  " 时照样按字节写，模拟存量 CC 单
        dbf_schema.append_stcrd(table, {"STKCOD": code, "LOCCOD": loc, "DOCNUM": docnum, "SEQNUM": "1", "DOCDAT": day,
                                        "POSOPR": "0", "TRNQTY": float(qty), "XTRNQTY": float(qty), "XTRNVAL": float(value)})
        if seq != "  1":
            from array import array
            from dbf.constants import LENGTH, START

            record = table[-1]
            fielddef = record._meta["SEQNUM"]
            record._data[fielddef[START]:fielddef[START] + fielddef[LENGTH]] = array("B", seq.encode("ascii"))
            record._update_disk()
    finally:
        table.close()


def test_a_count_gain_in_a_fifo_book_takes_the_cost_of_the_latest_receipt_and_links_that_lot(book):
    """实录第 9 条：先进先出账套的盘盈（载荷没带单价）取这个仓最近一次进货那一批的成本（S 盘 2,836 行），MLOTNUM / RDOCNUM
    指向那一行；别的仓的进货不算。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F")
    _receipt(book, "RR0000009", dt.date(2026, 3, 1), 10, 400)
    _receipt(book, "RR0000010", dt.date(2026, 3, 5), 10, 450)
    _receipt(book, "RR0000011", dt.date(2026, 3, 9), 10, 600, loc="02")
    res = _write(book, _entry("TK", [_line("ICE01", 15)]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["UNITPR"], line["XUNITPR"], line["XTRNVAL"], line["NETVAL"]) == (45.0, 45.0, 675.0, 675.0)
    assert (line["RDOCNUM"], line["MLOTNUM"].strip()) == ("RR0000010     1", "202603050RR0000010     1")
    assert (line["LOTBAL"], line["LOTVAL"], line["LUNITPR"], line["PHYBAL"]) == (15.0, 675.0, 45.0, 20.0)
    _heads, lines = _journal(book, res.docnum)
    assert lines == [("0", INVENTORY_ACC, Decimal("675.00"), "1"), ("1", OFFSET_ACC, Decimal("675.00"), "1")]


def test_a_count_gain_uses_the_price_a_person_edited(book):
    """主会话 11-10 定（引擎评估第 5 点）：物料调整单上人亲手填 / 改过的单价 ERP 才带进载荷（`stock_entry._net`），CC 写盘盈就用它，
    不再换成批成本；仍挂这个仓最近那一批。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F")
    _receipt(book, "RR0000010", dt.date(2026, 3, 5), 10, 450)
    res = _write(book, _entry("TK", [_line("ICE01", 15, unit_cost="99")]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["UNITPR"], line["XUNITPR"], line["XTRNVAL"], line["NETVAL"]) == (99.0, 99.0, 1485.0, 1485.0)
    assert (line["RDOCNUM"], line["LOTVAL"], line["LUNITPR"]) == ("RR0000010     1", 1485.0, 99.0)
    _heads, lines = _journal(book, res.docnum)
    assert lines == [("0", INVENTORY_ACC, Decimal("1485.00"), "1"), ("1", OFFSET_ACC, Decimal("1485.00"), "1")]


def test_a_count_gain_without_a_lot_uses_the_price_a_person_edited(book):
    """同上：挂不上批次的先进先出账套原来记 0；人改过单价时按人填的价记、照常过账。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F")
    res = _write(book, _entry("TK", [_line("ICE01", 3, unit_cost="12")]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["UNITPR"], line["XTRNVAL"], line["MLOTNUM"].strip()) == (12.0, 36.0, "")
    _heads, lines = _journal(book, res.docnum)
    assert lines == [("0", INVENTORY_ACC, Decimal("36.00"), "1"), ("1", OFFSET_ACC, Decimal("36.00"), "1")]


def test_a_count_gain_linked_to_an_old_cc_receipt_spells_its_line_number_as_stored(book):
    """验收 10-26：Express 拼 RDOCNUM 是「单号 C(12) + SEQNUM 原样」，存量 CC 单的行号是左对齐 "1  "，挂那一批时照存的原样拼。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F")
    _receipt(book, "RR690301-001", dt.date(2026, 3, 1), 10, 400, seq="1  ")
    res = _write(book, _entry("TK", [_line("ICE01", 1)]))
    assert res.ok, res.error
    assert _rows(book, "STCRD", DOCNUM=res.docnum)[0]["RDOCNUM"].rstrip() == "RR690301-0011"


def test_a_count_gain_ignores_receipts_after_the_count_day(book):
    """复核 10-26：任务积压后再写，盘盈只挂盘点那天以前（含当天）进的那一批，不挂之后才进的。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F")
    _receipt(book, "RR0000010", dt.date(2026, 3, 5), 10, 450)
    _receipt(book, "RR0000012", dt.date(2026, 3, 25), 10, 900)   # 盘点日 3 月 20 日之后
    res = _write(book, _entry("TK", [_line("ICE01", 1)]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["RDOCNUM"].strip(), line["XUNITPR"]) == ("RR0000010     1", 45.0)


def test_without_stloc_the_count_physical_balance_is_the_product_balance(book):
    """复核 10-26：账套没有 STLOC（不分仓）时，盘点明细的 PHYBAL 记商品主档写后的结存，不是只记变动量。"""
    for name in ("STLOC.DBF", "STLOC.CDX"):
        if (book / name).exists():
            (book / name).unlink()
    res = _write(book, _entry("TK", [_line("ICE01", 2)]))
    assert res.ok, res.error
    assert _rows(book, "STCRD", DOCNUM=res.docnum)[0]["PHYBAL"] == 7.0   # 主档 5 + 2
    assert not (book / "STLOC.DBF").exists()   # 照原生只动商品主档，不新建分仓表
    assert _rows(book, "STMAS", STKCOD="ICE01")[0]["MREMBAL"] == 7.0


def test_a_count_gain_without_a_lot_in_a_fifo_book_is_costed_at_zero(book):
    """实录第 9 条：挂不上批次的盘盈 Express 记 0 成本（6412NLT TK2000001），以后靠「计算成本」重排；零金额不过账。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", COSMTD="F")
    res = _write(book, _entry("TK", [_line("ICE01", 3)]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["UNITPR"], line["XTRNVAL"], line["MLOTNUM"].strip()) == (0.0, 0.0, "")
    assert _journal(book, res.docnum) == ([], [])


def test_a_count_gain_in_an_average_cost_book_takes_the_lot_cost_or_the_product_average(book):
    """平均成本账套：挂得上批次也取那一批的成本（实录 TK0000001 是平均成本账套，单价 45 = 批成本，不是 02 仓均价 50；S 盘 96 行对 9 行）；
    挂不上按商品均价（待核）。"""
    _receipt(book, "RR0000010", dt.date(2026, 3, 5), 10, 450)
    res = _write(book, _entry("TK", [_line("ICE01", 2)]))
    assert res.ok, res.error
    line = _rows(book, "STCRD", DOCNUM=res.docnum)[0]
    assert (line["UNITPR"], line["XTRNVAL"], line["RDOCNUM"]) == (45.0, 90.0, "RR0000010     1")
    other = _write(book, _entry("TK", [_line("ICE01", 1)], loccod="02"))
    assert other.ok, other.error
    line = _rows(book, "STCRD", DOCNUM=other.docnum)[0]
    assert (line["UNITPR"], line["RDOCNUM"].strip()) == (20.0, "")  # 02 仓没有进货：商品均价（5 个 50 + 盘盈 2 个 90）/ 7 = 20
    assert _stloc(book, "ICE01", "02")["TAKDAT"] == dt.date(2026, 3, 20)  # 盘点日记在 STLOC.TAKDAT


def test_a_periodic_account_set_or_a_zero_value_entry_is_not_journalled(book):
    res = _write(book, _entry("JU", [_line("ICE02", 3)]))  # 零金额
    assert res.ok, res.error
    assert _journal(book, res.docnum) == ([], [])
    isinfo(book, MAINLOC="01", ISPERPETUA="N")
    res = _write(book, _entry("OU", [_line("ICE01", 1)]))
    assert res.ok, res.error
    assert _journal(book, res.docnum) == ([], [])
    assert _rows(book, "STCRD", DOCNUM=res.docnum)[0]["XTRNVAL"] == 10.0


def test_the_same_day_gets_the_next_number_and_the_given_account_wins(book):
    glacc(book, ("53-09-00-00", "ค่าเสียหาย"))
    assert _write(book, _entry("OU", [_line("ICE01", 1)])).docnum == "OU690320-001"
    res = _write(book, _entry("OU", [_line("ICE01", 1)], account="53-09-00-00"))
    assert res.docnum == "OU690320-002"
    assert _rows(book, "STTRN", DOCNUM=res.docnum)[0]["ACCNUMDR"].strip() == "53-09-00-00"


@pytest.mark.parametrize("change, payload, code", [
    (lambda acct: None, _entry("XX", [_line("ICE01", 1)]), "PAYLOAD_INVALID"),
    (lambda acct: None, _entry("OU", [_line("ICE01", -1)]), "PAYLOAD_INVALID"),
    (lambda acct: None, _entry("OU", [_line("NOPE", 1)]), "STOCK_ITEM_NOT_FOUND"),
    (lambda acct: None, _entry("OU", [_line("ICE01", 1)], account="99-99"), "ACCOUNT_NOT_POSTABLE"),
    (lambda acct: seed(acct, "ISRUN", [{"DOCTYP": "OU", "PREFIX": "OU", "DOCNUM": "0000009", "JNLTYP": "00",
                                        "ACCNUM01": "52-08-00-00"}]),
     _entry("OU", [_line("ICE01", 1)]), "JOURNAL_BOOK_NOT_FOUND"),  # 两行同单别、科目不同：不猜
    (lambda acct: isinfo(acct, MAINLOC="01", ISPERPETUA="Y", NEGALLOW="N"), _entry("OU", [_line("ICE01", 6)]),
     stock_guard.ERR_STOCK_INSUFFICIENT),
])
def test_what_cannot_be_written_writes_nothing(book, change, payload, code):
    change(book)
    before = _state(book)
    res = _write(book, payload)
    assert not res.ok and res.error_code == code, (res.error_code, res.error)
    assert _state(book) == before


def test_a_failed_readback_rolls_everything_back(book):
    before = _state(book)
    with mock.patch.object(dbf_stock, "_verify", side_effect=dbf_stock._WriteError("boom", "READBACK_VERIFY_FAILED")):
        res = _write(book, _entry("OU", [_line("ICE01", 2)]), make_backup=True)
    assert not res.ok and res.error_code == "READBACK_VERIFY_FAILED"
    assert _state(book) == before


def _new(name, qty, **extra):
    return {"code": "", "name": name, "qty": str(qty), "unit": "ถัง", "new_ok": True, **extra}


def test_a_line_marked_new_creates_the_item_once_and_writes_it(book):
    """多仓闭环 P3x：Express 里没有、人在 ERP 里选了新建的行（``new_ok``，载荷 15）：写前备份里建 0 余额库存品（同采购 / 销售的建档），
    再照这个码写；同一张单同名两行只建一个；逐行回报建出的码（ERP 据此回填商品）。没标新建的照旧整单停。"""
    before = len(_rows(book, "STMAS"))
    res = _write(book, _entry("TK", [_new("ถ่านอัดแท่ง", 3), _new("ถ่านอัดแท่ง", 2), _line("ICE01", 1)],
                              payload_version=15))
    assert res.ok, res.error
    made = [row for row in _rows(book, "STMAS") if row["STKDES"].strip() == "ถ่านอัดแท่ง"]
    assert len(made) == 1 and len(_rows(book, "STMAS")) == before + 1
    code = made[0]["STKCOD"].strip()
    assert [(row["seq"], row["stkcod"]) for row in res.line_modes] == [(1, code), (2, code), (3, "ICE01")]
    assert made[0]["MREMBAL"] == 5.0  # 盘盈两行合计
    assert {row["STKCOD"].strip() for row in _rows(book, "STCRD", DOCNUM=res.docnum)} == {code, "ICE01"}
    res = _write(book, _entry("JU", [{**_new("ใหม่", 1), "new_ok": "yes"}]))
    assert not res.ok and res.error_code == "STOCK_ITEM_NOT_FOUND"  # 只认 true，旧值 / 乱写不建


def test_a_new_item_that_would_go_negative_stops_when_the_book_forbids_negative_stock(book):
    """主会话 11-01：账套不许负库存时，要新建的存货在减少（领用、盘亏）就建档前停下（结存 0，原因同结存核对）；增加的照建；
    允许负库存的账套减少的也照建照写。"""
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", NEGALLOW="N")
    before = _state(book)
    for payload in (_entry("OU", [_new("ถ่านอัดแท่ง", 2)], payload_version=15),
                    _entry("TK", [_line("ICE01", 1), _new("ถ่านอัดแท่ง", -1)], payload_version=15)):
        res = _write(book, payload)
        assert not res.ok and res.error_code == stock_guard.ERR_STOCK_INSUFFICIENT, (res.error_code, res.error)
        assert "ถ่านอัดแท่ง" in res.error and "have 0" in res.error
        assert _state(book) == before
    assert _write(book, _entry("JU", [_new("ถ่านอัดแท่ง", 2, unit_cost="5")], payload_version=15)).ok
    isinfo(book, MAINLOC="01", ISPERPETUA="Y", NEGALLOW="Y")
    res = _write(book, _entry("OU", [_new("ถ่านอีกชนิด", 1)], payload_version=15))
    assert res.ok, res.error


def test_the_stock_group_for_a_new_item_follows_the_payload_and_an_unfit_group_asks_for_one(book):
    """新建存货的存货科目组同采购：载荷带了 ``stock_acccod`` 就照它建；判不出是存货科目（含载荷给的不合格）报 STOCK_ACC_GROUP_UNFIT
    （ERP 据此让人选一次存货科目组），整单不写。"""
    before = _state(book)
    res = _write(book, _entry("JU", [_new("ถ่านอัดแท่ง", 1, unit_cost="5")], payload_version=15, stock_acccod="NOPE"))
    assert not res.ok and res.error_code == "STOCK_ACC_GROUP_UNFIT", (res.error_code, res.error)
    assert _state(book) == before
    res = _write(book, _entry("JU", [_new("ถ่านอัดแท่ง", 1, unit_cost="5")], payload_version=15, stock_acccod="ST01"))
    assert res.ok, res.error
    assert [row["ACCCOD"].strip() for row in _rows(book, "STMAS") if row["STKDES"].strip() == "ถ่านอัดแท่ง"] == ["ST01"]


def test_a_failed_write_also_rolls_back_the_new_item(book):
    before = _state(book)
    with mock.patch.object(dbf_stock, "_verify", side_effect=dbf_stock._WriteError("boom", "READBACK_VERIFY_FAILED")):
        res = _write(book, _entry("JU", [_new("ถ่านอัดแท่ง", 2, unit_cost="5")], payload_version=15), make_backup=True)
    assert not res.ok and res.error_code == "READBACK_VERIFY_FAILED"
    assert _state(book) == before


def test_payload_six_is_supported_and_older_cc_desktops_refuse_it():
    assert payload_contract.check_payload_version({"payload_version": 6}) is None
    assert payload_contract.check_payload_version({"payload_version": 20}) == payload_contract.ERR_PAYLOAD_VERSION


def test_the_worker_hands_a_stock_entry_to_the_stock_writer(monkeypatch, tmp_path):
    """载荷 6 的 ``direction = stock`` 交给 dbf_stock，不走进项 / 销项。"""
    from types import SimpleNamespace

    from companion import dbf_sales, dbf_writer, device_facts, task_journal, worker
    from companion.write_result import DbfWriteResult

    seen = []
    monkeypatch.setattr(dbf_stock, "write_stock_entry",
                        lambda payload, account_dir, **kw: seen.append((payload["doctype"], account_dir))
                        or DbfWriteResult(True, docnum="OU690320-001"))
    monkeypatch.setattr(dbf_writer, "write_purchase", lambda *a, **kw: pytest.fail("not a purchase"))
    monkeypatch.setattr(dbf_sales, "write_sale", lambda *a, **kw: pytest.fail("not a sale"))
    found = SimpleNamespace(account_dir=str(tmp_path), account_set=str(tmp_path), account_company="",
                            has_selection=lambda: True)
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, p: found)
    monkeypatch.setattr(device_facts, "file_uid", lambda path, api=None: "")
    monkeypatch.setattr(task_journal, "written", lambda task_id: None)
    monkeypatch.setattr(task_journal, "remember", lambda task_id, value: None)
    acks = []
    queue = SimpleNamespace(ack_success=lambda task_id, **kw: acks.append(("ok", kw.get("extracted_invoice_no"))),
                            ack_failure=lambda task_id, error, **kw: acks.append(("failed", kw.get("error_code"))))
    worker._handle_dbf_task(SimpleNamespace(default_depcod="", ui_lang="en"), queue,
                            SimpleNamespace(id="T-9", payload=_entry("OU", [_line("ICE01", 1)]), invoice_no="X"))
    assert seen == [("OU", str(tmp_path))] and acks == [("ok", "OU690320-001")]


def test_the_master_read_reports_the_default_account_of_each_stock_book(book):
    """读主档时一并报 OU / JU / TK 在 ISRUN 的默认科目（ERP 据此判要不要先让人选一次科目）。"""
    from companion import express_masters

    facts = express_masters._facts(book)
    assert facts["stock_books"] == {"OU": ISSUE_ACC, "JU": OFFSET_ACC, "TK": OFFSET_ACC}
    assert "ISRUN" in express_masters.TABLES
