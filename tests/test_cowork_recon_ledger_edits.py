"""银行对账复核里改总账金额：GL 期末与各行余额跟着走（`reconciliation.revision._follow_ledger_edits`）。"""
import unittest
from decimal import Decimal
from unittest import mock

from pearnly_cowork.reconciliation import bank_recon_contracts, revision

STORED = [
    {"date": "2026-09-01", "doc_no": "RV6909-001", "account_code": "", "description": "", "debit": "1000", "credit": "0",
     "balance": "241000"},
    {"date": "2026-09-05", "doc_no": "RV6909-005", "account_code": "", "description": "", "debit": "12480", "credit": "0",
     "balance": "253480"},
    {"date": "2026-09-09", "doc_no": "PV6909-002", "account_code": "", "description": "", "debit": "0", "credit": "763.86",
     "balance": "252716.14"},
]


def run(edited):
    header = {"gl_closing": Decimal("252716.14")}
    _, ledger = bank_recon_contracts.decode_rows([], edited)
    with mock.patch.object(revision, "bank_inputs", return_value={"gl": STORED}):
        revision._follow_ledger_edits(object(), header, ledger)
    return header["gl_closing"], [row["balance"] for row in ledger]


class LedgerEdits(unittest.TestCase):
    def test_an_edited_amount_moves_the_closing_and_the_running_balances(self):
        # 2026-09-28 录制：RV6909-005 借方 12,480 → 12,840，期末仍是 252,716.14，差额变成 360
        edited = [dict(row) for row in STORED]
        edited[1]["debit"] = "12840"
        closing, balances = run(edited)
        self.assertEqual(closing, Decimal("253076.14"))
        self.assertEqual(balances, [Decimal("241000"), Decimal("253840"), Decimal("253076.14")])

    def test_untouched_amounts_keep_the_file_values(self):
        closing, balances = run([dict(row) for row in STORED])
        self.assertEqual(closing, Decimal("252716.14"))
        self.assertEqual(balances, [Decimal("241000"), Decimal("253480"), Decimal("252716.14")])


if __name__ == "__main__":
    unittest.main()
