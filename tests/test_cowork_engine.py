"""Pure, deterministic engine scenarios. No credentials, HTTP, Site, or paid OCR."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import unittest

from pearnly_cowork.ocr.engine import (
    EngineConfig, EngineError, Frame, ModelResponse, VisionResponse, run_invoice,
)
from pearnly_cowork.ocr.engine.fewshot import fewshot_for_text
from pearnly_cowork.ocr.engine.contracts import ProviderFailure
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

    def test_direct_fallback_reprocesses_whole_file_and_retains_all_usage(self):
        model, source = Model([GOOD, {"total_amount": ""}, GOOD, GOOD]), Source(2)
        result = run(source, model)
        self.assertEqual(source.frames, [1, 2, 1, 2])
        self.assertEqual([r.stage for r in model.requests], ["direct", "direct", "layer2", "layer2"])
        self.assertEqual(result.pipeline.engine, "pipeline_v1_id_fb")
        self.assertEqual(len(result.calls), 6)
        self.assertEqual(sum(c.input_tokens for c in result.calls), 40)

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
        for extra in ({"document_type": "credit_note"}, {"date": "2016-09-21"}):
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
        result = run(model=Model([failure, GOOD]))
        self.assertEqual(result.pipeline.engine, "pipeline_v1_id_fb")
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
        self.assertEqual(tight.pipeline.engine, "pipeline_v1_id_fb")
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


if __name__ == "__main__":
    unittest.main()
