"""Pure whole-result preparation for native history.recognition.persist_batch.

Only validated, complete source-unit results belong here. The caller supplies
the independently verified physical-page or logical-section count and explicit
workspace/source channel. The persistence service checks its frozen manifest.
This module performs no permission decision, persistence, model call or charge.
"""
from collections import Counter
from copy import deepcopy

from pearnly_cowork.contracts import document_policy as policy_rules
from pearnly_cowork.contracts.history import normalize_source
from pearnly_cowork.contracts.push import (apply_batch_direction, apply_batch_payment,
    normalize_direction, normalize_payment, normalize_posting_kind)
from pearnly_cowork.ocr.grouping import group_invoices
from pearnly_cowork.ocr.pages import (
    aggregate_confidence_band, all_pages_not_invoice, assess_confidence, restore_total_before_withholding,
    flatten_pipeline_pages, history_pages, split_included_vat,
)
from pearnly_cowork.ocr.schema.results import PipelineResult

UNREAD_STAGE = "PAGE_UNREAD"  # 与 `ocr.engine.runner.UNREAD_STAGE` 同一标记（结果准备不反向依赖引擎）


class OcrResultError(ValueError):
    def __init__(self, code, **details):
        self.code = code
        self.details = details
        super().__init__(code)


_L2_REVIEW_FALLBACKS = frozenset({
    "L3_FAILED", "L3_QUOTA", "L3_TRANSIENT", "L3_SKIPPED_BUDGET", "L3_TOTALS_RESCUE", "IF_FAILED",
})


def _has_retained_result(invoice):
    # The source persistence cache calls number / amount / seller a usable
    # recognition. An explicit noninvoice classification is also a result.
    return bool(invoice.is_not_invoice or str(invoice.invoice_number or "").strip()
                or (invoice.total_amount is not None and str(invoice.total_amount).strip())
                or str(invoice.seller_name or "").strip()
                or any(not child.is_not_invoice and _has_retained_result(child)
                       for child in invoice.additional_invoices or []))


def _require_complete(result, expected_page_count):
    if not isinstance(result, PipelineResult):
        raise OcrResultError("ocr.result_unvalidated")
    if type(expected_page_count) is not int or expected_page_count < 1:
        raise OcrResultError("ocr.expected_pages_invalid")
    physical = [page.page_number for page in result.pages]
    counts = Counter(physical)
    expected = set(range(1, expected_page_count + 1))
    missing, unexpected = sorted(expected - set(physical)), sorted(set(physical) - expected)
    duplicates = sorted(number for number, count in counts.items() if count > 1)
    failed, review_pages = [], set()
    # 多页文件里读不出的页（`runner.unread_page`）：不算整份失败，挂到相邻单据上标红（见 `_attach_unread`）；
    # 全部页都读不出仍是失败（引擎那边已拦）。
    unread = {page.page_number for page in result.pages if UNREAD_STAGE in page.layer_chain}
    if unread and len(unread) == len(result.pages):
        failed.extend(sorted(unread))
    for page in result.pages:
        if page.page_number in unread:
            review_pages.add(page.page_number)
            continue
        chain = {str(stage).upper() for stage in page.layer_chain}
        hard_failure = any(stage == "FAILED" or (stage.endswith("_FAILED") and stage not in _L2_REVIEW_FALLBACKS)
                           for stage in chain)
        fallback = bool(chain & _L2_REVIEW_FALLBACKS)
        # A schema-valid empty object is still an unrecognized physical page.
        # Do not seal an empty History or charge it merely because L3 returned 200.
        pending = (result.engine == "roundtrip_workbook" and page.layer_chain == ["roundtrip_workbook"]
                   and page.needs_manual_review and not page.declared_direction
                   and bool(page.invoice.notes) and not page.invoice.additional_invoices)
        if (not _has_retained_result(page.invoice) and not pending) or hard_failure or ((page.error or fallback) and
                            ("L2" not in chain or not _has_retained_result(page.invoice))):
            failed.append(page.page_number)
        elif page.error or fallback or page.needs_manual_review or page.confidence_band == "needs_review":
            review_pages.add(page.page_number)
    if result.page_count != expected_page_count or missing or unexpected or duplicates or failed:
        raise OcrResultError("ocr.result_incomplete", expected_page_count=expected_page_count,
            reported_page_count=result.page_count, missing_pages=missing, unexpected_pages=unexpected,
            duplicate_pages=duplicates, failed_pages=sorted(failed))
    return review_pages


def _attach_unread(groups, unread, *, flag=True):
    """读不出的页挂到前一张单据上（从第 1 页起读不出的挂第一张），并在那张票上标红「有一页没读出」。

    与 ERP `recognition.policy.read_all` 同一做法：不进分组算法（空白页会被当成续页并进上一张，
    按金额分界时又进下一张，两种方向不一致），分完再挂。``flag=False``：只挂作证据、不标红（非发票页）。"""
    if not unread or not groups:
        return groups
    for page in sorted(unread, key=lambda item: item["page_index"]):
        before = [group for group in groups if min(group["page_indices"]) < page["page_index"]]
        group = max(before, key=lambda item: min(item["page_indices"])) if before else groups[0]
        group["source_pages"].append(deepcopy(page))
        group["page_indices"] = sorted([*group["page_indices"], page["page_index"]])
        if not flag:
            continue
        first = group["source_pages"][0]
        first["_validation_warnings"] = [*(first.get("_validation_warnings") or []),
                                         *(page.get("_validation_warnings") or [])]
        first["_needs_manual_review"] = True
        first["_confidence_band"] = "needs_review"
    return groups


def prepare_history_batch(result: PipelineResult, *, expected_page_count: int, workspace: str,
                          source: str, client=None, posting_kind=None, direction=None, payment=None,
                          reference_kind="physical_page", document_policy=None) -> dict:
    """Prepare groups for persist_batch; a result is never a billing permission.

    ``has_billable_result`` only distinguishes an invoice result from a complete
    all-not-invoice result. Balance, pricing, exemption, idempotency and charging
    after successful persistence remain the later authorized workflow's job.

    All groups use the explicitly supplied scope. Automatic company assignment,
    archive naming, accounting categories and duplicate DB lookup are not part
    of this pure slice. The persistence service rechecks every assigned scope.

    ``document_policy``：这个租户对转账凭条 / 预扣税凭证选过的「录入 / 不录入」
    （`contracts.document_policy`），由调用方读出传入。
    """
    source = normalize_source(source)
    if reference_kind not in {"physical_page", "logical_section"}:
        raise OcrResultError("ocr.reference_kind_invalid")
    references = {"reference_kind": reference_kind, "reference_count": expected_page_count,
                  "physical_page_count": expected_page_count if reference_kind == "physical_page" else None}
    if (not isinstance(workspace, str) or not workspace.strip()
            or (client is not None and (not isinstance(client, str) or not client.strip()))):
        raise OcrResultError("ocr.scope_required")
    review_pages = _require_complete(result, expected_page_count)
    if result.engine == "roundtrip_workbook":
        from pearnly_cowork.roundtrip.results import prepare_groups
        if (reference_kind != "logical_section" or any(p.layer_chain != ["roundtrip_workbook"]
                or p.invoice.additional_invoices for p in result.pages)):
            raise OcrResultError("ocr.result_unvalidated")
        groups = prepare_groups(result, workspace=workspace, client=client, source=source,
                                posting_kind=normalize_posting_kind(posting_kind))
        # 复核工作簿自己也声明一套字段，付款方式只补**没人定过**的那些页。
        for group in groups:
            for page in group.get("pages") or []:
                apply_batch_payment(page.get("fields"), normalize_payment(payment))
        return {"state": "review_required" if review_pages else "ready", "groups": groups,
                "raw_pages": flatten_pipeline_pages(result.pages), "invoice_count": len(groups),
                **references, "confidence": None, "confidence_band": aggregate_confidence_band(result.pages),
                "requires_review": bool(review_pages), "has_billable_result": bool(groups)}
    raw = flatten_pipeline_pages(result.pages)
    policy_rules.apply_to_pages(raw, policy_rules.normalize(document_policy))
    # A recoverable L3 / image-first failure keeps its validated L2 evidence.
    # Preserve the source error and force review, including every invoice on a
    # multi-invoice physical page, without mutating the caller's result model.
    for page in raw:
        if page["page_index"] in review_pages:
            page["_needs_manual_review"] = True
            page["_confidence_band"] = "needs_review"
    # 合计按扣预扣税之前的含税总额（读出的预扣额照旧单独存）。
    restore_total_before_withholding(raw)
    band = "needs_review" if review_pages else aggregate_confidence_band(result.pages)
    # Preserve the old whole-batch noninvoice gate. In a mixed batch, do not
    # silently discard noninvoice pages; grouping retains their source evidence.
    if all_pages_not_invoice(raw):
        return {"state": "not_invoice", "groups": [], "raw_pages": raw, "invoice_count": 0,
                **references, "confidence": None,
                "confidence_band": band, "requires_review": bool(review_pages), "has_billable_result": False}
    unread = [page for page in raw if UNREAD_STAGE in (page.get("_layer_chain") or [])]
    raw_read = [page for page in raw if UNREAD_STAGE not in (page.get("_layer_chain") or [])]
    # 非发票页（清单续页、附属页、选了不录入的转账凭条……）不自成一张票，分完挂到前一张作证据（2026-10-07：
    # 带自己单号和金额的非发票页原先会单独成一张记录）。整份只剩读不出的页和非发票页时照旧分，不让读不出的页丢掉。
    evidence = [page for page in raw_read if all_pages_not_invoice([page])]
    invoices = [page for page in raw_read if not all_pages_not_invoice([page])]
    if not invoices:
        invoices, evidence = raw_read, []
    assessed = assess_confidence(invoices)
    groups = _attach_unread(_attach_unread(group_invoices(assessed["pages"]), evidence, flag=False), unread)
    prepared = []
    declared_direction = normalize_direction(direction)
    # 入口勾的付款方式（现金/赊账）：先归一，再逐张写成人工裁决。
    declared_payment = normalize_payment(payment)
    for group in groups:
        apply_batch_direction(group["invoice_fields"], declared_direction)
        apply_batch_payment(group["invoice_fields"], declared_payment)
        first_final_pages = history_pages(group)
        # 简式税票票面价依法含 VAT：税额读成 0 / 没写的按 7/107 拆出（分组之后、每一页都改：不改分组结果，推送读哪页都一样）。
        split_included_vat(first_final_pages)
        prepared.append({"workspace": workspace, "client": client, "source": source,
            "source_page_indices": list(group["page_indices"]), "pages": first_final_pages,
            # Legacy insert_ocr_history defaulted ai_raw to the first saved
            # pages, including merged fields and the retained physical pages.
            "ai_raw": deepcopy(first_final_pages), "confidence": assessed["confidence"],
            "posting_kind": normalize_posting_kind(posting_kind)})
    return {"state": "review_required" if review_pages else "ready", "groups": prepared,
            "raw_pages": raw, "invoice_count": len(prepared),
            **references, "confidence": assessed["confidence"],
            "confidence_band": band, "requires_review": bool(review_pages), "has_billable_result": bool(prepared)}
