"""ERP 校验失败的细节按词条翻给用户，不拼内部英文 / 码（2026-10-09，泰文界面出现英文）。"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))

from pearnly_erp_portal.purchasing import contracts  # noqa: E402

ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"


class PurchaseDetails(unittest.TestCase):
    def test_contract_sentences_map_to_entries(self):
        self.assertEqual(contracts.detail_key("line 3: quantity must be greater than 0"), ("erp.detail.line_qty", ("3",)))
        self.assertEqual(contracts.detail_key("line 2: discount exceeds the line"), ("erp.detail.line_discount", ()))
        self.assertEqual(contracts.detail_key("lines: required"), ("erp.detail.lines", ()))
        self.assertEqual(contracts.detail_key("net_payable: negative"), ("erp.detail.net_payable", ()))
        self.assertEqual(contracts.detail_key("supplier: required"), (None, ()))

    def test_real_contract_errors_are_recognised(self):
        with self.assertRaises(ValueError) as caught:
            contracts.compute_totals(lines=[{"qty": 0, "unit_price": 10}], has_vat=False, doc_kind="tax_invoice")
        self.assertEqual(contracts.detail_key(caught.exception)[0], "erp.detail.line_qty")


class Entries(unittest.TestCase):
    def test_every_detail_key_has_thai_and_chinese(self):
        rows = {line.split("\t")[-1]: line.split("\t") for line in
                (ERP / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines() if line.strip()}
        used = set()
        for path in (ERP / "cashier/sales.py", ERP / "purchasing/contracts.py", ERP / "sales/entry.py"):
            text = path.read_text()
            used.update(f"erp.detail.{name}" for name in __import__("re").findall(r"erp\.detail\.([a-z_]+)", text))
            used.update(f"erp.detail.{name}" for name in ("underpaid", "noncash_overpay", "line_discount", "lines", "discount",
                                                          "amount", "method", "payment", "grand_total", "zero_total")
                        if path.name == "sales.py" and "erp.detail." in text)
        for key in used:
            self.assertIn(key, rows, key)
            self.assertTrue(rows[key][1].strip() and rows[key][2].strip(), key)

    def test_no_raw_detail_is_appended(self):
        for name in ("cashier/sales.py", "purchasing/service.py", "inventory/stock.py", "sales/entry.py",
                     "pearnly_erp_portal/doctype/purchase_document/purchase_document.py"):
            text = (ERP / name).read_text()
            self.assertNotIn("{exc.detail}", text, name)
            self.assertNotIn(': {exc}")', text, name)
            self.assertNotIn("frappe.throw(str(exc))", text, name)
            self.assertNotIn("', '.join(reasons)", text, name)


if __name__ == "__main__":
    unittest.main()
