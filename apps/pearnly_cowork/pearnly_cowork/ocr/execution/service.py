"""Admit one immutable invoice task per original; RPCs cannot select a tenant/model."""
import hashlib
import json

import frappe
from frappe import _
from pearnly_core import billing_client
from pearnly_core.execution import work
from pearnly_core.execution.errors import Deferred
from pearnly_cowork.access import scope
from pearnly_cowork.contracts.push import normalize_direction, normalize_posting_kind
from pearnly_cowork.files import access as files
from pearnly_cowork.ocr.execution import access, settings, store, work_guard
from pearnly_cowork.ocr.feedback import service as feedback

KIND = "cowork.invoice"


def balance(page_count, tier):
    result = billing_client.status("pdf", page_count, tier=tier)
    if result.get("allowed") is not True:
        code = "insufficient_balance" if result.get("error_code") == "insufficient_balance" else "lookup_error"
        raise Deferred(code, 60)


def submit(source_file, request_key, *, direction=None, posting_kind=None):
    scope.require("intake")
    if not isinstance(request_key, str) or not 1 <= len(request_key.strip()) <= 128:
        frappe.throw(_("A request key of 1 to 128 characters is required."))
    direction, posting_kind = normalize_direction(direction), normalize_posting_kind(posting_kind)
    source = frappe.get_doc(files.SOURCE, source_file, for_update=True)
    scope.require_scope(source.workspace, source.client)
    files.require_full(source)
    if source.kind not in {"image", "pdf"}:
        frappe.throw(_("This recognition service currently accepts PDF and image originals."))
    name = store.digest([KIND, source.name])
    fingerprint = store.digest([source.name, source.sha256, frappe.session.user, request_key, direction, posting_kind])
    if frappe.db.exists(access.DOCTYPE, name):
        doc = store.read(name, write=True)
        if doc.request_fingerprint != fingerprint:
            frappe.throw(_("This original already has a recognition task with different parameters."))
        # Never reset a failed/uncertain work item or buy another recognition.
        return store.project(doc)
    if source.binding_complete:
        frappe.throw(_("The original file already has a completed invoice manifest."))
    frozen = settings.snapshot()
    examples = feedback.snapshot_for_scope(source.workspace, source.client) if frozen["fewshot"] else None
    balance(source.page_count, frozen["billing_tier"])
    work_name = hashlib.sha256((KIND + "\0" + name).encode()).hexdigest()
    doc = store.create({"source_file": source.name, "workspace": source.workspace, "client": source.client or "",
        "actor": frappe.session.user, "request_key": request_key, "request_fingerprint": fingerprint,
        "source_sha256": source.sha256, "page_count": source.page_count,
        "billing_reference": "cowork.invoice:" + name, "settings_json": store.encode(frozen),
        "examples_json": store.encode(examples), "anchor_date": frozen["anchor_date"],
        "direction": direction, "posting_kind": posting_kind, "phase": "admitted",
        "billing_state": "not_due", "work_name": work_name})
    # Core commits admission+Work together. The Batch already contains the exact
    # deterministic Work name before a worker can be enqueued by that commit.
    with work_guard.work_creation():
        actual = work.submit(KIND, doc.name, {"batch_name": doc.name}, doc.actor)
    if actual != work_name:
        raise RuntimeError("Unexpected OCR work identity")
    return store.project(doc)


def detail(name):
    return store.project(store.read(name))


def settle(name):
    """Receipt-only retry. A saved result is never sent back through OCR."""
    doc = store.read(name, lock=True, write=True)
    if doc.phase != "saved" or doc.billing_state == "charged":
        return doc
    frozen = json.loads(doc.settings_json)
    try:
        result = billing_client.charge("cowork", "pdf", doc.page_count,
            tier=frozen["billing_tier"], reference=doc.billing_reference,
            description="Cowork invoice recognition")
    except Exception:
        # An unparseable/lost response is also unknown accounting outcome.
        # Stable reference makes a receipt retry safe; never log raw responses.
        result = {"ok": False}
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
