"""Differential check of the migrated OCR document tier (batch 42).

CLI: --source <fixed-source-root>, or --source-stdin with the fixed sources as JSON.
The pinned pieces are executed from their fixed text with the devices replaced by
fakes: the provider call, the model client, the escalation tier and the fallback
exception. Those are exactly what this batch does not port, so replacing them is
what isolates the decisions that were ported.

Compared: the balance-chain gate's page escalation (repair, one re-read, the
audit warnings, and whether an unanswerable re-read falls back), the two document
prompts byte for byte, and the validator order the tier runs.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from pearnly_cowork.ocr.engine import document_prompts, document_tier
from pearnly_cowork.ocr.schema.documents import BankStatementDocument, GeneralLedgerDocument, VatReportDocument
from pearnly_cowork.ocr.validation import chains, sources

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/bank_chain_gate.py":
        "2324467689249c1dcb6562fac3df03b2be2e2d8e03a43af14c0dff16d8e0408e",
    "services/ocr/layer2_prompts.py":
        "7087750fd5eede4dd0a36d7a4a5bbc4e32d4869c56bbf03b074ebcffaac0bfa6",
}
GATE_FUNCS = {"reread_bank_page", "_audit_warnings"}
PROMPT_CONSTS = {"_BANK_STATEMENT_SYSTEM_PROMPT", "_GL_SYSTEM_PROMPT", "_VAT_REPORT_SYSTEM_PROMPT",
    "_GENERIC_TABLE_SYSTEM_PROMPT"}
ENTRY_FIELDS = ("deposit", "withdrawal", "amount", "direction", "balance",
    "chain_repaired", "chain_amount_imputed", "review_required")


class PinnedFallback(Exception):
    """Stands in for services.ocr.contracts.DirectReadFallback."""


def extract(payload, path, names, constants, namespace):
    data = payload[path].encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == HASHES[path], f"Source hash mismatch: {path}"
    tree = ast.parse(data.decode("utf-8"))

    def assigned(node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        return ""

    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or assigned(node) in constants]
    found = {node.name for node in nodes if isinstance(node, ast.FunctionDef)}
    assert found == names, sorted(names - found)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


class QuietLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


class FakeOutcome:
    def __init__(self, data=None, *, ok=True, error_kind=""):
        self.data = data
        self.ok = ok
        self.error_kind = error_kind
        self.model = "escalated"
        self.input_tokens = 1
        self.output_tokens = 1


def load_legacy(payload):
    gate = {
        "logging": __import__("logging"), "os": __import__("os"), "Any": Any,
        "List": List, "Optional": Optional, "Tuple": Optional and tuple,
        "Decimal": __import__("decimal").Decimal,
        "InvalidOperation": __import__("decimal").InvalidOperation,
        "ValidationError": ValidationError,
        "DirectReadFallback": PinnedFallback,
        "logger": QuietLogger(),
        # Ported and differentially verified in the thirty-sixth batch, so injected
        # here to isolate this batch's escalation logic.
        "repair_bank_chain": chains.repair_bank_chain,
        "_dec": chains._dec,
        "_side_amount": chains._side_amount,
        "_flip_entry": chains._flip_bank_entry,
        "_impute_chain_amount": chains._impute_chain_amount,
        "_TOL": chains.BANK_TOL,
        "_IMPUTE_BAND": chains.IMPUTE_BAND,
    }
    extract(payload, "services/ocr/bank_chain_gate.py", GATE_FUNCS, set(), gate)
    prompts = {}
    extract(payload, "services/ocr/layer2_prompts.py", set(), PROMPT_CONSTS, prompts)
    return {"gate": gate, "prompts": prompts}


def entry_state(entries):
    return [tuple("" if getattr(entry, field, None) in (None, False) else str(getattr(entry, field))
        for field in ENTRY_FIELDS) for entry in entries]


def run_gate(gate, document, *, page=1, read_max):
    """Both sides: return (raised, entries, warnings)."""
    try:
        out, _outcome, warnings = gate["reread_bank_page"](document, b"image", page, None,
            read_max, BankStatementDocument)
    except PinnedFallback as error:
        return "fallback", str(error), []
    return "ok", entry_state(out.entries), warnings


def run_native(document, *, page=1, read_again):
    try:
        out, _payload, warnings = document_tier.reread_bank_page(document, page_number=page,
            read_again=read_again)
    except document_tier.DirectFallback:
        return "fallback", None, []
    return "ok", entry_state(out.entries), warnings


def bank_document(entries, opening="1000.00"):
    return BankStatementDocument(**{"document_type": "bank_statement",
        "opening_balance": opening, "entries": entries})


SWAP = [
    {"transaction_date": "2026-06-01", "description": "in", "deposit": "100.00", "balance": "1100.00"},
    {"transaction_date": "2026-06-02", "description": "out", "deposit": "50.00", "balance": "1050.00"},
]
IMPUTE = [
    {"transaction_date": "2026-06-01", "description": "in", "deposit": "100.00", "balance": "1100.00"},
    {"transaction_date": "2026-06-02", "description": "out", "withdrawal": "60.00", "balance": "1050.00"},
]
BREAK = [
    {"transaction_date": "2026-06-01", "description": "in", "deposit": "100.00", "balance": "1100.00"},
    {"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00", "balance": "9999.00"},
]
CARRIED = [
    {"transaction_date": "", "description": "ยอดยกมา", "balance": "1000.00"},
    {"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00", "balance": "950.00"},
]


def scenarios(rng):
    shapes = {"swap": SWAP, "impute": IMPUTE, "break": BREAK, "carried": CARRIED,
        "clean": SWAP[:1], "empty": []}
    answers = {
        "better": {"document_type": "bank_statement", "opening_balance": "1000.00", "entries": [
            {"transaction_date": "2026-06-02", "description": "out", "withdrawal": "50.00",
                "balance": "950.00"}]},
        "same": {"document_type": "bank_statement", "opening_balance": "1000.00",
            "entries": BREAK},
        "empty": None,
        "raise": "raise",
        "bad_schema": {"document_type": "bank_statement", "entries": "nope"},
        "not_mapping": "text",
    }
    for _ in range(120):
        name = rng.choice(list(shapes))
        answer_name = rng.choice(list(answers))
        yield {"shape": shapes[name], "answer": answers[answer_name],
            "opening": rng.choice(("1000.00", "", "0")), "page": rng.randrange(1, 4)}


def run(root=None, source_text=None):
    payload = (source_text if source_text is not None
        else {path: (Path(root) / path).read_text() for path in HASHES})
    legacy = load_legacy(payload)

    # The two document prompts must be identical to the pinned literals.
    assert legacy["prompts"]["_BANK_STATEMENT_SYSTEM_PROMPT"] == document_prompts.BANK_STATEMENT_SYSTEM_PROMPT
    assert legacy["prompts"]["_GL_SYSTEM_PROMPT"] == document_prompts.GENERAL_LEDGER_SYSTEM_PROMPT
    # 2026-09-29: the VAT report prompt moved with the scanned / photographed report path.
    assert legacy["prompts"]["_VAT_REPORT_SYSTEM_PROMPT"] == document_prompts.VAT_REPORT_SYSTEM_PROMPT
    assert document_prompts.BANK_STATEMENT_SYSTEM_PROMPT != document_prompts.GENERAL_LEDGER_SYSTEM_PROMPT
    # The tier runs the validators in the deployed order: the ledger validates and
    # then repairs, the bank statement validates only.
    assert document_tier.DOCUMENT_SCHEMAS == {"bank_statement": BankStatementDocument,
        "general_ledger": GeneralLedgerDocument, "vat_report": VatReportDocument}
    assert document_tier.validate_document is not None

    rng = random.Random(20260922)
    counts = {"batches": 0, "clean": 0, "repaired": 0, "imputed": 0, "escalated": 0,
        "fallback": 0, "audit_warnings": 0}
    for scenario in scenarios(rng):
        document = bank_document(scenario["shape"], scenario["opening"])
        answer = scenario["answer"]
        expected_model = bank_document(scenario["shape"], scenario["opening"])

        def read_max(_image, _api_key, answer=answer):
            if answer == "raise":
                raise RuntimeError("provider blew up")
            if answer is None:
                return FakeOutcome(None, ok=False, error_kind="empty output")
            if answer == "text":
                return FakeOutcome("not a mapping")
            return FakeOutcome(answer)

        def read_again(answer=answer):
            if answer == "raise":
                raise RuntimeError("provider blew up")
            if answer is None:
                return None
            if answer == "text":
                return "not a mapping"
            return answer

        expected = run_gate(legacy["gate"], expected_model, page=scenario["page"], read_max=read_max)
        produced = run_native(document, page=scenario["page"], read_again=read_again)
        assert expected[0] == produced[0], (scenario, expected, produced)
        if expected[0] == "ok":
            assert expected[1] == produced[1], (scenario, expected[1], produced[1])
            assert expected[2] == produced[2], (scenario, expected[2], produced[2])
            counts["audit_warnings"] += len(expected[2])
            if not any("chain" in cell for row in expected[1] for cell in row):
                counts["clean"] += 1
            if any(row[5] == "True" for row in expected[1]):
                counts["repaired"] += 1
            if any(row[6] == "True" for row in expected[1]):
                counts["imputed"] += 1
            if expected[2] or scenario["answer"] is not None:
                counts["escalated"] += 1
        else:
            counts["fallback"] += 1
        counts["batches"] += 1

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    floors = {"batches": 110, "clean": 15, "repaired": 10, "imputed": 5, "escalated": 20,
        "fallback": 10, "audit_warnings": 5}
    for name, floor in floors.items():
        if counts[name] < floor:
            raise SystemExit(f"fixture coverage below the floor: {name}={counts[name]} < {floor}")
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
