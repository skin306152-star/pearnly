"""转账凭条 / 预扣税凭证录不录由租户选（`contracts.document_policy`，用户 2026-10-07）：纯函数，不连库。"""
from decimal import Decimal
import unittest

from pearnly_cowork.contracts import document_policy as rules
from pearnly_core.express_rules.contracts.history import primary_page_fields
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
    def test_classes_come_from_the_type_or_the_title_flag(self):
        self.assertEqual(rules.document_class(SLIP), "payment_evidence")
        self.assertEqual(rules.document_class(WHT), "withholding_certificate")
        self.assertEqual(rules.document_class(TAX), "")
        self.assertEqual(rules.document_class(None), "")

    def test_the_push_waits_for_a_choice_and_honours_it(self):
        self.assertEqual(rules.push_block(SLIP, {}), "document_policy_needed_payment_evidence")
        self.assertEqual(rules.push_block(SLIP, {"payment_evidence": "skip"}), "document_policy_skip_payment_evidence")
        self.assertIsNone(rules.push_block(SLIP, {"payment_evidence": "enter"}))
        self.assertIsNone(rules.push_block(TAX, {}))
        # 一个选择只管它自己那一类
        self.assertEqual(rules.push_block(WHT, {"payment_evidence": "enter"}),
                         "document_policy_needed_withholding_certificate")

    def test_only_known_classes_and_values_are_kept(self):
        self.assertEqual(rules.normalize({"payment_evidence": "skip", "withholding_certificate": "maybe",
                                          "cancelled": "skip", "other": "enter"}), {"payment_evidence": "skip"})
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


class CertificateFoundFurtherDown(unittest.TestCase):
    """识别引擎全文认出的 50 ทวิ（标题不在前 10 行）照样走租户的选择与参考记录（2026-10-13 测试机）。"""

    def read(self):
        from test_cowork_engine import CERTIFICATE_50_TAWI, NOT_INVOICE, Model, run, text_source
        return run(text_source(CERTIFICATE_50_TAWI), Model([NOT_INVOICE])).pipeline

    def plan(self, policy):
        return prepare_history_batch(self.read(), expected_page_count=1, workspace="W", source="manual",
                                     document_policy=policy)

    def test_skip_keeps_a_reference_record_and_no_invoice(self):
        policy = {"withholding_certificate": "skip"}
        plan = self.plan(policy)
        self.assertEqual((plan["state"], plan["groups"]), ("not_invoice", []))
        kept = rules.skipped_certificates(plan["raw_pages"], policy)
        self.assertEqual([page["fields"]["seller_tax"] for _position, page in kept], ["1990100000029"])

    def test_enter_or_no_choice_is_asked_like_any_certificate(self):
        for policy in (None, {"withholding_certificate": "enter"}):
            plan = self.plan(policy)
            self.assertEqual(len(plan["groups"]), 1, policy)
            fields = plan["groups"][0]["pages"][0]["fields"]
            self.assertEqual(rules.document_class(fields), "withholding_certificate")
        self.assertEqual(rules.push_block(fields, {}), "document_policy_needed_withholding_certificate")


class CertificateReadWithoutText(unittest.TestCase):
    """2026-10-13 测试机两张真 50 ทวิ（扫描 PDF、手机照片）：结果里没有原文，标题规则没跑，回来的只有凭证的样子
    （不是发票、类别空着、读出预扣税额，付款公司在卖方一栏、收款方在买方一栏）。存档这一步按同一条规则认出、照租户的
    选择走；回放取回的是存下的结果 JSON，校验后同样经过这一步。"""

    PAYER = "0105599210021"
    RENT = {"is_not_invoice": True, "document_type": "other", "document_flag": "",
            "invoice_number": "09/2569 เลขที่ 001", "date": "2026-09-02",
            "seller_name": "บริษัท สยามทดสอบ จำกัด", "seller_tax": PAYER,
            "buyer_name": "บริษัท สายสุข พลาซ่า จำกัด", "buyer_tax": "0105599220141", "total_amount": "25000.00",
            "wht_amount": "1250.00"}
    PERSON = {**RENT, "invoice_number": "09/2569 เลขที่ 002", "date": "2026-09-30", "buyer_name": "นายสมศักดิ์ ทดสอบ",
              "buyer_tax": "1990100000029", "total_amount": None, "wht_amount": "180.00"}

    def replayed(self, *reads):
        pages = [PipelinePageResult(page_number=i, invoice=invoice(**values)) for i, values in enumerate(reads, 1)]
        result = PipelineResult(pages=pages, page_count=len(pages), elapsed_ms=1, estimated_cost_thb=Decimal("0"))
        return PipelineResult.model_validate_json(result.model_dump_json(), context={"anchor_year": 2026})

    def plan(self, policy, *reads):
        result = self.replayed(*reads)
        return prepare_history_batch(result, expected_page_count=len(result.pages), workspace="W", source="manual",
                                     document_policy=policy)

    def test_skip_keeps_both_as_references_and_no_record(self):
        policy = {"withholding_certificate": "skip"}
        for read in (self.RENT, self.PERSON):
            plan = self.plan(policy, read)
            self.assertEqual((plan["state"], plan["groups"]), ("not_invoice", []))
            (_position, page), = rules.skipped_certificates(plan["raw_pages"], policy)
            self.assertEqual((page["fields"]["document_flag"], page["fields"]["seller_tax"]),
                             ("withholding_certificate", self.PAYER))
            self.assertIn("document_policy: shape withholding_certificate", page["_validation_warnings"])

    def test_enter_or_no_choice_keeps_a_record_with_the_flag(self):
        for policy, block in ((None, "document_policy_needed_withholding_certificate"),
                              ({"withholding_certificate": "enter"}, None)):
            plan = self.plan(policy, self.PERSON)
            group, = plan["groups"]
            fields = group["pages"][0]["fields"]
            self.assertEqual((bool(fields.get("is_not_invoice")), fields["document_flag"]),
                             (False, "withholding_certificate"))
            self.assertEqual(rules.push_block(fields, rules.normalize(policy)), block)
            self.assertEqual(rules.skipped_certificates(plan["raw_pages"], rules.normalize(policy)), [])

    def test_an_invoice_with_tax_withheld_is_untouched(self):
        bill = {**self.RENT, "is_not_invoice": False, "document_type": "tax_invoice",
                "invoice_number": "SP-RC-2609-045", "seller_tax": "0105599220141", "buyer_tax": self.PAYER}
        plan = self.plan({"withholding_certificate": "skip"}, bill)
        fields = plan["groups"][0]["pages"][0]["fields"]
        self.assertEqual((bool(fields.get("is_not_invoice")), fields["document_flag"]), (False, ""))
        self.assertFalse(any(w.startswith("document_policy:") for w in plan["raw_pages"][0]["_validation_warnings"]))
        # 和票扫在一起、选了不录入的凭证：票成一张记录，凭证挂上作证据并存参考记录
        plan = self.plan({"withholding_certificate": "skip"}, bill, self.RENT)
        self.assertEqual([g["pages"][0]["fields"]["invoice_number"] for g in plan["groups"]], ["SP-RC-2609-045"])
        self.assertEqual(len(rules.skipped_certificates(plan["raw_pages"], {"withholding_certificate": "skip"})), 1)


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
        mixed = group_invoices([self.page(0, document_flag="withholding_certificate"), self.page(1)])
        self.assertEqual([g["invoice_fields"]["document_flag"] for g in mixed], [""])
        same = group_invoices([self.page(0, document_flag="withholding_certificate"),
                               self.page(1, document_flag="withholding_certificate")])
        self.assertEqual([g["invoice_fields"]["document_flag"] for g in same], ["withholding_certificate"])


if __name__ == "__main__":
    unittest.main()
