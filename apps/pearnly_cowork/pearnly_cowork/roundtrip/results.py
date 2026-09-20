"""Keep the review workbook's deterministic invoice boundaries through saving."""
from copy import deepcopy

from pearnly_cowork.ocr.pages import assess_confidence, flatten_pipeline_pages


def prepare_groups(result, *, workspace, client, source, posting_kind):
    groups = []
    for page, raw in zip(result.pages, flatten_pipeline_pages(result.pages), strict=True):
        # Assess each parsed document independently. Same invoice numbers across
        # keys/sheets must not merge or mark the second real invoice a copy.
        assessed = assess_confidence([raw])
        final = assessed["pages"]
        if page.needs_manual_review:
            final[0]["_needs_manual_review"] = True
            final[0]["_confidence_band"] = "needs_review"
            final[0]["fields"].pop("direction", None)
        groups.append({"workspace": workspace, "client": client, "source": source,
            "source_page_indices": [page.page_number], "pages": final,
            "ai_raw": deepcopy(final), "confidence": assessed["confidence"],
            "posting_kind": posting_kind})
    return groups
