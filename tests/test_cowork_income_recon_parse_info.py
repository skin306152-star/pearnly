"""Offline regressions for the per-file diagnostics stored with an income task."""

import unittest

from pearnly_cowork.reconciliation import income_recon_contracts as contracts


class ParseInfoContract(unittest.TestCase):
	def test_an_empty_value_stores_no_section(self):
		self.assertEqual(contracts.parse_info(None), {})
		self.assertEqual(contracts.parse_info(""), {})

	def test_both_deployed_sections_are_kept_with_their_counters(self):
		value = contracts.parse_info({
			"gl": [{"file": "GL June.xlsx", "method": "excel", "rows": 4,
				"parser_version": "1.0.0", "accounts_seen": ["4110-01", "4110-02"],
				"sheets_seen": 1, "rows_kept": 4}],
			"vat": [{"file": "VAT June.xlsx", "method": "excel", "rows": 3,
				"parser_version": "1.1.0", "warnings": ["跳过 1 行(无单据号)"]}],
		})
		self.assertEqual(value["gl"][0], {"file": "GL June.xlsx", "method": "excel", "rows": 4,
			"parser_version": "1.0.0", "accounts_seen": ["4110-01", "4110-02"], "sheets_seen": 1,
			"rows_kept": 4})
		self.assertEqual(value["vat"][0], {"file": "VAT June.xlsx", "method": "excel", "rows": 3,
			"parser_version": "1.1.0", "warnings": ["跳过 1 行(无单据号)"]})

	def test_a_missing_side_reads_as_an_empty_section(self):
		value = contracts.parse_info({"gl": [{"file": "GL.xlsx", "method": "text_lines",
			"rows": 2, "parser_version": "1.0.0"}]})
		self.assertEqual(value["vat"], [])
		self.assertEqual(value["gl"][0]["accounts_seen"], [])

	def test_the_optional_counters_and_the_pdf_flag_round_trip(self):
		value = contracts.parse_info({"gl": [{"file": "GL.pdf", "method": "pdf",
			"rows": 3, "parser_version": "1.0.0", "accounts_seen": [], "tables_seen": 2,
			"header_found": True, "candidate_data_lines": 7, "rows_kept": 3}]})
		record = value["gl"][0]
		self.assertEqual((record["tables_seen"], record["header_found"],
			record["candidate_data_lines"], record["rows_kept"]), (2, True, 7, 3))

	def test_an_unknown_section_record_key_or_shape_is_refused(self):
		cases = [
			({"scan": []}, "reconciliation.parse_info_invalid"),
			({"gl": [{"file": "a", "mystery": 1}]}, "reconciliation.parse_info_invalid:gl"),
			({"gl": {"file": "a"}}, "reconciliation.parse_info_too_many:gl"),
			({"gl": [{"rows": "many"}]}, "reconciliation.parse_info_rows:gl"),
			({"gl": [{"rows": True}]}, "reconciliation.parse_info_rows:gl"),
			({"vat": [{"warnings": "one"}]}, "reconciliation.parse_info_invalid:vat"),
			({"vat": [{"warnings": [1]}]}, "reconciliation.parse_info_invalid:vat"),
			({"gl": [{"accounts_seen": "4110"}]}, "reconciliation.parse_info_invalid:gl"),
			({"gl": [{"sheets_seen": -1}]}, "reconciliation.parse_info_invalid:gl"),
		]
		for payload, expected in cases:
			with self.subTest(payload=payload):
				with self.assertRaises(contracts.ReconciliationContractError) as caught:
					contracts.parse_info(payload)
				self.assertEqual(str(caught.exception), expected)

	def test_a_batch_over_the_file_cap_is_refused(self):
		records = [{"file": "f", "method": "excel", "rows": 1, "parser_version": "1"}
			for _ in range(contracts.MAX_PARSE_FILES + 1)]
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.parse_info({"gl": records})
		self.assertEqual(str(caught.exception), "reconciliation.parse_info_too_many:gl")

	def test_a_file_name_is_bounded_and_trimmed(self):
		self.assertEqual(contracts.file_name(" GL June.xlsx ", "gl_filename"), "GL June.xlsx")
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.file_name("x" * (contracts.MAX_FILE_NAME + 1), "gl_filename")
		self.assertEqual(str(caught.exception), "reconciliation.text_too_long:gl_filename")


if __name__ == "__main__":
	unittest.main()
