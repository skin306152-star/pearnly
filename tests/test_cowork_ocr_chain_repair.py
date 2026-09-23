"""Offline regressions for the migrated balance-chain repairs.

Both repairers work on plain objects with the documented fields, so the fixtures
are simple stand-ins: the pydantic schema they are handed in production is covered
in test_cowork_ocr_document_schema.py. The package import pulls in pydantic, so this
file runs in the lab bench like the rest of the OCR-family checks.
"""

import unittest
from decimal import Decimal
from types import SimpleNamespace

from pearnly_cowork.ocr.validation import chains


def bank_row(deposit="", withdrawal="", balance="", direction="", **extra):
    row = SimpleNamespace(deposit=deposit, withdrawal=withdrawal, balance=balance,
        direction=direction, chain_repaired=False, chain_amount_imputed=False,
        review_required=False)
    for key, value in extra.items():
        setattr(row, key, value)
    return row


def gl_row(debit="", credit="", balance="", direction="", description="", raw_row_data=None):
    return SimpleNamespace(debit=debit, credit=credit, balance=balance, direction=direction,
        description=description, raw_row_data={} if raw_row_data is None else raw_row_data)


def gl_document(entries, opening="", closing="", total_debit="", total_credit=""):
    return SimpleNamespace(entries=entries, opening_balance=opening, closing_balance=closing,
        total_debit=total_debit, total_credit=total_credit)


class BankChainAccepts(unittest.TestCase):
    def test_a_consistent_chain_is_left_untouched(self):
        rows = [bank_row("", "", "1000.00"), bank_row("500.00", "", "1500.00"),
            bank_row("", "200.50", "1299.50")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        self.assertEqual([(r.deposit, r.withdrawal) for r in out],
            [("", ""), ("500.00", ""), ("", "200.50")])
        self.assertFalse(any(r.chain_repaired or r.chain_amount_imputed for r in out))

    def test_a_carried_forward_row_passes_when_it_continues_the_balance(self):
        rows = [bank_row("", "", "1000.00"), bank_row("100.00", "", "1100.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        self.assertEqual(out[0].balance, "1000.00")

    def test_an_empty_page_is_released(self):
        self.assertEqual(chains.repair_bank_chain([], "100.00"), ([], []))
        self.assertEqual(chains.repair_bank_chain(None, "100.00"), ([], []))

    def test_the_opening_row_need_not_be_first(self):
        # The chain starts from the printed opening, so a leading carried-forward
        # row outside the period still has to continue it.
        rows = [bank_row("", "", "1000.00"), bank_row("", "", "1000.00"),
            bank_row("50.00", "", "1050.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])


class BankChainRepairs(unittest.TestCase):
    def test_a_swapped_row_is_flipped_and_marked(self):
        # A real deposit of 1,200 and withdrawal of 300, printed the other way
        # round: only the swapped equation reproduces the printed balance.
        rows = [bank_row("", "", "1000.00"), bank_row("300.00", "1200.00", "1900.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        self.assertEqual((out[1].deposit, out[1].withdrawal), ("1200.00", "300.00"))
        self.assertTrue(out[1].chain_repaired)
        # A proved swap is arithmetic, so it is not sent for review.
        self.assertFalse(out[1].review_required)

    def test_a_swapped_row_takes_its_direction_with_it(self):
        rows = [bank_row("", "", "1000.00"),
            bank_row("300.00", "1200.00", "1900.00", direction="withdrawal")]
        out, _ = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(out[1].direction, "deposit")

    def test_a_single_sided_misread_is_inferred_from_the_chain(self):
        # The printed balance says 1,200 moved in but the row reads 1,100.
        rows = [bank_row("", "", "1000.00"), bank_row("1100.00", "", "2200.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        self.assertEqual(out[1].deposit, "1200.00")
        self.assertTrue(out[1].chain_amount_imputed)
        # An inferred amount has no second evidence, so it must be reviewed.
        self.assertTrue(out[1].review_required)

    def test_an_inferred_amount_lands_in_the_column_the_sign_names(self):
        # The balance fell by 100 while the row reads a withdrawal of 90: close
        # enough to be the same movement read short, so it is inferred to 100.
        rows = [bank_row("", "", "1000.00"), bank_row("", "90.00", "900.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        self.assertEqual((out[1].deposit, out[1].withdrawal), ("", "100.00"))
        self.assertTrue(out[1].chain_amount_imputed)

    def test_a_reading_on_the_wrong_side_goes_to_the_column_the_sign_names(self):
        # The same 100 movement, but read as a deposit of 90: the value is close
        # enough to be believable, and the sign of the movement names the column.
        rows = [bank_row("", "", "1000.00"),
            bank_row("90.00", "", "900.00", direction="deposit")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        self.assertEqual((out[1].deposit, out[1].withdrawal), ("", "100.00"))
        self.assertEqual(out[1].direction, "withdrawal")

    def test_a_row_without_a_direction_does_not_gain_one(self):
        # The repair synchronises a direction that is printed, never invents one.
        rows = [bank_row("", "", "1000.00"), bank_row("90.00", "", "900.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        self.assertEqual(out[1].direction, "")
        self.assertTrue(out[1].chain_amount_imputed)


class BankChainRefuses(unittest.TestCase):
    def test_an_out_of_band_amount_is_refused_rather_than_invented(self):
        # 1,100 read against a 200 movement: the reading is not believable, so the
        # row stays a break instead of being overwritten from a suspect balance.
        rows = [bank_row("", "", "1000.00"), bank_row("1100.00", "", "1200.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(out[1].deposit, "1100.00")
        self.assertEqual(len(reasons), 1)
        self.assertIn("余额链断", reasons[0])

    def test_two_filled_sides_are_never_guessed(self):
        rows = [bank_row("", "", "1000.00"), bank_row("500.00", "400.00", "2000.00")]
        _, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(len(reasons), 1)

    def test_equal_sides_have_no_unique_swap(self):
        rows = [bank_row("", "", "1000.00"), bank_row("300.00", "300.00", "1500.00")]
        _, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(len(reasons), 1)

    def test_a_missing_opening_balance_cannot_start_a_chain(self):
        rows = [bank_row("100.00", "", "1100.00")]
        _, reasons = chains.repair_bank_chain(rows, "")
        self.assertIn("opening_balance 缺失", reasons[0])

    def test_a_missing_or_unparsable_value_breaks_the_row(self):
        _, reasons = chains.repair_bank_chain([bank_row("100.00", "", "")], "1000.00")
        self.assertIn("balance 缺失", reasons[0])
        _, reasons = chains.repair_bank_chain([bank_row("x", "", "1000.00")], "1000.00")
        self.assertIn("不可解析", reasons[0])

    def test_a_carried_forward_row_that_jumps_is_refused(self):
        rows = [bank_row("", "", "1000.00"), bank_row("", "", "1500.00")]
        _, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertIn("未承接上行", reasons[0])

    def test_only_the_first_break_is_reported(self):
        rows = [bank_row("", "", "1000.00"), bank_row("1.00", "", "5000.00"),
            bank_row("2.00", "", "6000.00")]
        _, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(len(reasons), 1)

    def test_warnings_name_the_broken_page_and_every_inferred_row(self):
        rows = [bank_row("", "", "1000.00"), bank_row("1100.00", "", "2200.00"),
            bank_row("", "90.00", "2100.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(reasons, [])
        warns = chains.bank_chain_warnings(out, ["行 3: 余额链断"])
        self.assertEqual(len(warns), 3)
        self.assertIn("max 重读链校验", warns[0])
        self.assertEqual(warns[1], "行 2 金额为链推定(印刷余额反推)覆写,待复核")
        self.assertEqual(warns[2], "行 3 金额为链推定(印刷余额反推)覆写,待复核")

    def test_a_clean_chain_produces_no_warning(self):
        rows = [bank_row("", "", "1000.00"), bank_row("300.00", "", "1300.00")]
        out, reasons = chains.repair_bank_chain(rows, "1000.00")
        self.assertEqual(chains.bank_chain_warnings(out, reasons), [])


class GlChain(unittest.TestCase):
    def test_a_wrong_side_is_put_right_and_marked(self):
        # The balance moved up by 1,000 while the amount is printed as a credit.
        doc = gl_document([gl_row("", "1000.00", "2000.00")], opening="1000.00")
        warnings = chains.repair_gl_document(doc)
        self.assertEqual(warnings, [])
        self.assertEqual(doc.entries[0].debit, "1000.00")
        self.assertEqual(doc.entries[0].credit, "")
        self.assertEqual(doc.entries[0].direction, "deposit")
        self.assertTrue(doc.entries[0].raw_row_data["direction_autocorrected"])

    def test_a_break_is_warned_about_and_left_alone(self):
        doc = gl_document([gl_row("", "1000.00", "5000.00")], opening="1000.00")
        warnings = chains.repair_gl_document(doc)
        self.assertEqual(len(warnings), 1)
        self.assertIn("GL 余额链断裂 行1", warnings[0])
        self.assertEqual(doc.entries[0].credit, "1000.00")

    def test_a_balance_less_row_hides_the_movement_across_it(self):
        # A row without a balance breaks prev, so the following movement is not
        # compared with a single row's amount and is never "corrected".
        doc = gl_document([gl_row("", "1000.00", ""), gl_row("", "300.00", "2300.00")],
            opening="1000.00")
        warnings = chains.repair_gl_document(doc)
        self.assertEqual(warnings, [])
        self.assertEqual(doc.entries[1].credit, "300.00")
        self.assertFalse(doc.entries[1].raw_row_data.get("direction_autocorrected", False))

    def test_the_printed_footer_settles_the_first_row_direction(self):
        # Without a printed opening balance the first row has nothing to check
        # against; the footer totals are then the only deterministic source.
        doc = gl_document([gl_row("", "1000.00", "2000.00")], total_debit="1000.00",
            total_credit="0.00")
        warnings = chains.repair_gl_document(doc)
        self.assertEqual(warnings, [])
        self.assertEqual(doc.entries[0].debit, "1000.00")
        self.assertTrue(doc.entries[0].raw_row_data["direction_autocorrected"])

    def test_footer_totals_that_stay_off_after_flipping_warn_instead(self):
        doc = gl_document([gl_row("", "1000.00", "2000.00"), gl_row("", "50.00", "2050.00")],
            total_debit="1.00", total_credit="2.00")
        warnings = chains.repair_gl_document(doc)
        self.assertTrue(any("GL 借贷合计与明细对不上" in w for w in warnings), warnings)

    def test_the_opening_balance_is_derived_when_it_is_not_printed(self):
        doc = gl_document([gl_row("1000.00", "", "2000.00")])
        chains.repair_gl_document(doc)
        self.assertEqual(doc.opening_balance, "1000.00")

    def test_matching_totals_leave_the_first_row_alone(self):
        doc = gl_document([gl_row("", "1000.00", "2000.00")], opening="1000.00",
            total_debit="0.00", total_credit="1000.00")
        self.assertEqual(chains.repair_gl_document(doc), [])
        self.assertEqual(doc.entries[0].credit, "1000.00")

    def test_the_closing_balance_is_backfilled_from_the_last_printed_row(self):
        doc = gl_document([gl_row("", "1000.00", "2000.00")], opening="1000.00")
        chains.repair_gl_document(doc)
        self.assertEqual(doc.closing_balance, "2000.00")

    def test_an_empty_document_is_released(self):
        doc = gl_document([])
        self.assertEqual(chains.repair_gl_document(doc), [])

    def test_the_tolerances_are_exposed_for_a_caller(self):
        self.assertEqual(chains.BANK_TOL, Decimal("0.01"))
        self.assertEqual(chains.GL_TOL, Decimal("0.05"))
        self.assertEqual(chains.IMPUTE_BAND, Decimal("0.2"))
