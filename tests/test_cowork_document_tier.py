"""Offline regressions for the migrated OCR document tier (batch 42).

Runs in the lab bench (pydantic and the ocr package need it). The model call is a
fake that hands back JSON, so no provider is contacted and nothing is paid for.
"""

import io
import json
import unittest

from pearnly_cowork.ocr.engine import document_prompts, document_tier
from pearnly_cowork.ocr.engine.contracts import ModelResponse
from pearnly_cowork.ocr.engine.errors import ContentError, DirectFallback
from pearnly_cowork.ocr.schema.documents import BankStatementDocument, GeneralLedgerDocument

BANK_JSON = {
	"document_type": "bank_statement",
	"bank_name": "Bangkok Bank",
	"opening_balance": "1000.00",
	"closing_balance": "900.00",
	"entries": [
		{"transaction_date": "2026-06-01", "description": "service fee",
			"withdrawal": "100.00", "balance": "900.00",
			"withdrawal_ref": {"value": "100.00", "source_text": "100.00",
				"source_column": "Withdrawal"}},
	],
}
GL_JSON = {
	"document_type": "general_ledger",
	"opening_balance": "1000.00",
	"closing_balance": "900.00",
	"entries": [
		{"transaction_date": "2026-06-01", "voucher_no": "JV-1", "description": "fuel 5301",
			"credit": "100.00", "balance": "900.00",
			"credit_ref": {"value": "100.00", "source_text": "100.00", "source_column": "Credit"}},
	],
}


class FakeModel:
	"""Hands back queued payloads in order (the last one repeats)."""

	def __init__(self, payload, *, text=None, finish_reason=""):
		queued = payload if isinstance(payload, list) else [payload]
		if text is None:
			self.texts = [json.dumps(item) if isinstance(item, (dict, list)) else str(item)
				for item in queued]
		else:
			self.texts = [text] * len(queued)
		self.finish_reason = finish_reason
		self.requests = []

	def __call__(self, request):
		self.requests.append(request)
		index = min(len(self.requests) - 1, len(self.texts) - 1)
		return ModelResponse(text=self.texts[index], raw={"fake": True}, input_tokens=1,
			output_tokens=1, model=request.model or "fake", finish_reason=self.finish_reason)


def pdf_with_text(text):
	"""A one-page PDF carrying ``text`` in its text layer."""
	stream = ("BT /F1 10 Tf 20 700 Td (%s) Tj ET" % text).encode("latin-1", "replace")
	objects = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
		b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
		b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
		b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	]
	out = bytearray(b"%PDF-1.4\n")
	offsets = []
	for number, body in enumerate(objects, 1):
		offsets.append(len(out))
		out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
	xref = len(out)
	out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
	for offset in offsets:
		out += ("%010d 00000 n \n" % offset).encode()
	out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
		b"startxref\n" + str(xref).encode() + b"\n%%EOF\n")
	return bytes(out)


class PromptsAndSchemas(unittest.TestCase):
	def test_only_the_two_document_types_are_admitted(self):
		self.assertTrue(document_tier.admitted("bank_statement"))
		self.assertTrue(document_tier.admitted("general_ledger"))
		for other in ("invoice", "auto", "vat_report", "generic_table", ""):
			self.assertFalse(document_tier.admitted(other))
			with self.assertRaises(ContentError):
				document_tier.document_prompt(other)

	def test_the_prompts_demand_the_provenance_the_validators_read(self):
		self.assertIn("*_ref", document_tier.document_prompt("bank_statement"))
		self.assertIn("source_column", document_tier.document_prompt("general_ledger"))
		self.assertEqual(set(document_tier.DOCUMENT_SCHEMAS),
			{"bank_statement", "general_ledger"})
		self.assertIs(document_tier.DOCUMENT_SCHEMAS["bank_statement"], BankStatementDocument)
		self.assertIs(document_tier.DOCUMENT_SCHEMAS["general_ledger"], GeneralLedgerDocument)

	def test_the_two_prompt_families_are_kept_apart(self):
		# The gemini prompts serve the row conversion; these serve the document
		# schema and its provenance refs. Swapping them would silently drop one.
		from pearnly_cowork.reconciliation import bank_prompts

		self.assertNotEqual(document_prompts.BANK_STATEMENT_SYSTEM_PROMPT,
			bank_prompts.STATEMENT_PROMPT)
		self.assertIn("printed_credit_count", bank_prompts.STATEMENT_PROMPT)
		self.assertNotIn("printed_credit_count", document_prompts.BANK_STATEMENT_SYSTEM_PROMPT)


class TextPages(unittest.TestCase):
	def test_the_text_layer_of_a_pdf_is_read_page_by_page(self):
		pages = document_tier.text_pages(pdf_with_text("SYNTHETIC STATEMENT"))
		self.assertEqual(len(pages), 1)
		self.assertIn("SYNTHETIC STATEMENT", pages[0])

	def test_bytes_that_are_not_a_pdf_return_nothing(self):
		self.assertEqual(document_tier.text_pages(b"not a pdf"), [])
		self.assertEqual(document_tier.text_pages(b""), [])
		self.assertEqual(document_tier.text_pages(None), [])


class ModelDocument(unittest.TestCase):
	def test_the_request_carries_the_document_prompt_and_the_page_text(self):
		model = FakeModel(BANK_JSON)
		document_tier.model_document("bank_statement", "01/06/69 FEE 100.00 900.00", model,
			model="gemini-flash-lite")
		request = model.requests[0]
		self.assertEqual(request.stage, "document")
		self.assertEqual(request.model, "gemini-flash-lite")
		self.assertTrue(request.prompt.startswith(document_prompts.BANK_STATEMENT_SYSTEM_PROMPT))
		self.assertTrue(request.prompt.endswith("01/06/69 FEE 100.00 900.00"))
		self.assertEqual(request.temperature, 0.0)
		self.assertTrue(request.response_mime)

	def test_the_json_becomes_the_document_model(self):
		document = document_tier.model_document("bank_statement", "text", FakeModel(BANK_JSON))
		self.assertIsInstance(document, BankStatementDocument)
		self.assertEqual(document.opening_balance, "1000.00")
		self.assertEqual(len(document.entries), 1)

	def test_a_schema_failure_is_a_content_failure(self):
		# deposit must be a string in the deployed schema; a nested object is refused
		# rather than repaired.
		bad = dict(BANK_JSON, entries=[{"description": "x", "deposit": {"value": "1"}}])
		with self.assertRaises(ContentError) as caught:
			document_tier.model_document("bank_statement", "text", FakeModel(bad))
		self.assertEqual(caught.exception.code, "ocr.document_schema_invalid")

	def test_a_truncated_reply_is_refused(self):
		model = FakeModel(BANK_JSON, text="", finish_reason="MAX_TOKENS")
		with self.assertRaises(ContentError) as caught:
			document_tier.model_document("bank_statement", "text", model)
		self.assertEqual(caught.exception.code, "ocr.response_json_invalid")
		self.assertEqual(len(model.requests), 1)

	def test_a_retry_sends_the_same_request_again(self):
		model = FakeModel(BANK_JSON, text="not json at all")
		with self.assertRaises(ContentError):
			document_tier.model_document("bank_statement", "text", model, retries=1)
		self.assertEqual([request.attempt for request in model.requests], [0, 1])

	def test_a_fenced_reply_still_parses(self):
		fenced = "```json\n" + json.dumps(BANK_JSON) + "\n```"
		document = document_tier.model_document("bank_statement", "text", FakeModel(BANK_JSON, text=fenced))
		self.assertEqual(document.document_type, "bank_statement")

	def test_a_non_mapping_payload_is_refused(self):
		with self.assertRaises(ContentError) as caught:
			document_tier.decode_document("bank_statement", ["not", "a", "mapping"])
		self.assertEqual(caught.exception.code, "ocr.document_payload_invalid")


class ValidateDocument(unittest.TestCase):
	def test_the_ledger_runs_the_validators_and_then_the_repair(self):
		# A row whose credit actually came from the description column is rejected and
		# cleared by the source validator; the repair then rebuilds the chain.
		data = dict(GL_JSON, entries=[{
			"transaction_date": "2026-06-01", "voucher_no": "JV-1",
			"description": "fuel 6091", "credit": "6091.00", "balance": "6091.00",
			"credit_ref": {"value": "6091.00", "source_text": "6091",
				"source_column": "Description"}}])
		document = document_tier.decode_document("general_ledger", data)
		warnings = document_tier.validate_document(document, "general_ledger")
		self.assertTrue(any("6091" in warning for warning in warnings))
		self.assertEqual(document.entries[0].credit, "")

	def test_a_clean_ledger_produces_no_warnings(self):
		document = document_tier.decode_document("general_ledger", GL_JSON)
		self.assertEqual(document_tier.validate_document(document, "general_ledger"), [])

	def test_the_bank_side_uses_its_own_validator(self):
		data = dict(BANK_JSON, entries=[{
			"transaction_date": "2026-06-01", "description": "fee 6091",
			"withdrawal": "6091.00", "balance": "6091.00",
			"withdrawal_ref": {"value": "6091.00", "source_text": "6091",
				"source_column": "Description"}}])
		document = document_tier.decode_document("bank_statement", data)
		warnings = document_tier.validate_document(document, "bank_statement")
		self.assertTrue(any("6091" in warning for warning in warnings))
		self.assertEqual(document.entries[0].withdrawal, "")

	def test_only_non_invoice_types_route_to_manual_review(self):
		self.assertTrue(document_tier.needs_manual_review("bank_statement", ["something"]))
		self.assertTrue(document_tier.needs_manual_review("general_ledger", ["something"]))
		self.assertFalse(document_tier.needs_manual_review("bank_statement", []))
		self.assertFalse(document_tier.needs_manual_review("invoice", ["something"]))


class BankChainGate(unittest.TestCase):
	def test_a_swapped_pair_is_repaired_without_escalating(self):
		data = dict(BANK_JSON, entries=[
			{"transaction_date": "2026-06-02", "description": "in", "deposit": "100.00",
				"balance": "1100.00"},
			{"transaction_date": "2026-06-03", "description": "out", "deposit": "50.00",
				"balance": "1050.00"},
		])
		document = document_tier.decode_document("bank_statement", data)
		escalations = []
		document, escalated, warnings = document_tier.reread_bank_page(document,
			read_again=lambda: escalations.append(1))
		self.assertIsNone(escalated)
		self.assertEqual(escalations, [])
		self.assertTrue(document.entries[1].chain_repaired)
		self.assertEqual((document.entries[1].withdrawal, document.entries[1].deposit),
			("50.00", ""))
		self.assertEqual(warnings, [])

	def test_an_unresolvable_break_escalates_once(self):
		data = dict(BANK_JSON, entries=[
			{"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00",
				"balance": "9999.00"},
		])
		document = document_tier.decode_document("bank_statement", data)
		better = dict(BANK_JSON, entries=[
			{"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00",
				"balance": "950.00"}])
		document, escalated, warnings = document_tier.reread_bank_page(document,
			read_again=lambda: better)
		self.assertEqual(escalated, better)
		self.assertEqual(document.entries[0].balance, "950.00")
		self.assertEqual(warnings, [])

	def test_the_escalated_reading_is_audited_but_not_escalated_again(self):
		data = dict(BANK_JSON, entries=[
			{"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00",
				"balance": "9999.00"}])
		document = document_tier.decode_document("bank_statement", data)
		calls = []

		def read_again():
			calls.append(1)
			return data

		document, escalated, warnings = document_tier.reread_bank_page(document, read_again=read_again)
		self.assertEqual(len(calls), 1)
		self.assertEqual(escalated, data)
		# The break survives the escalated read, so it is reported for review instead
		# of being escalated yet again.
		self.assertTrue(any("max 重读链校验" in warning for warning in warnings))

	def test_an_escalation_that_cannot_answer_falls_back(self):
		data = dict(BANK_JSON, entries=[
			{"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00",
				"balance": "9999.00"}])
		document = document_tier.decode_document("bank_statement", data)
		with self.assertRaises(DirectFallback):
			document_tier.reread_bank_page(document, read_again=lambda: None)
		with self.assertRaises(DirectFallback):
			document_tier.reread_bank_page(document, read_again=lambda: "not a mapping")

		def raises():
			raise RuntimeError("provider blew up")

		with self.assertRaises(DirectFallback) as caught:
			document_tier.reread_bank_page(document, read_again=raises)
		self.assertIn("RuntimeError", caught.exception.detail)

	def test_an_escalated_reply_that_breaks_the_schema_falls_back(self):
		data = dict(BANK_JSON, entries=[
			{"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00",
				"balance": "9999.00"}])
		document = document_tier.decode_document("bank_statement", data)
		with self.assertRaises(DirectFallback) as caught:
			document_tier.reread_bank_page(document, read_again=lambda: {"entries": "nope"})
		self.assertEqual(caught.exception.code, "ocr.bank_escalate_schema")

	def test_the_escalated_reader_reuses_the_document_prompt(self):
		model = FakeModel(BANK_JSON)
		reader = document_tier.escalated_reader("page text", model, model="stronger")
		payload = reader()
		self.assertEqual(payload["document_type"], "bank_statement")
		request = model.requests[0]
		self.assertEqual(request.model, "stronger")
		self.assertEqual(request.stage, "document")
		self.assertTrue(request.prompt.startswith(document_prompts.BANK_STATEMENT_SYSTEM_PROMPT))
		self.assertTrue(request.prompt.endswith("page text"))

	def test_an_escalated_reply_that_is_empty_returns_nothing(self):
		reader = document_tier.escalated_reader("text", FakeModel(BANK_JSON, text="",
			finish_reason="MAX_TOKENS"), model="stronger")
		self.assertIsNone(reader())

	def test_a_clean_chain_never_asks_for_a_reread(self):
		document = document_tier.decode_document("bank_statement", BANK_JSON)
		document, escalated, warnings = document_tier.reread_bank_page(document,
			read_again=lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
		self.assertIsNone(escalated)
		self.assertEqual(warnings, [])


class ReadDocument(unittest.TestCase):
	def test_the_statement_page_comes_back_in_the_legacy_shape(self):
		document, legacy, warnings = document_tier.read_document("bank_statement",
			"01/06/69 FEE 100.00 900.00", FakeModel(BANK_JSON), model="m")
		self.assertEqual(legacy["pages"][0]["document"]["document_type"], "bank_statement")
		self.assertEqual(legacy["pages"][0]["document"]["opening_balance"], "1000.00")
		self.assertEqual(legacy["pages"][0]["document_type"], "bank_statement")
		self.assertFalse(legacy["_needs_review"])
		self.assertEqual(warnings, [])

	def test_a_validator_warning_routes_the_page_to_review(self):
		# The cleared amount leaves a row with no movement and a jumped balance, which
		# the chain gate cannot resolve — so a page like this needs an escalated
		# reader, exactly as deployed. With one, the cleared row is reported for
		# review and the reading that comes back is used.
		data = dict(BANK_JSON, entries=[{
			"transaction_date": "2026-06-01", "description": "fee 6091",
			"withdrawal": "6091.00", "balance": "6091.00",
			"withdrawal_ref": {"value": "6091.00", "source_text": "6091",
				"source_column": "Description"}}])
		clean = dict(BANK_JSON, entries=[{
			"transaction_date": "2026-06-01", "description": "fee",
			"withdrawal": "100.00", "balance": "900.00",
			"withdrawal_ref": {"value": "100.00", "source_text": "100.00",
				"source_column": "Withdrawal"}}])
		model = FakeModel([data, clean])
		_document, legacy, warnings = document_tier.read_document("bank_statement", "text",
			model, escalated_model="stronger")
		self.assertTrue(warnings)
		self.assertTrue(legacy["_needs_review"])
		# The escalated read asked the stronger model, with the same document prompt.
		self.assertEqual([request.model for request in model.requests], ["", "stronger"])
		self.assertEqual(legacy["pages"][0]["document"]["entries"][0]["withdrawal"], "100.00")

	def test_a_validator_warning_without_an_escalation_falls_back(self):
		data = dict(BANK_JSON, entries=[{
			"transaction_date": "2026-06-01", "description": "fee 6091",
			"withdrawal": "6091.00", "balance": "6091.00",
			"withdrawal_ref": {"value": "6091.00", "source_text": "6091",
				"source_column": "Description"}}])
		with self.assertRaises(DirectFallback):
			document_tier.read_document("bank_statement", "text", FakeModel(data))

	def test_the_ledger_page_gets_the_same_treatment(self):
		_document, legacy, warnings = document_tier.read_document("general_ledger", "text",
			FakeModel(GL_JSON))
		self.assertEqual(legacy["pages"][0]["document"]["document_type"], "general_ledger")
		self.assertEqual(warnings, [])

	def test_the_legacy_document_is_json_serialisable(self):
		_document, legacy, _warnings = document_tier.read_document("general_ledger", "text",
			FakeModel(GL_JSON))
		self.assertIn('"document_type": "general_ledger"', json.dumps(legacy))

	def test_an_unsupported_type_is_refused_before_the_model(self):
		model = FakeModel({})
		with self.assertRaises(ContentError):
			document_tier.read_document("vat_report", "text", model)
		self.assertEqual(model.requests, [])


if __name__ == "__main__":
	unittest.main()
