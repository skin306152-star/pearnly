"""Atomic, sealed original-to-invoice binding for a trusted future OCR handler.

This is deliberately not an RPC or provider execution path. The caller supplies
the complete validated grouping result while acting as the requesting user.
"""
import hashlib

import frappe
from frappe import _
from pearnly_cowork.access import scope
from pearnly_cowork.contracts.history import initial_history
from pearnly_cowork.files import access, guard, service as files
from pearnly_cowork.history import storage


def persist_batch(source_file, groups, request_key, *, anchor_year=None, previews=None):
    """``previews``：识别时存好的预览图记录（``files.service.preview_record``），随原件绑定同一次保存写入。"""
    scope.require("intake")
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        frappe.throw(_("A request key of 1 to 128 characters is required."))
    if not isinstance(groups, list) or not 1 <= len(groups) <= 500:
        frappe.throw(_("Select between 1 and 500 records."))
    # Serializes competing manifests. No preview can observe a partially bound
    # source because completion and all invoice records commit together.
    source = frappe.get_doc(access.SOURCE, source_file, for_update=True)
    scope.require_scope(source.workspace, source.client)
    if not source.binding_complete:
        access.require_full(source)
    count = access.reference_count(source)
    kind = access.reference_kind(source)
    if not count or kind not in {"physical_page", "logical_section"}:
        frappe.throw(_("The original document manifest is not ready."))
    prepared = []
    for group in groups:
        if not isinstance(group, dict):
            frappe.throw(_("Invalid recognized invoice group."))
        scope.require_scope(group.get("workspace"), group.get("client"))
        indices = group.get("source_page_indices")
        if (not isinstance(indices, list) or not indices
                or any(type(p) is not int or not 1 <= p <= count for p in indices)):
            frappe.throw(_("The requested page is not part of this invoice."))
        body = storage.contract(initial_history, group.get("pages"), source=group.get("source"),
            confidence=group.get("confidence"), ai_raw=group.get("ai_raw"),
            posting_kind=group.get("posting_kind"), anchor_year=anchor_year or storage.anchor_year(), recognized=True)
        if set(body["source_page_indices"]) != set(indices):
            frappe.throw(_("Invoice pages do not match the original page references."))
        prepared.append({"workspace": group["workspace"], "client": group.get("client"),
                         "body": body, "indices": sorted(set(indices))})
    identity = [request_key, prepared]
    if kind == "logical_section":
        identity.append(hashlib.sha256(source.provenance_json.encode()).hexdigest())
    fingerprint = hashlib.sha256(storage.encode(identity).encode()).hexdigest()
    if source.binding_complete and source.binding_fingerprint != fingerprint:
        frappe.throw(_("The original file already has a completed invoice manifest."))
    if source.binding_complete:
        # Record IDs belong to the first accepted manifest, not the retrying
        # user's request namespace. A different authorized actor must not create
        # another batch from the same source.
        names = storage.parse(source.binding_records)
        if not isinstance(names, list) or len(names) != len(prepared):
            frappe.throw(_("The original invoice manifest is incomplete."))
        rows = [storage.read(name, for_update=True) for name in names]
        if any(row.source_file != source.name for row in rows):
            frappe.throw(_("The original invoice manifest is incomplete."))
        return [storage.project(row) for row in rows]
    # Check the stored bytes before admitting any recognized reference.
    files.verify_stored(source)
    rows = []
    frappe.db.savepoint("cowork_bind_original")
    try:
        for index, group in enumerate(prepared, 1):
            attachment = {"source_file": source.name,
                "source_pdf_id": source.name if kind == "physical_page" else None,
                "source_page_indices": storage.encode(group["indices"]),
                "source_index": index if len(groups) > 1 else None,
                "source_total": len(groups) if len(groups) > 1 else None,
                "filename": source.filename}
            group_key = hashlib.sha256(storage.encode([source.name, request_key, index]).encode()).hexdigest()
            rows.append(storage._create(group["workspace"], group["body"], group_key,
                                         group["client"], attachment=attachment, check_duplicates=True))
        if not source.binding_complete:
            source.binding_complete = 1
            source.binding_fingerprint = fingerprint
            source.binding_records = storage.encode([row["name"] for row in rows])
            if previews:
                source.preview_objects = previews
            source.flags.cowork_original_write = guard.WRITE_CAPABILITY
            source.save(ignore_permissions=True)
    except frappe.QueryDeadlockError:
        frappe.db.rollback()
        raise
    except Exception:
        frappe.db.rollback(save_point="cowork_bind_original")
        raise
    return rows
