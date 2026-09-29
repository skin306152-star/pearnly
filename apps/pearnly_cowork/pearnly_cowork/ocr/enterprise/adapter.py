"""Loss-aware adapter from the frozen extraction contract to business schemas."""

from __future__ import annotations

from copy import deepcopy

from datetime import date
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from pearnly_cowork.ocr.schema import ThaiInvoice, validate_invoice
from pearnly_cowork.ocr.schema.documents import (
    BankStatementDocument,
    GeneralLedgerDocument,
    VatReportDocument,
)

TYPES = {"bank": BankStatementDocument, "gl": GeneralLedgerDocument, "vat": VatReportDocument}
DOCUMENT_TYPES = {"bank": "bank_statement", "gl": "general_ledger", "vat": "vat_report"}


class PipelinePageResult(BaseModel):
    """The deployed page-result fields this pipeline fills, for the three financial document types.

    The platform's ``ocr.schema.PipelinePageResult`` was migrated for invoices only (its document type is
    ``auto``/``invoice`` and its document is always empty); the enterprise pages carry a statement, a
    ledger or a VAT report, so they get this shape with the same field names and defaults."""
    model_config = {"arbitrary_types_allowed": True}

    page_number: int = Field(..., ge=1)
    invoice: Optional[ThaiInvoice] = None
    document_type: Literal["bank_statement", "general_ledger", "vat_report"]
    document: Union[BankStatementDocument, GeneralLedgerDocument, VatReportDocument]
    layer_chain: List[str] = Field(default_factory=list)
    needs_manual_review: bool = False
    confidence_band: Literal["auto", "yellow_confirm", "needs_review"] = "auto"
    final_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_warnings: List[str] = Field(default_factory=list)
    extraction_audit: Dict[str, Any] = Field(default_factory=dict)
    layer1_ms: int = 0
    layer2_model: str = ""
    layer2_ms: int = 0
    layer2_input_tokens: int = 0
    layer2_output_tokens: int = 0


class PipelineResult(BaseModel):
    pages: List[PipelinePageResult]
    page_count: int = Field(..., ge=0)
    elapsed_ms: int = 0
    engine: str = ""
    estimated_cost_thb: float = 0.0


def _not_invoice():
    # The platform's ThaiInvoice is only built through validate_invoice with an explicit anchor year;
    # this placeholder carries no date, so the year only satisfies the contract (deployed: ThaiInvoice()).
    return validate_invoice({"is_not_invoice": True}, anchor_year=date.today().year)


def make_page(
    document: dict,
    category: str,
    page_number: int,
    *,
    audit=None,
    provenance=None,
    issues=None,
    local=False,
) -> PipelinePageResult:
    data = deepcopy(document)
    audit, provenance, issues = audit or [], provenance or [], issues or []
    data["document_type"] = DOCUMENT_TYPES[category]
    for row in data.get("entries") or []:
        if category == "gl":
            # Benchmark uses debit/credit; production recon uses deposit/withdrawal.
            row["direction"] = {"debit": "deposit", "credit": "withdrawal"}.get(
                row.get("direction"), row.get("direction", "")
            )
            row["raw_row_data"] = {
                **row.get("raw_row_data", {}),
                "enterprise_source": deepcopy(row),
            }
    review = bool(local or issues or audit)
    return PipelinePageResult(
        page_number=page_number,
        invoice=_not_invoice(),
        document_type=DOCUMENT_TYPES[category],
        document=TYPES[category].model_validate(data),
        layer_chain=["enterprise", "local_schema" if local else "schema38"],
        needs_manual_review=review,
        confidence_band="needs_review" if review else "yellow_confirm",
        # Do not fabricate a calibrated confidence score from arithmetic gates.
        final_confidence=0,
        validation_warnings=list(issues),
        extraction_audit={
            "contract": "enterprise-2026-09-03-v1",
            "local": local,
            "repairs": deepcopy(audit),
            "provenance": deepcopy(provenance),
        },
    )
