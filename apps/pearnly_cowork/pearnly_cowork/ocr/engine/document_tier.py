"""Document tier: the model path for bank statements and ledgers.

Source: ec2e5c957e6b ``services/ocr/page_runner.py`` (the validators and the
repair that run right after the model answers), ``services/ocr/direct_read.py``
(the single-page read and its bank chain gate) and ``services/ocr/bank_chain_gate.py``
(``reread_bank_page``).

This is the tier the file intake calls when no deterministic reader can decode a
file. It is deliberately the *post-model* half: it decodes the model's JSON into
the document models, runs the two validation layers in the deployed order, and
hands back the legacy document shape the reconciliation side already consumes.
The model call itself is injected (``ModelCall``), exactly like the invoice
engine takes it, so this module never opens a socket and a test can run it with
a fake.

Deployed order, kept verbatim (page_runner 2026-05-21 multi-schema refactor):

* ``general_ledger``: ``validate_gl_document`` then ``repair_gl_document``
  ("the two chains must not diverge" — the direct-read path runs the same repair);
* ``bank_statement``: ``validate_bank_document``; the balance-chain gate is the
  separate escalation below;
* non-invoice types with any warning go to manual review (the invoice path has
  its own triggers; the deployed comment says visual fallback is not implemented
  for these types yet).

The bank chain gate is the one piece deferred from the first batch of this family
and ported here: repair what arithmetic can prove row by row, and only escalate a
page whose chain still has an unresolvable break. An escalation that cannot answer
raises ``DirectFallback`` — the deployed fail-safe signal, kept as the same class
the engine already uses.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from pearnly_cowork.ocr.engine.contracts import ModelCall, ModelRequest
from pearnly_cowork.ocr.engine.document_prompts import (
	BANK_STATEMENT_SYSTEM_PROMPT, GENERAL_LEDGER_SYSTEM_PROMPT,
)
from pearnly_cowork.ocr.engine.errors import ContentError, DirectFallback
from pearnly_cowork.ocr.engine.parsing import _parse_json
from pearnly_cowork.ocr.schema.documents import BankStatementDocument, GeneralLedgerDocument
from pearnly_cowork.ocr.validation.chains import (
	bank_chain_warnings, repair_bank_chain, repair_gl_document,
)
from pearnly_cowork.ocr.validation.sources import validate_bank_document, validate_gl_document

DOCUMENT_SCHEMAS = {
	"bank_statement": BankStatementDocument,
	"general_ledger": GeneralLedgerDocument,
}
DOCUMENT_PROMPTS = {
	"bank_statement": BANK_STATEMENT_SYSTEM_PROMPT,
	"general_ledger": GENERAL_LEDGER_SYSTEM_PROMPT,
}
# The types this tier admits. ``vat_report`` / ``generic_table`` have no native
# document model yet, so admitting them would only produce a shape nobody reads.
DOCUMENT_TYPES = tuple(DOCUMENT_SCHEMAS)


def text_pages(content: bytes) -> List[str]:
	"""The document's text layer, one string per page; ``[]`` when unreadable.

	The deployed L2 tier reads text pages (character recognition is L1's job), so
	this is the input the document prompt gets. An image has no text layer, which
	is why an image-only file cannot use this tier.
	"""
	if not isinstance(content, bytes) or not content:
		return []
	try:
		import io

		import pypdf

		reader = pypdf.PdfReader(io.BytesIO(content))
		return [page.extract_text() or "" for page in reader.pages]
	except Exception:
		return []


def admitted(document_type: str) -> bool:
	"""Only the two document types with a native model may enter this tier."""
	return document_type in DOCUMENT_TYPES


def document_prompt(document_type: str) -> str:
	if not admitted(document_type):
		raise ContentError("ocr.document_type_not_supported", detail=str(document_type))
	return DOCUMENT_PROMPTS[document_type]


def decode_document(document_type: str, data: Dict[str, Any]):
	"""Model JSON → the document model; a schema failure is a content failure.

	The deployed code raises out of the tier on a schema error (direct read falls
	back, the Vision path records it); it never repairs the model's shape.
	"""
	if not admitted(document_type):
		raise ContentError("ocr.document_type_not_supported", detail=str(document_type))
	if not isinstance(data, dict):
		raise ContentError("ocr.document_payload_invalid", detail=type(data).__name__)
	try:
		return DOCUMENT_SCHEMAS[document_type](**data)
	except ValidationError as error:
		raise ContentError("ocr.document_schema_invalid", detail=str(error)[:400]) from error


def request_document(prompt: str, text: str, *, page_number: int = 1, model: str = "",
		attempt: int = 1, max_output_tokens: int = 8192, timeout_s: float = 60) -> ModelRequest:
	"""The deployed call shape: the document prompt, then the page text."""
	return ModelRequest(stage="document", page_number=page_number, attempt=attempt, model=model,
		prompt=prompt + "\n\n" + text, max_output_tokens=max_output_tokens, timeout_s=timeout_s)


def model_document(document_type: str, text: str, model_call: ModelCall, *, page_number: int = 1,
		model: str = "", retries: int = 0, max_output_tokens: int = 8192,
		timeout_s: float = 60):
	"""Text → model → document model. A retry re-sends the same request (attempt index moves)."""
	if not admitted(document_type):
		raise ContentError("ocr.document_type_not_supported", detail=str(document_type))
	prompt = DOCUMENT_PROMPTS[document_type]
	last: Optional[ContentError] = None
	for attempt in range(retries + 1):
		request = request_document(prompt, text, page_number=page_number, model=model,
			attempt=attempt, max_output_tokens=max_output_tokens, timeout_s=timeout_s)
		response = model_call(request)
		try:
			if not response.text or "MAX_TOKENS" in response.finish_reason:
				raise ContentError("ocr.response_json_invalid", detail="empty/truncated")
			data = _parse_json(response.text)
		except ContentError as error:
			last = error
			continue
		except Exception as error:  # a malformed reply is a content failure, not a crash
			last = ContentError("ocr.response_json_invalid", detail=type(error).__name__)
			continue
		return decode_document(document_type, data)
	raise last or ContentError("ocr.response_json_invalid")


def validate_document(document, document_type: str, page: Any = None) -> List[str]:
	"""The deployed post-model validation, in the deployed order.

	``page`` is kept in the shape the deployed validators take; today's rules read
	only the row's own provenance, so the page may be ``None``.
	"""
	warnings: List[str] = []
	if document_type == "general_ledger":
		warnings.extend(validate_gl_document(document, page))
		# 台账#10: the stacked layout does not print a direction column, so the
		# balance chain repairs the first row's side and derives the opening.
		warnings.extend(repair_gl_document(document))
	elif document_type == "bank_statement":
		warnings.extend(validate_bank_document(document, page))
	return warnings


def needs_manual_review(document_type: str, warnings: Sequence[str]) -> bool:
	"""Non-invoice types route to manual review as soon as any validator speaks."""
	return bool(warnings) and document_type not in ("invoice", "auto")


def reread_bank_page(document, *, page_number: int = 1,
		read_again: Optional[Callable[[], Optional[Dict[str, Any]]]] = None):
	"""Row-level chain repair, then one escalated re-read of a page that still breaks.

	Returns ``(document, escalated_payload_or_None, audit_warnings)``. Repair what
	the printed chain proves first: a swapped pair has a unique solution, and a
	single-sided misread is overwritten inside a 20% band of the model's own
	reading (both leave their audit marker). Anything left is a real break, which
	is the only thing worth paying to re-read. The re-read result goes through the
	same chain check and is *not* escalated again (the top tier cannot escalate
	further); what is still broken comes back as warnings for review.

	``read_again`` is the caller's model call for the escalated tier; a failure or
	an unreadable answer raises ``DirectFallback``, the deployed fail-safe signal.
	"""
	entries, reasons = repair_bank_chain(document.entries, document.opening_balance)
	document.entries = entries
	if not reasons:
		return document, None, bank_chain_warnings(entries, [])
	try:
		data = read_again() if read_again is not None else None
	except Exception as error:  # noqa: BLE001 — an escalated read that blows up is a fallback
		raise DirectFallback("ocr.bank_escalate_raise", stage="document",
			page_number=page_number, detail=f"{type(error).__name__}: {error}") from error
	if not isinstance(data, dict):
		raise DirectFallback("ocr.bank_escalate_empty", stage="document", page_number=page_number)
	try:
		escalated = BankStatementDocument(**data)
	except ValidationError as error:
		raise DirectFallback("ocr.bank_escalate_schema", stage="document",
			page_number=page_number, detail=str(error)[:400]) from error
	audited, audit_reasons = repair_bank_chain(escalated.entries, escalated.opening_balance)
	escalated.entries = audited
	return escalated, data, bank_chain_warnings(audited, audit_reasons)


def document_to_legacy(document, document_type: str, *, page_number: int = 1,
		warnings: Sequence[str] = (), needs_review: bool = False) -> Dict[str, Any]:
	"""The deployed legacy-adapter shape (``pipeline_result_to_legacy_dict``'s essentials).

	One page holding one document: the reconciliation side already consumes this
	shape (``bank_parse.statement_from_pipeline`` / ``gl_from_pipeline``), so the
	rows, the opening/closing and the review flags keep a single conversion.
	"""
	return {
		"pages": [{"page_number": page_number,
			"document": document.model_dump(mode="json"),
			"document_type": document_type,
			"validation_warnings": list(warnings)}],
		"_needs_review": bool(needs_review),
	}


def escalated_reader(text: str, model_call: ModelCall, *, model: str, page_number: int = 1,
		max_output_tokens: int = 8192, timeout_s: float = 60):
	"""The caller's one-shot re-read of the same page on the stronger tier.

	Deployed shape: the escalation sends the same document prompt again (there, on
	the page image) and hands the payload back for the chain check. A reply that is
	not a JSON object raises, which the gate turns into its fail-safe fallback.
	"""
	def read_again() -> Optional[Dict[str, Any]]:
		response = model_call(request_document(document_prompt("bank_statement"), text,
			page_number=page_number, model=model, attempt=0, max_output_tokens=max_output_tokens,
			timeout_s=timeout_s))
		if not response.text or "MAX_TOKENS" in response.finish_reason:
			return None
		return _parse_json(response.text)

	return read_again


def read_document(document_type: str, text: str, model_call: ModelCall, *, page_number: int = 1,
		model: str = "", retries: int = 0, read_again: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
		escalated_model: str = "") -> Tuple[Any, Dict[str, Any], List[str]]:
	"""One page of text → validated document → legacy shape.

	This is the whole tier in one call: model, decode, validate, and (for bank
	statements) the balance-chain gate with its one escalation. Returns
	``(document, legacy, warnings)``; ``legacy["_needs_review"]`` already folds in
	the validator warnings and the bank audit warnings.
	"""
	document = model_document(document_type, text, model_call, page_number=page_number, model=model,
		retries=retries)
	warnings = validate_document(document, document_type)
	if document_type == "bank_statement":
		reader = read_again
		if reader is None and escalated_model:
			reader = escalated_reader(text, model_call, model=escalated_model,
				page_number=page_number)
		document, _escalated, audit = reread_bank_page(document, page_number=page_number,
			read_again=reader)
		warnings.extend(audit)
	legacy = document_to_legacy(document, document_type, page_number=page_number,
		warnings=warnings, needs_review=needs_manual_review(document_type, warnings))
	return document, legacy, warnings
