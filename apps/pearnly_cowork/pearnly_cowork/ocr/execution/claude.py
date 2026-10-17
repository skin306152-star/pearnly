"""Cowork 的 Claude 识别路线（ADR-022）：本站点选了 Claude 时，图片 / PDF 整份（表格文件按逻辑页的文字）交共用层 ``pearnly_core.claude_ocr``
（Haiku→Opus，不调谷歌），把标准格式转成 ``PipelineResult``（每个实体页一条：该页第一张单放页上、其余进
``additional_invoices``；续页按「非发票」留作证据；第 9 页起读不了的页按「读不出」标红），之后存档、推送、扣费照旧。"""
import hashlib
import json
import mimetypes
import time

from pearnly_core.claude_ocr import client
from pearnly_core.tax_documents import codes as tax_codes
from pearnly_cowork.ocr.results import UNREAD_STAGE
from pearnly_cowork.ocr.schema import PipelineResult

ENGINE = "claude_v1"
# Claude 的单据种类 → Cowork 票种（共用码表）；不在表里的按「其他」。
TYPES = {"tax_invoice": tax_codes.TAX_INVOICE, "receipt_tax_invoice": tax_codes.TAX_INVOICE, "debit_note": tax_codes.TAX_INVOICE,
         "customs_import": tax_codes.TAX_INVOICE, "utility": tax_codes.TAX_INVOICE, "bill_invoice": tax_codes.TAX_INVOICE,
         "receipt": tax_codes.RECEIPT, "credit_note": tax_codes.CREDIT_NOTE, "transfer_slip": tax_codes.PAYMENT_EVIDENCE,
         "ecommerce_order": tax_codes.ORDER_EVIDENCE}
# 选了「录 / 不录」由租户定的两类（10-07）：照常出票，交给存档时的单据口径决定。
POLICY_KINDS = {"transfer_slip", "withholding_certificate"}
BAHT = {"THB", "บาท", "฿", "BAHT"}


def _s(value):
    return "" if value is None else str(value)


def _money(value):
    return client.claude_engine.rules.money(value)


def _type(kind, doc):
    if kind == "fuel_receipt":
        return tax_codes.TAX_INVOICE if doc.get("seller_tax") and doc.get("buyer_tax") else tax_codes.SIMPLIFIED_TAX_INVOICE
    if kind in ("tax_invoice", "receipt_tax_invoice", "bill_invoice", "utility") and not doc.get("seller_tax"):
        return tax_codes.RECEIPT
    if kind == "receipt_tax_invoice" and doc.get("price_mode") == "inclusive" and not doc.get("buyer_tax"):
        return tax_codes.SIMPLIFIED_TAX_INVOICE
    return TYPES.get(kind, tax_codes.OTHER)


def _subtotal(doc):
    """Cowork 的 subtotal 是税前基数：含税小票印的 Subtotal 就是含税合计时，按票面印的税额扣出税前（Cowork 原有口径 4e）。"""
    sub, vat, total = _money(doc.get("subtotal")), _money(doc.get("vat")), _money(doc.get("total"))
    if sub is not None and vat is not None and total is not None and sub == total and vat:
        return _s(total - vat)
    return _s(doc.get("subtotal"))


def invoice(read, doc, copy):
    kind = read.get("doc_kind")
    currency = _s(doc.get("currency")).strip()
    return {
        "document_type": _type(kind, doc), "is_not_invoice": read.get("entry") != "enter" and kind not in POLICY_KINDS,
        "is_copy_or_duplicate": copy, "document_flag": "withholding_certificate" if kind == "withholding_certificate" else "",
        "invoice_number": doc.get("invoice_number"), "date": doc.get("date"), "date_raw": _s(doc.get("date_raw")),
        "seller_name": _s(doc.get("seller_name")), "seller_tax": _s(doc.get("seller_tax")), "seller_addr": _s(doc.get("seller_address")),
        "seller_branch": _s(doc.get("seller_branch")), "buyer_name": _s(doc.get("buyer_name")), "buyer_tax": _s(doc.get("buyer_tax")),
        "buyer_addr": _s(doc.get("buyer_address")), "buyer_branch": _s(doc.get("buyer_branch")), "subtotal": _subtotal(doc),
        "vat": _s(doc.get("vat")), "wht_rate": _s(doc.get("wht_rate")), "wht_amount": _s(doc.get("wht_amount")),
        "discount": _s(doc.get("discount")), "total_amount": doc.get("total"), "payment_received": _s(doc.get("payment_received")),
        "cash_amount": _s(doc.get("cash_amount")), "change_amount": _s(doc.get("change_amount")),
        "payment_method": _s(doc.get("payment_method")), "currency": "" if currency.upper() in BAHT else currency,
        "items": [{"name": _s(line.get("description")), "qty": _s(line.get("qty")), "price": _s(line.get("unit_price")),
                   "subtotal": _s(line.get("amount"))} for line in doc.get("lines") or []],
        "notes": _s(doc.get("notes")), "category": "", "additional_invoices": [], "source_refs": {},
    }


def to_pipeline(outcome, page_count, anchor_year):
    """标准格式 → PipelineResult（每个实体页一条）。"""
    read = outcome.get("read") or {}
    copy = read.get("copy_marking") == "copy"
    read_pages = min(page_count, client.claude_engine.MAX_PAGES)
    models = [s["model"] for s in outcome.get("stages") or []]
    chain = ["CLAUDE", *[m.upper() for m in models]]
    by_page = {}
    for doc in read.get("documents") or []:
        page = doc.get("page") if isinstance(doc.get("page"), int) and 1 <= doc["page"] <= read_pages else 1
        by_page.setdefault(page, []).append(invoice(read, doc, copy))
    pages = []
    for number in range(1, page_count + 1):
        found = by_page.get(number) or []
        if number > read_pages:
            body = invoice(read, {}, copy) | {"is_not_invoice": False}
            pages.append({"page_number": number, "invoice": body, "layer_chain": [*chain, UNREAD_STAGE],
                          "needs_manual_review": True, "confidence_band": "needs_review"})
            continue
        if found:
            first = found[0]
            first["additional_invoices"] = found[1:]
        else:
            # 没有单据的页：非票据文件的各页，或跨页单据的续页 —— 都按「非发票」留作证据。
            first = invoice(read, {}, copy) | {"is_not_invoice": True, "document_type": tax_codes.OTHER,
                                                  "notes": "claude: no document starts on this page"}
        pages.append({"page_number": number, "invoice": first, "layer_chain": chain, "layer2_model": models[0] if models else "",
                      "layer3_model": models[1] if len(models) > 1 else "",
                      "trigger_reasons": list(outcome.get("why") or [])})
    body = {"pages": pages, "page_count": page_count, "elapsed_ms": int((outcome.get("seconds") or 0) * 1000),
            "engine": ENGINE, "estimated_cost_thb": round((outcome.get("usd") or 0) * 35, 4)}
    return PipelineResult.model_validate(body, context={"anchor_year": anchor_year})


def evidence(outcome):
    """与谷歌路线的 ``provider_evidence`` 同一形状：每次模型调用一条（不含原始回复正文）。"""
    rows = []
    for attempt, stage in enumerate(outcome.get("stages") or []):
        usage = stage.get("usage") or {}
        rows.append({"checkpoint_key": hashlib.sha256(json.dumps([stage.get("model"), attempt, outcome.get("why")],
                                                                  sort_keys=True).encode()).hexdigest(),
                     "stage": "claude_read" if attempt == 0 else "claude_review", "page": None, "attempt": attempt,
                     "model": stage.get("model"), "raw_sha256": "", "input_tokens": usage.get("input_tokens", 0),
                     "output_tokens": usage.get("output_tokens", 0), "finish_reason": stage.get("stop_reason"),
                     "usd": stage.get("usd"), "where": outcome.get("where")})
    return rows


def recognize(content, filename, page_count, anchor_year):
    """返回 ``(pipeline_json, evidence)``（与 ``forward.recognize`` 同形）。读不出时抛 ``Deferred`` 交工作单重试。"""
    from pearnly_core.execution.errors import Deferred

    media_type = mimetypes.guess_type(filename or "")[0] or ""
    if not media_type.startswith("image/") and media_type != "application/pdf":
        from pearnly_core import ocr
        media_type = ocr.detect_image_mime(content)
    started = time.monotonic()
    outcome = client.read("voucher", [(content, media_type)])
    if not outcome.get("read"):
        raise Deferred("claude_no_result", delay=30)
    outcome.setdefault("seconds", round(time.monotonic() - started, 2))
    return to_pipeline(outcome, page_count, anchor_year).model_dump_json(), evidence(outcome)


def recognize_table(document, anchor_year):
    """表格文件（Excel / Word，``table_inputs`` 解出的逻辑页）：每个逻辑页的文字交 Claude（与谷歌路线 ``run_table`` 同样按页读），
    每页的结果放回同一页号。返回 ``(pipeline_json, evidence)``。"""
    from pearnly_core.execution.errors import Deferred
    from pearnly_cowork.ocr.engine.runner import page_text

    pages, rows, usd, seconds = [], [], 0.0, 0.0
    for logical in document.pages:
        outcome = client.read("voucher", [], text=page_text(logical))
        if not outcome.get("read"):
            raise Deferred("claude_no_result", delay=30)
        page = to_pipeline(outcome, 1, anchor_year).pages[0]
        page.page_number = logical.page_number
        pages.append(page)
        rows += evidence(outcome)
        usd += outcome.get("usd") or 0
        seconds += outcome.get("seconds") or 0
    body = {"pages": [page.model_dump(mode="json") for page in pages], "page_count": document.page_count,
            "elapsed_ms": int(seconds * 1000), "engine": ENGINE, "estimated_cost_thb": round(usd * 35, 4)}
    return PipelineResult.model_validate(body, context={"anchor_year": anchor_year}).model_dump_json(), rows
