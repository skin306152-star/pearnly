# -*- coding: utf-8 -*-
"""服务品进项税口径（APTRN.SRV_VATTYP，10-29）：直写进货单按这本账套的历史定（service_vat），定不了用载荷里人选的，
都没有就不写、回 SERVICE_VAT_BASIS_CHOICE。过真写盘器、合成账套。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dbf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402
import account_rules_fixture as F  # noqa: E402

from companion import catalog_probe, cdx_reindex, service_vat  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as W  # noqa: E402
from companion.purchase_adapter import validate_purchase_payload  # noqa: E402

PURCHASE_ACC = "51-03-00-00"
VAT_IN_ACC = "11-05-04-01"
AP_ACC = "21-02-01-00"
DEFERRED_ACC = "11-05-04-09"  # 未到期进项税（ISINFO.ACCNUM28）


def _payload(ref, doctype="RR", mode="non_stock_item", vat="7.00"):
    total = format(100 + float(vat), ".2f")
    return {
        "doctype": doctype, "account_set": "DATAT", "docdate_be": "690115", "vat_period_be": "690101", "ref_no": ref,
        "supplier": {"code": "S001", "name": "ผู้ขาย", "tax_id": "", "prename": "", "supplier_new": False},
        "vat_rate": 7.0, "base_amount": "100.00", "vat_amount": vat, "total_amount": total,
        "lines": [{"acc": PURCHASE_ACC, "side": "D", "amount": "100.00", "desc": "ซื้อ"},
                  *([{"acc": VAT_IN_ACC, "side": "D", "amount": vat, "desc": "ภาษีซื้อ"}] if float(vat) else []),
                  {"acc": AP_ACC, "side": "C", "amount": total, "desc": "เจ้าหนี้"}],
        "items": [{"name": "ค่าบริการ", "qty": "1", "unit_price": "100.00", "amount": "100.00", "unit": "",
                   "item_mode": mode}],
        "items_status": "ok", "items_account": PURCHASE_ACC,
    }


class ServiceVatWriteTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cdx_reindex, "runner_available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        service_vat._cache.clear()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.acct = _synth_account.build(self.tmp / "70EXP" / "test")
        F.isinfo(self.acct, ACCNUM28=DEFERRED_ACC)
        F.glacc(self.acct, (DEFERRED_ACC, "ภาษีซื้อยังไม่ถึงกำหนด"))

    def _rows(self, table, docnum):
        t = dbf.Table(str(self.acct / f"{table}.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_ONLY)
        try:
            names = {"APTRN": "DOCNUM", "ISVAT": "DOCNUM", "GLJNLIT": "VOUCHER"}
            return [r for r in t if (r[names[table]] or "").strip() == docnum]
        finally:
            t.close()

    def _count(self):
        """账套里单据表、凭证表、进项税表一共几行（拒写时一行都不多）。"""
        total = 0
        for name in ("APTRN", "GLJNLIT", "ISVAT"):
            t = dbf.Table(str(self.acct / f"{name}.DBF"), codepage=S.CODEPAGE)
            t.open(dbf.READ_ONLY)
            total += len(t)
            t.close()
        return total

    def _history(self, *rows):
        """APTRN 里先放几张 Express 人录的单：(供应商, 口径[, 单别 RECTYP, 价格类型, 录入人, 状态])。"""
        t = dbf.Table(str(self.acct / "APTRN.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        try:
            for index, row in enumerate(rows):
                supcod, srv, rectyp, flgvat, userid, stat = (*row, "3", "2", "ACC", "N")[:6] if len(row) == 2 else row
                t.append({"RECTYP": rectyp, "DOCNUM": f"RR000{index:04d}", "SUPCOD": supcod, "FLGVAT": flgvat,
                          "SRV_VATTYP": srv, "USERID": userid, "DOCSTAT": stat})
        finally:
            t.close()

    def _buy(self, payload):
        return W.write_purchase(payload, self.acct, allowed_sets=("DATAT",), selected_dir=str(self.acct),
                                make_backup=False)

    def _srv(self, docnum):
        t = dbf.Table(str(self.acct / "APTRN.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_ONLY)
        try:
            return [(r.SRV_VATTYP or "").strip() for r in t if (r.DOCNUM or "").strip() == docnum]
        finally:
            t.close()

    def test_a_supplier_that_is_always_cash_basis_gets_cash_basis(self):
        self._history(("S001", "2"), ("S001", "2"), ("S002", "1"))
        res = self._buy(_payload("SV-1"))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._srv(res.docnum), ["2"])

    def test_a_supplier_without_history_follows_a_consistent_book(self):
        self._history(("S002", "2"), ("S003", "2"))
        res = self._buy(_payload("SV-2"))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._srv(res.docnum), ["2"])

    def test_undecided_is_not_written_until_someone_chooses(self):
        """供应商两种都有（或没历史、整本两种都有）：不写、回码，账套里不多一张单；载荷带人选的值就照它写。"""
        self._history(("S001", "1"), ("S001", "2"))
        before = self._count()
        res = self._buy(_payload("SV-3"))
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, service_vat.CHOICE_CODE)
        self.assertEqual(self._count(), before)
        res = self._buy({**_payload("SV-3"), service_vat.PAYLOAD_KEY: "cash_basis"})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._srv(res.docnum), ["2"])

    def test_cash_stock_only_and_no_vat_documents_keep_the_native_value(self):
        """现购单 1、只有库存品的赊购单 1、没有税的 -：都不看历史、不问（语料无例外）。"""
        self._history(("S001", "1"), ("S001", "2"))
        res = self._buy(_payload("SV-4", doctype="HP"))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._srv(res.docnum), ["1"])
        res = self._buy(_payload("SV-5", vat="0.00"))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._srv(res.docnum), ["-"])

    def test_pearnly_written_cancelled_cash_and_no_vat_rows_are_not_history(self):
        self._history(("S001", "2", "3", "2", "PEARNLY", "N"), ("S001", "2", "3", "2", "ACC", "C"),
                      ("S001", "2", "1", "1", "ACC", "N"), ("S001", "2", "3", "0", "ACC", "N"), ("S002", "1"))
        self.assertEqual(service_vat.history(self.acct), {"S002": "1"})
        res = self._buy(_payload("SV-6"))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._srv(res.docnum), ["1"])  # S001 没有算数的历史 → 整本一贯 1

    def test_history_reads_rows_appended_after_the_first_read(self):
        self._history(("S001", "1"))
        self.assertEqual(service_vat.history(self.acct), {"S001": "1"})
        self._history(("S001", "2"))
        self.assertEqual(service_vat.history(self.acct), {"S001": "12"})

    def test_the_supplier_catalog_reports_each_suppliers_basis(self):
        self._history(("S001", "2"))
        t = dbf.Table(str(self.acct / "APMAS.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        try:
            for code in ("S001", "S009"):
                t.append({"SUPCOD": code, "SUPNAM": "ผู้ขาย " + code})
        finally:
            t.close()
        rows = {row["code"]: row for row in catalog_probe.probe_suppliers(self.acct)}
        self.assertEqual(rows["S001"]["srv"], "2")
        self.assertEqual(rows["S009"]["srv"], service_vat.NO_HISTORY)

    def test_cash_basis_defers_the_input_vat_like_native_express(self):
        """10-30（语料 78 / 78）：口径 2 开单不写进项税记录、税期空，进项税借「未到期进项税」（ISINFO.ACCNUM28），付款时 Express 才转；
        口径 1 照旧借进项税科目、记税期、写进项税记录。"""
        self._history(("S001", "2"), ("S002", "1"))
        cash = self._buy(_payload("SV-8"))
        self.assertTrue(cash.ok, cash.error)
        head = self._rows("APTRN", cash.docnum)[0]
        self.assertEqual(((head.SRV_VATTYP or "").strip(), head.VATPRD), ("2", None))
        self.assertEqual(self._rows("ISVAT", cash.docnum), [])
        vat_lines = [(r.ACCNUM or "").strip() for r in self._rows("GLJNLIT", cash.docnum) if (r.SEQIT or "").strip() == "3"]
        self.assertEqual(vat_lines, [DEFERRED_ACC])
        accrual = self._buy({**_payload("SV-9"), "supplier": {**_payload("x")["supplier"], "code": "S002"}})
        self.assertTrue(accrual.ok, accrual.error)
        self.assertIsNotNone(self._rows("APTRN", accrual.docnum)[0].VATPRD)
        self.assertEqual(len(self._rows("ISVAT", accrual.docnum)), 1)
        self.assertEqual([(r.ACCNUM or "").strip() for r in self._rows("GLJNLIT", accrual.docnum)
                          if (r.SEQIT or "").strip() == "3"], [VAT_IN_ACC])

    def test_cash_basis_without_a_postable_deferred_account_is_not_written(self):
        F.isinfo(self.acct, ACCNUM28="")
        self._history(("S001", "2"))
        before = self._count()
        res = self._buy(_payload("SV-10"))
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, "ACCOUNT_NOT_POSTABLE")
        self.assertEqual(self._count(), before)

    def test_the_payload_value_must_be_a_known_basis(self):
        self.assertTrue(validate_purchase_payload({**_payload("SV-7"), "srv_vattyp": "accrual"},
                                                  allowed=("DATAT",)).ok)
        self.assertFalse(validate_purchase_payload({**_payload("SV-7"), "srv_vattyp": "3"}, allowed=("DATAT",)).ok)


class ServiceVatRuleTest(unittest.TestCase):
    def test_rule(self):
        basis = service_vat.basis
        self.assertEqual(basis(has_vat=False, cash=False, service=True, supplier="2"), "-")
        self.assertEqual(basis(has_vat=True, cash=True, service=True, supplier="2"), "1")
        self.assertEqual(basis(has_vat=True, cash=False, service=False, supplier="2"), "1")
        self.assertEqual(basis(has_vat=True, cash=False, service=True, supplier="2", book="12"), "2")
        self.assertEqual(basis(has_vat=True, cash=False, service=True, supplier="12", book="1"), None)
        self.assertEqual(basis(has_vat=True, cash=False, service=True, supplier="12", chosen="accrual"), "1")
        self.assertEqual(basis(has_vat=True, cash=False, service=True, supplier="0", book="2"), "2")
        self.assertEqual(basis(has_vat=True, cash=False, service=True), None)


if __name__ == "__main__":
    unittest.main()
