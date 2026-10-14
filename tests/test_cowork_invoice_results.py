"""Pure integration with real validated Pydantic results; no Site, charge or IO."""
from decimal import Decimal
import unittest

from pearnly_cowork.contracts.history import HistoryContractError, initial_history
from pearnly_cowork.ocr.results import OcrResultError, prepare_history_batch
from pearnly_cowork.ocr.schema.invoice import validate_invoice
from pearnly_cowork.ocr.schema.results import PipelinePageResult, PipelineResult


def invoice(**values):
    return validate_invoice(values, anchor_year=2026)


def page(number=1, inv=None, **values):
    return PipelinePageResult(page_number=number, invoice=inv or invoice(invoice_number="A", total_amount="107"),
        **values)


def batch(*pages, page_count=None):
    return PipelineResult(pages=list(pages), page_count=len(pages) if page_count is None else page_count,
        elapsed_ms=25, estimated_cost_thb=Decimal("0.05"))


def prepare(result, **values):
    return prepare_history_batch(result, expected_page_count=values.pop("expected_page_count", result.page_count),
        workspace="workspace-A", source=values.pop("source", "manual"), **values)


class InvoiceResultPreparation(unittest.TestCase):
    def test_complete_groups_keep_fields_confidence_ai_raw_and_physical_pages(self):
        inv = invoice(invoice_number="A", date_raw="20/09/2569", date="2026-09-20", seller_name="Seller",
            buyer_name="Buyer", total_amount="107", subtotal="100", vat="7",
            items=[{"name": "Rice", "qty": "1", "price": "100", "subtotal": "100"}])
        result = batch(page(1, inv, layer_chain=["L1", "L2"], layer2_input_tokens=20,
            confidence_band="yellow_confirm", validation_warnings=["review amount"],
            extraction_audit={"amount_source": "column"}))
        original = result.model_dump(mode="json")
        plan = prepare(result, posting_kind="stock", direction="expense")
        self.assertEqual(plan["state"], "ready")
        self.assertEqual(plan["confidence"], "high")
        self.assertEqual(plan["confidence_band"], "yellow_confirm")
        self.assertTrue(plan["has_billable_result"])
        group = plan["groups"][0]
        self.assertEqual(group["source_page_indices"], [1])
        self.assertEqual(group["posting_kind"], "stock")
        self.assertEqual(group["pages"][0]["fields"]["direction"], "purchase")
        self.assertEqual(group["pages"][0]["_extraction_audit"], {"amount_source": "column"})
        self.assertEqual(group["pages"], group["ai_raw"])
        body = initial_history(group["pages"], source=group["source"], confidence=group["confidence"],
            ai_raw=group["ai_raw"], posting_kind=group["posting_kind"], anchor_year=2026)
        self.assertEqual(body["pages"], body["ai_raw"])
        group["pages"][0]["fields"]["total_amount"] = "999"
        self.assertEqual(group["ai_raw"][0]["fields"]["total_amount"], "107")
        self.assertEqual(result.model_dump(mode="json"), original)
        self.assertNotIn("direction", plan["raw_pages"][0]["fields"])

    def test_total_is_the_grand_total_before_withholding_tax(self):
        # 2026-10-02 金标评测：读出了预扣税、合计却是扣后的实付额 → 合计改回扣前的含税总额（票面 จำนวนเงินรวมทั้งสิ้น）
        net_paid = invoice(invoice_number="A", subtotal="20000", vat="1400", wht_rate="3", wht_amount="600",
            total_amount="20800")
        plan = prepare(batch(page(1, net_paid)))
        fields = plan["groups"][0]["pages"][0]["fields"]
        self.assertEqual((fields["total_amount"], fields["wht_amount"]), ("21400.00", "600"))
        # 合计本来就是扣前的、没有预扣税、或对不上的：不动
        for values in ({"total_amount": "21400", "wht_amount": "600"}, {"total_amount": "21400", "wht_amount": ""},
                       {"total_amount": "20500", "wht_amount": "600"}):
            same = invoice(invoice_number="B", subtotal="20000", vat="1400", **values)
            kept = prepare(batch(page(1, same)))["groups"][0]["pages"][0]["fields"]
            self.assertEqual(kept["total_amount"], values["total_amount"])

    def test_an_abbreviated_invoice_read_without_vat_is_saved_with_vat_split_out(self):
        # 2026-10-13 testcowork：手写 ABB 727.00 读成税前 727.00、税额 "0"；简式税票票面价依法含 VAT（ม.86/6）
        abb = invoice(invoice_number="ABB-1", document_type="simplified_tax_invoice", subtotal="727.00", vat="0",
            total_amount="727.00", notes="ABB / VAT INCLUDED")
        split = invoice(invoice_number="ABB-2", document_type="simplified_tax_invoice", subtotal="557.94",
            vat="39.06", total_amount="597.00")
        full = invoice(invoice_number="T-1", document_type="tax_invoice", subtotal="727.00", vat="0",
            total_amount="727.00")
        exempt = invoice(invoice_number="ABB-3", document_type="simplified_tax_invoice", subtotal="120", vat="",
            total_amount="120", items=[{"name": "ผักสด (ยกเว้นภาษี)", "subtotal": "120"}])
        for direction in ("sales", "purchase"):
            with self.subTest(direction):
                plan = prepare(batch(page(1, abb), page(2, split), page(3, full), page(4, exempt)), direction=direction)
                saved = [(group["pages"][0]["fields"]["subtotal"], group["pages"][0]["fields"]["vat"])
                         for group in plan["groups"]]
                self.assertEqual(saved, [("679.44", "47.56"), ("557.94", "39.06"), ("727.00", "0"), ("120", None)])
                first = plan["groups"][0]
                self.assertEqual(first["pages"][0]["fields"]["direction"], direction)
                self.assertEqual(first["pages"], first["ai_raw"])
                self.assertEqual(first["pages"][0]["_extraction_audit"],
                                 {"vat_included": {"subtotal": "727.00", "vat": "0"}})
                # 只规整数字：不加提示、不改复核状态
                self.assertEqual(first["pages"][0]["_validation_warnings"], [])
                self.assertEqual(plan["state"], "ready")
                # 分组前的原读数不动
                self.assertEqual(plan["raw_pages"][0]["fields"]["vat"], "0")

    def test_an_unread_page_joins_the_invoice_before_it_and_flags_it(self):
        unread = PipelinePageResult(page_number=2, invoice=invoice(), layer_chain=["PAGE_UNREAD"],
            needs_manual_review=True, confidence_band="needs_review",
            validation_warnings=["page_unread: page 2 (ocr.response_json_invalid)"], error="ocr.response_json_invalid")
        result = batch(page(1, invoice(invoice_number="A", total_amount="107")), unread,
                       page(3, invoice(invoice_number="B", total_amount="214")))
        plan = prepare(result)
        self.assertEqual(plan["state"], "review_required")
        self.assertEqual([group["source_page_indices"] for group in plan["groups"]], [[1, 2], [3]])
        first = plan["groups"][0]["pages"][0]
        self.assertTrue(first["_needs_manual_review"])
        self.assertIn("page_unread: page 2 (ocr.response_json_invalid)", first["_validation_warnings"])
        # 第 1 页就读不出：挂到第一张
        plan = prepare(batch(unread.model_copy(update={"page_number": 1}), page(2, invoice(invoice_number="B",
                                                                                          total_amount="1"))))
        self.assertEqual(plan["groups"][0]["source_page_indices"], [1, 2])
        # 全部读不出：整份失败
        with self.assertRaises(OcrResultError):
            prepare(batch(unread.model_copy(update={"page_number": 1})))

    def test_same_page_multiple_invoices_do_not_double_physical_page_count(self):
        main = invoice(invoice_number="A", total_amount="107", additional_invoices=[
            {"invoice_number": "B", "total_amount": "214"}])
        result = batch(page(1, main, layer2_input_tokens=100))
        plan = prepare(result)
        self.assertEqual(plan["physical_page_count"], 1)
        self.assertEqual(plan["invoice_count"], 2)
        self.assertEqual([group["source_page_indices"] for group in plan["groups"]], [[1], [1]])
        self.assertEqual([group["pages"][0]["page_index"] for group in plan["groups"]], [1, 1])
        self.assertTrue(plan["groups"][1]["pages"][0]["_multi_invoice_sibling"])

    def test_nonconsecutive_pages_and_row_direction_provenance_survive(self):
        result = batch(page(1, invoice(invoice_number="A"), declared_direction="sales", source_history_id="old-A"),
                       page(2, invoice(invoice_number="B", total_amount="214")),
                       page(3, invoice(invoice_number="A", total_amount="107")))
        plan = prepare(result, direction="purchase", source="cowork_line")
        self.assertEqual([group["source_page_indices"] for group in plan["groups"]], [[1, 3], [2]])
        first = plan["groups"][0]
        self.assertEqual(first["pages"][0]["fields"]["direction"], "sales")
        self.assertEqual(first["pages"][0]["fields"]["history_id"], "old-A")
        self.assertEqual(first["source"], "cowork_line")
        self.assertTrue(first["pages"][1]["is_duplicate"])
        self.assertEqual(plan["groups"][1]["pages"][0]["fields"]["direction"], "purchase")

    def test_copy_fallback_and_batch_confidence_keep_source_behavior(self):
        result = batch(page(1, invoice(invoice_number="A", total_amount="107", is_copy_or_duplicate=True)),
                       page(2, invoice(invoice_number="B", is_copy_or_duplicate=True)))
        plan = prepare(result)
        self.assertEqual(plan["confidence"], "low")
        self.assertFalse(plan["groups"][0]["pages"][0]["is_duplicate"])
        self.assertTrue(plan["groups"][1]["pages"][0]["is_duplicate"])
        self.assertEqual({group["confidence"] for group in plan["groups"]}, {"low"})

    def test_complete_all_noninvoice_has_no_manifest_or_billable_result(self):
        result = batch(page(1, invoice(is_not_invoice=True)), page(2, invoice(is_not_invoice=True)))
        plan = prepare(result)
        self.assertEqual(plan["state"], "not_invoice")
        self.assertEqual(plan["groups"], [])
        self.assertEqual(plan["invoice_count"], 0)
        self.assertFalse(plan["has_billable_result"])
        self.assertEqual(len(plan["raw_pages"]), 2)

    def test_mixed_noninvoice_evidence_follows_existing_grouping(self):
        result = batch(page(1, invoice(invoice_number="A", total_amount="107")),
                       page(2, invoice(is_not_invoice=True, notes="cover/continuation")))
        plan = prepare(result)
        self.assertEqual(plan["state"], "ready")
        self.assertEqual(plan["groups"][0]["source_page_indices"], [1, 2])
        self.assertTrue(plan["groups"][0]["pages"][1]["fields"]["is_not_invoice"])

    def test_noninvoice_parent_or_sibling_does_not_erase_same_page_invoice(self):
        for values in (
            {"is_not_invoice": True, "additional_invoices": [{"invoice_number": "B", "total_amount": "107"}]},
            {"invoice_number": "B", "total_amount": "107", "additional_invoices": [{"is_not_invoice": True}]},
        ):
            plan = prepare(batch(page(1, invoice(**values))))
            with self.subTest(values=values):
                self.assertEqual(plan["state"], "ready")
                self.assertTrue(plan["has_billable_result"])
                self.assertEqual(plan["physical_page_count"], 1)
                self.assertEqual(plan["invoice_count"], 1)
                self.assertEqual(plan["groups"][0]["source_page_indices"], [1, 1])
                self.assertEqual(plan["groups"][0]["pages"][0]["fields"]["invoice_number"], "B")

    def test_missing_duplicate_out_of_range_and_reported_counts_cannot_seal(self):
        for result, expected, detail in [
            (batch(page(1), page_count=2), 2, ("missing_pages", [2])),
            (batch(page(1), page(1)), 2, ("duplicate_pages", [1])),
            (batch(page(1), page(3)), 2, ("unexpected_pages", [3])),
            (batch(page(1), page_count=2), 1, ("reported_page_count", 2)),
            (batch(page_count=0), 1, ("missing_pages", [1])),
        ]:
            with self.subTest(detail=detail), self.assertRaises(OcrResultError) as caught:
                prepare(result, expected_page_count=expected)
            self.assertEqual(caught.exception.code, "ocr.result_incomplete")
            self.assertEqual(caught.exception.details[detail[0]], detail[1])

    def test_early_failure_or_failure_without_retained_result_cannot_seal(self):
        for values in ({"error": "render failed"}, {"layer_chain": ["L1_failed", "L2"]},
                       {"layer_chain": ["L1", "L2_failed"]}, {"layer_chain": ["failed"]},
                       {"layer_chain": ["L1", "L3_failed"]}):
            with self.subTest(values=values), self.assertRaises(OcrResultError) as caught:
                prepare(batch(page(1, **values)))
            self.assertEqual(caught.exception.details["failed_pages"], [1])
        # A failed page is not converted to a free, successful noninvoice result.
        with self.assertRaises(OcrResultError):
            prepare(batch(page(1, invoice(is_not_invoice=True), error="unreadable")))
        with self.assertRaises(OcrResultError):
            prepare(batch(page(1, invoice(), layer_chain=["L1", "L2", "L3_failed"], error="L3 error")))

    def test_schema_valid_empty_pages_cannot_seal_or_be_billable(self):
        for chain in (["text", "L2"], ["text", "L2", "L3"], ["L1", "L2", "L3"], ["ID"]):
            for empty in (invoice(), invoice(invoice_number=" ", total_amount=" ", seller_name=" "),
                          invoice(additional_invoices=[{"is_not_invoice": True}])):
                result = batch(page(1, empty, layer_chain=chain, confidence_band="yellow_confirm"))
                with self.subTest(chain=chain), self.assertRaises(OcrResultError) as caught:
                    prepare(result)
                self.assertEqual(caught.exception.code, "ocr.result_incomplete")
                self.assertEqual(caught.exception.details["failed_pages"], [1])

    def test_one_unrecognized_page_rejects_whole_batch_before_manifest_or_charge(self):
        result = batch(page(1), page(2, invoice(), layer_chain=["text", "L2", "L3"]))
        with self.assertRaises(OcrResultError) as caught:
            prepare(result)
        self.assertEqual(caught.exception.details["failed_pages"], [2])

    def test_empty_parent_with_retained_invoice_sibling_is_not_lost(self):
        result = batch(page(1, invoice(additional_invoices=[{"invoice_number": "B", "total_amount": "107"}])))
        plan = prepare(result)
        self.assertTrue(plan["has_billable_result"])
        self.assertEqual(plan["physical_page_count"], 1)
        self.assertEqual(plan["groups"][0]["pages"][0]["fields"]["invoice_number"], "B")

    def test_retained_l2_fallback_is_a_reviewable_draft_not_automatic_completion(self):
        for stage in ("L3_failed", "L3_quota", "L3_transient", "L3_skipped_budget", "L3_totals_rescue", "IF_failed"):
            result = batch(page(1, layer_chain=["L1", "L2", stage], error="recoverable error"))
            original = result.model_dump(mode="json")
            plan = prepare(result)
            with self.subTest(stage=stage):
                self.assertEqual(plan["state"], "review_required")
                self.assertTrue(plan["requires_review"])
                self.assertEqual(plan["confidence_band"], "needs_review")
                evidence = plan["groups"][0]["pages"][0]
                self.assertEqual(evidence["error"], "recoverable error")
                self.assertTrue(evidence["_needs_manual_review"])
                self.assertEqual(evidence["_confidence_band"], "needs_review")
                self.assertEqual(evidence, plan["groups"][0]["ai_raw"][0])
                self.assertEqual(result.model_dump(mode="json"), original)
        plan = prepare(batch(page(1, layer_chain=["L1", "L2"], error="recoverable error")))
        self.assertEqual(plan["state"], "review_required")

    def test_recoverable_failure_preserves_all_noninvoice_no_charge_rule(self):
        plan = prepare(batch(page(1, invoice(is_not_invoice=True), layer_chain=["L1", "L2", "L3_quota"])))
        self.assertEqual(plan["state"], "not_invoice")
        self.assertEqual(plan["groups"], [])
        self.assertTrue(plan["requires_review"])
        self.assertFalse(plan["has_billable_result"])

    def test_review_needed_is_not_the_same_as_failed_or_missing(self):
        for flags in ({"confidence_band": "needs_review"}, {"needs_manual_review": True}):
            plan = prepare(batch(page(1, **flags)))
            with self.subTest(flags=flags):
                self.assertEqual(plan["state"], "review_required")
                self.assertEqual(plan["confidence_band"], "needs_review")
                self.assertTrue(plan["groups"][0]["pages"][0]["_needs_manual_review"])

    def test_unvalidated_results_missing_scope_and_forbidden_channels_fail(self):
        result = batch(page())
        with self.assertRaisesRegex(OcrResultError, "ocr.result_unvalidated"):
            prepare_history_batch(result.model_dump(), expected_page_count=1, workspace="A", source="manual")
        for count in (None, True, 0, -1, "1"):
            with self.assertRaisesRegex(OcrResultError, "ocr.expected_pages_invalid"):
                prepare(result, expected_page_count=count)
        for scope in (None, "", " "):
            with self.assertRaisesRegex(OcrResultError, "ocr.scope_required"):
                prepare_history_batch(result, expected_page_count=1, workspace=scope, source="manual")
        for source in ("erp_web", "line_erp", "web", None):
            with self.assertRaises(HistoryContractError):
                prepare(result, source=source)


if __name__ == "__main__":
    unittest.main()
