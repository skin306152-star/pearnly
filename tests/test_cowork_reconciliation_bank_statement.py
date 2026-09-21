"""Offline regressions for the migrated bank-statement balance chain."""

import unittest
from datetime import date

from pearnly_cowork.reconciliation import bank_statement as statement

STATEMENT_DATE = date(2026, 6, 1)


def row(deposit=0.0, withdrawal=0.0, balance=0.0, description="tx", date_value=STATEMENT_DATE):
	return statement.StatementRow(date_value, description, withdrawal, deposit, balance)


def unknown_balance_row(description="continuation page"):
	# The deployed dataclass formats the balance in its fingerprint, so a row
	# whose balance column was not read must carry an explicit fingerprint.
	item = statement.StatementRow(STATEMENT_DATE, description, 0.0, 0.0, 0.0)
	item.balance = None
	return item


class SummaryRowContract(unittest.TestCase):
	def test_deployed_footer_keywords_are_filtered(self):
		for description in ("Total Credit", "  grand total ", "รวมรายการ", "ยอดรวม", "本期合计", "累计"):
			with self.subTest(description=description):
				self.assertTrue(statement.is_summary_row(description))

	def test_transactions_and_empty_descriptions_are_kept(self):
		for description in ("SWD 65,573.75", "", "   ", None, "Transfer to KKP"):
			with self.subTest(description=description):
				self.assertFalse(statement.is_summary_row(description))


class BadRatioContract(unittest.TestCase):
	def test_clean_chain_is_below_the_gate(self):
		rows = [row(deposit=1000.0, balance=1000.0 + index * 1000.0) for index in range(1, 13)]
		self.assertLess(statement.statement_row_bad_ratio(rows, 0.0), 0.30)

	def test_zero_balance_column_is_flagged(self):
		rows = [row(withdrawal=700.0, balance=0.0) for _ in range(10)]
		self.assertGreater(statement.statement_row_bad_ratio(rows, 0.0), 0.30)

	def test_swapped_direction_alone_stays_below_the_gate(self):
		# Balances are right and only the columns are swapped, so the model is not
		# asked to re-read: correct_direction_from_balance handles this case.
		rows = [row(withdrawal=1000.0, balance=1000.0 + index * 1000.0) for index in range(1, 13)]
		self.assertLess(statement.statement_row_bad_ratio(rows, 0.0), 0.30)

	def test_transaction_id_read_as_amount_is_flagged(self):
		rows = [
			row(balance=100000.0),
			row(withdrawal=2.026e19, balance=99000.0),
			row(withdrawal=1.5e18, balance=98000.0),
			row(withdrawal=9.9e18, balance=97000.0),
		]
		self.assertGreater(statement.statement_row_bad_ratio(rows, 100000.0), 0.30)

	def test_too_few_movement_rows_is_never_bad(self):
		rows = [row(withdrawal=700.0, balance=0.0), row(withdrawal=700.0, balance=0.0)]
		self.assertEqual(statement.statement_row_bad_ratio(rows, 0.0), 0.0)
		self.assertEqual(statement.statement_row_bad_ratio([], 0.0), 0.0)


class DirectionCorrectionContract(unittest.TestCase):
	def test_balance_fall_corrects_a_deposit_column(self):
		# Deployed BBL case: the balance falls by the recorded deposit, so the
		# columns were read swapped.
		rows = [row(deposit=400000.0, balance=600000.0)]
		statement.correct_direction_from_balance(rows, 1000000.0)
		self.assertEqual((rows[0].withdrawal, rows[0].deposit), (400000.0, 0.0))
		self.assertTrue(rows[0].direction_autocorrected)

	def test_balance_rise_corrects_a_withdrawal_column(self):
		rows = [row(withdrawal=500.0, balance=1500.0)]
		statement.correct_direction_from_balance(rows, 1000.0)
		self.assertEqual((rows[0].withdrawal, rows[0].deposit), (0.0, 500.0))
		self.assertTrue(rows[0].direction_autocorrected)

	def test_amount_that_does_not_match_the_movement_is_left_alone(self):
		rows = [row(deposit=999.0, balance=1500.0)]
		statement.correct_direction_from_balance(rows, 1000.0)
		self.assertEqual((rows[0].withdrawal, rows[0].deposit), (0.0, 999.0))
		self.assertFalse(rows[0].direction_autocorrected)

	def test_unreliable_opening_never_corrects_the_first_movement(self):
		rows = [row(deposit=999.0, balance=1500.0), row(deposit=100.0, balance=1600.0)]
		statement.correct_direction_from_balance(rows, 0.0)
		self.assertFalse(rows[0].direction_autocorrected)
		self.assertFalse(rows[1].direction_autocorrected)

	def test_rows_without_movement_or_balance_only_seed_the_running_balance(self):
		rows = [row(description="ยอดยกมา", balance=1200.0), unknown_balance_row("no balance column"),
			row(deposit=250.0, balance=1450.0)]
		statement.correct_direction_from_balance(rows, 5000.0)
		self.assertFalse(any(item.direction_autocorrected for item in rows))

	def test_empty_input_is_a_no_op(self):
		statement.correct_direction_from_balance([], 100.0)


class VerifyContract(unittest.TestCase):
	def test_clean_chain_verifies_and_a_break_is_flagged(self):
		rows = [row(deposit=100.0, balance=1100.0), row(deposit=50.0, balance=2000.0)]
		statement.verify_row_balances(rows, 1000.0)
		self.assertIs(rows[0].balance_ok, True)
		self.assertIs(rows[1].balance_ok, False)

	def test_unverifiable_rows_are_none_not_false(self):
		rows = [
			unknown_balance_row("no balance column"),
			row(description="ยอดยกมา", balance=1200.0),
			row(description="Transfer", balance=1200.0),
			row(deposit=250.0, balance=1450.0),
		]
		statement.verify_row_balances(rows, 0.0)
		self.assertEqual([item.balance_ok for item in rows], [None, None, None, None])

	def test_opening_label_is_matched_case_insensitively(self):
		rows = [row(description="Opening Balance", balance=700.0)]
		statement.verify_row_balances(rows, 0.0)
		self.assertIsNone(rows[0].balance_ok)

	def test_row_tolerance_is_half_a_percent_clamped_between_two_satang_and_one_baht(self):
		# (deposit, balance, expected) around an opening of 1000 with no withdrawal.
		cases = [
			(2.0, 1002.02, True),  # 0.5% would be 0.01, so the 0.02 floor applies
			(2.0, 1002.03, False),
			(10.0, 1010.03, True),  # 0.5% of 10 is 0.05
			(10.0, 1010.06, False),
			(100.0, 1100.40, True),  # 0.5% of 100 is 0.50
			(100.0, 1100.60, False),
			(10000.0, 11000.90, True),  # capped at one baht, not 50
			(10000.0, 11001.20, False),
		]
		for deposit, balance, expected in cases:
			with self.subTest(deposit=deposit, balance=balance):
				rows = [row(deposit=deposit, balance=balance)]
				statement.verify_row_balances(rows, 1000.0)
				self.assertIs(rows[0].balance_ok, expected)

	def test_empty_input_is_a_no_op(self):
		statement.verify_row_balances([], 100.0)


class RepairContract(unittest.TestCase):
	def test_misread_digit_is_repaired_and_marked(self):
		rows = [
			row(deposit=5000.0, balance=15000.0, description="open"),
			row(deposit=660.0, balance=15600.0, description="misread digit"),
			row(deposit=100.0, balance=15700.0, description="next"),
		]
		statement.verify_row_balances(rows, 10000.0)
		self.assertIs(rows[1].balance_ok, False)
		statement.repair_amount_from_balance(rows, 10000.0)
		self.assertEqual((rows[1].deposit, rows[1].withdrawal), (600.0, 0.0))
		self.assertIs(rows[1].balance_ok, True)
		self.assertTrue(rows[1].amount_autocorrected)

	def test_large_gap_is_left_for_the_accountant(self):
		rows = [
			row(deposit=5000.0, balance=15000.0),
			row(deposit=100.0, balance=45000.0),
			row(deposit=100.0, balance=45100.0),
		]
		statement.verify_row_balances(rows, 10000.0)
		statement.repair_amount_from_balance(rows, 10000.0)
		self.assertEqual((rows[1].deposit, rows[1].withdrawal), (100.0, 0.0))
		self.assertIs(rows[1].balance_ok, False)
		self.assertFalse(rows[1].amount_autocorrected)

	def test_uncorroborated_balance_is_never_repaired(self):
		rows = [
			row(deposit=5000.0, balance=15000.0),
			row(deposit=600.0, balance=15600.0),
			row(deposit=100.0, balance=1.0),
		]
		statement.verify_row_balances(rows, 10000.0)
		statement.repair_amount_from_balance(rows, 10000.0)
		self.assertFalse(rows[1].amount_autocorrected)

	def test_unreliable_previous_row_blocks_the_repair(self):
		rows = [
			row(deposit=600.0, balance=15600.0),
			row(deposit=100.0, balance=15700.0),
		]
		statement.verify_row_balances(rows, 0.0)
		statement.repair_amount_from_balance(rows, 0.0)
		self.assertFalse(any(item.amount_autocorrected for item in rows))


class FinalizeContract(unittest.TestCase):
	def test_footer_rows_are_dropped_before_verification(self):
		rows = [
			row(deposit=100.0, balance=1100.0, description="SWD"),
			row(deposit=9000.0, balance=10100.0, description="Total Deposit"),
			row(deposit=50.0, balance=1150.0, description="TRF"),
		]
		kept = statement.finalize_rows(rows, 1000.0)
		self.assertEqual([item.description for item in kept], ["SWD", "TRF"])
		self.assertIs(kept[0].balance_ok, True)
		self.assertIs(kept[1].balance_ok, True)

	def test_direction_correction_precedes_verification(self):
		rows = [
			row(deposit=1000.0, balance=2000.0, description="in"),
			row(deposit=400.0, balance=1600.0, description="swapped columns"),
		]
		kept = statement.finalize_rows(rows, 1000.0)
		self.assertTrue(kept[1].direction_autocorrected)
		self.assertEqual((kept[1].withdrawal, kept[1].deposit), (400.0, 0.0))
		self.assertIs(kept[1].balance_ok, True)

	def test_input_list_is_not_reordered_and_rows_are_updated_in_place(self):
		rows = [row(deposit=100.0, balance=1100.0), row(deposit=50.0, balance=2000.0)]
		kept = statement.finalize_rows(rows, 1000.0)
		self.assertEqual(len(kept), 2)
		self.assertIs(kept[0], rows[0])
		self.assertIs(rows[1].balance_ok, False)


class CompletenessAuditContract(unittest.TestCase):
	def test_all_anchors_agree(self):
		rows = [row(deposit=2000.0, balance=4786.0 + index * 2000.0) for index in range(4)]
		result = statement.audit_completeness(rows, opening=2786.0, closing=10786.0,
			printed={"total_credit": 8000, "total_debit": 0, "credit_count": 4, "debit_count": 0})
		self.assertTrue(result["ok"], result["issues"])
		self.assertEqual(result["sum_deposit"], 8000.0)
		self.assertEqual(result["count_deposit"], 4)

	def test_printed_count_detects_the_missed_row(self):
		rows = [row(deposit=2000.0, balance=4786.0 + index * 2000.0) for index in range(3)]
		result = statement.audit_completeness(rows, opening=2786.0, closing=10786.0,
			printed={"total_credit": 8000, "total_debit": 0, "credit_count": 4, "debit_count": 0})
		self.assertFalse(result["ok"])
		self.assertEqual({issue["type"] for issue in result["issues"]},
			{"credit_count_mismatch", "credit_sum_mismatch", "closing_mismatch"})

	def test_misread_amount_is_flagged_by_the_printed_total(self):
		rows = [
			row(deposit=2000.0, balance=4786.0), row(deposit=1800.0, balance=6586.0),
			row(deposit=2000.0, balance=8586.0), row(deposit=2000.0, balance=10586.0),
		]
		result = statement.audit_completeness(rows, opening=2786.0, closing=10586.0,
			printed={"total_credit": 8000, "credit_count": 4, "total_debit": 0, "debit_count": 0})
		self.assertIn("credit_sum_mismatch", {issue["type"] for issue in result["issues"]})

	def test_closing_balance_alone_still_detects_a_break(self):
		rows = [row(deposit=2000.0, balance=4786.0), row(deposit=2000.0, balance=6786.0)]
		result = statement.audit_completeness(rows, opening=2786.0, closing=99999.0, printed=None)
		self.assertFalse(result["ok"])
		self.assertEqual({issue["type"] for issue in result["issues"]}, {"closing_mismatch"})

	def test_no_anchors_is_not_a_false_alarm(self):
		rows = [row(deposit=2000.0, balance=4786.0), row(deposit=2000.0, balance=6786.0)]
		result = statement.audit_completeness(rows, opening=0.0, closing=0.0, printed=None)
		self.assertTrue(result["ok"])

	def test_balance_misfiled_into_a_total_is_skipped_but_counts_still_checked(self):
		rows = [row(deposit=100.0, balance=100.0) for _ in range(179)]
		result = statement.audit_completeness(rows, opening=500.0, closing=919384.8,
			printed={"total_credit": 919384.8, "total_debit": 919384.8,
				"credit_count": 314, "debit_count": 31})
		types = {issue["type"] for issue in result["issues"]}
		self.assertNotIn("credit_sum_mismatch", types)
		self.assertNotIn("debit_sum_mismatch", types)
		self.assertEqual(types, {"closing_mismatch", "credit_count_mismatch", "debit_count_mismatch"})

	def test_wrong_total_near_the_opening_is_still_reported(self):
		rows = [
			row(deposit=5000.0, balance=15000.0), row(withdrawal=1200.0, balance=13800.0),
			row(withdrawal=300.0, balance=13500.0), row(deposit=700.0, balance=14200.0),
		]
		result = statement.audit_completeness(rows, opening=10000.0, closing=14200.0,
			printed={"total_credit": 9999.0, "total_debit": 1500.0,
				"credit_count": 2, "debit_count": 2})
		self.assertEqual({issue["type"] for issue in result["issues"]}, {"credit_sum_mismatch"})

	def test_total_copied_from_the_closing_balance_is_suppressed(self):
		rows = [
			row(deposit=5000.0, balance=15000.0), row(withdrawal=1200.0, balance=13800.0),
			row(withdrawal=300.0, balance=13500.0), row(deposit=700.0, balance=14200.0),
		]
		result = statement.audit_completeness(rows, opening=10000.0, closing=14200.0,
			printed={"total_credit": 14200.0, "total_debit": 1500.0,
				"credit_count": 2, "debit_count": 2})
		self.assertTrue(result["ok"], result["issues"])


class RowFingerprintContract(unittest.TestCase):
	def test_identical_rows_share_a_fingerprint_but_a_different_balance_does_not(self):
		first = row(deposit=0.0, withdrawal=1000.0, balance=5000.0, description="SWD")
		second = row(deposit=0.0, withdrawal=1000.0, balance=5000.0, description="SWD")
		different_balance = row(deposit=0.0, withdrawal=1000.0, balance=4000.0, description="SWD")
		self.assertEqual(first.row_hash, second.row_hash)
		self.assertNotEqual(first.row_hash, different_balance.row_hash)
		self.assertEqual(len(first.row_hash), 12)

	def test_an_explicit_fingerprint_is_kept(self):
		explicit = statement.StatementRow(STATEMENT_DATE, "tx", 0.0, 100.0, 1100.0, row_hash="abc")
		self.assertEqual(explicit.row_hash, "abc")


if __name__ == "__main__":
	unittest.main()
