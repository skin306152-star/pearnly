"""上传盘点表（`stocktake.upload_sheet`）：表头认多种写法、错行跳过并给原因、好行照收。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "pearnly_cowork"))

from pearnly_cowork.stocktake import upload_sheet  # noqa: E402
from pearnly_cowork.stocktake.contracts import StocktakeValidationError  # noqa: E402

HEADER = ["product_code", "product_name", "barcode", "unit", "book_qty", "actual_qty", "warehouse", "location"]


def row(*values, kinds=None):
    kinds = kinds or ["s", "s", "s", "s", "n", "n", "s", "s"]
    return [(value, kind) for value, kind in zip(values, kinds)]


class UploadSheetTest(unittest.TestCase):
    def test_counted_and_uncounted_rows(self):
        items, problems = upload_sheet.rows(HEADER, [
            row("A01", "Apple", "885001", "pcs", 10, 8, "WH", "A-1"),
            row("A02", "Pear", "", "pcs", 5, None, "", ""),
        ])
        self.assertEqual(problems, [])
        self.assertEqual(items[0]["actual_qty"], "8.000000")
        self.assertEqual(items[0]["book_qty"], "10.000000")
        self.assertIsNone(items[1]["actual_qty"])

    def test_bad_rows_are_skipped_with_reasons(self):
        items, problems = upload_sheet.rows(HEADER, [
            row("A01", "Apple", "", "pcs", 10, 8, "", ""),
            row("A01", "Apple again", "", "pcs", 1, 1, "", ""),
            row(12, "Numeric code", "", "pcs", 1, 1, "", "", kinds=["n", "s", "s", "s", "n", "n", "s", "s"]),
            row("A03", "Negative count", "", "pcs", 1, -1, "", ""),
            row("A04", "", "", "pcs", 1, 1, "", ""),
            row("A05", "Formula", "", "pcs", "=1+1", 1, "", "", kinds=["s", "s", "s", "s", "f", "n", "s", "s"]),
            row(None, None, None, None, None, None, None, None),
        ])
        self.assertEqual([item["product_code"] for item in items], ["A01"])
        self.assertEqual([(p["row"], p["reason"]) for p in problems], [
            (3, "stocktake.duplicate"), (4, "stocktake.code_as_text"), (5, "stocktake.quantity_invalid"),
            (6, "stocktake.row_invalid"), (7, "stocktake.row_invalid")])

    def test_barcode_cannot_identify_another_product(self):
        _items, problems = upload_sheet.rows(HEADER, [
            row("A01", "Apple", "X1", "pcs", 1, 1, "", ""),
            row("A02", "Pear", "A01", "pcs", 1, 1, "", ""),
        ])
        self.assertEqual(problems[0]["reason"], "stocktake.ambiguous_code")

    def test_localized_and_legacy_headers(self):
        thai = ["รหัสสินค้า", "ชื่อสินค้า", "บาร์โค้ด / รหัส QR", "หน่วย", "จำนวนตามบัญชี", "จำนวนที่นับได้"]
        items, _ = upload_sheet.rows(thai, [row("A01", "Apple", "", "pcs", 2, 2)])
        self.assertEqual(items[0]["actual_qty"], "2.000000")
        labels = {"实盘": "actual_qty"}
        items, _ = upload_sheet.rows(["编码", "名称", "单位", "账面数量", "实盘"],
                                     [row("A01", "Apple", "pcs", 2, 3, kinds=["s", "s", "s", "n", "n"])], labels)
        self.assertEqual(items[0]["actual_qty"], "3.000000")

    def test_missing_required_column_or_no_rows(self):
        with self.assertRaises(StocktakeValidationError) as error:
            upload_sheet.rows(["product_code", "product_name"], [])
        self.assertEqual(error.exception.code, "stocktake.headers_invalid")
        with self.assertRaises(StocktakeValidationError) as error:
            upload_sheet.rows(HEADER, [row("A01", "", "", "pcs", 1, 1, "", "")])
        self.assertEqual(error.exception.code, "stocktake.empty")


if __name__ == "__main__":
    unittest.main()
