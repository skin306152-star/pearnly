"""Differential check of the migrated model path for statements and ledgers.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed sources as JSON
(the bench has the reconciliation package). The pinned functions are executed from
their fixed text with the devices replaced by fakes: the model client, the upgrade
policy, the in-memory/on-disk caches, the PDF page splitter and the re-read
renderer/reader. That is deliberate — those are the parts this batch does not port,
so replacing them is what isolates the decisions that were ported.

The statement case renders the deployed escalation policy in the harness itself
(gate the payload, then convert it), because that policy lives in the model client
the deployed code calls and is not part of this port.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from pearnly_cowork.reconciliation import bank_model_path, bank_prompts, bank_reread
from pearnly_cowork.reconciliation.bank_matching import GlRow
from pearnly_cowork.reconciliation.bank_parse import parse_date, statement_rows_from_entries
from pearnly_cowork.reconciliation.bank_statement import StatementRow, finalize_rows

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/recon/bank_stmt_gemini.py":
        "9d0d277b4a03eb12ac02bbc1d6968cf44dccedc502d30d60ef2706569cd38170",
    "services/recon/bank_gl_gemini.py":
        "6e2c61111e075052f16d961cb49b4083d41b2280f4d0aab032553d21810c99a0",
    "services/recon/bank_stmt_reread.py":
        "1e15ee4fc4c15880551e09702fdb82f9195922130b51ddde48a58700a27e0f64",
}

STATEMENT_FUNCS = {"_stmt_has_content", "_gemini_parse_statement"}
GL_FUNCS = {"gemini_parse_gl", "_row_from_dict"}
GL_CONSTS = {"_PROMPT", "_PAGES_PER_CHUNK"}
REREAD_FUNCS = {"maybe_reread_chain_breaks", "_reread_one_page", "_count_breaks", "enabled"}
REREAD_CONSTS = {"EYE_VISION", "EYE_DIRECT", "_MAX_REREAD_PAGES", "_RENDER_DPI",
    "_MAX_WORKERS"}
# The three injection points the deployed module rebinds at import time are set
# directly in the namespace instead (their defaults need PyMuPDF).

STATEMENT_ROW_FIELDS = ("date", "description", "withdrawal", "deposit", "balance",
    "source_file", "account_no", "row_hash", "confidence")
GL_ROW_FIELDS = ("date", "doc_no", "account_code", "description", "debit", "credit",
    "source_file", "balance", "row_hash")


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


def local_literal(payload, path, function_name, variable):
    """A string literal assigned inside a pinned function (the statement prompt)."""
    tree = ast.parse(payload[path])
    function = [node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name][0]
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == variable:
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing {function_name}.{variable}")


class QuietLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


class ModelQueue:
    """Stand-in for the model client's upgrade policy: hand back what was queued."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def __call__(self, call, *, primary=None, ok=None, label=""):
        self.calls += 1
        payload = self.payloads.pop(0) if self.payloads else None
        if payload is None:
            return None
        if ok is not None and not ok(payload):
            return None
        return payload


def load_legacy(payload):
    statement = {
        "hashlib": hashlib, "logging": __import__("logging"),
        "Any": Any, "Dict": Dict,
        "StatementRow": StatementRow, "_parse_date": parse_date,
        "logger": QuietLogger(),
        "_GEMINI_STMT_CACHE": {},
        "_cache_get": lambda cache, key: None,
        "_cache_put": lambda cache, key, value: None,
        "_disk_cache_get": lambda key: None,
        "_disk_cache_put": lambda key, value: None,
        "flash_lite": lambda: "flash-lite",
    }
    extract(payload, "services/recon/bank_stmt_gemini.py", STATEMENT_FUNCS, set(), statement)
    ledger = {
        "logging": __import__("logging"), "Any": Any, "Dict": Dict, "List": List,
        "Optional": Optional, "GlRow": GlRow, "_parse_date": parse_date,
        "logger": QuietLogger(),
    }
    extract(payload, "services/recon/bank_gl_gemini.py", GL_FUNCS, GL_CONSTS, ledger)
    reread = {
        "logging": __import__("logging"), "os": os,
        "ThreadPoolExecutor": ThreadPoolExecutor,
        # The deployed cost-attribution wrapper is a device: keep the pool.submit shape.
        "submit_ctx": lambda pool, function, *args: pool.submit(function, *args),
        "statement_rows_from_entries": statement_rows_from_entries,
        "StatementRow": StatementRow, "finalize_rows": finalize_rows,
        "logger": QuietLogger(),
    }
    extract(payload, "services/recon/bank_stmt_reread.py", REREAD_FUNCS, REREAD_CONSTS, reread)
    return {"statement": statement, "ledger": ledger, "reread": reread}


def statement_row_state(row):
    return tuple(str(getattr(row, field, None)) for field in STATEMENT_ROW_FIELDS)


def gl_row_state(row):
    return tuple(str(getattr(row, field, None)) for field in GL_ROW_FIELDS)


def native_statement_call(data):
    """The ported pieces composed the way the deployed escalation policy used them."""
    if not bank_model_path.statement_has_content(data):
        return {"ok": False, "rows": [], "error": "gemini statement parse failed (all models)"}
    rows, opening, printed_totals = bank_model_path.statement_from_model(data)
    return {
        "ok": True,
        "rows": rows,
        "opening": opening,
        "closing": float(data.get("closing_balance", 0) or 0),
        "bank_code": data.get("bank_code", "generic"),
        "printed_totals": printed_totals,
    }


def normalized_statement(result):
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}
    return {
        "ok": True,
        "opening": result["opening"],
        "closing": result["closing"],
        "bank_code": result["bank_code"],
        "printed_totals": result["printed_totals"],
        "rows": [statement_row_state(row) for row in result["rows"]],
    }


DESCRIPTIONS = ("โอนเงิน", "ค่าอาหาร", "ยอดยกมา", "brought forward", "opening balance",
    "balance b/f", "期初余额", "", "รวมรายการ")
AMOUNTS = (None, 0, 100.0, 1250.5, -50.0, 0.0, 39.15)
CONFIDENCES = ("high", "medium", "low", "certain", None)
DATES = ("2026-06-01", "2026-06-02", "", None, "01/06/2569", "not a date", "01/06/69")


def statement_payloads(rng):
    for _ in range(200):
        rows = []
        for _ in range(rng.randrange(0, 6)):
            row = {"date": rng.choice(DATES), "description": rng.choice(DESCRIPTIONS)}
            if rng.random() < 0.8:
                row["withdrawal"] = rng.choice(AMOUNTS)
            if rng.random() < 0.8:
                row["deposit"] = rng.choice(AMOUNTS)
            if rng.random() < 0.85:
                row["balance"] = rng.choice(AMOUNTS)
            if rng.random() < 0.7:
                row["confidence"] = rng.choice(CONFIDENCES)
            rows.append(row)
        payload = {"rows": rows}
        if rng.random() < 0.5:
            payload["opening_balance"] = rng.choice(AMOUNTS + (1000.0,))
        if rng.random() < 0.5:
            payload["closing_balance"] = rng.choice(AMOUNTS + (900.0,))
        if rng.random() < 0.4:
            payload["bank_code"] = rng.choice(("kbank", "scb", "generic", None))
        for field in ("printed_total_debit", "printed_total_credit", "printed_debit_count",
                "printed_credit_count"):
            if rng.random() < 0.5:
                payload[field] = rng.choice((None, 0, 4, 8000.0, "8000.00", "n/a"))
        yield payload
    # Fixed shapes: the blank read, an empty statement, and non-mappings.
    yield {"rows": [], "opening_balance": 0, "closing_balance": None}
    yield {"rows": []}
    yield {"rows": [], "opening_balance": 39749.85}
    yield {"rows": None}


def chunk_payloads(rng):
    for _ in range(140):
        chunks = []
        for _ in range(rng.randrange(0, 4)):
            if rng.random() < 0.3:
                chunks.append(None)  # a chunk whose model call failed
                continue
            rows = []
            for _ in range(rng.randrange(0, 4)):
                rows.append({
                    "date": rng.choice(("2026-06-01", "02/06/2569", "", "x")),
                    "doc_no": rng.choice(("PV-1", "", "6201")),
                    "account_code": rng.choice(("5301", "5301-01", "6201", "")),
                    "description": rng.choice(("ค่าอาหาร", "โอนเงิน", "")),
                    "debit": rng.choice((0, 100.0, 1250.5, None)),
                    "credit": rng.choice((0, 50.0, None, 39.15)),
                })
            chunk = {"rows": rows}
            if rng.random() < 0.6:
                chunk["opening_balance"] = rng.choice((0, 1000.0, None, 39749.85))
            chunks.append(chunk)
        yield chunks, rng.choice(("", "5301", "6201"))
    yield [], ""
    yield [None, None], "5301"
    yield [{"rows": []}], ""


def run(root=None, source_text=None):
    payload = (source_text if source_text is not None
        else {path: (Path(root) / path).read_text() for path in HASHES})
    legacy = load_legacy(payload)
    statement_ns = legacy["statement"]
    ledger_ns = legacy["ledger"]
    reread_ns = legacy["reread"]

    # The prompt texts are compared byte for byte against the pinned literals.
    assert local_literal(payload, "services/recon/bank_stmt_gemini.py",
        "_gemini_parse_statement", "prompt") == bank_prompts.STATEMENT_PROMPT
    assert local_literal(payload, "services/recon/bank_stmt_gemini.py",
        "_gemini_parse_statement", "_STMT_PROMPT_VER") == bank_prompts.STATEMENT_PROMPT_VERSION
    assert ledger_ns["_PROMPT"] == bank_prompts.GL_PROMPT
    assert ledger_ns["_PAGES_PER_CHUNK"] == bank_prompts.GL_PAGES_PER_CHUNK
    for account_code in ("", "5301", "4"):
        hint = f" Filter to account code starting with '{account_code}'." if account_code else ""
        assert ledger_ns["_PROMPT"].format(hint=hint) == bank_prompts.gl_prompt(account_code)
    assert reread_ns["EYE_VISION"] == bank_reread.EYE_VISION
    assert reread_ns["EYE_DIRECT"] == bank_reread.EYE_DIRECT
    assert reread_ns["_MAX_REREAD_PAGES"] == bank_reread.MAX_REREAD_PAGES

    rng = random.Random(20260922)
    counts = {"statement": 0, "statement_usable": 0, "statement_blank": 0,
        "statement_rows": 0, "statement_low_confidence": 0, "gl": 0, "gl_ok": 0,
        "gl_failed": 0, "gl_failed_chunk": 0, "gl_filtered": 0, "gl_rows": 0}
    for data in statement_payloads(rng):
        # The deployed escalation policy lives in the model client: hand it the payload
        # through the same `ok=` gate it used there.
        statement_ns["try_with_fallback"] = ModelQueue([data])
        expected = normalized_statement(statement_ns["_gemini_parse_statement"](
            b"pdf-bytes", "f.pdf", "key"))
        produced = normalized_statement(native_statement_call(data))
        assert expected == produced, (data, expected, produced)
        counts["statement"] += 1
        if produced["ok"]:
            counts["statement_usable"] += 1
            counts["statement_rows"] += len(produced["rows"])
            counts["statement_low_confidence"] += sum(
                1 for row in produced["rows"] if row[-1] == "low")
        else:
            counts["statement_blank"] += 1

    for chunks, account_code in chunk_payloads(rng):
        queue = ModelQueue(chunks)
        ledger_ns["try_with_fallback"] = queue
        ledger_ns["_page_chunks"] = lambda file_bytes, per_chunk, chunks=chunks: [
            b"chunk-%d" % index for index in range(len(chunks))]
        expected = ledger_ns["gemini_parse_gl"](b"pdf", "gl.pdf", account_code, "key")
        produced = bank_model_path.gl_from_model_chunks(chunks, account_code)
        assert _row_states(expected.get("rows")) == _row_states(produced.get("rows")), (
            chunks, account_code, expected, produced)
        for field in ("ok", "accounts", "opening", "closing", "row_count", "error"):
            assert expected.get(field) == produced.get(field), (field, chunks, account_code,
                expected, produced)
        counts["gl"] += 1
        if produced["ok"]:
            counts["gl_ok"] += 1
            counts["gl_rows"] += produced["row_count"]
            counts["gl_filtered"] += 1 if account_code else 0
        else:
            counts["gl_failed"] += 1
        counts["gl_failed_chunk"] += sum(1 for chunk in chunks if chunk is None)

    counts.update({"reread": 0, "reread_kept": 0, "reread_discarded": 0, "reread_raised": 0,
        "reread_skipped": 0, "pages_read": 0})
    for scenario in reread_scenarios(rng):
        os.environ.pop("OCR_BANK_CHAIN_REREAD", None)
        assert reread_ns["enabled"]() is True
        reread_ns["_pdf_page_count"] = lambda file_bytes, count=scenario["page_count"]: count
        reread_ns["_render_page"] = lambda file_bytes, page: b"png-%d" % page
        original = rows_for(scenario["original"], scenario["opening"])
        seen = []
        native_seen = []

        def read_page(image_bytes, page_number, filename, api_key):
            seen.append(page_number)
            rows = scenario["pages"].get(page_number)
            if rows == "raise":
                raise RuntimeError("render failed")
            return rows_for(rows or [], scenario["opening"])

        reread_ns["_read_page_rows"] = read_page
        expected_rows, expected_escalations = reread_ns["maybe_reread_chain_breaks"](
            original, scenario["opening"], file_bytes=b"pdf", filename="f.pdf",
            ext=scenario["ext"], api_key=None)
        produced_rows, produced_escalations = bank_reread.maybe_reread_chain_breaks(
            original, scenario["opening"], extension=scenario["ext"],
            page_count=scenario["page_count"],
            read_page=lambda page, pages=scenario["pages"], opening=scenario["opening"]:
                (native_seen.append(page), _raise(pages.get(page), opening))[1])
        # This side reads the pages one by one in page order (the deployed one fans
        # them out; the fakes may therefore record a different interleaving).
        assert native_seen == list(range(1, len(native_seen) + 1))
        assert [row.row_hash for row in expected_rows] == [row.row_hash for row in produced_rows]
        assert expected_escalations == produced_escalations, (scenario, expected_escalations,
            produced_escalations)
        counts["reread"] += 1
        counts["pages_read"] += len(seen) + len(native_seen)
        if not expected_escalations:
            counts["reread_skipped"] += 1
        elif expected_escalations[0]["kept"]:
            counts["reread_kept"] += 1
        else:
            counts["reread_discarded"] += 1
        if any(value == "raise" for value in scenario["pages"].values()) \
                and scenario["ext"] == "pdf" and scenario["page_count"] <= 50:
            counts["reread_raised"] += 1

    for rows, opening in ((rows_for(ROWS_CLEAN, 1000.0), 1000.0),
            (rows_for(ROWS_BROKEN, 1000.0), 1000.0), ([], 0.0)):
        assert reread_ns["_count_breaks"](rows) == bank_reread.count_chain_breaks(rows)

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    floors = {"statement": 200, "statement_usable": 120, "statement_blank": 15,
        "statement_rows": 300, "statement_low_confidence": 20, "gl": 140, "gl_ok": 40,
        "gl_failed": 10, "gl_failed_chunk": 30, "gl_filtered": 20, "gl_rows": 60,
        "reread": 70, "reread_kept": 5, "reread_discarded": 8, "reread_raised": 3,
        "reread_skipped": 10, "pages_read": 25}
    for name, floor in floors.items():
        if counts[name] < floor:
            raise SystemExit(f"fixture coverage below the floor: {name}={counts[name]} < {floor}")
    raise SystemExit(0)


def _raise(value, opening):
    if value == "raise":
        raise RuntimeError("render failed")
    return rows_for(value or [], opening)


def _row_states(rows):
    return [gl_row_state(row) for row in (rows or [])]


ROWS_CLEAN = [("โอนเงิน", 0.0, 100.0, 1100.0)]
ROWS_BROKEN = [("โอนเงิน", 0.0, 100.0, 1100.0), ("ค่าอาหาร", 50.0, 0.0, 9999.0)]


def rows_for(entries, opening):
    return finalize_rows([
        StatementRow(date=None, description=description, withdrawal=withdrawal,
            deposit=deposit, balance=balance)
        for description, withdrawal, deposit, balance in entries
    ], opening)


def reread_scenarios(rng):
    page_shapes = [
        [("โอนเงิน", 0.0, 100.0, 1100.0)],
        [("โอนเงิน", 0.0, 100.0, 1200.0)],
        [("ค่าอาหาร", 50.0, 0.0, 1150.0)],
        ROWS_BROKEN,
        [],
        "raise",
    ]
    for index in range(70):
        page_count = rng.choice((1, 1, 2, 3, 7, 51))
        pages = {page: rng.choice(page_shapes) for page in range(1, page_count + 1)}
        if index % 7 == 0:
            pages = {}
        yield {
            "original": rng.choice((ROWS_CLEAN, ROWS_BROKEN, ROWS_BROKEN, [])),
            "opening": rng.choice((1000.0, 0.0)),
            "page_count": page_count,
            "pages": pages,
            "ext": rng.choice(("pdf", "pdf", "pdf", "xlsx", "csv")),
        }
    # Shapes where the other eye is clearly better, and shapes where it is not.
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": 1,
        "pages": {1: [("โอนเงิน", 0.0, 100.0, 1100.0), ("โอนเงิน", 0.0, 100.0, 1200.0)]},
        "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": 2,
        "pages": {1: [("โอนเงิน", 0.0, 100.0, 1100.0)],
            2: [("ค่าอาหาร", 50.0, 0.0, 1050.0)]}, "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 0.0, "page_count": 3,
        "pages": {1: [("รับโอน", 0.0, 500.0, 500.0)],
            2: [("โอนออก", 100.0, 0.0, 400.0)],
            3: [("โอนออก", 400.0, 0.0, 0.0)]}, "ext": "pdf"}
    yield {"original": [("ค่าอาหาร", 50.0, 0.0, 9999.0), ("โอนเงิน", 0.0, 100.0, 10099.0)],
        "opening": 1000.0, "page_count": 1,
        "pages": {1: [("โอนเงิน", 0.0, 100.0, 1100.0)]}, "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": 1,
        "pages": {1: ROWS_BROKEN}, "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": 1,
        "pages": {1: [("โอนเงิน", 0.0, 100.0, 9999.0)]}, "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": 2,
        "pages": {1: [("โอนเงิน", 0.0, 100.0, 1100.0)],
            2: [("โอนเงิน", 0.0, 100.0, 1100.0)]}, "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": 0, "pages": {}, "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": -1, "pages": {}, "ext": "pdf"}
    yield {"original": ROWS_BROKEN, "opening": 1000.0, "page_count": 2, "pages": {1: "raise"},
        "ext": "pdf"}


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
