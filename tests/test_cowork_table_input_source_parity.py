"""Hash-bound old/new table pages and billing units, using synthetic bytes only.

Execute selected fixed function ASTs, not legacy application imports. Both
decoders run in the candidate runtime; exact upstream production execution is
not claimed. --source-stdin accepts two fixed source texts, never credentials.
"""
import argparse
import ast
import csv
from datetime import datetime
import hashlib
import io
import json
import logging
from pathlib import Path
import random
import sys
import time
from typing import List

from pearnly_cowork.ocr.schema import Layer1Result, Page
from pearnly_cowork.ocr.table_inputs import extract_table, TableInputError
from test_cowork_table_input_runtime import document_bytes, workbook_bytes

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/table_path.py": "4c12cc5acea0e5633261ca866ba45bfd74c02bde0fcb5e2e2b617f21e62a671d",
    "services/billing/pricing.py": "fa7d4d604e2a39223a63667bc5f4c11669d9c2af7bce087e34ae42398456f908",
}


def legacy(contents):
    namespace = {"logger": logging.getLogger("table-source-parity"), "io": io, "csv": csv,
                 "time": time, "List": List, "Page": Page, "Layer1Result": Layer1Result,
                 "SUPPORTED_TABLE_EXTENSIONS": {".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".docx", ".doc", ".txt"}}
    for path, expected in HASHES.items():
        content = contents[path]
        if hashlib.sha256(content.encode()).hexdigest() != expected:
            raise ValueError("Source mismatch: " + path)
        functions = [node for node in ast.parse(content).body if isinstance(node, ast.FunctionDef)
                     and (path.endswith("table_path.py") or node.name == "excel_char_count_estimate")]
        exec(compile(ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
                     "fixed-source:" + path, "exec"), namespace)
    return namespace


def verify(contents):
    old, rng, counts = legacy(contents), random.Random(20260921), {}

    def compare(content, filename):
        previous = old["extract_from_table_file"](content, filename)
        current = extract_table(content, filename)
        assert previous.page_count == current.page_count, filename
        assert previous.engine == current.engine, filename
        assert [page.model_dump() for page in previous.pages] == [page.model_dump() for page in current.pages], filename
        assert old["excel_char_count_estimate"](content, filename) == current.billing_characters, filename
        extension = filename.rsplit(".", 1)[1]
        counts[extension] = counts.get(extension, 0) + 1

    for index in range(200):
        delimiter = "\t" if index % 2 else ","
        rows = [[rng.choice(["H", "Amount", "", " H ", "คำอธิบาย"]) for _ in range(rng.randint(1, 5))]]
        rows.extend([[rng.choice(["", "word", "ภาษาไทย", "20", "2.675", "a\nb", 'a"b'])
                      for _ in range(rng.randint(0, 7))] for _ in range(rng.randint(0, 8))])
        output = io.StringIO()
        csv.writer(output, delimiter=delimiter).writerows(rows)
        encoding = ("utf-8", "utf-8-sig", "cp874", "tis-620")[index % 4]
        compare(output.getvalue().encode(encoding), "x.tsv" if delimiter == "\t" else "x.csv")

    for index in range(60):
        content = rng.choice(["plain words\n", "ภาษาไทย\t123\n", " ", "中文发票"])
        encoding = "utf-8-sig" if index % 3 else "utf-8"
        compare((content * rng.randint(1, 8)).encode(encoding), "x.txt")
    for content in (b"\xff", b"\x00\x80\x81\xff", b"\xef\xbb\xbf"):
        compare(content, "x.txt")
        compare(content, "x.csv")

    for index in range(15):
        rows = [[], ["Name", "Amount", "Day", "Flag", "Formula"],
                ["Tea", 2.675, datetime(2026, 9, 21), True, "=1+2"], [],
                ["ภาษาไทย", -rng.randint(0, 200), None, False, None]]
        other = [["H", "H", ""], ["first", "last", "unnamed"]] if index % 2 else []
        content = workbook_bytes([("ขาย", rows), ("Other", other)])
        for extension in ("xlsx", "xlsm", "xls"):
            compare(content, "x." + extension)

    for index in range(30):
        paragraphs = ["ภาษาไทย", "", "Invoice\t" + str(index)] if index % 2 else []
        tables = [[["Name", "Amount"], ["Tea", str(index)], ["", ""]]] if index % 3 else []
        if index % 5 == 0:
            tables.append([["Other"], ["More"]])
        compare(document_bytes(paragraphs, tables), "x.docx")
    for content in (b"simple text\x00\x01\n123", "ภาษาไทย\n20".encode("cp874"), b"\x00\x01\x02"):
        compare(content, "x.doc")
    compare(document_bytes(["Mislabeled Word"], []), "x.doc")

    rejected = 0
    for content, filename in ((b"broken", "x.xlsx"), (b"broken", "x.xlsm"),
                              (b"\xd0\xcf\x11\xe0" + bytes(512), "x.xls"),
                              (b"broken", "x.docx"), (b"a\nb\rc", "x.csv")):
        try:
            old["extract_from_table_file"](content, filename)
        except Exception:
            pass
        else:
            raise AssertionError("Legacy unexpectedly accepted " + filename)
        try:
            extract_table(content, filename)
        except TableInputError:
            rejected += 1
        else:
            raise AssertionError("Migration unexpectedly accepted " + filename)
    print(json.dumps({"status": "PASS", "source": SOURCE_SHA, "page_and_character_cases": sum(counts.values()),
                      "cases_by_extension": counts, "matching_parse_failures": rejected}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    contents = json.load(sys.stdin) if args.source_stdin else {path: (args.source / path).read_text() for path in HASHES}
    verify(contents)
