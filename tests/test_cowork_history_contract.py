"""Offline history parity with deployed ec2e5c957e6b; no Frappe, DB or OCR calls.

The frozen summary oracle below is the pure selection/projection in
services/ocr_history/mutations.py:21-80. Expected dates freeze
core/thai_date.py and routes/history_routes.py:193-215 at that same SHA.
Money type/precision and malformed JSON hardening are intentional differences.
"""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from itertools import product
import unittest

from pearnly_cowork.contracts import history


def legacy_summary(pages):
	"""Frozen legacy oracle, including truthiness, first-page and tie behavior."""
	def build(page):
		fields = page.get("fields") or {}
		invoice_date = None
		if fields.get("date"):
			try:
				candidate = str(fields["date"]).replace("/", "-")[:10]
				datetime.strptime(candidate, "%Y-%m-%d")
				invoice_date = candidate
			except Exception:
				pass
		total = None
		if fields.get("total_amount") is not None:
			try:
				total = float(str(fields["total_amount"]).replace(",", ""))
			except Exception:
				pass
		return {
			"invoice_no": (fields.get("invoice_number") or "")[:200] or None,
			"invoice_date": invoice_date,
			"seller_name": (fields.get("seller_name") or "")[:200] or None,
			"total_amount": total,
		}

	for page in pages:
		fields = page.get("fields") or {}
		if not page.get("is_copy") and not page.get("is_duplicate"):
			if fields.get("invoice_number") or fields.get("total_amount") or fields.get("seller_name"):
				return build(page)
	if pages:
		def score(page):
			fields = page.get("fields") or {}
			return sum(1 for key in ("invoice_number", "total_amount", "seller_name", "date") if fields.get(key))
		best = max(pages, key=score)
		if score(best) > 0:
			return build(best)
	return {"invoice_no": None, "invoice_date": None, "seller_name": None, "total_amount": None}


class SummaryParity(unittest.TestCase):
	def test_summary_differential_over_copy_flags_field_presence_and_zero(self):
		variants = [{}, {"total_amount": 0}, {"date": "2026/09/20"}]
		for invoice, seller, amount, raw_date in product(
			(None, "I-01"), (None, "Seller"), (None, 0, "0.00", "1,234.50", "bad"),
			(None, "2026-09-20", "2026/2/3", "not-a-date"),
		):
			variants.append({"invoice_number": invoice, "seller_name": seller, "total_amount": amount, "date": raw_date})
		for fields, is_copy, is_duplicate in product(variants, (False, True), (False, True)):
			pages = [
				{"fields": fields, "is_copy": is_copy, "is_duplicate": is_duplicate},
				{"fields": {"seller_name": "Second", "total_amount": "75.25"}},
			]
			with self.subTest(fields=fields, is_copy=is_copy, is_duplicate=is_duplicate):
				old = legacy_summary(pages)
				new = history.extract_summary_fields(pages)
				for key in ("invoice_no", "seller_name", "invoice_date"):
					self.assertEqual(new[key], old[key])
				if old["total_amount"] is None:
					self.assertIsNone(new["total_amount"])
				else:
					self.assertEqual(Decimal(new["total_amount"]), Decimal(str(old["total_amount"])))

	def test_all_duplicate_fallback_is_stable_first_highest_score(self):
		pages = [
			{"is_duplicate": True, "fields": {"invoice_number": "FIRST", "total_amount": "12.40"}},
			{"is_copy": True, "fields": {"invoice_number": "SECOND", "seller_name": "Seller"}},
		]
		self.assertEqual(history.extract_summary_fields(pages)["invoice_no"], "FIRST")
		self.assertEqual(history.extract_summary_fields(pages)["total_amount"], "12.40")

	def test_zero_alone_retains_legacy_no_selected_page_semantics(self):
		self.assertEqual(history.extract_summary_fields([{"fields": {"total_amount": 0}}]), legacy_summary([{"fields": {"total_amount": 0}}]))
		self.assertEqual(history.extract_summary_fields([{"fields": {"total_amount": "0"}}])["total_amount"], "0")

	def test_summary_uses_invoice_number_not_unregistered_alias_and_truncates_only_projection(self):
		pages = [{"fields": {"invoice_no": "NOT-AN-ALIAS", "seller_name": "s" * 250, "invoice_number": "n" * 220}}]
		result = history.initial_history(pages)
		self.assertEqual(result["seller_name"], "s" * 200)
		self.assertEqual(result["invoice_no"], "n" * 200)
		self.assertEqual(result["pages"], pages)
		self.assertIsNone(history.extract_summary_fields([{"fields": {"invoice_no": "UNUSED"}}])["invoice_no"])

	def test_exact_decimal_is_explicit_new_api_difference_from_legacy_float(self):
		pages = [{"fields": {"total_amount": "9,007,199,254,740,993.01"}}]
		self.assertEqual(history.extract_summary_fields(pages)["total_amount"], "9007199254740993.01")
		self.assertNotEqual(Decimal(str(legacy_summary(pages)["total_amount"])), Decimal("9007199254740993.01"))

	def test_nonfinite_text_amount_is_not_a_valid_summary_amount_but_raw_value_survives(self):
		for amount in ("NaN", "sNaN", "Infinity", "-Infinity", "bad", ""):
			with self.subTest(amount=amount):
				result = history.initial_history([{"fields": {"total_amount": amount}}])
				self.assertIsNone(result["total_amount"])
				self.assertEqual(result["pages"][0]["fields"]["total_amount"], amount)

	def test_quality_status_uses_null_not_truthiness_and_never_commit(self):
		for confidence, summary, expected in (
			("high", {}, "failed"),
			("high", {"invoice_date": "2026-01-01"}, "failed"),
			("high", {"seller_name": "Seller"}, "pending"),
			("high", {"invoice_no": "A", "total_amount": "0"}, "confirmed"),
			("auto", {"invoice_no": "A", "total_amount": "0"}, "pending"),
			("HIGH", {"invoice_no": "A", "total_amount": "0"}, "pending"),
			("low", {"invoice_no": "A", "total_amount": "10"}, "pending"),
		):
			with self.subTest(confidence=confidence, summary=summary):
				self.assertEqual(history.list_status(confidence, summary), expected)


class PagesAndDates(unittest.TestCase):
	def test_complete_evidence_survives_without_total_recalculation_or_generic_cleanup(self):
		pages = [{
			"page_index": 5, "page_number": 5, "is_copy": True, "is_duplicate": False,
			"fields": {"subtotal": "100", "vat": "7", "total_amount": "98", "wht_amount": "3",
				"discount": "6", "_direction": "sales", "items": [{"quantity": 0, "_product_code": "001"}]},
			"provenance": {"ocr": ["fragment", None, False, 0]},
		}, {"fields": None, "is_duplicate": True}]
		result = history.prepare_pages(pages)
		self.assertEqual(result, pages)
		result[0]["fields"]["items"][0]["quantity"] = 88
		self.assertEqual(pages[0]["fields"]["items"][0]["quantity"], 0)

	def test_incomplete_and_failed_drafts_have_no_new_business_required_fields(self):
		for pages in ([], [{}], [{"fields": {}}], [{"fields": {"items": []}}]):
			with self.subTest(pages=pages):
				result = history.initial_history(pages)
				self.assertEqual(result["pages"], pages)
				self.assertEqual(result["list_status"], "failed")

	def test_printed_date_vectors_match_deployed_thai_date_rules(self):
		for raw, expected in (
			("31/5/2569", "2026-05-31"), ("2569-5-31", "2026-05-31"),
			("2026-05-31", "2026-05-31"), ("31.5.2026", "2026-05-31"),
			("31/5/69", "2026-05-31"), ("31/5/26", "2026-05-31"),
			("31 พ.ค. 2569", "2026-05-31"), ("31 พค 69", "2026-05-31"),
			("31 พฤษภาคม 2569", "2026-05-31"), ("31 May, 2026", "2026-05-31"),
			("29/2/2567", "2024-02-29"), ("1/1/2400", "1857-01-01"),
			("28 05 2026", "2026-05-28"), ("28 5 69", "2026-05-28"), ("28 / 05 / 2569", "2026-05-28"),
		):
			with self.subTest(raw=raw):
				pages = [{"fields": {"date_raw": raw, "date": "1999-01-01"}}]
				result = history.prepare_pages(pages, anchor_year=2026)
				self.assertEqual(result[0]["fields"]["date"], expected)
				self.assertEqual(result[0]["fields"]["date_raw"], raw)
				self.assertEqual(pages[0]["fields"]["date"], "1999-01-01")

	def test_two_digit_year_requires_explicit_runtime_anchor(self):
		pages = [{"fields": {"date_raw": "31/5/69"}}]
		with self.assertRaisesRegex(history.HistoryContractError, "history.date_anchor_required"):
			history.prepare_pages(pages)
		self.assertEqual(history.prepare_pages(pages, anchor_year=2070)[0]["fields"]["date"], "2069-05-31")
		for invalid in (True, "2026", 0, 10000):
			with self.subTest(invalid=invalid), self.assertRaisesRegex(history.HistoryContractError, "history.date_anchor_invalid"):
				history.prepare_pages(pages, anchor_year=invalid)

	def test_invalid_printed_or_unexplained_buddhist_date_rejects_whole_preparation(self):
		for fields in ({"date_raw": "29/2/2569"}, {"date_raw": "nonsense"}, {"date": "2569-05-31"}, {"date_raw": " ", "date": "2569-05-31"}):
			with self.subTest(fields=fields), self.assertRaisesRegex(history.HistoryContractError, "history.date_unreadable"):
				history.prepare_pages([{"fields": fields}], anchor_year=2026)

	def test_recognized_ingest_keeps_the_model_date_when_the_printed_date_is_unreadable(self):
		# 旧线上识别入库不因票面日期写法认不出而整批失败：保留模型给的合法公历日期、原文照存待复核。
		cases = ((" 28-May-26 ", "2026-05-28", "2026-05-28"), ("nonsense", "2026-02-30", None),
		         ("nonsense", "2569-05-28", None), ("nonsense", None, None), ("29/2/2569", "2026-03-01", "2026-03-01"))
		for raw, model, expected in cases:
			with self.subTest(raw=raw, model=model):
				pages = [{"fields": {"date_raw": raw, "date": model}}]
				result = history.initial_history(pages, source="cowork_line", anchor_year=2026, recognized=True)
				self.assertEqual(result["pages"][0]["fields"]["date"], expected)
				self.assertEqual(result["pages"][0]["fields"]["date_raw"], raw)
				with self.assertRaisesRegex(history.HistoryContractError, "history.date_unreadable"):
					history.initial_history(pages, anchor_year=2026)
		readable = [{"fields": {"date_raw": "31/5/2569", "date": "1999-01-01"}}]
		self.assertEqual(history.prepare_pages(readable, anchor_year=2026, recognized=True)[0]["fields"]["date"], "2026-05-31")

	def test_without_printed_date_legacy_value_is_preserved_and_only_summary_may_be_null(self):
		pages = [{"fields": {"date": "not-a-date", "invoice_number": "I"}}]
		result = history.initial_history(pages)
		self.assertEqual(result["pages"], pages)
		self.assertIsNone(result["invoice_date"])

	def test_structural_json_errors_are_explicit_instead_of_downstream_crashes(self):
		for pages in (None, {}, "[]", [None], ["page"], [{"fields": []}], [{"fields": {"value": float("nan")}}], [{1: "not a JSON key"}]):
			with self.subTest(pages=pages), self.assertRaises(history.HistoryContractError):
				history.prepare_pages(pages)

	def test_page_association_is_original_pdf_one_based_not_preview_offsets(self):
		pages = [{"page_index": 5, "page_number": 99}, {"page_index": 2}, {}, {"page_index": 5}]
		self.assertEqual(history.source_page_indices(pages), [2, 3, 5, 5])
		self.assertNotIn("page_index", pages[2])
		for invalid in (0, -1, "2", None, True, 2.5):
			with self.subTest(invalid=invalid), self.assertRaisesRegex(history.HistoryContractError, "history.page_index_invalid"):
				history.source_page_indices([{"page_index": invalid}])


class FirstSaveAndCommit(unittest.TestCase):
	def test_only_manual_can_be_publicly_created(self):
		for source in history.SOURCES:
			self.assertEqual(history.initial_history([], source=source)["source"], source)
			if source != "manual":
				with self.assertRaisesRegex(history.HistoryContractError, "history.source_not_public"):
					history.initial_history([], source=source, public_create=True)
		self.assertEqual(history.initial_history([], public_create=True)["source"], "manual")
		for source in ("erp_web", "line_erp"):
			with self.subTest(source=source), self.assertRaisesRegex(history.HistoryContractError, "history.erp_source_forbidden"):
				history.initial_history([], source=source)
		for source in (None, "", "email", "line", "upload", "Manual", " manual "):
			with self.subTest(source=source), self.assertRaisesRegex(history.HistoryContractError, "history.source_invalid"):
				history.initial_history([], source=source)

	def test_posting_kind_matches_source_normalization_without_default(self):
		for value, expected in ((" STOCK ", "stock"), ("Service", "service"), (None, None), ("", None), ("goods", None), (False, None), (17, None)):
			with self.subTest(value=value):
				self.assertEqual(history.normalize_posting_kind(value), expected)
		self.assertIsNone(history.initial_history([])["posting_kind"])

	def test_raw_evidence_keeps_original_type_and_independent_snapshot(self):
		pages = [{"fields": {"date_raw": "31/5/2569", "total_amount": "12"}}]
		for raw in (None, "raw provider text", {"provider": [{"amount": "12"}]}, [{"fields": {"old": True}}], "", []):
			with self.subTest(raw=raw):
				result = history.initial_history(pages, ai_raw=raw)
				expected = pages if raw is None else raw
				self.assertEqual(result["ai_raw"], expected)
				self.assertIsInstance(result["ai_raw"], type(expected))
				before = deepcopy(result["ai_raw"])
				result["pages"][0]["fields"]["total_amount"] = "999"
				self.assertEqual(result["ai_raw"], before)
		self.assertNotIn("date", pages[0]["fields"])

	def test_commit_only_turns_staged_false_and_is_idempotent(self):
		draft = history.initial_history([{"fields": {"invoice_number": "A", "total_amount": "0"}}], confidence="high")
		draft.update({"push_status": "unknown", "edit_count": 3, "custom": {"keep": [1]}})
		before = deepcopy(draft)
		result = history.commit_history(draft)
		self.assertEqual(result, {**draft, "staged": False})
		self.assertEqual(draft, before)
		self.assertEqual(history.commit_history(result), result)
		self.assertEqual(result["list_status"], "confirmed")
		self.assertEqual(result["push_status"], "unknown")
		result["custom"]["keep"].append(2)
		self.assertEqual(draft["custom"]["keep"], [1])

	def test_summary_import_cleanup_is_source_specific_and_preserves_explicit_direction(self):
		for explicit, expected in ((None, "sales"), ("", "sales"), ("purchase", "purchase")):
			fields = {"_direction": " sales ", "direction": explicit, "_walkin": True, "_product_code": "1", "items": [{"_product_code": "2"}], "total_amount": "5"}
			result = history.clean_summary_fields(fields)
			self.assertEqual(result["direction"], expected)
			self.assertNotIn("_direction", result)
			self.assertNotIn("_walkin", result)
			self.assertNotIn("_product_code", result)
			self.assertEqual(result["items"], [{"_product_code": "2"}])
			self.assertEqual(history.prepare_pages([{"fields": fields}])[0]["fields"], fields)
			result["items"][0]["_product_code"] = "changed"
			self.assertEqual(fields["items"][0]["_product_code"], "2")


if __name__ == "__main__":
	unittest.main()
