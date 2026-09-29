"""Offline S8 boundary and row validation; no file/model/framework access."""
import copy
import unittest

from pearnly_cowork.reconciliation import bank_review as review
from pearnly_cowork.reconciliation import bank_recon_contracts as contracts

ORIGINAL = [{"date": "2026-06-01", "description": "payment", "withdrawal": "40.00",
	"deposit": "0.00", "balance": "60.00", "confidence": "low", "autocorrected": True}]


class BankReview(unittest.TestCase):
	def test_gate_is_low_confidence_amount_repair_or_incompleteness(self):
		clean = {"ok": True, "rows": [1], "completeness": {"ok": True}}
		self.assertFalse(review.needs_review(clean))
		self.assertFalse(review.needs_review({**clean, "balance_warn_count": 1}))
		for change in ({"low_conf_count": 1}, {"amount_fixed_count": 1},
				{"completeness": {"ok": False}}):
			self.assertTrue(review.needs_review({**clean, **change}))
			self.assertFalse(review.needs_review({**clean, **change, "rows": []}))
			self.assertFalse(review.needs_review({**clean, **change, "ok": False}))

	def test_retain_metadata_but_reject_client_metadata(self):
		rows = review.editable_rows(ORIGINAL)
		validated, canonical = review.validate_rows(rows, ORIGINAL, [])
		self.assertEqual(validated[0]["confidence"], "low")
		self.assertTrue(validated[0]["autocorrected"])
		self.assertEqual(canonical, rows)
		with self.assertRaises(contracts.ReconciliationContractError):
			review.validate_rows([{**rows[0], "confidence": "high"}], ORIGINAL, [])

	def test_full_replacement_can_add_remove_but_not_duplicate_ids(self):
		row = review.editable_rows(ORIGINAL)[0]
		validated, canonical = review.validate_rows([{**row, "idx": None}], ORIGINAL, [])
		self.assertIsNone(canonical[0]["idx"])
		self.assertEqual(validated[0]["confidence"], "high")
		for value in ([], [row, row], [{**row, "idx": True}], [{**row, "idx": 1}],
				[{**row, "idx": -1}]):
			with self.assertRaises(contracts.ReconciliationContractError):
				review.validate_rows(value, ORIGINAL, [])

	def test_equivalent_amount_formats_have_the_same_confirmation_fingerprint(self):
		row = review.editable_rows(ORIGINAL)[0]
		_, first = review.validate_rows([row], ORIGINAL, [])
		_, second = review.validate_rows([{**row, "deposit": 0, "withdrawal": 40,
			"balance": "60.0"}], ORIGINAL, [])
		self.assertEqual(contracts.canonical_digest(first), contracts.canonical_digest(second))

	def test_invalid_amount_date_and_missing_fields_are_refused(self):
		row = review.editable_rows(ORIGINAL)[0]
		for change in ({"balance": None}, {"balance": "NaN"}, {"withdrawal": "-1"},
				{"deposit": "1.001"}, {"date": "tomorrow"}):
			with self.assertRaises(contracts.ReconciliationContractError):
				review.validate_rows([{**row, **change}], ORIGINAL, [])
		# 摘要可以空（旧版复核同样按空串收；2026-09-28 起不再因此整份失败）
		review.validate_rows([{**row, "description": ""}], ORIGINAL, [])
		incomplete = copy.deepcopy(row)
		del incomplete["deposit"]
		with self.assertRaises(contracts.ReconciliationContractError):
			review.validate_rows([incomplete], ORIGINAL, [])

	def test_a_statement_read_without_any_balance_can_be_confirmed_blank(self):
		# 网银查询截图（BBL-SA-01/06，2026-09-30）：读出来每行都没有余额，复核保存不能卡住。
		original = [{**ORIGINAL[0], "balance": None}, {**ORIGINAL[0], "balance": None, "withdrawal": "0.00",
			"deposit": "4986.23"}]
		rows = review.editable_rows(original)
		validated, _canonical = review.validate_rows(rows, original, [])
		self.assertEqual([row["balance"] for row in validated], [None, None])
		self.assertTrue(all(check["ok"] for check in review.balance_chain(validated, 0)))
		# 只空一部分仍要补
		with self.assertRaises(contracts.ReconciliationContractError):
			review.validate_rows([rows[0], {**rows[1], "balance": "5000.00"}], original, [])

	def test_chain_uses_actual_predecessor_and_exact_two_cent_tolerance(self):
		row = review.editable_rows(ORIGINAL)[0]
		rows, _ = review.validate_rows([row, {**row, "idx": None, "balance": "20.02"},
			{**row, "idx": None, "balance": "-19.95"},
			{**row, "idx": None, "balance": "-59.95"}], ORIGINAL, [])
		checks = review.balance_chain(rows, "100")
		self.assertEqual([item["ok"] for item in checks], [True, True, False, True])
		self.assertEqual(checks[2]["diff"], "0.03")


if __name__ == "__main__":
	unittest.main()
