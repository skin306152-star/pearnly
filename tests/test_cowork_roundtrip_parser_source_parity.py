"""Hash-bound deployed roundtrip algorithms, evaluated without legacy app imports.

Run with --source or --source-stdin. Source bytes are not vendored. All generated
workbooks and all old/new decoders run in the selected candidate Python runtime.
The previously verified schema is reused with explicit year 2026 on both sides;
this script isolates reader/intake behavior, not old wall-clock date behavior.
"""
import argparse
import ast
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import io
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace

from pearnly_cowork.ocr.schema import LineItem, PipelinePageResult, PipelineResult, validate_invoice
from pearnly_cowork.roundtrip import money, spec
from pearnly_cowork.roundtrip.contracts import to_pipeline, RoundtripParseError
from pearnly_cowork.roundtrip.parser import parse_roundtrip_workbook, _num
from test_cowork_roundtrip_parser import SALES_HEADERS, PURCHASE_HEADERS, PENDING_HEADERS, workbook_bytes, sales_row

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/excel/erp_roundtrip.py": "03aaf37f6396613811d3c5723fd3c685ec121415b88770d3632c38f7d76f33ad",
    "services/excel/erp_money.py": "6bfe9ba4ed0e69a7417e2c1520e682615dbdd43a133a245db3fb25a6d7d7bdc8",
    "services/excel/erp_roundtrip_reader.py": "26a367002ff778ae00e43ee309c3c90077accca1dfef2a4eb32420e09de171bc",
    "services/ocr/roundtrip_intake.py": "68f3eae7d3e67f821458818f7f1440e467bcd579332aee519e2e5135c439308b",
    "services/erp/express_push/common.py": "e00c6410c711208cf2bcbf20579d9d67e365b2957aa9d90a3cd9f68e4fe09f58",
}


def fixed_namespace(content, path, extra=None, only=None):
    namespace = {"__name__": "fixed_roundtrip_source", **(extra or {})}
    nodes = ast.parse(content).body
    if only is not None:
        nodes = [node for node in nodes if isinstance(node, ast.FunctionDef) and node.name in only]
    else:
        nodes = [node for node in nodes if not isinstance(node, ast.ImportFrom)
                 or not (node.module or "").startswith("services")]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), path, "exec"), namespace)
    return namespace


def legacy(contents):
    for path, expected in HASHES.items():
        assert hashlib.sha256(contents[path].encode()).hexdigest() == expected, "Source mismatch: " + path
    common = fixed_namespace(contents["services/erp/express_push/common.py"], "fixed:common",
        {"Decimal": Decimal, "ROUND_HALF_EVEN": ROUND_HALF_EVEN, "_CENT": Decimal("0.01")}, only={"_q"})
    old_spec = SimpleNamespace(**fixed_namespace(contents["services/excel/erp_roundtrip.py"], "fixed:spec"))
    old_money = SimpleNamespace(**fixed_namespace(contents["services/excel/erp_money.py"], "fixed:money",
                                                 {"quantize_money": common["_q"]}))
    old_reader = fixed_namespace(contents["services/excel/erp_roundtrip_reader.py"], "fixed:reader",
                                 {"rt": old_spec, "money": old_money})
    old_intake = fixed_namespace(contents["services/ocr/roundtrip_intake.py"], "fixed:intake", {
        "ThaiInvoice": lambda **values: validate_invoice(values, anchor_year=2026), "LineItem": LineItem,
        "PipelinePageResult": PipelinePageResult, "PipelineResult": PipelineResult,
        "parse_roundtrip_workbook": old_reader["parse_roundtrip_workbook"],
        "RoundtripParseError": old_reader["RoundtripParseError"],
    })
    return old_spec, old_money, old_reader, old_intake


def numbers_to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: numbers_to_decimal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [numbers_to_decimal(item) for item in value]
    return value


def original_shape(parsed):
    result = {key: deepcopy(parsed[key]) for key in ("documents", "pending", "sheets")}
    for item in [*result["documents"], *result["pending"]]:
        item.pop("source_row_keys", None)
    return result


def verify(contents):
    old_spec, old_money, old_reader, old_intake = legacy(contents)
    rng, count = random.Random(20260921), {"workbooks": 0, "pipeline_pages": 0, "money": 0, "numbers": 0, "spec": 0,
                                         "matching_decimal_failures": 0}

    def compare(content):
        try:
            old = old_reader["parse_roundtrip_workbook"](content)
        except InvalidOperation:
            try:
                parse_roundtrip_workbook(content)
            except RoundtripParseError as error:
                assert error.code == "ocr.roundtrip_parse_failed"
                count["matching_decimal_failures"] += 1
                return
            raise AssertionError("Old Decimal failure was silently accepted")
        new = parse_roundtrip_workbook(content)
        assert numbers_to_decimal(old) == original_shape(new), (old, new)
        previous = old_intake["try_parse_roundtrip"](content, "review.xlsx")
        current = to_pipeline(new, anchor_year=2026)
        before, after = previous.model_dump(), current.model_dump()
        before.pop("elapsed_ms"), after.pop("elapsed_ms")
        for page in after["pages"]:
            page["extraction_audit"] = {}
        assert before == after, (before, after)
        count["workbooks"] += 1
        count["pipeline_pages"] += current.page_count

    choices = [None, True, False, 0, 1, -1, 2.675, -2.675, .01, "", "none", "bad", "=E2*F2",
               " 2.675 ", "-0", "1,002.50", "฿35", "THB 20", "1.00000000000000009", "9007199254740993", "1e-10"]
    for value in choices:
        old, new = old_reader["_num"](value), _num(value)
        assert (Decimal(str(old)) if old is not None else None) == new, value
        count["numbers"] += 1
        assert old_money.to_money(value) == money.to_money(value), value
        count["money"] += 1
    for index in range(300):
        values = [rng.choice(choices) for _ in range(rng.randint(0, 6))]
        printed = rng.choice([None, "0", "7.01", "7.02", "7.03", "-0.005", "2.675", "-2.675"])
        explicit = rng.choice([None, 0, "100", "1.005", "9999999999999.99"])
        assert old_money.doc_totals(values, explicit_base=explicit, printed_vat=printed) == money.doc_totals(
            values, explicit_base=explicit, printed_vat=printed)
        assert old_money.allocate_vat(values, printed) == money.allocate_vat(values, printed)
        assert old_money.sum_money(values) == money.sum_money(values)
        assert old_money.vat_of(explicit) == money.vat_of(explicit)
        assert old_money.line_amount(explicit, printed) == money.line_amount(explicit, printed)
        count["money"] += 5

    for index in range(160):
        sales = []
        for row in range(rng.randint(1, 9)):
            sales.append(sales_row(**{
                spec.COL_ROW_KEY: rng.choice(["", "invalid", "PRN:original:0", "PRN:other:1", "PRN:x:-1"]),
                spec.SALES_COL_INVOICE: rng.choice(["INV1", "INV2", None, " EDITED "]),
                spec.SALES_COL_QTY: rng.choice(choices), spec.SALES_COL_PRICE: rng.choice(choices),
                spec.SALES_COL_AMOUNT: rng.choice(choices), spec.SALES_COL_PRE_VAT: rng.choice(choices),
                spec.COL_DOC_VAT: rng.choice([None, "7", "7.02", "-2.675", 0]),
            }))
        purchase = [{spec.PURCHASE_COL_INVOICE: "PURCHASE", spec.PURCHASE_COL_PRE_VAT: rng.choice(choices),
            spec.PURCHASE_COL_VAT: rng.choice(choices), spec.COL_DOC_VAT: rng.choice(choices),
            spec.COL_ROW_KEY: "PRN:original:0", spec.PURCHASE_COL_TOTAL: 999,
            spec.PURCHASE_COL_BRANCH: "00018", spec.COL_PARTY_BRANCH: "00000"} for _ in range(2)]
        pending = [{spec.PENDING_COL_INVOICE: rng.choice([None, "INV1"]), spec.PENDING_COL_REASON: "reason",
                    spec.COL_ROW_KEY: "PRN:original:1"}]
        headers = list(SALES_HEADERS)
        rng.shuffle(headers)
        if index % 2:
            headers.remove(spec.COL_PARTY_BRANCH)
        sheets = [(spec.SHEET_SALES, headers, sales), (spec.SHEET_PURCHASE, PURCHASE_HEADERS, purchase),
                  (spec.SHEET_PENDING, PENDING_HEADERS, pending), (spec.SHEET_SUMMARY, ["note"], [["ignored"]])]
        rng.shuffle(sheets)
        compare(workbook_bytes(sheets))

    for blanks in (0, 1, 49):
        compare(workbook_bytes([(spec.SHEET_SALES, SALES_HEADERS,
                                [sales_row(), *([[]] * blanks), sales_row()])]))
    headers = SALES_HEADERS + [spec.SALES_COL_QTY, "extra"]
    values = [sales_row().get(header) for header in headers]
    values[-2:] = [999, "note"]
    compare(workbook_bytes([(spec.SHEET_SALES, headers, [values])]))
    for value in ["", "note", "PRN:x:0", "PRN:x:-1", "PRN:x:bad", " PRN: x :2 ", "PRN:a:b:0"]:
        assert old_spec.decode_row_key(value) == spec.decode_row_key(value)
        count["spec"] += 1
    for status in (None, "", "success", "queued", "failed", "error", "manual", "skipped_dup", "other"):
        assert old_spec.status_label(status, "a" * 80) == spec.status_label(status, "a" * 80)
        count["spec"] += 1
    for header_set in ([], list(spec.ROUNDTRIP_HEADERS), list(spec.REQUIRED_HEADERS), list(spec.REQUIRED_HEADERS[:-1])):
        assert old_spec.is_roundtrip_sheet(header_set) == spec.is_roundtrip_sheet(header_set)
        count["spec"] += 1

    # Explicit changes: old ignored tail, old KeyError, old empty result, old nonfinite.
    changes = []
    trailing = workbook_bytes([(spec.SHEET_SALES, SALES_HEADERS, [sales_row(), *([[]] * 50), sales_row()])])
    assert old_reader["parse_roundtrip_workbook"](trailing)["documents"][0]["source_rows"] == [2]
    try:
        parse_roundtrip_workbook(trailing)
    except RoundtripParseError as error:
        assert error.code == "ocr.roundtrip_trailing_rows"
        changes.append("50_blank_rows_then_data")
    else:
        raise AssertionError("Trailing rows not rejected")
    spaced = workbook_bytes([(" " + spec.SHEET_SALES, SALES_HEADERS, [sales_row()])])
    assert old_intake["try_parse_roundtrip"](spaced, "x.xlsx") is None
    try:
        parse_roundtrip_workbook(spaced)
    except RoundtripParseError as error:
        assert error.code == "ocr.roundtrip_sheet_name_invalid"
        changes.append("trimmed_sheet_name")
    else:
        raise AssertionError("Invalid sheet name not rejected")
    empty = workbook_bytes([(spec.SHEET_SALES, SALES_HEADERS, [])])
    assert old_intake["try_parse_roundtrip"](empty, "x.xlsx") is None
    try:
        parse_roundtrip_workbook(empty)
    except RoundtripParseError as error:
        assert error.code == "ocr.roundtrip_empty"
        changes.append("recognized_empty")
    else:
        raise AssertionError("Empty recognized workbook not rejected")
    assert str(old_reader["_num"]("NaN")) == "nan"
    try:
        _num("NaN")
    except RoundtripParseError as error:
        assert error.code == "ocr.roundtrip_invalid_number"
        changes.append("nonfinite_number")
    else:
        raise AssertionError("Nonfinite accepted")
    print(json.dumps({"status": "PASS", "source": SOURCE_SHA, "equal_cases": count,
                      "explicit_changes_verified": changes}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    contents = json.load(sys.stdin) if args.source_stdin else {path: (args.source / path).read_text() for path in HASHES}
    verify(contents)
