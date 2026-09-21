"""Offline regressions for the migrated client-import rules (batch 48)."""

import unittest

from pearnly_cowork.masters import client_import


class HeaderMatching(unittest.TestCase):
    def test_the_three_languages_are_recognised(self):
        self.assertEqual(client_import.match_column("Client Name"), "name")
        self.assertEqual(client_import.match_column("ชื่อลูกค้า"), "name")
        self.assertEqual(client_import.match_column("客户名称"), "name")
        self.assertEqual(client_import.match_column("TAX ID"), "tax_id")
        self.assertEqual(client_import.match_column("เลขประจำตัวผู้เสียภาษี"), "tax_id")
        self.assertEqual(client_import.match_column("税号"), "tax_id")
        self.assertEqual(client_import.match_column("Branch No"), "branch")
        self.assertEqual(client_import.match_column("เบอร์โทรศัพท์"), "phone")
        self.assertEqual(client_import.match_column("ที่อยู่"), "address")

    def test_a_substring_hit_and_a_miss(self):
        self.assertEqual(client_import.match_column("Customer Name (Thai)"), "name")
        self.assertIsNone(client_import.match_column("ช่องทาง"))
        self.assertIsNone(client_import.match_column(""))
        self.assertIsNone(client_import.match_column(None))

    def test_the_first_column_wins_for_each_field(self):
        matched = client_import.match_headers(["Note", "Tax ID", "VAT No", "Client Name"])
        self.assertEqual(matched, {"tax_id": 1, "name": 3})


class TaxAndBranch(unittest.TestCase):
    def test_a_thai_tax_id_needs_exactly_thirteen_digits(self):
        self.assertEqual(client_import.clean_tax_id("0-1055-48103-91-2"), "0105548103912")
        self.assertEqual(client_import.clean_tax_id("0105561000013 บริษัท"), "0105561000013")
        self.assertEqual(client_import.clean_tax_id("13"), "")
        self.assertEqual(client_import.clean_tax_id("13/06/26"), "")
        self.assertEqual(client_import.clean_tax_id(""), "")
        self.assertEqual(client_import.clean_tax_id(None), "")

    def test_a_branch_number_is_padded_but_never_invented(self):
        self.assertEqual(client_import.clean_branch_no("18"), "00018")
        self.assertEqual(client_import.clean_branch_no("สาขาที่ 18"), "00018")
        self.assertEqual(client_import.clean_branch_no("00018"), "00018")
        # Missing must stay missing: 00000 would mean the head office.
        self.assertEqual(client_import.clean_branch_no(""), "")
        self.assertEqual(client_import.clean_branch_no(None), "")
        self.assertEqual(client_import.clean_branch_no("123456"), "")


class StructuralError(unittest.TestCase):
    def test_a_row_without_a_name_is_an_error(self):
        self.assertEqual(client_import.structural_error("", None),
            client_import.ERR_MISSING_NAME)
        self.assertEqual(client_import.structural_error("   ", "0105548103912"),
            client_import.ERR_MISSING_NAME)

    def test_a_malformed_tax_id_is_an_error_but_empty_is_not(self):
        self.assertEqual(client_import.structural_error("ACME", "0-1055-48103-91-2"), None)
        self.assertEqual(client_import.structural_error("ACME", ""), None)
        self.assertEqual(client_import.structural_error("ACME", None), None)
        self.assertEqual(client_import.structural_error("ACME", "12345"),
            client_import.ERR_BAD_TAX_ID)


def table(rows):
    return [{"index": index, "cells": cells, "is_summary": False}
            for index, cells in enumerate(rows)]


class ImportRows(unittest.TestCase):
    def test_rows_are_mapped_through_the_matched_columns(self):
        result = client_import.import_rows(
            ["Client Name", "Tax ID", "Branch", "Phone", "Address"],
            table([["ACME Co., Ltd.", "0-1055-48103-91-2", "18", "0812345678", "Bangkok"],
                ["  Beta Shop  ", "", "", "", ""]]))
        self.assertTrue(result["name_column_found"])
        self.assertEqual(result["matched"],
            {"name": 0, "tax_id": 1, "branch": 2, "phone": 3, "address": 4})
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["rows"][0], {"row_index": 0, "name": "ACME Co., Ltd.",
            "tax_id": "0-1055-48103-91-2", "branch": "18", "phone": "0812345678",
            "address": "Bangkok"})
        self.assertEqual(result["rows"][1]["name"], "Beta Shop")
        self.assertEqual(result["rows"][1]["tax_id"], "")

    def test_without_a_name_column_nothing_is_imported(self):
        result = client_import.import_rows(["Tax ID", "Phone"],
            table([["0-1055-48103-91-2", "0812345678"]]))
        self.assertFalse(result["name_column_found"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["matched"], {"tax_id": 0, "phone": 1})

    def test_no_headers_at_all_is_an_empty_preview(self):
        result = client_import.import_rows([], [])
        self.assertEqual(result, {"headers": [], "matched": {}, "rows": [], "row_count": 0,
            "truncated": False, "name_column_found": False})

    def test_summary_rows_are_skipped(self):
        rows = table([["ACME"]])
        rows.append({"index": 9, "cells": ["Total"], "is_summary": True})
        result = client_import.import_rows(["Client Name"], rows)
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["rows"][0]["name"], "ACME")

    def test_a_long_file_is_truncated_and_says_so(self):
        rows = table([["C%d" % index] for index in range(client_import.MAX_ROWS + 20)])
        result = client_import.import_rows(["Client Name"], rows)
        self.assertEqual(result["row_count"], client_import.MAX_ROWS)
        self.assertTrue(result["truncated"])

    def test_a_missing_optional_column_stays_empty(self):
        result = client_import.import_rows(["Client Name"], table([["ACME"]]))
        self.assertEqual(result["rows"][0]["tax_id"], "")
        self.assertEqual(result["rows"][0]["address"], "")

    def test_a_short_row_is_read_as_far_as_it_goes(self):
        result = client_import.import_rows(["Client Name", "Tax ID"], table([["ACME"]]))
        self.assertEqual(result["rows"][0]["name"], "ACME")
        self.assertEqual(result["rows"][0]["tax_id"], "")


if __name__ == "__main__":
    unittest.main()
