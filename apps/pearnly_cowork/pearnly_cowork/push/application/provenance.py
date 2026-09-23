"""Resolve PRN origins from sealed evidence, never editable invoice fields."""
import json

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.history import storage


def _invalid():
    frappe.throw(_("push.original_reference_invalid"), frappe.PermissionError)


def _json(value, expected):
    try:
        parsed = json.loads(value or "null")
    except (ValueError, TypeError):
        _invalid()
    if not isinstance(parsed, expected):
        _invalid()
    return parsed


def original_history(doc):
    """Return a currently authorized original, or this History for ordinary input.

    A roundtrip reference must agree between the immutable Source manifest and
    the independently sealed OCR Batch result that created this exact History.
    The lookup is not a claim of external success; only the scoped push ledger
    can subsequently supply an ERP document number.
    """
    scope.require_scope(doc.workspace, doc.client)
    if not doc.source_file:
        return doc.name
    source = frappe.get_doc("Cowork Source File", doc.source_file)
    if (not source.binding_complete or doc.name not in _json(source.binding_records, list)):
        _invalid()
    if not source.provenance_json:
        return doc.name
    manifest = _json(source.provenance_json, dict)
    provenance = manifest.get("provenance") or {}
    if provenance.get("unit_kind") != "roundtrip_document":
        return doc.name
    if (source.workspace != doc.workspace or (source.client or "") != (doc.client or "")
            or manifest.get("source_sha256") != source.sha256):
        _invalid()
    indices = _json(doc.source_page_indices, list)
    if len(indices) != 1 or type(indices[0]) is not int:
        _invalid()
    units = provenance.get("units")
    if not isinstance(units, list):
        _invalid()
    matching = [unit for unit in units if isinstance(unit, dict) and unit.get("page_number") == indices[0]]
    if len(matching) != 1 or not isinstance(matching[0].get("rows"), list) or not matching[0]["rows"]:
        _invalid()
    references = set()
    for row in matching[0]["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("history_id"), str):
            _invalid()
        reference = row["history_id"]
        if reference != reference.strip():
            _invalid()
        references.add(reference)
    if len(references) != 1:
        _invalid()
    reference = references.pop()
    # There is one admitted Batch per Source; use its unique source link and
    # verify the saved outputs, rather than searching editable pages JSON.
    batches = frappe.get_all("Cowork OCR Batch", filters={"source_file": source.name}, pluck="name", limit_page_length=2)
    if len(batches) != 1:
        _invalid()
    batch = frappe.get_doc("Cowork OCR Batch", batches[0])
    if (batch.phase != "saved" or batch.source_sha256 != source.sha256
            or batch.workspace != doc.workspace or (batch.client or "") != (doc.client or "")
            or doc.name not in _json(batch.history_records, list)):
        _invalid()
    result = _json(batch.result_json, dict)
    pages = result.get("pages")
    if not isinstance(pages, list):
        _invalid()
    saved = [page for page in pages if isinstance(page, dict) and page.get("page_number") == indices[0]]
    if len(saved) != 1 or str(saved[0].get("source_history_id") or "") != reference:
        _invalid()
    if not reference:
        return doc.name
    prior = storage.read(reference, for_update=True)
    if prior.workspace != doc.workspace or (prior.client or "") != (doc.client or ""):
        _invalid()
    return prior.name
