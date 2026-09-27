"""Sequential invoice/auto economy+Vertex orchestration, free of IO and wall clocks.

Infrastructure exceptions propagate. Completed JSON/schema failures alone may
consume content retry budget. Definite ProviderFailure follows explicit source
fallback rules; authentication and uncertain outcomes never do.

图片页按 ADR-013 逐页读（2026-09-27，与 ERP 同一套先后）：同一模型直读 → 没过金额 / 合理性检查就带提示复核
（`RC`）→ 仍不过才调 Vision，把原图和 OCR 原文一起再读（`L1` + `L2`）→ 还有问题标红交人工。3.8 视觉复核（`L3`）
只在站点显式打开时才跑。只有没过的那一页走后面几步，不再因为一页没过把整份文件重读一遍。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from typing import Callable

from pydantic import ValidationError

from pearnly_cowork.ocr.schema import Page, PipelinePageResult, PipelineResult, validate_invoice
from pearnly_cowork.ocr.validation import (
    DISCOUNT_INFERRED_PREFIX, aggregate_page_confidence, bucket_confidence,
    check_amount_math, count_invoice_no_candidates, credit_note_review_reason,
    evaluate_sanity, evaluate_soft_flags, evaluate_triggers, field_confidences,
    infer_missing_discount, validate_invoice_date, validate_invoice_sources,
    vat_ratio_mismatch,
)
from pearnly_cowork.ocr.money import normalize_money
from pearnly_cowork.ocr.validation.accounting import infer_withholding, normalize_amounts, repair_from_transcript
from pearnly_cowork.ocr.engine.contracts import (
    CallEvidence, EngineConfig, EngineResult, ModelCall, ModelRequest,
    PageSource, ProviderFailure, VisionCall,
)
from pearnly_cowork.ocr.engine.errors import ContentError, EngineError
from pearnly_cowork.ocr.engine.parsing import _parse_json
from pearnly_cowork.ocr.engine.prompts import (
    L2_RETRY_HINT, L2_SYSTEM, L2_USER_PREFIX,
    IMAGE_SYSTEM, L3_RETRY_HINT, L3_SYSTEM, OCR_TRANSCRIPT_NOTE, RESCUE_SYSTEM, build_l3_user_prompt,
    build_recheck_prompt,
)

RESCUE_FIELDS = ("subtotal", "vat", "discount", "total_amount")
# 带原文再读用 LOW 思考档（ERP `recognition.policy.RECHECK_THINKING`），输出上限同 ERP。复核不开思考：
# 实测 LOW 一次平均多写约 1,800 个思考 token（约 ฿0.09），比直读本身还贵。
OCR_READ_THINKING = "LOW"
OCR_READ_TOKENS = 8192
# 复核读数里空着、第一次读到了的字段沿用第一次（实测复核把金额改对的同时丢了买方）。
KEEP_FROM_FIRST = frozenset({"additional_invoices", "source_refs", "is_not_invoice", "is_copy_or_duplicate"})
IMAGE_READS = ("direct", "recheck", "ocr_read")


def is_auth_failure(failure: ProviderFailure) -> bool:
    return (failure.code == "ocr.provider_auth" or failure.http_status in (401, 403)
            or failure.provider_code in (7, 16))


def sniff_direct_mime(data: bytes) -> str:
    """Preserve direct_read's actual four-signature/default-PNG contract."""
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/png"


def page_text(page: Page) -> str:
    if page.table_rows is not None and page.table_headers is not None:
        lines = [" | ".join(page.table_headers)]
        for row in page.table_rows:
            lines.append(" | ".join(str(row.get(h, "")) for h in page.table_headers))
        return lines[0] + "\n" + "\n".join(lines[1:])
    return page.full_text


def apply_rescue(invoice, rescued: dict, *, tolerance=Decimal("0.5")):
    """Source rescue's four-field patch; Decimal validation is shared with gates."""
    values = {k: rescued.get(k) for k in RESCUE_FIELDS if rescued.get(k) not in (None, "")}
    if not values:
        return None
    # Preserve schema string fields: malformed rescue values cannot bypass validation.
    if any(not isinstance(value, str) for value in values.values()):
        return None
    patched = invoice.model_copy(deep=True, update=values)
    if check_amount_math(patched, tolerance=tolerance):
        return None
    if vat_ratio_mismatch(normalize_money(patched.subtotal), normalize_money(patched.vat),
                          normalize_money(patched.discount)):
        return None
    return patched


def rescue_note(before, after) -> str:
    diffs = []
    for field in RESCUE_FIELDS:
        old = str(getattr(before, field, "") or "")
        new = str(getattr(after, field, "") or "")
        if old != new:
            diffs.append(f"{field} {old or '(空)'}→{new or '(空)'}")
    return ("totals_rescued: L3 视觉复读失败,金额由第二个模型窄口径重读后整体替换("
            + " / ".join(diffs) + ")")


class _Run:
    def __init__(self, source, model_call, vision_call, config, anchor_year, today,
                 fewshot_selector, pattern_memory):
        self.source, self.model_call, self.vision_call = source, model_call, vision_call
        self.config, self.anchor_year, self.today = config, anchor_year, today
        self.fewshot_selector, self.pattern_memory = fewshot_selector, pattern_memory
        self.calls: list[CallEvidence] = []
        self.escalations = 0

    def parse_invoice(self, data, stage, page):
        try:
            return validate_invoice(data, anchor_year=self.anchor_year)
        except ValidationError as exc:
            raise ContentError("ocr.invoice_schema_invalid", stage=stage, page_number=page,
                               detail=str(exc)) from exc

    def model_json(self, *, stage, page, model, prompt, images=(), tokens=8192,
                   timeout=60, retries=None, thinking=""):
        retries = self.config.json_retries if retries is None else retries
        first = len(self.calls)
        truncated = False
        for attempt in range(retries + 1):
            retry_hint = ""
            if attempt and stage == "layer2":
                retry_hint = L2_RETRY_HINT
            elif attempt and stage == "layer3":
                retry_hint = L3_RETRY_HINT + "\n\nPrevious parse error: parse"
            request = ModelRequest(stage, page, attempt, model, prompt + retry_hint,
                                   images, tokens, timeout, thinking=thinking)
            # This call is deliberately outside every content-error try/except.
            response = self.model_call(request)
            self.calls.append(CallEvidence(
                stage, page, attempt, response.model, deepcopy(response.raw), response.text,
                response.input_tokens, response.output_tokens, response.elapsed_ms,
                response.finish_reason, response.checkpoint_key,
            ))
            if "MAX_TOKENS" in response.finish_reason and stage in IMAGE_READS:
                # 看图读写满了输出上限：同一张图重读多半照样写满（语料实测一次 16k token、90 秒），交下一步。
                truncated = True
                break
            try:
                if not response.text or "MAX_TOKENS" in response.finish_reason:
                    raise json.JSONDecodeError("empty/truncated", response.text, 0)
                data = _parse_json(response.text)
            except json.JSONDecodeError:
                continue
            return data, self.calls[first:]
        raise ContentError("ocr.response_json_invalid", stage=stage, page_number=page,
                           detail="output hit the token limit" if truncated
                           else f"no valid JSON after {retries + 1} attempts (parse)")

    def read_image(self, stage, page, frame, prompt, *, thinking="", retries=None):
        data, calls = self.model_json(
            stage=stage, page=page, model=self.config.direct_model, prompt=prompt,
            images=((frame.image_bytes, sniff_direct_mime(frame.image_bytes)),),
            timeout=self.config.direct_timeout_s, retries=retries, thinking=thinking,
        )
        return self.parse_invoice(data, stage, page), calls

    @staticmethod
    def fill_missing(candidate, first):
        update = {name: deepcopy(getattr(first, name)) for name in type(candidate).model_fields
                  if name not in KEEP_FROM_FIRST and getattr(candidate, name) in (None, "", [])
                  and getattr(first, name) not in (None, "", [])}
        return candidate.model_copy(update=update) if update else candidate

    def gate(self, invoice):
        """直读闸：返回 (没过的原因, 已有提示)；原因为空就是过了。不是发票的页不拦。"""
        warnings = []
        if invoice.is_not_invoice:
            return "", warnings
        normalize_amounts(invoice)
        repaired = infer_missing_discount(invoice)
        if repaired:
            warnings.append(repaired)
        withheld = infer_withholding(invoice)  # 共用会计规则（与 ERP 同一套）
        if withheld:
            warnings.append(withheld)
        issue = ("total_amount missing" if not str(invoice.total_amount or "").strip()
                 else check_amount_math(invoice, tolerance=self.config.amount_tolerance))
        return issue or "; ".join(evaluate_sanity(invoice)), warnings

    def image_page(self, page):
        """一页图片（ADR-013）：直读 → 带提示复核 → Vision 原文 + 原图再读；前一步过了闸就不再往下走。"""
        frame = self.source.frame(page)
        first = len(self.calls)
        best = None
        try:
            invoice, _ = self.read_image("direct", page, frame, IMAGE_SYSTEM)
            issue, warnings = self.gate(invoice)
            if not issue:
                return self.direct_result(page, invoice, warnings, self.calls[first:], ["ID"])
            best, evidence = invoice, {"code": "ocr.direct_gate_failed", "detail": issue}
        except ProviderFailure as exc:
            if is_auth_failure(exc):
                raise
            evidence = {"code": exc.code, "detail": None}
        except ContentError as exc:
            evidence = {"code": exc.code, "detail": exc.detail}
        evidence = {"page_number": page, **evidence}
        chain = ["ID"]
        if best is not None:  # 直读读出了东西、只是没过闸：先让同一模型带着没过的原因看原图复核
            chain.append("RC")
            try:
                invoice, _ = self.read_image("recheck", page, frame, build_recheck_prompt(best, evidence["detail"]),
                                             retries=0)
                invoice = self.fill_missing(invoice, best)
                issue, warnings = self.gate(invoice)
                if not issue:
                    result = self.direct_result(page, invoice, warnings, self.calls[first:], chain)
                    result.extraction_audit["direct_fallback"] = evidence
                    return result
            except ProviderFailure as exc:
                if is_auth_failure(exc):
                    raise
            except ContentError:
                pass
        result = self.pipeline_page(page, None, frame=frame, prefix=chain, first=first, fallback=best)
        result.extraction_audit["direct_fallback"] = evidence
        return result

    def direct_result(self, page, invoice, warnings, calls, chain):
        if not invoice.is_not_invoice and not str(invoice.invoice_number or "").strip():
            warnings.append("direct_read: invoice_number missing")
        force = any(w.startswith(DISCOUNT_INFERRED_PREFIX) for w in warnings)
        credit = credit_note_review_reason(invoice)
        if credit:
            warnings.append(credit)
            force = True
        date_issues = validate_invoice_date(invoice, today=self.today)
        if date_issues:
            warnings.extend(date_issues)
            force = True
        confidence = round(max(0.0, .95 - .05 * len(warnings)), 4)
        band = self.bucket(confidence, force)
        return PipelinePageResult(
            page_number=page, invoice=invoice, document_type=self.config.document_type,
            layer_chain=chain, layer2_model=calls[-1].model,
            layer2_input_tokens=sum(c.input_tokens for c in calls),
            layer2_output_tokens=sum(c.output_tokens for c in calls),
            layer2_ms=sum(c.elapsed_ms for c in calls), total_ms=sum(c.elapsed_ms for c in calls),
            needs_manual_review=band == "needs_review", confidence_band=band,
            final_confidence=confidence, validation_warnings=warnings,
        )

    def bucket(self, confidence, force):
        return bucket_confidence(confidence, force, auto_threshold=self.config.auto_threshold,
                                 review_threshold=self.config.review_threshold)

    def pipeline_page(self, page, text_page, *, frame=None, prefix=None, first=None, fallback=None):
        """Vision（或 PDF 文字层）+ 模型。`prefix` 非空＝图片页直读 / 复核没过之后（ADR-013）：模型同时看原图和
        Vision 原文（`ocr_read`），读不出时保留前面没过闸的那次读数并标红；没有 `prefix` 是原来的纯文字路线。"""
        first = len(self.calls) if first is None else first
        image_read = prefix is not None
        if text_page is None:
            frame = frame or self.source.frame(page)
            response = self.vision_call(frame, stage="layer1", page_number=page)
            l1 = response.page.model_copy(deep=True)
            if l1.page_number != page:
                raise EngineError("ocr.page_association_invalid", stage="layer1", page_number=page)
            self.calls.append(CallEvidence("layer1", page, 0, "vision", deepcopy(response.raw),
                                           elapsed_ms=response.elapsed_ms,
                                           checkpoint_key=response.checkpoint_key))
            chain = [*(prefix or ()), "L1", "L2"]
            l1_ms = response.elapsed_ms
        else:
            l1 = text_page.model_copy(deep=True)
            chain, l1_ms = ["text", "L2"], 0
        text = page_text(l1).strip()
        l2_calls = []
        unread = None
        if image_read:
            fewshot = self.fewshot_selector(text) if self.fewshot_selector and text else ""
            l2_start = len(self.calls)
            try:
                data, _ = self.model_json(
                    stage="ocr_read", page=page, model=self.config.layer2_model,
                    prompt=IMAGE_SYSTEM + "\n\n" + fewshot + OCR_TRANSCRIPT_NOTE + text[:30000],
                    images=((frame.image_bytes, sniff_direct_mime(frame.image_bytes)),),
                    tokens=OCR_READ_TOKENS, timeout=self.config.layer2_timeout_s, retries=0,
                    thinking=OCR_READ_THINKING,
                )
                l2 = self.fill_missing(self.parse_invoice(data, "ocr_read", page), fallback) if fallback else \
                    self.parse_invoice(data, "ocr_read", page)
            except ContentError as exc:
                if fallback is None:
                    raise
                l2, unread = fallback, f"ocr_read: {exc.code}"
                chain[-1] = "L2_unread"
            l2_calls = self.calls[l2_start:]
        elif len(text) < 20:
            l2 = self.parse_invoice({"is_not_invoice": True}, "layer2", page)
        else:
            text = text[:30000]
            fewshot = self.fewshot_selector(text) if self.fewshot_selector else ""
            data, l2_calls = self.model_json(
                stage="layer2", page=page, model=self.config.layer2_model,
                prompt=L2_SYSTEM + "\n\n" + fewshot + L2_USER_PREFIX + text,
                tokens=16384, timeout=self.config.layer2_timeout_s,
            )
            l2 = self.parse_invoice(data, "layer2", page)
        if not l2.is_not_invoice:
            normalize_amounts(l2)
        warnings = validate_invoice_sources(l2, today=self.today)
        repaired = infer_missing_discount(l2) if not l2.is_not_invoice else None
        if repaired:
            warnings.append(repaired)
        if not l2.is_not_invoice:  # 共用会计规则：预扣税、用 Vision 原文核对税号与读错一位的金额
            withheld = infer_withholding(l2)
            warnings.extend([withheld] if withheld else [])
            warnings.extend(repair_from_transcript(l2, l1.full_text, tolerance=self.config.amount_tolerance))
        anomaly = (self.pattern_memory.check_anomaly(l2.seller_tax, l2.invoice_number)
                   if self.pattern_memory else None)
        triggers = evaluate_triggers(l1, l2, pattern_anomaly=anomaly,
                                     amount_tolerance=self.config.amount_tolerance)
        soft_flags = evaluate_soft_flags(l1, l2, confidence_threshold=self.config.confidence_threshold)
        invoice, review, error = l2, False, None
        if unread:  # 带原文再读也读不出：保留直读 / 复核那次没过闸的读数，交人工
            warnings.append(unread)
            review = True
        if triggers and not self.config.enable_layer3 and self.source is not None:
            # 3.8 视觉复核默认关（ADR-013）：原本要升级的问题直接标红交人工。表格路线（source 为空）本来就不升级，照旧只降置信度。
            review = True
        l3_calls = []
        if triggers and self.config.enable_layer3:
            allowed = (self.config.max_escalations is None or
                       self.escalations < self.config.max_escalations)
            if not allowed:
                chain.append("L3_skipped_budget")
                review, error = True, "L3 skipped: run fallback budget exhausted"
            else:
                self.escalations += 1
                frame = frame or self.source.frame(page)
                l3_start = len(self.calls)
                try:
                    data, l3_calls = self.model_json(
                        stage="layer3", page=page, model=self.config.layer3_model,
                        prompt=L3_SYSTEM + "\n\n" + build_l3_user_prompt(l1.full_text, l2, triggers),
                        images=((frame.image_bytes, frame.mime_type),),
                        timeout=self.config.layer3_timeout_s,
                    )
                    invoice = self.parse_invoice(data, "layer3", page)
                    chain.append("L3")
                except ContentError as exc:
                    if not self.config.fallback_to_layer2:
                        raise
                    review, error = True, f"layer3: {exc.code} ({exc.detail})"
                    try:
                        rescued, _ = self.model_json(
                            stage="totals_rescue", page=page, model=self.config.layer3_model,
                            prompt=RESCUE_SYSTEM,
                            images=((frame.image_bytes, sniff_direct_mime(frame.image_bytes)),),
                            tokens=512, timeout=self.config.rescue_timeout_s, retries=0,
                        )
                    except ProviderFailure as failure:
                        if is_auth_failure(failure):
                            raise
                        rescued = {}
                    except ContentError:
                        rescued = {}
                    patched = apply_rescue(l2, rescued, tolerance=self.config.amount_tolerance)
                    if patched is not None:
                        invoice = patched
                        warnings.append(rescue_note(l2, patched))
                        chain.append("L3_totals_rescue")
                    else:
                        chain.append("L3_failed")
                except ProviderFailure as failure:
                    if is_auth_failure(failure) or not self.config.fallback_to_layer2:
                        raise
                    review = True
                    error = f"L3 provider refusal: {failure.code}"
                    if failure.http_status == 429 or failure.provider_code == 8:
                        chain.append("L3_quota")
                    elif failure.http_status in (408, 500, 502, 503, 504) or failure.provider_code in (4, 14):
                        chain.append("L3_transient")
                    else:
                        chain.append("L3_failed")
                l3_calls = self.calls[l3_start:]
        if not invoice.is_not_invoice and invoice.invoice_number:
            extracted_n = 1 + len(invoice.additional_invoices or [])
            candidate_n = count_invoice_no_candidates(l1.full_text, invoice.invoice_number)
            if candidate_n > extracted_n:
                warnings.append(
                    f"possible_missed_invoice: page shows {candidate_n} invoice-number candidates "
                    f"matching the pattern of {invoice.invoice_number!r} but only {extracted_n} "
                    "invoice(s) extracted after visual re-read — manual review needed"
                )
                review = True
        if invoice is not l2 and not invoice.is_not_invoice:
            repaired = infer_missing_discount(invoice)
            if repaired:
                warnings.append(repaired)
        if not invoice.is_not_invoice:
            sanity = evaluate_sanity(invoice)
            warnings.extend(sanity)
            review = review or bool(sanity)
            credit = credit_note_review_reason(invoice)
            if credit:
                warnings.append(credit)
                review = True
        if any(w.startswith(DISCOUNT_INFERRED_PREFIX) for w in warnings):
            review = True
        if self.pattern_memory and not invoice.is_not_invoice:
            self.pattern_memory.record(invoice.seller_tax, invoice.invoice_number)
        confidence = aggregate_page_confidence(l1, triggers, review, soft_flags)
        band = self.bucket(confidence, review)
        return PipelinePageResult(
            page_number=page, invoice=invoice, document_type=self.config.document_type,
            layer_chain=chain, trigger_reasons=triggers + soft_flags,
            layer1_avg_confidence=l1.avg_confidence, layer1_ms=l1_ms,
            layer2_model=l2_calls[-1].model if l2_calls else self.config.layer2_model,
            layer2_input_tokens=sum(c.input_tokens for c in l2_calls),
            layer2_output_tokens=sum(c.output_tokens for c in l2_calls),
            layer2_ms=sum(c.elapsed_ms for c in l2_calls),
            layer3_model=l3_calls[-1].model if l3_calls else "",
            layer3_input_tokens=sum(c.input_tokens for c in l3_calls),
            layer3_output_tokens=sum(c.output_tokens for c in l3_calls),
            layer3_ms=sum(c.elapsed_ms for c in l3_calls),
            total_ms=sum(c.elapsed_ms for c in self.calls[first:]),
            needs_manual_review=band == "needs_review", confidence_band=band,
            final_confidence=confidence, field_confidence=field_confidences(l1, invoice),
            validation_warnings=warnings, error=error,
        )


def run_invoice(source: PageSource, model_call: ModelCall, vision_call: VisionCall, *,
                config: EngineConfig, anchor_year: int, today: date,
                fewshot_selector: Callable[[str], str] | None = None,
                pattern_memory=None) -> EngineResult:
    """Replayable whole-file run. The caller pins config/date/fewshots and owns checkpoints.

    Calls are billing evidence once per actual checkpoint, regardless of sibling
    invoice flattening or an abandoned direct result. No exception from a callback
    outside the explicit ProviderFailure contract authorizes a paid fallback.
    """
    if (config.document_type not in ("invoice", "auto") or config.mode not in ("economy", "enterprise")
            or config.provider != "vertex" or config.double_read or config.image_first):
        raise EngineError("ocr.engine_mode_not_supported")
    if (isinstance(source.page_count, bool) or not isinstance(source.page_count, int)
            or not 1 <= source.page_count <= 50):
        raise EngineError("ocr.page_count_invalid")
    if (not all((config.direct_model, config.layer2_model, config.layer3_model))
            or config.json_retries not in (0, 1) or config.max_direct_pages < 0
            or config.max_escalations is not None and config.max_escalations < 0):
        raise EngineError("ocr.engine_config_invalid")
    # Validate deterministic date context before the first paid callback.
    validate_invoice({}, anchor_year=anchor_year)
    if not isinstance(today, date):
        raise EngineError("ocr.date_context_invalid")
    text_pages = source.text_pages
    if text_pages is not None:
        if len(text_pages) != source.page_count:
            text_pages = None  # source drops the whole-file override on count mismatch
        elif any(p.page_number != i for i, p in enumerate(text_pages, 1)):
            raise EngineError("ocr.page_association_invalid")
    run = _Run(source, model_call, vision_call, config, anchor_year, today,
               fewshot_selector, pattern_memory)
    if text_pages is None and config.direct_enabled and source.page_count <= config.max_direct_pages:
        pages = [run.image_page(i) for i in range(1, source.page_count + 1)]
        engine = "image_direct" if all(p.layer_chain == ["ID"] for p in pages) else "image_checked"
    else:
        engine = "pipeline_v1"
        pages = [run.pipeline_page(i, text_pages[i - 1] if text_pages is not None else None)
                 for i in range(1, source.page_count + 1)]
    # Adapters must supply a stable key. Empty keys (pure callers) represent unique calls.
    seen, calls = set(), []
    for call in run.calls:
        if call.checkpoint_key and call.checkpoint_key in seen:
            continue
        if call.checkpoint_key:
            seen.add(call.checkpoint_key)
        calls.append(call)
    return EngineResult(PipelineResult(pages=pages, page_count=source.page_count,
                                       elapsed_ms=sum(c.elapsed_ms for c in calls), engine=engine),
                        tuple(calls))


def run_text_pages(pages: list[Page] | tuple[Page, ...], model_call: ModelCall, *,
                   config: EngineConfig, anchor_year: int, today: date,
                   fewshot_selector: Callable[[str], str] | None = None) -> EngineResult:
    """Run already-decoded logical units through the shared invoice L2 decisions.

    This is the table path, not a physical PDF page source. It never renders or
    calls Vision/direct/L3, and does not borrow the PDF's 50-page limit. The
    table adapter validates document limits before calling this pure boundary.
    """
    from dataclasses import replace

    if (config.document_type not in ("invoice", "auto") or config.mode not in ("economy", "enterprise")
            or config.provider != "vertex" or config.double_read or config.image_first):
        raise EngineError("ocr.engine_mode_not_supported")
    if not config.layer2_model or config.json_retries not in (0, 1):
        raise EngineError("ocr.engine_config_invalid")
    if not pages or any(not isinstance(p, Page) or p.page_number != i
                        for i, p in enumerate(pages, 1)):
        raise EngineError("ocr.page_association_invalid")
    validate_invoice({}, anchor_year=anchor_year)
    if not isinstance(today, date):
        raise EngineError("ocr.date_context_invalid")
    # Copy the complete input before the first callback, including later units.
    frozen_pages = tuple(p.model_copy(deep=True) for p in pages)
    run = _Run(None, model_call, None,
               replace(config, direct_enabled=False, enable_layer3=False),
               anchor_year, today, fewshot_selector, None)
    results = [run.pipeline_page(p.page_number, p) for p in frozen_pages]
    seen, calls = set(), []
    for call in run.calls:
        if call.checkpoint_key and call.checkpoint_key in seen:
            continue
        if call.checkpoint_key:
            seen.add(call.checkpoint_key)
        calls.append(call)
    return EngineResult(PipelineResult(pages=results, page_count=len(results),
                                       elapsed_ms=sum(c.elapsed_ms for c in calls),
                                       engine="pipeline_v1_table"), tuple(calls))
