"""Physical-page evidence and recognition quality from deployed ec2e5c957e6b.

Sources: services/ocr/legacy_adapter.py, recognize/core.py:44-62,287-345,
recognize/persist.py:178-186 and entrypoints.py:187-197.
The adapter consumes validated pipeline value objects with model_dump(), not
unvalidated provider JSON. It performs no OCR, schema validation or grouping.
"""

from copy import deepcopy


def _invoice_fields(invoice) -> dict:
	fields = invoice.model_dump(mode="json")
	fields.pop("additional_invoices", None)
	if not fields.get("payment_received"):
		fields.pop("payment_received", None)
	tax_ids = []
	if invoice.seller_tax:
		tax_ids.append(invoice.seller_tax)
	if invoice.buyer_tax and invoice.buyer_tax not in tax_ids:
		tax_ids.append(invoice.buyer_tax)
	fields["tax_ids"] = tax_ids
	return fields


def _page_entry(page, invoice=None, *, additional=False) -> dict:
	invoice = page.invoice if invoice is None else invoice
	entry = {
		"page_number": page.page_number, "page": page.page_number, "page_index": page.page_number,
		"text": "", "lines": [], "fields": _invoice_fields(invoice),
		"is_copy": bool(invoice.is_copy_or_duplicate), "is_duplicate": False,
		"_multi_invoice_sibling": bool(additional),
		"elapsed_ms": int(page.total_ms),
		"input_tokens": int(page.layer2_input_tokens + page.layer3_input_tokens),
		"output_tokens": int(page.layer2_output_tokens + page.layer3_output_tokens),
		"error": page.error, "_layer_chain": list(page.layer_chain),
		"_trigger_reasons": list(page.trigger_reasons),
		"_needs_manual_review": bool(page.needs_manual_review),
		"_layer1_avg_confidence": float(page.layer1_avg_confidence),
		"_document_type": str(page.document_type), "_confidence_band": str(page.confidence_band),
		"_final_confidence": float(page.final_confidence),
		"_validation_warnings": list(page.validation_warnings),
		"_extraction_audit": page.extraction_audit,
	}
	if page.declared_direction:
		entry["fields"]["direction"] = page.declared_direction
	if page.source_history_id:
		entry["fields"]["history_id"] = page.source_history_id
	if page.document is not None:
		entry["document"] = page.document.model_dump(mode="json")
	return entry


def flatten_pipeline_pages(pages) -> list[dict]:
	"""Expand same-page invoices while retaining their original 1-based page ID.

	Extra invoices repeat page-level diagnostics, timings and token counts in
	legacy history. Never sum this expanded list to derive physical pages or
	provider usage; use the unexpanded pipeline result for those measures.
	"""
	result = []
	for page in pages:
		result.append(_page_entry(page))
		for invoice in page.invoice.additional_invoices or []:
			result.append(_page_entry(page, invoice, additional=True))
	return deepcopy(result)


def aggregate_confidence_band(pages) -> str:
	priority = {"needs_review": 2, "yellow_confirm": 1, "auto": 0}
	worst = "auto"
	for page in pages:
		if priority.get(page.confidence_band, 0) > priority[worst]:
			worst = page.confidence_band
	return worst


def page_confidence(page: dict) -> int:
	fields = page.get("fields", {}) or {}
	score = sum(bool(fields.get(key)) for key in ("invoice_number", "date", "total_amount"))
	score += bool(fields.get("seller_name") or fields.get("seller_tax"))
	score += bool(fields.get("buyer_name") or fields.get("buyer_tax"))
	if fields.get("items") or (fields.get("subtotal") and fields.get("vat")):
		score += 2
	return score


def assess_confidence(pages: list[dict]) -> dict:
	"""Legacy duplicate marking and highest-page quality, on an independent copy."""
	pages = deepcopy(pages)
	seen, primary = set(), []
	for page in pages:
		number = (page.get("fields") or {}).get("invoice_number")
		if page.get("is_copy", False) or (number and number in seen):
			page["is_duplicate"] = True
			continue
		if number:
			seen.add(number)
		page["is_duplicate"] = False
		primary.append(page)
	if not primary and pages:
		unique, seen = [], set()
		for page in pages:
			number = (page.get("fields") or {}).get("invoice_number")
			if number and number in seen:
				continue
			if number:
				seen.add(number)
			unique.append(page)
		if unique:
			best = max(unique, key=page_confidence)
			best["is_duplicate"] = False
			primary.append(best)
	maximum = max((page_confidence(page) for page in primary), default=0)
	return {"pages": pages, "confidence": "high" if maximum >= 6 else "medium" if maximum >= 3 else "low"}


def restore_total_before_withholding(pages: list[dict]) -> int:
	"""读出了预扣税额、合计却是扣了预扣税之后的实付额（未税 + 税额 − 预扣 = 合计）：合计改回扣前的含税总额（票面
	「จำนวนเงินรวมทั้งสิ้น」，会计登记与金标都按它），预扣额照旧单独存。回改了几张。

	2026-10-02 金标评测（零费用重放已存结果）：第一批改 5 张全对、扩充批改 17 张合计错 28 → 14，其余字段不变。"""
	from decimal import Decimal, InvalidOperation

	def money(value):
		try:
			return Decimal(str(value).replace(",", "").strip()) if value not in (None, "") else None
		except InvalidOperation:
			return None

	changed = 0
	for page in pages or []:
		fields = (page or {}).get("fields") or {}
		subtotal, vat, total, withheld = (money(fields.get(key)) for key in ("subtotal", "vat", "total_amount", "wht_amount"))
		if withheld and withheld > 0 and None not in (subtotal, vat, total) and abs(subtotal + vat - withheld - total) <= Decimal("0.02"):
			fields["total_amount"] = f"{subtotal + vat:.2f}"
			changed += 1
	return changed


def all_pages_not_invoice(pages: list[dict]) -> bool:
	if not pages:
		return False
	for page in pages:
		value = ((page or {}).get("fields") or {}).get("is_not_invoice")
		if isinstance(value, str):
			value = value.strip().lower() == "true"
		if not value:
			return False
	return True


def history_pages(group: dict) -> list[dict]:
	"""Save every source page; only the first page receives the merged fields.

	Input is a source grouper result: source_pages plus invoice_fields. This
	function does not decide invoice boundaries or recompute money.
	"""
	pages = deepcopy(group["source_pages"])
	if pages and group.get("invoice_fields"):
		pages[0]["fields"] = deepcopy(group["invoice_fields"])
	return pages
