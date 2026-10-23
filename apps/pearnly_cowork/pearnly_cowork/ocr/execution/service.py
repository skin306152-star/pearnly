"""Admit one immutable invoice task per original; RPCs cannot select a tenant/model."""
import hashlib
import json
from datetime import date

import frappe
from frappe import _
from pearnly_core import billing_client
from pearnly_core.execution import work
from pearnly_core.execution.errors import Deferred
from pearnly_cowork.access import scope
from pearnly_core.express_rules.contracts.push import (normalize_direction, normalize_payment,
                                          normalize_posting_kind)
from pearnly_cowork.files import access as files
from pearnly_cowork.files import service as originals
from pearnly_cowork.ocr.execution import access, settings, store, work_guard
from pearnly_cowork.ocr.feedback import service as feedback
from pearnly_cowork.ocr.table_inputs import extract_table
from pearnly_cowork.ocr.table_engine import validate_document
from pearnly_cowork.ocr.table_engine.roundtrip import has_roundtrip_sheet
from pearnly_cowork.roundtrip import intake as roundtrip
from pearnly_cowork.roundtrip.access import require_links

KIND = "cowork.invoice"


def billing_basis(doc):
    # Old immutable batches predate explicit billing fields; they only admitted
    # PDF/image originals. An explicit zero-character Word file remains zero.
    return (doc.billing_kind, int(doc.billing_units)) if doc.get("billing_kind") else ("pdf", int(doc.page_count))


def balance(units, tier, *, kind="pdf"):
    result = billing_client.status(kind, units, tier=tier)
    if result.get("allowed") is not True:
        code = "insufficient_balance" if result.get("error_code") == "insufficient_balance" else "lookup_error"
        raise Deferred(code, 60)


def _target(connection, account_set_key):
    """新建录入选的推送目标：只记下来（推送时由推送域按当前授权与目录证据再核），这里只查形状。"""
    connection = str(connection or "").strip() or None
    account_set_key = str(account_set_key or "").strip() or None
    if (connection and len(connection) > 140) or (account_set_key and (len(account_set_key) > 255 or not connection)):
        frappe.throw(_("Invalid push target."))
    return connection, account_set_key


TARGETS = ("mrerp", "express", "none")


def submit(source_file, request_key, *, direction=None, posting_kind=None, payment=None,
           connection=None, account_set_key=None, target=None):
    scope.require("intake")
    if not isinstance(request_key, str) or not 1 <= len(request_key.strip()) <= 128:
        frappe.throw(_("A request key of 1 to 128 characters is required."))
    direction, posting_kind = normalize_direction(direction), normalize_posting_kind(posting_kind)
    # 入口勾的付款方式：认不出就拒（不静默丢掉商家的选择）。
    payment = normalize_payment(payment)
    connection, account_set_key = _target(connection, account_set_key)
    # 新建录入选的目标卡片（MR.ERP / Express / 识别后导出）：记在票上，之后打开复核页才知道右上角是「提交」还是「导出」。
    target = str(target or "").strip() or None
    if target and target not in TARGETS:
        frappe.throw(_("Invalid push target."))
    # 先不锁原件：要问计费服务的余额（走网络），锁着原件等它，计费一慢原件行锁和连接就一直占着（独立复查 2026-10-01）。
    # 余额问完再锁、再核一次（下面 `_admitted`），同一份同时交两次照旧只建一个。
    source = frappe.get_doc(files.SOURCE, source_file)
    scope.require_scope(source.workspace, source.client)
    files.require_full(source)
    name = store.digest([KIND, source.name])
    # 目标只在带了时进指纹：不带目标的老请求指纹不变，重放照旧认得出来。
    fingerprint = store.digest([source.name, source.sha256, frappe.session.user, request_key,
                                direction, posting_kind, payment, *([connection, account_set_key] if connection else []),
                                *([target] if target else [])])
    admitted = _admitted(name, fingerprint, source)
    if admitted is not None:
        return admitted
    count, billing_kind, billing_units = int(source.page_count), "pdf", int(source.page_count)
    frozen = None
    if source.kind == "document":
        content = originals.read_bytes(source)
        document = extract_table(content, source.filename)
        if has_roundtrip_sheet(document.provenance):
            frozen = settings.snapshot(deterministic=True)
            document, result = roundtrip.decode(content, document,
                anchor_year=date.fromisoformat(frozen["anchor_date"]).year)
            require_links(result, source.workspace, source.client)
        else:
            validate_document(document)
        count, billing_kind, billing_units = document.page_count, "excel", document.billing_characters
    elif source.kind not in {"image", "pdf"}:
        frappe.throw(_("This file type is not supported."))
    frozen = frozen or settings.snapshot()
    examples = feedback.snapshot_for_scope(source.workspace, source.client) if frozen["fewshot"] else None
    balance(billing_units, frozen["billing_tier"], kind=billing_kind)
    frappe.db.commit()  # 结束读快照再锁（到这里只读没写）：锁后的「交过没有」要看到别人刚提交的那份
    source = frappe.get_doc(files.SOURCE, source_file, for_update=True)
    admitted = _admitted(name, fingerprint, source)  # 问余额期间别人（同一份的重复提交）可能已经建好
    if admitted is not None:
        return admitted
    if source.kind == "document":
        originals.freeze_doc_manifest(source, document)
    work_name = hashlib.sha256((KIND + "\0" + name).encode()).hexdigest()
    doc = store.create({"source_file": source.name, "workspace": source.workspace, "client": source.client or "",
        "actor": frappe.session.user, "request_key": request_key, "request_fingerprint": fingerprint,
        "source_sha256": source.sha256, "page_count": count,
        "reference_kind": "logical_section" if source.kind == "document" else "physical_page",
        "billing_kind": billing_kind, "billing_units": billing_units,
        "billing_reference": "cowork.invoice:" + name, "settings_json": store.encode(frozen),
        "examples_json": store.encode(examples), "anchor_date": frozen["anchor_date"],
        "direction": direction, "posting_kind": posting_kind, "payment": payment,
        "target_connection": connection, "target_account_set": account_set_key, "intake_target": target,
        "phase": "admitted",
        "billing_state": "not_due", "work_name": work_name})
    # Core commits admission+Work together. The Batch already contains the exact
    # deterministic Work name before a worker can be enqueued by that commit.
    with work_guard.work_creation():
        actual = work.submit(KIND, doc.name, {"batch_name": doc.name}, doc.actor)
    if actual != work_name:
        raise RuntimeError("Unexpected OCR work identity")
    return store.project(doc)


def _admitted(name, fingerprint, source):
    """这一份已经交过：同样参数回原来那份（不重置失败 / 结果不明的，也不再买一次识别），参数不同就拒；还没交过回 None。"""
    if frappe.db.exists(access.DOCTYPE, name):
        doc = store.read(name, write=True)
        if doc.request_fingerprint != fingerprint:
            frappe.throw(_("This original already has a recognition task with different parameters."))
        # Never reset a failed/uncertain work item or buy another recognition.
        return store.project(doc)
    if source.binding_complete:
        frappe.throw(_("The original file already has a completed invoice manifest."))
    return None


def detail(name):
    return store.project(store.read(name))


def recognized(source_file):
    """这份原件已交过的识别任务（只读，同 `detail`）；还没交过回 None。

    同一份原件只认第一次交的口径（`_admitted` 对不同参数拒收）。对话里先「仅识别」、会计员随后才说是哪家公司的，
    CC 再交时用它拿回原来那份（不另买一次识别），再按会计员说的账套推送（推送可指定连接与账套）。"""
    name = store.digest([KIND, source_file])
    if not frappe.db.exists(access.DOCTYPE, name):
        return None
    return detail(name)


def certificates(name) -> dict:
    """这份识别里的 50 ทวิ（CC 据此说「这是预扣税凭证」，不只说「不是发票」）：{"pages": 读成凭证的读数个数,
    "kept": 存给 ภ.ง.ด.3 / 53 草稿的参考记录数（租户选了「不录入」的）}。认凭证与识别存档同一条规则
    （`contracts.document_policy.document_class`），读存下的结果，不调模型、不扣费。"""
    from pearnly_cowork.contracts import document_policy
    from pearnly_cowork.history import certificates as references

    doc = store.read(name)
    try:
        pages = json.loads(doc.result_json or "{}").get("pages") or []
    except (AttributeError, ValueError):
        pages = []
    reads = []
    for page in pages if isinstance(pages, list) else []:
        invoice = page.get("invoice") if isinstance(page, dict) else None
        if isinstance(invoice, dict):
            extras = invoice.get("additional_invoices") or []
            reads += [invoice, *[extra for extra in extras if isinstance(extra, dict)]]
    found = sum(document_policy.document_class(read) == document_policy.CERTIFICATE for read in reads)
    return {"pages": found, "kept": references.kept(doc.name)}


def settle(name, *, authorized=False):
    """Receipt-only retry. A saved result is never sent back through OCR.

    ``authorized``：识别任务里存完结果紧接着扣费，刚复核过，不再重复同一刻的复核；用户点「重试记账」照旧复核。"""
    doc = store.read(name, write=True, authorize=not authorized)
    if doc.phase != "saved" or doc.billing_state == "charged":
        return doc
    frozen = json.loads(doc.settings_json)
    kind, units = billing_basis(doc)
    # 计费要走网络（排队时可能几秒）：不锁着识别单、不占着事务等它（独立复查 2026-10-01：计费一慢，整台数据库的连接
    # 都被这些等着的请求推高）。扣费按参考号幂等，同一张同时记两次也只扣一次；回来后再锁住写结果。
    frappe.db.commit()
    try:
        result = billing_client.charge("cowork", kind, units,
            tier=frozen["billing_tier"], reference=doc.billing_reference,
            description="Cowork invoice recognition")
    except Exception:
        # An unparseable/lost response is also unknown accounting outcome.
        # Stable reference makes a receipt retry safe; never log raw responses.
        result = {"ok": False}
    doc = store.read(name, lock=True, write=True, authorize=False)
    if doc.phase != "saved" or doc.billing_state == "charged":
        return doc
    if isinstance(result, dict) and result.get("ok") is True:
        doc.billing_state, doc.billing_code = "charged", ""
        # Only explicitly allowed receipt fields, no remote response body leaks.
        doc.billing_receipt = store.encode({k: result[k] for k in
            ("ok", "charged_thb", "balance_after", "kind", "units", "tier", "duplicate", "reason") if k in result})
    else:
        doc.billing_state, doc.billing_code = "pending", "billing_pending"
    store.save(doc)
    frappe.db.commit()
    return doc


def retry_billing(name):
    return store.project(settle(name))
