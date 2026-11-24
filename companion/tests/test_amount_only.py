# -*- coding: utf-8 -*-
"""没有明细行的买卖单补一行「编码 = 科目码」的服务品（``companion.amount_only``，11-24）。

起因：直写的「只有单头」单在 Express 里「แก้ไข → 保存」会被清零（测试机2 69EXP\\TEST 实测 RR691124-001、IV0000034）；带一行明细的
单修改再存不变。现在直写进货、直写销货、代录三处同一个口径补一行。钉住：

1. 新旧写法比：单头、凭证、分录、税记录、应付 / 应收逐字段不变，只多一行明细（编码 = 科目码、数量 1、净额 = 税前）和可能新建的服务品；
2. 同码已是库存品：整单交人（``ACCOUNT_CODE_IS_STOCK_ITEM``），账套不变；
3. 已有同码品沿用、不改；新建时科目组 / 单位优先照抄这本账套原生的科目码品（4 / 5 型）；
4. 不补的情形（有明细、ok、没有科目、金额 0、POS 日汇总、别的单种）；
5. 代录与直写同一个函数（``masters.amount_only_line``）。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_purchase_no_invoice_number import *  # noqa: F401,F403 —— 冻结时钟 frozen_clock
from test_stock_location import (AR_ACC, PURCHASE_ACC, REVENUE_ACC, _append, _buy, _purchase, _rows, _sale, _sell, _state,
                                 acct)  # noqa: F401

from companion import amount_only
from companion import product_master as P
from companion.express_operator import masters

SAME = (("APTRN", "DOCNUM"), ("ARTRN", "DOCNUM"), ("GLJNL", "VOUCHER"), ("GLJNLIT", "VOUCHER"), ("ISVAT", "DOCNUM"))


def _header_only_purchase():
    p = _purchase([], items_status="empty", base_amount="500.00", vat_amount="35.00", total_amount="535.00")
    for line, amount in zip(p["lines"], ("500.00", "35.00", "535.00")):
        line["amount"] = amount
    return p


def _header_only_sale():
    p = _sale([], items_status="empty", base_amount="500.00", vat_amount="35.00", total_amount="535.00")
    for line, amount in zip(p["lines"], ("535.00", "500.00", "35.00")):
        line["amount"] = amount
    return p


def _old(monkeypatch, write, folder, payload):
    with monkeypatch.context() as m:
        m.setattr(amount_only, "line", lambda *_a, **_k: None)  # 旧写法：只有单头
        return write(folder, payload)


def _no_chg(rows):
    return [{k: v for k, v in r.items() if k != "CHGTIM"} for r in rows]


@pytest.mark.parametrize("kind", ["purchase", "sale"])
def test_a_ticket_without_lines_gets_one_account_line_and_nothing_else_changes(acct, tmp_path, frozen_clock, monkeypatch, kind):
    twin = tmp_path / "twin"
    shutil.copytree(acct, twin)
    write, payload, account = ((_buy, _header_only_purchase(), PURCHASE_ACC) if kind == "purchase"
                               else (_sell, _header_only_sale(), REVENUE_ACC))
    old = _old(monkeypatch, write, twin, payload)
    new = write(acct, payload)
    assert old.ok and new.ok and old.docnum == new.docnum, (old.error, new.error)
    for table, key in SAME:
        assert _no_chg(_rows(twin, table, **{key: old.docnum})) == _no_chg(_rows(acct, table, **{key: new.docnum})), table
    for table in ("APBAL", "ARBAL", "APMAS", "ARMAS"):
        if (acct / f"{table}.DBF").exists():
            assert _rows(twin, table) == _rows(acct, table), table
    assert _rows(twin, "STCRD", DOCNUM=old.docnum) == []
    [line] = _rows(acct, "STCRD", DOCNUM=new.docnum)
    assert (str(line["STKCOD"]).strip(), float(line["TRNQTY"])) == (account, 1.0)
    [item] = [r for r in _rows(acct, "STMAS") if str(r["STKCOD"]).strip() == account]
    assert str(item["STKTYP"]).strip() in P._NONSTOCK_TYPES
    changed = sorted(n for n in _state(acct) if _state(acct)[n] != _state(twin).get(n))
    assert set(changed) <= {"STCRD.DBF", "STMAS.DBF", "ISACC.DBF", "APTRN.DBF", "ARTRN.DBF"}, changed


def test_an_account_code_that_is_a_stock_item_hands_the_ticket_to_a_person_and_leaves_the_book(acct):
    _append(acct / "STMAS.DBF", [{"STKCOD": PURCHASE_ACC, "STKDES": "สินค้า", "STKTYP": "0", "QUCOD": "ถง"}])
    before = _state(acct)
    result = _buy(acct, _header_only_purchase(), make_backup=True)
    assert not result.ok and result.error_code == P.ERR_ACCOUNT_ITEM_STOCK
    assert _state(acct) == before


def test_an_existing_account_item_is_used_as_it_is(acct):
    _append(acct / "STMAS.DBF", [{"STKCOD": PURCHASE_ACC, "STKDES": "ค่าบริการของบัญชี", "STKTYP": "5", "STKGRP": "คช",
                                  "QUCOD": "คง"}])
    before = [r for r in _rows(acct, "STMAS") if str(r["STKCOD"]).strip() == PURCHASE_ACC]
    assert _buy(acct, _header_only_purchase()).ok
    after = [r for r in _rows(acct, "STMAS") if str(r["STKCOD"]).strip() == PURCHASE_ACC]
    assert after == before


def test_a_new_account_item_takes_its_group_and_unit_from_the_books_own_account_items(acct):
    _append(acct / "STMAS.DBF", [{"STKCOD": AR_ACC, "STKDES": "ของบัญชี", "STKTYP": "5", "STKGRP": "คช", "QUCOD": "คง",
                                  "PQUCOD": "คง", "SQUCOD": "คง"}])
    assert _buy(acct, _header_only_purchase()).ok
    [item] = [r for r in _rows(acct, "STMAS") if str(r["STKCOD"]).strip() == PURCHASE_ACC]
    assert (str(item["STKGRP"]).strip(), str(item["QUCOD"]).strip(), str(item["STKTYP"]).strip()) == ("คช", "คง", "3")


@pytest.mark.parametrize("change", [
    {"items_status": "ok"}, {"items_account": ""}, {"base_amount": "0"},
    {"doctype": "JU"}, {"pos": {"transfers": []}},
])
def test_nothing_is_added_when_the_ticket_does_not_need_it(change):
    assert amount_only.line({**_header_only_purchase(), **change}) is None


def test_untrusted_lines_of_a_ticket_that_is_not_ok_are_replaced_by_the_account_line(acct):
    """OCR 只读到一部分 / 对不上（incomplete / mismatch）：带来的行不可信、以前直写只写单头；现在换成补的这一行。"""
    payload = dict(_header_only_purchase(), items_status="incomplete",
                   items=[{"name": "บางส่วน", "qty": "1", "unit_price": "100", "amount": "100.00", "item_mode": "non_stock_item"}])
    result = _buy(acct, payload)
    assert result.ok, result.error
    [line] = _rows(acct, "STCRD", DOCNUM=result.docnum)
    assert (str(line["STKCOD"]).strip(), float(line["NETVAL"])) == (PURCHASE_ACC, 500.0)
    assert masters.amount_only_line(payload)["account"] == PURCHASE_ACC


def test_a_new_account_item_prefers_a_native_one_of_the_same_account_class(acct):
    _append(acct / "STMAS.DBF", [{"STKCOD": AR_ACC, "STKDES": "ทรัพย์สิน", "STKTYP": "5", "STKGRP": "สท", "QUCOD": "ชน"},
                                 {"STKCOD": "51-01-00-00", "STKDES": "ค่าใช้จ่าย", "STKTYP": "5", "STKGRP": "คช", "QUCOD": "คง"}])
    assert _buy(acct, _header_only_purchase()).ok
    [item] = [r for r in _rows(acct, "STMAS") if str(r["STKCOD"]).strip() == PURCHASE_ACC]
    assert str(item["STKGRP"]).strip() == "คช"  # 51-03-00-00 是费用类：照抄 51-01-00-00 的组，不照抄 11-02-01-00（资产）的


def test_the_operator_and_direct_write_use_the_same_line():
    payload = _header_only_purchase()
    assert masters.amount_only_line(payload) == amount_only.line(payload)
    assert masters.amount_only_line({**payload, "operator": {"vat_included": True}})["unit_price"] == "535.00"
    assert masters.AMOUNT_ONLY_LINE == amount_only.MARK


# ── 代录：建主档时补的那一行（express_operator.masters，与直写同一个函数）──────────────────────────────────────────
import tempfile  # noqa: E402
import unittest  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

import _synth_account  # noqa: E402

from companion import cdx_reindex  # noqa: E402
from companion import product_master  # noqa: E402
from companion.express_operator.draft import from_payload  # noqa: E402

EXPENSE_ACC = "52-08-00-00"


def _amount_only(**over):
    payload = {
        "doctype": "RR", "account_set": "DATAT", "docdate_be": "690115", "vat_period_be": "690101", "ref_no": "INV-9",
        "supplier": {"code": "", "name": "บริษัท ทดสอบ จำกัด", "tax_id": "0105555000011", "prename": "บริษัท",
                     "supplier_new": True},
        "vat_rate": 7.0, "base_amount": "500.00", "vat_amount": "35.00", "total_amount": "535.00",
        "lines": [{"acc": EXPENSE_ACC, "side": "D", "amount": "500.00", "desc": "ซื้อ"},
                  {"acc": "11-05-04-01", "side": "D", "amount": "35.00", "desc": "ภาษีซื้อ"},
                  {"acc": "21-02-01-00", "side": "C", "amount": "535.00", "desc": "เจ้าหนี้"}],
        "items": [], "items_status": "empty", "items_account": EXPENSE_ACC,
        "entry_mode": "operator", "operator": {"batch": {"id": "b1", "index": 1, "total": 1}},
    }
    payload.update(over)
    return payload


class AmountOnlyTicketTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cdx_reindex, "runner_available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.acct = _synth_account.build(self.tmp / "69EXP" / "test")
        self.settings = SimpleNamespace(account_dir=str(self.acct), account_set="DATAT", account_company="")

    def _prepare(self, payload):
        return masters.prepare(payload, self.settings, before_create=lambda: None)

    def test_a_ticket_without_lines_is_entered_as_one_service_line_coded_with_its_account(self):
        for status in ("empty", "incomplete", "mismatch"):
            ready = self._prepare(_amount_only(items_status=status, ref_no=f"INV-{status}"))
            self.assertTrue(ready.ok, ready.error)
            [line] = ready.payload["items"]
            self.assertEqual((line["code"], line["qty"], line["unit_price"]), (EXPENSE_ACC, "1", "500.00"))
            draft = from_payload("d1", ready.payload)
            self.assertEqual([(ln.item_code, ln.qty, ln.unit_price) for ln in draft.lines], [(EXPENSE_ACC, "1", "500")])
            self.assertEqual(draft.totals(), {"net": 500, "vat": 35, "total": 535})
        self.assertEqual(product_master.account_item_type(self.acct, EXPENSE_ACC) in product_master._NONSTOCK_TYPES, True)
        self.assertNotIn("line_codes", masters.written_codes(ready.payload))  # 补的那行不当成票上的明细回报

    def test_sales_and_price_including_vat_follow_the_same_rule(self):
        line = masters.amount_only_line(_amount_only(doctype="IV", items_account="41-01-00-00"))
        self.assertEqual((line["account"], line["unit_price"]), ("41-01-00-00", "500.00"))
        incl = masters.amount_only_line(_amount_only(operator={"vat_included": True}))
        self.assertEqual(incl["unit_price"], "535.00")

    def test_nothing_is_added_when_the_lines_are_trusted_or_there_is_no_account_or_amount(self):
        self.assertIsNone(masters.amount_only_line(_amount_only(items=[{"name": "x", "amount": "1"}], items_status="ok")))
        self.assertIsNone(masters.amount_only_line(_amount_only(items_status="ok")))
        self.assertIsNone(masters.amount_only_line(_amount_only(items_account="")))
        self.assertIsNone(masters.amount_only_line(_amount_only(base_amount="0")))
        self.assertIsNone(masters.amount_only_line(_amount_only(doctype="JU")))


    def test_an_account_that_is_also_a_stock_item_code_is_handed_to_a_person_before_express_opens(self):
        with mock.patch.object(product_master, "account_item_type", lambda _d, _a: "0"):
            ready = self._prepare(_amount_only())
        self.assertFalse(ready.ok)
        self.assertEqual(ready.error_code, product_master.ERR_ACCOUNT_ITEM_STOCK)
