"""Shared footer-total predicate from fixed legacy bank_table_io; no IO."""
SUMMARY_ROW_KEYWORDS = (
    "total credit",
    "total debit",
    "total deposit",
    "total withdrawal",
    "total transaction",
    "grand total",
    "subtotal",
    "sub total",
    "รวมรายการ",
    "ยอดรวม",
    "รวมยอด",
    "รวมเงิน",
    "总计",
    "合计",
    "小计",
    "本期合计",
    "累计",
)


def is_summary_row(description: str) -> bool:
	"""Deployed footer-total detection; an empty description is not a summary row."""
	value = (description or "").strip().lower()
	if not value:
		return False
	return any(keyword in value for keyword in SUMMARY_ROW_KEYWORDS)
