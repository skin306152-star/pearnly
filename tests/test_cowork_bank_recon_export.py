"""Offline XLSX checks for the migrated bank-reconciliation review workbook.

The workbook is pure presentation: rows and a summary are supplied by the caller,
so these cases need no Site, database, model or file.
"""
import unittest
from datetime import date
from io import BytesIO
from zipfile import ZipFile

import openpyxl

from pearnly_cowork.reconciliation.bank_matching import BankReconRow, BankReconSummary
from pearnly_cowork.reconciliation.export import labels, usage
from pearnly_cowork.reconciliation.export.styles import fmt_date, hdr_style
from pearnly_cowork.reconciliation.export.workbook import workbook

STATEMENT_DATE = date(2026, 6, 1)


def result_row(status="matched", layer=1, **values):
    return BankReconRow(
        match_status=status, match_layer=layer,
        stmt_date=values.get("stmt_date", STATEMENT_DATE), stmt_desc=values.get("stmt_desc", "SWD"),
        stmt_withdrawal=values.get("withdrawal", 400.0), stmt_deposit=values.get("deposit", 0.0),
        stmt_balance=values.get("balance", 600.0), gl_date=values.get("gl_date", STATEMENT_DATE),
        gl_doc_no=values.get("gl_doc_no", "GL-1"), gl_account_code="1000", gl_desc="payment",
        gl_debit=values.get("debit", 0.0), gl_credit=values.get("credit", 400.0),
        gl_balance=600.0, date_diff_days=values.get("date_diff_days", 0),
        source_stmt_file="STM KBANK.pdf", source_gl_file="GL.xlsx",
        stmt_confidence=values.get("confidence", "high"),
        stmt_balance_ok=values.get("balance_ok"), stmt_autocorrected=values.get("autocorrected", False))


def summary(**values):
    return BankReconSummary(
        bank_code=values.get("bank_code", "BBL"), gl_account_code=values.get("gl_account_code", "1000"),
        stmt_opening=1000.0, stmt_closing=510.0, gl_opening=990.0, gl_closing=500.0,
        stmt_total_deposit=0.0, stmt_total_withdrawal=490.0, gl_total_credit=400.0, gl_total_debit=75.0,
        matched_count=1, gl_debit_only_count=1, gl_credit_only_count=0, stmt_withdrawal_only_count=1,
        stmt_deposit_only_count=0, gl_debit_only_amount=75.0, gl_credit_only_amount=0.0,
        stmt_withdrawal_only_amount=90.0, stmt_deposit_only_amount=0.0, opening_diff=10.0,
        formula_stmt_closing=435.0, formula_diff=75.0)


def book(rows=None, value=None, **arguments):
    content = workbook(rows if rows is not None else [result_row()],
                       value if value is not None else summary(), **arguments)
    return content, openpyxl.load_workbook(BytesIO(content))


def column(sheet, letter="A"):
    return [cell.value for cell in sheet[letter]]


class SheetLayout(unittest.TestCase):
    def test_four_sheets_named_in_every_shipped_language(self):
        for lang in ("th", "en", "zh", "ja"):
            with self.subTest(lang=lang):
                _, loaded = book(lang=lang)
                self.assertEqual(loaded.sheetnames, [
                    labels.t("sh_summary", lang), labels.t("sh_match_results", lang),
                    labels.t("sh_stmt_detail", lang), labels.t("sh_gl_detail", lang)])

    def test_an_unknown_language_falls_back_to_thai(self):
        _, fallback = book(lang="de")
        _, thai = book(lang="th")
        self.assertEqual(fallback.sheetnames, thai.sheetnames)

    def test_the_download_is_a_workbook_archive_with_four_sheets(self):
        content, _ = book()
        self.assertTrue(content.startswith(b"PK"))
        with ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
        self.assertIn("[Content_Types].xml", names)
        for index in range(1, 5):
            self.assertIn(f"xl/worksheets/sheet{index}.xml", names)
        self.assertNotIn("xl/worksheets/sheet5.xml", names)

    def test_headers_are_frozen_and_the_summary_hides_its_grid(self):
        _, loaded = book()
        summary_sheet, match_sheet, stmt_sheet, gl_sheet = loaded.worksheets
        self.assertFalse(summary_sheet.sheet_view.showGridLines)
        # The deployed sheets keep the header (and the match sheet its status and
        # layer columns) visible while scrolling.
        self.assertEqual((match_sheet.freeze_panes, stmt_sheet.freeze_panes, gl_sheet.freeze_panes),
                         ("C2", "A2", "A2"))
        self.assertEqual(summary_sheet.freeze_panes, "A6")


class MatchResultsSheet(unittest.TestCase):
    def test_a_matched_row_carries_status_layer_amounts_and_sources(self):
        _, loaded = book()
        row = [cell.value for cell in loaded[labels.t("sh_match_results", "th")][2]]
        self.assertEqual(row[0], labels.t("status_matched", "th"))
        self.assertEqual(row[1], labels.layer_label(1, "th"))
        self.assertEqual(row[2], "01/06/2026")
        self.assertEqual(row[4], 400.0)
        self.assertEqual((row[8], row[9]), ("GL-1", "1000"))
        self.assertEqual((row[14], row[15]), ("STM KBANK.pdf", "GL.xlsx"))

    def test_amounts_use_the_shared_number_format(self):
        _, loaded = book()
        sheet = loaded[labels.t("sh_match_results", "th")]
        self.assertEqual(sheet.cell(2, 5).number_format, "#,##0.00;[Red]-#,##0.00")
        self.assertEqual(sheet.cell(2, 5).alignment.horizontal, "right")

    def test_status_colour_sits_on_the_status_column_only(self):
        # 统一外观（export/theme.py）：绿 = 一致、红 = 缺一边，只染状态列；其余格白底、同一字体。
        rows = [result_row("matched", 1), result_row("gl_debit_only", None, withdrawal=0.0, credit=0.0,
                                                     debit=75.0, gl_doc_no="GL-2")]
        _, loaded = book(rows)
        sheet = loaded[labels.t("sh_match_results", "th")]
        self.assertEqual([sheet.cell(row, 1).fill.fgColor.rgb for row in (2, 3)], ["00DCFCE7", "00FEE2E2"])
        self.assertTrue(all(sheet.cell(row, column).fill.fill_type is None
                            for row in (2, 3) for column in range(2, sheet.max_column + 1)))
        self.assertEqual({sheet.cell(row, column).font.name for row in (1, 2, 3)
                          for column in range(1, sheet.max_column + 1)}, {"Arial"})
        self.assertEqual(sheet.cell(1, 1).fill.fgColor.rgb, "001F2937")

    def test_one_sided_rows_take_their_layer_dash_and_status(self):
        rows = [result_row("matched", 2), result_row("gl_debit_only", None, withdrawal=0.0, credit=0.0,
                                                     debit=75.0, gl_doc_no="GL-2"),
                result_row("stmt_withdrawal_only", None, withdrawal=90.0)]
        _, loaded = book(rows)
        sheet = loaded[labels.t("sh_match_results", "th")]
        statuses = [sheet.cell(index, 1).value for index in (2, 3, 4)]
        layers = [sheet.cell(index, 2).value for index in (2, 3, 4)]
        self.assertEqual(statuses[0], labels.t("status_matched", "th"))
        self.assertEqual(statuses[1:], [labels.status_label("gl_debit_only", "th"),
                                        labels.status_label("stmt_withdrawal_only", "th")])
        self.assertEqual(layers, [labels.layer_label(2, "th"), "—", "—"])

    def test_confidence_and_balance_state_columns_follow_the_row_flags(self):
        rows = [result_row(confidence="low", balance_ok=False, autocorrected=True),
                result_row(confidence="medium", balance_ok=False, autocorrected=False),
                result_row(confidence="high", balance_ok=None, autocorrected=False)]
        _, loaded = book(rows)
        sheet = loaded[labels.t("sh_stmt_detail", "th")]
        header_row = [cell.value for cell in sheet[1]]
        confidence = header_row.index(labels.t("col_confidence", "th")) + 1
        state = header_row.index(labels.t("col_balance_ok", "th")) + 1
        self.assertEqual([sheet.cell(row, confidence).value for row in (2, 3, 4)],
                         [labels.t("conf_low", "th"), labels.t("conf_medium", "th"),
                          labels.t("conf_high", "th")])
        # An auto-corrected row reports the correction instead of the mismatch.
        self.assertEqual([sheet.cell(row, state).value for row in (2, 3, 4)],
                         [labels.t("bal_fixed", "th"), labels.t("bal_warn", "th"),
                          labels.t("bal_na", "th")])


class SummarySheet(unittest.TestCase):
    def test_title_and_identity_rows_use_the_summary_values(self):
        _, loaded = book()
        sheet = loaded[labels.t("sh_summary", "th")]
        self.assertTrue(sheet["A1"].value.endswith(" · BBL"))
        self.assertIn("A1:B1", str(sheet.merged_cells))
        self.assertEqual(sheet["A2"].value, labels.t("lbl_bank", "th"))
        self.assertEqual(sheet["B2"].value, "BBL")
        self.assertEqual(sheet["A3"].value, labels.t("lbl_gl_acct", "th"))
        self.assertEqual(sheet["B3"].value, "1000")

    def test_an_empty_gl_account_shows_the_deployed_dash(self):
        _, loaded = book(value=summary(gl_account_code=""))
        sheet = loaded[labels.t("sh_summary", "th")]
        self.assertEqual(sheet["B3"].value, "—")

    def test_the_summary_carries_no_banner_or_usage_text(self):
        # Cowork 统一复核表（用户 2026-09-26）：不写横幅与使用说明，匹配率在“匹配率”一行。
        _, loaded = book([result_row("gl_debit_only", None)])
        values = column(loaded[labels.t("sh_summary", "th")])
        self.assertNotIn(labels.t("banner_no_match", "th"), values)
        self.assertNotIn(labels.t("sh_usage", "th"), values)
        self.assertIn(labels.t("lbl_match_rate", "th"), values)

    def test_the_match_banner_stays_away_when_something_matched(self):
        _, loaded = book()
        values = column(loaded[labels.t("sh_summary", "th")])
        self.assertNotIn(labels.t("banner_no_match", "th"), values)

    def test_ocr_section_only_appears_for_flagged_rows(self):
        clean = column(book()[1][labels.t("sh_summary", "th")])
        self.assertNotIn(labels.t("lbl_ocr_check", "th"), clean)
        _, loaded = book([result_row(confidence="low", balance_ok=False, autocorrected=True)])
        values = column(loaded[labels.t("sh_summary", "th")])
        self.assertIn(labels.t("lbl_ocr_check", "th"), values)
        self.assertIn(f"  · {labels.t('lbl_ocr_autofixed', 'th')}", values)
        self.assertIn(f"  · {labels.t('lbl_ocr_bal_warn', 'th')}", values)
        self.assertIn(f"  · {labels.t('lbl_ocr_lowconf', 'th')}", values)

    def test_file_info_uses_the_stored_parse_diagnostics(self):
        parse_info = {
            "stmt_files": [{"file": "STM KBANK.pdf", "rows": 3, "ok": True, "error": None,
                            "error_code": None, "bank_code": "kbank"}],
            "gl_files": [{"file": "GL.xlsx", "rows": 2, "ok": False, "error": "no rows",
                          "error_code": "empty", "accounts": ["1000", "1100"]}],
        }
        _, loaded = book(parse_info=parse_info)
        sheet = loaded[labels.t("sh_summary", "th")]
        values = column(sheet)
        self.assertIn(labels.t("sh_fileinfo", "th"), values)
        statement = cell_containing(sheet, "STM KBANK.pdf")
        self.assertIn(f"3 {labels.t('fi_rows', 'th').lower()}", statement.value)
        self.assertIn("kbank", statement.value)
        self.assertEqual(sheet.cell(statement.row, 2).value, labels.t("fi_ok", "th"))
        gl = cell_containing(sheet, "GL.xlsx")
        self.assertIn("no rows", gl.value)
        self.assertIn("1000, 1100", gl.value)
        self.assertEqual(sheet.cell(gl.row, 2).value, labels.t("fi_fail", "th"))

    def test_file_info_section_stays_empty_without_stored_diagnostics(self):
        _, loaded = book()
        sheet = loaded[labels.t("sh_summary", "th")]
        values = column(sheet)
        self.assertIn(labels.t("sh_fileinfo", "th"), values)
        # No per-file row means no status cell either; the usage text below the
        # section still explains the reading rules.
        for key in ("fi_ok", "fi_warn", "fi_fail"):
            self.assertNotIn(labels.t(key, "th"), [cell.value for cell in sheet["B"]])

    def test_manual_entry_trace_only_appears_with_overrides(self):
        clean = column(book()[1][labels.t("sh_summary", "th")])
        self.assertNotIn(labels.t("sec_manual_entry", "th"), clean)
        # The deployed trace records the OCR value and the value the user typed.
        overrides = {"stmt_opening": {"ocr": 1000.0, "user": 990.0}}
        _, loaded = book(anchor_overrides=overrides)
        sheet = loaded[labels.t("sh_summary", "th")]
        values = column(sheet)
        self.assertIn(labels.t("sec_manual_entry", "th"), values)
        self.assertIn(f"  · {labels.t('lbl_anchor_stmt_open', 'th')}", values)
        traced = [cell for cell in sheet["B"] if cell.value == 990.0]
        self.assertEqual(len(traced), 1)
        self.assertIn("OCR: 1,000.00", traced[0].comment.text)

    def test_a_task_without_rows_still_builds_the_four_sheets(self):
        _, loaded = book([], summary())
        self.assertEqual(len(loaded.sheetnames), 4)
        self.assertEqual(loaded[labels.t("sh_match_results", "th")].max_row, 1)


def cell_containing(sheet, text):
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and text in cell.value:
                return cell
    raise AssertionError(f"no cell contains {text!r}")


class LanguageTables(unittest.TestCase):
    def test_every_label_covers_the_four_shipped_languages(self):
        # 旧线上 103 条；有意加 2 条：layer_4「合计匹配（按预设）」、layer_5「刷卡净额（按预设）」（2026-09-30 对账预设）。
        self.assertEqual(len(labels.I18N_EXPORT), 105)
        for key, table in labels.I18N_EXPORT.items():
            with self.subTest(key=key):
                self.assertEqual(set(table), {"th", "en", "zh", "ja"})
                self.assertTrue(all(str(value).strip() for value in table.values()))

    def test_label_helpers_fall_back_like_the_deployed_ones(self):
        self.assertEqual(labels.t("sh_summary", "de"), labels.t("sh_summary", "th"))
        self.assertEqual(labels.t("not_a_key", "th"), "not_a_key")
        self.assertEqual(labels.layer_label(1, "th"), labels.t("layer_1", "th"))
        # The helper only knows the three deployed layers; the sheets draw the
        # dash for anything else.
        self.assertEqual(labels.layer_label(None, "th"), "")
        self.assertEqual(labels.layer_label(9, "th"), "")
        self.assertEqual(labels.status_label("gl_credit_only", "en"),
                         labels.t("st_gl_credit_only", "en"))
        self.assertEqual(labels.status_label("unknown", "th"), "unknown")

    def test_usage_blocks_cover_every_language_and_mark_their_headings(self):
        self.assertEqual(set(usage.USAGE_BLOCKS), {"th", "en", "zh", "ja"})
        for lang, blocks in usage.USAGE_BLOCKS.items():
            with self.subTest(lang=lang):
                self.assertTrue(blocks)
                self.assertTrue(all(isinstance(row, tuple) and len(row) == 2 for row in blocks))
                self.assertTrue(any(bold for _, bold in blocks))
                self.assertTrue(any(not bold for _, bold in blocks))


class StyleHelpers(unittest.TestCase):
    def test_fmt_date_matches_the_deployed_display_format(self):
        self.assertEqual(fmt_date(None), "")
        self.assertEqual(fmt_date(STATEMENT_DATE), "01/06/2026")

    def test_hdr_style_returns_a_centered_white_bold_header(self):
        sheet = openpyxl.Workbook().active
        cell = hdr_style(sheet, 1, 1, "Header")
        self.assertEqual(cell.value, "Header")
        self.assertTrue(cell.font.bold)
        self.assertEqual(cell.font.color.rgb[-6:], "FFFFFF")
        self.assertEqual(cell.fill.fgColor.rgb[-6:], "2D6A4F")
        self.assertTrue(cell.alignment.wrap_text)


if __name__ == "__main__":
    unittest.main()
