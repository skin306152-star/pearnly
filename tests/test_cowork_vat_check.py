"""销项税报告核查（旧线上 ec2e5c95 逐字移植部分）：报告合并的同卖方 / 同期间校验、结构化发票映射、核查工作簿与计数。"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "pearnly_cowork"))

try:
    import openpyxl  # noqa: F401
except ImportError:  # pragma: no cover - the bench environment has it
    openpyxl = None

from pearnly_cowork.reconciliation import vat_check_core  # noqa: E402


def report(rows, meta=None):
    return {"ok": True, "rows": rows, "meta": meta or {}}


def row(no, date, buyer, tax, pre, vat):
    return {"report_invoice_no": no, "report_date": date, "report_buyer_name": buyer, "report_buyer_tax_id": tax,
            "report_buyer_branch": "00000", "report_amount_pre_vat": pre, "report_vat_amount": vat,
            "report_amount": round(pre + vat, 2)}


class MergeTest(unittest.TestCase):
    def test_rows_are_renumbered_with_their_source(self):
        merged = vat_check_core.merge_parsed_reports(
            [report([row("IV1", "01/06/2569", "A", "0105551234567", 100, 7)]),
             report([row("IV2", "02/06/2569", "B", "0105559876543", 200, 14)])], ["a.xlsx", "b.xlsx"])
        self.assertTrue(merged["ok"])
        self.assertEqual([(r["row_no"], r["source_filename"]) for r in merged["rows"]], [(1, "a.xlsx"), (2, "b.xlsx")])
        self.assertEqual((merged["period_year"], merged["period_month"]), (2026, 6))

    def test_mixed_periods_or_sellers_are_refused(self):
        periods = vat_check_core.merge_parsed_reports(
            [report([row("IV1", "01/06/2569", "A", "", 1, 0)]), report([row("IV2", "01/07/2569", "B", "", 1, 0)])],
            ["a", "b"])
        self.assertEqual(periods["error_code"], "vat_check.periods_differ")
        sellers = vat_check_core.merge_parsed_reports(
            [report([], {"seller_tax_id": "1"}), report([], {"seller_tax_id": "2"})], ["a", "b"])
        self.assertEqual(sellers["error_code"], "vat_check.sellers_differ")

    def test_structured_invoice_row_maps_to_invoice_record(self):
        record = vat_check_core.report_row_to_invoice(row("IV9", "03/06/2569", "C", "0105550000001", 500, 35), "inv.xlsx")
        self.assertEqual((record["invoice_no"], record["buyer_tax_id"], record["total_amount"]),
                         ("IV9", "0105550000001", 535))


@unittest.skipIf(openpyxl is None, "openpyxl is not installed")
class WorkbookTest(unittest.TestCase):
    def test_workbook_counts_matches_differences_and_orphans(self):
        from pearnly_cowork.reconciliation.export.vat_check_workbook import build_excel

        rows = [row("IV1", "01/06/2569", "A", "0105551234567", 1000, 70),
                row("IV2", "02/06/2569", "B", "0105559876543", 2100, 147),
                row("IV4", "05/06/2569", "D", "0105550000002", 800, 56)]
        invoices = [vat_check_core.report_row_to_invoice(r, "inv.xlsx") for r in rows[:2]]
        invoices[1] = {**invoices[1], "amount_pre_vat": 2000, "vat_amount": 140, "total_amount": 2140}
        invoices.append(vat_check_core.report_row_to_invoice(row("IV5", "07/06/2569", "E", "0105550000003", 300, 21), "inv.xlsx"))
        content, summary = build_excel(invoices, rows, "Seller", "", 2026, 6, "zh")
        kinds = sorted(r["kind"] for r in summary["rows"])
        self.assertEqual(summary["n_ok"], 1)
        self.assertEqual(kinds.count("invoice_orphan") + kinds.count("report_orphan"), 2)
        book = openpyxl.load_workbook(io.BytesIO(content))
        self.assertEqual(book.sheetnames, ["发票明细", "VAT 报告明细", "对账结果", "使用说明"])


if __name__ == "__main__":
    unittest.main()
