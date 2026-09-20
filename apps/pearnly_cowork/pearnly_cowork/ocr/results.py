"""Pure whole-result preparation for native history.recognition.persist_batch.

Only validated, complete source-unit results belong here. The caller supplies
the independently verified physical-page or logical-section count and explicit
workspace/source channel. The persistence service checks its frozen manifest.
This module performs no permission decision, persistence, model call or charge.
"""
from collections import Counter
from copy import deepcopy

from pearnly_cowork.contracts.history import normalize_source
from pearnly_cowork.contracts.push import apply_batch_direction, normalize_direction, normalize_posting_kind
from pearnly_cowork.ocr.grouping import group_invoices
from pearnly_cowork.ocr.pages import (
    aggregate_confidence_band, all_pages_not_invoice, assess_confidence,
    flatten_pipeline_pages, history_pages,
)
from pearnly_cowork.ocr.schema.results import PipelineResult


class OcrResultError(ValueError):
    def __init__(self, code, **details):
        self.code = code
        self.details = details
        super().__init__(code)


_L2_REVIEW_FALLBACKS = frozenset({
    "L3_FAILED", "L3_QUOTA", "L3_TRANSIENT", "L3_SKIPPED_BUDGET", "L3_TOTALS_RESCUE", "IF_FAILED",
})


def _has_retained_result(invoice):
    # The source persistence cache calls number / amount / seller a usable
    # recognition. An explicit noninvoice classification is also a result.
    return bool(invoice.is_not_invoice or str(invoice.invoice_number or "").strip()
                or (invoice.total_amount is not None and str(invoice.total_amount).strip())
                or str(invoice.seller_name or "").strip()
                or any(not child.is_not_invoice and _has_retained_result(child)
                       for child in invoice.additional_invoices or []))


def _require_complete(result, expected_page_count):
    if not isinstance(result, PipelineResult):
        raise OcrResultError("ocr.result_unvalidated")
    if type(expected_page_count) is not int or expected_page_count < 1:
        raise OcrResultError("ocr.expected_pages_invalid")
    physical = [page.page_number for page in result.pages]
    counts = Counter(physical)
    expected = set(range(1, expected_page_count + 1))
    missing, unexpected = sorted(expected - set(physical)), sorted(set(physical) - expected)
    duplicates = sorted(number for number, count in counts.items() if count > 1)
    failed, review_pages = [], set()
    for page in result.pages:
        chain = {str(stage).upper() for stage in page.layer_chain}
        hard_failure = any(stage == "FAILED" or (stage.endswith("_FAILED") and stage not in _L2_REVIEW_FALLBACKS)
                           for stage in chain)
        fallback = bool(chain & _L2_REVIEW_FALLBACKS)
        # A schema-valid empty object is still an unrecognized physical page.
        # Do not seal an empty History or charge it merely because L3 returned 200.
        if not _has_retained_result(page.invoice) or hard_failure or ((page.error or fallback) and
                            ("L2" not in chain or not _has_retained_result(page.invoice))):
            failed.append(page.page_number)
        elif page.error or fallback or page.needs_manual_review or page.confidence_band == "needs_review":
            review_pages.add(page.page_number)
    if result.page_count != expected_page_count or missing or unexpected or duplicates or failed:
        raise OcrResultError("ocr.result_incomplete", expected_page_count=expected_page_count,
            reported_page_count=result.page_count, missing_pages=missing, unexpected_pages=unexpected,
            duplicate_pages=duplicates, failed_pages=sorted(failed))
    return review_pages


def prepare_history_batch(result: PipelineResult, *, expected_page_count: int, workspace: str,
                          source: str, client=None, posting_kind=None, direction=None,
                          reference_kind="physical_page") -> dict:
    """Prepare groups for persist_batch; a result is never a billing permission.

    ``has_billable_result`` only distinguishes an invoice result from a complete
    all-not-invoice result. Balance, pricing, exemption, idempotency and charging
    after successful persistence remain the later authorized workflow's job.

    All groups use the explicitly supplied scope. Automatic company assignment,
    archive naming, accounting categories and duplicate DB lookup are not part
    of this pure slice. The persistence service rechecks every assigned scope.
    """
    source = normalize_source(source)
    if reference_kind not in {"physical_page", "logical_section"}:
        raise OcrResultError("ocr.reference_kind_invalid")
    references = {"reference_kind": reference_kind, "reference_count": expected_page_count,
                  "physical_page_count": expected_page_count if reference_kind == "physical_page" else None}
    if (not isinstance(workspace, str) or not workspace.strip()
            or (client is not None and (not isinstance(client, str) or not client.strip()))):
        raise OcrResultError("ocr.scope_required")
    review_pages = _require_complete(result, expected_page_count)
    raw = flatten_pipeline_pages(result.pages)
    # A recoverable L3 / image-first failure keeps its validated L2 evidence.
    # Preserve the source error and force review, including every invoice on a
    # multi-invoice physical page, without mutating the caller's result model.
    for page in raw:
        if page["page_index"] in review_pages:
            page["_needs_manual_review"] = True
            page["_confidence_band"] = "needs_review"
    band = "needs_review" if review_pages else aggregate_confidence_band(result.pages)
    # Preserve the old whole-batch noninvoice gate. In a mixed batch, do not
    # silently discard noninvoice pages; grouping retains their source evidence.
    if all_pages_not_invoice(raw):
        return {"state": "not_invoice", "groups": [], "raw_pages": raw, "invoice_count": 0,
                **references, "confidence": None,
                "confidence_band": band, "requires_review": bool(review_pages), "has_billable_result": False}
    assessed = assess_confidence(raw)
    groups = group_invoices(assessed["pages"])
    prepared = []
    declared_direction = normalize_direction(direction)
    for group in groups:
        apply_batch_direction(group["invoice_fields"], declared_direction)
        first_final_pages = history_pages(group)
        prepared.append({"workspace": workspace, "client": client, "source": source,
            "source_page_indices": list(group["page_indices"]), "pages": first_final_pages,
            # Legacy insert_ocr_history defaulted ai_raw to the first saved
            # pages, including merged fields and the retained physical pages.
            "ai_raw": deepcopy(first_final_pages), "confidence": assessed["confidence"],
            "posting_kind": normalize_posting_kind(posting_kind)})
    return {"state": "review_required" if review_pages else "ready", "groups": prepared,
            "raw_pages": raw, "invoice_count": len(prepared),
            **references, "confidence": assessed["confidence"],
            "confidence_band": band, "requires_review": bool(review_pages), "has_billable_result": bool(prepared)}
