# -*- coding: utf-8 -*-
"""进销存闭环:采购票建库存 → 销售票扣库存结转真 COGS(跨单据 · 过真写盘器 · 合成账套)。

单模块级(write_stock_in_lines 本身、重分类分录、混行拒收)分别在 test_stock_purchase /
test_purchase_lines;这里只验它们拼起来是不是一条通的闭环 —— 采购不入库,销售侧就永远没货
可扣,C6 的整条路等于没通,而这一点任何单模块测试都照不到。

反向也钉:没进过货就先推销售票照样落(建档 + 卖成负 + 不结转成本),随后的进货票必须补回
这条负库存,不能另建一条同名档 —— 否则「先卖后买」的客户账里会长出两条同名品。
"""

from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import cdx_reindex  # noqa: E402
from companion import dbf_sales as WS  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as W  # noqa: E402
from companion import product_master  # noqa: E402

PURCHASE_ACC = "51-03-00-00"  # 购货 · 刻意 ≠ 存货科目,否则看不出重分类有没有发生
INVENTORY_ACC = "11-04-02-00"  # 存货 = ISACC.ACCNUM01
COGS_ACC = "51-01-00-00"  # 销货成本 = ISACC.ACCNUM02
VAT_IN_ACC = "11-05-04-01"
AP_ACC = "21-02-01-00"
AR_ACC = "11-02-01-00"
REVENUE_ACC = "41-01-00-00"
VAT_OUT_ACC = "11-05-04-02"

ITEM = "ผงชูรส"  # 账套里原本没有的品:采购建档 → 销售复用,闭环两端用同一个名字


def _item(qty, unit_price, amount, mode):
    return {
        "name": ITEM,
        "qty": str(qty),
        "unit_price": str(unit_price),
        "amount": amount,
        "unit": "ถัง",
        "item_mode": mode,
    }


def _purchase(ref_no, base, vat, total, qty, unit_price):
    return {
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "690115",
        "vat_period_be": "690101",
        "ref_no": ref_no,
        "supplier": {
            "code": "S001",
            "name": "ผู้ขาย",
            "tax_id": "",
            "prename": "",
            "supplier_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": base,
        "vat_amount": vat,
        "total_amount": total,
        "lines": [
            {"acc": PURCHASE_ACC, "side": "D", "amount": base, "desc": "ซื้อ"},
            {"acc": VAT_IN_ACC, "side": "D", "amount": vat, "desc": "ภาษีซื้อ"},
            {"acc": AP_ACC, "side": "C", "amount": total, "desc": "เจ้าหนี้"},
        ],
        "items": [_item(qty, unit_price, base, "stock_item")],
        "items_status": "ok",
        "items_account": PURCHASE_ACC,
    }


def _sale(qty, unit_price, base, vat, total):
    return {
        "direction": "sales",
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "690320",
        "vat_period_be": "690301",
        "ref_no": "IV-8001",
        "customer": {
            "code": "C001",
            "name": "ลูกค้า",
            "tax_id": "",
            "prename": "",
            "customer_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": base,
        "vat_amount": vat,
        "total_amount": total,
        "lines": [
            {"acc": AR_ACC, "side": "D", "amount": total, "desc": "ลูกหนี้"},
            {"acc": REVENUE_ACC, "side": "C", "amount": base, "desc": "รายได้"},
            {"acc": VAT_OUT_ACC, "side": "C", "amount": vat, "desc": "ภาษีขาย"},
        ],
        "items": [_item(qty, unit_price, base, "stock_sale")],
        "items_status": "ok",
        "items_account": REVENUE_ACC,
    }


class PurchaseToSaleLoopTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cdx_reindex, "runner_available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.acct = _synth_account.build(self.tmp / "70EXP" / "test")
        self._seed_template()

    def _seed_template(self):
        """账套里的另一个真库存品(建档要克隆它的结构)+ 存货/COGS 科目映射。
        刻意不是本测试要买卖的品 —— 那个品得靠采购票自己建出来。"""
        t = dbf.Table(str(self.acct / "STMAS.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        t.append(
            {
                "STKCOD": "OIL01",
                "STKDES": "ดีเซล",
                "STKTYP": "0",
                "ACCCOD": "ST01",
                "QUCOD": "ลต",
                "MREMBAL": 0.0,
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
                "ACCNUM02": COGS_ACC,
            }
        )
        t.close()

    def _rows(self, name, **match):
        t = dbf.Table(str(self.acct / f"{name}.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_ONLY)
        try:
            return [
                r for r in t if all((r[k] or "").strip() == v for k, v in match.items())
            ]
        finally:
            t.close()

    def _buy(self, payload):
        return W.write_purchase(
            payload,
            self.acct,
            allowed_sets=("DATAT",),
            selected_dir=str(self.acct),
            make_backup=False,
        )

    def _sell(self, payload):
        return WS.write_sale(
            payload,
            self.acct,
            allowed_sets=("DATAT",),
            selected_dir=str(self.acct),
            make_backup=False,
        )

    def _net(self, docnum, acc):
        """该科目在本凭证上的净额(借为正、贷为负)。"""
        total = Decimal("0")
        for r in self._rows("GLJNLIT", VOUCHER=docnum, ACCNUM=acc):
            amt = Decimal(str(r.AMOUNT))
            total += amt if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT else -amt
        return total

    def test_two_receipts_then_sale_carries_real_cogs(self):
        """进 100@฿10 + 100@฿14 → 200 件 / ฿2400 / 均价 ฿12;卖 50 件 → COGS ฿600、结存 150 件 / ฿1800。

        这是本批的存在理由:成本基础来自真实进货票,不是期初也不是猜的。
        """
        r1 = self._buy(_purchase("PO-7001", "1000.00", "70.00", "1070.00", 100, 10))
        self.assertTrue(r1.ok, r1.error)
        r2 = self._buy(_purchase("PO-7002", "1400.00", "98.00", "1498.00", 100, 14))
        self.assertTrue(r2.ok, r2.error)

        stkcod = product_master.find_stock_item_code(self.acct, ITEM)
        self.assertTrue(stkcod)
        stock = self._rows("STMAS", STKCOD=stkcod)[0]
        self.assertEqual(float(stock.MREMBAL), 200.0)
        self.assertEqual(float(stock.MREMVAL), 2400.0)

        res = self._sell(_sale(50, 30, "1500.00", "105.00", "1605.00"))
        self.assertTrue(res.ok, res.error)
        # 复用采购建的那条主档 —— 不是又建了一条同名空壳
        self.assertIs(res.line_modes[0]["created"], False)
        self.assertEqual(res.line_modes[0]["stkcod"], stkcod)

        after = self._rows("STMAS", STKCOD=stkcod)[0]
        self.assertEqual(float(after.MREMBAL), 150.0)
        self.assertEqual(float(after.MREMVAL), 1800.0)

        # COGS = 50 × 均价 12;贷方存货科目 = 采购入库时借的那个(闭环两端对得上)
        self.assertEqual(self._net(res.docnum, COGS_ACC), Decimal("600.00"))
        self.assertEqual(self._net(res.docnum, INVENTORY_ACC), Decimal("-600.00"))

        # 库存卡三条:两张进货 + 一张出货,出货行按均价结转
        card = self._rows("STCRD", STKCOD=stkcod)
        self.assertEqual(len(card), 3)
        out = [r for r in card if (r.DOCNUM or "").strip() == res.docnum]
        self.assertEqual(len(out), 1)
        self.assertEqual(float(out[0].XUNITPR), 12.0)
        self.assertEqual(float(out[0].XTRNVAL), 600.0)
        self.assertEqual(float(out[0].MREMBAL), 150.0)

    def test_reclass_does_not_rewrite_the_cloud_side_entry(self):
        """重分类是**追加**:云端算好、本来就平的那三行必须原位原额还在。

        借贷平 / 购货净额归零由 test_purchase_lines 覆盖;这里钉的是「没被改写」——
        直接把购货行的科目改成存货也能让净额归零,但那是在动客户已核对过的分录。
        """
        res = self._buy(_purchase("PO-7001", "1000.00", "70.00", "1070.00", 100, 10))
        self.assertTrue(res.ok, res.error)
        orig = [
            r
            for r in self._rows("GLJNLIT", VOUCHER=res.docnum, ACCNUM=PURCHASE_ACC)
            if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT
        ]
        self.assertEqual(len(orig), 1)
        self.assertEqual(float(orig[0].AMOUNT), 1000.0)
        self.assertEqual((orig[0].SEQIT or "").strip(), S.SEQIT["purchase"])
        # 进项税 / 应付不受库存路影响
        self.assertEqual(self._net(res.docnum, VAT_IN_ACC), Decimal("70.00"))
        self.assertEqual(self._net(res.docnum, AP_ACC), Decimal("-1070.00"))

    def test_sale_before_any_purchase_sells_into_negative(self):
        """没进过货就先推销售票:照落 —— 建档 + 卖成负 + 不结转成本(没有成本基础,绝不编)。

        钉的是闭环反向也走得通:后到的进货票要把这条负库存补回来,而不是另建一条同名档。
        """
        res = self._sell(_sale(1, 100, "100.00", "7.00", "107.00"))
        self.assertTrue(res.ok, res.error)
        stkcod = product_master.find_stock_item_code(self.acct, ITEM)
        self.assertTrue(stkcod)
        self.assertIs(res.line_modes[0]["created"], True)
        sold = self._rows("STMAS", STKCOD=stkcod)[0]
        self.assertEqual(float(sold.MREMBAL), -1.0)
        self.assertEqual(float(sold.MREMVAL or 0), 0.0)
        self.assertEqual(self._net(res.docnum, COGS_ACC), Decimal("0"))

        buy = self._buy(_purchase("PO-7009", "1000.00", "70.00", "1070.00", 100, 10))
        self.assertTrue(buy.ok, buy.error)
        self.assertIs(buy.line_modes[0]["created"], False)  # 落在同一条档上
        after = self._rows("STMAS", STKCOD=stkcod)[0]
        self.assertEqual(float(after.MREMBAL), 99.0)  # -1 + 100


if __name__ == "__main__":
    unittest.main(verbosity=2)
