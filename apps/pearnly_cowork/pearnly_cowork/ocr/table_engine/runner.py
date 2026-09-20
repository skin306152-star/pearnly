"""Pure table boundary over the existing invoice prompts and decision rules."""
from datetime import date
from typing import Callable, Protocol, Sequence

from pearnly_cowork.ocr.engine.contracts import EngineConfig, EngineResult, ModelCall
from pearnly_cowork.ocr.engine.errors import EngineError
from pearnly_cowork.ocr.engine.runner import page_text, run_text_pages
from pearnly_cowork.ocr.schema import Page
from pearnly_cowork.ocr.table_engine.roundtrip import has_roundtrip_sheet

MAX_INPUT_CHARACTERS = 30_000


class TableDocument(Protocol):
    """Structural contract: the runtime decoder owns bytes and source metadata."""
    pages: Sequence[Page]
    page_count: int
    provenance: dict


def validate_document(document: TableDocument) -> None:
    """Validate the whole document before a work/billing/provider call is admitted.

    Page numbers identify logical units only. No row splitting, physical-page
    remapping or hidden truncation is performed here.
    """
    pages = document.pages
    if (isinstance(document.page_count, bool) or not isinstance(document.page_count, int)
            or document.page_count < 1 or document.page_count != len(pages)):
        raise EngineError("ocr.table_units_invalid")
    if any(not isinstance(p, Page) or p.page_number != i for i, p in enumerate(pages, 1)):
        raise EngineError("ocr.page_association_invalid")
    if has_roundtrip_sheet(document.provenance):
        raise EngineError("ocr.table_roundtrip_not_supported")
    for page in pages:
        if len(page_text(page).strip()) > MAX_INPUT_CHARACTERS:
            raise EngineError("ocr.table_text_too_long", page_number=page.page_number)


def run_table(document: TableDocument, model_call: ModelCall, *, config: EngineConfig,
              anchor_year: int, today: date,
              fewshot_selector: Callable[[str], str] | None = None) -> EngineResult:
    """Sequential L2-only extraction with original raw calls and review decisions."""
    validate_document(document)
    return run_text_pages(tuple(document.pages), model_call, config=config,
                          anchor_year=anchor_year, today=today,
                          fewshot_selector=fewshot_selector)
