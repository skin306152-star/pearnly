"""Deterministic review-workbook admission; source units are invoices, not sheets."""
from copy import deepcopy

from pearnly_cowork.ocr.schema import Page
from pearnly_cowork.ocr.table_inputs import TableDocument, TableInputError
from pearnly_cowork.roundtrip.contracts import to_pipeline
from pearnly_cowork.roundtrip.parser import parse_roundtrip_workbook

ENGINE = "roundtrip_workbook"


def decode(content, original, *, anchor_year):
    parsed = parse_roundtrip_workbook(content)
    result = to_pipeline(parsed, anchor_year=anchor_year)
    if not 1 <= result.page_count <= 500:
        raise TableInputError("roundtrip.document_limit")
    evidence = deepcopy(parsed["provenance"])
    # Summary / extra sheets, hidden links, and other unconsumed cells must not
    # be unlocked merely because all parsed invoices share an authorized scope.
    evidence.update(version=1, reference_kind="logical_section", unit_kind="roundtrip_document",
        coverage_complete=False, coverage_issues=[{"code": "roundtrip_unassigned_workbook_content"}],
        workbook_evidence=deepcopy(original.provenance))
    pages = tuple(Page(page_number=p.page_number, width=0, height=0,
        full_text=p.model_dump_json(), avg_confidence=1, blocks=[]) for p in result.pages)
    return TableDocument(pages, result.page_count, ENGINE, original.billing_characters,
                         evidence, original.source_sha256), result
