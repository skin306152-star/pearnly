# -*- coding: utf-8 -*-
"""采购明细行路由(车道 B):选库存的行走入库路并追加存货重分类分录,其余行照旧不碰库存。

覆盖:非库存行零回归 / 库存行追加「借 存货 · 贷 购货」且借贷平且购货净额归零 / 混行一张单写完(载荷 19) /
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
        "srv_vattyp": "accrual",  # 合成账套没有赊购历史：服务品进项税口径照人选过的（service_vat）
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
        """非库存行零回归:分录只有采购 / 进项税 / 应付,明细行零库存影响。"""
        res = self._write([_item("ค่าบริการ", "1", "600", "600", "direct_account")])
        self.assertTrue(res.ok, res.error)
        seqits = {(r.SEQIT or "").strip() for r in self._gl(res.docnum)}
        self.assertEqual(seqits, {"0", "3", "6"})
        for row in self._rows("STCRD", DOCNUM=res.docnum):
            self.assertTrue(S.stcrd_no_stock_impact(row))

    def test_stock_lines_debit_the_item_groups_inventory_account_like_native_express(self):
        """走库存(永续盘存)→ 照原生直接借物料科目组的存货科目(SEQIT 0),没有「借存货 / 贷购货」重分类;借贷仍平(S 盘 95 本 11-07)。"""
        res = self._write([_item("ดีเซล", "30", "20", "600", "stock_item")])
        self.assertTrue(res.ok, res.error)
        gl = self._gl(res.docnum)
        dr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT)
        cr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == S.TRNTYP_CREDIT)
        self.assertEqual(dr, cr)
        self.assertEqual({(r.SEQIT or "").strip() for r in gl}, {"0", "3", "6"})
        debits = [((r.ACCNUM or "").strip(), float(r.AMOUNT)) for r in gl
                  if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT and (r.SEQIT or "").strip() == "0"]
        self.assertEqual(debits, [(INVENTORY_ACC, 600.0)])  # 购货科目一行都没有了
        self.assertEqual(res.line_modes[0]["mode"], "stock_item")

    def _stcrd_by_seq(self, docnum):
        return {int((r.SEQNUM or "").strip()): r for r in self._rows("STCRD", DOCNUM=docnum)}

    def test_mixed_stock_and_non_stock_lines_land_in_one_document(self):
        """混行(载荷 19,ERP 按行选入库 / 只记账):一张单写完;SEQNUM 是整张单的行号不撞;只有库存行动库存、进重分类。"""
        res = self._write(
            [
                _item("ดีเซล", "10", "20", "200", "stock_item"),
                _item("ค่าขนส่ง", "1", "400", "400", "direct_account"),
            ],
            make_backup=True,
            payload_version=19,
        )
        self.assertTrue(res.ok, res.error)
        rows = self._stcrd_by_seq(res.docnum)
        self.assertEqual(sorted(rows), [1, 2])
        self.assertFalse(S.stcrd_no_stock_impact(rows[1]))  # 柴油真入库
        self.assertTrue(S.stcrd_no_stock_impact(rows[2]))   # 运费不动库存
        self.assertEqual([m["seq"] for m in res.line_modes], [1, 2])
        self.assertEqual([m["mode"] for m in res.line_modes], ["stock_item", "direct_account"])
        gl = self._gl(res.docnum)
        debits = {(r.ACCNUM or "").strip(): float(r.AMOUNT) for r in gl
                  if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT and (r.SEQIT or "").strip() == "0"}
        self.assertEqual(debits, {INVENTORY_ACC: 200.0, PURCHASE_ACC: 400.0})  # 只有库存行记存货，运费留在原科目
        self.assertFalse({"7", "8"} & {(r.SEQIT or "").strip() for r in gl})
        dr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT)
        cr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == S.TRNTYP_CREDIT)
        self.assertEqual(dr, cr)
        oil = self._rows("STMAS", STKCOD="OIL01")[0]
        self.assertAlmostEqual(float(oil.MREMBAL), 110.0, places=2)

    def test_mixed_purchase_lines_carry_the_native_cost_and_sale_columns(self):
        """原生 RR / HP(S 盘 95 本人录的单,11-07):库存行 X 栏记成本、XSALVAL 是 0;非库存行 X 栏与 XSALVAL 都记税前额。"""
        res = self._write(
            [
                _item("ดีเซล", "10", "20", "200", "stock_item"),
                _item("ค่าขนส่ง", "1", "400", "400", "direct_account"),
            ],
            payload_version=19,
        )
        self.assertTrue(res.ok, res.error)
        rows = self._stcrd_by_seq(res.docnum)
        self.assertEqual((float(rows[1].XTRNVAL), float(rows[1].XSALVAL)), (200.0, 0.0))
        self.assertEqual((float(rows[2].XUNITPR), float(rows[2].XTRNVAL), float(rows[2].XSALVAL)), (400.0, 400.0, 400.0))
        self.assertEqual({(r.POSOPR or "").strip() for r in rows.values()}, {S.STCRD_POSOPR_PURCHASE})

    def test_mixed_lines_keep_the_payload_order(self):
        """非库存行在前、库存行夹在中间:物理顺序与 SEQNUM 都照载荷顺序,NXTSEQ = 总行数。"""
        res = self._write(
            [
                _item("ค่าขนส่ง", "1", "100", "100", "direct_account"),
                _item("ดีเซล", "10", "20", "200", "stock_item"),
                _item("ค่าบริการ", "1", "300", "300", "direct_account"),
            ],
            payload_version=19,
        )
        self.assertTrue(res.ok, res.error)
        written = self._rows("STCRD", DOCNUM=res.docnum)
        self.assertEqual([int((r.SEQNUM or "").strip()) for r in written], [1, 2, 3])
        self.assertEqual([(r.STKDES or "").strip() for r in written], ["ค่าขนส่ง", "ดีเซล", "ค่าบริการ"])
        rows = self._stcrd_by_seq(res.docnum)
        self.assertTrue(S.stcrd_no_stock_impact(rows[1]))
        self.assertFalse(S.stcrd_no_stock_impact(rows[2]))
        self.assertTrue(S.stcrd_no_stock_impact(rows[3]))
        head = self._rows("APTRN", DOCNUM=res.docnum)[0]
        self.assertEqual((head.NXTSEQ or "").strip(), "3")

    def test_mixed_lines_take_the_stock_amount_out_of_the_line_the_stock_lines_sit_in(self):
        """采购分录按行科目拆成几条、第一条是费用行自己的科目:库存行的钱从它那条采购科目(``items_account``)扣出记存货,费用科目不动。"""
        freight = "53-01-00-00"
        lines = [
            {"acc": freight, "side": "D", "amount": "400.00", "desc": "ค่าขนส่ง", "role": "purchase"},
            {"acc": PURCHASE_ACC, "side": "D", "amount": "200.00", "desc": "ซื้อ", "role": "purchase"},
            {"acc": "11-05-04-01", "side": "D", "amount": "42.00", "desc": "ภาษีซื้อ", "role": "input_vat"},
            {"acc": "21-02-01-00", "side": "C", "amount": "642.00", "desc": "เจ้าหนี้", "role": "ap"},
        ]
        res = self._write(
            [
                {**_item("ค่าขนส่ง", "1", "400", "400", "direct_account"), "account": freight},
                _item("ดีเซล", "10", "20", "200", "stock_item"),
            ],
            lines=lines,
            payload_version=19,
        )
        self.assertTrue(res.ok, res.error)
        debits = {(r.ACCNUM or "").strip(): float(r.AMOUNT) for r in self._gl(res.docnum)
                  if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT and (r.SEQIT or "").strip() == "0"}
        self.assertEqual(debits, {freight: 400.0, INVENTORY_ACC: 200.0})  # 原采购科目扣成 0 就不写

    def test_readback_lets_only_the_stock_lines_of_a_mixed_document_touch_stock(self):
        """混行单读回:库存行放行库存影响,非库存行动了库存就报错(不因整张单有库存行一起放过)。"""
        res = self._write(
            [
                _item("ดีเซล", "10", "20", "200", "stock_item"),
                _item("ค่าขนส่ง", "1", "400", "400", "direct_account"),
            ],
            payload_version=19,
        )
        self.assertTrue(res.ok, res.error)
        self.assertEqual(dbf_detail.stock_line_numbers(res.line_modes), frozenset({1}))
        verify = lambda seqs: dbf_detail.verify_detail(  # noqa: E731
            self.acct, res.docnum, Decimal("600"), 2, allow_stock_impact=True, stock_seqs=seqs)
        self.assertIsNone(verify({1}))
        t = dbf.Table(str(self.acct / "STCRD.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        try:
            for r in t:
                if (r.DOCNUM or "").strip() == res.docnum and (r.SEQNUM or "").strip() == "2":
                    with r as rec:
                        rec.MREMBAL = 5.0
        finally:
            t.close()
        self.assertIn("non-stock line 2", verify({1}) or "")
        self.assertIsNone(verify(None))  # 不给行号:照以前整张放行

    def test_runs_split_lines_into_consecutive_kinds_with_whole_document_numbers(self):
        stock = lambda it: it["item_mode"] == "stock_item"  # noqa: E731
        items = [{"item_mode": m} for m in ("direct_account", "stock_item", "stock_item", "non_stock_item")]
        runs = dbf_detail.runs(items, stock)
        self.assertEqual([(kind, seqs) for kind, seqs, _part in runs], [(False, [1]), (True, [2, 3]), (False, [4])])
        self.assertEqual(dbf_detail.runs(items[1:3], stock)[0][1], [1, 2])
        with self.assertRaises(ValueError):
            dbf_detail.line_numbers(items, [1, 2])

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
