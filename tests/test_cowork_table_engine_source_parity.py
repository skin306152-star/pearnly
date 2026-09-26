"""Fixed-source table execution differential; no legacy application imports or IO callbacks."""
import argparse
import ast
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import random
from types import SimpleNamespace

from pydantic import ValidationError

from pearnly_cowork.ocr.engine import EngineError
from pearnly_cowork.ocr.engine.runner import page_text
from pearnly_cowork.ocr.table_engine.roundtrip import has_roundtrip_sheet
from test_cowork_engine import CONFIG, GOOD, Model, TEXT
from test_cowork_engine_source_parity import compare_page, load
from test_cowork_invoice_schema_source_parity import SOURCE_SHA, definitions, execute
from test_cowork_invoice_validation_source_parity import legacy_validation
from test_cowork_table_engine import document, page, roundtrip_provenance, run

HASHES = {
    "services/ocr/pipeline.py": "9e8fa8fb566a8b457c0ae6f64f6961069c757994269c9b65f6284a14a7843e52",
    "services/ocr/table_path.py": "4c12cc5acea0e5633261ca866ba45bfd74c02bde0fcb5e2e2b617f21e62a671d",
    "services/excel/erp_roundtrip.py": "03aaf37f6396613811d3c5723fd3c685ec121415b88770d3632c38f7d76f33ad",
    "services/excel/erp_roundtrip_reader.py": "26a367002ff778ae00e43ee309c3c90077accca1dfef2a4eb32420e09de171bc",
}


def table_sources(root):
    sources = load(root)  # Also binds schemas, prompts, validators and page_runner.
    for name, expected in HASHES.items():
        data = (Path(root) / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, name
        sources[Path(name).name] = ast.parse(data.decode())
    return sources


def legacy_table(sources):
    old = legacy_validation(sources)
    for name in ("BoundingBox", "Word", "Paragraph", "Block", "Page"):
        old[name].model_rebuild(_types_namespace=old, force=True)
    old.update(time=SimpleNamespace(time=lambda: 0), ValidationError=ValidationError,
               logger=SimpleNamespace(warning=lambda *a: None, info=lambda *a: None),
               gemini_models=SimpleNamespace(flash_lite=lambda: "lite"),
               quota_pacing=SimpleNamespace(call_with_backoff=lambda call, **k: call()),
               image_first=SimpleNamespace(is_enabled=lambda: False),
               escalation_budget=SimpleNamespace(try_escalate=lambda: True),
               DEFAULT_MODEL="lite", DEFAULT_MAX_RETRIES=1, DEFAULT_TIMEOUT_SECONDS=60,
               _resolve_gemini_key=lambda api_key: None)
    execute(definitions(sources["layer2_gemini.py"], {"Layer2Error"}), old)
    execute(definitions(sources["layer2_prompts.py"], {
        "_SYSTEM_PROMPT", "_USER_PROMPT_PREFIX", "_RETRY_TRIM_HINT"}), old)
    # Keep exact prompt construction. Replace imports only: authorized fewshots
    # are injected and the old ERP context is disabled for this Cowork product.
    nodes = definitions(sources["layer2_gemini.py"], {"_call_gemini_with_retry"})
    class NoImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node): return None
    nodes = [NoImports().visit(node) for node in nodes]
    old.update(fewshot=SimpleNamespace(maybe_block_for_text=lambda text: "PINNED FEWSHOT\n"),
               extend=lambda prompt: prompt)
    execute(nodes, old)
    execute(definitions(sources["layer2_structure.py"], {
        "MIN_TEXT_LENGTH", "MAX_TEXT_LENGTH", "extract_from_page", "_page_to_text", "_extract_internal"}), old)
    old["_l2_extract_page"] = old["extract_from_page"]
    execute(definitions(sources["page_runner.py"], {"_process_one_page"}), old)
    execute(definitions(sources["pipeline.py"], {"run_on_table_bytes"}), old)
    old["_compute_total_cost"] = lambda pages: 0
    execute(definitions(sources["erp_roundtrip.py"], {
        "SHEET_SALES", "SHEET_PURCHASE", "SHEET_PENDING", "DIRECTION_BY_SHEET", "DATA_SHEETS",
        "COL_PARTY_TAX", "COL_PARTY_BRANCH", "COL_DOC_VAT", "COL_ERP_DOCNUM", "COL_ERP_ITEM",
        "COL_ERP_PARTY", "COL_PUSH_STATUS", "COL_ROW_KEY", "ROUNDTRIP_HEADERS", "OPTIONAL_HEADERS",
        "_REQUIRED_HEADERS", "is_roundtrip_sheet", "is_data_sheet"}), old)
    return old


def registered_where_present(prompt):
    from test_cowork_engine_source_parity import L2_CHANGES

    for before, after in L2_CHANGES:
        prompt = prompt.replace(before, after, 1)
    return prompt


def run_parity(sources):
    old = legacy_table(sources)
    rng = random.Random(20260921)
    marker_count = 0
    for _ in range(1500):
        headers = list(old["ROUNDTRIP_HEADERS"])
        headers = [h for h in headers if rng.randrange(5)] + rng.choice(([], ["", None, "=formula"]))
        rng.shuffle(headers)
        name = rng.choice(("ขาย", "ซื้อ", "รอจำแนก", " ขาย ", "สรุป", "other"))
        row = rng.choice((1, 2, 3))
        expected = row == 1 and old["is_data_sheet"](name) and old["is_roundtrip_sheet"](headers)
        assert has_roundtrip_sheet(roundtrip_provenance(name, headers, source_row=row)) == expected
        marker_count += 1

    payloads = [GOOD, {**GOOD, "invoice_number": "WRONG"}, {**GOOD, "total_amount": ""},
                {**GOOD, "date": "2016-09-21"}, {**GOOD, "document_type": "credit_note"},
                {**GOOD, "total_amount": "102"}, {**GOOD, "seller_tax": "1111111111111"},
                {**GOOD, "items": [{"subtotal": "10"}, {"subtotal": "20"}]},
                {**GOOD, "source_refs": {"total_amount": {"value": "107", "source_column": "Description"}}},
                {**GOOD, "additional_invoices": [{**GOOD, "invoice_number": "IV69/101"}]},
                {"is_not_invoice": True}, {}]
    decision_count = 0
    for doc_type in ("invoice", "auto"):
        for confidence in (0, .7, .95, 1):
            for payload in payloads:
                for structured in (False, True, "empty"):
                    current = page(avg_confidence=confidence)
                    if structured is True:
                        current.table_headers = ["Description", "Amount"]
                        current.table_rows = [{"Description": TEXT, "Amount": "107"}]
                    elif structured == "empty":
                        current.full_text = "short"
                    evidence = old["Page"](**current.model_dump())
                    assert old["_page_to_text"](evidence) == page_text(current)
                    captured = []
                    def gateway(prompt, key, model, retries, timeout):
                        captured.append((prompt, model, retries, timeout))
                        return deepcopy(payload), {"input_tokens": 10, "output_tokens": 20, "retries": 0}
                    old["_call_l2_via_gateway"] = gateway
                    old["_table_extract"] = lambda *a, **k: SimpleNamespace(pages=[evidence])
                    legacy = old["run_on_table_bytes"](b"synthetic", "input.csv", document_type=doc_type)
                    model = Model([payload])
                    result = run(document([current]), model, config=replace(CONFIG, document_type=doc_type),
                                 fewshot_selector=lambda text: "PINNED FEWSHOT\n")
                    assert legacy.engine == result.pipeline.engine
                    assert legacy.page_count == result.pipeline.page_count
                    compare_page(legacy.pages[0], result.pipeline.pages[0])
                    # 表格路径复用发票 L2 提示词：套上引擎对照里登记的有意改动再比（语料实测 2026-09-27）。
                    captured = [(registered_where_present(prompt), *rest) for prompt, *rest in captured]
                    assert captured == [(r.prompt, r.model, CONFIG.json_retries, r.timeout_s) for r in model.requests]
                    decision_count += 1

    # Explicit new admission rule: legacy silently truncates; new path refuses
    # the full document before calling a model, and does not invent row chunks.
    captured = []
    def gateway(prompt, *args):
        captured.append(prompt)
        return GOOD, {"input_tokens": 1, "output_tokens": 2, "retries": 0}
    old["_call_l2_via_gateway"] = gateway
    old["_extract_internal"]("x" * 30001, None, "lite", 1, 60)
    assert captured[0].endswith("x" * 30000) and not captured[0].endswith("x" * 30001)
    model = Model()
    try:
        run(document([page(text="x" * 30001)]), model)
    except EngineError as exc:
        assert exc.code == "ocr.table_text_too_long"
    else:
        raise AssertionError("unannounced input truncation")
    assert not model.requests
    return {"roundtrip_markers": marker_count, "page_prompt_and_decisions": decision_count,
            "intentional_truncation_change": 1}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    print(f"PASS {SOURCE_SHA}: {run_parity(table_sources(args.source))}")
