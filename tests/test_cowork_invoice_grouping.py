"""Offline invoice grouping and Decimal rules; no Site, file IO or OCR provider."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
import unittest

from pearnly_cowork.ocr.grouping import group_invoices, _norm_price
from pearnly_cowork.ocr.money import normalize_id, normalize_money, valid_thai_tax_id
from pearnly_cowork.ocr.pages import history_pages
from pearnly_cowork.ocr.schema.invoice import ThaiInvoice


def page(index, **fields):
    return {"page_index": index, "fields": fields}


def item(name="Rice", qty="1", price="10.00", **extra):
    return {"name": name, "qty": qty, "price": price, **extra}


class MoneyContract(unittest.TestCase):
    def test_ocr_money_cleaning_keeps_missing_separate_from_zero(self):
        for value, expected in [("1,780.00", "1780.00"), ("฿1780", "1780"),
                                (1780, "1780"), (" -1,780.01 baht ", "-1780.01"),
                                ("๗.๕๐", "7.50"), (0, "0"), ("-.5", "-0.5")]:
            with self.subTest(value=value):
                actual = normalize_money(value)
                self.assertIsInstance(actual, Decimal)
                self.assertEqual(actual, Decimal(expected))
        for value in (None, "", "-", ".", "-.", "NaN", "Infinity", "no price", "1.2.3", "--2", True):
            with self.subTest(value=value):
                self.assertIsNone(normalize_money(value))

    def test_legacy_cleaner_is_not_a_strict_numeric_parser(self):
        for value, expected in [("1e3", "13"), ("(100)", "100"), ("−100", "100"), ("7%", "7")]:
            self.assertEqual(normalize_money(value), Decimal(expected))

    def test_exact_money_does_not_collapse_large_values_or_underflow(self):
        self.assertNotEqual(normalize_money("9007199254740992"), normalize_money("9007199254740993"))
        self.assertEqual(normalize_money("9" * 400), Decimal("9" * 400))
        self.assertNotEqual(normalize_money("0." + "0" * 399 + "1"), Decimal(0))

    def test_decimal_price_keys_pin_half_even_and_keep_negative_zero(self):
        for value, expected in [("2.675", "2.68"), ("1.005", "1.00"), ("1.015", "1.02"),
                                ("-2.675", "-2.68"), ("-0.005", "-0.00"), ("2.665", "2.66")]:
            self.assertEqual(_norm_price(value), expected)
        with localcontext() as context:
            context.rounding = ROUND_HALF_UP
            self.assertEqual(_norm_price("2.665"), "2.66")
        self.assertEqual(_norm_price("Price unavailable"), "Price unavailable")

    def test_thai_tax_id_normalization_and_mod11(self):
        prefix = "073552700028"
        check = (11 - sum(int(c) * (13 - i) for i, c in enumerate(prefix)) % 11) % 10
        valid = prefix + str(check)
        self.assertEqual(normalize_id("0-7355-27000-28-9"), "0735527000289")
        self.assertTrue(valid_thai_tax_id(valid))
        self.assertTrue(valid_thai_tax_id("-".join(valid)))
        self.assertFalse(valid_thai_tax_id(valid[:-1] + str((check + 1) % 10)))
        self.assertFalse(valid_thai_tax_id("123"))
        self.assertFalse(valid_thai_tax_id(None))


class InvoiceGrouping(unittest.TestCase):
    def test_empty_and_fallback_add_missing_physical_indices_without_mutating_input(self):
        self.assertEqual(group_invoices([]), [])
        source = [{"fields": {"seller_name": "First", "notes": "a", "items": [item("One")]}},
                  {"fields": {"seller_name": "Second", "notes": "b", "items": [item("Two")]}}]
        original = deepcopy(source)
        groups = group_invoices(source)
        self.assertEqual(source, original)
        self.assertEqual(groups[0]["page_indices"], [1, 2])
        self.assertEqual(groups[0]["invoice_fields"]["seller_name"], "First")
        self.assertEqual(groups[0]["invoice_fields"]["notes"], "a · b")
        self.assertEqual(len(groups[0]["invoice_fields"]["items"]), 2)

    def test_nonconsecutive_same_number_pages_merge_into_original_group(self):
        groups = group_invoices([page(1, invoice_number=" A ", seller_name="Seller", items=[item("One")]),
                                 page(2, invoice_number="B", total_amount="214"),
                                 page(3, invoice_number="A", total_amount="107", items=[item("Two")])])
        self.assertEqual([g["page_indices"] for g in groups], [[1, 3], [2]])
        self.assertEqual(groups[0]["invoice_fields"]["invoice_number"], " A ")
        self.assertEqual(groups[0]["invoice_fields"]["total_amount"], "107")
        self.assertEqual(len(groups[0]["invoice_fields"]["items"]), 2)

    def test_exactly_half_numbered_pages_choose_number_boundaries(self):
        groups = group_invoices([page(1, invoice_number="A", total_amount="107"),
                                 page(2, notes="continuation"), page(3, invoice_number="B", total_amount="214"),
                                 page(4, subtotal="100", total_amount="107")])
        self.assertEqual([g["page_indices"] for g in groups], [[1, 2], [3], [4]])

    def test_self_contained_unnumbered_same_total_is_continuation_different_total_is_new(self):
        for total, expected in [("฿107.00", [[1, 2]]), ("214", [[1], [2]])]:
            groups = group_invoices([page(1, invoice_number="A", total_amount="107"),
                                     page(2, subtotal="100", total_amount=total)])
            self.assertEqual([g["page_indices"] for g in groups], expected)
        # A total without subtotal/VAT is a continuation even when different.
        groups = group_invoices([page(1, invoice_number="A", total_amount="107"), page(2, total_amount="214")])
        self.assertEqual(groups[0]["page_indices"], [1, 2])

    def test_pending_leading_pages_attach_to_first_formed_invoice(self):
        groups = group_invoices([page(1, notes="cover"), page(2, invoice_number="A"), page(3, invoice_number="B")])
        self.assertEqual([g["page_indices"] for g in groups], [[1, 2], [3]])
        self.assertEqual(groups[0]["invoice_fields"]["notes"], "cover")

    def test_money_end_pages_collect_preceding_pages_and_preserve_tail_behavior(self):
        groups = group_invoices([page(1, items=[item("Prefix")]), page(2, total_amount="107", notes="end"),
                                 page(3, items=[item("Tail")], notes="tail")])
        self.assertEqual(groups[0]["page_indices"], [1, 2, 3])
        # Known source behavior: appended tail evidence does not rebuild fields.
        self.assertEqual([v["name"] for v in groups[0]["invoice_fields"]["items"]], ["Prefix"])
        self.assertEqual(groups[0]["invoice_fields"]["notes"], "end")
        self.assertEqual(groups[0]["source_pages"][2]["fields"]["items"][0]["name"], "Tail")

    def test_below_half_numbered_pages_uses_each_total_as_end_boundary(self):
        groups = group_invoices([page(1, invoice_number="A", total_amount="1"), page(2, total_amount="2"),
                                 page(3, total_amount="3"), page(4, total_amount="4")])
        self.assertEqual([g["page_indices"] for g in groups], [[1], [2], [3], [4]])

    def test_zero_total_is_present_and_number_case_remains_distinct(self):
        groups = group_invoices([page(1, total_amount="0"), page(2, total_amount=0)])
        self.assertEqual([g["page_indices"] for g in groups], [[1], [2]])
        groups = group_invoices([page(1, invoice_number="A"), page(2, invoice_number="a")])
        self.assertEqual(len(groups), 2)

    def test_all_schema_scalars_and_provenance_pass_without_nested_evidence_fields(self):
        fields = {key: key for key in ThaiInvoice.model_fields}
        fields.update(items=[], notes="note", direction="purchase", history_id="original-history")
        group = group_invoices([page(7, **fields)])[0]
        excluded = {"items", "notes", "additional_invoices", "source_refs", "is_not_invoice", "is_copy_or_duplicate"}
        for key in set(ThaiInvoice.model_fields) - excluded:
            self.assertEqual(group["invoice_fields"][key], key)
        for key in excluded - {"items", "notes"}:
            self.assertNotIn(key, group["invoice_fields"])
        self.assertEqual(group["invoice_fields"]["direction"], "purchase")
        self.assertEqual(group["invoice_fields"]["history_id"], "original-history")
        self.assertIn("source_refs", group["source_pages"][0]["fields"])

    def test_strict_and_substring_item_dedup_keep_longer_name_without_altering_caller(self):
        source = [page(1, invoice_number="A", items=[item("ราคา ส่ง", "1", "฿10")]),
                  page(2, invoice_number="A", items=[item("ราคาส่ง", "1 ก้อน", "10.00"),
                                                    item("ราคาส่ง Original", "1", "10")])]
        original = deepcopy(source)
        group = group_invoices(source)[0]
        self.assertEqual(len(group["invoice_fields"]["items"]), 1)
        self.assertEqual(group["invoice_fields"]["items"][0]["name"], "ราคาส่ง Original")
        self.assertEqual(source, original)

    def test_fuzzy_copy_threshold_is_at_least_half_and_normal_pages_do_not_fuzzy_merge(self):
        first = [item("Chocolate cookie", price="5"), item("Pearnly rice 1kg", price="9")]
        copy_page = [first[0].copy(), item("Pearnly rice 1k9", price="9")]
        grouped = group_invoices([page(1, invoice_number="A", items=first), page(2, invoice_number="A", items=copy_page)])
        self.assertEqual(len(grouped[0]["invoice_fields"]["items"]), 2)
        normal_page = copy_page + [item("Tea", price="2")]
        grouped = group_invoices([page(1, invoice_number="A", items=first), page(2, invoice_number="A", items=normal_page)])
        self.assertEqual(len(grouped[0]["invoice_fields"]["items"]), 4)

    def test_same_physical_page_siblings_and_history_adapter_keep_evidence(self):
        source = [page(4, invoice_number="A", total_amount="107"),
                  {**page(4, invoice_number="B", total_amount="214"), "_multi_invoice_sibling": True}]
        groups = group_invoices(source)
        self.assertEqual([g["page_indices"] for g in groups], [[4], [4]])
        for group in groups:
            saved = history_pages(group)
            self.assertEqual([p["page_index"] for p in saved], group["page_indices"])
            self.assertEqual(saved[0]["fields"], group["invoice_fields"])
        self.assertTrue(history_pages(groups[1])[0]["_multi_invoice_sibling"])
        same_number = group_invoices([page(4, invoice_number="A", items=[item("One")]),
                                      page(4, invoice_number="A", items=[item("Two")])])[0]
        self.assertEqual(same_number["page_indices"], [4, 4])
        self.assertEqual([p["page_index"] for p in history_pages(same_number)], [4, 4])



class MoneyBlock(unittest.TestCase):
    """多页合成一张票：金额块整组取末尾第一页自己对得上的（10-09 实测：合计只印在第 2 页的票，第 1 页编出
    28,770 / 税 0，逐字段取第一个非空把真合计 54,056.40 丢了）。"""

    def test_totals_on_the_last_page_beat_a_made_up_first_page(self):
        pages = [page(1, invoice_number="RP-2569-0456", subtotal="28770.00", vat="0", total_amount="28770.00",
                      items=[item("A", "1", "28770.00")]),
                 page(2, invoice_number="RP-2569-0456", subtotal="50520.00", vat="3536.40", total_amount="54056.40",
                      items=[item("B", "1", "21750.00")])]
        fields = group_invoices(pages)[0]["invoice_fields"]
        self.assertEqual((fields["subtotal"], fields["vat"], fields["total_amount"]), ("50520.00", "3536.40", "54056.40"))
        self.assertEqual(len(fields["items"]), 2)

    def test_the_block_is_taken_whole_with_its_discount(self):
        pages = [page(1, invoice_number="X", subtotal="999", discount="1", total_amount="999"),
                 page(2, invoice_number="X", subtotal="5000", discount="500", vat="315", total_amount="4815")]
        fields = group_invoices(pages)[0]["invoice_fields"]
        self.assertEqual((fields["subtotal"], fields["discount"], fields["vat"], fields["total_amount"]),
                         ("5000", "500", "315", "4815"))

    def test_without_a_consistent_page_the_first_value_rule_stays(self):
        pages = [page(1, invoice_number="Y", total_amount="107"), page(2, invoice_number="Y", vat="7")]
        fields = group_invoices(pages)[0]["invoice_fields"]
        self.assertEqual((fields["total_amount"], fields["vat"]), ("107", "7"))

    def test_totals_only_on_the_first_page_stay_there(self):
        pages = [page(1, invoice_number="Z", subtotal="100", vat="7", total_amount="107"),
                 page(2, invoice_number="Z", items=[item("Tail")])]
        fields = group_invoices(pages)[0]["invoice_fields"]
        self.assertEqual((fields["subtotal"], fields["vat"], fields["total_amount"]), ("100", "7", "107"))

    def test_a_zero_vat_block_on_a_later_page_is_not_evidence(self):
        # 金标 6901827：续页是一组「税 0、小计 = 合计」，第 1 页才是印着的真金额块。
        pages = [page(1, invoice_number="INV1", subtotal="5230.00", vat="366.10", total_amount="5596.10"),
                 page(2, invoice_number="INV1", subtotal="5596.10", vat="0", total_amount="5596.10")]
        fields = group_invoices(pages)[0]["invoice_fields"]
        self.assertEqual((fields["subtotal"], fields["vat"], fields["total_amount"]), ("5230.00", "366.10", "5596.10"))

    def test_a_single_page_is_unchanged(self):
        fields = group_invoices([page(1, invoice_number="S", subtotal="100", vat="0", total_amount="999")])[0]["invoice_fields"]
        self.assertEqual((fields["subtotal"], fields["vat"], fields["total_amount"]), ("100", "0", "999"))

if __name__ == "__main__":
    unittest.main()
