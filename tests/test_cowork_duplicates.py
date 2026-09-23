import unittest
from decimal import Decimal

from pearnly_cowork.contracts import duplicates as rules


class DuplicateRules(unittest.TestCase):
    def setUp(self):
        self.row = {"invoice_no": "INV-01", "invoice_date": "2026-09-22",
                    "seller_name": "Acme", "total_amount": "107.00"}

    def test_invoice_number_ignores_case_but_not_accents_or_internal_spaces(self):
        current = rules.terms(dict(self.row, invoice_no="  inv-01  "))
        self.assertTrue(rules.matches(current, self.row, "exact"))
        for other in ("ÍNV-01", "INV -01", "INV-01 "):
            self.assertFalse(rules.matches(current, dict(self.row, invoice_no=other), "exact"))

    def test_likely_can_match_different_invoice_numbers(self):
        current = rules.terms(dict(self.row, invoice_no="different", seller_name=" acME "))
        self.assertFalse(rules.matches(current, self.row, "exact"))
        self.assertTrue(rules.matches(current, self.row, "likely"))

    def test_three_fields_are_required(self):
        for key in ("invoice_date", "seller_name", "total_amount"):
            current = rules.terms(dict(self.row, **{key: None}))
            self.assertFalse(rules.matches(current, self.row, "likely"))

    def test_money_is_exact_and_zero_is_valid(self):
        for value in ("107", "107.0000", Decimal("107")):
            self.assertTrue(rules.matches(rules.terms(dict(self.row, total_amount=value)), self.row, "likely"))
        row = dict(self.row, total_amount="0")
        self.assertTrue(rules.matches(rules.terms(row), dict(row, total_amount="0.00"), "likely"))
        row = dict(self.row, total_amount="9007199254740993.0000000000000000001")
        self.assertFalse(rules.matches(rules.terms(row), dict(row, total_amount="9007199254740993.0000000000000000002"), "likely"))

    def test_nonfinite_and_invalid_amounts_never_match(self):
        for value in ("NaN", "Infinity", "-Infinity", "n/a", True, None):
            current = rules.terms(dict(self.row, invoice_no="", total_amount=value))
            self.assertFalse(rules.matches(current, self.row, "likely"))

    def test_changed_date_seller_or_amount_does_not_match(self):
        for key, value in (("invoice_date", "2026-09-21"), ("seller_name", "Acmé"), ("total_amount", "107.01")):
            self.assertFalse(rules.matches(rules.terms(self.row), dict(self.row, **{key: value}), "likely"))


if __name__ == "__main__":
    unittest.main()
