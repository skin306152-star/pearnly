"""验收样票库（scripts/acceptance/samples.py、tests/acceptance/samples.toml）离线单测：登记的每张样票按行算出的合计与登记的预期一致
（样票自己算错就不能拿来验 CC）；票面带齐买卖双方、票号、佛历日期与金额，不带任何给模型的提示；按内容生成一次、内容没变不重生成；
不认识的名字、合计对不上的样票记「没测到」。不起浏览器（截图换成替身）。真 Chrome 生成 11-13 在本机跑过两张并看过画面。
"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "acceptance"))
spec = importlib.util.spec_from_file_location("acc_samples", ROOT / "scripts" / "acceptance" / "samples.py")
samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samples)


class SamplesTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        os.environ["PEARNLY_ACC_SAMPLES"] = self.folder.name + "/out"
        self.addCleanup(os.environ.pop, "PEARNLY_ACC_SAMPLES", None)

    def test_every_registered_sample_adds_up_to_what_it_expects(self):
        data = samples.registry()
        self.assertIn("purchase-a1", data["samples"])
        for name, sample in data["samples"].items():
            self.assertEqual(samples.check(sample), [], name)
            party = "customer_code" if sample.get("direction") == "sales" else "supplier_code"  # 销售票核的是客户编码
            self.assertTrue(sample.get("expect", {}).get(party), name)
        self.assertEqual(samples.totals(data["samples"]["purchase-a1"])["total"], Decimal("128.40"))

    def test_page_carries_the_facts_and_nothing_for_the_model(self):
        data = samples.registry()
        page = samples.page(data["samples"]["purchase-a1"], data["buyer"])
        for words in ("วิศวภัณฑ์ 1991", "0105535001847", "ACC-A1-0001", "13/11/2569", "1234567890123", "120.00", "8.40", "128.40"):
            self.assertIn(words, page)
        for words in ("V001", "expect", "purchase-a1", "lab-"):  # 账套里的编码、登记的预期、用例名都不在票面上
            self.assertNotIn(words, page)

    def test_a_sales_ticket_has_the_acceptance_company_as_seller_and_the_customer_as_buyer(self):
        data = samples.registry()
        sale = {"direction": "sales", "title": "ใบกำกับภาษี", "number": "ACC-S1-0001", "date": "2026-11-13", "vat_rate": "7",
                "customer": {"code": "C001", "name": "ร้านทดสอบ", "tax_id": "0105500000001", "branch": "สำนักงานใหญ่"},
                "lines": [{"description": "ค่าบริการ", "qty": "1", "unit": "งาน", "price": "1000.00"}]}
        page = samples.page(sale, data["buyer"])
        self.assertLess(page.index(data["buyer"]["tax_id"]), page.index("ลูกค้า"))   # 验收公司在票头（卖方）
        self.assertGreater(page.index("0105500000001"), page.index("ลูกค้า"))         # 客户在「ลูกค้า」栏
        self.assertNotIn("C001", page)

    def test_rounding_is_decimal_half_up_on_the_net_total(self):
        sample = {"vat_rate": "7", "lines": [{"qty": "3", "price": "0.335"}, {"qty": "1", "price": "10.07"}]}
        self.assertEqual(samples.totals(sample), {"rows": [Decimal("1.01"), Decimal("10.07")], "amount": Decimal("11.08"),
                                                  "vat": Decimal("0.78"), "total": Decimal("11.86")})

    def test_resolve_renders_once_and_refuses_unknown_or_wrong_samples(self):
        made = []

        def printer(source, target):
            made.append(target.name)
            target.write_bytes(b"png")
        first = samples.resolve("purchase-a1", printer=printer)
        self.assertEqual((first.suffix, samples.resolve("purchase-a1", printer=printer), len(made)), (".png", first, 1))
        with self.assertRaises(samples.Unmet) as caught:
            samples.resolve("purchase-zz", printer=printer)
        self.assertIn("样票库里没有", str(caught.exception))
        wrong = pathlib.Path(self.folder.name) / "wrong.toml"
        wrong.write_text((ROOT / "tests/acceptance/samples.toml").read_text(encoding="utf-8").replace('total = "128.40"', 'total = "128.00"'), encoding="utf-8")
        with self.assertRaises(samples.Unmet) as caught:
            samples.resolve("purchase-a1", printer=printer, path=wrong)
        self.assertIn("不一致", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
