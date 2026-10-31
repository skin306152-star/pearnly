"""盘点表（`inventory.workbook`）的内部编码一列（岳父 10-25：盘点用的 Excel 模板带上内部编码）。

Run with openpyxl (candidate image). No Site.
- 模板多一列「内部编码」紧跟「编码」，预置成文本；
- 只给内部编码的行照认（不算缺标识），内部编码列认几种写法；
- 老模板（没有内部编码列）照样导；
- 按台的公司（车 / 电器）多出号码三列紧跟条码，号码预置文本；只给车架号的行照认（导回按车架号认台）。
"""
import io
import unittest

import openpyxl

from pearnly_erp_portal.inventory import workbook


def xlsx(rows):
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


class StockCountWorkbook(unittest.TestCase):
    def test_template_has_the_internal_code_column_as_text(self):
        fields = [field for field, _ in workbook.FIELD_LABELS]
        self.assertEqual(fields[:2], ["product_code", "internal_code"])
        data = workbook.build_bytes(fields, [{"product_code": "P-000001", "internal_code": "00123", "product_name": "x",
                                              "barcode": "885", "unit": "ชิ้น", "book_qty": 1}])
        sheet = openpyxl.load_workbook(io.BytesIO(data)).active
        self.assertEqual((sheet["A2"].value, sheet["B2"].value), ("P-000001", "00123"))
        self.assertEqual(sheet["B2"].number_format, "@")

    def test_a_row_with_only_an_internal_code_is_read(self):
        for header in ("internal_code", "内部编码", "รหัสภายใน", "Internal code"):
            with self.subTest(header=header):
                rows, problems = workbook.parse_workbook(xlsx([[header, "counted_qty"], ["A-01", 3]]))
                self.assertEqual(problems, [])
                self.assertEqual((rows[0]["internal_code"], rows[0]["product_code"]), ("A-01", ""))

    def test_an_old_template_without_the_column_still_imports(self):
        rows, problems = workbook.parse_workbook(xlsx([["编码", "实盘数量"], ["P-000001", 2]]))
        self.assertEqual(problems, [])
        self.assertEqual((rows[0]["product_code"], rows[0]["internal_code"]), ("P-000001", ""))

    def test_a_unit_tracking_company_gets_the_number_columns_after_the_barcode(self):
        self.assertEqual(workbook.field_labels(None), workbook.FIELD_LABELS)
        fields = [field for field, _ in workbook.field_labels("vehicle")]
        at = fields.index("barcode")
        self.assertEqual(fields[at + 1:at + 4], ["serial_no", "secondary_no", "color"])
        self.assertEqual(dict(workbook.field_labels("vehicle"))["serial_no"], "serial.vehicle.primary")
        self.assertEqual(dict(workbook.field_labels("device"))["serial_no"], "serial.device.primary")
        self.assertEqual(workbook.template_headers(lambda key: key, "vehicle"), [key for _, key in workbook.field_labels("vehicle")])
        data = workbook.build_bytes(fields, [{"product_code": "P-000001", "product_name": "Car", "serial_no": "0012345678ABCDEFG",
                                              "secondary_no": "0099", "color": "红", "book_qty": 1}], "vehicle")
        sheet = openpyxl.load_workbook(io.BytesIO(data)).active
        column = fields.index("serial_no") + 1
        self.assertEqual(sheet.cell(2, column).value, "0012345678ABCDEFG")
        self.assertEqual(sheet.cell(2, column).number_format, "@")
        self.assertEqual(sheet.cell(2, column + 1).number_format, "@")

    def test_a_row_with_only_a_unit_number_is_read(self):
        rows, problems = workbook.parse_workbook(xlsx([["serial_no", "color", "counted_qty"], ["VIN-1", "红", 1], ["", "", 1]]))
        self.assertEqual(rows[0]["serial_no"], "VIN-1")
        self.assertEqual(rows[0]["counted_qty"], 1)
        self.assertEqual(problems, [{"row": 3, "reason": "no_identifier"}])
        rows, _ = workbook.parse_workbook(xlsx([["编码", "实盘数量"], ["P-000001", 2]]))
        self.assertEqual(rows[0]["serial_no"], "")


if __name__ == "__main__":
    unittest.main()
