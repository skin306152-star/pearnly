"""Hash-bound engine source differential; compile selected definitions, no legacy app import."""
import argparse
import ast
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace

from pydantic import ValidationError
from pearnly_cowork.ocr.engine import prompts
from pearnly_cowork.ocr.engine.fewshot import build_prompt_block
from pearnly_cowork.ocr.engine.parsing import _parse_json
from pearnly_cowork.ocr.engine.runner import sniff_direct_mime
from pearnly_cowork.ocr.schema import validate_invoice
from test_cowork_engine import CONFIG, GOOD, Model, Source, TEXT, run
from test_cowork_invoice_schema_source_parity import SOURCE_SHA, definitions, execute, read_sources
from test_cowork_invoice_validation_source_parity import legacy_validation, reason_shapes

HASHES = {'direct_read.py': '7417300bd59f1dd4b523685068e4230790d9a2e62ad9a35538bd99c64beb6cfa', 'layer2_prompts.py': '7087750fd5eede4dd0a36d7a4a5bbc4e32d4869c56bbf03b074ebcffaac0bfa6', 'layer2_gemini.py': 'b5a2af8baaea0ca6966354d028727890d7f9107680b9eb085b79271de8cd5f87', 'layer2_structure.py': '8b1e0cb0e31a70d5b25fdab8838438b57dc2938acdcc823ecff446501d7433ea', 'layer3_fallback.py': '8cdfd0cfed25b8756234a7bca952fe216ad8b5925f85594ac9ecfdf61e8fba9f', 'layer3_gemini.py': 'fc8d064cb100cf43e3212cfefe6ced1147a2dc630b1536a8c2001bad115cc785', 'totals_rescue.py': '1d5df4e2623ce74476751b56b866277a7760ce84c573dced736aaf926f054fa6', 'page_runner.py': 'c4ef017901b6e62c86d9135ddfeba8c2326b5650ea7c8b902cb1bb0ff0cf620f', 'feedback/fewshot.py': '32008315e86ad6b14739c22b459501a41dd6c2139ee4fcf37ac302018d03a7d5', 'pattern_memory.py': '63513537f1357a77a36d086d592ffd2ed4fd42ba4321ec76cb8776c514cfd6fa'}


L3_OLD_TYPES = '"document_type": "tax_invoice" | "receipt" | "credit_note" | "other",'
L3_NEW_TYPES = ('"document_type": "tax_invoice" | "simplified_tax_invoice" | "receipt" | "credit_note" | '
                '"payment_evidence" | "order_evidence" | "other",')


# 登记的有意改动（语料实测 2026-09-27）：小票单号取 R# 流水号；票面印了 VAT 照抄不重算。
L2_CHANGES = (
    ("invent or require a legal เลขที่.\n",
     "invent or require a legal เลขที่. On a slip printed as \"R#0000970791P2 :0302013\" the\n     invoice number is the R# part; the \":0302013\" after it is the store / POS code, never\n     the invoice number.\n"),
    # 用户 2026-09-28：只处理采购票与销售票，列多张发票的汇总清单 / 登记表 / 对账单不是发票。
    ("   note + tax invoice) IS a real tax invoice — never reject those.\n7b.",
     "   note + tax invoice) IS a real tax invoice — never reject those.\n"
     "7d. LISTS OF SEVERAL INVOICES are not invoices: a sales / purchase register or summary\n"
     "   (รายงานภาษีขาย / ภาษีซื้อ, รายละเอียดใบกำกับภาษี, a monthly sales or delivery summary, a customer\n"
     "   statement) — a table with one row per invoice number and no single issuer invoice header — set\n"
     "   is_not_invoice = true and do NOT turn its rows into additional_invoices.\n7b."),
    ("Copy the printed VAT as-is; do not recompute it.\n",
     "Copy the printed VAT as-is; do not recompute it. Shops mark VAT-exempt items (N / E),\n   so a printed VAT can be far below 7/107 of the total — keep the printed figure.\n"),
)
# 登记的有意改动（用户 2026-09-27：旧项目分店票记到总店名下）：两方都读分店号，总公司 = 00000。
BRANCH_CHANGES = (
    ('\n  "seller_tax": "13-digit Thai tax ID or empty string",\n',
     '\n  "seller_tax": "13-digit Thai tax ID or empty string",\n  "seller_branch": "5-digit branch or empty string",\n'),
    ('\n  "buyer_tax": "13-digit Thai tax ID or empty string",\n',
     '\n  "buyer_tax": "13-digit Thai tax ID or empty string",\n  "buyer_branch": "5-digit branch or empty string",\n'),
    ("5. TAX IDs: Exactly 13 digits, no dashes/spaces. Empty string if not found.\n",
     "5. TAX IDs: Exactly 13 digits, no dashes/spaces. Empty string if not found.\n"
     "5b. BRANCH (สาขา): seller_branch / buyer_branch = that party's branch as printed next to its name or tax ID, "
     "as 5 digits: \"สำนักงานใหญ่\" / \"Head Office\" / \"HQ\" -> \"00000\"; \"สาขาที่ 18\" / \"สาขา 00018\" / "
     "\"Branch 18\" -> \"00018\". Empty string when not printed. Never copy one party's branch to the other.\n"),
)
L3_CHANGES = (
    ("The correct relationship is: subtotal + vat = total_amount (within rounding tolerance).\n",
     "The correct relationship is: subtotal + vat = total_amount (within rounding tolerance). Copy a printed VAT exactly; never recompute it from the total (VAT-exempt items make it smaller).\n"),
    ("9. is_copy_or_duplicate: true if the text contains สำเนา / COPY / DUPLICATE markers.\n10. MULTIPLE",
     "9. is_copy_or_duplicate: true if the text contains สำเนา / COPY / DUPLICATE markers.\n9b. INVOICE NUMBER on shop slips: \"R#0000970791P2 :0302013\" → invoice_number is the R# part; the \":0302013\" after it is the store / POS code, never the invoice number.\n10. MULTIPLE"),
)


def registered(text, changes):
    for old, new in changes:
        assert text.count(old) == 1, old
        text = text.replace(old, new, 1)
    return text


def load(root):
    sources = read_sources(root)
    for name, expected in HASHES.items():
        data = (Path(root) / "services" / "ocr" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, name
        sources[name] = ast.parse(data.decode())
    return sources


def compare_page(old, new):
    left, right = old.model_dump(mode="json"), new.model_dump(mode="json")
    # Provider receipts now retain ALL retries/rescue and use deterministic summed
    # callback duration, unlike old wall time / final-attempt-only counters.
    for value in (left, right):
        for name in ("layer1_ms", "layer2_ms", "layer3_ms", "total_ms", "layer2_input_tokens",
                     "layer2_output_tokens", "layer3_input_tokens", "layer3_output_tokens", "layer3_model"):
            value.pop(name)
        value["error"] = bool(value["error"])
        value["validation_warnings"] = reason_shapes(value["validation_warnings"])
    assert left == right, (left, right)


def run_parity(sources):
    old = legacy_validation(sources)
    for name in ("BoundingBox", "Word", "Paragraph", "Block", "Page"):
        old[name].model_rebuild(_types_namespace=old, force=True)
    execute(definitions(sources["layer2_gemini.py"], {"_first_json_object", "_parse_json"}), old)
    execute(definitions(sources["layer2_prompts.py"], {"_SYSTEM_PROMPT", "_USER_PROMPT_PREFIX", "_RETRY_TRIM_HINT"}), old)
    assert registered(old["_SYSTEM_PROMPT"], (*L2_CHANGES, *BRANCH_CHANGES)) == prompts.L2_SYSTEM
    assert old["_USER_PROMPT_PREFIX"] == prompts.L2_USER_PREFIX
    assert old["_RETRY_TRIM_HINT"] == prompts.L2_RETRY_HINT
    execute(definitions(sources["direct_read.py"], {"_IMAGE_INPUT_NOTE", "_sniff_mime"}), old)
    assert old["_IMAGE_INPUT_NOTE"] == prompts.DIRECT_IMAGE_NOTE
    execute(definitions(sources["layer3_gemini.py"], {"_RETRY_HINT_BASE"}), old)
    assert old["_RETRY_HINT_BASE"] == prompts.L3_RETRY_HINT
    execute(definitions(sources["layer3_fallback.py"], {"_SYSTEM_PROMPT", "_INDEPENDENT_REREAD", "MAX_TEXT_LENGTH", "_build_user_prompt"}), old)
    # 登记的有意改动（ADR-0012）：第三轮票种枚举补齐为共用码表的 7 个，其余逐字不变。
    assert registered(old["_SYSTEM_PROMPT"], ((L3_OLD_TYPES, L3_NEW_TYPES), *L3_CHANGES, *BRANCH_CHANGES)) \
        == prompts.L3_SYSTEM
    execute(definitions(sources["totals_rescue.py"], {"_SYSTEM_PROMPT"}), old)
    assert old["_SYSTEM_PROMPT"] == prompts.RESCUE_SYSTEM
    old["json"] = json
    rng = random.Random(20260921)
    parsed = 0
    for i in range(1200):
        data = {"text": rng.choice(("{}", "brace }", 'quote "', "泰文ไทย", "\\", "")), "i": i}
        raw = rng.choice((json.dumps(data), "```json\n" + json.dumps(data) + "\n```",
                          "prose " + json.dumps(data) + " after", "broken {", "[1]", "null", ""))
        def outcome(fn):
            try: return fn(raw)
            except json.JSONDecodeError: return "parse failure"
        assert outcome(old["_parse_json"]) == outcome(_parse_json)
        parsed += 1
    for raw in (b"", b"GIF89a", b"\xff\xd8", b"RIFF1234WEBP", b"II*\x00", b"BM", b"\x89PNG\r\n\x1a\n"):
        assert old["_sniff_mime"](raw) == sniff_direct_mime(raw)
    for length in (0, 20, 20000, 20001, 30000):
        text = "a" * length
        invoice = validate_invoice(GOOD, anchor_year=2026)
        for triggers in ([], ["missing total", "wrong amount"]):
            assert old["_build_user_prompt"](text, invoice, triggers) == prompts.build_l3_user_prompt(text, invoice, triggers)
    execute(definitions(sources["feedback/fewshot.py"], {"_MAX_EXAMPLES", "build_prompt_block"}), old)
    for count in range(25):
        examples = [{"field_name": f"f{i}", "ai_value": str(i) if i % 2 else "", "corrected_value": "泰"} for i in range(count)]
        assert old["build_prompt_block"](examples) == build_prompt_block(examples)

    class Fallback(Exception): pass
    old.update(time=SimpleNamespace(time=lambda: 0), ValidationError=ValidationError,
               engine_policy=SimpleNamespace(MODE_INVOICE_PAGE_READER={}, active_mode=lambda: "economy"),
               _tier_for=lambda _: "flash_lite", DirectReadFallback=Fallback,
               double_read_enabled=lambda: False, logger=SimpleNamespace(warning=lambda *a: None, info=lambda *a: None))
    execute(definitions(sources["direct_read.py"], {"read_page", "_invoice_hard_gates", "_invoice_soft_flags"}), old)
    direct_count = 0
    for i in range(600):
        payload = deepcopy(GOOD)
        payload.update(invoice_number=rng.choice(("IV69/100", "", None)),
                       document_type=rng.choice(("tax_invoice", "receipt", "credit_note")),
                       date=rng.choice(("2026-09-21", "2016-09-21", None)),
                       total_amount=rng.choice(("107", "102", "", "3", "-1")))
        old["_call_model"] = lambda *a, **k: SimpleNamespace(ok=True, data=deepcopy(payload), model="lite", input_tokens=10, output_tokens=20)
        try:
            legacy = old["read_page"](b"PNG", 1, "invoice")
        except Fallback:
            # 登记的有意改动（ADR-013，2026-09-27）：旧线上直读没过闸就整份改走 Vision + L2；现在同一页先带提示复核，
            # 闸判定本身不变（同一批输入在旧线上走回落的，新代码也判没过）。
            result = run(model=Model([payload, GOOD]))
            page = result.pipeline.pages[0]
            assert result.pipeline.engine == "image_checked" and page.layer_chain == ["ID", "RC"]
            assert page.extraction_audit["direct_fallback"]["code"] == "ocr.direct_gate_failed"
        else:
            result = run(model=Model([payload]))
            page = result.pipeline.pages[0]
            if payload["document_type"] == "tax_invoice" and not page.invoice.is_not_invoice:
                # 登记的有意改动（2026-09-27 大语料 A/B）：完整税票过了直读闸也拿 Vision 原文核对单号 / 税号（`L1`），
                # 判定与字段不变（桩原文里有这个单号、不改），只多这一步。
                assert page.layer_chain == ["ID", "L1"], page.layer_chain
                page = page.model_copy(update={"layer_chain": ["ID"]})
            compare_page(legacy, page)
        direct_count += 1

    class L3Error(Exception): pass
    class L3Fallback(L3Error): pass
    class L3Auth(L3Error): pass
    class L3Quota(L3Error): pass
    class L3Transient(L3Error): pass
    old.update(gemini_models=SimpleNamespace(flash_lite=lambda: "lite", fallback=lambda: "pro", flash=lambda: "pro"),
               quota_pacing=SimpleNamespace(call_with_backoff=lambda call, **k: call()),
               image_first=SimpleNamespace(is_enabled=lambda: False),
               escalation_budget=SimpleNamespace(try_escalate=lambda: True),
               Layer3FallbackError=L3Fallback, Layer3AuthError=L3Auth, Layer3QuotaError=L3Quota,
               Layer3TransientError=L3Transient, Layer3Error=L3Error)
    execute(definitions(sources["page_runner.py"], {"_process_one_page"}), old)
    pipeline_count = 0
    scenarios = [GOOD, {**GOOD, "invoice_number": "WRONG"}, {**GOOD, "total_amount": ""},
                 {**GOOD, "document_type": "credit_note"}, {**GOOD, "date": "2016-09-21"},
                 {**GOOD, "source_refs": {"total_amount": {"value": "107", "source_column": "Description"}}},
                 {**GOOD, "total_amount": "102"}, {"is_not_invoice": True}]
    for payload in scenarios:
        for confidence in (0, .7, .9, 1):
            for text_path in (True, False):
                evidence = old["Page"](page_number=1, width=1, height=1, blocks=[], full_text=TEXT, avg_confidence=confidence)
                l2 = old["ThaiInvoice"](**deepcopy(payload))
                old["_l1_extract_image"] = lambda *a, **k: SimpleNamespace(pages=[evidence])
                old["_l2_extract_page"] = lambda *a, **k: old["Layer2PageResult"](page_number=1, invoice=l2, input_tokens=10, output_tokens=20)
                old["_l3_refine_page"] = lambda *a, **k: old["Layer3PageResult"](page_number=1, invoice=old["ThaiInvoice"](**GOOD), input_tokens=10, output_tokens=20)
                legacy = old["_process_one_page"](b"PNG", 1, None, True, True, layer1_page_override=evidence if text_path else None, document_type="invoice")
                source = Source(text=text_path)
                if text_path: source.text_pages[0].avg_confidence = confidence
                from dataclasses import replace
                from pearnly_cowork.ocr.engine import VisionResponse
                from pearnly_cowork.ocr.schema import Page
                def vision(*a, **k):
                    return VisionResponse(Page(**evidence.model_dump()), {})
                result = run(source, Model([payload, GOOD]), config=replace(CONFIG, direct_enabled=False), vision_call=vision)
                compare_page(legacy, result.pipeline.pages[0])
                pipeline_count += 1
    execute(definitions(sources["totals_rescue.py"], {"_FIELDS", "TOTALS_RESCUED_PREFIX", "apply_rescue", "rescue_note"}), old)
    fallback_count = 0
    for rescue in ({"subtotal": "100", "vat": "7", "total_amount": "107"}, {}, {"total_amount": "999"}):
        payload = {**GOOD, "invoice_number": "WRONG", "vat": "9", "total_amount": "109"}
        l2 = old["ThaiInvoice"](**payload)
        old["_l2_extract_page"] = lambda *a, **k: old["Layer2PageResult"](page_number=1, invoice=l2, input_tokens=10, output_tokens=20)
        def fail_l3(*a, **k): raise L3Fallback("content failed")
        old["_l3_refine_page"] = fail_l3
        old["totals_rescue"] = SimpleNamespace(rescue_totals=lambda *a: rescue or None,
            apply_rescue=old["apply_rescue"], rescue_note=old["rescue_note"])
        legacy = old["_process_one_page"](b"PNG", 1, None, True, True, layer1_page_override=evidence, document_type="invoice")
        result = run(Source(text=True), Model([payload, "bad", "bad", rescue]))
        compare_page(legacy, result.pipeline.pages[0])
        fallback_count += 1
    for source_error, target_status in ((L3Quota, 429), (L3Transient, 503), (L3Error, 400)):
        l2 = old["ThaiInvoice"](**{**GOOD, "invoice_number": "WRONG"})
        old["_l2_extract_page"] = lambda *a, **k: old["Layer2PageResult"](page_number=1, invoice=l2, input_tokens=10, output_tokens=20)
        def refuse_l3(*a, **k): raise source_error("definite refusal")
        old["_l3_refine_page"] = refuse_l3
        legacy = old["_process_one_page"](b"PNG", 1, None, True, True, layer1_page_override=evidence, document_type="invoice")
        from pearnly_cowork.ocr.engine.contracts import ProviderFailure
        result = run(Source(text=True), Model([{**GOOD, "invoice_number": "WRONG"},
                     ProviderFailure("definite", http_status=target_status)]))
        compare_page(legacy, result.pipeline.pages[0])
        fallback_count += 1
    return {"prompt_constants": 7, "json_grammar": parsed, "mime": 7, "l3_prompt": 10,
            "fewshot_prompt": 25, "direct_decisions": direct_count, "pipeline_decisions": pipeline_count, "l3_fallbacks": fallback_count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    print(f"PASS {SOURCE_SHA}: {run_parity(load(args.source))}")
