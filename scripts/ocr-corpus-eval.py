#!/usr/bin/env python3
"""用真实语料对 ERP 采购识别与 Cowork 发票识别做付费实测，对照金标打分。

**付费外部读取**：每张图会调用 Vertex（与线上同一套模型与凭据），不进默认回归，只在人工实测时跑。
不写业务数据：不建单、不扣租户费用、不落识别记录；Cowork 引擎用一个不落库的调用适配器驱动。

在 bench 环境里跑（需要站点的 OCR 连接配置）：

    env/bin/python scripts/ocr-corpus-eval.py --site <site> --corpus <dir> --out <dir> [--product erp|cowork|both]

语料目录：`manifest.json` = [{"id", "file", "set", "gold": {...}}]，图片与 manifest 同目录。
金标字段（有哪项比哪项）：total、vat、subtotal、doc_no、seller_tax、doc_date（YYYY-MM-DD）、
claims_input_vat（true/false）、price_mode（inclusive/exclusive）。
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import traceback
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import frappe


def money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("฿", "").strip())
    except (InvalidOperation, ValueError):
        return None


def code(value):
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def retrying(call, label):
    from pearnly_core.execution.errors import Deferred, QueueFull

    for attempt in range(6):
        try:
            return call()
        except (Deferred, QueueFull) as exc:
            wait = 20 * (attempt + 1)
            print(f"  {label}: {type(exc).__name__} {getattr(exc, 'reason', '')}, wait {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("rate limited too long")


# ── ERP ───────────────────────────────────────────────────────────────────────


OWN_TAX = {"sm-tax": "0105567178203"}  # 语料里的本公司税号（买方就是本公司）


USAGE = []


def spy_core():
    """记下每次模型 / Vision 调用的模型、耗时与 token（算每张成本与速度用）。"""
    import pearnly_core.ocr as core
    from pearnly_core.execution import provider

    if getattr(provider, "_eval_spied", False):
        return
    original = provider.post

    def spied(url, headers, payload, timeout_s, model, bucket):
        started = time.monotonic()
        response = original(url, headers, payload, timeout_s, model, bucket)
        usage = None
        try:
            usage = response.json().get("usageMetadata") if response.status_code == 200 else None
        except Exception:  # noqa: BLE001
            pass
        USAGE.append({"model": model, "ms": int((time.monotonic() - started) * 1000),
                      "status": response.status_code, "usage": usage,
                      "stage": (payload.get("labels") or {}).get("stage")})
        return response

    provider.post = spied
    provider._eval_spied = True


def run_erp(content, mime, own_tax=None):
    spy_core()
    USAGE.clear()
    from pearnly_core import ocr
    from pearnly_erp_portal.purchasing import amounts, recognition as pr
    from pearnly_erp_portal.recognition import policy

    images = [ocr.upright(content, mime)]
    documents = retrying(lambda: policy.read_all(pr.PROMPT, images, structured=pr._structured, review=pr.review,
                                                 label="eval", fields=pr.HEADER_FIELDS, finish=pr._finish), "erp")
    if not documents:
        return {"error": "nothing"}
    # 与 `recognise` 相同：按需 OCR 核对（本公司税号由语料给定，不查库）。
    from unittest.mock import patch

    with patch.object(frappe.db, "get_value", return_value=own_tax):
        documents = pr._anchor_with_ocr(documents, images, "CO" if own_tax else None)
    result, issues, model = documents[0]
    from pearnly_core.tax_documents import codes as tax_codes
    if result.get("document_type") == tax_codes.CREDIT_NOTE:
        result = {**result, "blocked": "credit_note"}
    out = {"model": model, "issues": issues, "documents": len(documents), "result": result, "usage": list(USAGE)}
    try:
        totals = amounts.calculate(result.get("lines") or [], has_vat=result.get("has_vat") is not False,
                                   price_mode=result.get("price_mode") or "exclusive",
                                   discount_total=result.get("discount_total") or 0,
                                   rounding=result.get("rounding") or 0, discount_before_vat=True,
                                   vat_from_total=True)
        out["computed"] = {key: str(totals[key]) for key in ("subtotal", "vat_amount", "grand_total", "net_payable")}
    except Exception as exc:  # noqa: BLE001 - scoring records why the card would show no amounts
        out["computed"] = {"error": str(exc)}
    printed = result.get("printed") or {}
    # 实付额（扣了预扣税后）才是票面「合计」的口径。
    grand = (out["computed"] or {}).get("net_payable")
    vat = (out["computed"] or {}).get("vat_amount") if result.get("has_vat") is not False else "0"
    out["fields"] = {
        "total": grand, "printed_total": printed.get("total"), "vat": vat, "read_vat": printed.get("vat"), "read_subtotal": printed.get("subtotal"),
        "subtotal": (out["computed"] or {}).get("subtotal"), "doc_no": result.get("doc_no"),
        "seller_tax": result.get("supplier_tax_id"), "doc_date": result.get("doc_date"),
        "claims_input_vat": result.get("has_vat") is not False, "price_mode": result.get("price_mode"),
        "document_type": result.get("document_type"), "supplier": result.get("supplier_name"),
        "lines": len(result.get("lines") or []), "not_document": bool(result.get("not_document")),
        "documents": len(documents),
    }
    return out


def run_erp_sales(content, mime):
    from pearnly_core import ocr
    from pearnly_erp_portal.recognition import policy
    from pearnly_erp_portal.sales import contracts, recognition as sr

    images = [ocr.upright(content, mime)]
    documents = retrying(lambda: policy.read_all(sr.PROMPT, images, structured=sr._structured, review=sr.review,
                                                 label="eval-sales", fields=sr._HEAD, finish=sr._finish), "erp-sales")
    if not documents:
        return {"error": "nothing"}
    result, issues, model = documents[0]
    out = {"model": model, "issues": issues, "documents": len(documents), "result": result}
    try:
        totals = contracts.compute_totals(result.get("lines") or [],
                                          vat_rate=result.get("vat_rate") if result.get("has_vat") else 0,
                                          header_discount_amount=result.get("header_discount") or 0,
                                          price_includes_vat=bool(result.get("price_includes_vat")))
        out["computed"] = {key: str(totals[key]) for key in ("vat_amount", "grand_total")}
    except Exception as exc:  # noqa: BLE001
        out["computed"] = {"error": str(exc)}
    printed = result.get("printed") or {}
    out["fields"] = {"total": out["computed"].get("grand_total"), "printed_total": printed.get("total"),
                     "vat": out["computed"].get("vat_amount"), "read_vat": printed.get("vat"),
                     "doc_no": result.get("doc_no"), "doc_date": result.get("doc_date"),
                     "lines": len(result.get("lines") or []), "documents": len(documents)}
    return out


# ── Cowork ────────────────────────────────────────────────────────────────────


def cowork_client(environment):
    import requests
    from pearnly_core import ocr
    from pearnly_cowork.ocr.providers.client import ProviderClient

    def post(*, url, headers, payload, timeout_s, model, bucket):
        return requests.post(url, headers=headers, json=payload, timeout=timeout_s)

    return ProviderClient(environment, project=ocr.project(), post=post,
                          checkpoint_key=lambda payload, identity: hashlib.sha256(
                              (identity + json.dumps(payload, sort_keys=True)[:4096]).encode()).hexdigest(),
                          reject_checkpoint=lambda key, value: None, terminal_read=lambda key: None,
                          terminal_write=lambda key, value: None, feedback=lambda *args: None,
                          scope=lambda *args: None, access_token=ocr.access_token, require_context=lambda: None)


def run_cowork(content, filename):
    from pearnly_core import ocr
    from pearnly_core.tax_documents import rules as tax_rules
    from pearnly_cowork.ocr.engine import run_invoice
    from pearnly_cowork.ocr.engine.config import config_from_environment
    from pearnly_cowork.ocr.inputs_runtime.source import PageSource
    from pearnly_cowork.ocr.policy import invoice_policy
    from pearnly_cowork.push.express.field_rules import judge_direction

    # 与线上开户写入的 Cowork 识别配置相同（`infrastructure/production/site-step.py` COWORK_OCR）。
    environment = {"OCR_LLM_BACKEND": "vertex", "GCP_PROJECT": ocr.project(), "VERTEX_LOCATION": ocr.location()}
    selected = invoice_policy(environment, {"mode": "economy"})
    config = config_from_environment(environment, selected)
    client = cowork_client(environment)
    today = date.today()
    pages_count = 1
    if filename.lower().endswith(".pdf"):
        from pearnly_erp_portal.recognition import inputs

        pages_count = max(1, len(inputs.pdf_pages(content)))

    def call():
        with PageSource(content, filename, expected_page_count=pages_count, enable_text_path=selected.text_fast_path,
                        image_max_long_edge=2400) as pages:
            return run_invoice(pages, client.model_call, client.vision_call, config=config,
                               anchor_year=today.year, today=today)

    result = retrying(call, "cowork")
    page = result.pipeline.pages[0]
    invoice = page.invoice
    data = invoice.model_dump(mode="json") if invoice is not None else {}
    kind, _route = judge_direction(dict(data), today=today) if invoice is not None else (None, None)
    return {
        "engine": result.pipeline.engine, "models": sorted({call.model for call in result.calls}),
        "calls": len(result.calls), "call_log": [{"stage": c.stage, "model": c.model, "text": (c.text or "")[:4000]}
                                                for c in result.calls],
        "band": getattr(page, "confidence_band", None),
        "warnings": [str(w)[:200] for w in (getattr(page, "validation_warnings", None) or [])][:10],
        "error": getattr(page, "error", None), "result": data,
        "fields": {
            "total": data.get("total_amount"), "vat": data.get("vat"), "read_vat": data.get("vat"),
            "subtotal": data.get("subtotal"), "read_subtotal": data.get("subtotal"),
            "doc_no": data.get("invoice_number"), "seller_tax": data.get("seller_tax"),
            "doc_date": data.get("date"), "claims_input_vat": kind == "purchase_invoice",
            "document_type": data.get("document_type"), "supplier": data.get("seller_name"),
            "lines": len(data.get("items") or []), "not_document": bool(data.get("is_not_invoice")),
            "documents": len([p for p in result.pipeline.pages if p.invoice is not None]),
            "treatment_review": list(tax_rules.purchase_treatment(
                data.get("document_type"), vat=data.get("vat"), buyer_tax_id=data.get("buyer_tax"),
                buyer_name=data.get("buyer_name")).review),
        },
    }


# ── 打分 ─────────────────────────────────────────────────────────────────────


def score(fields, gold):
    marks = {}
    for key, field in (("total", "total"), ("vat", "read_vat"), ("subtotal", "read_subtotal")):
        if key in gold:
            want, got = money(gold[key]), money(fields.get(field))
            marks[key] = want is not None and got is not None and abs(want - got) <= Decimal("0.01")
    if "doc_no" in gold:
        marks["doc_no"] = bool(code(gold["doc_no"])) and code(gold["doc_no"]) in code(fields.get("doc_no")) \
            or code(fields.get("doc_no")) != "" and code(fields.get("doc_no")) in code(gold["doc_no"])
    if "seller_tax" in gold:
        marks["seller_tax"] = code(gold["seller_tax"]) == code(fields.get("seller_tax"))
    if "doc_date" in gold:
        marks["doc_date"] = str(fields.get("doc_date") or "")[:10] == gold["doc_date"]
    if "document_type" in gold:
        marks["document_type"] = fields.get("document_type") == gold["document_type"]
    if "not_document" in gold:
        marks["not_document"] = bool(fields.get("not_document")) == bool(gold["not_document"])
    if "documents" in gold:
        marks["documents"] = fields.get("documents") == gold["documents"]
    if "claims_input_vat" in gold:
        marks["claims_input_vat"] = bool(fields.get("claims_input_vat")) == bool(gold["claims_input_vat"])
    return marks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--product", default="both", choices=("erp", "cowork", "both", "erp-sales"))
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    frappe.init(site=args.site, sites_path=os.environ.get("SITES_PATH", "."))
    frappe.connect()
    corpus, out = Path(args.corpus), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    products = ("erp", "cowork") if args.product == "both" else (args.product,)
    runners = {"erp": lambda c, m, f, e: run_erp(c, m, OWN_TAX.get(e.get("set"))),
               "cowork": lambda c, m, f, e: run_cowork(c, f),
               "erp-sales": lambda c, m, f, e: run_erp_sales(c, m)}
    for entry in manifest:
        if args.only and not re.search(args.only, entry["id"]):
            continue
        content = (corpus / entry["file"]).read_bytes()
        mime = mimetypes.guess_type(entry["file"])[0] or "image/jpeg"
        for product in products:
            target = out / f"{product}--{entry['id']}.json"
            if target.exists():
                continue
            started = time.monotonic()
            try:
                row = runners[product](content, mime, entry["file"], entry)
            except Exception as exc:  # noqa: BLE001 - one bad document must not stop the corpus
                row = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]}
            row["seconds"] = round(time.monotonic() - started, 1)
            row["id"], row["set"], row["gold"] = entry["id"], entry.get("set"), entry.get("gold", {})
            row["marks"] = score(row.get("fields") or {}, row["gold"])
            target.write_text(json.dumps(row, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            bad = [key for key, ok in row["marks"].items() if not ok]
            print(f"{product:6} {entry['id']:48} {row['seconds']:6}s "
                  f"{'ERR ' + row['error'][:80] if row.get('error') and not row.get('fields') else ('OK' if not bad else 'MISS ' + ','.join(bad))}",
                  flush=True)
    frappe.db.rollback()
    frappe.destroy()


if __name__ == "__main__":
    sys.exit(main())
