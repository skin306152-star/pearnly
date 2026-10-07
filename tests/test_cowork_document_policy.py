"""转账凭条 / 预扣税凭证 / 还款收据录不录、疑点票（`contracts.document_policy`，用户 2026-10-07）：纯函数，不连库。"""
from decimal import Decimal
import unittest

from pearnly_cowork.contracts import document_policy as rules
from pearnly_cowork.contracts.history import primary_page_fields
from pearnly_cowork.ocr.grouping import group_invoices
from pearnly_cowork.ocr.results import prepare_history_batch
from pearnly_cowork.ocr.schema.invoice import validate_invoice
from pearnly_cowork.ocr.schema.results import PipelinePageResult, PipelineResult

TAX = {"document_type": "tax_invoice", "invoice_number": "IV1", "seller_name": "Seller", "total_amount": "107"}
SLIP = {"document_type": "payment_evidence", "invoice_number": "TX9", "total_amount": "500"}
WHT = {"document_type": "other", "document_flag": "withholding_certificate", "total_amount": "30"}


def invoice(**values):
    return validate_invoice(values, anchor_year=2026)


def prepare(*reads, policy=None):
    pages = [PipelinePageResult(page_number=i, invoice=invoice(**values)) for i, values in enumerate(reads, 1)]
    result = PipelineResult(pages=pages, page_count=len(pages), elapsed_ms=1, estimated_cost_thb=Decimal("0"))
    return prepare_history_batch(result, expected_page_count=len(pages), workspace="W", source="manual",
                                 document_policy=policy)


class Rules(unittest.TestCase):
    def test_classes_come_from_the_type_or_the_flag(self):
        self.assertEqual(rules.document_class(SLIP), "payment_evidence")
        self.assertEqual(rules.document_class(WHT), "withholding_certificate")
        self.assertEqual(rules.document_class({"document_flag": "loan_repayment"}), "loan_repayment")
        self.assertEqual(rules.document_class(TAX), "")
        self.assertEqual(rules.document_class({**TAX, "document_flag": "cancelled"}), "")
        self.assertEqual(rules.suspect_flag({**TAX, "document_flag": "cancelled"}), "cancelled")
        self.assertEqual(rules.document_class(None), "")

    def test_the_push_waits_for_a_choice_honours_it_and_holds_suspects_until_released(self):
        self.assertEqual(rules.push_block(SLIP, {}), "document_policy_needed_payment_evidence")
        self.assertEqual(rules.push_block(SLIP, {"payment_evidence": "skip"}), "document_policy_skip_payment_evidence")
        self.assertIsNone(rules.push_block(SLIP, {"payment_evidence": "enter"}))
        self.assertIsNone(rules.push_block(TAX, {}))
        held = {**TAX, "document_flag": "billing_notice"}
        self.assertEqual(rules.push_block(held, {}), "document_suspect_billing_notice")
        self.assertIsNone(rules.push_block(held, {}, release_suspect=True))
        # 一个选择只管它自己那一类
        self.assertEqual(rules.push_block(WHT, {"payment_evidence": "enter"}),
                         "document_policy_needed_withholding_certificate")

    def test_only_known_classes_and_values_are_kept(self):
        self.assertEqual(rules.normalize({"payment_evidence": "skip", "loan_repayment": "maybe", "cancelled": "skip",
                                          "other": "enter"}), {"payment_evidence": "skip"})
        self.assertEqual(rules.normalize("not a mapping"), {})

    def test_an_unknown_flag_from_the_model_is_dropped(self):
        self.assertEqual(invoice(**TAX, document_flag="something_else").document_flag, "")
        self.assertEqual(invoice(**TAX, document_flag=None).document_flag, "")
        self.assertEqual(invoice(**WHT).document_flag, "withholding_certificate")


class SavingFollowsTheChoice(unittest.TestCase):
    def test_skip_saves_the_class_as_a_non_invoice(self):
        plan = prepare(SLIP, policy={"payment_evidence": "skip"})
        self.assertEqual((plan["state"], plan["groups"]), ("not_invoice", []))
        self.assertIn("document_policy: skip payment_evidence", plan["raw_pages"][0]["_validation_warnings"])

    def test_enter_or_no_choice_keeps_it_and_undoes_a_title_rejection(self):
        for policy in (None, {"withholding_certificate": "enter"}):
            plan = prepare({**WHT, "is_not_invoice": True}, policy=policy)
            self.assertEqual(len(plan["groups"]), 1, policy)
        self.assertEqual(len(prepare(SLIP)["groups"]), 1)

    def test_a_mixed_file_keeps_the_invoice_when_the_slip_is_skipped(self):
        plan = prepare(TAX, SLIP, policy={"payment_evidence": "skip"})
        self.assertEqual([g["pages"][0]["fields"]["invoice_number"] for g in plan["groups"]], ["IV1"])

    def test_ordinary_reads_are_untouched(self):
        plan = prepare(TAX, policy={"payment_evidence": "skip"})
        self.assertFalse(any(w.startswith("document_policy:") for p in plan["raw_pages"]
                             for w in p.get("_validation_warnings") or []))


class Evidence(unittest.TestCase):
    def test_a_non_invoice_page_with_its_own_number_and_total_is_evidence_not_a_record(self):
        plan = prepare(TAX, {"document_type": "other", "invoice_number": "IV1 to IV9", "total_amount": "900",
                             "is_not_invoice": True})  # 清单续页：带自己的「单号」和合计
        self.assertEqual(len(plan["groups"]), 1)
        self.assertEqual(plan["groups"][0]["source_page_indices"], [1, 2])

    def test_evidence_never_becomes_the_primary_page(self):
        copy = {"is_copy": True, "fields": {**TAX}}
        slip = {"is_copy": False, "fields": {**SLIP, "is_not_invoice": True}}
        self.assertEqual(primary_page_fields([copy, slip])["invoice_number"], "IV1")
        self.assertEqual(primary_page_fields([slip])["invoice_number"], "TX9")  # 整张都是非发票页：照旧


class Grouping(unittest.TestCase):
    def page(self, index, **fields):
        return {"page_index": index, "fields": {**TAX, **fields}, "is_copy": False}

    def test_a_flag_counts_only_when_every_page_of_the_invoice_has_it(self):
        mixed = group_invoices([self.page(0, document_flag="billing_notice"), self.page(1)])
        self.assertEqual([g["invoice_fields"]["document_flag"] for g in mixed], [""])
        same = group_invoices([self.page(0, document_flag="cancelled"), self.page(1, document_flag="cancelled")])
        self.assertEqual([g["invoice_fields"]["document_flag"] for g in same], ["cancelled"])


if __name__ == "__main__":
    unittest.main()
