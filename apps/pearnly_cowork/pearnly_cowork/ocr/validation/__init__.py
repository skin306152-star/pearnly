"""Pure document gates; callers own provider dispatch and durable evidence.

The source-column checks cover all three document types the deployed validator did
(invoice, general ledger, bank statement) and share one set of column keyword
tables, so the same printed header is judged the same way everywhere.
"""
from pearnly_cowork.ocr.validation.confidence import check_field_in_l1_text, find_field_min_word_conf
from pearnly_cowork.ocr.validation.dates import validate_invoice_date
from pearnly_cowork.ocr.validation.gates import (
    aggregate_page_confidence, bucket_confidence, count_invoice_no_candidates,
    evaluate_triggers, evaluate_soft_flags, field_confidences,
)
from pearnly_cowork.ocr.validation.math import check_amount_math
from pearnly_cowork.ocr.validation.sanity import (
    DISCOUNT_INFERRED_PREFIX, credit_note_review_reason, evaluate_sanity,
    infer_missing_discount, line_sum_mismatch, vat_inclusive_lines, vat_ratio_mismatch,
)
from pearnly_cowork.ocr.validation.sources import (
    validate_bank_document, validate_gl_document, validate_invoice_sources,
)
