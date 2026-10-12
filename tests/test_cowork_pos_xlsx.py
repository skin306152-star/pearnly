"""POS 导出的 Excel（.xlsx）读取：用 openpyxl 生成两种真实列结构（Ocha 式按单报表、Loyverse 式收据导出，数据合成），
经 `pos_import.tables.read` 读成表头与数据行，再认列、逐日汇总。要 openpyxl：在生产镜像里离线跑
（`scripts/ci-candidate-checks.sh` offline）。
"""
import io
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_cowork"), str(ROOT / "apps/pearnly_core")]

from openpyxl import Workbook  # noqa: E402

from pearnly_cowork.pos_import import contracts as C  # noqa: E402
from pearnly_cowork.pos_import import tables as T  # noqa: E402


def _book(sheets):
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets:
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class WorkbookTests(unittest.TestCase):
    def test_ocha_style_report_with_title_rows_and_a_cover_sheet(self):
        content = _book([
            ("สรุป", [["ร้านทดสอบ"], ["ยอดขายรวม", 3959.0]]),
            ("รายบิล", [["รายงานยอดขายแยกตามบิล"], ["วันที่ 13/10/2569 - 14/10/2569"], [],
                        ["วันที่", "เวลา", "เลขที่บิล", "จำนวนลูกค้า", "ยอดก่อนส่วนลด", "ส่วนลด", "ภาษีมูลค่าเพิ่ม",
                         "ยอดสุทธิ", "ช่องทางชำระเงิน", "สถานะบิล"],
                        [datetime(2026, 10, 13), "10:01", "B0001", 2, 1070, 0, 70, 1070, "เงินสด", "ปกติ"],
                        [datetime(2026, 10, 13), "11:15", "B0002", 1, 535, 0, 35, 535, "พร้อมเพย์", "ปกติ"],
                        [datetime(2026, 10, 13), "12:00", "B0003", 1, 100, 0, 6.54, 100, "เงินสด", "ยกเลิก"],
                        [datetime(2026, 10, 14), "09:00", "B0004", 3, 2354, 0, 154, 2354, "บัตรเครดิต", "ปกติ"],
                        ["รวม", None, None, None, None, None, 265.54, 4059]]),
        ])
        headers, rows, first = T.read(content, "ocha.xlsx")
        self.assertEqual((headers[2], first), ("เลขที่บิล", 5))
        detection = C.detect(headers, rows)
        self.assertEqual(detection.questions, [])
        found, voids = C.parse(headers, rows, detection.layout, anchor_year=2026, first_row=first)
        self.assertEqual(voids, 1)
        self.assertEqual([(d.day, d.gross, d.vat, d.ref) for d in found],
                         [(date(2026, 10, 13), Decimal("1605.00"), Decimal("105.00"), "B0001-B0002"),
                          (date(2026, 10, 14), Decimal("2354.00"), Decimal("154.00"), "B0004")])

    def test_loyverse_style_receipts_with_floats_and_a_refund(self):
        content = _book([("Receipts", [
            ["Date", "Receipt number", "Receipt type", "Gross sales", "Discounts", "Net sales", "Taxes",
             "Total collected", "Payment type", "Status"],
            [datetime(2026, 10, 13, 9, 5), "1-1001", "Sale", 215.0, 5.0, 210.0, 13.74, 210.0, "Cash", "Closed"],
            [datetime(2026, 10, 13, 9, 40), "1-1002", "Sale", 65.1, 0.0, 65.1, 4.26, 65.1, "Card", "Closed"],
            [datetime(2026, 10, 13, 11, 0), "1-1003", "Refund", 30.0, 0.0, 30.0, 1.96, 30.0, "Cash", "Closed"],
        ])])
        headers, rows, first = T.read(content, "receipts.xlsx")
        detection = C.detect(headers, rows)
        self.assertEqual(detection.questions, [])
        found, _ = C.parse(headers, rows, detection.layout, anchor_year=2026, first_row=first)
        day = found[0]
        self.assertEqual((day.gross, day.vat, day.net), (Decimal("245.10"), Decimal("16.04"), Decimal("229.06")))
        self.assertEqual(day.payments, [("Cash", Decimal("180.00")), ("Card", Decimal("65.10"))])
        self.assertEqual((day.first, day.last, day.count, day.refunds), ("1-1001", "1-1002", 2, 1))

    def test_unreadable_bytes_are_reported(self):
        with self.assertRaises(C.PosError) as raised:
            T.read(b"not a workbook", "pos.xlsx")
        self.assertEqual(raised.exception.code, "pos.file_unreadable")
        with self.assertRaises(C.PosError) as raised:
            T.read(_book([("Sheet", [["a", "b"], [1, 2]])]), "pos.xlsx")
        self.assertEqual(raised.exception.code, "pos.no_header")


if __name__ == "__main__":
    unittest.main()
