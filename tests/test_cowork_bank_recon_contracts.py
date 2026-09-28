"""Offline regressions for the decoded-row contract of the native reconciliation store."""

import unittest
from datetime import date
from decimal import Decimal

from pearnly_cowork.reconciliation import bank_recon_contracts as contracts


class StatementRowContract(unittest.TestCase):
	def test_decimal_amounts_are_kept_exactly_and_empty_means_zero(self):
		row = contracts.statement_row({"description": "SWD", "withdrawal": "1,234.50"})
		self.assertEqual(row["withdrawal"], Decimal("1234.50"))
		self.assertEqual(row["deposit"], Decimal(0))
		self.assertIsNone(row["balance"])
		self.assertEqual((row["confidence"], row["balance_ok"], row["autocorrected"]),
			("high", "", False))

	def test_negative_balance_is_allowed_but_negative_movement_is_not(self):
		# 摘要可以空（利息 / 手续费行），不让整份对账失败
		self.assertEqual(contracts.statement_row({"description": "", "deposit": "1"})["description"], "")
		row = contracts.statement_row({"description": "overdraft", "balance": "-150.25"})
		self.assertEqual(row["balance"], Decimal("-150.25"))
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.statement_row({"description": "x", "deposit": "-1"})

	def test_rejections_cover_precision_unknown_fields_text_and_dates(self):
		cases = [
			({"description": "x", "deposit": "1.1234567"}, "reconciliation.amount_precision:statement.deposit"),
			({"description": "x", "deposit": "1e400"}, "reconciliation.amount_invalid:statement.deposit"),
			({"description": "x", "extra": 1}, "reconciliation.row_unknown_field:statement"),
			({"description": "x" * 301}, "reconciliation.text_too_long:statement.description"),
			({"description": "x", "date": "01/06/2026"}, "reconciliation.date_invalid:statement.date"),
			({"description": "x", "confidence": "certain"}, "reconciliation.confidence_invalid"),
			({"description": "x", "balance_ok": "maybe"}, "reconciliation.balance_state_invalid"),
		]
		for payload, expected in cases:
			with self.subTest(payload=payload):
				with self.assertRaises(contracts.ReconciliationContractError) as caught:
					contracts.statement_row(payload)
				self.assertEqual(str(caught.exception), expected)

	def test_non_mapping_row_is_refused(self):
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.statement_row(["not", "a", "row"])


class GlRowContract(unittest.TestCase):
	def test_gl_row_keeps_its_own_fields(self):
		row = contracts.gl_row({"date": "2026-06-01", "doc_no": "GL1", "account_code": "1000",
			"description": "credit", "credit": "400", "balance": "1000.5"})
		self.assertEqual(row["date"], date(2026, 6, 1))
		self.assertEqual((row["debit"], row["credit"], row["balance"]),
			(Decimal(0), Decimal("400"), Decimal("1000.5")))

	def test_statement_only_fields_are_refused_on_the_gl_side(self):
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.gl_row({"description": "x", "withdrawal": "1"})


class BatchAndHeaderContract(unittest.TestCase):
	def test_row_batch_is_bounded_and_both_sides_are_required(self):
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.decode_rows("not a list", [])
		too_many = [{"description": "x"} for _ in range(contracts.MAX_ROWS + 1)]
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.decode_rows(too_many, [])

	def test_header_defaults_to_zero_and_refuses_unknown_keys(self):
		value = contracts.header(None)
		self.assertEqual([value[key] for key in contracts.HEADER_AMOUNTS], [Decimal(0)] * 4)
		self.assertEqual(contracts.header({"bank_code": " BBL "})["bank_code"], "BBL")
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.header({"mystery": 1})

	def test_header_rejects_a_non_mapping(self):
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.header(["bank_code"])

	def test_title_validation(self):
		self.assertEqual(contracts.title_text(" June 2569 "), "June 2569")
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.title_text("")


class DigestContract(unittest.TestCase):
	def setUp(self):
		self.header = contracts.header({"bank_code": "BBL", "stmt_closing": "100"})
		self.statement = contracts.decode_rows([{"description": "SWD", "withdrawal": "10"}], [])[0]
		self.gl = contracts.decode_rows([], [{"doc_no": "GL1", "credit": "10"}])[1]

	def digest(self, header=None, statement=None, gl=None):
		return contracts.digest(header or self.header, statement if statement is not None else self.statement,
			gl if gl is not None else self.gl)

	def test_same_input_same_digest_and_any_change_is_detected(self):
		baseline = self.digest()
		self.assertEqual(baseline, self.digest())
		changed_header = contracts.header({"bank_code": "BBL", "stmt_closing": "101"})
		changed_row = contracts.decode_rows([{"description": "SWD", "withdrawal": "11"}], [])[0]
		self.assertNotEqual(baseline, self.digest(header=changed_header))
		self.assertNotEqual(baseline, self.digest(statement=changed_row))
		self.assertNotEqual(baseline, self.digest(gl=[]))

	def test_json_helpers_are_canonical_and_tolerant_of_empty_text(self):
		self.assertEqual(contracts.json_dumps({"b": 1, "a": 2}), '{"a":2,"b":1}')
		self.assertEqual(contracts.json_loads(None), {})
		self.assertEqual(contracts.json_loads('{"a": 1}'), {"a": 1})


class MatchingConversionContract(unittest.TestCase):
	def test_statement_rows_convert_with_the_deployed_defaults(self):
		rows = contracts.decode_rows([
			{"date": "2026-06-01", "description": "SWD", "withdrawal": "10.5", "deposit": "0",
				"balance": "100.25", "confidence": "low", "balance_ok": "mismatch",
				"autocorrected": True},
		], [])[0]
		converted = contracts.matching_statement_rows(rows)
		item = converted[0]
		self.assertEqual((item.date, item.description, item.withdrawal, item.deposit, item.balance),
			(date(2026, 6, 1), "SWD", 10.5, 0.0, 100.25))
		self.assertEqual(item.confidence, "low")
		self.assertIs(item.balance_ok, False)
		self.assertTrue(item.amount_autocorrected and item.direction_autocorrected)
		self.assertEqual(len(item.row_hash), 12)

	def test_an_unread_balance_stays_unknown_after_conversion(self):
		rows = contracts.decode_rows([{"description": "continuation"}], [])[0]
		self.assertIsNone(contracts.matching_statement_rows(rows)[0].balance)

	def test_gl_rows_convert_to_the_matching_contract(self):
		rows = contracts.decode_rows([], [{"date": "2026-06-01", "doc_no": "GL1",
			"account_code": "1000", "description": "credit", "credit": "400"}])[1]
		item = contracts.matching_gl_rows(rows)[0]
		self.assertEqual((item.date, item.doc_no, item.debit, item.credit),
			(date(2026, 6, 1), "GL1", 0.0, 400.0))


if __name__ == "__main__":
	unittest.main()
