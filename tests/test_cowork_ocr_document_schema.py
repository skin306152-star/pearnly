"""Offline regressions for the migrated bank-statement and GL document shapes."""

import unittest

from pearnly_cowork.ocr.schema.documents import (
    BankStatementDocument,
    BankStatementEntry,
    GeneralLedgerDocument,
    GLEntry,
)


class BankEntry(unittest.TestCase):
    def test_an_empty_entry_is_all_defaults(self):
        entry = BankStatementEntry()
        self.assertEqual((entry.transaction_date, entry.description, entry.reference), ("", "", ""))
        self.assertEqual((entry.deposit, entry.withdrawal, entry.amount, entry.balance),
            ("", "", "", ""))
        self.assertEqual(entry.direction, "")
        self.assertEqual((entry.deposit_ref, entry.withdrawal_ref, entry.balance_ref),
            (None, None, None))

    def test_the_deployed_audit_markers_start_clear(self):
        entry = BankStatementEntry()
        self.assertFalse(entry.chain_repaired)
        self.assertFalse(entry.chain_amount_imputed)
        self.assertFalse(entry.review_required)

    def test_printed_values_are_kept_as_given(self):
        entry = BankStatementEntry(deposit="1,200.00", withdrawal="", balance="฿1,200.00")
        self.assertEqual(entry.deposit, "1,200.00")
        self.assertEqual(entry.balance, "฿1,200.00")

    def test_none_and_numbers_are_coerced_like_the_deployed_shape(self):
        entry = BankStatementEntry(description=None, deposit=1200.5, withdrawal=0,
            amount=True)
        self.assertEqual(entry.description, "")
        self.assertEqual(entry.deposit, "1200.5")
        self.assertEqual(entry.withdrawal, "0")
        self.assertEqual(entry.amount, "true")

    def test_an_unknown_direction_is_cleared(self):
        self.assertEqual(BankStatementEntry(direction="debit").direction, "")
        self.assertEqual(BankStatementEntry(direction=None).direction, "")
        self.assertEqual(BankStatementEntry(direction="deposit").direction, "deposit")
        self.assertEqual(BankStatementEntry(direction="withdrawal").direction, "withdrawal")


class BankDocument(unittest.TestCase):
    def test_a_null_page_still_builds(self):
        # A model returns null for a continued page or a missing opening balance.
        doc = BankStatementDocument(bank_name=None, opening_balance=None, entries=None)
        self.assertEqual((doc.bank_name, doc.opening_balance, doc.entries), ("", "", []))

    def test_the_document_type_is_fixed(self):
        self.assertEqual(BankStatementDocument().document_type, "bank_statement")

    def test_entries_are_parsed_into_the_row_shape(self):
        doc = BankStatementDocument(entries=[{"deposit": "10.00", "balance": "10.00"}])
        self.assertIsInstance(doc.entries[0], BankStatementEntry)
        self.assertEqual(doc.entries[0].deposit, "10.00")


class GlShape(unittest.TestCase):
    def test_a_null_entry_still_builds(self):
        entry = GLEntry(description=None, debit=None, balance=1000, raw_row_data=None)
        self.assertEqual((entry.description, entry.debit), ("", ""))
        self.assertEqual(entry.balance, "1000")
        self.assertEqual(entry.raw_row_data, {})

    def test_the_document_keeps_the_printed_footer_totals(self):
        doc = GeneralLedgerDocument(total_debit=None, total_credit="1,000.00", entries=None)
        self.assertEqual((doc.total_debit, doc.total_credit), ("", "1,000.00"))
        self.assertEqual(doc.entries, [])
        self.assertEqual(doc.document_type, "general_ledger")

    def test_the_row_keeps_its_provenance_and_raw_columns(self):
        entry = GLEntry(
            debit="10.00",
            debit_ref={"value": 10, "source_column": "Debit"},
            raw_row_data={"Debit": "10.00"},
        )
        self.assertEqual(entry.debit_ref.source_column, "Debit")
        self.assertEqual(entry.raw_row_data, {"Debit": "10.00"})
