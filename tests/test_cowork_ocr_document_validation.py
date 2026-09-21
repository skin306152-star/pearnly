"""Offline regressions for the migrated source-column validators.

Runs in the lab bench (the validation package imports pydantic). The point of
these rules is the deployed "6091 case": a number printed in a description column
must never be read as an amount, and when a reference is rejected the value it
carried must be cleared so nothing downstream can use it.
"""

import unittest

from pearnly_cowork.ocr.schema.documents import (
    BankStatementDocument,
    BankStatementEntry,
    GeneralLedgerDocument,
    GLEntry,
)
from pearnly_cowork.ocr.validation import (
    sources,
    validate_bank_document,
    validate_gl_document,
)


def gl_row(**changes):
    values = {"voucher_no": "JV1", "description": "", "debit": "", "credit": "",
        "amount": "", "direction": "", "balance": ""}
    values.update(changes)
    return GLEntry(**values)


def bank_row(**changes):
    values = {"reference": "", "description": "", "deposit": "", "withdrawal": "",
        "amount": "", "direction": "", "balance": ""}
    values.update(changes)
    return BankStatementEntry(**values)


def gl_document(*rows):
    return GeneralLedgerDocument(entries=list(rows))


def bank_document(*rows):
    return BankStatementDocument(entries=list(rows))


class ValueInDescription(unittest.TestCase):
    def test_a_standalone_token_is_found(self):
        self.assertTrue(sources._value_appears_in_description("6091", "ค่าอาหาร 6091 บาท"))
        self.assertTrue(sources._value_appears_in_description("1250", "ค่าเช่า 1250 บาท"))

    def test_a_number_inside_a_longer_number_is_not_a_match(self):
        # "Invoice 6091/2024" style: the token is part of something else.
        self.assertFalse(sources._value_appears_in_description("609", "ค่าอาหาร 6091"))
        self.assertFalse(sources._value_appears_in_description("6091", "อ้างอิง 60912"))

    def test_trailing_zeros_are_stripped_and_retried(self):
        # A debit printed as 6091.00 still matches a description that says 6091.
        self.assertTrue(sources._value_appears_in_description("6091.00", "ค่าอาหาร 6091"))
        self.assertTrue(sources._value_appears_in_description("6091.0", "ค่าอาหาร 6091"))

    def test_thousands_separators_are_stripped_from_the_value_only(self):
        self.assertTrue(sources._value_appears_in_description("1,250.00", "ค่าเช่า 1250"))
        # 部署版只对**值**去逗号，说明原文不动：所以说明里印成 "1,250.00" 时反而不匹配。
        # 这是旧行为，照原样保留（差分里正是这么比的）。
        self.assertFalse(sources._value_appears_in_description("1,250.00", "1,250.00 ค่าเช่า"))

    def test_empty_input_is_never_a_match(self):
        for value, description in (("", "6091"), ("6091", ""), (",", "1"), ("0.00", "")):
            with self.subTest(value=value, description=description):
                self.assertFalse(sources._value_appears_in_description(value, description))


class ColumnKeywords(unittest.TestCase):
    def test_description_voucher_and_account_columns_are_rejected(self):
        for column, expected in (("Description", "description"), ("รายการ", "description"),
                                 ("摘要", "description"), ("Voucher No.", "voucher-number"),
                                 ("เลขที่เอกสาร", "voucher-number"), ("凭证号", "voucher-number"),
                                 ("Account Code", "account-code"), ("รหัสบัญชี", "account-code"),
                                 ("科目代码", "account-code")):
            with self.subTest(column=column):
                reason = sources._is_forbidden_amount_source(column)
                self.assertIsNotNone(reason, column)
                self.assertIn(expected, reason)

    def test_trailing_punctuation_and_case_do_not_hide_a_rejection(self):
        self.assertIsNotNone(sources._is_forbidden_amount_source("  DESCRIPTION:  "))
        self.assertIsNotNone(sources._is_forbidden_amount_source("备注•"))

    def test_an_amount_column_or_an_unrecorded_one_is_not_rejected(self):
        for column in ("Debit", "Credit", "Balance", "", "ยอดคงเหลือ", "余额"):
            with self.subTest(column=column):
                self.assertIsNone(sources._is_forbidden_amount_source(column))

    def test_the_right_column_is_required_per_amount_kind(self):
        self.assertTrue(sources._is_allowed_gl_amount_source("Debit", "debit"))
        self.assertTrue(sources._is_allowed_gl_amount_source("เดบิต", "debit"))
        self.assertFalse(sources._is_allowed_gl_amount_source("Credit", "debit"))
        self.assertTrue(sources._is_allowed_bank_amount_source("Withdrawal", "withdrawal"))
        self.assertFalse(sources._is_allowed_bank_amount_source("Deposit", "withdrawal"))
        # A balance column is the same list on both sides, by the deployed rule.
        self.assertTrue(sources._is_allowed_bank_amount_source("ยอดคงเหลือ", "balance"))
        # An unrecorded column cannot be disproved, so it is allowed.
        self.assertTrue(sources._is_allowed_gl_amount_source("", "debit"))


class GlDocument(unittest.TestCase):
    def test_the_deployed_6091_case_is_reported(self):
        # A row whose description carries 6091 and whose debit repeats it.
        doc = gl_document(gl_row(description="ค่าอาหาร 6091", debit="6091.00"))
        warnings = validate_gl_document(doc, None)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("debit='6091.00' also appears in description=", warnings[0])
        self.assertIn("possibly mis-sourced from description column", warnings[0])

    def test_a_number_that_is_also_the_voucher_number_is_left_alone(self):
        doc = gl_document(gl_row(voucher_no="6091", description="ค่าอาหาร 6091", debit="6091.00"))
        self.assertEqual(validate_gl_document(doc, None), [])

    def test_a_forbidden_reference_is_rejected_and_its_value_cleared(self):
        entry = gl_row(description="ค่าอาหาร", credit="250.00", amount="250.00",
            direction="withdrawal", credit_ref={"value": 250, "source_column": "Description"})
        warnings = validate_gl_document(gl_document(entry), None)
        self.assertIn("credit_ref=250 sourced from description column", warnings[0])
        self.assertIn("— rejected", warnings[0])
        self.assertEqual(entry.credit, "")
        self.assertIsNone(entry.credit_ref)
        # The direction and the derived magnitude go with the value.
        self.assertEqual(entry.direction, "")
        self.assertEqual(entry.amount, "")

    def test_a_balance_reference_rejected_leaves_direction_alone(self):
        entry = gl_row(balance="100.00",
            balance_ref={"value": 100, "source_column": "หมายเหตุ"})
        validate_gl_document(gl_document(entry), None)
        self.assertEqual(entry.balance, "")
        self.assertIsNone(entry.balance_ref)

    def test_the_wrong_amount_column_is_reported_without_clearing(self):
        entry = gl_row(credit="250.00", credit_ref={"value": 250, "source_column": "Debit"})
        warnings = validate_gl_document(gl_document(entry), None)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("is not a credit column", warnings[0])
        self.assertEqual(entry.credit, "250.00")

    def test_both_sides_non_zero_is_reported(self):
        doc = gl_document(gl_row(debit="10.00", credit="20.00"))
        warnings = validate_gl_document(doc, None)
        self.assertTrue(any("both debit (10.0) AND credit (20.0) non-zero" in w for w in warnings),
            warnings)

    def test_a_non_numeric_side_is_reported(self):
        warnings = validate_gl_document(gl_document(gl_row(debit="n/a")), None)
        self.assertTrue(any("debit/credit not numeric" in w for w in warnings), warnings)

    def test_the_direction_must_agree_with_the_split(self):
        deposit = validate_gl_document(gl_document(gl_row(direction="deposit")), None)
        withdrawal = validate_gl_document(
            gl_document(gl_row(direction="withdrawal", debit="5.00")), None)
        self.assertTrue(any("direction=deposit but debit=0.0" in w for w in deposit), deposit)
        self.assertTrue(any("direction=withdrawal but credit=0.0" in w for w in withdrawal),
            withdrawal)

    def test_the_derived_amount_must_match_debit_or_credit(self):
        mismatched = validate_gl_document(
            gl_document(gl_row(debit="10.00", amount="99.00")), None)
        matched = validate_gl_document(gl_document(gl_row(debit="10.00", amount="10.00")), None)
        self.assertTrue(any("doesn't match debit-or-credit=10.0" in w for w in mismatched),
            mismatched)
        self.assertEqual(matched, [])

    def test_a_non_numeric_amount_is_reported(self):
        warnings = validate_gl_document(
            gl_document(gl_row(debit="10.00", amount="ten")), None)
        self.assertTrue(any("amount='ten' not numeric" in w for w in warnings), warnings)

    def test_the_row_id_falls_back_to_its_position(self):
        warnings = validate_gl_document(
            gl_document(gl_row(voucher_no="", debit="10.00", credit="20.00")), None)
        self.assertTrue(warnings[0].startswith("GL row#1:"), warnings)

    def test_a_clean_document_produces_nothing(self):
        doc = gl_document(gl_row(debit="10.00", direction="deposit", amount="10.00"),
            gl_row(voucher_no="JV2", credit="5.00", direction="withdrawal", amount="5.00"))
        self.assertEqual(validate_gl_document(doc, None), [])


class BankDocument(unittest.TestCase):
    def test_a_value_repeated_in_the_description_is_reported(self):
        doc = bank_document(bank_row(description="โอน 1250 บาท", deposit="1250.00"))
        warnings = validate_bank_document(doc, None)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("deposit='1250.00' also appears in description=", warnings[0])

    def test_a_value_that_is_also_the_reference_is_left_alone(self):
        doc = bank_document(bank_row(reference="1250", description="โอน 1250 บาท",
            deposit="1250.00"))
        self.assertEqual(validate_bank_document(doc, None), [])

    def test_a_forbidden_reference_is_rejected_and_its_value_cleared(self):
        entry = bank_row(description="ถอนเงิน", withdrawal="500.00", amount="500.00",
            direction="withdrawal", withdrawal_ref={"value": 500, "source_column": "备注"})
        warnings = validate_bank_document(bank_document(entry), None)
        self.assertIn("withdrawal_ref=500 sourced from description column", warnings[0])
        self.assertEqual(entry.withdrawal, "")
        self.assertIsNone(entry.withdrawal_ref)
        self.assertEqual(entry.direction, "")
        self.assertEqual(entry.amount, "")

    def test_the_wrong_column_is_reported(self):
        entry = bank_row(deposit="10.00",
            deposit_ref={"value": 10, "source_column": "Withdrawal"})
        warnings = validate_bank_document(bank_document(entry), None)
        self.assertIn("is not a deposit column", warnings[0])

    def test_both_sides_non_zero_is_reported(self):
        warnings = validate_bank_document(
            bank_document(bank_row(deposit="10.00", withdrawal="20.00")), None)
        self.assertTrue(any("both deposit (10.0) AND withdrawal (20.0) non-zero" in w
            for w in warnings), warnings)

    def test_a_non_numeric_side_is_silent_on_the_bank_side(self):
        # The deployed bank rule swallows the parse failure (unlike the GL rule).
        warnings = validate_bank_document(bank_document(bank_row(deposit="n/a")), None)
        self.assertEqual(warnings, [])

    def test_the_row_id_falls_back_to_its_position(self):
        warnings = validate_bank_document(
            bank_document(bank_row(reference="", deposit="10.00", withdrawal="20.00")), None)
        self.assertTrue(warnings[0].startswith("Bank tx#1:"), warnings)

    def test_a_clean_document_produces_nothing(self):
        doc = bank_document(bank_row(reference="R1", description="เงินเข้า",
            deposit="100.00", balance="100.00"))
        self.assertEqual(validate_bank_document(doc, None), [])


class PortedSurface(unittest.TestCase):
    def test_the_dead_amount_token_pattern_is_not_carried_over(self):
        # The deployed source defines it and never uses it; there is no rule to keep.
        self.assertFalse(hasattr(sources, "_AMOUNT_TOKEN_RE"))

    def test_the_column_keyword_tables_are_the_deployed_ones(self):
        self.assertIn("description", sources._DESCRIPTION_COLUMN_KEYWORDS)
        self.assertIn("备注", sources._DESCRIPTION_COLUMN_KEYWORDS)
        self.assertIn("凭证号", sources._VOUCHER_COLUMN_KEYWORDS)
        self.assertIn("科目代码", sources._ACCOUNT_CODE_COLUMN_KEYWORDS)
        self.assertEqual(sources._BANK_BALANCE_COLUMNS, sources._GL_BALANCE_COLUMNS)
