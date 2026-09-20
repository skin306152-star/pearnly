"""Offline source-bound OCR upload and page contracts; no provider, Frappe or files."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from pearnly_cowork.ocr import inputs, pages


class Invoice:
	"""Value-object fixture matching the legacy adapter's normalized schema input."""
	def __init__(self, **values):
		self.values = {"invoice_number": None, "seller_tax": "", "buyer_tax": "",
			"is_copy_or_duplicate": False, "payment_received": "", "additional_invoices": [], **values}
		self.__dict__.update(self.values)

	def model_dump(self, mode="json"):
		return {key: [item.model_dump(mode=mode) for item in value] if key == "additional_invoices"
				else deepcopy(value) for key, value in self.values.items()}


def pipeline_page(number=1, invoice=None, **values):
	return SimpleNamespace(**{
		"page_number": number, "invoice": invoice or Invoice(), "total_ms": 11,
		"layer2_input_tokens": 20, "layer3_input_tokens": 3,
		"layer2_output_tokens": 5, "layer3_output_tokens": 2,
		"error": None, "layer_chain": ["ID"], "trigger_reasons": [],
		"needs_manual_review": False, "layer1_avg_confidence": 0.0,
		"document_type": "invoice", "confidence_band": "yellow_confirm", "final_confidence": .95,
		"validation_warnings": [], "extraction_audit": {"source": ["original"]},
		"declared_direction": "", "source_history_id": "", "document": None, **values,
	})


class InputContract(unittest.TestCase):
	def test_all_legacy_formats_and_their_billing_family(self):
		for extension in inputs.SUPPORTED_EXTENSIONS:
			with self.subTest(extension=extension):
				metadata = inputs.validate_input_metadata("ใบแจ้งหนี้" + extension.upper(), 12, pdf_page_count=5)
				family = "pdf" if extension == ".pdf" else "image" if extension in inputs.IMAGE_EXTENSIONS else "table"
				self.assertEqual(metadata, {"extension": extension, "family": family,
					"page_count": 5 if family == "pdf" else 1,
					"billing_kind": "excel" if family == "table" else "pdf"})

	def test_inclusive_limits_and_errors_preserve_front_gate_order(self):
		self.assertEqual(inputs.validate_input_metadata("x.pdf", 104857600, pdf_page_count=50)["page_count"], 50)
		for name, size, count, expected in (
			("x.exe", 0, 0, "ocr.unsupported_format"), ("x.pdf", 0, 0, "ocr.empty_file"),
			("x.pdf", 104857601, 0, "ocr.file_too_large"), ("x.pdf", 100, 0, "ocr.invalid_pdf"),
			("x.pdf", 100, None, "ocr.invalid_pdf"), ("x.pdf", 100, 51, "ocr.too_many_pages"),
		):
			with self.subTest(name=name, size=size, count=count), self.assertRaises(inputs.OcrInputError) as error:
				inputs.validate_input_metadata(name, size, pdf_page_count=count)
			self.assertEqual(error.exception.code, expected)

	def test_filename_gate_does_not_claim_image_decoding_or_magic_validation(self):
		for name in ("x.jpg", "x.gif", "x.tiff"):
			self.assertEqual(inputs.validate_input_metadata(name, 1)["page_count"], 1)
		for name in ("", "pdf", "x.pdf ", "x.heic", "x.avif", "x.zip"):
			with self.subTest(name=name), self.assertRaisesRegex(inputs.OcrInputError, "ocr.unsupported_format"):
				inputs.validate_input_metadata(name, 1)

	def test_internal_adapter_must_supply_integer_sizes_and_physical_counts(self):
		for size in (True, -1, "100", 1.5):
			with self.subTest(size=size), self.assertRaisesRegex(inputs.OcrInputError, "ocr.invalid_input_metadata"):
				inputs.validate_input_metadata("x.jpg", size)
		for count in (True, -1, "2", 1.5):
			with self.subTest(count=count), self.assertRaisesRegex(inputs.OcrInputError, "ocr.invalid_pdf"):
				inputs.validate_input_metadata("x.pdf", 100, pdf_page_count=count)


class PageAssociationContract(unittest.TestCase):
	def test_multiple_invoices_on_one_physical_page_keep_the_same_page_ids(self):
		first = Invoice(invoice_number="A", seller_tax="001", buyer_tax="001",
			additional_invoices=[Invoice(invoice_number="B", seller_tax="001", buyer_tax="002")])
		result = pages.flatten_pipeline_pages([pipeline_page(7, first), pipeline_page(8, Invoice(invoice_number="C"))])
		self.assertEqual([row["fields"]["invoice_number"] for row in result], ["A", "B", "C"])
		self.assertEqual([row["page_index"] for row in result], [7, 7, 8])
		self.assertEqual([row["page_number"] for row in result], [7, 7, 8])
		self.assertEqual([row["_multi_invoice_sibling"] for row in result], [False, True, False])
		self.assertEqual(result[0]["fields"]["tax_ids"], ["001"])
		self.assertEqual(result[1]["fields"]["tax_ids"], ["001", "002"])
		self.assertNotIn("additional_invoices", result[0]["fields"])
		self.assertNotIn("payment_received", result[0]["fields"])

	def test_provenance_warnings_and_printed_amounts_survive_without_recalculation(self):
		invoice = Invoice(total_amount="97.30", subtotal="100", vat="7", discount="9.70", payment_received="95")
		page = pipeline_page(2, invoice, declared_direction="sales", source_history_id="old-id",
			validation_warnings=["possible_missed_invoice"], needs_manual_review=True, confidence_band="needs_review")
		result = pages.flatten_pipeline_pages([page])[0]
		self.assertEqual(result["fields"]["total_amount"], "97.30")
		self.assertEqual(result["fields"]["payment_received"], "95")
		self.assertEqual(result["fields"]["direction"], "sales")
		self.assertEqual(result["fields"]["history_id"], "old-id")
		self.assertTrue(result["_needs_manual_review"])
		self.assertEqual(result["_validation_warnings"], ["possible_missed_invoice"])
		result["_extraction_audit"]["source"].append("edited")
		self.assertEqual(page.extraction_audit, {"source": ["original"]})

	def test_additional_invoices_repeat_diagnostics_but_do_not_create_physical_pages(self):
		result = pages.flatten_pipeline_pages([pipeline_page(1, Invoice(additional_invoices=[Invoice()]))])
		self.assertEqual([row["input_tokens"] for row in result], [23, 23])
		self.assertEqual(len({row["page_index"] for row in result}), 1)
		self.assertEqual(pages.aggregate_confidence_band([pipeline_page(confidence_band="auto"), pipeline_page(confidence_band="needs_review")]), "needs_review")
		self.assertEqual(pages.aggregate_confidence_band([]), "auto")

	def test_history_merge_only_replaces_first_fields_and_retains_page_evidence(self):
		group = {"source_pages": [{"page_index": 3, "fields": {"first": True}, "is_copy": True},
			{"page_index": 4, "fields": {"original": [1]}, "is_duplicate": False}],
			"invoice_fields": {"total_amount": "5", "items": [{"name": "A"}]}}
		before = deepcopy(group)
		result = pages.history_pages(group)
		self.assertEqual(result[0]["fields"], group["invoice_fields"])
		self.assertEqual(result[1], group["source_pages"][1])
		self.assertTrue(result[0]["is_copy"])
		result[1]["fields"]["original"].append(2)
		self.assertEqual(group, before)
		self.assertEqual(pages.history_pages({"source_pages": [], "invoice_fields": {}}), [])


class RecognitionQualityContract(unittest.TestCase):
	def test_high_quality_uses_best_primary_page_not_completeness_average(self):
		complete = {"invoice_number": "A", "date": "2026-09-20", "total_amount": "0", "seller_tax": "001", "buyer_tax": "002", "items": [{}]}
		result = pages.assess_confidence([{"fields": complete}, {"fields": {}}, {"fields": {"invoice_number": "A"}}])
		self.assertEqual(result["confidence"], "high")
		self.assertEqual([page["is_duplicate"] for page in result["pages"]], [False, False, True])
		self.assertEqual(pages.page_confidence({"fields": complete}), 7)

	def test_all_copy_fallback_uses_first_unique_number_even_if_later_copy_is_richer(self):
		original = [{"is_copy": True, "fields": {"invoice_number": "A"}},
			{"is_copy": True, "fields": {"invoice_number": "A", "date": "2026-09-20", "total_amount": "10", "items": [{}]}}]
		before = deepcopy(original)
		result = pages.assess_confidence(original)
		self.assertEqual(result["confidence"], "low")
		self.assertEqual([page["is_duplicate"] for page in result["pages"]], [False, True])
		self.assertEqual(original, before)

	def test_source_truthiness_zero_and_text_structure_compensation(self):
		self.assertEqual(pages.page_confidence({"fields": {"total_amount": 0, "subtotal": "1", "vat": 0}}), 0)
		self.assertEqual(pages.page_confidence({"fields": {"total_amount": "0", "subtotal": "1", "vat": "0"}}), 3)
		self.assertEqual(pages.assess_confidence([]), {"pages": [], "confidence": "low"})

	def test_noninvoice_gate_only_rejects_when_every_page_explicitly_is_noninvoice(self):
		self.assertFalse(pages.all_pages_not_invoice([]))
		self.assertFalse(pages.all_pages_not_invoice([None]))
		self.assertFalse(pages.all_pages_not_invoice([{"fields": {"is_not_invoice": True}}, {"fields": {}}]))
		self.assertTrue(pages.all_pages_not_invoice([{"fields": {"is_not_invoice": " TRUE "}}]))
		self.assertFalse(pages.all_pages_not_invoice([{"fields": {"is_not_invoice": "yes"}}]))


if __name__ == "__main__":
	unittest.main()
