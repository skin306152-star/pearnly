"""Offline tests for the decoded JSON boundary, independent of Frappe/Site."""
import copy
import unittest
from uuid import uuid4

from pearnly_cowork.stocktake.contracts import FIELDS, StocktakeValidationError
from pearnly_cowork.stocktake.native_contract import decoded_import, identifier, revision, void_flag


def row(values):
    return [{"value": value, "data_type": "s" if isinstance(value, str) else "n"} for value in values]


ROWS = [row(["0012", "Product", "000012", "A", "01", "EA", "2.500001"])]


class NativeContract(unittest.TestCase):
    def test_decoded_rows_retain_codes_precision_and_stable_digest(self):
        title, items, digest = decoded_import("Test", list(FIELDS), ROWS)
        self.assertEqual(title, "Test")
        self.assertEqual(items[0]["product_code"], "0012")
        self.assertEqual(items[0]["book_qty"], "2.500001")
        self.assertEqual(len(digest), 64)
        self.assertEqual(decoded_import("Other title", list(FIELDS), ROWS)[2], digest)
        changed = copy.deepcopy(ROWS)
        changed[0][-1]["value"] = "2.500002"
        self.assertNotEqual(decoded_import("Test", list(FIELDS), changed)[2], digest)

    def test_api_cannot_skip_original_row_validation(self):
        for rows, code in [([], "stocktake.empty"), ([ROWS[0], ROWS[0]], "stocktake.duplicate"),
                           ([ROWS[0][:-1]], "stocktake.row_invalid")]:
            with self.subTest(code=code), self.assertRaisesRegex(StocktakeValidationError, code):
                decoded_import("Test", list(FIELDS), rows)
        numeric = copy.deepcopy(ROWS)
        numeric[0][0] = {"value": 12, "data_type": "s"}
        with self.assertRaisesRegex(StocktakeValidationError, "stocktake.code_as_text"):
            decoded_import("Test", list(FIELDS), numeric)
        formula = copy.deepcopy(ROWS)
        formula[0][1] = {"value": "=1+1", "data_type": "f"}
        with self.assertRaisesRegex(StocktakeValidationError, "stocktake.row_invalid"):
            decoded_import("Test", list(FIELDS), formula)

    def test_json_shapes_are_not_coerced_to_strings(self):
        for rows in (None, {}, [[{"value": {}, "data_type": "s"}]], [["x"]], [[]]):
            with self.subTest(rows=rows), self.assertRaises(StocktakeValidationError):
                decoded_import("Test", list(FIELDS), rows)
        for title in (None, "", " ", "x" * 121):
            with self.assertRaisesRegex(StocktakeValidationError, "stocktake.name_invalid"):
                decoded_import(title, list(FIELDS), ROWS)

    def test_uuid_revisions_and_void_flags_are_unambiguous(self):
        token = str(uuid4())
        self.assertEqual(identifier(token.upper()), token)
        for value in (None, True, [], "invalid"):
            with self.assertRaises(StocktakeValidationError):
                identifier(value)
        self.assertEqual(revision("0"), 0)
        self.assertEqual(revision(123), 123)
        for value in (None, True, -1, "0.0", "01", "1e3", "2147483647"):
            with self.assertRaises(StocktakeValidationError):
                revision(value)
        for value in (False, 0, "0", "false"):
            self.assertFalse(void_flag(value))
        for value in (True, 1, "1", "true"):
            self.assertTrue(void_flag(value))
        for value in (None, [], 2, "False", "anything"):
            with self.assertRaises(StocktakeValidationError):
                void_flag(value)


if __name__ == "__main__":
    unittest.main()
