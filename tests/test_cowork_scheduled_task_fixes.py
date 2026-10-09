"""定时任务实测（2026-10-09，testcowork 演示账套 T9PDEMO）发现的推送问题，按类修后的回归：
无买方的门店简易税票记现金客户、名称只取第一行、整单折扣摊进明细、明确印 0 的税额是无税票、
后台推进按负责人的语言记原因、编辑任务不丢默认科目。"""
import pathlib
import sys
import unittest
from datetime import date
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from pearnly_cowork.push.express.common import ITEMS_MISMATCH, ITEMS_OK, amounts, extract_line_items  # noqa: E402
from pearnly_cowork.push.express.field_clean import clean_seller  # noqa: E402
from pearnly_cowork.push.express.sales_mapper import build_express_sales_payload  # noqa: E402
from pearnly_cowork.roundtrip import money  # noqa: E402
from pearnly_cowork.contracts.line_items import review_status  # noqa: E402

CONFIG = {"account_set": "DATAT", "revenue_acc": "41-01-00-00", "vat_output_acc": "11-05-04-02", "ar_acc": "11-02-01-00"}


class WalkInSale(unittest.TestCase):
    def history(self, **fields):
        base = {"subtotal": "100.00", "vat": "7.00", "invoice_number": "S-1"}
        base.update(fields)
        return {"id": "h", "invoice_date": "2026-09-15", "invoice_no": "S-1", "total_amount": "107.00", "fields": base}

    def test_no_buyer_at_all_is_the_cash_customer(self):
        result = build_express_sales_payload(self.history(), config=CONFIG, today=date(2026, 10, 9))
        self.assertTrue(result.ok, result.reason)
        self.assertEqual((result.payload["customer"]["code"], result.payload["customer"]["customer_new"]), ("เงินสด", False))

    def test_a_tax_id_without_a_name_is_still_a_customer(self):
        result = build_express_sales_payload(self.history(buyer_tax="0105551234567"), config=CONFIG, today=date(2026, 10, 9))
        self.assertNotEqual(result.payload["customer"]["code"] if result.ok else "", "เงินสด")


class PartyNameLines(unittest.TestCase):
    def test_only_the_first_line_goes_to_the_erp(self):
        self.assertEqual(clean_seller("บริษัท สยามโฮม จำกัด\nSIAM HOME APPLIANCE CO., LTD."), "บริษัท สยามโฮม จำกัด")
        self.assertEqual(clean_seller("บริษัท\nสยามโฮม จำกัด"), "บริษัท สยามโฮม จำกัด")
        self.assertEqual(clean_seller("  Bangchak  "), "Bangchak")


class Discount(unittest.TestCase):
    ITEMS = [{"name": "A", "qty": "2", "price": "1500", "subtotal": "3000"},
             {"name": "B", "qty": "1", "price": "2000", "subtotal": "2000"}]

    def test_subtotal_after_discount_spreads_the_discount_over_the_lines(self):
        fields = {"items": self.ITEMS, "discount": "500", "subtotal": "4500", "vat": "315", "total_amount": "4815"}
        detail = extract_line_items(fields, Decimal("4500"), total=Decimal("4815"))
        self.assertEqual(detail["status"], ITEMS_OK)
        self.assertEqual(sum(Decimal(item["amount"]) for item in detail["items"]), Decimal("4500.00"))
        self.assertEqual([item["amount"] for item in detail["items"]], ["2700.00", "1800.00"])
        self.assertIsNone(review_status(fields))

    def test_subtotal_before_discount_takes_the_discounted_base(self):
        fields = {"subtotal": "5000", "discount": "500", "vat": "315", "total_amount": "4815"}
        self.assertEqual(amounts(fields, {}), (Decimal("4500.00"), Decimal("315.00"), Decimal("4815.00")))

    def test_lines_that_do_not_match_even_with_the_discount_still_stop(self):
        fields = {"items": self.ITEMS, "discount": "100", "subtotal": "4500"}
        self.assertEqual(extract_line_items(fields, Decimal("4500"))["status"], ITEMS_MISMATCH)


class ZeroVat(unittest.TestCase):
    def test_a_printed_zero_is_a_no_vat_document(self):
        self.assertEqual(money.doc_totals([], explicit_base="420", printed_vat="0"),
                         (Decimal("420.00"), Decimal("0.00"), Decimal("420.00")))
        # 空着的照旧按税基重算
        self.assertEqual(money.doc_totals([], explicit_base="100", printed_vat=None)[1], Decimal("7.00"))


class Sources(unittest.TestCase):
    def test_background_push_uses_the_owner_language(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/folder_intake/pump.py").read_text()
        body = source[source.index("def _as(user):"):source.index("def _lock():")]
        self.assertIn("frappe.local.lang = get_user_lang(user)", body)
        self.assertIn("frappe.local.lang = previous_lang", body)

    def test_editing_a_task_keeps_its_default_account_until_the_accounts_are_read(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/public/desk/intake-folders.js").read_text()
        self.assertIn("if (state.options?.accounts) {", source)
        self.assertIn("state.options_key = key;", source)
        self.assertIn("next === state.account_set_key && state.options_key === next", source)


if __name__ == "__main__":
    unittest.main()
