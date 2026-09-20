"""Pure deterministic workbook-to-pipeline conversion; no IO or authorization."""
from copy import deepcopy
from decimal import Decimal

from pearnly_cowork.ocr.schema import PipelinePageResult, PipelineResult, validate_invoice

ENGINE = "roundtrip_workbook"


class RoundtripParseError(ValueError):
    def __init__(self, code, **details):
        self.code = code
        self.details = details
        super().__init__(code)


class RoundtripNotMatched(RoundtripParseError):
    def __init__(self):
        super().__init__("ocr.roundtrip_not_matched")


def _s(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number_text(value):
    """Preserve the old _num float serialization, without float money arithmetic."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise RoundtripParseError("ocr.roundtrip_invalid_number")
        return _s(float(value))
    return _s(value)


def _invoice(fields, anchor_year):
    return validate_invoice({
        "invoice_number": _s(fields.get("invoice_number")) or None,
        "date": _s(fields.get("date")) or None,
        **{key: _s(fields.get(key)) for key in ("seller_name", "seller_tax", "seller_branch",
            "buyer_name", "buyer_tax", "buyer_branch", "category")},
        "subtotal": _s(fields.get("amount_before_vat")),
        "vat": _s(fields.get("vat_amount")),
        "total_amount": _s(fields.get("total_amount")) or None,
        "notes": _s(fields.get("pending_reason")),
        "items": [{"name": _s(item.get("description")), "qty": _number_text(item.get("qty")),
                   "price": _number_text(item.get("unit_price")), "subtotal": _s(item.get("amount"))}
                  for item in (fields.get("items") or []) if isinstance(item, dict)],
    }, anchor_year=anchor_year)


def _audit_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _audit_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_audit_value(item) for item in value]
    return value


def to_pipeline(parsed, *, anchor_year: int) -> PipelineResult:
    """Original documents-first/pending-last order, one logical unit per invoice.

    Source history IDs are untrusted references; the caller must authorize them.
    Reader fields remain in extraction_audit, including ERP codes absent from the
    old ThaiInvoice model. No mutation or implicit grouping occurs here.
    """
    validate_invoice({}, anchor_year=anchor_year)
    pages = []
    for pending, entries in ((False, parsed["documents"]), (True, parsed["pending"])):
        for entry in entries:
            fields = deepcopy(entry["fields"])
            if pending:
                fields["pending_reason"] = str(entry.get("reason") or "").strip() or "ยังไม่ได้จำแนกทิศทาง"
            pages.append(PipelinePageResult(
                page_number=len(pages) + 1, invoice=_invoice(fields, anchor_year), document_type="invoice",
                layer_chain=[ENGINE], layer1_avg_confidence=1.0, needs_manual_review=pending,
                declared_direction=_s(fields.get("direction")), source_history_id=_s(fields.get("history_id")),
                extraction_audit={"roundtrip": {
                    "sheet": entry["sheet"], "source_rows": deepcopy(entry["source_rows"]),
                    "source_row_keys": deepcopy(entry.get("source_row_keys", [])),
                    "reader_fields": _audit_value(fields), "pending": pending,
                }},
            ))
    if not pages:
        raise RoundtripParseError("ocr.roundtrip_empty")
    return PipelineResult(pages=pages, page_count=len(pages), engine=ENGINE,
                          elapsed_ms=0, estimated_cost_thb=0)
