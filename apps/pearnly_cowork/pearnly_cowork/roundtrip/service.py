"""Authorized history export; adapters and ERP facts remain server-owned."""
from copy import deepcopy

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.history import storage
from pearnly_cowork.push import export_facts
from pearnly_cowork.roundtrip.export import ExportError, build_review_workbook


def merge_pages(pages, *, canonical=False):
    """Bound OCR histories already hold the merged invoice on their first page.

    Appending subsequent raw evidence a second time duplicates real item rows.
    Unbound multi-page input has no such invariant and requires explicit review.
    """
    if len(pages) > 1:
        if not canonical:
            raise ExportError("roundtrip.export_multipage_unverified")
        return deepcopy(pages[0].get("fields") or {})
    merged, items = {}, []
    for page in pages:
        fields = page.get("fields") or {}
        for key, value in fields.items():
            if key == "items":
                if isinstance(value, list):
                    items.extend(deepcopy(value))
            elif value not in (None, "", [], {}) and not merged.get(key):
                merged[key] = deepcopy(value)
    if items:
        merged["items"] = items
    return merged


def export(names):
    scope.require_read()
    names = storage.parse(names)
    if (not isinstance(names, list) or not 1 <= len(names) <= 100
            or any(not isinstance(n, str) or not n or len(n) > 140 for n in names)
            or len(names) != len(set(names))):
        frappe.throw(_("roundtrip.export_records_invalid"))
    records, histories, ownership, push_facts = [], [], set(), {}
    for name in names:
        doc = storage.read(name)
        histories.append(doc)
        ownership.add((doc.workspace, doc.client or ""))
        canonical = bool(doc.source_file)
        if canonical:
            original = frappe.get_doc("Cowork Source File", doc.source_file)
            if not original.binding_complete or doc.name not in storage.parse(original.binding_records or "[]"):
                raise ExportError("roundtrip.export_multipage_unverified")
        fields = merge_pages(storage.parse(doc.pages), canonical=canonical)
        if not canonical:
            for key, value in (("invoice_number", doc.invoice_no), ("date", doc.invoice_date),
                               ("seller_name", doc.seller_name), ("total_amount", doc.total_amount)):
                if value and not fields.get(key):
                    fields[key] = value
        records.append({"history_id": doc.name, "merged_fields": fields})
        if doc.last_push_status:
            push_facts[doc.name] = {"state": {"status": doc.last_push_status}}
    if len(ownership) != 1:
        frappe.throw(_("roundtrip.export_scope_mixed"), frappe.PermissionError)
    for name, facts in export_facts.for_histories(histories).items():
        push_facts.setdefault(name, {}).update(facts)
    # 表格按导出人的网页语言出（Sheet 名、表头、状态；导回时四种语言都认）
    return build_review_workbook(records, push_facts=push_facts, lang=getattr(frappe.local, "lang", None) or "th")
