"""Internal immutable admission snapshots and monotonic result receipts.

Provider checkpoints commit independently. No open history transaction may
cross a provider call; final history+manifest+batch receipt commit together.
"""
from contextlib import contextmanager
import hashlib
import json

import frappe
from frappe import _
from pearnly_cowork.ocr.execution import access

WRITE = object()
IMMUTABLE = ("source_file", "workspace", "client", "actor", "request_key", "request_fingerprint",
             "source_sha256", "page_count", "billing_reference", "settings_json", "examples_json",
             "anchor_date", "direction", "posting_kind", "work_name")


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def validate(doc):
    if doc.flags.cowork_ocr_write is not WRITE:
        frappe.throw(_("Use the Cowork OCR service to change recognition tasks."), frappe.PermissionError)
    previous = doc.get_doc_before_save()
    if previous:
        if any(doc.get(k) != previous.get(k) for k in IMMUTABLE):
            frappe.throw(_("Recognition input and ownership cannot be changed."))
        if previous.result_json and previous.result_json != doc.result_json:
            frappe.throw(_("The completed recognition result cannot be replaced."))
        if previous.history_records and previous.history_records != doc.history_records:
            frappe.throw(_("The recognized invoice manifest cannot be replaced."))
        if previous.billing_state == "charged" and doc.billing_state != "charged":
            frappe.throw(_("The billing receipt cannot be reverted."))
    else:
        doc.name = digest(["cowork.invoice", doc.source_file])


def reject_delete(doc):
    frappe.throw(_("Recognition tasks cannot be deleted through the document API."), frappe.PermissionError)


def read(name, *, lock=False, authorize=True, write=False):
    doc = frappe.get_doc(access.DOCTYPE, name, for_update=lock)
    if authorize:
        access.require_batch(doc, write=write)
    return doc


def save(doc):
    doc.flags.cowork_ocr_write = WRITE
    doc.flags.notify_update = False
    return doc.save(ignore_permissions=True)


def create(values):
    doc = frappe.get_doc({"doctype": access.DOCTYPE, **values})
    doc.flags.cowork_ocr_write = WRITE
    doc.flags.notify_update = False
    return doc.insert(ignore_permissions=True)


@contextmanager
def actor(user):
    previous = frappe.session.user
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous)


def project(doc):
    """Return safe lifecycle fields, never provider/raw prompt/credential data."""
    access.require_batch(doc)
    work = frappe.db.get_value(access.WORK, doc.work_name,
        ["status", "last_code", "attempts"], as_dict=True) if doc.work_name else None
    state = doc.phase
    if state not in {"saved", "not_invoice"} and work:
        state = work.status
    return {"name": doc.name, "source_file": doc.source_file, "page_count": doc.page_count,
            "state": state, "billing_state": doc.billing_state, "billing_code": doc.billing_code or "",
            "requires_review": bool(doc.requires_review),
            "history_records": json.loads(doc.history_records or "[]"),
            "attempts": work.attempts if work else 0,
            "last_code": (work.last_code or "") if work and state not in {"saved", "not_invoice"} else ""}
