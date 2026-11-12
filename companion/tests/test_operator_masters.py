# -*- coding: utf-8 -*-
"""代录开工前建主档（``express_operator.masters``，主会话 10-27 ③）：直写建档原样复用，只多这一个调用方。

钉住：要建才建、建好的码写回载荷；已有的不碰账套也不退 Express；疑似重复不建、交人；任一步失败主档表整笔还原；
账套被占用按「等账套」；账套名 / 公司闸同直写；不碰交易表与 ISRUN。"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import dbf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import cdx_reindex  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as W  # noqa: E402
from companion import product_master  # noqa: E402
from companion.express_operator import masters  # noqa: E402

INVENTORY_ACC = "11-04-02-00"
COGS_ACC = "51-01-00-00"
EXPENSE_ACC = "52-08-00-00"
TRANSACTION_TABLES = ("ISRUN", "APTRN", "ARTRN", "ISVAT", "GLJNL", "GLJNLIT", "APBAL", "ARBAL", "STCRD")


def _purchase(**over):
    payload = {
        "doctype": "RR", "account_set": "DATAT", "docdate_be": "690115", "vat_period_be": "690101",
        "ref_no": "INV-1", "supplier": {"code": "", "name": "บริษัท ทดสอบ จำกัด", "tax_id": "0105555000011",
                                        "prename": "บริษัท", "supplier_new": True},
        "vat_rate": 7.0, "base_amount": "100.00", "vat_amount": "7.00", "total_amount": "107.00",
        "lines": [{"acc": EXPENSE_ACC, "side": "D", "amount": "100.00", "desc": "ซื้อ"},
                  {"acc": "11-05-04-01", "side": "D", "amount": "7.00", "desc": "ภาษีซื้อ"},
                  {"acc": "21-02-01-00", "side": "C", "amount": "107.00", "desc": "เจ้าหนี้"}],
        "items": [{"name": "ค่าบริการ", "qty": "1", "unit_price": "100", "amount": "100.00", "unit": "ครั้ง",
                   "item_mode": "non_stock_item"}],
        "items_status": "ok", "items_account": EXPENSE_ACC,
        "entry_mode": "operator", "operator": {"batch": {"id": "b1", "index": 1, "total": 1}},
    }
    payload.update(over)
    return payload


def _rows(acct, name):
    t = dbf.Table(str(acct / f"{name}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [dict(zip(t.field_names, r)) for r in t if not dbf.is_deleted(r)]
    finally:
        t.close()


def _digest(acct, names):
    return {n: hashlib.sha256((acct / f"{n}.DBF").read_bytes()).hexdigest() for n in names if (acct / f"{n}.DBF").exists()}


class OperatorMastersTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cdx_reindex, "runner_available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.acct = _synth_account.build(self.tmp / "69EXP" / "test")
        self.settings = SimpleNamespace(account_dir=str(self.acct), account_set="DATAT", account_company="")
        self.closed = []

    def _prepare(self, payload, settings=None):
        return masters.prepare(payload, settings or self.settings, before_create=lambda: self.closed.append(1))

    def _per_item_vat(self):
        """开「按商品记增值税」（ISINFO.VAT_INITEM = Y）：夹具没有 ISINFO，只建这一栏。"""
        t = dbf.Table(str(self.acct / "ISINFO.DBF"), "VAT_INITEM C(1)", codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        try:
            t.append({"VAT_INITEM": "Y"})
        finally:
            t.close()

    def _seed_stock(self):
        t = dbf.Table(str(self.acct / "STMAS.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        t.append({"STKCOD": "OIL01", "STKDES": "ดีเซล", "STKTYP": "0", "ACCCOD": "ST01", "QUCOD": "ลต"})
        t.close()
        t = dbf.Table(str(self.acct / "ISACC.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        t.append({"ACCTYP": "S", "ACCCOD": "ST01", "METHOD": "A", "ACCNUM01": INVENTORY_ACC, "ACCNUM02": COGS_ACC})
        t.close()

    def test_creates_party_and_items_and_hands_codes_to_the_operator(self):
        before = _digest(self.acct, TRANSACTION_TABLES)
        ready = self._prepare(_purchase())
        self.assertTrue(ready.ok, ready.error)
        supplier = ready.payload["supplier"]["code"]
        item = ready.payload["items"][0]["code"]
        self.assertTrue(supplier and item)
        self.assertEqual([r["SUPCOD"].strip() for r in _rows(self.acct, "APMAS")], [supplier])
        self.assertIn(item, [r["STKCOD"].strip() for r in _rows(self.acct, "STMAS")])
        self.assertEqual([c["kind"] for c in ready.created], ["party", "item"])
        self.assertEqual(self.closed, [1])  # 真要建才让宿主退 Express
        self.assertEqual(_digest(self.acct, TRANSACTION_TABLES), before)  # 交易表与 ISRUN 一字节不动

    def test_stock_line_gets_a_stock_item(self):
        self._seed_stock()
        payload = _purchase(items=[{"name": "ผงชูรส", "qty": "2", "unit_price": "50", "amount": "100.00",
                                    "unit": "ถัง", "item_mode": "stock_item"}])
        ready = self._prepare(payload)
        self.assertTrue(ready.ok, ready.error)
        code = ready.payload["items"][0]["code"]
        self.assertEqual(product_master.find_stock_item_code(self.acct, "ผงชูรส"), code)

    def _guided_tables(self):
        """单位表（ISTAB 20）里有「ขด」、分类表（ISTAB 22）里有 FG / RAW。"""
        t = dbf.Table(str(self.acct / "ISTAB.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        try:
            for tabtyp, code in (("20", "ขด"), ("20", "ลต"), ("22", "FG"), ("22", "RAW")):
                t.append({"TABTYP": tabtyp, "TYPCOD": code, "TYPDES": code})
        finally:
            t.close()

    def test_a_new_stock_item_takes_the_unit_group_category_and_negative_option_of_its_line(self):
        """载荷 21 的四个行键（11-12 晚，引导式第 1 期）：代录开工前建新物料照这一行给的单位码、科目组、分类、允许负库存建，
        不再静默取众数；同直写 ``stock_purchase`` 的建法。"""
        self._seed_stock()
        self._guided_tables()
        payload = _purchase(payload_version=21, items=[
            {"name": "น้ำมันเครื่อง", "qty": "2", "unit_price": "50", "amount": "100.00", "unit": "ถัง", "item_mode": "stock_item",
             "unit_code": "ขด", "acccod": "ST01", "stkgrp": "RAW", "negallow": "N"}])
        ready = self._prepare(payload)
        self.assertTrue(ready.ok, ready.error)
        code = ready.payload["items"][0]["code"]
        row = next(r for r in _rows(self.acct, "STMAS") if r["STKCOD"].strip() == code)
        self.assertEqual((row["QUCOD"].strip(), row["ACCCOD"].strip(), row["STKGRP"].strip(), row["NEGALLOW"].strip()),
                         ("ขด", "ST01", "RAW", "N"))
        self.assertEqual(ready.payload["items"][0]["unit"], "ขด")  # 录单也按这个单位码

    def test_a_line_key_naming_something_the_book_does_not_have_is_handed_to_a_person_untouched(self):
        self._seed_stock()
        self._guided_tables()
        before = _digest(self.acct, ("STMAS", "APMAS", "ISACC"))
        for key, value, code in (("stkgrp", "NOPE", "ITEM_GROUP_NOT_FOUND"), ("unit_code", "ZZ", "UNIT_CODE_NOT_FOUND")):
            payload = _purchase(payload_version=21, items=[
                {"name": "น้ำมันเครื่อง", "qty": "2", "unit_price": "50", "amount": "100.00", "item_mode": "stock_item", key: value}])
            ready = self._prepare(payload)
            self.assertFalse(ready.ok)
            self.assertEqual((ready.error_code, ready.outcome), (code, "needs_review"))
        self.assertEqual(_digest(self.acct, ("STMAS", "APMAS", "ISACC")), before)
        self.assertEqual(self.closed, [])  # 没去关 Express

    def test_an_expense_line_booked_to_an_account_gets_a_service_item_on_that_account(self):
        # 费用单的明细（直接记科目）：Express 界面录明细要商品码，照直写建非库存品的函数按这一行的科目建服务品
        payload = _purchase(items=[{"name": "ค่าโฆษณาออนไลน์", "qty": "1", "unit_price": "3000", "amount": "3000.00",
                                    "item_mode": "direct_account", "account": EXPENSE_ACC}])
        ready = self._prepare(payload)
        self.assertTrue(ready.ok, ready.error)
        code = ready.payload["items"][0]["code"]
        self.assertEqual(product_master.find_item_code(self.acct, "ค่าโฆษณาออนไลน์"), code)
        no_account = _purchase(items=[{"name": "อื่นๆ", "qty": "1", "amount": "1.00", "item_mode": "direct_account"}],
                               items_account="")
        self.assertNotIn("code", self._prepare(no_account).payload["items"][0])  # 没有科目可挂：不建

    def test_an_exempt_line_is_entered_with_an_exempt_service_item(self):
        # 10-29 真实语料（部分免税的进货单）：开了「按商品记增值税」的账套，免税行落到 VATCOD '0' 的服务品，Express 照商品税码
        # 记免税；应税行照旧
        self._per_item_vat()
        payload = _purchase(items=[
            {"name": "ค่าส่ง", "qty": "1", "unit_price": "100", "amount": "100.00", "item_mode": "direct_account",
             "account": EXPENSE_ACC},
            {"name": "ค่าส่ง", "qty": "1", "unit_price": "50", "amount": "50.00", "item_mode": "direct_account",
             "account": EXPENSE_ACC, "vat_exempt": True}])
        ready = self._prepare(payload)
        self.assertTrue(ready.ok, ready.error)
        taxable, exempt = (line["code"] for line in ready.payload["items"])
        self.assertNotEqual(taxable, exempt)
        codes = {r["STKCOD"].strip(): r["VATCOD"].strip() for r in _rows(self.acct, "STMAS")}
        self.assertEqual(codes[exempt], "0")
        self.assertNotEqual(codes[taxable], "0")

    def test_a_taxable_line_never_lands_on_an_exempt_item_when_the_book_takes_vat_from_items(self):
        # 10-29 TEST2 实测（开了「按商品记增值税」）：同名两行先免税后应税，应税行落到了刚建的免税品上，Express 照商品税码把它也记成免税
        # （RR0000088）。开了的账套应税行另建 / 另找不是免税的商品；没开的账套商品税码不起作用，照旧共用一份
        payload = lambda: _purchase(items=[  # noqa: E731
            {"name": "ค่าส่ง", "qty": "1", "unit_price": "50", "amount": "50.00", "item_mode": "direct_account",
             "account": EXPENSE_ACC, "vat_exempt": True},
            {"name": "ค่าส่ง", "qty": "1", "unit_price": "100", "amount": "100.00", "item_mode": "direct_account",
             "account": EXPENSE_ACC}])
        ready = self._prepare(payload())
        # 没开的账套：明细免税码录不进去，免税、应税混着的交人（10-30 实机，主会话定）
        self.assertEqual((ready.ok, ready.error_code, ready.outcome),
                         (False, masters.ERR_EXEMPT_MIXED_CLOSED, "needs_review"))
        self._per_item_vat()
        ready = self._prepare(payload())
        self.assertTrue(ready.ok, ready.error)
        exempt, taxable = (line["code"] for line in ready.payload["items"])
        codes = {r["STKCOD"].strip(): r["VATCOD"].strip() for r in _rows(self.acct, "STMAS")}
        self.assertNotEqual(exempt, taxable)
        self.assertEqual(codes[exempt], "0")
        self.assertNotEqual(codes[taxable], "0")

    def test_a_whole_exempt_purchase_in_a_book_without_per_item_vat_is_entered_without_vat(self):
        # 没开「按商品记增值税」的账套（10-30 实机：F 栏录不进、免税商品的明细税码也是空的）：整单免税照语料另一种原生记法
        # 「0 - ไม่มี VAT」录，明细不标免税，主档照常（不另建免税品）
        ready = self._prepare(_purchase(items=[
            {"name": "ผักสด", "qty": "1", "unit_price": "100", "amount": "100.00", "item_mode": "direct_account",
             "account": EXPENSE_ACC, "vat_exempt": True}]))
        self.assertTrue(ready.ok, ready.error)
        self.assertEqual([it["vat_exempt"] for it in ready.payload["items"]], [False])
        codes = {r["STKCOD"].strip(): r["VATCOD"].strip() for r in _rows(self.acct, "STMAS")}
        self.assertNotEqual(codes[ready.payload["items"][0]["code"]], "0")

    def test_an_exempt_stock_line_gets_an_exempt_stock_item(self):
        # 10-29 实机（rec-laptop 覆盖样本）：整单免税的进货单原商品是库存品（生鲜），免税只给了服务品，建出应税库存品，Express 算出税
        self._per_item_vat()
        self._seed_stock()
        ready = self._prepare(_purchase(items=[{"name": "ผักสด", "qty": "2", "unit_price": "50", "amount": "100.00",
                                                "unit": "ถัง", "item_mode": "stock_item", "vat_exempt": True}]))
        self.assertTrue(ready.ok, ready.error)
        code = ready.payload["items"][0]["code"]
        row = {r["STKCOD"].strip(): r for r in _rows(self.acct, "STMAS")}[code]
        self.assertEqual((row["STKTYP"].strip(), row["VATCOD"].strip()), ("0", "0"))
        self.assertEqual(product_master.find_stock_item_code(self.acct, "ผักสด", exempt=True), code)

    def test_an_old_service_item_of_ours_without_units_is_repaired_before_entry(self):
        # 10-27 实机：试点账套里我们以前建的 PN00001 单位空、换算系数 0，Express 进货单按码查不到。代录用到它时只补空字段
        self._seed_stock()
        t = dbf.Table(str(self.acct / "STMAS.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        t.append({"STKCOD": "PN00001", "STKDES": "ค่าบริการ", "STKTYP": "3", "CREBY": S.PEARNLY_USERID, "PFACTOR": 0.0})
        t.close()
        before = _digest(self.acct, TRANSACTION_TABLES)
        ready = self._prepare(_purchase(supplier={"code": "", "name": "บริษัท ทดสอบ จำกัด", "tax_id": "0105555000011",
                                                  "prename": "บริษัท", "supplier_new": True}))
        self.assertTrue(ready.ok, ready.error)
        self.assertEqual(ready.payload["items"][0]["code"], "PN00001")
        self.assertIn({"kind": "repaired", "code": "PN00001"}, ready.created)
        self.assertEqual(product_master.shape_gaps(self.acct, "PN00001"), {})
        self.assertEqual(_digest(self.acct, TRANSACTION_TABLES), before)

    def test_existing_masters_are_found_without_touching_the_book(self):
        self._prepare(_purchase())
        before = _digest(self.acct, masters.MASTER_TABLES)
        self.closed.clear()
        ready = self._prepare(_purchase(ref_no="INV-2"))
        self.assertTrue(ready.ok)
        self.assertEqual(ready.created, [])
        self.assertTrue(ready.payload["supplier"]["code"] and ready.payload["items"][0]["code"])
        self.assertEqual(self.closed, [])  # 都有了：不退 Express
        self.assertEqual(_digest(self.acct, masters.MASTER_TABLES), before)

    def test_suspected_duplicate_is_not_created(self):
        self._prepare(_purchase())
        before = _digest(self.acct, masters.MASTER_TABLES)
        payload = _purchase(supplier={"code": "", "name": "บริษัท ทดสอบ. จำกัด", "tax_id": "0105555000029",
                                      "prename": "บริษัท", "supplier_new": True})
        ready = self._prepare(payload)
        self.assertFalse(ready.ok)
        self.assertEqual((ready.error_code, ready.outcome), (W.ERR_SUPPLIER_DUP, "needs_review"))
        self.assertEqual(_digest(self.acct, masters.MASTER_TABLES), before)

    def test_chosen_new_skips_the_lookup(self):
        self._prepare(_purchase())
        payload = _purchase(supplier={"code": "", "name": "บริษัท ทดสอบ จำกัด", "tax_id": "0105555000029",
                                      "prename": "บริษัท", "supplier_new": True, "confirmed_new": True})
        ready = self._prepare(payload)
        self.assertTrue(ready.ok, ready.error)
        self.assertEqual(len(_rows(self.acct, "APMAS")), 2)

    def test_failure_restores_every_master_table(self):
        before = _digest(self.acct, masters.MASTER_TABLES)
        with mock.patch.object(product_master, "ensure_item", side_effect=ValueError("boom")):
            ready = self._prepare(_purchase())
        self.assertFalse(ready.ok)
        self.assertEqual(ready.outcome, "needs_review")
        self.assertEqual(_rows(self.acct, "APMAS"), [])  # 已建的供应商一起还原
        self.assertEqual(_digest(self.acct, masters.MASTER_TABLES), before)
        self.assertEqual(ready.payload["supplier"]["code"], "")

    def test_book_in_use_waits_like_direct_write(self):
        before = _digest(self.acct, masters.MASTER_TABLES)
        with mock.patch.object(W, "_reindex", side_effect=W._WriteError("busy", W.ERR_WAITING_LOCK)):
            ready = self._prepare(_purchase())
        self.assertEqual((ready.ok, ready.error_code, ready.outcome), (False, W.ERR_WAITING_LOCK, "waiting_lock"))
        self.assertEqual(_digest(self.acct, masters.MASTER_TABLES), before)

    def test_other_account_set_or_company_is_refused(self):
        other = SimpleNamespace(account_dir=str(self.acct), account_set="OTHER", account_company="")
        ready = self._prepare(_purchase(), other)
        self.assertEqual((ready.ok, ready.error_code), (False, W.ERR_VALIDATION))
        wrong = SimpleNamespace(account_dir=str(self.acct), account_set="DATAT", account_company="บริษัท อื่น จำกัด")
        ready = self._prepare(_purchase(), wrong)
        self.assertEqual((ready.ok, ready.error_code), (False, W.ERR_COMPANY))
        self.assertEqual(_rows(self.acct, "APMAS"), [])
        self.assertEqual(self.closed, [])

    def test_sales_customer_is_created(self):
        payload = {
            "direction": "sales", "doctype": "IV", "account_set": "DATAT", "docdate_be": "690320",
            "vat_period_be": "690301", "ref_no": "IV-1",
            "customer": {"code": "", "name": "ร้าน ลูกค้า", "tax_id": "", "prename": "ร้าน", "customer_new": True},
            "vat_rate": 7.0, "base_amount": "100.00", "vat_amount": "7.00", "total_amount": "107.00",
            "lines": [{"acc": "11-02-01-00", "side": "D", "amount": "107.00", "desc": "ลูกหนี้"},
                      {"acc": "41-01-00-00", "side": "C", "amount": "100.00", "desc": "รายได้"},
                      {"acc": "11-05-04-02", "side": "C", "amount": "7.00", "desc": "ภาษีขาย"}],
        }
        ready = self._prepare(payload)
        self.assertTrue(ready.ok, ready.error)
        self.assertEqual([r["CUSCOD"].strip() for r in _rows(self.acct, "ARMAS")], [ready.payload["customer"]["code"]])

    def test_unknown_document_type_is_left_alone(self):
        payload = {"doctype": "JU", "items": []}
        ready = self._prepare(payload)
        self.assertTrue(ready.ok)
        self.assertIs(ready.payload, payload)


if __name__ == "__main__":
    unittest.main()
