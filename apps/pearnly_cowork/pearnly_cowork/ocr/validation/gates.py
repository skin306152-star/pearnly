"""Pure invoice confidence/escalation decisions from ec2e5c957e6b.

Thresholds and precomputed pattern evidence are explicit arguments, not env,
file-backed pattern memory, or permission to run a paid reread.
"""
from __future__ import annotations
import re
from decimal import Decimal
from typing import Dict, List, Optional
from pearnly_cowork.ocr.schema.evidence import Page
from pearnly_cowork.ocr.schema.invoice import ThaiInvoice
from pearnly_cowork.ocr.validation.confidence import check_field_in_l1_text, find_field_min_word_conf
from pearnly_cowork.ocr.validation.math import check_amount_math

CRITICAL_FIELDS = ("invoice_number", "total_amount", "seller_tax")
_FIELD_CONF_ATTRS = CRITICAL_FIELDS

def aggregate_page_confidence(l1_page: Page, triggers: List[str], needs_manual_review: bool, soft_flags: Optional[List[str]]=None) -> float:
    """Return a single 0..1 confidence for the page.

    - Start at L1 avg_confidence (1.0 for text/table paths).
    - Subtract penalty per trigger AND per soft_flag (low word-conf that didn't
      escalate to L3 still lowers confidence into yellow_confirm so the user
      eyeballs it — that's the whole point of moving Rule 4 here, doc 09 §3.1).
    - Floor at 0.0, cap at 1.0.
    """
    base = float(l1_page.avg_confidence or 0.0)
    if base <= 0.0:
        base = 0.85
    penalty = 0.05 * (len(triggers) + len(soft_flags or []))
    if needs_manual_review:
        penalty += 0.1
    final = max(0.0, min(1.0, base - penalty))
    return final

def bucket_confidence(conf: float, needs_review: bool, *, auto_threshold=0.98, review_threshold=0.9) -> str:
    if needs_review:
        return 'needs_review'
    if conf >= auto_threshold:
        return 'auto'
    if conf >= review_threshold:
        return 'yellow_confirm'
    return 'needs_review'

def count_invoice_no_candidates(full_text: str, sample_invoice_no: str) -> int:
    """P0 修 (2026-05-26) · 数 OCR 文本里跟 sample 发票号"同族"的不同候选号个数。

    从已成功提取的 invoice_number 推导出结构正则(字母段照搬 · 数字段→\\d+),
    再在整页 OCR 文本里 findall · 去重计数。用于"同页多票防静默漏":候选数 >
    实际提取数 → 大概率漏了一张堆叠发票。

    例:sample='IV69/00179' → 正则 'IV\\d+/\\d+' · 页文本含 IV69/00179 + IV69/00189
        → 返回 2。
    保守:推不出正则 / 正则错 → 返回 0(不误报)。
    """
    if not full_text or not sample_invoice_no:
        return 0
    parts = re.split('(\\d+)', str(sample_invoice_no).strip())
    segs: List[str] = []
    for seg in parts:
        if not seg:
            continue
        segs.append('\\d+' if seg.isdigit() else re.escape(seg))
    if not segs or '\\d+' not in segs:
        return 0
    pattern = ''.join(segs)
    try:
        found = re.findall(pattern, full_text)
    except re.error:
        return 0
    return len({f.strip() for f in found if f.strip()})

def evaluate_triggers(page: Page, invoice: ThaiInvoice, *, pattern_anomaly=None, amount_tolerance=Decimal('0.5')) -> List[str]:
    """Return list of L3-escalation reasons; empty list = layer 3 not needed.

    L3 is the slow visual re-read arm — only fire it when a visual pass can
    actually fix something. Low per-field word confidence on a value that DOES
    appear in the L1 text is NOT escalated here (thermal/small-print receipts
    read low-conf on nearly everything); that path goes to _evaluate_soft_flags
    → yellow_confirm instead (doc 09 §3.1, speed fix).

    Escalation rules:
    1. invoice_number missing — only for FULL tax invoices (a legal เลขที่ is
       mandatory only there). Simplified / receipt / non-invoice keep their
       R#/REF as-is and never escalate for a "missing" legal number (§3.2).
       total_amount missing escalates for any doc (an expense with no total is
       unusable).
    2. Amount math fail (subtotal + vat != total).
    3. seller_tax format invalid — only for FULL tax invoices (input-VAT claim
       needs a real 13-digit seller id; a card SLIP's TID is not a tax id).
    5. L1 containment — extracted value absent from L1 full_text (layer 2 likely
       hallucinated it → a visual re-read is warranted).
    6. Invoice pattern anomaly vs previously seen patterns for this seller_tax.
    7. Same-page multi-invoice candidate count > extracted.
    """
    if invoice.is_not_invoice:
        return []
    is_full_tax_invoice = getattr(invoice, 'document_type', 'tax_invoice') == 'tax_invoice'
    triggers: List[str] = []
    if not invoice.invoice_number and is_full_tax_invoice:
        triggers.append('invoice_number missing')
    if not invoice.total_amount:
        triggers.append('total_amount missing')
    math_reason = check_amount_math(invoice, tolerance=amount_tolerance)
    if math_reason:
        triggers.append(math_reason)
    for field_name in CRITICAL_FIELDS:
        value = getattr(invoice, field_name, None)
        if not value:
            continue
        if not check_field_in_l1_text(page, value):
            triggers.append(f'{field_name}={value!r} not found in L1 OCR text (possibly hallucinated by layer 2)')
    if pattern_anomaly and invoice.invoice_number:
        triggers.append(pattern_anomaly)
    main_amount_reliable = bool(invoice.total_amount) and (not math_reason)
    if invoice.invoice_number and (not main_amount_reliable):
        extracted_n = 1 + len(invoice.additional_invoices or [])
        candidate_n = count_invoice_no_candidates(page.full_text, invoice.invoice_number)
        if candidate_n > extracted_n:
            triggers.append(f'possible_missed_invoice(L2): {candidate_n} invoice-number candidates vs {extracted_n} extracted — escalate to visual re-read')
    return triggers

def evaluate_soft_flags(page: Page, invoice: ThaiInvoice, *, confidence_threshold=0.85) -> List[str]:
    """Low word-level confidence on a value that DOES appear in the L1 text.

    Not a hallucination (Rule 5 escalates those) and not worth a slow L3 visual
    re-read — just lower page confidence to yellow_confirm so the user eyeballs
    the field. Thermal / small-print receipts read low-conf on nearly every
    field; escalating that to L3 was the main speed killer (doc 09 §3.1).
    Returns reasons that feed the confidence penalty but never gate L3.
    """
    if invoice.is_not_invoice:
        return []
    flags: List[str] = []
    for field_name in CRITICAL_FIELDS:
        value = getattr(invoice, field_name, None)
        if not value:
            continue
        min_conf = find_field_min_word_conf(page, value)
        if min_conf is None or min_conf >= confidence_threshold:
            continue
        if check_field_in_l1_text(page, value):
            flags.append(f'{field_name}={value!r} low word conf {min_conf:.3f} < {confidence_threshold} (in L1 text → confirm, no L3)')
    date_val = invoice.date_raw or invoice.date
    if date_val:
        min_conf = find_field_min_word_conf(page, date_val)
        if min_conf is not None and min_conf < confidence_threshold:
            flags.append(f'date={date_val!r} low word conf {min_conf:.3f} < {confidence_threshold} (confirm, no L3)')
    is_full_tax_invoice = getattr(invoice, 'document_type', 'tax_invoice') == 'tax_invoice'
    if is_full_tax_invoice and invoice.seller_tax:
        if not invoice.seller_tax.isdigit() or len(invoice.seller_tax) != 13:
            flags.append(f'seller_tax format invalid: {invoice.seller_tax!r} (expected 13 digits → confirm, no L3)')
    return flags

def field_confidences(l1_page: Page, invoice) -> Dict[str, float]:
    """关键字段词级最低置信(0-1),喂复核屏字段三态着色。不在 OCR 文本(幻觉/缺)→ 0.0;
    在全文但无词级匹配(短值)→ 省略(不误标低)。"""
    out: Dict[str, float] = {}
    for attr in _FIELD_CONF_ATTRS:
        val = str(getattr(invoice, attr, '') or '').strip()
        if not val:
            continue
        if not check_field_in_l1_text(l1_page, val):
            out[attr] = 0.0
            continue
        conf = find_field_min_word_conf(l1_page, val)
        if conf is not None:
            out[attr] = round(float(conf), 4)
    return out
