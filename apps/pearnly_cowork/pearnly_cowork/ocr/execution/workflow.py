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
from pearnly_core import ocr
from pearnly_core.execution import pages as core_pages
from pearnly_core.execution import pool as core_pool
from pearnly_core.execution.errors import Deferred
from pearnly_cowork.files.service import read_bytes, freeze_doc_manifest, preview_record, preview_spec, stored_original
from pearnly_cowork.files.access import reference_count, reference_kind
from pearnly_cowork.history.recognition import persist_batch
from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.engine.fewshot import fewshot_for_text
from pearnly_cowork.ocr.execution import access, service, settings, store
from pearnly_cowork.ocr.feedback import service as feedback
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers.client import ProviderError
from pearnly_cowork.ocr.providers.core import build_client
from pearnly_cowork.ocr.remote import contract, forward
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


def _call_page(args, kwargs):
    """这次模型 / 读原文调用是第几页（引擎按页顺序读：读到第 N 页，前 N−1 页已读完）。"""
    page = kwargs.get("page_number")
    if page is None and args:
        page = getattr(args[0], "page_number", None)
    return page if isinstance(page, int) and not isinstance(page, bool) else None


def _recognize(doc, frozen, examples, source):
    """``source``：调用方（``run``）刚做完的完整复核（含冻结配置与样例）回的原件；之后每次付费调用前照旧再复核。"""
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
    stored = stored_original(source)
    content = None if stored or source.kind == "document" or not forward.settings() else read_bytes(source)
    # 原件在服务器上且超过一次请求能带的大小（20 MiB）：照旧在本机读，不让整份报错（ADR-016）。
    if source.kind != "document" and forward.settings() and (stored or len(content) <= contract.MAX_CONTENT_BYTES):
        # 在 Cloud Run 上读（ADR-0015）：同一份引擎、同样冻结的配置；发出前已复核（上面的 _check），存结果前再复核一次。
        # 原件在原件桶里（浏览器直传，ADR-016）就只给对象和版本号，Cloud Run 自己读、核对 sha256。
        body = feedback.validate_snapshot(examples) if examples else None
        original = contract.original(object=stored[1], generation=stored[2], sha256=source.sha256) if stored else None
        # 识别时顺手把各页预览图存进原件桶：页面看原件直接从桶取，不再占服务器线程等 Cloud Run 现画。
        previews = []
        pipeline, evidence = forward.recognize(doc, frozen, body, content, source.filename,
                                               lambda page: store.pages_reached(doc.name, page), original=original,
                                               previews=preview_spec(source),
                                               on_previews=lambda items: previews.append(preview_record(source, items)))
        anchor = date.fromisoformat(str(doc.anchor_date)).year
        PipelineResult.model_validate_json(pipeline, context={"anchor_year": anchor})  # 收到的必须是合规结果
        doc = _freeze_result(doc, None, frozen, examples, remote=(pipeline, evidence))
        # 只放在内存里交给紧接着的落库（``_persist``），随绑定原件那一次保存写进原件记录，不额外写原件表；
        # 中途断掉没记上也没关系：预览照旧现画。
        doc.flags.cowork_previews = previews[0] if previews else None
        return doc
    selected = invoice_policy(environment, frozen["policy"])
    config = config_from_environment(environment, selected)
    client = build_client(environment, core_identity={k: frozen["target"][k] for k in
                          ("backend", "project", "location", "credentials_path")})
    body = feedback.validate_snapshot(examples) if examples else None
    selector = partial(fewshot_for_text, examples_by_tax=body["examples_by_tax"]) if body else None
    started = time.monotonic()
    # 多页文件各页同时读（开关 PEARNLY_OCR_PAGE_THREADS，见 pearnly_core.execution.pages）：进度改按「已读完几页」推，
    # 不再按「读到第 N 页」推（各页同时读时第 N 页开始不代表前面都读完了）。
    page_map = (core_pages.mapper(on_done=lambda count: store.pages_reached(doc.name, count + 1))
                if int(doc.page_count or 0) > 1 else None)

    def guarded(callback):
        def invoke(*args, **kwargs):
            # End the read snapshot before checking revocations/cancellation.
            # No history writes exist while providers or checkpoints run.
            frappe.db.commit()
            _check(doc, frozen, examples)
            if time.monotonic() - started > 420:
                raise Deferred("progress_yield", 1)
            if page_map is None:
                store.pages_reached(doc.name, _call_page(args, kwargs))
            return callback(*args, **kwargs)
        return invoke

    # 本机读：开了多线程时每个进程同时最多 local_slots() 份（渲染与整理吃内存）；交给 Cloud Run 的不占这个名额（ADR-015）。
    with core_pool.local_slot():
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
                            image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")), orient=ocr.orient) as pages:
                result = run_invoice(pages, guarded(client.model_call), guarded(client.vision_call), config=config,
                    anchor_year=date.fromisoformat(str(doc.anchor_date)).year,
                    today=date.fromisoformat(str(doc.anchor_date)), fewshot_selector=selector, map_pages=page_map)
    return _freeze_result(doc, result, frozen, examples)


def _freeze_result(doc, result, frozen, examples, remote=None):
    frappe.db.commit()
    # 结果回来、存之前复核一次（读的过程中可能被撤权、停用、改原件）；紧接着加锁读任务单不再重复同一刻的复核。
    _check(doc, frozen, examples)
    doc = store.read(doc.name, lock=True, write=True, authorize=False)
    if not doc.result_json:
        if remote:
            doc.result_json, evidence = remote
            doc.provider_evidence = store.encode(evidence)
        else:
            doc.result_json = result.pipeline.model_dump_json()
            doc.provider_evidence = store.encode(_evidence(result.calls))
        doc.phase = "recognized"
        store.save(doc)
    frappe.db.commit()
    return doc


def _persist(doc):
    """调用方（``run``）在这之前刚复核过（开头的复核，或识别后 ``_freeze_result`` 的复核），中间没有等待或外部调用。"""
    anchor = date.fromisoformat(str(doc.anchor_date)).year
    result = PipelineResult.model_validate_json(doc.result_json, context={"anchor_year": anchor})
    if result.engine == roundtrip.ENGINE:
        require_links(result, doc.workspace, doc.client)
    plan = prepare_history_batch(result, expected_page_count=int(doc.page_count), workspace=doc.workspace,
        source="manual", client=doc.client or None, direction=doc.direction, posting_kind=doc.posting_kind,
        payment=doc.get("payment") or None,  # 库里的空值＝入口没选，不是非法值
        reference_kind=doc.get("reference_kind") or "physical_page")
    previews = doc.flags.get("cowork_previews")
    # Read after provider/result commits. This transaction contains every history,
    # its sealed source binding, and the receipt that makes billing eligible.
    doc = store.read(doc.name, lock=True, write=True, authorize=False)
    if doc.phase in {"saved", "not_invoice"}:
        return doc
    rows = (persist_batch(doc.source_file, plan["groups"], doc.name, anchor_year=anchor, previews=previews)
            if plan["groups"] else [])
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


def remote_progress(arguments, events, state, user=None):
    """任务派发（``ocr.remote.callback``）送来的进度：读到第几页，与按行等回复时同样推给提交人
    （hooks ``cowork_remote_progress``）。"""
    for event in events:
        if event.get("event") == "page" and type(event.get("page")) is int:
            store.pages_reached(arguments.get("batch_name"), event["page"], user=user)


def _announce(doc):
    """识别结果已落成票：告诉登记了 `cowork_ocr_recognized` 的入口（LINE 收单据此推卡）。

    只在这一次落库时调用；通知失败不影响识别结果与扣费。"""
    for path in frappe.get_hooks("cowork_ocr_recognized") or []:
        try:
            frappe.get_attr(path)(doc)
        except Exception:
            frappe.log_error(title="Cowork OCR recognized hook failed", message=frappe.get_traceback()[-1500:])


def run(batch_name):
    # 授权复核（2026-10-01 用户同意合并）：付费调用前一次（开头这次，要识别时连冻结配置与样例一起复核）、结果回来存之前一次
    # （``_freeze_result``）；本机读时每次付费调用前照旧再复核。原来同一时刻背靠背的重复复核（开头两次、存结果前三次）去掉，
    # 被停用、撤权、改原件照旧在这两处拦下。
    doc = store.read(batch_name, authorize=False)
    _require_work(doc)
    with store.actor(doc.actor):
        recognizing = doc.phase not in {"saved", "not_invoice"} and not doc.result_json
        frozen = json.loads(doc.settings_json) if recognizing else None
        examples = json.loads(doc.examples_json) if recognizing else None
        source = _check(doc, frozen, examples)
        if doc.phase not in {"saved", "not_invoice"}:
            if not doc.result_json:
                doc.phase = "recognizing"
                store.save(doc)
                frappe.db.commit()
                try:
                    doc = _recognize(doc, frozen, examples, source)
                except ProviderError as error:
                    # 谷歌明确拒绝（不是临时故障：那些已在调用层稍后重试）：按拒绝的原因记失败，不当成程序错误
                    # （原来落成 workflow_error 并写错误日志，2026-10-01 测试租户）。不扣费；LINE 照常收到失败通知。
                    frappe.db.rollback()
                    return {"failed": error.code or "ocr.provider_http_error"}
            try:
                doc = _persist(doc)
            except frappe.QueryDeadlockError as exc:
                # Only the local transaction is retried. The immutable result
                # and independently committed provider checkpoints survive.
                frappe.db.rollback()
                raise Deferred("database_busy", 5) from exc
        if doc.phase == "saved":
            doc = service.settle(doc.name, authorized=True)
            if doc.billing_state != "charged":
                raise Deferred("billing_pending", 60)
        return {"batch_name": doc.name, "state": doc.phase}
