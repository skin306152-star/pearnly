"""Immutable native directory publication with no implied preparation authority."""
from datetime import datetime, timezone
import uuid
import frappe
from frappe import _
from pearnly_cowork.targets.identity import normalize_express_account_key
from . import access, model, state


def body_for(value):
    return {"adapter": value.adapter, "scope_kind": value.scope_kind, "scope_key": value.scope_key,
        "account_sets": value.account_sets, "masters": value.masters, "form_schema": value.form_schema,
        "capabilities": value.capabilities, "entity_counts": value.entity_counts,
        "component_hashes": value.component_hashes, "collector": value.collector,
        "observed_at": value.observed_at.isoformat()}


def publish(doc, normalized, *, preparation=None, request_id=None, preserve_pending=False):
    """Caller owns Connection lock, current authority and transaction completion."""
    if normalized.adapter != doc.adapter:
        frappe.throw(_("connection.snapshot_invalid"))
    kind, key, head_key = state.scope(None if normalized.scope_kind == "endpoint" else normalized.scope_key, doc.adapter)
    prepared = model.preparation(preparation)
    body = {**body_for(normalized), "scope_kind": kind, "scope_key": key}
    if len(model.dumps(body).encode()) > 16 * 1024 * 1024:
        frappe.throw(_("connection.snapshot_too_large"))
    if prepared is not None and kind == "account_set":
        identity = normalize_express_account_key if doc.adapter == "express" else lambda value: str(value or "").strip()
        if identity(prepared["account_set"]) != identity(key):
            frappe.throw(_("connection.preparation_invalid"))
    heads = state.heads(doc)
    head = heads.get(head_key, {})
    content_hash = model.fingerprint([normalized.source_hash, prepared])
    current = state.read(state.SNAPSHOT, head["snapshot_id"]) if head.get("snapshot_id") else None
    if current and (current.connection != doc.name or current.connection_revision != doc.record_revision):
        frappe.throw(_("connection.snapshot_invalid"))
    same = bool(current and current.content_hash == content_hash)
    if same:
        snapshot = current
    else:
        snapshot = frappe.get_doc({"doctype": state.SNAPSHOT, "record_key": "CWES-" + uuid.uuid4().hex,
            "connection": doc.name, "workspace": doc.workspace, "client": doc.client or "",
            "connection_revision": doc.record_revision, "binding_generation": doc.binding_generation,
            "scope_kind": kind, "scope_key": key, "projection_revision": int(head.get("revision") or 0) + 1,
            "adapter": doc.adapter, "source_hash": normalized.source_hash, "content_hash": content_hash,
            "body": model.dumps(body), "preparation": model.dumps(prepared),
            "observed_at": normalized.observed_at.replace(tzinfo=None),
            "installed_at": datetime.now(timezone.utc).replace(tzinfo=None)})
        access.write(snapshot)
    pending = False
    if preserve_pending and head.get("request_id"):
        request = state.read(state.REFRESH, head["request_id"])
        pending = request.status in {"requested", "leased"}
    heads[head_key] = {"snapshot_id": snapshot.name, "revision": snapshot.projection_revision,
        "request_id": request_id if request_id is not None else head.get("request_id"),
        "status": "pending" if pending else "fresh"}
    encoded = model.dumps(heads)
    if encoded != (doc.projection_heads or ""):
        # 心跳每分钟都会带同一份目录来：指针没变就不重写连接这一行（整行含上报记录，会全量进数据库日志）。
        doc.projection_heads = encoded
        access.write(doc)
    return {"snapshot_id": snapshot.name, "revision": snapshot.projection_revision, "published": not same}
