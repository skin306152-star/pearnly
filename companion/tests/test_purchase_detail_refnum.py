# -*- coding: utf-8 -*-
"""STCRD 明细行必须带供应商票号(REFNUM),销项侧必须留空。

判据取自真账镜像(EXP67/68/69 共 17 套 · STCRD 活行 59 799):采购单别的明细行
RR 18168/18533=98.0%、HP 147/147=100% 带 REFNUM;销项单别 IV 0/9266、HS 0/5492、
HL 0/3076 全空。所以「采购填、销项不填」是照抄 Express 自己的写法,不是求整齐。

为什么这一列不是可有可无:Express 随程序发的库存卡报表(eng/thai/STCRD*.RWT ·
STK-CARD.RWT · STOCK001.RWT,镜像里 39 个模板 117 处绑定)的单据号列是
`DOC=IIF(STCRD->REFNUM # '  ', STCRD->REFNUM+' ', STCRD->DOCNUM-'-'-STCRD->SEQNUM)`
—— 这一列空着,库存卡上那一行就只剩内部单号,与同一张报表上所有原生行长得不一样,
会计要对供应商票就得逐张单点开看。
"""

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

from companion import cdx_reindex  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_sales as SA  # noqa: E402
from companion import dbf_writer as W  # noqa: E402

from _synth_account import seed_fiscal_year  # noqa: E402

REF_NO = "TIV67/0640"  # 真账里的供应商票号形状(STCRD.REFNUM 存的就是它)
PURCHASE_ACC = "51-01-00-00"
INVENTORY_ACC = "11-04-02-00"
SALES_ACC = "41-01-00-00"


def _item(name, amount, mode, qty="1"):
    return {
        "name": name,
        "qty": qty,
        "unit_price": amount,
        "amount": amount,
        "item_mode": mode,
    }


class StcrdRefnumTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cdx_reindex, "runner_available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.acct = self.tmp / "70EXP" / "test"
        self.acct.mkdir(parents=True)
        for name, spec in S.TABLE_SPECS.items():
            t = dbf.Table(
                str(self.acct / f"{name}.DBF"),
                spec,
                codepage=S.CODEPAGE,
                dbf_type=S.DBF_TYPE,
            )
            t.open(dbf.READ_WRITE)
            t.close()
        seed_fiscal_year(self.acct, 2026)
        self._seed_stock_item()

    def _seed_stock_item(self):
        t = dbf.Table(str(self.acct / "STMAS.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        t.append(
            {
                "STKCOD": "OIL01",
                "STKDES": "ดีเซล",
                "STKTYP": "0",
                "ACCCOD": "ST01",
                "MREMBAL": 100.0,
                "MREMVAL": 2000.0,
                "TOTBAL": 100.0,
                "TOTVAL": 2000.0,
            }
        )
        t.close()
        t = dbf.Table(str(self.acct / "ISACC.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        t.append(
            {
                "ACCTYP": "S",
                "ACCCOD": "ST01",
                "METHOD": "A",
                "ACCNUM01": INVENTORY_ACC,
                "ACCNUM02": "51-02-00-00",
            }
        )
        t.close()

    def _purchase(self, items):
        payload = {
            "doctype": "RR",
            "account_set": "DATAT",
            "docdate_be": "690228",
            "vat_period_be": "690201",
            "ref_no": REF_NO,
            "supplier": {
                "code": "S001",
                "name": "ผู้ขาย",
                "tax_id": "",
                "prename": "",
                "supplier_new": False,
            },
            "vat_rate": 7.0,
            "base_amount": "600.00",
            "vat_amount": "42.00",
            "total_amount": "642.00",
            "lines": [
                {"acc": PURCHASE_ACC, "side": "D", "amount": "600.00", "desc": "ซื้อ"},
                {
                    "acc": "11-05-04-01",
                    "side": "D",
                    "amount": "42.00",
                    "desc": "ภาษีซื้อ",
                },
                {
                    "acc": "21-02-01-00",
                    "side": "C",
                    "amount": "642.00",
                    "desc": "เจ้าหนี้",
                },
            ],
            "items": items,
            "items_status": "ok",
            "items_account": PURCHASE_ACC,
        }
        return W.write_purchase(
            payload,
            self.acct,
            allowed_sets=("DATAT",),
            selected_dir=str(self.acct),
            make_backup=False,
        )

    def _sale(self, items):
        payload = {
            "doctype": "IV",
            "account_set": "DATAT",
            "docdate_be": "690228",
            "vat_period_be": "690201",
            "ref_no": REF_NO,
            "customer": {
                "code": "C001",
                "name": "ลูกค้า",
                "tax_id": "",
                "prename": "",
                "customer_new": False,
            },
            "vat_rate": 7.0,
            "base_amount": "600.00",
            "vat_amount": "42.00",
            "total_amount": "642.00",
            "lines": [
                {
                    "acc": "11-02-01-00",
                    "side": "D",
                    "amount": "642.00",
                    "desc": "ลูกหนี้",
                },
                {"acc": SALES_ACC, "side": "C", "amount": "600.00", "desc": "ขาย"},
                {
                    "acc": "21-05-01-00",
                    "side": "C",
                    "amount": "42.00",
                    "desc": "ภาษีขาย",
                },
            ],
            "items": items,
            "items_status": "ok",
            "items_account": SALES_ACC,
        }
        return SA.write_sale(
            payload,
            self.acct,
            allowed_sets=("DATAT",),
            selected_dir=str(self.acct),
            make_backup=False,
        )

    def _stcrd(self, docnum):
        t = dbf.Table(str(self.acct / "STCRD.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_ONLY)
        try:
            return [
                {f: r[f] for f in t.field_names}
                for r in S.live_rows(t)
                if (r.DOCNUM or "").strip() == docnum
            ]
        finally:
            t.close()

    def _refnums(self, docnum):
        rows = self._stcrd(docnum)
        self.assertTrue(rows, f"{docnum} 一行明细都没写,这条断言就没在验东西")
        return {(r["REFNUM"] or "").strip() for r in rows}

    def test_direct_account_rows_carry_the_supplier_invoice_number(self):
        """直接科目行(费用/服务票的主路)。"""
        res = self._purchase([_item("ค่าบริการ", "600", "direct_account")])
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._refnums(res.docnum), {REF_NO})

    def test_non_stock_item_rows_carry_it_too(self):
        """非库存商品行:走的是另一个 builder,漏一个就漏半类票。"""
        res = self._purchase([_item("ค่าขนส่ง", "600", "non_stock_item")])
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._refnums(res.docnum), {REF_NO})

    def test_stock_in_rows_carry_it_too(self):
        """进真库存的行(库存卡上最需要对供应商票的恰恰是这类)。"""
        res = self._purchase([_item("ดีเซล", "600", "stock_item", qty="30")])
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._refnums(res.docnum), {REF_NO})

    def test_sales_rows_leave_refnum_empty_like_express_does(self):
        """销项侧留空 —— 真账 IV 0/9266、HS 0/5492 带 REFNUM,填上就是我们独有的形状。"""
        res = self._sale([_item("ค่าบริการ", "600", "direct_account")])
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self._refnums(res.docnum), {""})


if __name__ == "__main__":
    unittest.main()
