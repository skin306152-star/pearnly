"""Transactional correction capture and actor-bound admission snapshots.

Only internal services call this module; nothing is whitelisted. Capture owns
a savepoint, never a commit. The history request owns transaction-fatal retries.
"""
from datetime import datetime, timezone
import json
from uuid import uuid4

import frappe
from frappe import _

from pearnly_cowork.ocr.feedback import access, model

HISTORY = "Cowork OCR History"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _diagnostic(code, doc, error):
    # Never send invoice values, SQL/exception text, traceback locals or a
    # serialized document to native Error Log/Version/Comment/Activity Log.
    try:
        frappe.logger("cowork_ocr_feedback").warning(
            "capture=%s history=%s revision=%s error_type=%s", code, doc.name,
            int(doc.record_version or 0), type(error).__name__)
    except Exception:
        pass


def capture_saved_edit(doc):
    """Call after History.save(), before the caller's final transaction commit.

    Uses the server-side previous document only to decide whether pages changed;
    the learned baseline always comes from persisted immutable ai_raw.
    """
    previous = doc.get_doc_before_save()
    if not previous or previous.pages == doc.pages:
        return {"status": "unchanged", "count": 0}
    point = "cowork_feedback_" + uuid4().hex
    frappe.db.savepoint(point)
    try:
        access.require_scope(doc.workspace, doc.client)
        current = frappe.get_doc(HISTORY, doc.name, for_update=True)
        if (current.record_version != doc.record_version or current.pages != doc.pages
                or current.ai_raw != doc.ai_raw or current.modified_by != frappe.session.user):
            raise ValueError("Saved history evidence does not match the capture request")
        access.require_scope(current.workspace, current.client)
        raw = json.loads(current.ai_raw or "null")
        if not raw:
            return {"status": "no_baseline", "count": 0}
        rows = model.events(site=frappe.local.site, workspace=current.workspace, client=current.client,
                            actor=frappe.session.user, history=current.name, version=int(current.record_version),
                            ai_pages=raw, edited_pages=json.loads(current.pages))
        written = 0
        for row in rows:
            name = "CWFC-" + row["event_key"]
            existing = frappe.db.get_value(access.DOCTYPE, name, "fingerprint", for_update=True)
            if existing:
                if existing != row["fingerprint"]:
                    raise ValueError("Correction event identity conflict")
                continue
            access.insert({**row, "occurred_at": _now()})
            written += 1
        return {"status": "recorded" if written else "replayed", "count": written}
    except frappe.QueryDeadlockError as error:
        # InnoDB may already have rolled back History.save(), so pretending the
        # feedback failure was harmless would report a lost invoice as saved.
        _diagnostic("transaction_retry", doc, error)
        raise
    except Exception as error:
        # If rollback itself fails, propagate: the caller must not claim its
        # enclosing invoice transaction remains usable.
        frappe.db.rollback(save_point=point)
        _diagnostic("skipped", doc, error)
        return {"status": "failed", "count": 0}


def _candidate_headers(scope_key):
    # Immutable event rows allow two bounded reads without racing a changed
    # corrected_value. First read returns only identities, counts and byte sizes.
    return frappe.db.sql("""
        SELECT name, use_count, value_bytes FROM (
          SELECT name, use_count, value_bytes, seller_tax,
            ROW_NUMBER() OVER (PARTITION BY BINARY seller_tax
              ORDER BY use_count DESC, occurred_at DESC, name DESC) AS tax_rank
          FROM (
            SELECT c.name, c.seller_tax, c.occurred_at,
              OCTET_LENGTH(COALESCE(c.ai_value,'')) + OCTET_LENGTH(c.corrected_value)
                + OCTET_LENGTH(COALESCE(c.seller_tax,'')) AS value_bytes,
              COUNT(*) OVER (PARTITION BY c.sample_key) AS use_count,
              ROW_NUMBER() OVER (PARTITION BY c.sample_key
                ORDER BY c.occurred_at DESC, c.name DESC) AS event_rank
            FROM `tabCowork OCR Correction` c
            INNER JOIN `tabCowork OCR History` h ON h.name=c.source_history
              AND h.workspace=c.workspace AND COALESCE(h.client,'')=COALESCE(c.client,'')
            WHERE c.scope_key=%s
          ) newest WHERE event_rank=1
        ) ranked WHERE tax_rank <= 20
        ORDER BY seller_tax, tax_rank LIMIT %s
        """, (scope_key, model.MAX_SAMPLES + 1), as_dict=True)


def snapshot_for_scope(workspace, client=None):
    """Freeze this exact company/client's examples for the current actor.

    No cross-client fallback. Size failures reject admission before a provider
    is called; they never silently truncate the set of eligible tax identities.
    The caller stores this in its protected Batch, not Core work arguments.
    """
    access.require_scope(workspace, client)
    key = model.scope_key(frappe.local.site, workspace, client)
    headers = _candidate_headers(key)
    if (len(headers) > model.MAX_SAMPLES
            or sum(int(row.value_bytes) for row in headers) > model.MAX_SNAPSHOT_BYTES):
        raise ValueError("Correction snapshot exceeds the admission limit")
    values = _event_rows([row.name for row in headers])
    by_name = {row.name: row for row in values}
    examples = {}
    for header in headers:
        row = by_name.get(header.name)
        if (not row or row.scope_key != key or row.site_key != frappe.local.site
                or row.workspace != workspace or (row.client or "") != (client or "")):
            raise ValueError("Correction snapshot source changed")
        examples.setdefault(row.seller_tax or "", []).append({
            "field_name": row.field_name, "ai_value": row.ai_value,
            "corrected_value": row.corrected_value, "use_count": int(header.use_count),
            "event_id": row.name, "event_fingerprint": row.fingerprint,
            "source_history": row.source_history,
        })
    return model.snapshot({"schema": model.SCHEMA, "site": frappe.local.site,
        "actor": frappe.session.user, "workspace": workspace, "client": client or "",
        "captured_at": _now().isoformat(), "examples_by_tax": examples})


def _event_rows(names):
    if not names:
        return []
    return frappe.db.sql("""SELECT c.name, c.site_key, c.scope_key, c.workspace, c.client, c.source_history,
        c.field_name, c.seller_tax, c.ai_value, c.corrected_value, c.fingerprint,
        h.name AS history_name, h.workspace AS history_workspace, h.client AS history_client
        FROM `tabCowork OCR Correction` c
        LEFT JOIN `tabCowork OCR History` h ON h.name=c.source_history
        WHERE c.name IN %s""", (tuple(names),), as_dict=True)


def validate_snapshot(snapshot):
    """Worker must restore its protected Batch actor before calling this.

    Revalidate current entitlement/scope and persisted immutable source rows;
    preserve accepted values/counts rather than reselecting from a newer library.
    """
    body = model.verify_snapshot(snapshot)
    if body.get("site") != frappe.local.site or body.get("actor") != frappe.session.user:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    access.require_scope(body.get("workspace"), body.get("client"))
    key = model.scope_key(frappe.local.site, body["workspace"], body.get("client"))
    samples = [(tax, row) for tax, rows in body["examples_by_tax"].items() for row in rows]
    names = [row["event_id"] for _, row in samples]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate correction snapshot source")
    current = {row.name: row for row in _event_rows(names)}
    for tax, example in samples:
        row = current.get(example["event_id"])
        if (not row or row.site_key != frappe.local.site or row.scope_key != key
                or row.workspace != body["workspace"] or (row.client or "") != (body.get("client") or "")
                or (row.seller_tax or "") != tax or row.fingerprint != example["event_fingerprint"]
                or row.source_history != example["source_history"]
                or row.field_name != example["field_name"] or row.ai_value != example["ai_value"]
                or row.corrected_value != example["corrected_value"]
                or type(example["use_count"]) is not int or example["use_count"] < 1):
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        if (not row.history_name or row.history_workspace != body["workspace"]
                or (row.history_client or "") != (body.get("client") or "")):
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return body
