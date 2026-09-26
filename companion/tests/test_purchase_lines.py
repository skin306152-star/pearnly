# -*- coding: utf-8 -*-
"""采购明细行路由(车道 B):选库存的行走入库路并追加存货重分类分录,其余行照旧不碰库存。

覆盖:非库存行零回归 / 库存行追加「借 存货 · 贷 购货」且借贷平且购货净额归零 / 混行整单拒 /
dbf_detail 见到 stock_item 显性报错(不静默降级成不动库存的科目行)。
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import dbf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import cdx_reindex  # noqa: E402
from companion import dbf_detail  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as W  # noqa: E402
from companion import purchase_lines  # noqa: E402

from _synth_account import seed_fiscal_year  # noqa: E402

PURCHASE_ACC = "51-01-00-00"
INVENTORY_ACC = "11-04-02-00"


def _payload(items, **over):
    p = {
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "690228",
        "vat_period_be": "690201",
        "ref_no": "INV-STK-1",
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
            {"acc": "11-05-04-01", "side": "D", "amount": "42.00", "desc": "ภาษีซื้อ"},
            {"acc": "21-02-01-00", "side": "C", "amount": "642.00", "desc": "เจ้าหนี้"},
        ],
        "items": items,
        "items_status": "ok",
        "items_account": PURCHASE_ACC,
    }
    p.update(over)
    return p


def _item(name, qty, unit_price, amount, mode):
    return {
        "name": name,
        "qty": qty,
        "unit_price": unit_price,
        "amount": amount,
        "item_mode": mode,
    }


class PurchaseLineRoutingTest(unittest.TestCase):
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
        seed_fiscal_year(self.acct, 2026)  # 本模块的票是佛历 69xx = 公历 2026
        self._seed_stock_item()

    def _seed_stock_item(self):
        """种一个既有库存品 + ISACC 存货/COGS 科目映射(建档要克隆同账套真库存品结构)。"""
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

    def _write(self, items, *, make_backup=False, **over):
        return W.write_purchase(
            _payload(items, **over),
            self.acct,
            allowed_sets=("DATAT",),
            selected_dir=str(self.acct),
            make_backup=make_backup,
        )

    def _rows(self, name, **match):
        t = dbf.Table(str(self.acct / f"{name}.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_ONLY)
        try:
            return [
                r for r in t if all((r[k] or "").strip() == v for k, v in match.items())
            ]
        finally:
            t.close()

    def _gl(self, docnum):
        return self._rows("GLJNLIT", VOUCHER=docnum)

    def test_non_stock_lines_stay_on_the_old_path(self):
        """非库存行零回归:不追加重分类分录,明细行零库存影响。"""
        res = self._write([_item("ค่าบริการ", "1", "600", "600", "direct_account")])
        self.assertTrue(res.ok, res.error)
        seqits = {(r.SEQIT or "").strip() for r in self._gl(res.docnum)}
        self.assertEqual(seqits, {"0", "3", "6"})
        for row in self._rows("STCRD", DOCNUM=res.docnum):
            self.assertTrue(S.stcrd_no_stock_impact(row))

    def test_stock_lines_reclass_purchase_into_inventory(self):
        """走库存 → 追加 借 存货 / 贷 购货:借贷仍平,购货科目对这些行净额归零。"""
        res = self._write([_item("ดีเซล", "30", "20", "600", "stock_item")])
        self.assertTrue(res.ok, res.error)
        gl = self._gl(res.docnum)
        dr = sum(
            Decimal(str(r.AMOUNT))
            for r in gl
            if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT
        )
        cr = sum(
            Decimal(str(r.AMOUNT))
            for r in gl
            if (r.TRNTYP or "").strip() == S.TRNTYP_CREDIT
        )
        self.assertEqual(dr, cr)
        inv_dr = [r for r in gl if (r.SEQIT or "").strip() == "7"]
        pur_cr = [r for r in gl if (r.SEQIT or "").strip() == "8"]
        self.assertEqual(len(inv_dr), 1)
        self.assertEqual(len(pur_cr), 1)
        self.assertEqual((inv_dr[0].ACCNUM or "").strip(), INVENTORY_ACC)
        self.assertEqual((pur_cr[0].ACCNUM or "").strip(), PURCHASE_ACC)
        self.assertAlmostEqual(float(inv_dr[0].AMOUNT), 600.0, places=2)
        net_purchase = sum(
            Decimal(str(r.AMOUNT))
            * (1 if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT else -1)
            for r in gl
            if (r.ACCNUM or "").strip() == PURCHASE_ACC
        )
        self.assertEqual(net_purchase, Decimal("0"))
        self.assertEqual(res.line_modes[0]["mode"], "stock_item")

    def test_mixed_stock_and_non_stock_rejects_whole_document(self):
        """混行 → 整单失败并回滚(SEQNUM 会撞 + 只有一半货进库存),错误码指向入库路。"""
        res = self._write(
            [
                _item("ดีเซล", "10", "20", "200", "stock_item"),
                _item("ค่าขนส่ง", "1", "400", "400", "direct_account"),
            ],
            make_backup=True,  # 走真备份-回滚路,验单头也被撤掉
        )
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, W.ERR_STOCK_IN)
        self.assertEqual(self._rows("APTRN"), [])
        self.assertEqual(self._rows("STCRD"), [])

    def test_unbalanced_voucher_is_refused_before_it_reaches_the_ledger(self):
        """载荷整体平、但只有前两条借方进得了分录(第三条被丢)→ 写盘前自检拦下并整单回滚。"""
        lines = [
            {"acc": PURCHASE_ACC, "side": "D", "amount": "400.00", "desc": "ซื้อ"},
            {"acc": "11-05-04-01", "side": "D", "amount": "42.00", "desc": "ภาษีซื้อ"},
            {"acc": "53-01-00-00", "side": "D", "amount": "200.00", "desc": "ค่าขนส่ง"},
            {"acc": "21-02-01-00", "side": "C", "amount": "642.00", "desc": "เจ้าหนี้"},
        ]
        res = self._write(
            [_item("ค่าบริการ", "1", "600", "600", "direct_account")],
            make_backup=True,
            lines=lines,
        )
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, "ENTRY_NOT_BALANCED")
        self.assertEqual(self._rows("GLJNLIT"), [])
        self.assertEqual(self._rows("APTRN"), [])

    def test_dbf_detail_refuses_stock_item_instead_of_silent_fallback(self):
        """守卫:库存品行漏到非库存路 → 显性报错,不静默降级成不动库存的科目行。"""
        with self.assertRaises(dbf_detail.StockLineMisrouted):
            dbf_detail.resolve_line(
                self.acct,
                dbf_detail.ITEM_MODE_STOCK,
                "ดีเซล",
                PURCHASE_ACC,
                "",
                dt.date(2026, 2, 28),
                "BIT9",
            )


class GlBalanceGuardTest(unittest.TestCase):
    def test_has_stock_lines_reads_line_modes_not_reclass_rows(self):
        """赠品行(净额 0)动库存却不产重分类分录 —— 库存签名的放行不能挂在分录上。"""
        self.assertTrue(purchase_lines.has_stock_lines([{"mode": "stock_item"}]))
        self.assertFalse(purchase_lines.has_stock_lines([{"mode": "direct_account"}]))

    def test_imbalance_reports_unbalanced_rows(self):
        rows = [
            ("0", PURCHASE_ACC, S.TRNTYP_DEBIT, Decimal("600.00")),
            ("6", "21-02-01-00", S.TRNTYP_CREDIT, Decimal("642.00")),
        ]
        self.assertTrue(purchase_lines.imbalance(rows))
        rows.append(("3", "11-05-04-01", S.TRNTYP_DEBIT, Decimal("42.00")))
        self.assertEqual(purchase_lines.imbalance(rows), "")


if __name__ == "__main__":
    unittest.main()
