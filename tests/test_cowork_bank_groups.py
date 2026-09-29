"""多笔对一笔的疑似合计匹配（用户 2026-09-30 定：只提示、不当作确定匹配）。离线，不连站点。"""
import unittest
from datetime import date

from pearnly_cowork.reconciliation.bank_groups import suspected_groups
from pearnly_cowork.reconciliation.bank_matching import BankReconRow


def stmt(day, deposit=0.0, withdrawal=0.0):
	return BankReconRow(match_status="stmt_deposit_only" if deposit else "stmt_withdrawal_only", match_layer=None,
		stmt_date=date(2026, 3, day), stmt_deposit=deposit, stmt_withdrawal=withdrawal)


def gl(day, debit=0.0, credit=0.0, doc="RE"):
	return BankReconRow(match_status="gl_debit_only" if debit else "gl_credit_only", match_layer=None,
		gl_date=date(2026, 3, day), gl_debit=debit, gl_credit=credit, gl_doc_no=doc)


class SuspectedGroups(unittest.TestCase):
	def test_many_ledger_receipts_against_one_bank_deposit(self):
		# BAY-CA-03：总账逐笔收款、银行一笔合并入账；同几天的总账付款不算进去。
		results = [stmt(20, deposit=600.0), gl(19, debit=100.0), gl(20, debit=200.0), gl(20, debit=300.0),
			gl(20, credit=999.0)]
		groups = suspected_groups(results)
		self.assertEqual(len(groups), 1)
		self.assertEqual((groups[0]["side"], len(groups[0]["members"])), ("stmt", 3))

	def test_many_card_charges_against_one_ledger_total(self):
		# KBANK-CA-04：16 笔信用卡扣款 = 总账一笔 107,403.66。
		results = [gl(20, credit=107403.66, doc="PS69042002")] + [stmt(20, withdrawal=6712.73)] * 15 + [
			stmt(20, withdrawal=round(107403.66 - 15 * 6712.73, 2))]
		groups = suspected_groups(results)
		self.assertEqual([(group["side"], len(group["members"])) for group in groups], [("gl", 16)])

	def test_two_windows_that_both_add_up_are_left_for_a_person(self):
		results = [stmt(10, deposit=300.0), gl(8, debit=100.0), gl(8, debit=200.0), gl(12, debit=150.0),
			gl(12, debit=150.0)]
		self.assertEqual(suspected_groups(results), [])

	def test_matched_rows_and_single_counterparts_are_not_grouped(self):
		self.assertEqual(suspected_groups([stmt(5, deposit=100.0), gl(5, debit=100.0)]), [])


if __name__ == "__main__":
	unittest.main()
