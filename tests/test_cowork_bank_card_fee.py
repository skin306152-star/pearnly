"""刷卡净额预设（第 5 层，用户 2026-09-30 定：B02 做成问一次费率的预设）。离线，不连站点。

数据是 B02 形状的合成数：手续费 = 每笔 × 1.95% × 1.07，按笔四舍五入；晚一天到账；同一天一到两笔合并入账。
"""
import unittest
from datetime import date

from pearnly_cowork.reconciliation import bank_card_fee, presets
from pearnly_cowork.reconciliation.bank_matching import BankReconRow, summarize


def stmt(day, deposit):
	return BankReconRow(match_status="stmt_deposit_only", match_layer=None, stmt_date=date(2026, 5, day),
		stmt_deposit=deposit, stmt_balance=1000.0)


def gl(day, debit, doc="RV"):
	return BankReconRow(match_status="gl_debit_only", match_layer=None, gl_date=date(2026, 5, day), gl_debit=debit,
		gl_doc_no=doc)


def net(*slips, rate=1.95, vat=True):
	return round(sum(slips) - sum(bank_card_fee.fee(slip, rate, vat) for slip in slips), 2)


def b02():
	"""三天刷卡：第 4 天两笔合一笔到账、第 5 / 7 天各一笔（第 7 天那笔隔周末晚三天到）；另有一笔普通收款对不上。"""
	return [
		stmt(5, net(4280.00, 1250.50)), stmt(6, net(9870.25)), stmt(10, net(615.00)), stmt(12, 3000.00),
		gl(4, 4280.00, "RV1"), gl(4, 1250.50, "RV2"), gl(5, 9870.25, "RV3"), gl(7, 615.00, "RV4"), gl(11, 2999.00, "RV5"),
	]


class Pairing(unittest.TestCase):
	def test_fee_is_rounded_per_slip_with_vat(self):
		self.assertEqual(bank_card_fee.fee(4280.00, 1.95, True), 89.30)
		self.assertEqual(bank_card_fee.fee(4280.00, 1.95, False), 83.46)

	def test_b02_shape_pairs_every_card_deposit(self):
		found = bank_card_fee.pairings(b02(), 1.95)
		self.assertEqual([(len(combo), vat) for _stmt, combo, vat in found], [(2, True), (1, True), (1, True)])
		self.assertEqual([slip.gl_doc_no for slip in found[0][1]], ["RV1", "RV2"])

	def test_fee_already_including_vat_also_pairs(self):
		results = [stmt(5, net(5000.00, vat=False)), gl(4, 5000.00)]
		self.assertEqual([vat for _stmt, _combo, vat in bank_card_fee.pairings(results, 1.95)], [False])

	def test_wrong_rate_or_outside_the_window_stays_one_sided(self):
		self.assertEqual(bank_card_fee.pairings(b02(), 2.50), [])
		self.assertEqual(bank_card_fee.pairings([stmt(9, net(9870.25)), gl(5, 9870.25)], 1.95), [])
		# 到账早于刷卡日不算
		self.assertEqual(bank_card_fee.pairings([stmt(4, net(9870.25)), gl(5, 9870.25)], 1.95), [])

	def test_two_ways_to_add_up_is_left_for_a_person(self):
		results = [stmt(5, net(1000.00)), gl(4, 1000.00, "A"), gl(4, 1000.00, "B")]
		self.assertEqual(bank_card_fee.pairings(results, 1.95), [])

	def test_slips_from_different_days_are_not_combined(self):
		results = [stmt(6, net(4280.00, 1250.50)), gl(4, 4280.00), gl(5, 1250.50)]
		self.assertEqual(bank_card_fee.pairings(results, 1.95), [])


class Suggest(unittest.TestCase):
	def test_suggests_the_rate_that_pairs_the_most(self):
		rate, count, _pairs = bank_card_fee.suggest(b02())
		self.assertEqual((rate, count), (1.95, 3))

	def test_nothing_to_suggest_from_a_single_hit_or_no_card_rows(self):
		self.assertIsNone(bank_card_fee.suggest([stmt(5, net(1000.00)), gl(4, 1000.00)]))
		self.assertIsNone(bank_card_fee.suggest([stmt(5, 1000.00), gl(4, 1000.00)]))
		self.assertIsNone(bank_card_fee.suggest([stmt(5, 1000.00)]))


class Accept(unittest.TestCase):
	def test_matched_groups_and_fee_rows_keep_the_formula_balanced(self):
		results = b02()
		stmt_closing, gl_closing = 50000.00, 48000.00
		before = summarize(results, [], [], stmt_closing=stmt_closing, gl_closing=gl_closing)
		rows, count = bank_card_fee.accept(results, "1.95", "刷卡手续费")
		after = summarize(rows, [], [], stmt_closing=stmt_closing, gl_closing=gl_closing)
		self.assertEqual(count, 3)
		self.assertEqual(after.formula_diff, before.formula_diff)
		self.assertEqual((after.matched_count, after.stmt_deposit_only_count, after.gl_debit_only_count), (7, 1, 1))
		fees = [row for row in rows if row.stmt_desc == "刷卡手续费"]
		self.assertEqual([row.match_status for row in fees], ["stmt_withdrawal_only"] * 3)
		self.assertEqual(round(sum(row.stmt_withdrawal for row in fees), 2),
			round(4280.00 + 1250.50 + 9870.25 + 615.00 - net(4280.00, 1250.50) - net(9870.25) - net(615.00), 2))
		self.assertTrue(all(row.stmt_balance is None for row in fees))
		layered = [row for row in rows if row.match_layer == bank_card_fee.CARD_FEE_LAYER]
		self.assertEqual(sorted({row.group_no for row in layered}), [1, 2, 3])

	def test_group_numbers_follow_existing_groups(self):
		results = b02() + [BankReconRow(match_status="matched", match_layer=4, group_no=2)]
		rows, _count = bank_card_fee.accept(results, "1.95", "fee")
		self.assertEqual(sorted({row.group_no for row in rows if row.match_layer == 5}), [3, 4, 5])

	def test_no_or_bad_rate_changes_nothing(self):
		results = b02()
		self.assertEqual(bank_card_fee.accept(results, "abc", "fee"), (results, 0))
		self.assertEqual(bank_card_fee.accept(results, "0", "fee"), (results, 0))


class PresetAnswer(unittest.TestCase):
	def test_card_fee_accepts_a_rate_or_no(self):
		self.assertTrue(presets.valid("bank", presets.CARD_FEE, "1.95"))
		self.assertTrue(presets.valid("bank", presets.CARD_FEE, "1.95%"))
		self.assertTrue(presets.valid("bank", presets.CARD_FEE, presets.NO))
		for bad in ("", "abc", "0", "12", "-1"):
			self.assertFalse(presets.valid("bank", presets.CARD_FEE, bad), bad)
		self.assertFalse(presets.valid("income", presets.CARD_FEE, "1.95"))


if __name__ == "__main__":
	unittest.main()
