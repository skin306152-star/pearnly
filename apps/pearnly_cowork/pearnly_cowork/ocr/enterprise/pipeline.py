"""Enterprise pipeline: pinned OCR -> local schema, or pinned image + schema on every page.

Source: ec2e5c957e6b ``services/ocr/enterprise_pipeline.py`` (``read_pdf``, ``_local_pages``, ``run``).
Kept as deployed: Document AI reads a PDF in chunks (deployed 15 pages, now ``MAX_PAGES_PER_READ``; an
image once); bank statements and ledgers are first reconstructed locally from the page geometry and used as they are
when the arithmetic passes; otherwise — and always for a VAT report, which has no local parser —
every page is extracted by the frozen contract (the page image, the Enterprise transcript, 3.8 LOW,
the contract's JSON schema); a page whose extraction does not come back as an object fails the file
(the deployed pipeline never substitutes another method). The ledger and statement pages extracted
that way are checked as one document afterwards.

Changed for this platform: provider calls are injected (``document_ai_call``, ``model_call``) so they
run inside the durable OCR Work (checkpoints, shared budget, deferral); pages are rendered only when
the schema extraction needs them, one at a time (the deployed code rendered every page up front);
the model timeout is 120 s (the provider client's ceiling; deployed 180 s). The cost estimate counts
the Document AI pages plus each extraction's tokens at the deployed ``services/ocr/cost.py`` prices
(internal observation only, never billing; until 2026-09-29 the model tokens were left out).
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Callable, Iterable, List, Tuple

from pearnly_cowork.ocr.engine.contracts import ModelRequest
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import contract, header_bank, pages as enterprise_pages
from pearnly_cowork.ocr.enterprise.adapter import PipelineResult, make_page
from pearnly_cowork.ocr.enterprise.local.geometry import rows_from_payloads
from pearnly_cowork.ocr.enterprise.local.result import check_document, reconstruct
from pearnly_cowork.ocr.enterprise.request import build_request

ENGINE = contract.CONTRACT_VERSION  # "enterprise-2026-09-03-v1", the deployed engine label
THB_PER_USD = 35.0
# USD per million input / output tokens: deployed services/ocr/cost.py MODEL_PRICES_PER_M_USD (2026-07 price
# list). An unknown model is priced as the most expensive one listed, as deployed (observation errs high).
MODEL_PRICES_PER_M_USD = {"gemini-3.8-flash": (0.75, 3.75), "gemini-3.1-flash-lite": (0.25, 1.50)}


def model_cost_thb(model, input_tokens, output_tokens):
    price = next((value for prefix, value in MODEL_PRICES_PER_M_USD.items() if str(model or "").startswith(prefix)),
                 max(MODEL_PRICES_PER_M_USD.values()))
    usd = max(0, int(input_tokens or 0)) / 1e6 * price[0] + max(0, int(output_tokens or 0)) / 1e6 * price[1]
    return round(usd * THB_PER_USD, 6)
MODEL_TIMEOUT_SECONDS = 120
CATEGORIES = {"bank_statement": "bank", "general_ledger": "gl", "vat_report": "vat"}


def pdf_chunks(pdf: bytes, page_count: int) -> List[Tuple[bytes, str, int]]:
    """The deployed ``read_pdf`` split: ``(chunk bytes, mime, pages)`` of at most ``MAX_PAGES_PER_READ`` pages each."""
    import pymupdf

    chunks = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if document.page_count != page_count:
            raise ContentError("ocr.enterprise_page_count_mismatch")
        for start in range(0, page_count, enterprise_pages.MAX_PAGES_PER_READ):
            end = min(start + enterprise_pages.MAX_PAGES_PER_READ, page_count)
            with pymupdf.open() as chunk:
                chunk.insert_pdf(document, from_page=start, to_page=end - 1)
                chunks.append((chunk.tobytes(), "application/pdf", end - start))
    return chunks


def _local_pages(result, category):
    pages = []
    previous = result.document.get("opening_balance", "")
    for number in range(1, result.pages + 1):
        document = deepcopy(result.document)
        document["entries"] = [row for row in document["entries"] if int(row["page"]) == number]
        document["opening_balance"] = previous
        document["closing_balance"] = document["entries"][-1].get("balance", "")
        previous = document["closing_balance"]
        # Document-wide totals must not be repeated as each page's printed totals.
        for field in ("total_debit", "total_credit"):
            document[field] = ""
        pages.append(
            make_page(
                document,
                category,
                number,
                audit=result.audit if number == 1 else [],
                provenance=result.provenance if number == 1 else [],
                issues=result.issues,
                local=True,
            )
        )
    return pages


def _local(payloads, category, page_count):
    """A balanced local reconstruction or None.

    Added 2026-09-29: the deployed reconstruction raised on a page without date rows (``statistics``
    has no median to take), which the deployed pipeline reported as a page-count mismatch and failed
    the whole file; now only a real page-count mismatch fails it, anything else goes on to the schema
    extraction. A bank statement the deployed reconstruction cannot balance gets one more, stricter
    attempt anchored on the printed column headers (``header_bank``).
    """
    if rows_from_payloads(payloads)[3]["pages"] != page_count:
        raise ContentError("ocr.enterprise_page_count_mismatch")
    try:
        local = reconstruct(payloads, category, expected_pages=page_count)
    except (ValueError, ArithmeticError, LookupError):
        local = None
    if (local is None or not local.arithmetic_passed) and category == "bank":
        local = header_bank.from_payloads(payloads, page_count) or local
    return local if local is not None and local.arithmetic_passed else None


def _extract(model_call, image, mime, category, transcript, number):
    request = build_request(category, transcript)
    config = request["config"]
    response = model_call(ModelRequest(stage="enterprise_schema", page_number=number, attempt=0,
        model=request["model"], prompt=request["prompt"], images=((image, mime),),
        max_output_tokens=config["max_output_tokens"], timeout_s=MODEL_TIMEOUT_SECONDS,
        temperature=config["temperature"], response_mime=True,
        thinking=config["thinking_config"]["thinking_level"], response_schema=config["response_json_schema"]))
    if not response.text or "MAX_TOKENS" in (response.finish_reason or ""):
        raise ContentError("ocr.enterprise_schema_failed", page_number=number, detail="parse")
    try:
        data = json.loads(response.text)
    except (ValueError, TypeError):
        raise ContentError("ocr.enterprise_schema_failed", page_number=number, detail="parse") from None
    if not isinstance(data, dict):
        raise ContentError("ocr.enterprise_schema_failed", page_number=number, detail="parse")
    return data, response


def run(category: str, *, page_count: int, reads: Iterable[Tuple[bytes, str, int]],
        render: Callable[[int], Tuple[bytes, str]], document_ai_call, model_call) -> PipelineResult:
    """``reads``: the Document AI inputs (PDF chunks or the one image); ``render(n)``: page n's image."""
    if category not in ("bank", "gl", "vat") or page_count < 1:
        raise ContentError("ocr.enterprise_category_invalid", detail=str(category))
    started = time.monotonic()
    payloads, billed = [], 0
    for index, (content, mime, expected) in enumerate(reads, 1):
        body, _elapsed, _key = document_ai_call(content, mime, expected_pages=expected, page_number=index)
        payloads.append(body)
        billed += expected
    cost = billed * enterprise_pages.USD_PER_PAGE * THB_PER_USD
    pages = None
    if category in ("bank", "gl"):
        local = _local(payloads, category, page_count)
        if local is not None:
            pages = _local_pages(local, category)
    if pages is None:
        transcripts = [page.text for payload in payloads for page in enterprise_pages.page_texts(payload)]
        if len(transcripts) != page_count:
            raise ContentError("ocr.enterprise_transcript_count_mismatch")
        pages = []
        for number, transcript in enumerate(transcripts, 1):
            image, mime = render(number)
            data, response = _extract(model_call, image, mime, category, transcript, number)
            del image  # one rendered page at a time, not the whole scan in memory
            document = data
            for row in document.get("entries") or []:
                row["page"] = str(number)
            # Per-page arithmetic alone is not evidence of full-file correctness.
            issues = ["schema_transcription_requires_document_validation"]
            if not document.get("entries"):
                issues.append("no_transaction_rows")
            page = make_page(document, category, number, issues=issues)
            page.layer2_model = response.model
            page.layer2_ms = response.elapsed_ms
            page.layer2_input_tokens = response.input_tokens
            page.layer2_output_tokens = response.output_tokens
            cost += model_cost_thb(response.model, response.input_tokens, response.output_tokens)
            pages.append(page)
        if category in ("bank", "gl"):
            aggregate = {
                "opening_balance": pages[0].document.opening_balance,
                "closing_balance": pages[-1].document.closing_balance,
                "entries": [
                    {**row.model_dump(), "page": str(page.page_number)}
                    for page in pages
                    for row in page.document.entries
                ],
            }
            for page in pages:
                page.validation_warnings.extend(check_document(aggregate, category, page_count))
    elapsed = int((time.monotonic() - started) * 1000)
    return PipelineResult(pages=pages, page_count=page_count, elapsed_ms=elapsed, engine=ENGINE,
        estimated_cost_thb=cost)


def to_legacy(result: PipelineResult) -> dict:
    """The deployed ``pipeline_result_to_legacy_dict`` essentials the reconciliation decoders read."""
    return {
        "pages": [{"page_number": page.page_number,
                   "document": page.document.model_dump(mode="json") if page.document is not None else {},
                   "document_type": page.document_type,
                   "validation_warnings": list(page.validation_warnings),
                   "needs_manual_review": bool(page.needs_manual_review),
                   "extraction_audit": deepcopy(page.extraction_audit)} for page in result.pages],
        "_needs_review": any(page.needs_manual_review for page in result.pages),
        "engine": result.engine,
        "page_count": result.page_count,
        "estimated_cost_thb": result.estimated_cost_thb,
        "elapsed_ms": result.elapsed_ms,
    }
