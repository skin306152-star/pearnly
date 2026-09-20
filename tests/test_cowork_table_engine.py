"""Pure logical-unit table execution. No decoder IO, Site or provider requests."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from pearnly_cowork.ocr.engine import EngineError
from pearnly_cowork.ocr.engine.contracts import ProviderFailure
from pearnly_cowork.ocr.schema import Page
from pearnly_cowork.ocr.table_engine import run_table, validate_document
from pearnly_cowork.ocr.table_engine.roundtrip import REQUIRED_HEADERS, has_roundtrip_sheet
from test_cowork_engine import CONFIG, GOOD, Model, TEXT, TODAY


def page(number=1, *, text=TEXT, **kwargs):
    return Page(page_number=number, width=0, height=0, blocks=[], full_text=text,
                avg_confidence=kwargs.pop("avg_confidence", 1), **kwargs)


def document(pages=None, *, provenance=None):
    pages = tuple(pages or (page(),))
    return SimpleNamespace(pages=pages, page_count=len(pages), provenance=provenance or {})


def run(doc=None, model=None, **kwargs):
    return run_table(doc or document(), model or Model(), config=kwargs.pop("config", CONFIG),
                     anchor_year=2026, today=TODAY, **kwargs)


def roundtrip_provenance(name="ขาย", headers=None, *, source_row=1):
    return {"units": [{"sheet_name": name, "rows": [{"source_row": source_row,
             "cells": list(REQUIRED_HEADERS) if headers is None else headers}]}]}


class TableEngineTests(unittest.TestCase):
    def test_l2_only_even_when_direct_and_layer3_are_enabled(self):
        model = Model([{**GOOD, "invoice_number": "WRONG", "total_amount": ""}])
        result = run(document([page(avg_confidence=.7)]), model)
        self.assertEqual(result.pipeline.engine, "pipeline_v1_table")
        self.assertEqual(result.pipeline.pages[0].layer_chain, ["text", "L2"])
        self.assertEqual([r.stage for r in model.requests], ["layer2"])
        self.assertEqual(model.requests[0].images, ())
        self.assertEqual(model.requests[0].max_output_tokens, 16384)
        self.assertEqual(result.pipeline.pages[0].layer3_input_tokens, 0)
        self.assertTrue(result.pipeline.pages[0].needs_manual_review)

    def test_structured_grid_is_the_model_input_and_full_text_is_retained(self):
        source = page(text="evidence including paragraphs and grid", table_headers=["A", "B"],
                      table_rows=[{"A": TEXT, "B": "value"}])
        seen, model = [], Model()
        run(document([source]), model, fewshot_selector=lambda text: seen.append(text) or "FEWSHOT\n")
        expected = "A | B\n" + TEXT + " | value"
        self.assertEqual(seen, [expected])
        self.assertTrue(model.requests[0].prompt.endswith(expected))
        self.assertIn("FEWSHOT\nExtract from", model.requests[0].prompt)
        self.assertEqual(source.full_text, "evidence including paragraphs and grid")

    def test_all_units_validate_before_any_paid_call(self):
        model = Model()
        with self.assertRaisesRegex(EngineError, "ocr.table_text_too_long") as ctx:
            run(document([page(), page(2, text="x" * 30001)]), model)
        self.assertEqual(ctx.exception.page_number, 2)
        self.assertEqual(model.requests, [])

    def test_exact_text_limit_uses_stripped_model_text(self):
        model = Model()
        run(document([page(text=" \n" + "x" * 30000 + " \n")]), model)
        self.assertTrue(model.requests[0].prompt.endswith("x" * 30000))
        validate_document(document([page(text="x" * 30001, table_headers=["A"],
                                         table_rows=[{"A": TEXT}])]))

    def test_virtual_units_do_not_inherit_physical_pdf_limit(self):
        model = Model()
        result = run(document([page(i) for i in range(1, 52)]), model)
        self.assertEqual(result.pipeline.page_count, 51)
        self.assertEqual([r.page_number for r in model.requests], list(range(1, 52)))

    def test_short_and_blank_units_are_explicit_noninvoice_without_calls(self):
        model = Model()
        result = run(document([page(text=""), page(2, text="short")]), model)
        self.assertEqual(model.requests, [])
        self.assertTrue(all(p.invoice.is_not_invoice for p in result.pipeline.pages))
        self.assertEqual([p.page_number for p in result.pipeline.pages], [1, 2])

    def test_low_confidence_follows_original_bucket_not_any_trigger_rule(self):
        for confidence, band in ((1, "auto"), (.95, "yellow_confirm"), (.85, "needs_review")):
            result = run(document([page(avg_confidence=confidence)]))
            self.assertEqual(result.pipeline.pages[0].confidence_band, band)
        result = run(model=Model([{**GOOD, "invoice_number": "WRONG"}]))
        self.assertEqual(result.pipeline.pages[0].confidence_band, "yellow_confirm")
        self.assertFalse(result.pipeline.pages[0].needs_manual_review)

    def test_discount_repair_keeps_first_response_and_requires_review(self):
        payload = {**GOOD, "total_amount": "102"}
        model = Model([payload])
        result = run(model=model)
        self.assertEqual(result.pipeline.pages[0].invoice.discount, "5.00")
        self.assertTrue(result.pipeline.pages[0].needs_manual_review)
        self.assertNotIn("discount", result.calls[0].raw["original"])
        self.assertNotIn("discount", payload)

    def test_sibling_invoices_stay_in_one_unit_without_duplicate_usage(self):
        result = run(model=Model([{**GOOD, "additional_invoices": [{**GOOD, "invoice_number": "IV69/101"}]}]))
        self.assertEqual(len(result.pipeline.pages), 1)
        self.assertEqual(len(result.pipeline.pages[0].invoice.additional_invoices), 1)
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.calls[0].input_tokens, 10)

    def test_retry_keeps_distinct_attempts_and_original_raw(self):
        model = Model(["bad JSON", GOOD])
        result = run(model=model)
        self.assertEqual([r.attempt for r in model.requests], [0, 1])
        self.assertEqual([c.text for c in result.calls][0], "bad JSON")
        self.assertEqual(result.pipeline.pages[0].layer2_input_tokens, 20)

    def test_unknown_deferred_and_definite_failure_never_escalate(self):
        class Deferred(Exception): pass
        class Uncertain(Exception): pass
        for failure in (Deferred("later"), Uncertain("unknown"), ProviderFailure("ocr.provider_http_error")):
            model = Model([failure])
            with self.assertRaises(type(failure)) as ctx:
                run(model=model)
            self.assertIs(ctx.exception, failure)
            self.assertEqual([r.stage for r in model.requests], ["layer2"])

    def test_schema_failure_stops_without_image_or_extra_paid_retry(self):
        model = Model([{**GOOD, "items": "wrong"}])
        with self.assertRaisesRegex(EngineError, "ocr.invoice_schema_invalid"):
            run(model=model)
        self.assertEqual(len(model.requests), 1)

    def test_later_unit_refusal_does_not_return_partial_success(self):
        failure = ProviderFailure("ocr.provider_http_error", http_status=400)
        model = Model([GOOD, failure])
        with self.assertRaises(ProviderFailure) as ctx:
            run(document([page(), page(2), page(3)]), model)
        self.assertIs(ctx.exception, failure)
        self.assertEqual([r.page_number for r in model.requests], [1, 2])

    def test_sequence_and_count_fail_before_calls(self):
        invalid = [document([page(2)]), SimpleNamespace(pages=(), page_count=0, provenance={}),
                   SimpleNamespace(pages=(page(),), page_count=True, provenance={}),
                   SimpleNamespace(pages=(page(),), page_count=2, provenance={})]
        for doc in invalid:
            model = Model()
            with self.assertRaises(EngineError):
                run(doc, model)
            self.assertEqual(model.requests, [])

    def test_mode_and_date_context_fail_before_calls(self):
        for config in (replace(CONFIG, document_type="general_ledger"), replace(CONFIG, double_read=True),
                       replace(CONFIG, provider="qwen"), replace(CONFIG, layer2_model="")):
            model = Model()
            with self.assertRaises(EngineError):
                run(model=model, config=config)
            self.assertEqual(model.requests, [])
        with self.assertRaisesRegex(EngineError, "ocr.date_context_invalid"):
            run_table(document(), Model(), config=CONFIG, anchor_year=2026, today=None)

    def test_frozen_inputs_cannot_be_changed_by_an_earlier_callback(self):
        doc = document([page(), page(2)])
        inner, seen = Model(), []
        def model(request):
            seen.append(request.prompt)
            doc.pages[1].full_text = "short"
            return inner(request)
        result = run(doc, model)
        self.assertEqual(len(seen), 2)
        self.assertFalse(result.pipeline.pages[1].invoice.is_not_invoice)

    def test_runtime_configuration_is_used_for_model_timeout_and_thresholds(self):
        model = Model()
        config = replace(CONFIG, layer2_model="chosen", layer2_timeout_s=31,
                         auto_threshold=.97, review_threshold=.92)
        result = run(document([page(avg_confidence=.91)]), model, config=config)
        self.assertEqual((model.requests[0].model, model.requests[0].timeout_s), ("chosen", 31))
        self.assertEqual(result.pipeline.pages[0].confidence_band, "needs_review")

    def test_roundtrip_first_row_markers_reject_before_model(self):
        for name in ("ขาย", "ซื้อ", "รอจำแนก", " ขาย "):
            model = Model()
            with self.assertRaisesRegex(EngineError, "ocr.table_roundtrip_not_supported"):
                run(document(provenance=roundtrip_provenance(name)), model)
            self.assertEqual(model.requests, [])

    def test_roundtrip_requires_exact_known_sheet_and_all_required_headers_on_row_one(self):
        headers = list(REQUIRED_HEADERS)
        for provenance in (roundtrip_provenance("สรุป"), roundtrip_provenance("sales"),
                           roundtrip_provenance(headers=headers[:-1]), roundtrip_provenance(source_row=2)):
            self.assertFalse(has_roundtrip_sheet(provenance))
        self.assertTrue(has_roundtrip_sheet(roundtrip_provenance(headers=[" " + h + " " for h in headers]
                                                               + ["สาขาคู่ค้า", "irrelevant", headers[0]])))

    def test_roundtrip_uses_formula_text_headers_over_cached_cell_values(self):
        provenance = roundtrip_provenance()
        provenance["units"][0]["roundtrip_headers"] = ["=formula"]
        self.assertFalse(has_roundtrip_sheet(provenance))
        provenance["units"][0]["roundtrip_headers"] = list(REQUIRED_HEADERS)
        provenance["units"][0]["rows"][0]["cells"] = []
        self.assertTrue(has_roundtrip_sheet(provenance))


if __name__ == "__main__":
    unittest.main()
