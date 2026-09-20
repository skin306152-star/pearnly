"""Customer-facing rates are projections of the actual billing policy."""
from pearnly_control import billing


def public_rates():
    return {"pdf_first_pages": billing.PDF_TIER1_LIMIT,
            "pdf_first_rate": float(billing.PDF_TIER1_PRICE),
            "pdf_after_rate": float(billing.PDF_TIER2_PRICE),
            "chars_per_unit": billing.EXCEL_CHARS_PER_SATANG,
            "char_rate": float(billing.EXCEL_SATANG_PRICE)}
