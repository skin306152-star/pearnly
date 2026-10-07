"""Pure, deterministic engine scenarios. No credentials, HTTP, Site, or paid OCR."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import threading
import unittest

from pearnly_cowork.ocr.engine import (
    EngineConfig, EngineError, Frame, ModelResponse, VisionResponse, run_invoice,
)
from pearnly_cowork.ocr.engine.fewshot import fewshot_for_text
from pearnly_cowork.ocr.engine.contracts import ProviderFailure
from pearnly_cowork.ocr.engine.errors import ContentError as ContentErrorBase
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.engine.patterns import InvoicePatternMemory
from pearnly_cowork.ocr.engine.runner import page_text
from pearnly_cowork.ocr.schema import Page

CONFIG = EngineConfig("lite", "lite", "pro")
TODAY = date(2026, 9, 21)
GOOD = {"document_type": "receipt", "invoice_number": "IV69/100", "subtotal": "100",
        "vat": "7", "total_amount": "107", "date": "2026-09-21"}
TEXT = "Receipt IV69/100 subtotal 100 VAT 7 total 107 2026-09-21"


class Source:
    def __init__(self, count=1, *, text=False):
        self.page_count = count
        self.text_pages = [Page(page_number=i, width=1, height=1, blocks=[], full_text=TEXT, avg_confidence=1)
                           for i in range(1, count + 1)] if text else None
        self.frames = []

    def frame(self, number):
        self.frames.append(number)
        return Frame(b"\x89PNG\r\n\x1a\n" + bytes([number]), "image/png")


class Model:
    def __init__(self, outputs=None):
        self.outputs = list(outputs or [])
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0) if self.outputs else GOOD
        if isinstance(output, Exception):
            raise output
        if isinstance(output, ModelResponse):
            return output
        text = output if isinstance(output, str) else json.dumps(output)
        return ModelResponse(text, {"original": text}, 10, 20, request.model, 3,
                             checkpoint_key=f"{request.stage}:{request.page_number}:{request.attempt}")


def vision(frame, *, stage, page_number):
    return VisionResponse(Page(page_number=page_number, width=1, height=1, blocks=[], full_text=TEXT, avg_confidence=1),
                          {"text": TEXT}, 2, f"{stage}:{page_number}")


def run(source=None, model=None, **kwargs):
    return run_invoice(source or Source(), model or Model(), kwargs.pop("vision_call", vision),
                       config=kwargs.pop("config", CONFIG), anchor_year=2026, today=TODAY, **kwargs)


class EngineTests(unittest.TestCase):
    def test_direct_is_confirm_first_and_preserves_raw(self):
        model = Model()
        result = run(model=model)
        page = result.pipeline.pages[0]
        self.assertEqual(page.layer_chain, ["ID"])
        self.assertEqual(page.confidence_band, "yellow_confirm")
        self.assertEqual(result.calls[0].raw, {"original": json.dumps(GOOD)})
        self.assertEqual((result.calls[0].input_tokens, result.calls[0].output_tokens), (10, 20))
        self.assertEqual(model.requests[0].max_output_tokens, 8192)

    def test_direct_invalid_json_retry_same_prompt_new_attempt(self):
        model = Model(["broken", GOOD])
        result = run(model=model)
        self.assertEqual([r.attempt for r in model.requests], [0, 1])
        self.assertEqual(model.requests[0].prompt, model.requests[1].prompt)
        self.assertEqual(result.pipeline.pages[0].layer2_input_tokens, 20)

    def test_a_single_invoice_wrapped_in_an_array_is_read_without_retry(self):
        # 经济档模型偶尔把唯一一张票包成 [{...}]（2026-09-28 压测实测），拆开即可，不算格式错误。
        model = Model([[GOOD]])
        result = run(model=model)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result.pipeline.pages[0].invoice.invoice_number, "IV69/100")
        # 一张照片几张票：模型写成对象数组（压测里二到四张油票每次都这样回），第一张作主票、其余进附加票，不重试。
        second, third = {**GOOD, "invoice_number": "IV69/101"}, {**GOOD, "invoice_number": "IV69/102"}
        model = Model([[GOOD, {**second, "additional_invoices": [third]}]])
        invoice = run(model=model).pipeline.pages[0].invoice
        self.assertEqual(len(model.requests), 1)
        self.assertEqual([invoice.invoice_number] + [extra.invoice_number for extra in invoice.additional_invoices],
                         ["IV69/100", "IV69/101", "IV69/102"])
        self.assertTrue(all(extra.additional_invoices == [] for extra in invoice.additional_invoices))
        model = Model([[1], "[]", GOOD])
        run(model=model)
        self.assertEqual([r.attempt for r in model.requests][:2], [0, 1])  # 不是对象的数组仍按格式错误重试

    def test_one_unreadable_page_does_not_fail_a_multi_page_file(self):
        # 2026-09-28 压测：15 页 PDF 其中一页模型写满上限，直读、复核、带原文再读都没结果 → 以前整份失败
        garbage = "{\"invoice_number\": \"IV69/0" + "0" * 50
        model = Model([GOOD, garbage, garbage, garbage, garbage, GOOD])
        pages = run(source=Source(3), model=model).pipeline.pages
        self.assertEqual([p.layer_chain[-1] for p in pages][1], "PAGE_UNREAD")
        self.assertTrue(pages[1].needs_manual_review)
        self.assertTrue(pages[1].validation_warnings[0].startswith("page_unread: page 2"))
        self.assertEqual((pages[0].invoice.invoice_number, pages[2].invoice.invoice_number), ("IV69/100", "IV69/100"))
        with self.assertRaises(ContentErrorBase):  # 只有一页的文件照旧报错
            run(source=Source(1), model=Model([garbage] * 6))

    def test_only_the_failed_page_is_rechecked_and_all_usage_is_kept(self):
        # ADR-013：一页没过闸只复核那一页，不再把整份文件改走 Vision 重读
        model, source = Model([GOOD, {"total_amount": ""}, GOOD]), Source(2)
        result = run(source, model)
        self.assertEqual(source.frames, [1, 2])
        self.assertEqual([r.stage for r in model.requests], ["direct", "direct", "recheck"])
        self.assertEqual(result.pipeline.engine, "image_checked")
        self.assertEqual([p.layer_chain for p in result.pipeline.pages], [["ID"], ["ID", "RC"]])
        self.assertNotIn("direct_fallback", result.pipeline.pages[0].extraction_audit)
        self.assertEqual(result.pipeline.pages[1].extraction_audit["direct_fallback"],
                         {"page_number": 2, "code": "ocr.direct_gate_failed", "detail": "total_amount missing"})
        self.assertEqual(len(result.calls), 3)
        self.assertEqual(result.pipeline.pages[1].layer2_input_tokens, 20)

    def test_recheck_carries_the_first_read_and_why_it_failed(self):
        model = Model([{**GOOD, "total_amount": "170"}, GOOD])
        run(model=model)
        recheck = model.requests[1]
        self.assertEqual((recheck.thinking, recheck.images, recheck.attempt), ("", model.requests[0].images, 0))
        self.assertIn('"total_amount": "170"', recheck.prompt)
        self.assertIn("It failed these checks:", recheck.prompt)

    def test_recheck_keeps_fields_it_left_empty(self):
        # 实测：复核把金额改对的同时丢了买方 —— 空着的字段沿用第一次读到的
        first = {**GOOD, "total_amount": "170", "buyer_name": "ACME", "buyer_tax": "0105567178203"}
        model = Model([first, GOOD])
        page = run(model=model).pipeline.pages[0]
        self.assertEqual((page.invoice.total_amount, page.invoice.buyer_name, page.invoice.buyer_tax),
                         ("107", "ACME", "0105567178203"))

    def test_image_reads_use_the_compact_prompt_without_source_refs(self):
        model = Model([{**GOOD, "total_amount": "170"}, {**GOOD, "total_amount": "170"}, GOOD])
        run(model=model)
        for request in model.requests:
            self.assertNotIn("source_refs", request.prompt.split("A first reading")[0])
            # 上限随 2026-09-28 加的第 14 条（会计盖的凭证号章不是票号）放宽到 4800，随 2026-10-07 第 15 条
            # （录不录的类别 / 疑点标记，约 100 个输入令牌）放宽到 5300：仍远小于 L2 的 1.2 万。
            self.assertLess(len(request.prompt.split("A first reading")[0].split("An OCR transcript")[0]), 5300)

    def test_bills_are_purchases_only_when_the_direct_model_is_3_8(self):
        # 2026-10-02 eval7b：3.8 把电费通知单、请款单判成非票据；补的那句只随 3.8 走，flash-lite 的提示词一字不变
        from pearnly_cowork.ocr.engine.prompts import BILLS_ARE_PURCHASES, IMAGE_SYSTEM, image_system
        self.assertEqual(image_system("gemini-3.1-flash-lite"), IMAGE_SYSTEM)
        self.assertEqual(image_system("gemini-3.8-flash").count(BILLS_ARE_PURCHASES), 1)
        self.assertLess(len(image_system("gemini-3.8-flash")), 5700)
        wrong = {**GOOD, "total_amount": "170"}
        for model, expected in (("gemini-3.8-flash", True), ("lite", False)):
            reader = Model([wrong, wrong, GOOD])
            run(model=reader, config=replace(CONFIG, direct_model=model))
            self.assertEqual([r.stage for r in reader.requests], ["direct", "recheck", "ocr_read"])
            self.assertEqual([BILLS_ARE_PURCHASES in r.prompt for r in reader.requests], [expected] * 3)

    def test_a_full_tax_invoice_is_checked_against_the_transcript(self):
        # 2026-09-27 大语料：直读把单号读错一个字符、金额全对 —— 完整税票过了闸也拿 Vision 原文核对单号；
        # 2026-09-28 压测：原文也会读错，差一个字符时不替换，两种写法交复核、这一页转复核
        wrong = {**GOOD, "document_type": "tax_invoice", "invoice_number": "IV69/108"}
        page = run(model=Model([wrong])).pipeline.pages[0]
        self.assertEqual((page.layer_chain, page.invoice.invoice_number), (["ID", "L1"], "IV69/108"))
        self.assertIn("invoice_number_alternative: IV69/108|IV69/100", page.validation_warnings)
        self.assertTrue(page.needs_manual_review)
        right = run(model=Model([{**GOOD, "document_type": "tax_invoice"}])).pipeline.pages[0]
        self.assertFalse(any(w.startswith("invoice_number_alternative") for w in right.validation_warnings))
        receipt = run(model=Model([{**GOOD, "invoice_number": "IV69/108"}])).pipeline.pages[0]
        self.assertEqual((receipt.layer_chain, receipt.invoice.invoice_number), (["ID"], "IV69/108"))  # 小票不多花

    def test_every_invoice_on_one_image_is_checked_against_the_transcript(self):
        # 2026-09-28 复测：一张图四张票，第四张 000098 读成 000096，只核第一张时漏标
        full = {**GOOD, "document_type": "tax_invoice", "invoice_number": "IV69/100"}
        seen = {**full, "additional_invoices": []}
        unseen = {**full, "invoice_number": "ZZ-777", "additional_invoices": []}
        page = run(model=Model([{**full, "additional_invoices": [seen, unseen]}])).pipeline.pages[0]
        self.assertEqual([w for w in page.validation_warnings if w.startswith("invoice_number_")],
                         ["invoice_number_unseen: ZZ-777"])
        self.assertTrue(page.needs_manual_review)

    def test_a_non_invoice_title_in_the_transcript_marks_the_page_not_invoice(self):
        # 2026-10-02 两批金标重放：预扣税凭证、税报表被读成完整税票 —— 原文票头印着这些标题就按「不是发票」处理
        def titled(text):
            def call(frame, *, stage, page_number):
                return VisionResponse(Page(page_number=page_number, width=1, height=1, blocks=[], full_text=text,
                                           avg_confidence=1), {"text": text}, 2, f"{stage}:{page_number}")
            return call

        full = {**GOOD, "document_type": "tax_invoice", "additional_invoices": [{**GOOD, "additional_invoices": []}]}
        page = run(model=Model([full]), vision_call=titled("หนังสือรับรองการหักภาษี ณ ที่จ่าย\n" + TEXT)).pipeline.pages[0]
        self.assertTrue(page.invoice.is_not_invoice)
        self.assertEqual(page.invoice.additional_invoices, [])
        self.assertEqual(page.extraction_audit, {"not_invoice_title": "withholding_certificate"})
        self.assertEqual(page.invoice.document_flag, "withholding_certificate")  # 录不录由租户的选择定（存档时）
        self.assertFalse(any(w.startswith("invoice_number_") for w in page.validation_warnings))
        # 真票页脚写着「请寄回预扣税凭证」：只看票头，不判
        footer = "\n".join([TEXT] + ["line"] * 12 + ["กรุณาส่งหนังสือรับรองการหักภาษี ณ ที่จ่าย"])
        kept = run(model=Model([full]), vision_call=titled(footer)).pipeline.pages[0]
        self.assertFalse(kept.invoice.is_not_invoice)
        self.assertEqual(kept.extraction_audit, {})
        # 没过闸、带原文再读的路线同样判
        wrong = {**GOOD, "total_amount": "170"}
        reread = run(model=Model([wrong, wrong, GOOD]), vision_call=titled("รายงานภาษีขาย\n" + TEXT)).pipeline.pages[0]
        self.assertTrue(reread.invoice.is_not_invoice)
        self.assertEqual(reread.extraction_audit["not_invoice_title"], "tax_report")

    def test_a_guessed_head_office_branch_is_cleared_only_against_vision_text(self):
        # 2026-10-02 两批金标重放：原文里没有任何分店字样，模型填的 00000 是猜的；PDF 文字层常有乱码，不据此清
        full = {**GOOD, "document_type": "tax_invoice", "seller_branch": "00000"}
        self.assertEqual(run(model=Model([full])).pipeline.pages[0].invoice.seller_branch, "")
        self.assertEqual(run(Source(text=True), Model([full])).pipeline.pages[0].invoice.seller_branch, "00000")

    def test_a_failed_recheck_reads_image_and_transcript_together(self):
        wrong = {**GOOD, "total_amount": "170"}
        model = Model([wrong, wrong, GOOD])
        result = run(model=model)
        page = result.pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "recheck", "ocr_read"])
        ocr_read = model.requests[2]
        self.assertEqual((ocr_read.thinking, ocr_read.max_output_tokens), ("LOW", 8192))
        self.assertTrue(ocr_read.images)
        self.assertIn("An OCR transcript of this image follows.", ocr_read.prompt)
        self.assertTrue(ocr_read.prompt.endswith(TEXT))
        self.assertEqual(page.layer_chain, ["ID", "RC", "L1", "L2"])
        self.assertEqual(page.invoice.total_amount, "107")
        self.assertEqual(page.extraction_audit["direct_fallback"]["code"], "ocr.direct_gate_failed")
        self.assertEqual([c.stage for c in result.calls], ["direct", "recheck", "layer1", "ocr_read"])

    def test_unreadable_direct_skips_the_recheck(self):
        model = Model(["broken", "broken", GOOD])
        page = run(model=model).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "direct", "ocr_read"])
        self.assertEqual(page.layer_chain, ["ID", "L1", "L2"])

    def test_a_runaway_image_read_is_retried_once_with_other_settings(self):
        # 2026-09-28 压测：模型在单号后复读一长串 0 写满上限 —— 原样重读照样写满，换参数原地重读一次
        runaway = ModelResponse('{"invoice_number": "IV69/0' + "0" * 40, {}, 4, 8192, "lite", finish_reason="MAX_TOKENS")
        model = Model([runaway, GOOD])
        page = run(model=model).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "direct"])
        retry = model.requests[1]
        self.assertEqual((retry.temperature, retry.max_output_tokens), (0.4, 4096))
        self.assertIn("never repeat a digit sequence", retry.prompt)
        self.assertEqual((page.layer_chain, page.invoice.invoice_number), (["ID"], "IV69/100"))

    def test_a_runaway_that_repeats_goes_on_to_the_transcript_read(self):
        runaway = ModelResponse('{"invoice_number": "1', {}, 4, 8192, "lite", finish_reason="MAX_TOKENS")
        model = Model([runaway, runaway, GOOD])
        page = run(model=model).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "direct", "ocr_read"])
        self.assertEqual(page.extraction_audit["direct_fallback"]["detail"], "output hit the token limit")

    def test_unreadable_transcript_read_keeps_the_image_read_for_review(self):
        wrong = {**GOOD, "total_amount": "170"}
        model = Model([wrong, wrong, "broken"])
        page = run(model=model, config=replace(CONFIG, enable_layer3=False)).pipeline.pages[0]
        self.assertEqual(page.layer_chain, ["ID", "RC", "L1", "L2_unread"])
        self.assertEqual(page.invoice.total_amount, "170")
        self.assertTrue(page.needs_manual_review)
        self.assertIn("ocr_read: ocr.response_json_invalid", page.validation_warnings)
        from pearnly_cowork.ocr.results import _require_complete
        result = run(model=Model([wrong, wrong, "broken"]), config=replace(CONFIG, enable_layer3=False)).pipeline
        self.assertEqual(_require_complete(result, 1), {1})  # 待复核，不是识别失败

    def test_without_layer3_the_remaining_problems_are_flagged(self):
        wrong = {**GOOD, "total_amount": "170"}
        model = Model([wrong, wrong, wrong])
        page = run(model=model, config=replace(CONFIG, enable_layer3=False)).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "recheck", "ocr_read"])
        self.assertTrue(page.needs_manual_review)
        self.assertEqual(page.confidence_band, "needs_review")
        text = run(Source(text=True), Model([{**GOOD, "invoice_number": "WRONG"}]),
                   config=replace(CONFIG, enable_layer3=False)).pipeline.pages[0]
        self.assertEqual(text.layer_chain, ["text", "L2"])
        self.assertTrue(text.needs_manual_review)

    def test_layer3_is_off_unless_the_site_turns_it_on(self):
        environment = {"OCR_LLM_BACKEND": "vertex"}
        self.assertFalse(config_from_environment(environment, invoice_policy(environment)).enable_layer3)
        environment["OCR_LAYER3_ENABLED"] = "1"
        self.assertTrue(config_from_environment(environment, invoice_policy(environment)).enable_layer3)
        environment["OCR_LAYER3_ENABLED"] = "maybe"
        with self.assertRaises(EngineError):
            config_from_environment(environment, invoice_policy(environment))

    def test_the_site_picks_the_image_reading_model(self):
        # 2026-10-02 用户同意扫描件 / 照片看图直读改用 3.8-flash：站点配置 OCR_DIRECT_MODEL，只认登记的模型，文字转读照旧
        environment = {"OCR_LLM_BACKEND": "vertex"}
        default = config_from_environment(environment, invoice_policy(environment))
        environment["OCR_DIRECT_MODEL"] = "gemini-3.8-flash"
        chosen = config_from_environment(environment, invoice_policy(environment))
        self.assertEqual((chosen.direct_model, chosen.layer2_model), ("gemini-3.8-flash", default.layer2_model))
        self.assertEqual(default.direct_model, default.layer2_model)
        environment["OCR_DIRECT_MODEL"] = "gemini-9-ultra"
        with self.assertRaises(EngineError):
            config_from_environment(environment, invoice_policy(environment))

    def test_a_38_read_google_cannot_answer_is_read_with_flash_lite_instead(self):
        # 2026-10-02 m5：3.8 各入口都超时 / 都限流时不判结果不明，改用 flash-lite 读完；提示词用 flash-lite 现行的那份
        from pearnly_core.execution.errors import Deferred, Uncertain
        from pearnly_cowork.ocr.engine.prompts import BILLS_ARE_PURCHASES

        pro = replace(CONFIG, direct_model="gemini-3.8-flash", layer2_model="gemini-3.1-flash-lite")
        for failure in (Uncertain("no answer from any entry"), Deferred("provider_429", 5)):
            model = Model([failure, GOOD])
            result = run(model=model, config=pro)
            page = result.pipeline.pages[0]
            self.assertEqual([r.model for r in model.requests], ["gemini-3.8-flash", "gemini-3.1-flash-lite"])
            self.assertIn(BILLS_ARE_PURCHASES, model.requests[0].prompt)
            self.assertNotIn(BILLS_ARE_PURCHASES, model.requests[1].prompt)
            self.assertEqual(page.invoice.invoice_number, "IV69/100")
            self.assertEqual(page.extraction_audit["model_downgrade"],
                             [{"stage": "direct", "from": "gemini-3.8-flash", "to": "gemini-3.1-flash-lite",
                               "reason": "provider_outcome_unknown" if isinstance(failure, Uncertain) else "provider_429"}])

    def test_only_google_side_failures_of_a_38_read_fall_back(self):
        from pearnly_core.execution.errors import Deferred, Uncertain

        pro = replace(CONFIG, direct_model="gemini-3.8-flash", layer2_model="gemini-3.1-flash-lite")
        for config, outputs, error in ((pro, [Deferred("capacity", 3)], Deferred),            # 自家额度：换模型解决不了
                                       (CONFIG, [Uncertain("x")], Uncertain),                 # 本来就是 flash-lite：不降
                                       (pro, [Uncertain("x"), Uncertain("y")], Uncertain)):   # flash-lite 也失败：照原样抛
            with self.assertRaises(error):
                run(model=Model(outputs), config=config)

    def test_text_path_has_no_render_or_vision_and_injects_fewshot_only_l2(self):
        source, model, seen = Source(text=True), Model(), []
        result = run(source, model, fewshot_selector=lambda text: seen.append(text) or "HINT\n")
        self.assertEqual(source.frames, [])
        self.assertEqual(result.pipeline.pages[0].layer_chain, ["text", "L2"])
        self.assertEqual(seen, [TEXT])
        self.assertIn("HINT\nExtract from", model.requests[0].prompt)
        run(fewshot_selector=lambda _: self.fail("direct must not inject fewshot"))

    def test_max_tokens_valid_json_is_content_failure(self):
        first = ModelResponse(json.dumps(GOOD), {"finish": "MAX_TOKENS"}, 4, 5, "lite",
                              finish_reason="MAX_TOKENS")
        model = Model([first, GOOD])
        result = run(Source(text=True), model)
        self.assertEqual(len(result.calls), 2)
        self.assertIn("previous response was truncated", model.requests[1].prompt)

    def test_completed_l2_schema_error_is_not_paid_retry_or_l3(self):
        model = Model([{**GOOD, "items": "invalid"}])
        with self.assertRaisesRegex(EngineError, "ocr.invoice_schema_invalid"):
            run(Source(text=True), model)
        self.assertEqual(len(model.requests), 1)

    def test_l2_content_budget_exhaustion_stops(self):
        model = Model(["bad", "bad"])
        with self.assertRaisesRegex(EngineError, "ocr.response_json_invalid"):
            run(Source(text=True), model)
        self.assertEqual([r.stage for r in model.requests], ["layer2", "layer2"])

    def test_layer3_lazy_render_and_source_prompt(self):
        source, model = Source(text=True), Model([{**GOOD, "invoice_number": "WRONG"}, GOOD])
        result = run(source, model)
        self.assertEqual(source.frames, [1])
        self.assertEqual([r.stage for r in model.requests], ["layer2", "layer3"])
        self.assertIn("INDEPENDENT RE-READ", model.requests[1].prompt)
        self.assertEqual(result.pipeline.pages[0].layer_chain, ["text", "L2", "L3"])

    def test_totals_rescue_keeps_original_raw_and_forces_review(self):
        wrong = {**GOOD, "vat": "9", "total_amount": "109", "invoice_number": "WRONG"}
        model = Model([wrong, "bad", "bad", {"subtotal": "100", "vat": "7", "total_amount": "107"}])
        result = run(Source(text=True), model)
        page = result.pipeline.pages[0]
        self.assertEqual(page.layer_chain, ["text", "L2", "L3_totals_rescue"])
        self.assertTrue(page.needs_manual_review)
        self.assertEqual(page.invoice.total_amount, "107")
        self.assertIn('"total_amount": "109"', result.calls[0].text)
        self.assertTrue(any(w.startswith("totals_rescued:") for w in page.validation_warnings))
        self.assertEqual(sum(c.input_tokens for c in result.calls), 40)
        self.assertEqual(model.requests[-1].max_output_tokens, 512)

    def test_unusable_rescue_keeps_l2_review(self):
        model = Model([{**GOOD, "invoice_number": "WRONG"}, "bad", "bad", "bad"])
        result = run(Source(text=True), model)
        self.assertEqual(result.pipeline.pages[0].layer_chain[-1], "L3_failed")
        self.assertTrue(result.pipeline.pages[0].needs_manual_review)

    def test_no_fallback_policy_does_not_rescue(self):
        model = Model([{**GOOD, "invoice_number": "WRONG"}, "bad", "bad"])
        with self.assertRaises(EngineError):
            run(Source(text=True), model, config=replace(CONFIG, fallback_to_layer2=False))
        self.assertEqual(len(model.requests), 3)

    def test_infrastructure_exceptions_propagate_at_every_stage(self):
        class Deferred(Exception): pass
        class Uncertain(Exception): pass
        for exception in (Deferred("wait"), Uncertain("unknown")):
            for outputs, source, expected in (
                ([exception], Source(), 1),
                ([exception], Source(text=True), 1),
                ([{**GOOD, "invoice_number": "WRONG"}, exception], Source(text=True), 2),
                ([{**GOOD, "invoice_number": "WRONG"}, "bad", "bad", exception], Source(text=True), 4),
            ):
                model = Model(outputs)
                with self.assertRaises(type(exception)) as caught:
                    run(source, model)
                self.assertIs(caught.exception, exception)
                self.assertEqual(len(model.requests), expected)
            with self.assertRaises(type(exception)):
                run(config=replace(CONFIG, direct_enabled=False),
                    vision_call=lambda *a, **k: (_ for _ in ()).throw(exception))

    def test_discount_inference_preserves_first_result(self):
        original = {**GOOD, "total_amount": "102"}
        result = run(model=Model([original]))
        page = result.pipeline.pages[0]
        self.assertEqual(page.invoice.discount, "5.00")
        self.assertTrue(page.needs_manual_review)
        self.assertNotIn('"discount"', result.calls[0].text)

    def test_credit_note_and_old_date_force_direct_review(self):
        # 2019：超过 5 年但不是差约 10 年（差约 10 年先带提示复核一次，见 test_cowork_reread_checks）
        for extra in ({"document_type": "credit_note"}, {"date": "2019-09-21"}):
            self.assertTrue(run(model=Model([{**GOOD, **extra}])).pipeline.pages[0].needs_manual_review)

    def test_missing_receipt_number_is_direct_soft_flag_not_vision(self):
        result = run(model=Model([{**GOOD, "invoice_number": ""}]))
        self.assertEqual(result.pipeline.engine, "image_direct")
        self.assertEqual(result.pipeline.pages[0].final_confidence, .90)

    def test_sibling_invoice_usage_and_physical_page_preserved(self):
        result = run(model=Model([{**GOOD, "additional_invoices": [GOOD]}]))
        self.assertEqual(result.pipeline.page_count, 1)
        self.assertEqual(len(result.pipeline.pages[0].invoice.additional_invoices), 1)
        self.assertEqual(len(result.calls), 1)

    def test_whole_text_override_mismatch_drops_to_direct(self):
        source = Source(2, text=True)
        source.text_pages.pop()
        self.assertEqual(run(source).pipeline.engine, "image_direct")

    def test_invalid_page_link_rejected_before_model(self):
        source, model = Source(text=True), Model()
        source.text_pages[0].page_number = 9
        with self.assertRaisesRegex(EngineError, "ocr.page_association_invalid"):
            run(source, model)
        self.assertFalse(model.requests)

    def test_unsupported_modes_fail_before_callback(self):
        for fields in ({"mode": "max"}, {"provider": "qwen"}, {"double_read": True},
                       {"image_first": True}, {"document_type": "bank_statement"}):
            model = Model()
            with self.assertRaisesRegex(EngineError, "ocr.engine_mode_not_supported"):
                run(model=model, config=replace(CONFIG, **fields))
            self.assertFalse(model.requests)

    def test_blank_text_skips_provider(self):
        source = Source(text=True)
        source.text_pages[0].full_text = "short"
        model = Model()
        result = run(source, model)
        self.assertFalse(model.requests)
        self.assertTrue(result.pipeline.pages[0].invoice.is_not_invoice)

    def test_budget_skips_l3_explicit_review(self):
        model = Model([{**GOOD, "invoice_number": "WRONG"}])
        page = run(Source(text=True), model, config=replace(CONFIG, max_escalations=0)).pipeline.pages[0]
        self.assertEqual(page.layer_chain[-1], "L3_skipped_budget")
        self.assertTrue(page.needs_manual_review)

    def test_fewshot_selection_source_order_dedup_and_limit(self):
        examples = [{"field_name": f"f{i}", "ai_value": str(i), "corrected_value": "fixed"} for i in range(20)]
        text = " ".join(str(1000000000000 + i) for i in range(5))
        block = fewshot_for_text(text, {"1000000000000": examples[:10],
                                       "1000000000001": examples[5:],
                                       "1000000000004": [{"field_name": "EXCLUDED"}]})
        self.assertEqual(block.count("AI 曾输出"), 12)
        self.assertNotIn("EXCLUDED", block)
        self.assertEqual(fewshot_for_text("no tax", {}), "")

    def test_table_text_preserves_numeric_cells(self):
        self.assertEqual(page_text(Page(page_number=1, width=1, height=1, blocks=[], full_text="", avg_confidence=1, table_headers=["a", "b"],
                                        table_rows=[{"a": 3, "b": None}])), "a | b\n3 | None")

    def test_definite_provider_refusal_has_source_fallback(self):
        failure = ProviderFailure("ocr.provider_http_error", http_status=500)
        model = Model([failure, GOOD])
        result = run(model=model)
        self.assertEqual(result.pipeline.engine, "image_checked")
        self.assertEqual([r.stage for r in model.requests], ["direct", "ocr_read"])  # 没读出东西，不复核
        model = Model([{**GOOD, "invoice_number": "WRONG"}, failure])
        page = run(Source(text=True), model).pipeline.pages[0]
        self.assertEqual(page.layer_chain[-1], "L3_transient")
        self.assertTrue(page.needs_manual_review)
        self.assertEqual(len(model.requests), 2)

    def test_provider_auth_is_never_downgraded(self):
        for failure in (ProviderFailure("ocr.provider_auth"),
                        ProviderFailure("other", http_status=403),
                        ProviderFailure("other", provider_code=7)):
            for outputs, source in (([failure], Source()),
                                    ([{**GOOD, "invoice_number": "WRONG"}, failure], Source(text=True))):
                with self.assertRaises(ProviderFailure) as caught:
                    run(source, Model(outputs))
                self.assertIs(caught.exception, failure)

    def test_rescue_numeric_value_cannot_bypass_string_schema(self):
        model = Model([{**GOOD, "invoice_number": "WRONG"}, "bad", "bad", {"total_amount": 107}])
        page = run(Source(text=True), model).pipeline.pages[0]
        self.assertEqual(page.layer_chain[-1], "L3_failed")
        self.assertIsInstance(page.invoice.total_amount, str)

    def test_config_maps_actual_env_knobs(self):
        environment = {"OCR_LLM_BACKEND": "vertex", "OCR_PIPELINE_CONF_THRESHOLD": ".8",
                       "OCR_PIPELINE_AMOUNT_TOL": "0.01", "OCR_CONF_AUTO": ".99", "OCR_CONF_REVIEW": ".8",
                       "OCR_L1_TIMEOUT_SECONDS": "30", "OCR_L2_TIMEOUT_SECONDS": "35", "OCR_L3_TIMEOUT_SECONDS": "12",
                       "OCR_DIRECT_TIMEOUT_SECONDS": "45", "OCR_TOTALS_RESCUE_TIMEOUT_SECONDS": "9"}
        config = config_from_environment(environment, invoice_policy(environment))
        self.assertEqual(config.confidence_threshold, .8)
        self.assertEqual(str(config.amount_tolerance), "0.01")
        self.assertEqual((config.auto_threshold, config.review_threshold), (.99, .8))
        self.assertEqual((config.direct_timeout_s, config.layer2_timeout_s, config.layer3_timeout_s, config.rescue_timeout_s),
                         (45, 35, 12, 9))
        self.assertEqual(config.direct_model, "gemini-3.1-flash-lite")

    def test_config_rejects_unhandled_or_invalid_values(self):
        for values in ({"OCR_TRANSIENT_RETRY": "0"}, {"OCR_TRANSIENT_RETRY": "2"},
                       {"OCR_L1_TIMEOUT_SECONDS": "121"}, {"OCR_IMG_MAX_LONG_EDGE": "-1"},
                       {"OCR_PIPELINE_AMOUNT_TOL": "NaN"}, {"OCR_CONF_REVIEW": "1"},
                       {"OCR_L3_TIMEOUT_SECONDS": ""}, {"OCR_DOUBLE_READ": "1"}):
            environment = {"OCR_LLM_BACKEND": "vertex", **values}
            with self.assertRaises(EngineError):
                config_from_environment(environment, invoice_policy(environment))

    def test_nondefault_thresholds_change_real_decisions(self):
        from test_cowork_invoice_validation import page
        source = Source(text=True)
        source.text_pages[0] = page(TEXT, [("IV69/100", .8)], confidence=1)
        high = run(source, config=replace(CONFIG, confidence_threshold=.85)).pipeline.pages[0]
        low = run(source, config=replace(CONFIG, confidence_threshold=.75)).pipeline.pages[0]
        self.assertEqual((high.confidence_band, low.confidence_band), ("yellow_confirm", "auto"))
        payload = {**GOOD, "total_amount": "107.2"}
        loose = run(model=Model([payload]), config=replace(CONFIG, amount_tolerance=Decimal(".5")))
        tight = run(model=Model([payload, GOOD]), config=replace(CONFIG, amount_tolerance=Decimal(".1")))
        self.assertEqual(loose.pipeline.engine, "image_direct")
        self.assertEqual(tight.pipeline.engine, "image_checked")
        self.assertEqual(tight.pipeline.pages[0].extraction_audit["direct_fallback"]["code"], "ocr.direct_gate_failed")

    def test_enterprise_invoice_uses_same_source_chain(self):
        environment = {"OCR_LLM_BACKEND": "vertex", "OCR_ENGINE_MODE": "enterprise"}
        config = config_from_environment(environment, invoice_policy(environment))
        model = Model()
        self.assertEqual(run(model=model, config=config).pipeline.engine, "image_direct")
        self.assertEqual(model.requests[0].model, "gemini-3.8-flash")

    def test_pattern_memory_source_threshold(self):
        memory = InvoicePatternMemory()
        memory.record("tax", "IV69/1")
        self.assertIsNone(memory.check_anomaly("tax", "IV60/3"))
        memory.record("tax", "IV69/2")
        self.assertIn("2 prior invoices", memory.check_anomaly("tax", "IV60/3"))



class PageModel:
    """按页给固定的返回（各页同时读时调用先后不定，所以不能按调用顺序给）。"""
    def __init__(self, delay=0.0):
        self.delay, self.lock = delay, threading.Lock()
        self.requests = []

    def __call__(self, request):
        import time
        time.sleep(self.delay)
        with self.lock:
            self.requests.append((request.page_number, request.stage, request.attempt))
        page = request.page_number
        if page == 3:                       # 第 3 页：模型一直回坏格式 → 这一页记成没读出
            output = "not json"
        elif page == 2 and request.stage == "direct":
            output = {**GOOD, "total_amount": ""}   # 第 2 页：直读缺金额 → 走复核
        else:
            output = {**GOOD, "invoice_number": f"IV69/{page:03d}"}
        text = output if isinstance(output, str) else json.dumps(output)
        return ModelResponse(text, {"original": text, "page": page}, 10, 20, request.model, 3,
                             checkpoint_key=f"{request.stage}:{page}:{request.attempt}")


def threads(fn, numbers):
    from concurrent.futures import ThreadPoolExecutor
    numbers = list(numbers)
    with ThreadPoolExecutor(max_workers=len(numbers)) as pool:
        futures = [pool.submit(fn, number) for number in numbers]
    error = next((f.exception() for f in futures if f.exception() is not None), None)
    if error is not None:
        raise error
    return [f.result() for f in futures]


class PagesTogether(unittest.TestCase):
    """多页文件各页同时读（2026-10-01）：结果、计费证据与一页一页读相同，只是不用一页等一页。"""

    def both(self, count=6, **kwargs):
        one = run(source=Source(count), model=PageModel(), **kwargs)
        together = run(source=Source(count), model=PageModel(), map_pages=threads, **kwargs)
        return one, together

    def test_same_pages_and_same_billing_evidence_in_page_order(self):
        one, together = self.both()
        self.assertEqual(together.pipeline.model_dump(), one.pipeline.model_dump())
        self.assertEqual([c.checkpoint_key for c in together.calls], [c.checkpoint_key for c in one.calls])
        self.assertEqual(together.calls, one.calls)
        self.assertIn("IV69/006", together.pipeline.model_dump_json())

    def test_text_pages_take_the_same_pipeline_together(self):
        one = run(source=Source(4, text=True), model=PageModel())
        together = run(source=Source(4, text=True), model=PageModel(), map_pages=threads)
        self.assertEqual(together.pipeline.model_dump(), one.pipeline.model_dump())
        self.assertEqual(together.calls, one.calls)

    def test_pages_are_read_at_the_same_time(self):
        import time
        begin = time.monotonic()
        run(source=Source(6), model=PageModel(delay=0.2), map_pages=threads)
        together = time.monotonic() - begin
        begin = time.monotonic()
        run(source=Source(6), model=PageModel(delay=0.2))
        self.assertLess(together * 2.5, time.monotonic() - begin)

    def test_a_limited_escalation_budget_or_one_page_keeps_reading_in_order(self):
        used = []
        def mapper(fn, numbers):
            used.append(True)
            return threads(fn, numbers)
        run(source=Source(3), model=PageModel(), map_pages=mapper, config=replace(CONFIG, max_escalations=1))
        run(source=Source(1), model=PageModel(), map_pages=mapper)
        self.assertEqual(used, [])

    def test_the_lowest_page_error_is_raised_after_the_others_finish(self):
        class Interrupted(RuntimeError):  # 契约之外的异常（如额度让出、结果不明）照原样往外抛
            pass

        class Failing(PageModel):
            def __call__(self, request):
                if request.page_number in (2, 4):
                    raise Interrupted(f"page {request.page_number}")
                return super().__call__(request)
        model = Failing()
        with self.assertRaises(Interrupted) as caught:
            run(source=Source(5), model=model, map_pages=threads)
        self.assertEqual(str(caught.exception), "page 2")
        self.assertIn(5, {page for page, *_ in model.requests})  # 其余页照常读完，检查点留着续跑取回

if __name__ == "__main__":
    unittest.main()
