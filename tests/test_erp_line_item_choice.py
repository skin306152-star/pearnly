#!/usr/bin/env python3
"""ERP 采购 / 销售按行选物料与库存（11-06，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 4.2）。不连库。

- 预选顺序 8 条（`products.line_rules.decide`）：人选的 → 不可抵扣增值税行 → 记忆 → 各家对方结论一致 → 认出的物料 →
  Express 非库存品只记账 → 识别说服务 / 费用单 → 其余新建并入库（未定）；卡片标签；
- 记忆（`products.line_memory`）：键 = 公司 × 方向 | 对方 | 品名（与 Express 存货对照同一个 ``item_subject``），不同公司互不套；
  记住的物料停用 / 是别家公司 Express 的档就当没记过；提交后按最终值覆盖；一般供应商只在全部记忆结论一致时套。
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()

from pearnly_core.express_rules.push.choices import item_subject  # noqa: E402
from pearnly_erp_portal.products import line_memory, line_rules  # noqa: E402

GOODS, SERVICE = line_rules.GOODS, line_rules.SERVICE


class Rules(unittest.TestCase):
    def test_a_person_s_choice_wins(self):
        result = line_rules.decide(chosen={"item_type": SERVICE}, remembered={"item_type": GOODS, "product": "P"})
        self.assertEqual((result["item_type"], result["source"], result["decided"]), (SERVICE, line_rules.USER, True))
        picked = line_rules.decide(chosen={"product": "P1"})
        self.assertEqual((picked["item_type"], picked["product"]), (GOODS, "P1"))

    def test_the_non_claimable_vat_line_is_record_only(self):
        result = line_rules.decide(non_claimable=True, matched={"product": "P"})
        self.assertEqual((result["item_type"], result["source"]), (SERVICE, line_rules.VAT))

    def test_memory_beats_consensus_and_matching(self):
        result = line_rules.decide(remembered={"item_type": GOODS, "product": "P-OLD", "location": "A1"},
                                   agreed={"item_type": SERVICE}, matched={"product": "P-NAME"}, recognised=SERVICE)
        self.assertEqual((result["product"], result["location"], result["source"]), ("P-OLD", "A1", line_rules.MEMORY))
        consensus = line_rules.decide(agreed={"item_type": SERVICE, "product": None}, matched={"product": "P"})
        self.assertEqual((consensus["item_type"], consensus["source"]), (SERVICE, line_rules.AGREED))

    def test_a_matched_item_moves_stock_unless_it_is_an_express_non_stock_item(self):
        stock = line_rules.decide(matched={"product": "P", "express_non_stock": False}, recognised=SERVICE)
        self.assertEqual((stock["item_type"], stock["source"]), (GOODS, line_rules.MATCHED))
        freight = line_rules.decide(matched={"product": "FREIGHT", "express_non_stock": True})
        self.assertEqual((freight["item_type"], freight["product"], freight["source"]),
                         (SERVICE, "FREIGHT", line_rules.EXPRESS_SERVICE))

    def test_recognised_service_or_an_expense_document_is_record_only(self):
        self.assertEqual(line_rules.decide(recognised=SERVICE)["item_type"], SERVICE)
        self.assertEqual(line_rules.decide(recognised=GOODS, expense=True)["item_type"], SERVICE)

    def test_the_rest_is_new_and_in_stock_but_undecided(self):
        result = line_rules.decide(recognised=GOODS)
        self.assertEqual((result["item_type"], result["product"], result["decided"]), (GOODS, None, False))

    def test_card_tags(self):
        self.assertEqual(line_rules.tag({"item_type": GOODS, "product": "P"}, "purchase"), "in")
        self.assertEqual(line_rules.tag({"item_type": GOODS, "product": None}, "purchase"), "new_in")
        self.assertEqual(line_rules.tag({"item_type": GOODS, "product": None}, "sales"), "new_out")
        self.assertEqual(line_rules.tag({"item_type": SERVICE, "product": "P"}, "sales"), "none")

    def test_the_non_claimable_mark_is_the_purchase_recognition_one(self):
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/recognition.py").read_text()
        self.assertIn(f'NON_CLAIMABLE_VAT = "{line_rules.NON_CLAIMABLE_VAT}"', source)


class Memory(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        fake.STATE.db = fake.DB()
        fake.STATE.site_dir = directory.name
        products = fake.STATE.db.table("ERP Product")
        products.update({
            "P-ICE": {"name": "P-ICE", "product_name": "น้ำแข็ง", "is_active": 1, "express_company": None},
            "P-OLD": {"name": "P-OLD", "product_name": "เก่า", "is_active": 0, "express_company": None},
            "P-B": {"name": "P-B", "product_name": "ของบริษัท B", "is_active": 1, "express_company": "CO-B",
                    "express_code": "X1", "express_stock": 1},
            "P-FREIGHT": {"name": "P-FREIGHT", "product_name": "ค่าขนส่ง", "is_active": 1, "express_company": None,
                          "express_code": "S01", "express_stock": 0},
        })
        original = line_memory.frappe.get_doc

        def get_doc(values, name=None):
            if isinstance(values, dict) and values.get("doctype") == line_memory.MEMORY:
                row = dict(values, name=line_memory.memory_name(values["company"], values["subject"]))
                return mock.Mock(insert=lambda **kw: fake.STATE.db.table(line_memory.MEMORY).__setitem__(row["name"], row))
            return original(values, name)

        patcher = mock.patch.object(line_memory.frappe, "get_doc", get_doc)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 按名认物料（`selection.match_product`）这里只看完全同名的启用档
        matcher = mock.patch("pearnly_erp_portal.masters.selection.match_product", self.match, create=True)
        try:
            matcher.start()
            self.addCleanup(matcher.stop)
        except (ImportError, AttributeError):
            pass
        finder = mock.patch("pearnly_erp_portal.products.service.find", lambda *a, **k: None, create=True)
        try:
            finder.start()
            self.addCleanup(finder.stop)
        except (ImportError, AttributeError):
            pass

    @staticmethod
    def match(description, company=None):
        rows = [name for name, row in fake.STATE.db.table("ERP Product").items()
                if row.get("product_name") == description and row.get("is_active")]
        return (rows[0] if len(rows) == 1 else None), len(rows)

    def learn(self, company, lines, party=("0105551234567", "ร้านน้ำแข็ง"), direction="purchase", document="PUR-1"):
        line_memory.learn(company, direction, party[0], party[1], lines, document)

    def test_the_key_is_the_shared_express_item_subject(self):
        self.assertEqual(line_memory.subject("purchase", "0105551234567", "ร้าน", "น้ำแข็ง 1 ถุง"),
                         item_subject("purchase", "0105551234567", "ร้าน", "น้ำแข็ง 1 ถุง"))

    def test_a_remembered_line_comes_back_only_for_the_same_company(self):
        self.learn("CO-A", [{"description": "น้ำแข็งหลอด", "product": "P-ICE", "item_type": GOODS, "location": "A1"}])
        found = line_memory.remembered("CO-A", "purchase", "0105551234567", "ร้านน้ำแข็ง", "น้ำแข็งหลอด")
        self.assertEqual(found, {"product": "P-ICE", "item_type": GOODS, "location": "A1"})
        self.assertIsNone(line_memory.remembered("CO-B", "purchase", "0105551234567", "ร้านน้ำแข็ง", "น้ำแข็งหลอด"))
        self.assertIsNone(line_memory.remembered("CO-A", "sales", "0105551234567", "ร้านน้ำแข็ง", "น้ำแข็งหลอด"))

    def test_a_later_submission_overwrites_the_memory(self):
        self.learn("CO-A", [{"description": "น้ำแข็งหลอด", "product": "P-ICE", "item_type": GOODS}])
        self.learn("CO-A", [{"description": "น้ำแข็งหลอด", "product": None, "item_type": SERVICE}], document="PUR-2")
        found = line_memory.remembered("CO-A", "purchase", "0105551234567", "ร้านน้ำแข็ง", "น้ำแข็งหลอด")
        self.assertEqual((found["item_type"], found["product"]), (SERVICE, None))
        [row] = fake.STATE.db.table(line_memory.MEMORY).values()
        self.assertEqual(row["last_document"], "PUR-2")

    def test_a_disabled_or_other_company_item_is_not_reused(self):
        self.learn("CO-A", [{"description": "เก่า", "product": "P-OLD", "item_type": GOODS},
                            {"description": "ของ B", "product": "P-B", "item_type": GOODS}])
        self.assertIsNone(line_memory.remembered("CO-A", "purchase", "0105551234567", "ร้านน้ำแข็ง", "เก่า"))
        self.assertIsNone(line_memory.remembered("CO-A", "purchase", "0105551234567", "ร้านน้ำแข็ง", "ของ B"))

    def test_a_general_supplier_takes_memory_only_when_every_supplier_agrees(self):
        self.learn("CO-A", [{"description": "ค่าส่ง", "product": None, "item_type": SERVICE}], party=("", "ร้าน 1"))
        self.learn("CO-A", [{"description": "ค่าส่ง", "product": None, "item_type": SERVICE}], party=("", "ร้าน 2"))
        self.assertEqual(line_memory.agreed("CO-A", "purchase", "ค่าส่ง")["item_type"], SERVICE)
        self.learn("CO-A", [{"description": "ค่าส่ง", "product": "P-ICE", "item_type": GOODS}], party=("", "ร้าน 3"))
        self.assertIsNone(line_memory.agreed("CO-A", "purchase", "ค่าส่ง"))

    def test_suggest_runs_the_order_and_fill_writes_the_line(self):
        self.learn("CO-A", [{"description": "น้ำแข็งหลอด", "product": "P-ICE", "item_type": GOODS}])
        lines = [{"description": "น้ำแข็งหลอด", "item_type": GOODS, "unit": "ถุง"},   # 记忆：票面名不同于物料名也认
                 {"description": "ค่าขนส่ง", "item_type": GOODS},                      # Express 非库存品：只记账
                 {"description": "ค่าบริการ", "item_type": SERVICE},                    # 识别说服务
                 {"description": "ของใหม่", "item_type": GOODS},                        # 新物料：未定
                 {"description": "VAT", line_rules.NON_CLAIMABLE_VAT: True}]
        with mock.patch.object(line_memory, "apply", wraps=line_memory.apply), \
                mock.patch("pearnly_erp_portal.masters.selection.own_unit", lambda item, unit: unit, create=True):
            results = line_memory.fill(lines, company="CO-A", direction="purchase", party_tax="0105551234567",
                                       party_name="ร้านน้ำแข็ง")
        self.assertEqual([row["source"] for row in results],
                         [line_rules.MEMORY, line_rules.EXPRESS_SERVICE, line_rules.RECOGNISED, line_rules.DEFAULT, line_rules.VAT])
        self.assertEqual([row["decided"] for row in results], [True, True, True, False, True])
        self.assertEqual([(line["item_type"], line.get("product")) for line in lines],
                         [(GOODS, "P-ICE"), (SERVICE, "P-FREIGHT"), (SERVICE, None), (GOODS, None), (SERVICE, None)])

    def test_a_typed_line_keeps_what_the_person_set(self):
        [result] = line_memory.fill([{"description": "ค่าขนส่ง", "item_type": GOODS}], company="CO-A", direction="purchase",
                                    typed=True)
        self.assertEqual((result["item_type"], result["source"]), (GOODS, line_rules.USER))

    def test_an_in_stock_line_without_an_item_is_not_remembered(self):
        self.learn("CO-A", [{"description": "ของใหม่", "product": None, "item_type": GOODS}])
        self.assertEqual(fake.STATE.db.table(line_memory.MEMORY), {})


if __name__ == "__main__":
    unittest.main()
