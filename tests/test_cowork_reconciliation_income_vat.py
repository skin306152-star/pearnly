"""Offline regressions for the migrated income (GL vs output-VAT) core."""

import copy
import unittest

from pearnly_cowork.reconciliation import income_vat as income


def gl(doc_no="GL-1", norm_doc_no="gl1", date="2026-06-01", account_code="4110-01",
       description="sale", debit=0.0, credit=1070.0):
    return income.GlRow(doc_no=doc_no, norm_doc_no=norm_doc_no, date=date,
                        account_code=account_code, description=description, debit=debit, credit=credit)


def vat(ref="GL-1", date="2026-06-01", buyer="ACME", amount=1070.0, **extra):
    row = {"report_ref_no": ref, "report_date": date, "report_buyer_name": buyer,
           "report_amount_pre_vat": amount}
    row.update(extra)
    return row


class DocumentNumber(unittest.TestCase):
    def test_reference_number_wins_over_the_invoice_number(self):
        self.assertEqual(income.vat_doc_no({"report_ref_no": "REF-1", "report_reference_no": "REF-2",
                                            "report_invoice_no": "INV-3"}), "REF-1")
        self.assertEqual(income.vat_doc_no({"report_reference_no": "REF-2",
                                            "report_invoice_no": "INV-3"}), "REF-2")
        self.assertEqual(income.vat_doc_no({"report_invoice_no": "INV-3"}), "INV-3")
        self.assertEqual(income.vat_doc_no({"report_ref_no": "", "report_invoice_no": " INV "}), "INV")
        self.assertEqual(income.vat_doc_no({}), "")
        self.assertEqual(income.vat_doc_no({"report_ref_no": 4110}), "4110")

    def test_normalization_uses_the_shared_invoice_reader(self):
        for value in ("GL-1", "gl 1", "GL/1", "ＧＬ-１"):
            with self.subTest(value=value):
                self.assertEqual(income.normalize_doc_no(value), "gl1")
        self.assertEqual(income.normalize_doc_no(None), "")


class DetailRows(unittest.TestCase):
    def test_an_exact_pair_reports_zero_difference_and_sorted_accounts(self):
        rows = [gl(account_code="4110-02", credit=70.0), gl(account_code="4110-01", credit=1000.0)]
        detail, _ = income.reconcile_gl_vat(rows, [vat(amount=1070.0)])
        self.assertEqual(len(detail), 1)
        row = detail[0]
        self.assertEqual((row.doc_no, row.date, row.customer_name, row.vat_amount),
                         ("GL-1", "2026-06-01", "ACME", 1070.0))
        self.assertEqual((row.gl_amount, row.diff), (1070.0, 0.0))
        self.assertEqual(row.account_codes, "4110-01,4110-02")

    def test_a_credit_note_pairs_with_the_negated_debit_column(self):
        rows = [gl(doc_no="CN-1", norm_doc_no="cn1", debit=107.0, credit=0.0)]
        detail, _ = income.reconcile_gl_vat(rows, [vat(ref="CN-1", amount=-107.0)])
        self.assertEqual((detail[0].vat_amount, detail[0].gl_amount, detail[0].diff),
                         (-107.0, -107.0, 0.0))
        # A positive VAT amount on the same ledger row compares credits, not debits.
        detail, _ = income.reconcile_gl_vat(rows, [vat(ref="CN-1", amount=107.0)])
        self.assertEqual((detail[0].gl_amount, detail[0].diff), (0.0, 107.0))

    def test_differences_inside_the_tolerance_are_reported_as_matched(self):
        rows = [gl(credit=1069.99)]
        detail, _ = income.reconcile_gl_vat(rows, [vat(amount=1070.0)])
        self.assertEqual(detail[0].diff, 0.0)
        detail, _ = income.reconcile_gl_vat(rows, [vat(amount=1070.05)])
        self.assertEqual(detail[0].diff, 0.06)
        detail, _ = income.reconcile_gl_vat(rows, [vat(amount=1070.05)], amount_tolerance=5.0)
        self.assertEqual(detail[0].diff, 0.0)

    def test_a_vat_row_without_a_ledger_counterpart(self):
        detail, _ = income.reconcile_gl_vat([gl()], [vat(amount=1070.0),
                                                     vat(ref="MISS-9", amount=300.0)])
        self.assertEqual(len(detail), 2)
        missing = [row for row in detail if row.doc_no == "MISS-9"][0]
        self.assertEqual((missing.gl_amount, missing.diff, missing.account_codes),
                         (None, None, ""))
        self.assertEqual(missing.customer_name, "ACME")

    def test_rows_are_sorted_by_date_then_document_number(self):
        detail, _ = income.reconcile_gl_vat([], [vat(ref="B", date="2026-06-02", amount=1.0),
                                                 vat(ref="A", date="2026-06-02", amount=1.0),
                                                 vat(ref="C", date="", amount=1.0)])
        self.assertEqual([row.doc_no for row in detail], ["C", "A", "B"])

    def test_empty_vat_amounts_are_zero_in_the_detail(self):
        # A missing or blank amount reads as zero in the detail; a non-empty
        # unreadable string only fails later, at the summary total (see below).
        detail, _ = income.reconcile_gl_vat([], [vat(amount=None), vat(amount=""),
                                                 vat(amount=0), vat(amount="1070.00")])
        self.assertEqual([row.vat_amount for row in detail], [0.0, 0.0, 0.0, 1070.0])


class Summary(unittest.TestCase):
    def test_totals_and_the_four_adjustment_buckets(self):
        rows = [gl(doc_no="GL-1", norm_doc_no="gl1", credit=1070.0),
                gl(doc_no="GL-2", norm_doc_no="gl2", description="unmatched credit", credit=500.0),
                gl(doc_no="GL-3", norm_doc_no="gl3", account_code="5000",
                   description="unmatched debit", debit=200.0, credit=0.0)]
        vat_rows = [vat(amount=1070.0), vat(ref="MISS-1", buyer="OTHER", amount=300.0),
                    vat(ref="MISS-2", buyer="OTHER", amount=-50.0)]
        detail, summary = income.reconcile_gl_vat(rows, vat_rows)
        self.assertEqual((summary.gl_total, summary.vat_total), (1370.0, 1320.0))
        self.assertEqual((summary.gl_only_credit, summary.gl_only_debit),
                         (500.0, 200.0))
        self.assertEqual((summary.vat_only_positive, summary.vat_only_negative), (300.0, -50.0))
        self.assertEqual(summary.gl_only_credit_items,
                         [{"doc_no": "GL-2", "date": "2026-06-01", "name": "unmatched credit",
                           "amount": 500.0}])
        self.assertEqual(summary.gl_only_debit_items,
                         [{"doc_no": "GL-3", "date": "2026-06-01", "name": "unmatched debit",
                           "amount": 200.0}])
        self.assertEqual(summary.vat_only_positive_items,
                         [{"doc_no": "MISS-1", "date": "2026-06-01", "name": "OTHER",
                           "amount": 300.0}])
        self.assertEqual(summary.vat_only_negative_items,
                         [{"doc_no": "MISS-2", "date": "2026-06-01", "name": "OTHER",
                           "amount": -50.0}])
        self.assertEqual(len(detail), 3)

    def test_a_ledger_row_without_a_normalized_number_is_always_an_adjustment(self):
        rows = [gl(doc_no="", norm_doc_no="", credit=10.0)]
        _, summary = income.reconcile_gl_vat(rows, [vat(ref="", amount=0.0)])
        self.assertEqual((summary.gl_only_credit, summary.vat_only_positive), (10.0, 0.0))
        self.assertEqual([item["doc_no"] for item in summary.gl_only_credit_items], [""])

    def test_item_lists_default_to_empty_lists(self):
        summary = income.GlVatSummary(gl_total=0.0, gl_only_credit=0.0, gl_only_debit=0.0,
                                     vat_only_positive=0.0, vat_only_negative=0.0, vat_total=0.0)
        self.assertEqual((summary.gl_only_credit_items, summary.gl_only_debit_items,
                          summary.vat_only_positive_items, summary.vat_only_negative_items),
                         ([], [], [], []))
        self.assertEqual(income.GlVatSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0.0).vat_only_negative_items, [])

    def test_empty_inputs_produce_an_empty_result(self):
        detail, summary = income.reconcile_gl_vat([], [])
        self.assertEqual(detail, [])
        self.assertEqual((summary.gl_total, summary.vat_total, summary.gl_only_credit,
                          summary.gl_only_debit, summary.vat_only_positive,
                          summary.vat_only_negative), (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

    def test_the_deployed_vat_total_reader_raises_on_unreadable_amounts(self):
        # The deployed summary total is an unguarded float() over the VAT amounts, so
        # a non-empty unreadable value fails the whole run; the migrated code keeps
        # that difference instead of quietly zeroing the total.
        with self.assertRaises(ValueError) as caught:
            income.reconcile_gl_vat([], [vat(amount="abc")])
        self.assertIn("abc", str(caught.exception))

    def test_input_rows_are_never_mutated(self):
        rows = [gl(), gl(doc_no="GL-2", norm_doc_no="gl2", description="other", credit=500.0)]
        vat_rows = [vat(amount=1070.0), {"report_ref_no": "X", "report_amount_pre_vat": 5.0}]
        rows_before, vat_before = copy.deepcopy(rows), copy.deepcopy(vat_rows)
        income.reconcile_gl_vat(rows, vat_rows)
        self.assertEqual(rows, rows_before)
        self.assertEqual(vat_rows, vat_before)


if __name__ == "__main__":
    unittest.main()
