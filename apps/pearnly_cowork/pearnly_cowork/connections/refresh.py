"""Local Express refresh leases; a wire request ID is a single-lease handle.

No external call, commit, retry or reverse dependency on Companion. The caller
holds Connection then its authenticated Credential throughout this transaction.
"""
from datetime import timedelta
from uuid import uuid4

import frappe
from frappe import _
from pearnly_cowork.targets.identity import normalize_express_account_key
from . import access, model, state

LEASE_SECONDS = 900
# 读这台电脑上任一份账套（不只是它当前选的那份）的完整主档：科目表、商品、客户、供应商、部门、存货科目组。
# 按单个账套读（`projection_refresh.collect_account`）从 1.2.3 起读完整目录，供应商与部门从 1.3.0 起才报；
# 更早的 CC 桌面端只把它选的那份交给它读，其他账套的请求留着、照实告诉用户要升级（`account_masters`）。
ANY_ACCOUNT_SET_MIN_VERSION = "1.3.0"


def version_tuple(value):
    try:
        return tuple(int(part) for part in str(value or "").strip().split("."))
    except ValueError:
        return (0,)


def reads_any_account_set(version):
    """这个版本的 CC 桌面端能读它当前选的账套以外的账套。"""
    return bool(str(version or "").strip()) and version_tuple(version) >= version_tuple(ANY_ACCOUNT_SET_MIN_VERSION)


def desktop_version(doc):
    """最近一次上报的 CC 桌面端版本（心跳里的 `companion_version`）。"""
    return str(model.parse(doc.get("report_json"), {}).get("companion_version") or "")


def directory(doc):
    """当前账套目录（'@endpoint' 那份快照）里的账套：``{账套键: 能不能写}``；还没有目录回 None。"""
    _, _, key = state.scope(None)
    head = state.heads(doc).get(key) or {}
    if not head.get("snapshot_id"):
        return None
    row = frappe.db.get_value(state.SNAPSHOT, head["snapshot_id"], ["connection", "connection_revision", "body"],
                              as_dict=True)
    if not row or row.connection != doc.name or int(row.connection_revision or 0) != int(doc.record_revision or 0):
        return None
    found = {}
    for item in (model.parse(row.body, {}) or {}).get("account_sets") or []:
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        found[normalize_express_account_key(item.get("source_id"))] = (
            item.get("active", True) is not False and attributes.get("writable", True) is not False)
    found.pop("", None)
    return found


def _rows(doc, statuses=("requested", "leased")):
    placeholders = ','.join(['%s'] * len(statuses))
    names = frappe.db.sql(f'''SELECT name FROM `tabCowork ERP Refresh`
        WHERE connection=%s AND status IN ({placeholders}) ORDER BY requested_at,name FOR UPDATE''',
        (doc.name, *statuses))
    return [state.read(state.REFRESH, row[0]) for row in names]


def _clear_lease(row):
    row.lease_handle = None
    row.lease_owner = None
    row.lease_generation = None
    row.lease_expires_at = None


def _head_status(doc, row, status, error=None):
    heads = state.heads(doc)
    _, _, key = state.scope(None if row.scope_kind == 'endpoint' else row.scope_key, doc.adapter)
    if heads.get(key, {}).get('request_id') == row.name:
        heads[key] = {**heads[key], 'status': status, 'error_code': error}
        doc.projection_heads = model.dumps(heads)
        access.write(doc)


def fail(doc, row, code, now):
    row.status, row.error_code, row.completed_at = 'failed', code[:200], now
    row.result_revision = None
    _clear_lease(row)
    access.write(row)
    _head_status(doc, row, 'error', code)


def expire(doc, now, *, credential_id=None, token_generation=None):
    for row in _rows(doc):
        if (row.connection_revision != doc.record_revision or row.binding_generation != doc.binding_generation):
            fail(doc, row, 'ERR_CONNECTION_CHANGED', now)
        elif row.status == 'leased':
            if credential_id is not None and (row.lease_owner != credential_id or row.lease_generation != token_generation):
                # Rotation must not let a newly issued key finish an old lease.
                fail(doc, row, 'ERR_AGENT_CREDENTIAL_CHANGED', now)
            elif not row.lease_expires_at or row.lease_expires_at <= now:
                if row.started_at and row.requested_at > row.started_at:
                    row.status, row.started_at, row.completed_at, row.error_code = 'requested', None, None, None
                    row.result_revision = None
                    _clear_lease(row)
                    access.write(row)
                    _head_status(doc, row, 'pending')
                else:
                    fail(doc, row, 'ERR_REFRESH_LEASE_EXPIRED', now)


def supersede(doc, row, now):
    if row.status == 'leased':
        fail(doc, row, 'ERR_REFRESH_SUPERSEDED', now)


def validate_completion(doc, completion, credential_id, token_generation, now):
    if completion is None:
        return None
    handle = completion.get('request_id')
    if not isinstance(handle, str) or not handle or len(handle) > 140:
        frappe.throw(_('connection.refresh_stale'))
    rows = frappe.db.sql('''SELECT name FROM `tabCowork ERP Refresh`
        WHERE connection=%s AND lease_handle=%s FOR UPDATE''', (doc.name, handle))
    if len(rows) != 1:
        frappe.throw(_('connection.refresh_stale'))
    row = state.read(state.REFRESH, rows[0][0])
    key = '@endpoint' if completion.get('scope') == 'endpoint' else normalize_express_account_key(completion.get('account_set'))
    if (row.status != 'leased' or row.adapter != 'express' or row.account_set_key != key
            or row.scope_kind != completion.get('scope')
            or row.connection_revision != doc.record_revision or row.binding_generation != doc.binding_generation
            or row.lease_owner != credential_id or row.lease_generation != token_generation
            or not row.lease_expires_at or row.lease_expires_at <= now):
        frappe.throw(_('connection.refresh_stale'))
    return row


def finish(doc, row, *, revision=None, error=None, now):
    if row is None:
        return
    if row.requested_at > row.started_at:
        row.status, row.started_at, row.completed_at, row.error_code = 'requested', None, None, None
        row.result_revision = None
        _clear_lease(row)
        access.write(row)
        _head_status(doc, row, 'pending')
    elif error:
        fail(doc, row, str(error), now)
    elif revision is not None:
        row.status, row.completed_at, row.error_code = 'succeeded', now, None
        row.result_revision = revision
        _clear_lease(row)
        access.write(row)
        _head_status(doc, row, 'fresh')
    # A valid heartbeat that omits the requested component is not completion.


def lease(doc, selected, credential_id, token_generation, now):
    """Hand at most one requested read to this connection's PC (one lease at a time).

    The selected account set and the endpoint directory are read by every CC Desktop. Any other account set is
    handed only when it is listed (and writable) in the current endpoint directory and the PC's CC Desktop reads
    any account set (`reads_any_account_set`); an older one leaves such requests waiting. A request for an account
    set the directory no longer lists fails instead of reaching the PC. ``selected`` is this heartbeat's choice;
    the poll lease has none and uses the connection's configured selection.
    """
    expire(doc, now, credential_id=credential_id, token_generation=token_generation)
    rows = _rows(doc)
    if any(row.status == 'leased' for row in rows):
        return None
    config = model.parse(doc.config_json, {})
    selected = normalize_express_account_key(selected or config.get('account_set') or config.get('account_dir'))
    found, capable = [], None
    listed = False
    for row in rows:
        if row.status != 'requested' or row.adapter != 'express':
            continue
        if row.scope_kind == 'endpoint' or row.account_set_key == selected:
            found.append(row)
            continue
        if capable is None:
            capable = reads_any_account_set(desktop_version(doc))
        if not capable:
            continue
        if listed is False:
            listed = directory(doc)
        if listed is None:
            continue  # no directory yet: the endpoint read comes first, then this one
        if listed.get(row.account_set_key) is not True:
            fail(doc, row, 'ERR_ACCOUNT_SET_UNAVAILABLE', now)
            continue
        found.append(row)
    if not found:
        return None
    found.sort(key=lambda row: (0 if row.scope_kind == 'endpoint' else 1 if row.account_set_key == selected else 2,
                                row.requested_at, row.name))
    row = found[0]
    row.status, row.started_at = 'leased', now
    row.lease_handle = str(uuid4())
    row.lease_owner, row.lease_generation = credential_id, token_generation
    row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    row.completed_at, row.error_code, row.result_revision = None, None, None
    access.write(row)
    return {'request_id': row.lease_handle, 'account_set_key': row.account_set_key, 'scope_kind': row.scope_kind}


def create(doc, kind, key, head_key, *, actor, identity, signature, now):
    """Record one requested read and point its scope head at it (caller holds the Connection lock)."""
    heads = state.heads(doc)
    previous = heads.get(head_key, {})
    row = frappe.get_doc({"doctype": state.REFRESH, "record_key": identity, "connection": doc.name,
        "workspace": doc.workspace, "client": doc.client or "", "actor": actor,
        "connection_revision": doc.record_revision, "binding_generation": doc.binding_generation,
        "adapter": doc.adapter, "scope_kind": kind, "scope_key": key,
        "account_set_key": "@endpoint" if kind == "endpoint" else key,
        "request_hash": signature, "status": "requested", "requested_at": now, "result_revision": 0})
    access.write(row)
    heads[head_key] = {**previous, "request_id": identity, "status": "pending", "error_code": None}
    doc.projection_heads = model.dumps(heads)
    return row
