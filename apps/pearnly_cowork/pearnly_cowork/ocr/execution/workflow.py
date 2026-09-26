"""Core-registered invoice workflow. All external effects use shared services.

Phases commit monotonically: accepted input -> frozen result -> atomic staged
histories and original manifest -> billing receipt. A pending receipt retries
only accounting; provider uncertainty is left to Core's explicit terminal state.
"""
from datetime import date
from functools import partial
import json
import time

import frappe
from frappe import _
from pearnly_core.execution.errors import Deferred
from pearnly_cowork.files.service import read_bytes, freeze_doc_manifest
from pearnly_cowork.files.access import reference_count, reference_kind
from pearnly_cowork.history.recognition import persist_batch
from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.engine.fewshot import fewshot_for_text
from pearnly_cowork.ocr.execution import access, service, settings, store
from pearnly_cowork.ocr.feedback import service as feedback
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers.core import build_client
from pearnly_cowork.ocr.results import prepare_history_batch
from pearnly_cowork.ocr.schema import PipelineResult
from pearnly_cowork.ocr.table_inputs import extract_table
from pearnly_cowork.ocr.table_engine import run_table, validate_document
from pearnly_cowork.ocr.engine.contracts import EngineResult
from pearnly_cowork.roundtrip import intake as roundtrip
from pearnly_cowork.roundtrip.access import require_links


def _require_work(doc):
    name = frappe.flags.get("pearnly_ocr_work")
    current = frappe.db.get_value(access.WORK, name, ["kind", "reference", "user", "status"], as_dict=True) if name else None
    if (name != doc.work_name or not current or current.kind != service.KIND
            or current.reference != doc.name or current.user != doc.actor or current.status != "running"):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def _check(doc, frozen=None, examples=None):
    _require_work(doc)
    # Long RQ jobs retain Frappe's local roles cache even after another request
    # invalidates Redis. Reset the actor context before each paid call/save.
    frappe.set_user(doc.actor)
    source = access.require_batch(doc, write=True)
    if (source.sha256 != doc.source_sha256 or reference_count(source) != doc.page_count
            or reference_kind(source) != (doc.get("reference_kind") or "physical_page")):
        frappe.throw(_("The original file checksum does not match."))
    if frozen is not None:
        settings.revalidate(frozen)
    if examples is not None:
        feedback.validate_snapshot(examples)
    return source


def _evidence(calls):
    # Completed raw provider bodies are held in Core checkpoints. Keep durable
    # digests/accounting metadata here without duplicating large prompt bodies.
    seen, result = set(), []
    for call in calls:
        key = call.checkpoint_key or store.digest([call.stage, call.page_number, call.attempt, call.model, call.raw])
        if key in seen:
            continue
        seen.add(key)
        result.append({"checkpoint_key": key, "stage": call.stage, "page": call.page_number,
            "attempt": call.attempt, "model": call.model, "raw_sha256": store.digest(call.raw),
            "input_tokens": call.input_tokens, "output_tokens": call.output_tokens,
            "finish_reason": call.finish_reason})
    return result


def _recognize(doc, frozen, examples):
    source = _check(doc, frozen, examples)
    kind, units = service.billing_basis(doc)
    service.balance(units, frozen["billing_tier"], kind=kind)
    if frozen.get("input_engine") == roundtrip.ENGINE:
        content = read_bytes(source)
        document, pipeline = roundtrip.decode(content, extract_table(content, source.filename),
            anchor_year=date.fromisoformat(str(doc.anchor_date)).year)
        freeze_doc_manifest(source, document)
        require_links(pipeline, doc.workspace, doc.client)
        if kind != "excel" or units != document.billing_characters:
            frappe.throw(_("Recognition input and ownership cannot be changed."))
        return _freeze_result(doc, EngineResult(pipeline, ()), frozen, examples)
    environment = frozen["environment"]
    selected = invoice_policy(environment, frozen["policy"])
    config = config_from_environment(environment, selected)
    client = build_client(environment, core_identity={k: frozen["target"][k] for k in
                          ("backend", "project", "location", "credentials_path")})
    body = feedback.validate_snapshot(examples) if examples else None
    selector = partial(fewshot_for_text, examples_by_tax=body["examples_by_tax"]) if body else None
    started = time.monotonic()

    def guarded(callback):
        def invoke(*args, **kwargs):
            # End the read snapshot before checking revocations/cancellation.
            # No history writes exist while providers or checkpoints run.
            frappe.db.commit()
            _check(doc, frozen, examples)
            if time.monotonic() - started > 420:
                raise Deferred("progress_yield", 1)
            return callback(*args, **kwargs)
        return invoke

    if source.kind == "document":
        document = extract_table(read_bytes(source), source.filename)
        validate_document(document)
        freeze_doc_manifest(source, document)  # unchanged input/manifest required on every replay
        if kind != "excel" or units != document.billing_characters:
            frappe.throw(_("Recognition input and ownership cannot be changed."))
        result = run_table(document, guarded(client.model_call), config=config,
            anchor_year=date.fromisoformat(str(doc.anchor_date)).year,
            today=date.fromisoformat(str(doc.anchor_date)), fewshot_selector=selector)
    else:
        with PageSource(read_bytes(source), source.filename, expected_page_count=int(doc.page_count),
                        enable_text_path=selected.text_fast_path,
                        image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400"))) as pages:
            result = run_invoice(pages, guarded(client.model_call), guarded(client.vision_call), config=config,
                anchor_year=date.fromisoformat(str(doc.anchor_date)).year,
                today=date.fromisoformat(str(doc.anchor_date)), fewshot_selector=selector)
    return _freeze_result(doc, result, frozen, examples)


def _freeze_result(doc, result, frozen, examples):
    frappe.db.commit()
    _check(doc, frozen, examples)
    doc = store.read(doc.name, lock=True, write=True)
    if not doc.result_json:
        doc.result_json = result.pipeline.model_dump_json()
        doc.provider_evidence = store.encode(_evidence(result.calls))
        doc.phase = "recognized"
        store.save(doc)
    frappe.db.commit()
    return doc


def _persist(doc):
    _check(doc)
    anchor = date.fromisoformat(str(doc.anchor_date)).year
    result = PipelineResult.model_validate_json(doc.result_json, context={"anchor_year": anchor})
    if result.engine == roundtrip.ENGINE:
        require_links(result, doc.workspace, doc.client)
    plan = prepare_history_batch(result, expected_page_count=int(doc.page_count), workspace=doc.workspace,
        source="manual", client=doc.client or None, direction=doc.direction, posting_kind=doc.posting_kind,
        payment=doc.get("payment") or None,  # 库里的空值＝入口没选，不是非法值
        reference_kind=doc.get("reference_kind") or "physical_page")
    # Read after provider/result commits. This transaction contains every history,
    # its sealed source binding, and the receipt that makes billing eligible.
    doc = store.read(doc.name, lock=True, write=True)
    if doc.phase in {"saved", "not_invoice"}:
        return doc
    rows = persist_batch(doc.source_file, plan["groups"], doc.name, anchor_year=anchor) if plan["groups"] else []
    if doc.get("target_connection") or doc.get("intake_target"):
        # 新建录入选的推送目标抄到每张票上：之后从列表进来提交也按第一次的选择推（用户 2026-09-26）；
        # 选的是哪张目标卡片也抄上（识别后导出的票打开复核页是「导出」，不是「提交」）。
        for row in rows:
            frappe.db.set_value("Cowork OCR History", row["name"], {"push_connection": doc.get("target_connection") or "",
                "push_account_set": doc.get("target_account_set") or "",
                "intake_target": doc.get("intake_target") or ""}, update_modified=False)
    doc.history_records = store.encode([r["name"] for r in rows])
    doc.phase = "saved" if rows else "not_invoice"
    doc.billing_state = "pending" if rows else "not_billable"
    doc.requires_review = plan["requires_review"]
    store.save(doc)
    frappe.db.commit()
    _announce(doc)
    return doc


def _announce(doc):
    """识别结果已落成票：告诉登记了 `cowork_ocr_recognized` 的入口（LINE 收单据此推卡）。

    只在这一次落库时调用；通知失败不影响识别结果与扣费。"""
    for path in frappe.get_hooks("cowork_ocr_recognized") or []:
        try:
            frappe.get_attr(path)(doc)
        except Exception:
            frappe.log_error(title="Cowork OCR recognized hook failed", message=frappe.get_traceback()[-1500:])


def run(batch_name):
    doc = store.read(batch_name, authorize=False)
    _require_work(doc)
    with store.actor(doc.actor):
        _check(doc)
        if doc.phase not in {"saved", "not_invoice"}:
            if not doc.result_json:
                frozen, examples = json.loads(doc.settings_json), json.loads(doc.examples_json)
                doc.phase = "recognizing"
                store.save(doc)
                frappe.db.commit()
                doc = _recognize(doc, frozen, examples)
            try:
                doc = _persist(doc)
            except frappe.QueryDeadlockError as exc:
                # Only the local transaction is retried. The immutable result
                # and independently committed provider checkpoints survive.
                frappe.db.rollback()
                raise Deferred("database_busy", 5) from exc
        if doc.phase == "saved":
            doc = service.settle(doc.name)
            if doc.billing_state != "charged":
                raise Deferred("billing_pending", 60)
        return {"batch_name": doc.name, "state": doc.phase}
