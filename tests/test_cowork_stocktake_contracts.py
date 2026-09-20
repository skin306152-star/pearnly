"""Offline stock count contracts from deployed ec2e5c957e6b; no Site or I/O adapters."""

import copy
import unittest
from decimal import Decimal
from itertools import repeat

from pearnly_cowork.stocktake import calculations, contracts


def cells(values):
	return [contracts.ImportCell(value, "s" if isinstance(value, str) else "n") for value in values]


ROW = ["0012", "Product", "000012345678", "A", "01", "EA", "2.500001"]


class QuantityContract(unittest.TestCase):
	def test_source_precision_range_and_sign_rules(self):
		for value, expected in (
			("2.500001", "2.500001"), (" 0 ", "0"), ("-2", "-2"),
			("99999999999999.999999", "99999999999999.999999"), ("1e-6", "0.000001"),
		):
			with self.subTest(value=value):
				self.assertEqual(contracts.quantity(value), Decimal(expected))
				self.assertIsInstance(contracts.quantity(value), Decimal)

	def test_invalid_quantities_never_round_or_coerce_to_zero(self):
		for value in (None, True, False, "", " ", "NaN", "sNaN", "Infinity", "-Infinity",
			"1.0000001", "100000000000000", "-100000000000000", "1e99", "1,000", "oops"):
			with self.subTest(value=value), self.assertRaisesRegex(
				contracts.StocktakeValidationError, "^stocktake.quantity_invalid$"
			):
				contracts.quantity(value)

	def test_count_is_nonnegative_but_book_snapshot_can_be_negative(self):
		self.assertEqual(contracts.quantity("-1"), Decimal("-1"))
		self.assertEqual(contracts.quantity("0", nonnegative=True), Decimal("0"))
		with self.assertRaises(contracts.StocktakeValidationError):
			contracts.quantity("-0.000001", nonnegative=True)


class SnapshotImportContract(unittest.TestCase):
	def test_legacy_layout_preserves_identifier_text_and_exact_quantity(self):
		rows = contracts.validate_import_rows(contracts.FIELDS, [cells(ROW)])
		self.assertEqual(rows[0]["product_code"], "0012")
		self.assertEqual(rows[0]["barcode"], "000012345678")
		self.assertEqual(rows[0]["book_qty"], Decimal("2.500001"))
		self.assertEqual(rows[0]["warehouse"], "A")

	def test_all_six_deployed_header_layouts(self):
		item = dict(zip(contracts.FIELDS, ROW))
		for headers, layout in (
			(contracts.FIELDS, contracts.FIELDS),
			(contracts.LEGACY_THAI_HEADERS, contracts.FIELDS),
			(contracts.TEMPLATE_FIELDS, contracts.TEMPLATE_FIELDS),
			(contracts.TEMPLATE_HEADERS, contracts.TEMPLATE_FIELDS),
			(contracts.TEMPLATE_FIELDS[:5], contracts.TEMPLATE_FIELDS[:5]),
			(contracts.TEMPLATE_HEADERS[:5], contracts.TEMPLATE_FIELDS[:5]),
		):
			with self.subTest(headers=headers):
				row = contracts.validate_import_rows(headers, [cells([item[key] for key in layout])])[0]
				self.assertEqual(row["book_qty"], Decimal("2.500001"))
				self.assertEqual(row["warehouse"], "A" if len(layout) == 7 else "")
				self.assertEqual(row["location"], "01" if len(layout) == 7 else "")

	def test_numeric_codes_and_formulas_are_rejected_before_import(self):
		for index, value in ((0, 12), (2, 12345678)):
			with self.subTest(index=index), self.assertRaisesRegex(
				contracts.StocktakeValidationError, "stocktake.code_as_text:2"
			):
				row = ROW.copy()
				row[index] = value
				contracts.validate_import_rows(contracts.FIELDS, [cells(row)])
		row = cells(ROW)
		row[1] = contracts.ImportCell("=1+1", "f")
		with self.assertRaisesRegex(contracts.StocktakeValidationError, "stocktake.row_invalid:2"):
			contracts.validate_import_rows(contracts.FIELDS, [row])

	def test_product_duplicates_across_warehouses_and_barcode_collisions(self):
		duplicate = ROW.copy()
		duplicate[3] = "B"
		with self.assertRaisesRegex(contracts.StocktakeValidationError, "stocktake.duplicate:3"):
			contracts.validate_import_rows(contracts.FIELDS, [cells(ROW), cells(duplicate)])
		for code, barcode in (("002", ROW[2]), (ROW[2], "another"), ("002", ROW[0])):
			with self.subTest(code=code), self.assertRaisesRegex(
				contracts.StocktakeValidationError, "stocktake.ambiguous_code:3"
			):
				other = [code, "Other", barcode, "", "", "EA", "0"]
				contracts.validate_import_rows(contracts.FIELDS, [cells(ROW), cells(other)])

	def test_qr_codes_remain_case_sensitive_and_unmodified(self):
		for qr in ("Company/QR?ID=0001", "company/qr?id=0001"):
			row = ["0001", "Product", qr, "EA", "10"]
			parsed = contracts.validate_import_rows(contracts.TEMPLATE_FIELDS[:5], [cells(row)])[0]
			self.assertEqual(parsed["barcode"], qr)

	def test_required_values_length_and_blank_rows(self):
		for index, value in ((0, ""), (1, " "), (5, ""), (1, "x" * 301)):
			with self.subTest(index=index), self.assertRaisesRegex(
				contracts.StocktakeValidationError, "stocktake.row_invalid:2"
			):
				row = ROW.copy()
				row[index] = value
				contracts.validate_import_rows(contracts.FIELDS, [cells(row)])
		self.assertEqual(len(contracts.validate_import_rows(contracts.FIELDS, [cells([None] * 7), cells(ROW)])), 1)

	def test_empty_invalid_headers_and_row_limit(self):
		for header, rows, error in (
			(("wrong",), [cells(ROW)], "stocktake.headers_invalid"),
			(contracts.FIELDS, [], "stocktake.empty"),
			(contracts.FIELDS, [cells(ROW[:-1])], "stocktake.row_invalid:2"),
			(contracts.FIELDS, repeat(cells([None] * 7), contracts.MAX_ROWS + 1), "stocktake.too_many_rows"),
		):
			with self.subTest(error=error), self.assertRaisesRegex(contracts.StocktakeValidationError, error):
				contracts.validate_import_rows(header, rows)


class EntryRequestContract(unittest.TestCase):
	def test_canonical_request_matches_persisted_source_shape(self):
		result = contracts.entry_request("task", "item", "2.5", " A ", " 01 ", actor_id="user")
		self.assertEqual(result, {
			"task_id": "task", "target_id": "item", "version": None,
			"quantity": "2.500000", "warehouse": "A", "location": "01",
			"voided": False, "actor_id": "user",
		})
		self.assertEqual(result, contracts.entry_request("task", "item", "2.500000", "A", "01", actor_id="user"))

	def test_edit_version_actor_void_and_photos_are_part_of_replay_payload(self):
		base = contracts.entry_request("task", "entry", "0", "A", "", actor_id="user", version=2)
		for options in ({"version": 3}, {"actor_id": "other"}, {"voided": True}, {"photo_digests": ["sha256"]}):
			kwargs = {"actor_id": "user", "version": 2, **options}
			self.assertNotEqual(base, contracts.entry_request("task", "entry", "0", "A", "", **kwargs))

	def test_missing_or_oversize_location_and_negative_counts_reject(self):
		for warehouse, location in ((" ", ""), ("x" * 301, ""), ("A", "x" * 301)):
			with self.assertRaisesRegex(contracts.StocktakeValidationError, "stocktake.warehouse_required"):
				contracts.entry_request("t", "i", "1", warehouse, location, actor_id="u")
		with self.assertRaises(contracts.StocktakeValidationError):
			contracts.entry_request("t", "i", "-1", "A", "", actor_id="u")


class CountCalculations(unittest.TestCase):
	def test_no_active_entries_differs_from_explicit_zero_and_voids_are_excluded(self):
		self.assertIsNone(calculations.active_quantity([]))
		self.assertIsNone(calculations.active_quantity([{"quantity": "100", "voided": True}]))
		self.assertEqual(calculations.active_quantity([{"quantity": "0", "voided": False}]), Decimal("0"))
		self.assertEqual(calculations.active_quantity([
			{"quantity": "0.1", "voided": False}, {"quantity": "0.2", "voided": False},
			{"quantity": "100", "voided": True},
		]), Decimal("0.3"))

	def test_total_cannot_overflow_quantity_contract(self):
		with self.assertRaises(contracts.StocktakeValidationError):
			calculations.active_quantity([
				{"quantity": "99999999999999", "voided": False}, {"quantity": "1", "voided": False},
			])

	def test_summary_preserves_book_and_authoritative_quantity_and_filters_locations(self):
		item = {"id": "a", "book_qty": Decimal("10"), "actual_qty": Decimal("10"), "warehouse": "A", "location": "01"}
		entries = [
			{"item_id": "a", "warehouse": "A", "location": "01", "voided": False},
			{"item_id": "a", "warehouse": "B", "location": "02", "voided": False},
			{"item_id": "a", "warehouse": "void", "location": "void", "voided": True},
			{"item_id": "other", "warehouse": "other", "location": "other", "voided": False},
		]
		before = copy.deepcopy((item, entries))
		result = calculations.summarize_item(item, entries)
		self.assertEqual(result["warehouses"], ["A", "B"])
		self.assertEqual(result["locations"], ["01", "02"])
		self.assertEqual(result["warehouse_comparison"], "mismatched")
		self.assertEqual(result["location_comparison"], "mismatched")
		self.assertEqual(result["count_result"], "matched")
		self.assertEqual(result["difference"], Decimal("0"))
		self.assertEqual((item, entries), before)

	def test_quantity_and_location_result_categories(self):
		base = {"id": "a", "book_qty": "10", "warehouse": "", "location": ""}
		for actual, difference, category in ((None, None, "not_counted"), ("0", Decimal("-10"), "short"),
			("10", Decimal("0"), "matched"), ("11", Decimal("1"), "over")):
			with self.subTest(actual=actual):
				result = calculations.summarize_item({**base, "actual_qty": actual}, [])
				self.assertEqual((result["difference"], result["count_result"]), (difference, category))
		self.assertEqual(calculations.compare_location("", []), "not_counted")
		self.assertEqual(calculations.compare_location("", [""]), "not_specified")
		self.assertEqual(calculations.compare_location("A", ["A", "A"]), "matched")
		self.assertEqual(calculations.compare_location("A", ["a"]), "mismatched")


if __name__ == "__main__":
	unittest.main()
