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
    res = _write(book, _entry("TK", [_line("ICE01", -1), _line("ICE02", 2)]))
    assert res.ok, res.error
    assert res.docnum.startswith("TK690320-")
    rows = {row["STKCOD"].strip(): row for row in _rows(book, "STCRD", DOCNUM=res.docnum)}
    assert rows["ICE01"]["TRNQTY"] == -1.0 and rows["ICE01"]["XTRNVAL"] == -10.0 and rows["ICE01"]["MREMBAL"] == 4.0
    assert rows["ICE02"]["TRNQTY"] == 2.0 and rows["ICE02"]["XTRNVAL"] == 0.0  # 没货、没标准成本：数量照记、货值 0
    assert _rows(book, "STTRN", DOCNUM=res.docnum)[0]["TRNVAL"] == -10.0
    _heads, lines = _journal(book, res.docnum)
    assert lines == [("0", OFFSET_ACC, Decimal("10.00"), "1"), ("1", INVENTORY_ACC, Decimal("10.00"), "1")]
    assert _stloc(book, "ICE02", "01")["LOCBAL"] == 2.0


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


def test_payload_six_is_supported_and_older_cc_desktops_refuse_it():
    assert payload_contract.check_payload_version({"payload_version": 6}) is None
    assert payload_contract.check_payload_version({"payload_version": 9}) == payload_contract.ERR_PAYLOAD_VERSION


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
