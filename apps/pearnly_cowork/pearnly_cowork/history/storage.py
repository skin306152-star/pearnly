"""Persist full invoice evidence in native DocTypes, not the early amount-only demo."""
from datetime import datetime, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from pearnly_cowork.access import scope
from .push_state import FIELDS as PUSH_FIELDS
from . import categories, duplicates
from pearnly_cowork.contracts.history import (HistoryContractError, PARTY_FIELDS, initial_history, prepare_pages,
    extract_party_fields, extract_summary_fields, list_status, normalize_posting_kind)

DOCTYPE = "Cowork OCR History"
_CREATE_TOKEN = object()
_UPDATE_TOKEN = object()
IMMUTABLE = ("workspace", "client", "source", "request_key", "request_fingerprint",
             "ai_raw", "confidence", "posting_kind", "source_pdf_id", "source_page_indices",
             "source_index", "source_total", "filename", "file_hash", "page_count", "source_file",
             # 新建录入时选的推送目标（连接 / 账套），识别落库时写入，之后按它推（用户 2026-09-26「按第一次选择」）。
             "push_connection", "push_account_set", "intake_target")
SUMMARY_FIELDS = ("invoice_no", "invoice_date", "seller_name", "total_amount")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        frappe.throw(_("history.json_invalid"))


def contract(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except HistoryContractError as error:
        frappe.throw(_(error.code))


def revision(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        frappe.throw(_("A positive record revision is required."))
    return int(value)


def anchor_year():
    return datetime.now(ZoneInfo("Asia/Bangkok")).year


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def conflict():
    frappe.throw(_("The record changed. Reload it before saving."), frappe.TimestampMismatchError)


def validate_document(doc):
    scope.require("intake")
    scope.require_scope(doc.workspace, doc.client)
    previous = doc.get_doc_before_save()
    if previous:
        if doc.flags.cowork_history_update is not _UPDATE_TOKEN:
            frappe.throw(_("Update invoice records through the Cowork review service."))
        if any(doc.get(key) != previous.get(key) for key in (*IMMUTABLE, *PUSH_FIELDS, *duplicates.FIELDS)):
            frappe.throw(_("Original evidence and ownership cannot be changed."))
        # Only dedicated APIs may mutate; native PUT omitting a client revision is rejected.
        if int(doc.record_version or 0) != int(previous.record_version or 0):
            conflict()
        if not previous.staged and doc.staged:
            frappe.throw(_("A completed record cannot become a draft."))
        doc.record_version = int(previous.record_version) + 1
        changed = doc.pages != previous.pages
        doc.edit_count = int(previous.edit_count or 0) + int(changed)
        doc.fields_edited_at = now_utc() if changed else previous.fields_edited_at
        if previous.staged and not doc.staged:
            doc.confirmed_by, doc.confirmed_at = frappe.session.user, now_utc()
        else:
            doc.confirmed_by, doc.confirmed_at = previous.confirmed_by, previous.confirmed_at
    else:
        # Native REST must not bypass the creation service to forge OCR/identity evidence.
        if doc.flags.cowork_history_create is not _CREATE_TOKEN:
            frappe.throw(_("Create invoice records through the Cowork intake service."))
        # Frappe clears fields above the actor's permlevel before validation.
        # Restore only this service-computed private reference after checking
        # the creation capability; keep every other native permission intact.
        doc.duplicate_match = doc.flags.cowork_duplicate_match
        if any(doc.get(key) for key in PUSH_FIELDS):
            frappe.throw(_("Original evidence and ownership cannot be changed."))
        doc.record_version, doc.edit_count = 1, 0
        doc.fields_edited_at, doc.confirmed_by, doc.confirmed_at = None, None, None
        if not doc.staged:
            frappe.throw(_("New records must start as drafts."))
    pages = contract(prepare_pages, parse(doc.pages), anchor_year=anchor_year())
    doc.pages = encode(pages)
    summary = extract_summary_fields(pages)
    for field in SUMMARY_FIELDS:
        doc.set(field, summary[field])
    # 列表的买卖方与税号列：与摘要同一主页面，随每次写入重算，才能在服务端排序。
    for field, value in extract_party_fields(pages).items():
        doc.set(field, value)
    doc.quality_status = list_status(doc.confidence, summary)


def guard_write(doc):
    token = _CREATE_TOKEN if doc.is_new() else _UPDATE_TOKEN
    actual = doc.flags.cowork_history_create if doc.is_new() else doc.flags.cowork_history_update
    if actual is not token:
        frappe.throw(_("Update invoice records through the Cowork review service."))


def create_manual(workspace, pages, request_key, client=None):
    # A caller-entered page is not an OCR result. Only a trusted recognition
    # workflow may set source/confidence/first-model evidence.
    scope.require("intake")
    scope.require_scope(workspace, client)
    body = contract(initial_history, parse(pages), public_create=True, anchor_year=anchor_year())
    body["ai_raw"] = None
    return _create(workspace, body, request_key, client)


def create_recognized(workspace, pages, request_key, *, source, confidence, ai_raw,
                      client=None, posting_kind=None):
    """Internal persistence boundary for a future OCR handler; not a whitelisted RPC.

    This does not invoke a model or charge usage. The handler must restore the
    verified actor before calling, as ordinary workspace permission still applies.
    """
    body = contract(initial_history, pages, source=source, confidence=confidence, ai_raw=ai_raw,
                           posting_kind=posting_kind, anchor_year=anchor_year())
    return _create(workspace, body, request_key, client, check_duplicates=True)


def _create(workspace, body, request_key, client=None, *, attachment=None, check_duplicates=False):
    scope.require("intake")
    scope.require_scope(workspace, client)
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        frappe.throw(_("A request key of 1 to 128 characters is required."))
    identity = {"workspace": workspace, "client": client or None, "body": body}
    if attachment:
        identity["attachment"] = attachment
    fingerprint = hashlib.sha256(encode(identity).encode()).hexdigest()
    key = hashlib.sha256(encode([workspace, frappe.session.user, request_key]).encode()).hexdigest()
    name = "CWH-" + key[:48]
    if frappe.db.exists(DOCTYPE, name):
        return _existing(name, fingerprint)
    pages = categories.prepare(workspace, client, body["pages"]) if check_duplicates else body["pages"]
    doc = frappe.get_doc({"doctype": DOCTYPE, "name": name, "workspace": workspace, "client": client or "",
                          "source": body["source"], "pages": encode(pages),
                          "ai_raw": encode(body["ai_raw"]), "confidence": body["confidence"], "staged": 1,
                          "posting_kind": body["posting_kind"],
                          "request_key": key, "request_fingerprint": fingerprint,
                          "page_count": len(body["pages"]),
                          "source_page_indices": encode(body["source_page_indices"])})
    if attachment:
        doc.update(attachment)
    if check_duplicates:
        doc.update(duplicates.check(workspace, client, body))
        doc.flags.cowork_duplicate_match = doc.duplicate_match
    else:
        doc.duplicate_check_status = "not_checked"
    doc.flags.cowork_history_create = _CREATE_TOKEN
    frappe.db.savepoint("cowork_create")
    try:
        doc.insert()
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point="cowork_create")
        return _existing(name, fingerprint)
    return project(doc)


def _existing(name, fingerprint):
    # Current read also sees a competing transaction's just-committed insert
    # under MariaDB REPEATABLE READ after rolling back our savepoint.
    doc = read(name, for_update=True)
    if doc.request_fingerprint != fingerprint:
        frappe.throw(_("This request key has already been used for different content."))
    return project(doc)


def read(name, *, for_update=False):
    scope.require_read()
    # Frappe native resource APIs also use the registered permission/query hooks.
    doc = frappe.get_doc(DOCTYPE, name, for_update=for_update)
    scope.require_scope(doc.workspace, doc.client)
    doc.check_permission("read")
    return doc


def project(doc):
    result = {key: doc.get(key) for key in (
        "name", "workspace", "client", "source", "staged", "record_version", *PUSH_FIELDS,
        "confidence", "quality_status", "posting_kind", *SUMMARY_FIELDS,
        "edit_count", "fields_edited_at", "confirmed_by", "confirmed_at",
        "source_file", "source_pdf_id", "source_index", "source_total", "filename", "file_hash", "page_count",
        "push_connection", "push_account_set", "intake_target")}
    for key in ("pages", "ai_raw", "source_page_indices"):
        result[key] = parse(doc.get(key) or "null")
    result["posting_kind"] = normalize_posting_kind(doc.posting_kind)
    result["duplicate_check_status"] = doc.duplicate_check_status or "not_checked"
    result["duplicate_warning"] = duplicates.warning(doc)
    if doc.source_file:
        from pearnly_cowork.files.access import reference_kind
        original = frappe.get_doc("Cowork Source File", doc.source_file)
        result["source_reference_kind"] = reference_kind(original)
    else:
        result["source_reference_kind"] = None
    return result


def update_pages(name, pages, expected_version):
    scope.require("intake")
    doc = read(name, for_update=True)
    if revision(expected_version) != int(doc.record_version):
        conflict()
    pages = contract(prepare_pages, parse(pages), anchor_year=anchor_year())
    if not pages:
        frappe.throw(_("At least one invoice page is required when editing."))
    changed = doc.pages != encode(pages)
    doc.pages = encode(pages)
    doc.flags.cowork_history_update = _UPDATE_TOKEN
    doc.save()
    from pearnly_cowork.ocr.feedback.service import capture_saved_edit
    capture_saved_edit(doc)
    if changed:
        categories.learn(doc.workspace, doc.client, pages)
    return project(doc)


def complete(records):
    """One transaction, all-or-nothing; repeated completion does not write again."""
    scope.require("intake")
    records = parse(records)
    if not isinstance(records, list) or not 1 <= len(records) <= 500:
        frappe.throw(_("Select between 1 and 500 records."))
    if any(not isinstance(r, dict) or set(r) != {"name", "record_version"} for r in records):
        frappe.throw(_("Each record must have a name and revision."))
    if any(not isinstance(r["name"], str) or not r["name"] or len(r["name"]) > 140 for r in records):
        frappe.throw(_("Each record must have a valid name."))
    for item in records:
        item["record_version"] = revision(item["record_version"])
    if len({r["name"] for r in records}) != len(records):
        frappe.throw(_("Duplicate records are not allowed."))
    locked = []
    for item in sorted(records, key=lambda r: r["name"]):
        doc = read(item["name"], for_update=True)
        if doc.staged and int(item["record_version"]) != int(doc.record_version):
            conflict()
        locked.append(doc)
    # Validate the entire scope/version set before any writes.
    by_name = {}
    frappe.db.savepoint("cowork_complete")
    try:
        for doc in locked:
            was_draft = bool(doc.staged)
            if was_draft:
                doc.staged = 0
                doc.flags.cowork_history_update = _UPDATE_TOKEN
                doc.save()
            by_name[doc.name] = {"name": doc.name, "staged": False, "already_completed": not was_draft,
                                 "record_version": doc.record_version}
    except frappe.QueryDeadlockError:
        # InnoDB can remove every savepoint when rolling back a deadlock.
        # A database conflict requires a fresh transaction, not a savepoint retry.
        frappe.db.rollback()
        raise
    except Exception:
        frappe.db.rollback(save_point="cowork_complete")
        raise
    return [by_name[item["name"]] for item in records]
