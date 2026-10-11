"""刷卡净额按总账实际手续费配（第 6 层）与单一账户明细账（2026-10-11，清单第 17a 项）。离线，不连站点。

数字取自 B02 TTB 2026-05 的真实总账与账单：刷卡毛额记借方、到账当天另记一笔实际手续费（贷方，对方科目 10003）。
"""
import unittest
from datetime import date

from pearnly_cowork.reconciliation import bank_net_settlement
from pearnly_cowork.reconciliation.bank_matching import BankReconRow, GlRow
from pearnly_cowork.reconciliation.bank_recon_service import single_account_ledger


def stmt(day, deposit):
	return BankReconRow(match_status="stmt_deposit_only", match_layer=None, stmt_date=date(2026, 5, day),
		stmt_deposit=deposit, stmt_balance=1000.0)


def debit(day, amount, desc="CARD"):
	return BankReconRow(match_status="gl_debit_only", match_layer=None, gl_date=date(2026, 5, day), gl_debit=amount,
		gl_desc=desc)


def credit(day, amount, desc="FEE"):
	return BankReconRow(match_status="gl_credit_only", match_layer=None, gl_date=date(2026, 5, day), gl_credit=amount,
		gl_desc=desc)


def ledger(*rows):
	"""(科目, 借, 贷)：余额按读出顺序连续累计，像 Express「G/L By Account Code」那样。"""
	out, balance = [], 0.0
	for code, dr, cr in rows:
		balance = round(balance + dr - cr, 2)
		out.append(GlRow(date(2026, 5, 2), "", code, "", dr, cr, balance=balance, balance_read=True))
	return out


class NetSettlement(unittest.TestCase):
	def test_b02_card_deposits_pair_with_the_ledger_fee(self):
		rows = [stmt(3, 14782.29), stmt(6, 12152.32), debit(2, 3893.25, "CARD RY"), debit(2, 11200.33, "CARD PD"),
				credit(3, 311.29), debit(5, 12343.84, "CARD PD"), credit(6, 191.52), stmt(9, 3000.00)]
		results, count = bank_net_settlement.accept(rows)
		self.assertEqual(count, 2)
		self.assertEqual([r.match_status for r in results].count("matched"), 7)
		self.assertTrue(all(r.match_layer == 6 for r in results if r.match_status == "matched"))
		self.assertEqual([r.stmt_deposit for r in results if r.match_status != "matched"], [3000.00])
		# 手续费是总账真记了的：不另造「银行已扣、总账未记」的行
		self.assertEqual(len(results), len(rows))

	def test_more_than_one_way_to_make_the_deposit_is_left_for_a_person(self):
		# 到账 400：两笔不同的 500 各自减手续费 100 都凑得上，分不清是哪一笔
		rows = [stmt(3, 400.00), debit(2, 500.00, "A"), debit(2, 500.00, "B"), credit(3, 100.00)]
		self.assertEqual(bank_net_settlement.accept(rows)[1], 0)
		self.assertNotIn("matched", {r.match_status for r in rows})

	def test_without_a_fee_entry_nothing_changes(self):
		rows = [stmt(3, 14782.29), debit(2, 3893.25), debit(2, 11200.33)]
		self.assertEqual(bank_net_settlement.accept(rows)[1], 0)
		self.assertEqual({r.match_status for r in rows}, {"stmt_deposit_only", "gl_debit_only"})


class SingleAccountLedger(unittest.TestCase):
	def test_counter_account_column_is_one_ledger(self):
		# B02：C1111 行之间夹着 10003（手续费）、2402010（收款）的行，余额一条链连下来
		rows = ledger(("C1111", 1727.46, 0), ("C1111", 3576.18, 0), ("10003", 0, 107.00), ("C1111", 2153.91, 0),
					  ("10003", 0, 311.29), ("2402010", 898200.00, 0), ("C1111", 1361.04, 0))
		self.assertTrue(single_account_ledger(rows))

	def test_real_multi_account_ledger_is_not_one_ledger(self):
		# 每个科目的余额各自从头算：换科目处接不上
		first = ledger(("1111", 100, 0), ("1111", 50, 0), ("1111", 0, 20))
		second = ledger(("1112", 500, 0), ("1112", 0, 80), ("1112", 40, 0))
		self.assertFalse(single_account_ledger(first + second))

	def test_computed_balances_are_not_evidence(self):
		# Excel 总账的余额是现算的（`gl_sheets.attach_running_balance`），不能拿来下结论
		rows = ledger(("1111", 100, 0), ("1112", 50, 0), ("1113", 0, 20))
		for row in rows:
			row.balance_read = False
		self.assertFalse(single_account_ledger(rows))

	def test_one_account_only_is_left_alone(self):
		self.assertFalse(single_account_ledger(ledger(("C1111", 10, 0), ("C1111", 20, 0), ("C1111", 0, 5))))


if __name__ == "__main__":
	unittest.main()
