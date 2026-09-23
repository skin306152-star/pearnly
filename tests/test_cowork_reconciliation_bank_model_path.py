"""Offline regressions for the migrated model-result handling (batch 40).

Runs in the lab bench (the reconciliation package needs it). Everything is built
in memory; no model call, no file, no network.
"""

import unittest

from pearnly_cowork.reconciliation import bank_model_path, bank_prompts


class StatementGate(unittest.TestCase):
	def test_rows_make_a_result_usable(self):
		self.assertTrue(bank_model_path.statement_has_content({"rows": [{"date": "2026-06-01"}]}))

	def test_a_zero_transaction_statement_is_usable_when_a_balance_came_back(self):
		self.assertTrue(bank_model_path.statement_has_content(
			{"rows": [], "opening_balance": 100.0}))
		self.assertTrue(bank_model_path.statement_has_content(
			{"rows": [], "closing_balance": 50.0}))

	def test_a_blank_read_is_not_usable(self):
		# The deployed note: without this gate a blank scan passes as success and
		# reconciliation ends up with a fabricated 0.00 closing.
		self.assertFalse(bank_model_path.statement_has_content({"rows": []}))
		self.assertFalse(bank_model_path.statement_has_content(
			{"rows": [], "opening_balance": 0, "closing_balance": None}))
		self.assertFalse(bank_model_path.statement_has_content({"rows": None}))

	def test_anything_that_is_not_a_mapping_is_not_usable(self):
		self.assertFalse(bank_model_path.statement_has_content(None))
		self.assertFalse(bank_model_path.statement_has_content([]))
		self.assertFalse(bank_model_path.statement_has_content("rows"))


class StatementRows(unittest.TestCase):
	def test_the_model_opening_wins_and_its_opening_row_stays_out(self):
		rows, opening, _totals = bank_model_path.statement_from_model({
			"opening_balance": 1000.0,
			"rows": [
				{"date": "2026-06-01", "description": "ยอดยกมา", "withdrawal": 0,
					"deposit": 0, "balance": 999.0},
				{"date": "2026-06-02", "description": "โอนเงิน", "withdrawal": None,
					"deposit": 100.0, "balance": 1100.0},
			]})
		self.assertEqual(len(rows), 1)
		self.assertEqual(opening, 1000.0)
		self.assertEqual(rows[0].deposit, 100.0)

	def test_an_opening_row_fills_the_opening_when_the_model_omitted_it(self):
		rows, opening, _totals = bank_model_path.statement_from_model({
			"rows": [
				{"date": "2026-06-01", "description": "balance b/f", "balance": 500.0},
				{"date": "2026-06-02", "description": "โอนเงิน", "deposit": 100.0,
					"balance": 600.0},
			]})
		self.assertEqual(opening, 500.0)
		self.assertEqual(len(rows), 1)

	def test_a_blank_date_inherits_the_previous_one(self):
		rows, _opening, _totals = bank_model_path.statement_from_model({
			"rows": [
				{"date": "2026-06-01", "description": "ครั้งแรก", "deposit": 100.0,
					"balance": 100.0},
				{"date": "", "description": "วันเดียวกัน", "deposit": 50.0, "balance": 150.0},
			]})
		self.assertEqual([row.date.isoformat() for row in rows],
			["2026-06-01", "2026-06-01"])

	def test_a_row_without_a_date_but_with_money_is_kept(self):
		rows, _opening, _totals = bank_model_path.statement_from_model({
			"rows": [{"date": None, "description": "ไม่รู้วันที่", "deposit": 100.0,
				"balance": 100.0}]})
		self.assertEqual(len(rows), 1)
		self.assertIsNone(rows[0].date)

	def test_a_pure_noise_row_is_dropped(self):
		rows, _opening, _totals = bank_model_path.statement_from_model({
			"rows": [
				{"date": None, "description": "", "withdrawal": None, "deposit": None,
					"balance": None},
				{"date": None, "description": "มี余额", "deposit": None, "balance": 0.0},
			]})
		self.assertEqual([row.description for row in rows], ["มี余额"])

	def test_confidence_is_downgraded_when_a_side_or_the_balance_is_missing(self):
		# The deployed rule downgrades only when BOTH sides are missing, or when
		# there is no balance.
		rows, _opening, _totals = bank_model_path.statement_from_model({
			"rows": [
				{"date": "2026-06-01", "description": "a", "deposit": 100.0,
					"balance": 100.0, "confidence": "high"},
				{"date": "2026-06-02", "description": "b", "balance": 50.0,
					"confidence": "high"},
				{"date": "2026-06-03", "description": "c", "deposit": 10.0,
					"balance": None, "confidence": "high"},
			]})
		self.assertEqual([row.confidence for row in rows], ["high", "low", "low"])

	def test_an_unknown_confidence_word_becomes_high(self):
		rows, _opening, _totals = bank_model_path.statement_from_model({
			"rows": [{"date": "2026-06-01", "description": "a", "deposit": 1.0,
				"balance": 1.0, "confidence": "certain"}]})
		self.assertEqual(rows[0].confidence, "high")

	def test_the_printed_footer_totals_are_kept_or_nulled(self):
		_rows, _opening, totals = bank_model_path.statement_from_model({
			"rows": [{"date": "2026-06-01", "description": "a", "deposit": 1.0,
				"balance": 1.0}],
			"printed_total_credit": "8000.00",
			"printed_credit_count": 4,
			"printed_total_debit": None,
			"printed_debit_count": "n/a",
		})
		self.assertEqual(totals, {"total_debit": None, "total_credit": 8000.0,
			"debit_count": None, "credit_count": 4.0})

	def test_a_model_result_with_no_rows_yields_no_rows(self):
		rows, opening, _totals = bank_model_path.statement_from_model({"rows": []})
		self.assertEqual((rows, opening), ([], 0.0))


class LedgerChunks(unittest.TestCase):
	def test_rows_are_merged_across_chunks(self):
		result = bank_model_path.gl_from_model_chunks([
			{"opening_balance": 1000.0, "rows": [
				{"date": "2026-06-01", "account_code": "5301", "description": "a",
					"debit": 100.0, "credit": 0},
			]},
			{"rows": [
				{"date": "2026-06-02", "account_code": "5301", "description": "b",
					"debit": 0, "credit": 50.0},
			]},
		])
		self.assertTrue(result["ok"])
		self.assertEqual(result["opening"], 1000.0)
		self.assertEqual(result["closing"], 1050.0)
		self.assertEqual(result["row_count"], 2)
		self.assertEqual(result["accounts"], ["5301"])

	def test_a_failed_chunk_does_not_sink_the_others(self):
		result = bank_model_path.gl_from_model_chunks([
			None,
			{"rows": [{"date": "2026-06-02", "debit": 10.0, "credit": 0}]},
		])
		self.assertTrue(result["ok"])
		self.assertEqual(result["row_count"], 1)

	def test_the_opening_only_comes_from_the_first_chunk(self):
		# The deployed code reads the opening only on the first iteration; when that
		# chunk failed the opening stays 0 even though a later chunk carries one.
		result = bank_model_path.gl_from_model_chunks([
			None,
			{"opening_balance": 777.0, "rows": [
				{"date": "2026-06-02", "debit": 10.0, "credit": 0}]},
		])
		self.assertEqual(result["opening"], 0.0)

	def test_a_row_without_a_date_is_dropped(self):
		result = bank_model_path.gl_from_model_chunks([
			{"rows": [
				{"date": "", "debit": 10.0, "credit": 0},
				{"date": "2026-06-02", "debit": 10.0, "credit": 0},
			]},
		])
		self.assertEqual(result["row_count"], 1)

	def test_the_account_filter_applies_to_the_model_rows_too(self):
		result = bank_model_path.gl_from_model_chunks([
			{"rows": [
				{"date": "2026-06-01", "account_code": "5301-01", "debit": 1.0, "credit": 0},
				{"date": "2026-06-01", "account_code": "6201-01", "debit": 2.0, "credit": 0},
			]},
		], "5301")
		self.assertEqual([row.account_code for row in result["rows"]], ["5301-01"])

	def test_no_usable_rows_is_a_failure(self):
		result = bank_model_path.gl_from_model_chunks([None, {"rows": []}])
		self.assertFalse(result["ok"])
		self.assertEqual(result["rows"], [])
		self.assertEqual(result["error"], "gemini returned no usable rows")

	def test_an_account_that_cannot_be_read_is_not_listed(self):
		result = bank_model_path.gl_from_model_chunks([
			{"rows": [{"date": "2026-06-01", "debit": 1.0, "credit": 0}]},
		])
		self.assertEqual(result["accounts"], [])

	def test_the_signed_ledger_row_reads_both_sides(self):
		row = bank_model_path.gl_row_from_model(
			{"date": "2026-06-01", "doc_no": "PV-1", "account_code": "5301",
				"description": "ค่าอาหาร", "debit": 100.0, "credit": 25.0})
		self.assertEqual((row.doc_no, row.debit, row.credit), ("PV-1", 100.0, 25.0))
		self.assertIsNone(bank_model_path.gl_row_from_model({"date": "not a date"}))


class Prompts(unittest.TestCase):
	def test_the_statement_prompt_version_is_the_deployed_one(self):
		self.assertEqual(bank_prompts.STATEMENT_PROMPT_VERSION, "v63-totals")

	def test_the_ledger_prompt_takes_the_account_hint(self):
		plain = bank_prompts.gl_prompt()
		with_hint = bank_prompts.gl_prompt("5301")
		self.assertIn("Filter to account code starting with '5301'.", with_hint)
		self.assertNotIn("Filter to account code", plain)
		self.assertNotIn("{hint}", plain + with_hint)

	def test_the_prompts_still_ask_for_the_deployed_fields(self):
		self.assertIn("printed_credit_count", bank_prompts.STATEMENT_PROMPT)
		self.assertIn("RUNNING-BALANCE SELF-CHECK", bank_prompts.STATEMENT_PROMPT)
		self.assertIn("debit:number, credit:number", bank_prompts.gl_prompt())
		self.assertEqual(bank_prompts.GL_PAGES_PER_CHUNK, 4)


if __name__ == "__main__":
	unittest.main()
