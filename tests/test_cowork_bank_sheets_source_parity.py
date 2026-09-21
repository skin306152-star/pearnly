"""Differential check of the migrated bank-statement workbook reader.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed sources as JSON
(the bench has openpyxl). The pinned low-level loaders are executed from their fixed
text; the row class, the number/date parsers and the three balance-chain steps are
injected from this side because they were ported and differentially verified in
earlier batches — this comparison therefore isolates the newly ported reader.

Both sides read the same freshly generated workbook/CSV bytes and are compared on
the whole result: status, per-account rows, openings/closings, aggregation and the
completeness issues.
"""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from pearnly_cowork.reconciliation import bank_parse, bank_sheets, bank_statement, table_io

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/recon/bank_stmt_xlsx.py":
        "e8df8a52697b524e4a61638099329c09fc08b2b02fac908211694d4e570e032f",
    "services/recon/bank_table_io.py":
        "19b9161eb5a96f8244efba97b215b50710365ba97f1bd93f8ce04ccd302f190e",
}

READER_FUNCS = {"_map_bank_stmt_cols", "_find_stmt_header", "_extract_sheet_account",
    "_scan_preheader_opening", "_parse_stmt_sheet", "parse_bank_stmt_xlsx_direct"}
READER_CONSTS = {"_STMT_DATE_H", "_STMT_DESC_H", "_STMT_WITHDRAW_H", "_STMT_DEPOSIT_H",
    "_STMT_BALANCE_H", "_STMT_AMOUNT_H", "_STMT_ACCT_LABEL", "_STMT_OPEN_KW"}
IO_FUNCS = {"_hit", "_is_summary_row", "_load_excel_all_sheets", "_load_csv_sheets"}
IO_CONSTS = {"_SUMMARY_ROW_KW"}

ROW_FIELDS = ("date", "description", "withdrawal", "deposit", "balance", "source_file",
    "account_no", "row_hash", "balance_ok")
RESULT_FIELDS = ("ok", "error_code", "row_count", "opening", "closing", "bank_code",
    "parser_version", "sheets_parsed", "multi_account", "account_codes", "completeness",
    "needs_review")
ACCOUNT_FIELDS = ("account_no", "opening", "closing", "opening_known", "closing_known")


def extract(payload, path, names, constants, namespace):
    data = payload[path].encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == HASHES[path], f"Source hash mismatch: {path}"
    tree = ast.parse(data.decode("utf-8"))

    def assigned(node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        return ""

    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or assigned(node) in constants]
    found = {node.name for node in nodes if isinstance(node, ast.FunctionDef)}
    assert found == names, sorted(names - found)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


def load_legacy(payload):
    io_ns = {"io": io, "re": __import__("re"), "List": List}
    extract(payload, "services/recon/bank_table_io.py", IO_FUNCS, IO_CONSTS, io_ns)
    reader = {
        "List": List, "Dict": Dict, "Any": Any, "Optional": Optional,
        # These were ported and differentially verified earlier, so they are injected
        # rather than re-executed here (the reader is what this file checks).
        "StatementRow": bank_statement.StatementRow,
        "_to_float": bank_parse.to_float,
        "_parse_date": bank_parse.parse_date,
        "_is_summary_row": io_ns["_is_summary_row"],
        "_hit": io_ns["_hit"],
        "_load_excel_all_sheets": io_ns["_load_excel_all_sheets"],
        "_load_csv_sheets": io_ns["_load_csv_sheets"],
        "_correct_direction_from_balance": bank_statement.correct_direction_from_balance,
        "_verify_row_balances": bank_statement.verify_row_balances,
        "_repair_amount_from_balance": bank_statement.repair_amount_from_balance,
    }
    extract(payload, "services/recon/bank_stmt_xlsx.py", READER_FUNCS, READER_CONSTS, reader)
    # The summary-row table lives with the balance chain on this side, so keep the
    # pinned copy next to the rest for the value comparison.
    reader["_SUMMARY_ROW_KW"] = io_ns["_SUMMARY_ROW_KW"]
    return reader


def row_state(rows):
    out = []
    for row in rows:
        state = []
        for field in ROW_FIELDS:
            value = getattr(row, field, None)
            state.append(str(value) if value is not None else None)
        out.append(tuple(state))
    return out


def normalized(result):
    """The comparable projection of one reader result."""
    if not result.get("ok"):
        return {"ok": False, "error_code": result.get("error_code")}
    out = {field: result.get(field) for field in RESULT_FIELDS}
    out["rows"] = row_state(result["rows"])
    out["accounts"] = [dict({field: account.get(field) for field in ACCOUNT_FIELDS},
        rows=row_state(account["rows"])) for account in result["accounts"]]
    return out


HEADERS = (
    ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"],
    ["Date", "Description", "Withdrawal", "Deposit", "Balance"],
    ["Date", "Description", "Debit", "Credit", "Balance"],
    ["Init Br.", "Description", "In", "Out", "Balance"],
    ["Date", "Description", "Amount", "Balance"],
    ["Date", "Details", "Paid out", "Paid in", "Running Balance"],
)
DATES = ("01/06/2569", "02/06/2569", "2026-06-03", "", "03/06/69", "x")
DESCRIPTIONS = ("โอนเงิน", "ค่าอาหาร", "Total Credit", "รวมรายการ", "เงินสดย่อย", "",
    "Sweep", "ยอดยกมา", "ยกยอดมา")
NUMBERS = (0, 100, -100, 1250.5, 1000, 250, "", "1,250.00", "x", 0.0, -39.15)


def workbook(sheets):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets:
        sheet = book.create_sheet(title=name)
        for row in rows:
            sheet.append(list(row))
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def sheet_rows(rng, header, header_at, opening_label=None):
    rows = []
    for _ in range(header_at):
        if opening_label and rng.random() < 0.5:
            rows.append([opening_label, rng.choice(("ยกมา", "brought forward", "期初")), "",
                "", rng.choice((-39.15, -7409714.58, 1000.0, 0.0))])
        else:
            rows.append(["Account No.", "984-2-99825-8", "", "", ""])
    rows.append(list(header))
    for _ in range(rng.randrange(1, 6)):
        values = [rng.choice(DATES), rng.choice(DESCRIPTIONS)]
        if "amount" in [str(cell).lower() for cell in header]:
            values.append(rng.choice(NUMBERS))
        else:
            values.extend([rng.choice(NUMBERS), rng.choice(NUMBERS)])
        values.append(rng.choice(NUMBERS))
        rows.append(values)
    return rows


def fixtures(rng):
    for _ in range(220):
        count = rng.randrange(1, 3)
        sheets = []
        for index in range(count):
            header = list(rng.choice(HEADERS))
            header_at = rng.choice([0, 1, 5, 11, 15, 20])
            label = rng.choice([None, "Account No.", "บัญชีเงินฝาก", "账号"])
            sheets.append((f"acct-{index}", sheet_rows(rng, header, header_at, label)))
        yield workbook(sheets), "stm.xlsx"
    # Fixed shapes: the KTB multi-account opening story, a header below the first 16
    # rows, a signed amount column, a summary row, an unreadable file and CSV bytes.
    yield workbook([("8258", [["Account No.", "8258", "", "", ""],
        ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"],
        ["01/06/2569", "ยกมา", "", "", -39.15],
        ["02/06/2569", "โอนเงิน", 100.0, "", 60.85]]),
        ("8606", [["Account No.", "8606", "", "", ""],
        ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"],
        ["01/06/2569", "ยกมา", "", "", -7409714.58],
        ["02/06/2569", "โอนเงิน", "", 500.0, -7409214.58]])]), "ktb.xlsx"
    yield workbook([("s", [["วันที่", "รายการ", "จำนวนเงิน", "ยอดคงเหลือ"]] * 20
        + [["01/06/2569", "โอนเงิน", 100.0, 100.0]])]), "deep.xlsx"
    yield workbook([("s", [["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"],
        ["01/06/2569", "โอนเงิน", 100.0, "", 100.0],
        ["02/06/2569", "โอนเงิน", "", 0.0, 100.0]])]), "zero.xlsx"
    yield workbook([("s", [["日期", "摘要", "取款", "存款", "余额"],
        ["01/06/2569", "โอนเงิน", 100.0, "", 100.0],
        ["02/06/2569", "合计", "", 9999.0, 10099.0]])]), "summary.xlsx"
    yield workbook([("empty", [["hello", "world"]])]), "nothing.xlsx"
    yield b"not a workbook at all", "broken.xlsx"
    yield "วันที่;รายการ;ถอนเงิน;ฝากเงิน;ยอดคงเหลือ\n01/06/2569;โอนเงิน;100;;100\n".encode("cp874"), "stm.csv"


def run(root=None, source_text=None):
    payload = (source_text if source_text is not None
        else {path: (Path(root) / path).read_text() for path in HASHES})
    legacy = load_legacy(payload)
    for name in sorted(READER_CONSTS):
        assert legacy[name] == getattr(bank_sheets, name), name
    assert legacy["_SUMMARY_ROW_KW"] == bank_statement.SUMMARY_ROW_KEYWORDS
    assert legacy["_hit"]("Init Br.", {"in"}) == table_io.header_hit("Init Br.", {"in"})

    rng = random.Random(20260922)
    counts = {"batches": 0, "installed": 0, "multi_account": 0, "accounts": 0,
        "closing_mismatch": 0, "balance_break": 0, "unreadable": 0, "no_header": 0,
        "summary_leaked": 0, "csv": 0}
    for content, filename in fixtures(rng):
        expected = normalized(legacy["parse_bank_stmt_xlsx_direct"](content, filename))
        produced = normalized(bank_sheets.read_statement_workbook(content, filename))
        assert expected == produced, (filename, expected, produced)
        counts["batches"] += 1
        if not produced["ok"]:
            counts["unreadable"] += 1 if produced["error_code"] == "file_unreadable" else 0
            counts["no_header"] += 1 if produced["error_code"] == "stmt_headers_not_found" else 0
            continue
        counts["installed"] += 1
        counts["accounts"] += len(produced["accounts"])
        counts["multi_account"] += 1 if produced["multi_account"] else 0
        issues = produced["completeness"]["issues"]
        counts["closing_mismatch"] += sum(1 for i in issues if i["type"] == "closing_mismatch")
        counts["balance_break"] += sum(1 for i in issues if i["type"] == "balance_break")
        # Summary rows must never reach the result — this counter has to stay 0.
        counts["summary_leaked"] += sum(
            1 for row in produced["rows"] if "total" in (row[1] or "").lower()
            or "รวมรายการ" in (row[1] or ""))
        counts["csv"] += 1 if str(filename).endswith(".csv") and produced["ok"] else 0
    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    assert counts["summary_leaked"] == 0, counts
    if (counts["batches"] < 200 or counts["installed"] < 100 or counts["multi_account"] < 15
            or counts["accounts"] < 110 or counts["unreadable"] < 1
            or counts["no_header"] < 1 or counts["csv"] < 1):
        raise SystemExit(f"fixture coverage below the floor: {counts}")
    raise SystemExit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path)
    group.add_argument("--source-stdin", action="store_true")
    arguments = parser.parse_args()
    if arguments.source_stdin:
        run(None, json.load(sys.stdin))
    else:
        run(arguments.source)
