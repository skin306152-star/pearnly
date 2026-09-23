"""Offline synthetic-workbook contracts; no Site or external calls."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import unittest
from unittest.mock import patch
import zipfile

from openpyxl import Workbook
from pearnly_cowork.roundtrip import money, spec
from pearnly_cowork.roundtrip.contracts import RoundtripNotMatched, RoundtripParseError, to_pipeline
from pearnly_cowork.roundtrip.parser import _num, parse_roundtrip_workbook

SALES_HEADERS = [spec.SALES_COL_DATE, spec.SALES_COL_INVOICE, spec.SALES_COL_PARTY,
    spec.SALES_COL_ITEM, spec.SALES_COL_QTY, spec.SALES_COL_PRICE, spec.SALES_COL_AMOUNT,
    spec.SALES_COL_PRE_VAT, *spec.ROUNDTRIP_HEADERS]
PURCHASE_HEADERS = [*spec.PURCHASE_HEADERS, *spec.ROUNDTRIP_HEADERS]
PENDING_HEADERS = [*spec.PENDING_HEADERS, *spec.ROUNDTRIP_HEADERS]


def workbook_bytes(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, headers, rows in sheets:
        sheet = workbook.create_sheet(name)
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(header) for header in headers] if isinstance(row, dict) else row)
    result = io.BytesIO()
    workbook.save(result)
    workbook.close()
    return result.getvalue()


def sales_row(**values):
    row = {spec.SALES_COL_DATE: datetime(2026, 9, 21), spec.SALES_COL_INVOICE: "INV-1",
        spec.SALES_COL_PARTY: "Buyer", spec.SALES_COL_ITEM: "Tea", spec.SALES_COL_QTY: 1,
        spec.SALES_COL_PRICE: 100, spec.SALES_COL_AMOUNT: "=E2*F2",
        spec.SALES_COL_PRE_VAT: "=G2", spec.COL_PARTY_TAX: "0105551234567",
        spec.COL_PARTY_BRANCH: "00018", spec.COL_DOC_VAT: "7.00",
        spec.COL_ERP_DOCNUM: "ERP-1", spec.COL_ERP_ITEM: "ITEM-1",
        spec.COL_ERP_PARTY: "PARTY-1", spec.COL_PUSH_STATUS: "สำเร็จ",
        spec.COL_ROW_KEY: "PRN:origin-1:0"}
    row.update(values)
    return row


class RoundtripParserTests(unittest.TestCase):
    def parse(self, rows, headers=SALES_HEADERS, sheet=spec.SHEET_SALES):
        return parse_roundtrip_workbook(workbook_bytes([(sheet, headers, rows)]))

    def test_sales_formula_fallback_metadata_and_provenance(self):
        content = workbook_bytes([(spec.SHEET_SALES, SALES_HEADERS, [sales_row()])])
        parsed = parse_roundtrip_workbook(content)
        doc = parsed["documents"][0]
        self.assertEqual(doc["fields"]["total_amount"], Decimal("107.00"))
        self.assertEqual(doc["fields"]["items"][0]["amount"], Decimal("100.00"))
        self.assertEqual(doc["fields"]["buyer_branch"], "00018")
        self.assertEqual(doc["source_row_keys"], [{"source_row": 2, "row_key": "PRN:origin-1:0",
            "history_id": "origin-1", "line_index": 0}])
        self.assertEqual(parsed["source_sha256"], hashlib.sha256(content).hexdigest())
        unit = parsed["provenance"]["units"][0]
        self.assertEqual(unit["page_number"], 1)
        self.assertEqual(unit["rows"][0]["cells"][6], "=E2*F2")
        self.assertEqual(unit["source_rows"], [2])
        self.assertFalse(parsed["provenance"]["coverage_complete"])

    def test_grouping_prefers_original_key_noncontiguous_first_metadata(self):
        rows = [sales_row(), sales_row(**{spec.COL_ROW_KEY: "PRN:other:0"}),
            sales_row(**{spec.COL_ROW_KEY: "PRN:origin-1:1", spec.SALES_COL_INVOICE: "EDITED",
                         spec.SALES_COL_PARTY: "Changed", spec.SALES_COL_PRICE: 50, spec.COL_DOC_VAT: 999})]
        docs = self.parse(rows)["documents"]
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["source_rows"], [2, 4])
        self.assertEqual(docs[0]["fields"]["invoice_number"], "INV-1")
        self.assertEqual(docs[0]["fields"]["buyer_name"], "Buyer")
        self.assertEqual(docs[0]["fields"]["vat_amount"], Decimal("10.50"))

    def test_grouping_fallback_invoice_then_row(self):
        rows = [sales_row(**{spec.COL_ROW_KEY: "bad"}) for _ in range(2)]
        rows += [sales_row(**{spec.COL_ROW_KEY: None, spec.SALES_COL_INVOICE: None}) for _ in range(2)]
        self.assertEqual([doc["source_rows"] for doc in self.parse(rows)["documents"]], [[2, 3], [4], [5]])

    def test_reordered_optional_branch_absent_duplicate_leftmost(self):
        headers = [h for h in reversed(SALES_HEADERS) if h != spec.COL_PARTY_BRANCH]
        headers += [spec.SALES_COL_PRICE, "Accounting note"]
        row = [sales_row().get(header) for header in headers]
        row[-2:] = [999999, "preserved note"]
        doc = self.parse([row], headers)["documents"][0]
        self.assertEqual(doc["fields"]["items"][0]["unit_price"], Decimal("100.0"))
        self.assertEqual(doc["fields"]["buyer_branch"], "")
        self.assertEqual(doc["fields"]["total_amount"], Decimal("107.00"))

    def test_explicit_amount_and_partial_explicit_base_preserved(self):
        rows = [sales_row(**{spec.SALES_COL_AMOUNT: "2.675", spec.SALES_COL_PRE_VAT: "1.005"}),
                sales_row(**{spec.COL_ROW_KEY: "PRN:origin-1:1", spec.SALES_COL_AMOUNT: "99.01"})]
        fields = self.parse(rows)["documents"][0]["fields"]
        self.assertEqual(fields["items"][0]["amount"], Decimal("2.675"))
        self.assertEqual(fields["amount_before_vat"], Decimal("1.00"))
        self.assertEqual(fields["total_amount"], Decimal("1.07"))

    def test_purchase_per_row_branch_tax_priority_total_ignored_zero_vat_fallback(self):
        row = {spec.PURCHASE_COL_INVOICE: "P", spec.PURCHASE_COL_PRE_VAT: 100,
            spec.PURCHASE_COL_VAT: 0, spec.COL_DOC_VAT: "7.02", spec.PURCHASE_COL_TOTAL: 999,
            spec.PURCHASE_COL_PARTY_TAX: "statutory", spec.COL_PARTY_TAX: "fallback",
            spec.PURCHASE_COL_BRANCH: "00002", spec.COL_PARTY_BRANCH: "00001", spec.COL_ROW_KEY: "PRN:same:0"}
        docs = self.parse([row, row], PURCHASE_HEADERS, spec.SHEET_PURCHASE)["documents"]
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["fields"]["total_amount"], Decimal("107.02"))
        self.assertEqual(docs[0]["fields"]["seller_tax"], "statutory")
        self.assertEqual(docs[0]["fields"]["seller_branch"], "00002")

    def test_purchase_no_amount_is_none(self):
        fields = self.parse([{spec.PURCHASE_COL_INVOICE: "P"}], PURCHASE_HEADERS,
                            spec.SHEET_PURCHASE)["documents"][0]["fields"]
        self.assertIsNone(fields["amount_before_vat"])
        self.assertIsNone(fields["vat_amount"])
        self.assertIsNone(fields["total_amount"])

    def test_pending_without_invoice_retained_and_ordered_after_documents(self):
        content = workbook_bytes([(spec.SHEET_PENDING, PENDING_HEADERS, [{spec.PENDING_COL_REASON: "reason"}]),
            (spec.SHEET_SALES, SALES_HEADERS, [sales_row()]), (spec.SHEET_SUMMARY, ["note"], [["ignored"]])])
        parsed = parse_roundtrip_workbook(content)
        before = deepcopy(parsed)
        result = to_pipeline(parsed, anchor_year=2026)
        self.assertEqual(parsed, before)
        self.assertEqual([page.page_number for page in result.pages], [1, 2])
        self.assertEqual([unit["sheet_name"] for unit in parsed["provenance"]["units"]],
                         [spec.SHEET_SALES, spec.SHEET_PENDING])
        page = result.pages[1]
        self.assertIsNone(page.invoice.invoice_number)
        self.assertEqual(page.invoice.notes, "reason")
        self.assertTrue(page.needs_manual_review)
        self.assertEqual(page.declared_direction, "")
        self.assertEqual(page.final_confidence, 0)
        self.assertEqual(page.confidence_band, "auto")
        self.assertEqual(result.estimated_cost_thb, 0)
        self.assertEqual(parsed["provenance"]["ignored_sheets"][0]["sheet_name"], spec.SHEET_SUMMARY)

    def test_pipeline_retains_audit_and_distinct_same_number_origins(self):
        parsed = self.parse([sales_row(), sales_row(**{spec.COL_ROW_KEY: "PRN:other:0"})])
        result = to_pipeline(parsed, anchor_year=2026)
        self.assertEqual(result.page_count, 2)
        self.assertEqual([page.source_history_id for page in result.pages], ["origin-1", "other"])
        audit = result.pages[0].extraction_audit["roundtrip"]
        self.assertEqual(audit["reader_fields"]["erp_docnum"], "ERP-1")
        self.assertEqual(result.pages[0].invoice.items[0].qty, "1")
        self.assertEqual(result.pages[0].invoice.items[0].subtotal, "100.00")
        audit["source_rows"].append(9)
        self.assertEqual(parsed["documents"][0]["source_rows"], [2])

    def test_49_blank_rows_continue_50_tail_fails_50_terminal_allowed(self):
        self.assertEqual(self.parse([sales_row(), *([[]] * 49), sales_row()])["documents"][0]["source_rows"], [2, 52])
        with self.assertRaises(RoundtripParseError) as caught:
            self.parse([sales_row(), *([[]] * 50), sales_row()])
        self.assertEqual(caught.exception.code, "ocr.roundtrip_trailing_rows")
        self.assertEqual(self.parse([sales_row(), *([[]] * 50)])["documents"][0]["source_rows"], [2])

    def test_nonmatching_and_recognized_empty_are_distinct(self):
        with self.assertRaises(RoundtripNotMatched):
            self.parse([sales_row()], ["ordinary"])
        with self.assertRaises(RoundtripParseError) as caught:
            self.parse([])
        self.assertEqual(caught.exception.code, "ocr.roundtrip_empty")

    def test_whitespace_sheet_rejected_instead_of_paid_fallback(self):
        with self.assertRaises(RoundtripParseError) as caught:
            self.parse([sales_row()], sheet=" " + spec.SHEET_SALES)
        self.assertEqual(caught.exception.code, "ocr.roundtrip_sheet_name_invalid")

    def test_nonfinite_input_rejected(self):
        for value in ("NaN", "Infinity", "1e309"):
            with self.subTest(value=value), self.assertRaises(RoundtripParseError):
                self.parse([sales_row(**{spec.SALES_COL_QTY: value})])

    def test_byte_archive_preflight_not_bypassable(self):
        content = workbook_bytes([(spec.SHEET_SALES, SALES_HEADERS, [sales_row()])])
        with patch("pearnly_cowork.roundtrip.parser._read_sheets", side_effect=AssertionError("decoded")):
            with self.assertRaises(RoundtripParseError) as caught:
                parse_roundtrip_workbook(b"broken")
            self.assertEqual(caught.exception.code, "ocr.table_invalid_archive")
        archive, altered = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
        with zipfile.ZipFile(altered, "w") as output:
            for name in archive.namelist():
                data = archive.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    data = data.replace(b'A1:P2', b'A1:XFD1048576')
                output.writestr(name, data)
        with self.assertRaises(RoundtripParseError) as caught:
            parse_roundtrip_workbook(altered.getvalue())
        self.assertEqual(caught.exception.code, "ocr.table_row_limit")

    def test_numeric_compatibility_and_decimal_thresholds(self):
        self.assertEqual(_num("9007199254740993"), Decimal("9007199254740992.0"))
        self.assertEqual(_num("1.00000000000000009"), Decimal("1.0"))
        self.assertIsNone(_num("THB 2"))
        self.assertEqual(_num("฿1,002.50"), Decimal("1002.5"))
        for value, expected in (("2.675", "2.68"), ("-2.675", "-2.68"), ("0.01", "0.01")):
            self.assertEqual(money.line_amount(value, 1), Decimal(expected))
        self.assertEqual(money.doc_totals([100], printed_vat="7.02"), (Decimal("100.00"), Decimal("7.02"), Decimal("107.02")))
        self.assertEqual(money.doc_totals([100], printed_vat="7.03")[1], Decimal("7.00"))
        self.assertEqual(money.allocate_vat(["33.33", "33.33", "33.34"], 7),
                         [Decimal("2.33"), Decimal("2.33"), Decimal("2.34")])

    def test_invalid_anchor_year_is_rejected(self):
        with self.assertRaises(ValueError):
            to_pipeline(self.parse([sales_row()]), anchor_year=True)


if __name__ == "__main__":
    unittest.main()
