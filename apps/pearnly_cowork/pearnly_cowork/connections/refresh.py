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
    expire(doc, now, credential_id=credential_id, token_generation=token_generation)
    rows = _rows(doc)
    if any(row.status == 'leased' for row in rows):
        return None
    selected = normalize_express_account_key(selected)
    candidates = [row for row in rows if row.status == 'requested' and row.adapter == 'express'
                  and (not selected or row.account_set_key in ('@endpoint', selected))]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (0 if row.scope_kind == 'endpoint' else 1 if row.account_set_key == selected else 2,
                                     row.requested_at, row.name))
    row = candidates[0]
    row.status, row.started_at = 'leased', now
    row.lease_handle = str(uuid4())
    row.lease_owner, row.lease_generation = credential_id, token_generation
    row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    row.completed_at, row.error_code, row.result_revision = None, None, None
    access.write(row)
    return {'request_id': row.lease_handle, 'account_set_key': row.account_set_key, 'scope_kind': row.scope_kind}
