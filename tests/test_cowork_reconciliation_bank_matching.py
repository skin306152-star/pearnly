"""Offline regressions for the migrated statement/GL matching core and scoring."""

import copy
import unittest
from datetime import date

from pearnly_cowork.reconciliation import bank_scoring as scoring
from pearnly_cowork.reconciliation.bank_matching import (
	DATE_TOL_DAYS,
	BankReconRow,
	GlRow,
	amount_matches,
	day_diff,
	reconcile,
)
from pearnly_cowork.reconciliation.bank_statement import StatementRow

DAY = date(2026, 6, 1)


def stmt(deposit=0.0, withdrawal=0.0, balance=0.0, when=DAY, description="tx", **kwargs):
	return StatementRow(when, description, withdrawal, deposit, balance, **kwargs)


def gl(debit=0.0, credit=0.0, when=DAY, doc_no="GL1", description="gl", **kwargs):
	return GlRow(when, doc_no, "1000", description, debit, credit, **kwargs)


class HelperContract(unittest.TestCase):
	def test_amount_matches_uses_the_registered_tolerance(self):
		self.assertTrue(amount_matches(100.0, 100.02))
		self.assertFalse(amount_matches(100.0, 100.03))

	def test_day_diff_is_absolute_and_none_when_a_date_is_missing(self):
		self.assertEqual(day_diff(date(2026, 6, 1), date(2026, 6, 4)), 3)
		self.assertEqual(day_diff(date(2026, 6, 4), date(2026, 6, 1)), 3)
		self.assertEqual(day_diff(None, DAY), None)
		self.assertEqual(day_diff(DAY, None), None)

	def test_shipped_layer_two_tolerance_is_seven_days(self):
		self.assertEqual(DATE_TOL_DAYS, 7)

	def test_gl_row_keeps_the_deployed_deduplication_fingerprint(self):
		first = gl(credit=100.0)
		self.assertEqual(len(first.row_hash), 12)
		self.assertEqual(first.row_hash, gl(credit=100.0).row_hash)
		self.assertNotEqual(first.row_hash, gl(credit=100.01).row_hash)
		self.assertNotEqual(first.row_hash, gl(credit=100.0, doc_no="GL2").row_hash)
		self.assertEqual(GlRow(DAY, "GL1", "1000", "gl", 0.0, 100.0, row_hash="kept").row_hash, "kept")


class LayerContract(unittest.TestCase):
	def test_layer_one_needs_the_same_date(self):
		rows, summary = reconcile([stmt(withdrawal=100.0, when=DAY)], [gl(credit=100.0, when=DAY)])
		self.assertEqual(summary.matched_count, 1)
		self.assertEqual(rows[0].match_status, "matched")
		self.assertEqual(rows[0].match_layer, 1)
		self.assertEqual(rows[0].date_diff_days, 0)
		self.assertEqual(rows[0].gl_credit, 100.0)

	def test_money_out_pairs_the_gl_credit_column_and_money_in_the_debit_column(self):
		rows, _ = reconcile([stmt(withdrawal=250.0, when=DAY)], [gl(debit=250.0, when=DAY)])
		self.assertEqual(rows[0].match_status, "stmt_withdrawal_only")
		rows, _ = reconcile([stmt(deposit=250.0, when=DAY)], [gl(credit=250.0, when=DAY)])
		self.assertEqual(rows[0].match_status, "stmt_deposit_only")
		rows, _ = reconcile([stmt(deposit=250.0, when=DAY)], [gl(debit=250.0, when=DAY)])
		self.assertEqual(rows[0].match_layer, 1)

	def test_layer_two_accepts_a_gap_within_the_shipped_tolerance(self):
		rows, _ = reconcile([stmt(withdrawal=100.0, when=DAY)], [gl(credit=100.0, when=date(2026, 6, 6))])
		self.assertEqual((rows[0].match_layer, rows[0].date_diff_days), (2, 5))

	def test_layer_two_keeps_the_nearest_unused_gl_row(self):
		rows, _ = reconcile([stmt(withdrawal=100.0, when=DAY)],
			[gl(credit=100.0, when=date(2026, 6, 5), doc_no="far"),
				gl(credit=100.0, when=date(2026, 6, 2), doc_no="near")])
		self.assertEqual(rows[0].gl_doc_no, "near")
		self.assertEqual(rows[0].date_diff_days, 1)
		self.assertEqual([row.match_status for row in rows[1:]], ["gl_credit_only"])
		self.assertEqual(rows[1].gl_doc_no, "far")

	def test_a_gap_beyond_the_tolerance_falls_to_the_amount_only_layer(self):
		rows, _ = reconcile([stmt(withdrawal=100.0, when=DAY)], [gl(credit=100.0, when=date(2026, 6, 30))])
		self.assertEqual(rows[0].match_layer, 3)
		self.assertEqual(rows[0].date_diff_days, 29)

	def test_layer_three_also_pairs_when_a_date_is_missing(self):
		rows, _ = reconcile([stmt(withdrawal=100.0, when=None)], [gl(credit=100.0, when=DAY)])
		self.assertEqual((rows[0].match_layer, rows[0].date_diff_days), (3, None))

	def test_each_gl_row_is_consumed_only_once(self):
		rows, summary = reconcile(
			[stmt(withdrawal=100.0, when=DAY), stmt(withdrawal=100.0, when=DAY)],
			[gl(credit=100.0, when=DAY)])
		self.assertEqual(summary.matched_count, 1)
		self.assertEqual(summary.stmt_withdrawal_only_count, 1)
		self.assertEqual(sorted(row.match_status for row in rows),
			["matched", "stmt_withdrawal_only"])

	def test_zero_amount_gl_rows_never_match(self):
		rows, summary = reconcile([stmt(withdrawal=0.0, deposit=0.0, when=DAY)], [gl(credit=0.0)])
		self.assertEqual(summary.matched_count, 0)
		self.assertEqual(rows[0].match_status, "stmt_deposit_only")

	def test_unmatched_gl_rows_are_flagged_by_side(self):
		rows, summary = reconcile([], [gl(debit=40.0), gl(credit=60.0)])
		self.assertEqual([row.match_status for row in rows], ["gl_debit_only", "gl_credit_only"])
		self.assertEqual(summary.gl_debit_only_amount, 40.0)
		self.assertEqual(summary.gl_credit_only_amount, 60.0)

	def test_matched_rows_sort_first_and_unmatched_rows_keep_append_order(self):
		rows, _ = reconcile(
			[stmt(withdrawal=100.0, when=date(2026, 6, 3)), stmt(deposit=50.0, when=date(2026, 6, 2)),
				stmt(withdrawal=70.0, when=date(2026, 6, 1))],
			[gl(credit=70.0, when=date(2026, 6, 1)), gl(debit=50.0, when=date(2026, 6, 2))])
		self.assertEqual([(row.match_status, row.stmt_date) for row in rows], [
			("matched", date(2026, 6, 1)), ("matched", date(2026, 6, 2)),
			("stmt_withdrawal_only", date(2026, 6, 3))])

	def test_statement_row_annotations_are_carried_onto_the_result(self):
		rows, _ = reconcile(
			[stmt(withdrawal=100.0, when=DAY, confidence="low", balance_ok=False,
				amount_autocorrected=True, source_file="a.pdf")],
			[gl(credit=100.0, when=DAY, source_file="b.xlsx")])
		row = rows[0]
		self.assertEqual(row.stmt_confidence, "low")
		self.assertIs(row.stmt_balance_ok, False)
		self.assertTrue(row.stmt_autocorrected)
		self.assertEqual((row.source_stmt_file, row.source_gl_file), ("a.pdf", "b.xlsx"))

	def test_gl_only_rows_fall_back_to_the_gl_date_for_sorting(self):
		rows, _ = reconcile([], [gl(debit=40.0, when=date(2026, 6, 9)), gl(credit=60.0, when=None)])
		# The deployed sort key uses date.min for a missing date, so it sorts first.
		self.assertEqual([row.gl_date for row in rows], [None, date(2026, 6, 9)])


class SummaryContract(unittest.TestCase):
	def test_totals_counts_and_the_reconciliation_formula(self):
		statement_rows = [stmt(deposit=1000.0, when=DAY), stmt(withdrawal=400.0, when=DAY),
			stmt(withdrawal=90.0, when=DAY)]
		gl_rows = [gl(debit=1000.0, when=DAY), gl(credit=400.0, when=DAY), gl(debit=75.0, when=DAY)]
		rows, summary = reconcile(statement_rows, gl_rows, stmt_opening=5000.0, gl_opening=4990.0,
			stmt_closing=5510.0, gl_closing=5590.0, bank_code="BBL", gl_account_code="1000")
		self.assertEqual((summary.bank_code, summary.gl_account_code), ("BBL", "1000"))
		self.assertEqual(summary.stmt_total_deposit, 1000.0)
		self.assertEqual(summary.stmt_total_withdrawal, 490.0)
		self.assertEqual(summary.gl_total_debit, 1075.0)
		self.assertEqual(summary.gl_total_credit, 400.0)
		self.assertEqual(summary.matched_count, 2)
		self.assertEqual(summary.stmt_withdrawal_only_count, 1)
		self.assertEqual(summary.gl_debit_only_count, 1)
		self.assertEqual(summary.stmt_withdrawal_only_amount, 90.0)
		self.assertEqual(summary.gl_debit_only_amount, 75.0)
		self.assertEqual(summary.opening_diff, 10.0)
		# gl_closing + opening_diff - gl_debit_only + gl_credit_only
		#            - stmt_withdrawal_only + stmt_deposit_only
		# gl_closing + opening_diff - gl_debit_only - stmt_withdrawal_only
		self.assertEqual(summary.formula_stmt_closing, round(5590.0 + 10.0 - 75.0 - 90.0, 2))
		self.assertEqual(summary.formula_diff, round(5510.0 - summary.formula_stmt_closing, 2))
		self.assertEqual(len(rows), 4)

	def test_empty_inputs_produce_an_empty_result(self):
		rows, summary = reconcile([], [])
		self.assertEqual(rows, [])
		self.assertEqual(summary.matched_count, 0)
		self.assertEqual(summary.formula_diff, 0.0)

	def test_input_rows_are_never_mutated(self):
		statement_rows = [stmt(deposit=1000.0, when=DAY)]
		gl_rows = [gl(debit=1000.0, when=DAY)]
		before = copy.deepcopy((statement_rows, gl_rows))
		reconcile(statement_rows, gl_rows)
		self.assertEqual((statement_rows, gl_rows), before)


class ScoringAmountContract(unittest.TestCase):
	def test_amount_bands(self):
		# 100.01 minus 100.00 is 0.010000000000005116 in binary float, so a nominal
		# one-satang gap already leaves the exact band; 100.00 itself is exact.
		cases = [(100.0, 100.0, 50.0), (100.0, 100.01, 45.0), (100.0, 100.02, 45.0),
			(100.0, 101.0, 45.0), (100.0, 101.01, 35.0), (100.0, 110.0, 35.0),
			(10000.0, 10050.0, 20.0), (10000.0, 10300.0, 10.0), (10000.0, 11000.0, 0.0)]
		for bank_amount, invoice_amount, expected in cases:
			with self.subTest(bank=bank_amount, invoice=invoice_amount):
				self.assertEqual(scoring.score_amount(bank_amount, invoice_amount), expected)

	def test_a_missing_side_scores_zero(self):
		for bank_amount, invoice_amount in ((0.0, 100.0), (100.0, 0.0), (None, None), (0, 0)):
			with self.subTest(bank=bank_amount, invoice=invoice_amount):
				self.assertEqual(scoring.score_amount(bank_amount, invoice_amount), 0.0)

	def test_tiny_invoice_amount_still_scores_zero_instead_of_dividing_by_zero(self):
		self.assertEqual(scoring.score_amount(100.0, 0.005), 0.0)


class ScoringDateContract(unittest.TestCase):
	def test_date_bands_follow_the_shipped_tolerance(self):
		cases = [("2026-06-01", 30.0), ("2026-06-02", 25.0), ("2026-06-04", 20.0),
			("2026-06-08", 10.0), ("2026-06-09", 0.0)]
		for invoice_date, expected in cases:
			with self.subTest(invoice_date=invoice_date):
				self.assertEqual(scoring.score_date("2026-06-01", invoice_date), expected)

	def test_missing_unparsable_or_non_string_dates_score_zero(self):
		for bank_date, invoice_date in ((None, "2026-06-01"), ("2026-06-01", None),
				("01/06/2026", "2026-06-01"), ("2026-06-01", "not a date"), (DAY, "2026-06-01")):
			with self.subTest(bank=bank_date, invoice=invoice_date):
				self.assertEqual(scoring.score_date(bank_date, invoice_date), 0.0)


class ScoringDirectionContract(unittest.TestCase):
	def test_income_and_expense_categories_score_full_weight(self):
		self.assertEqual(scoring.score_direction("IN", {"category_tag": "sale"}), 15.0)
		self.assertEqual(scoring.score_direction("OUT", {"category_tag": "Purchase Invoice"}), 15.0)
		self.assertEqual(scoring.score_direction("IN", {"category_tag": "收入凭证"}), 15.0)
		self.assertEqual(scoring.score_direction("OUT", {"category_tag": "费用报销"}), 15.0)
		# Thai wording is not in the deployed keyword list, so it stays unclassified.
		self.assertAlmostEqual(scoring.score_direction("OUT", {"category_tag": "ค่าใช้จ่าย"}), 15 * 0.7)

	def test_unclassified_outgoing_payment_keeps_partial_credit(self):
		self.assertAlmostEqual(scoring.score_direction("OUT", {}), 15 * 0.7)
		self.assertAlmostEqual(scoring.score_direction("IN", {}), 15 * 0.3)
		self.assertAlmostEqual(scoring.score_direction("IN", {"category_tag": "expense"}), 15 * 0.3)

	def test_a_missing_direction_scores_zero(self):
		self.assertEqual(scoring.score_direction("", {"category_tag": "sale"}), 0.0)


class ScoringKeywordContract(unittest.TestCase):
	def test_vendor_word_and_invoice_number_add_up_to_the_cap(self):
		meta = {"vendor": "ACME Trading", "invoice_no": "INV-77"}
		self.assertEqual(scoring.score_keyword("payment ACME co", meta), 3.0)
		self.assertEqual(scoring.score_keyword("payment ACME INV-77", meta), 5.0)
		self.assertEqual(scoring.score_keyword("payment inv-77", meta), 2.0)

	def test_short_vendor_ignored_and_thai_text_matches(self):
		self.assertEqual(scoring.score_keyword("payment ACME", {"vendor": "AC"}), 0.0)
		self.assertEqual(scoring.score_keyword("โอนให้ บริษัท ไทย จำกัด", {"vendor": "บริษัท ไทย"}), 3.0)

	def test_missing_description_scores_zero(self):
		self.assertEqual(scoring.score_keyword("", {"vendor": "ACME", "invoice_no": "INV"}),
			0.0)


class CandidateRankingContract(unittest.TestCase):
	def test_zero_amount_candidates_are_dropped_and_the_rest_ranked(self):
		bank_tx = {"amount": 1000.0, "tx_date": "2026-06-01", "direction": "OUT",
			"description": "payment ACME INV-77"}
		candidates = [
			{"id": "far", "amount_total": 2000.0, "invoice_date": "2026-06-01"},
			{"id": "exact", "amount_total": 1000.0, "invoice_date": "2026-06-01",
				"category_tag": "purchase", "vendor": "ACME", "invoice_no": "INV-77"},
			{"id": "near", "amount_total": 1000.5, "invoice_date": "2026-06-03"},
		]
		ranked = scoring.match_one_tx(bank_tx, candidates)
		self.assertEqual([item["history_id"] for item in ranked], ["exact", "near"])
		self.assertEqual(ranked[0]["score"], 100.0)
		self.assertEqual(ranked[0]["reason"], "金额精确 + 同日 + 描述匹配")
		self.assertEqual(ranked[0]["breakdown"],
			{"amount": 50.0, "date": 30.0, "direction": 15.0, "keyword": 5.0})
		self.assertEqual(ranked[1]["reason"], "金额接近 + 日期差 3 天内")
		self.assertEqual(ranked[1]["score"], 75.5)
		self.assertAlmostEqual(ranked[1]["breakdown"]["direction"], 15 * 0.7)
		self.assertEqual(ranked[1]["breakdown"]["amount"], 45.0)

	def test_the_total_amount_fallback_and_the_reason_bands(self):
		bank_tx = {"amount": 500.0, "tx_date": "2026-06-01"}
		candidates = [
			{"id": "total", "total": 500.0, "invoice_date": "2026-06-02"},
			{"id": "week", "amount_total": 500.0, "invoice_date": "2026-06-06"},
			{"id": "outside", "amount_total": 500.0, "invoice_date": "2026-06-20"},
		]
		ranked = scoring.match_one_tx(bank_tx, candidates)
		self.assertEqual({item["history_id"]: item["reason"] for item in ranked}, {
			"total": "金额精确 + 日期差 1 天", "week": "金额精确 + 日期差 7 天内",
			"outside": "金额精确"})

	def test_at_most_five_candidates_are_returned_and_order_is_stable(self):
		bank_tx = {"amount": 100.0, "tx_date": "2026-06-01"}
		candidates = [{"id": f"c{index}", "amount_total": 100.0, "invoice_date": "2026-06-01"}
			for index in range(8)]
		ranked = scoring.match_one_tx(bank_tx, candidates)
		self.assertEqual([item["history_id"] for item in ranked],
			["c0", "c1", "c2", "c3", "c4"])

	def test_no_candidate_scores_yields_an_empty_list(self):
		self.assertEqual(scoring.match_one_tx({"amount": 100.0}, []), [])
		self.assertEqual(scoring.match_one_tx({"amount": 100.0},
			[{"id": "x", "amount_total": 100000.0}]), [])

	def test_thresholds_stay_at_the_deployed_bands(self):
		self.assertEqual((scoring.THRESHOLD_AUTO, scoring.THRESHOLD_SUGGEST), (85, 60))
		self.assertEqual(scoring.WEIGHT_AMOUNT + scoring.WEIGHT_DATE + scoring.WEIGHT_DIRECTION
			+ scoring.WEIGHT_KEYWORD, 100)


class ResultTypeContract(unittest.TestCase):
	def test_unmatched_statement_rows_are_withdrawal_or_deposit_only(self):
		rows, _ = reconcile([stmt(withdrawal=10.0), stmt(deposit=20.0)], [])
		self.assertEqual(sorted(row.match_status for row in rows),
			["stmt_deposit_only", "stmt_withdrawal_only"])
		self.assertEqual([row.match_layer for row in rows], [None, None])

	def test_row_types_keep_the_deployed_defaults(self):
		row = BankReconRow(match_status="matched", match_layer=1)
		self.assertEqual((row.stmt_confidence, row.stmt_balance_ok, row.stmt_autocorrected),
			("high", None, False))
		self.assertEqual((row.gl_debit, row.gl_credit, row.gl_balance), (0.0, 0.0, 0.0))


if __name__ == "__main__":
	unittest.main()
