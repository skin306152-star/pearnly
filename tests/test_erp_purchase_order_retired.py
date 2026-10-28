"""采购单不再有「采购订单」（用户 2026-10-28：「采购订单删掉吧」；采购单就是「已付款 + 货已到」的进货单）。

旧调用方（识别结果、LINE / CC 桌面版、旧接口）仍传 purchase_order 时落地成 `RETIRED_KINDS` 给的那一种；
单据类型选项、网页下拉、打印格式与入口都不再有它。
"""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
spec = importlib.util.spec_from_file_location("purchase_contracts", APP / "purchasing/contracts.py")
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)


class PurchaseOrderRetired(unittest.TestCase):
    def test_old_callers_land_on_a_kept_kind(self):
        target = contracts.RETIRED_KINDS["purchase_order"]
        self.assertIn(target, ("purchase_invoice", "goods"))
        self.assertEqual(contracts.landing_kind("purchase_order"), target)
        for kept in ("purchase_invoice", "expense", "goods", "service", None, ""):
            self.assertEqual(contracts.landing_kind(kept), kept)

    def test_the_kind_is_gone_from_every_place_a_person_or_a_post_can_pick_it(self):
        meta = json.loads((APP / "pearnly_erp_portal/doctype/purchase_document/purchase_document.json").read_text())
        options = next(f["options"] for f in meta["fields"] if f["fieldname"] == "doc_kind").split("\n")
        self.assertNotIn("purchase_order", options)
        self.assertIn(contracts.RETIRED_KINDS["purchase_order"], options)
        entry = (APP / "purchasing/entry.py").read_text(encoding="utf-8")
        self.assertIn('KINDS = {"purchase_invoice", "expense", "goods", "service"}', entry)
        self.assertIn('contracts.landing_kind(payload.get("doc_kind"))', entry)
        self.assertIn('contracts.landing_kind(result["doc_kind"])', (APP / "purchasing/recognition.py").read_text(encoding="utf-8"))
        for js in ("purchase-fields.js", "purchase-form.js"):
            self.assertNotIn("purchase_order", (APP / "public/desk" / js).read_text(encoding="utf-8"))
        self.assertFalse((APP / "pearnly_erp_portal/print_format/pearnly_purchase_order").exists())
        self.assertNotIn("Purchase Order", (APP / "public/desk/purchase-form.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
